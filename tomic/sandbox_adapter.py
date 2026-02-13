"""
TOMIC Sandbox Adapter — Routes Orders Through OpenAlgo Sandbox
================================================================
When TOMIC_MODE=sandbox, all order flow is routed to Sandbox mode.
Spread leverage is configurable for defined-risk strategies.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from tomic.config import TomicConfig, TomicMode

logger = logging.getLogger(__name__)


class SandboxAdapter:
    """
    Routes TOMIC orders through OpenAlgo's Sandbox (paper trading) mode.

    Usage:
        adapter = SandboxAdapter(config)
        if adapter.is_sandbox:
            order = adapter.wrap_order(order_params)
    """

    def __init__(self, config: TomicConfig):
        self._config = config
        self._spread_leverage = config.paper.sandbox_spread_leverage

    @property
    def is_sandbox(self) -> bool:
        return self._config.mode == TomicMode.SANDBOX

    @property
    def is_semi_auto(self) -> bool:
        return self._config.mode == TomicMode.SEMI_AUTO

    def wrap_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Wrap order parameters for Sandbox mode.
        Adds sandbox flags and applies spread leverage.
        """
        if not self.is_sandbox:
            return order_params

        wrapped = dict(order_params)
        wrapped["sandbox_mode"] = True
        wrapped["paper_trade"] = True

        # Apply spread leverage for defined-risk strategies
        strategy = wrapped.get("strategy_tag", "")
        if any(s in strategy for s in ["SPREAD", "CONDOR", "CALENDAR"]):
            wrapped["spread_leverage"] = self._spread_leverage
            logger.debug(
                "Sandbox: applying %.1f× spread leverage for %s",
                self._spread_leverage, strategy,
            )

        return wrapped

    def should_require_approval(self, order_params: Dict[str, Any]) -> bool:
        """
        In semi-auto mode, orders require user approval.
        Returns True if the order should wait for approval.
        """
        if self.is_semi_auto:
            return True
        return False

    def get_status(self) -> Dict[str, Any]:
        """Adapter status for diagnostics."""
        return {
            "mode": self._config.mode.value,
            "is_sandbox": self.is_sandbox,
            "is_semi_auto": self.is_semi_auto,
            "spread_leverage": self._spread_leverage,
        }
