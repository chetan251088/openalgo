"""
MiroFish Client - Calls the MiroFish prediction API and caches results.

Safety features:
- Per-source circuit breaker (stops calling after N consecutive failures)
- Flip-flop protection (halves confidence when bias reverses between refreshes)
- Separate timeouts for quick_mode (10s) vs full simulation (45s)
- Graceful degradation to cached/stale data on failure
"""

import os
import time
import logging
from typing import Optional

import httpx

from .models import MiroFishSignal, MarketBias, VIXExpectation

logger = logging.getLogger(__name__)

QUICK_TIMEOUT_S = 10
FULL_TIMEOUT_S = 45
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_RESET_S = 300  # try again after 5 minutes


class MiroFishClient:
    """HTTP client for the MiroFish prediction API with circuit breaker and flip-flop protection."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: int = 120,
        cache_ttl: int = 900,
    ):
        self.base_url = (base_url or os.getenv("MIROFISH_URL", "http://localhost:5003")).rstrip("/")
        if ":5001" in self.base_url:
            logger.warning("MIROFISH_URL uses port 5001 which conflicts with OpenAlgo Dhan instance. Use 5003.")
        self.timeout = timeout
        self._cache: Optional[MiroFishSignal] = None
        self._cache_ts: float = 0.0
        self._cache_ttl = cache_ttl

        # Circuit breaker state
        self._consecutive_failures: int = 0
        self._circuit_open_since: float = 0.0

        # Flip-flop protection: track last N bias values
        self._bias_history: list = []  # last 2 bias values
        self._FLIPFLOP_WINDOW = 2

    def get_prediction(
        self,
        news: list = None,
        market_data: dict = None,
        requirement: str = "Predict NIFTY direction for the next trading session",
        quick_mode: bool = True,
    ) -> Optional[MiroFishSignal]:
        """Call MiroFish POST /api/prediction/predict and return a MiroFishSignal.

        Returns cached/stale signal if circuit breaker is open or service is unreachable.
        """
        if self._is_circuit_open():
            logger.debug("MiroFish circuit breaker OPEN — returning cached signal")
            return self._mark_stale()

        effective_timeout = QUICK_TIMEOUT_S if quick_mode else FULL_TIMEOUT_S

        try:
            payload = {
                "news": news or [],
                "market_data": market_data or {},
                "requirement": requirement,
                "quick_mode": quick_mode,
            }

            with httpx.Client(timeout=effective_timeout) as client:
                resp = client.post(f"{self.base_url}/api/prediction/predict", json=payload)
                resp.raise_for_status()
                body = resp.json()

            if not body.get("success"):
                logger.warning("MiroFish prediction returned error: %s", body.get("error"))
                self._record_failure()
                return self._mark_stale()

            data = body["data"]
            signal = MiroFishSignal(
                bias=MarketBias(data.get("bias", "NEUTRAL")),
                confidence=float(data.get("confidence", 0.5)),
                vix_expectation=VIXExpectation(data.get("vix_expectation", "STABLE")),
                narrative_summary=data.get("narrative_summary", ""),
                scenarios=data.get("scenarios", []),
                key_risks=data.get("key_risks", []),
                sector_outlook=data.get("sector_outlook", {}),
                timestamp=time.time(),
                stale=False,
                mode=data.get("mode", "quick"),
            )

            signal = self._apply_flipflop_protection(signal)

            self._cache = signal
            self._cache_ts = time.time()
            self._record_success()
            return signal

        except httpx.TimeoutException:
            logger.warning("MiroFish timed out after %ds (quick=%s)", effective_timeout, quick_mode)
            self._record_failure()
            return self._mark_stale()
        except httpx.ConnectError:
            logger.warning("MiroFish service unreachable at %s", self.base_url)
            self._record_failure()
            return self._mark_stale()
        except Exception as e:
            logger.error("MiroFish client error: %s", e)
            self._record_failure()
            return self._mark_stale()

    def get_cached(self) -> Optional[MiroFishSignal]:
        """Return the cached signal, marking it stale if TTL expired."""
        if self._cache is None:
            return None
        age = time.time() - self._cache_ts
        if age > self._cache_ttl:
            self._cache.stale = True
        return self._cache

    def is_available(self) -> bool:
        """Quick health check against the MiroFish prediction service."""
        if self._is_circuit_open():
            return False
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.base_url}/api/prediction/status")
                return resp.status_code == 200
        except Exception:
            return False

    # --- Circuit breaker ---

    def _is_circuit_open(self) -> bool:
        """Check if the circuit breaker is tripped."""
        if self._consecutive_failures < CIRCUIT_BREAKER_THRESHOLD:
            return False
        elapsed = time.time() - self._circuit_open_since
        if elapsed > CIRCUIT_BREAKER_RESET_S:
            logger.info("MiroFish circuit breaker RESET after %ds cool-down", int(elapsed))
            self._consecutive_failures = 0
            return False
        return True

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            if self._circuit_open_since == 0:
                self._circuit_open_since = time.time()
                logger.warning(
                    "MiroFish circuit breaker TRIPPED after %d consecutive failures. "
                    "Will retry after %ds.",
                    self._consecutive_failures, CIRCUIT_BREAKER_RESET_S,
                )

    def _record_success(self) -> None:
        if self._consecutive_failures > 0:
            logger.info("MiroFish recovered after %d failures", self._consecutive_failures)
        self._consecutive_failures = 0
        self._circuit_open_since = 0.0

    # --- Flip-flop protection ---

    def _apply_flipflop_protection(self, signal: MiroFishSignal) -> MiroFishSignal:
        """Hysteresis + flip-flop protection.

        Rules:
        1. If bias reverses from the previous refresh, halve confidence and force NEUTRAL
        2. To CHANGE the accepted bias, the new direction must appear in 2 CONSECUTIVE
           refreshes (hysteresis). A single flip doesn't change the trading posture.
        3. This prevents whipsawing when the LLM oscillates between calls.
        """
        current_bias = signal.bias.value if isinstance(signal.bias, MarketBias) else signal.bias

        if len(self._bias_history) >= 1:
            prev_bias = self._bias_history[-1]

            if prev_bias != current_bias and prev_bias != "NEUTRAL" and current_bias != "NEUTRAL":
                # Direction reversal detected
                if len(self._bias_history) >= 2 and self._bias_history[-2] == current_bias:
                    # The new direction appeared twice in a row (before the flip) — accept it
                    logger.info(
                        "MiroFish bias change accepted (hysteresis passed): %s → %s",
                        prev_bias, current_bias,
                    )
                else:
                    # Single flip — do NOT change posture, hold previous bias at reduced confidence
                    logger.warning(
                        "MiroFish FLIP-FLOP: %s → %s (single flip, needs 2 consecutive to change). "
                        "Forcing NEUTRAL with halved confidence.",
                        prev_bias, current_bias,
                    )
                    signal.confidence *= 0.5
                    signal.bias = MarketBias.NEUTRAL
                    signal.narrative_summary = (
                        f"[FLIP-FLOP: was {prev_bias}, saw {current_bias}, holding NEUTRAL until confirmed] "
                        + signal.narrative_summary
                    )

        self._bias_history.append(current_bias)
        if len(self._bias_history) > self._FLIPFLOP_WINDOW + 1:
            self._bias_history = self._bias_history[-(self._FLIPFLOP_WINDOW + 1):]

        return signal

    def get_circuit_breaker_status(self) -> dict:
        """Expose circuit breaker state for the Command Center."""
        return {
            "consecutive_failures": self._consecutive_failures,
            "circuit_open": self._is_circuit_open(),
            "circuit_open_since": self._circuit_open_since,
            "threshold": CIRCUIT_BREAKER_THRESHOLD,
            "reset_seconds": CIRCUIT_BREAKER_RESET_S,
            "bias_history": list(self._bias_history),
        }

    def _mark_stale(self) -> Optional[MiroFishSignal]:
        if self._cache:
            self._cache.stale = True
        return self._cache
