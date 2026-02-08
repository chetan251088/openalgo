from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Tuple

from utils.logging import get_logger

from .config import ExecutionConfig

logger = get_logger(__name__)


class RateLimiter:
    def __init__(self, max_per_sec: int) -> None:
        self.max_per_sec = max(1, int(max_per_sec))
        self.timestamps: Deque[float] = deque()
        self.lock = threading.Lock()

    def allow(self) -> bool:
        now = time.time()
        with self.lock:
            while self.timestamps and now - self.timestamps[0] > 1:
                self.timestamps.popleft()
            if len(self.timestamps) >= self.max_per_sec:
                return False
            self.timestamps.append(now)
            return True


@dataclass
class ExecutionResult:
    ok: bool
    response: Dict[str, Any]
    status_code: int


class ExecutionEngine:
    def __init__(
        self,
        config: ExecutionConfig,
        api_key: str | None,
        paper_mode: bool = True,
        assist_only: bool = False,
    ) -> None:
        self.config = config
        self.api_key = api_key or config.api_key
        self.paper_mode = paper_mode
        self.assist_only = assist_only
        self.rate_limiter = RateLimiter(config.rate_limit_per_sec)

    def update_modes(self, paper_mode: bool | None = None, assist_only: bool | None = None) -> None:
        if paper_mode is not None:
            self.paper_mode = bool(paper_mode)
        if assist_only is not None:
            self.assist_only = bool(assist_only)

    def _build_order(
        self,
        action: str,
        symbol: str,
        quantity: int,
        price_type: str = "MARKET",
        price: float = 0.0,
        trigger_price: float = 0.0,
    ) -> Dict[str, Any]:
        return {
            "strategy": self.config.strategy,
            "symbol": symbol,
            "exchange": self.config.exchange,
            "action": action,
            "quantity": int(quantity),
            "pricetype": price_type,
            "product": self.config.product,
            "price": float(price or 0.0),
            "trigger_price": float(trigger_price or 0.0),
        }

    def place_market_order(
        self, action: str, symbol: str, quantity: int, reason: str = ""
    ) -> ExecutionResult:
        if self.assist_only:
            return ExecutionResult(
                ok=False,
                response={"status": "skipped", "message": "Assist-only mode", "reason": reason},
                status_code=200,
            )
        if not self.api_key:
            return ExecutionResult(
                ok=False,
                response={"status": "error", "message": "API key missing"},
                status_code=400,
            )
        if not self.rate_limiter.allow():
            return ExecutionResult(
                ok=False,
                response={"status": "error", "message": "Rate limited"},
                status_code=429,
            )

        order_data = self._build_order(action, symbol, quantity, price_type="MARKET")
        original_data = {**order_data, "apikey": self.api_key}

        try:
            if self.paper_mode:
                from services.sandbox_service import sandbox_place_order

                ok, response, status_code = sandbox_place_order(order_data, self.api_key, original_data)
            else:
                from services.place_order_service import place_order

                ok, response, status_code = place_order(order_data, api_key=self.api_key)
            return ExecutionResult(ok=ok, response=response, status_code=status_code)
        except Exception as exc:
            logger.exception("Execution error: %s", exc)
            return ExecutionResult(
                ok=False,
                response={"status": "error", "message": "Execution failure", "reason": str(exc)},
                status_code=500,
            )
