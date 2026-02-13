"""
TOMIC Conflict Router — Signal Priority & Capital Allocation
=============================================================
Arbitrates between Sniper Agent (directional) and Volatility Agent
(options) signals using:

  1. Regime hierarchy — Regime Agent = Master Filter
  2. Signal priority rules — regime determines which agent "leads"
  3. Capital allocator — sector heat limits, position limits
  4. Conflict resolution — simultaneous signals on same underlying

Architecture doc: "Regime Agent is the Master Filter. Congestion →
Volatility priority. Bullish → Sniper priority for directional."
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from tomic.agents.regime_agent import AtomicRegimeState, RegimeSnapshot
from tomic.agents.sniper_agent import SniperSignal
from tomic.agents.volatility_agent import VolSignal
from tomic.config import (
    RegimePhase,
    TomicConfig,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Routing Enums
# ---------------------------------------------------------------------------

class SignalSource(str, Enum):
    SNIPER = "SNIPER"
    VOLATILITY = "VOLATILITY"


class ResolutionAction(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    DEFER = "DEFER"           # hold, re-evaluate next tick
    MERGE = "MERGE"           # combine into multi-leg


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class RouteDecision:
    """Decision record for a signal routing."""
    source: SignalSource
    instrument: str
    strategy_type: str
    action: ResolutionAction
    reason: str
    priority_score: float = 0.0
    timestamp: float = field(default_factory=time.monotonic)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source.value,
            "instrument": self.instrument,
            "strategy_type": self.strategy_type,
            "action": self.action.value,
            "reason": self.reason,
            "priority_score": self.priority_score,
        }


@dataclass
class RoutedSignal:
    """A signal approved by the router, ready for Risk Agent."""
    source: SignalSource
    signal_dict: Dict[str, Any]
    priority_score: float = 0.0
    route_decision: Optional[RouteDecision] = None


@dataclass
class SectorAllocation:
    """Track capital allocation per sector."""
    sector: str
    current_margin_pct: float = 0.0
    position_count: int = 0


# ---------------------------------------------------------------------------
# Priority Scoring
# ---------------------------------------------------------------------------

def compute_priority(
    source: SignalSource,
    regime: RegimeSnapshot,
    signal_score: float = 0.0,
) -> float:
    """
    Compute routing priority for a signal based on regime phase.

    Phase-dependent priority (from architecture docs):
    - BULLISH: Sniper leads (directional momentum), Volatility secondary
    - CONGESTION: Volatility leads (range-bound = premium selling), Sniper blocked
    - BEARISH: Volatility leads (defined risk puts/calls), Sniper short-only
    - BLOWOFF: Neither (too risky, reduce exposure)

    Returns scored priority (higher = process first).
    """
    base_priority = {
        (SignalSource.SNIPER, RegimePhase.BULLISH): 80,
        (SignalSource.SNIPER, RegimePhase.CONGESTION): 0,     # blocked
        (SignalSource.SNIPER, RegimePhase.BEARISH): 30,       # short-only
        (SignalSource.SNIPER, RegimePhase.BLOWOFF): 10,       # minimal

        (SignalSource.VOLATILITY, RegimePhase.BULLISH): 60,
        (SignalSource.VOLATILITY, RegimePhase.CONGESTION): 90,  # leads
        (SignalSource.VOLATILITY, RegimePhase.BEARISH): 70,
        (SignalSource.VOLATILITY, RegimePhase.BLOWOFF): 20,
    }

    key = (source, regime.phase)
    base = base_priority.get(key, 0)

    # Blend with signal-specific score
    return base + signal_score * 0.2


# ---------------------------------------------------------------------------
# Conflict Resolution
# ---------------------------------------------------------------------------

def resolve_conflict(
    sniper_signal: Optional[SniperSignal],
    vol_signal: Optional[VolSignal],
    regime: RegimeSnapshot,
) -> Tuple[ResolutionAction, str, Optional[SignalSource]]:
    """
    Resolve conflict when both agents fire on the same underlying.

    Rules (from architecture docs):
    1. If same direction → ACCEPT both (additive conviction)
    2. If opposing direction → regime determines winner
    3. CONGESTION → always Volatility wins
    4. BULLISH → Sniper wins for directional
    """
    if sniper_signal is None and vol_signal is None:
        return ResolutionAction.REJECT, "no signals", None

    # Master filter: blowoff regime defers all entries.
    if regime.phase == RegimePhase.BLOWOFF:
        return ResolutionAction.DEFER, "blowoff: defer all signals", None

    sniper_dir = (sniper_signal.direction.upper() if sniper_signal else "")
    vol_dir = (vol_signal.direction.upper() if vol_signal else "")

    # Master filter: sniper directional longs are blocked in bearish regime.
    if sniper_signal and regime.phase == RegimePhase.BEARISH and sniper_dir == "BUY":
        if vol_signal is None:
            return ResolutionAction.REJECT, "bearish: sniper BUY blocked", None
        sniper_signal = None

    # Master filter: congestion routes to volatility only.
    if regime.phase == RegimePhase.CONGESTION and sniper_signal and vol_signal is None:
        return ResolutionAction.DEFER, "congestion: volatility priority (sniper deferred)", None

    if sniper_signal is None:
        return ResolutionAction.ACCEPT, "only vol signal", SignalSource.VOLATILITY

    if vol_signal is None:
        return ResolutionAction.ACCEPT, "only sniper signal", SignalSource.SNIPER

    # Both signals exist → check direction agreement
    if sniper_dir == vol_dir:
        # Same direction → accept highest priority source
        sniper_prio = compute_priority(SignalSource.SNIPER, regime, sniper_signal.signal_score)
        vol_prio = compute_priority(SignalSource.VOLATILITY, regime, vol_signal.signal_strength)
        winner = SignalSource.SNIPER if sniper_prio >= vol_prio else SignalSource.VOLATILITY
        return ResolutionAction.ACCEPT, f"same dir ({sniper_dir}), {winner.value} priority", winner

    # Opposing directions → regime decides
    if regime.phase == RegimePhase.CONGESTION:
        return ResolutionAction.ACCEPT, "congestion: volatility leads", SignalSource.VOLATILITY

    if regime.phase == RegimePhase.BULLISH:
        return ResolutionAction.ACCEPT, "bullish: sniper leads", SignalSource.SNIPER

    if regime.phase == RegimePhase.BEARISH:
        return ResolutionAction.ACCEPT, "bearish: volatility leads", SignalSource.VOLATILITY

    return ResolutionAction.DEFER, "unsupported regime", None


# ---------------------------------------------------------------------------
# Conflict Router
# ---------------------------------------------------------------------------

class ConflictRouter:
    """
    Central signal arbiter for the TOMIC system.

    Receives signals from Sniper and Volatility agents,
    applies regime-based priority, resolves conflicts,
    enforces position/sector limits, and outputs an ordered
    list of approved signals for the Risk Agent.
    """

    def __init__(
        self,
        config: TomicConfig,
        regime_state: AtomicRegimeState,
    ):
        self._config = config
        self._regime_state = regime_state
        self._sector_heat: Dict[str, float] = {}
        self._position_count: int = 0
        self._max_positions: int = config.sizing.max_positions
        self._sector_limit: float = config.sizing.sector_heat_limit
        self._decisions: List[RouteDecision] = []
        self._route_count: int = 0
        self._context_only_underlyings = {"INDIAVIX", "VIX"}

    def route(
        self,
        sniper_signals: List[SniperSignal],
        vol_signals: List[VolSignal],
    ) -> List[RoutedSignal]:
        """
        Route all incoming signals through the conflict resolution pipeline.

        Steps:
        1. Group signals by underlying
        2. Apply regime priority scoring
        3. Resolve per-underlying conflicts
        4. Check position/sector limits
        5. Return ordered approved signals
        """
        regime = self._regime_state.read_snapshot()
        self._decisions = []
        approved: List[RoutedSignal] = []

        # Group by underlying
        sniper_by_underlying: Dict[str, List[SniperSignal]] = {}
        for sig in sniper_signals:
            key = sig.instrument.upper()
            sniper_by_underlying.setdefault(key, []).append(sig)

        vol_by_underlying: Dict[str, List[VolSignal]] = {}
        for sig in vol_signals:
            key = sig.underlying.upper()
            vol_by_underlying.setdefault(key, []).append(sig)

        # All underlyings with signals
        all_underlyings = set(sniper_by_underlying.keys()) | set(vol_by_underlying.keys())

        for underlying in all_underlyings:
            if underlying.upper() in self._context_only_underlyings:
                self._decisions.append(
                    RouteDecision(
                        source=SignalSource.VOLATILITY,
                        instrument=underlying,
                        strategy_type="",
                        action=ResolutionAction.REJECT,
                        reason=f"{underlying} is context-only; skipping execution routing",
                    )
                )
                continue

            # Best signal from each source
            best_sniper = max(
                sniper_by_underlying.get(underlying, []),
                key=lambda s: s.signal_score,
                default=None,
            )
            best_vol = max(
                vol_by_underlying.get(underlying, []),
                key=lambda s: s.signal_strength,
                default=None,
            )

            # Resolve conflict
            action, reason, winner = resolve_conflict(best_sniper, best_vol, regime)
            fallback_source = (
                winner
                or (SignalSource.SNIPER if best_sniper is not None else SignalSource.VOLATILITY)
            )

            if action == ResolutionAction.REJECT or action == ResolutionAction.DEFER:
                decision = RouteDecision(
                    source=fallback_source,
                    instrument=underlying,
                    strategy_type="",
                    action=action,
                    reason=reason,
                )
                self._decisions.append(decision)
                continue

            # Position limit check
            if self._position_count >= self._max_positions:
                decision = RouteDecision(
                    source=fallback_source,
                    instrument=underlying,
                    strategy_type="",
                    action=ResolutionAction.REJECT,
                    reason=f"position limit ({self._max_positions})",
                )
                self._decisions.append(decision)
                continue

            # Build routed signal
            if winner == SignalSource.SNIPER and best_sniper:
                signal_dict = best_sniper.to_signal_dict()
                priority = compute_priority(SignalSource.SNIPER, regime, best_sniper.signal_score)
                strategy_type = signal_dict.get("strategy_type", "")
            elif winner == SignalSource.VOLATILITY and best_vol:
                signal_dict = best_vol.to_signal_dict()
                priority = compute_priority(SignalSource.VOLATILITY, regime, best_vol.signal_strength)
                strategy_type = signal_dict.get("strategy_type", "")
            else:
                continue

            # Sector heat check
            sector = self._get_sector(underlying)
            current_heat = self._sector_heat.get(sector, 0.0)
            if current_heat >= self._sector_limit:
                decision = RouteDecision(
                    source=winner,
                    instrument=underlying,
                    strategy_type=strategy_type,
                    action=ResolutionAction.REJECT,
                    reason=f"sector heat ({sector}: {current_heat:.1%}) >= {self._sector_limit:.1%}",
                )
                self._decisions.append(decision)
                continue

            # Approved
            decision = RouteDecision(
                source=winner,
                instrument=underlying,
                strategy_type=strategy_type,
                action=ResolutionAction.ACCEPT,
                reason=reason,
                priority_score=priority,
            )
            self._decisions.append(decision)

            approved.append(RoutedSignal(
                source=winner,
                signal_dict=signal_dict,
                priority_score=priority,
                route_decision=decision,
            ))

        # Sort by priority (highest first)
        approved.sort(key=lambda r: r.priority_score, reverse=True)
        self._route_count += 1

        total_in = len(sniper_signals) + len(vol_signals)
        rejected_or_deferred = len(self._decisions) - len(approved)
        if total_in == 0:
            # Avoid noisy per-cycle INFO logs when there are no inputs.
            logger.debug(
                "ConflictRouter: %d signals in, %d approved, %d rejected/deferred",
                total_in,
                len(approved),
                rejected_or_deferred,
            )
        else:
            logger.info(
                "ConflictRouter: %d signals in, %d approved, %d rejected/deferred",
                total_in,
                len(approved),
                rejected_or_deferred,
            )

        return approved

    def update_sector_heat(self, sector: str, margin_pct: float) -> None:
        """Update sector heat allocation."""
        self._sector_heat[sector] = margin_pct

    def update_position_count(self, count: int) -> None:
        """Update current open position count."""
        self._position_count = count

    def _get_sector(self, instrument: str) -> str:
        """
        Get sector for an instrument.
        Phase 3: simplified mapping. Production: use exchange master data.
        """
        # Indices → special sector
        idx = instrument.upper()
        if idx in ("NIFTY", "BANKNIFTY", "FINNIFTY"):
            return "INDEX"
        # Default: treat each stock as its own sector for now
        return idx

    @property
    def decisions(self) -> List[RouteDecision]:
        return self._decisions

    @property
    def route_count(self) -> int:
        return self._route_count

    def diagnostics(self, limit: int = 25) -> Dict[str, Any]:
        """
        Lightweight router diagnostics for dashboards.

        Returns latest decision traces, active allocation limits, and
        an aggregate of blocking reasons from current decision set.
        """
        capped = max(1, min(int(limit or 1), 200))
        decisions = [d.to_dict() for d in self._decisions[:capped]]
        blocking_reasons: Dict[str, int] = {}
        for decision in self._decisions:
            if decision.action == ResolutionAction.ACCEPT:
                continue
            reason = decision.reason or "unknown"
            blocking_reasons[reason] = blocking_reasons.get(reason, 0) + 1

        return {
            "route_count": self._route_count,
            "position_count": self._position_count,
            "max_positions": self._max_positions,
            "sector_limit": self._sector_limit,
            "sector_heat": dict(self._sector_heat),
            "blocking_reasons": blocking_reasons,
            "decisions": decisions,
        }
