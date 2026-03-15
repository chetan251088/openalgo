"""
Decision Attribution Logger — Logs every trade decision with full intelligence context.

For every scalping/selling entry or skip:
  - Technical score (momentum, regime, options context)
  - Intelligence snapshot (MiroFish bias/confidence, rotation quadrant, fundamental status)
  - Gate outcomes (which gates passed/failed, with what scores)
  - Final size multiplier
  - After execution: realized P&L, slippage, fill quality

This is the highest-value analytics addition. Without it, you cannot run ablation tests
or determine whether intelligence is helping or hurting profitability.
"""

import json
import time
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

LOG_DIR = os.getenv("INTELLIGENCE_LOG_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "db"))
LOG_FILE = "trade_decisions.jsonl"


class DecisionLogger:
    """Append-only JSONL logger for trade decisions with full attribution."""

    def __init__(self):
        self._log_path = Path(LOG_DIR) / LOG_FILE
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def log_entry_decision(
        self,
        symbol: str,
        side: str,
        action: str,  # "ENTER" | "SKIP" | "GHOST"
        technical_score: float,
        gate_snapshot: Optional[Dict] = None,
        intelligence_snapshot: Optional[Dict] = None,
        final_size_multiplier: float = 1.0,
        final_quantity: int = 0,
        skip_reason: str = "",
        entry_price: float = 0.0,
        spread_at_entry: float = 0.0,
        strategy: str = "",
        mode: str = "scalping",
    ) -> str:
        """Log a trade entry decision. Returns a decision_id for later P&L matching."""
        decision_id = f"DEC_{int(time.time()*1000)}_{symbol}_{side}"

        record = {
            "decision_id": decision_id,
            "timestamp": datetime.now().isoformat(),
            "epoch": time.time(),
            "mode": mode,
            "symbol": symbol,
            "side": side,
            "action": action,
            "strategy": strategy,

            # Technical layer
            "technical_score": round(technical_score, 4),

            # Intelligence layer
            "gate_snapshot": gate_snapshot,
            "intelligence": intelligence_snapshot,
            "final_size_multiplier": round(final_size_multiplier, 3),
            "final_quantity": final_quantity,
            "skip_reason": skip_reason,

            # Execution quality
            "entry_price": entry_price,
            "spread_at_entry_bps": spread_at_entry,

            # Filled later by log_exit()
            "exit_price": None,
            "realized_pnl": None,
            "slippage_bps": None,
            "hold_duration_s": None,
        }

        self._append(record)
        return decision_id

    def log_exit(
        self,
        decision_id: str,
        exit_price: float,
        realized_pnl: float,
        slippage_bps: float = 0.0,
        hold_duration_s: float = 0.0,
        exit_reason: str = "",
    ) -> None:
        """Log the exit for a previously logged entry. Appended as a separate record."""
        record = {
            "decision_id": decision_id,
            "timestamp": datetime.now().isoformat(),
            "epoch": time.time(),
            "event": "EXIT",
            "exit_price": exit_price,
            "realized_pnl": round(realized_pnl, 2),
            "slippage_bps": round(slippage_bps, 2),
            "hold_duration_s": round(hold_duration_s, 1),
            "exit_reason": exit_reason,
        }
        self._append(record)

    def get_recent_decisions(self, limit: int = 100) -> list:
        """Read the last N decisions for the Command Center."""
        if not self._log_path.exists():
            return []
        try:
            lines = self._log_path.read_text(encoding="utf-8").strip().split("\n")
            recent = lines[-limit:] if len(lines) > limit else lines
            return [json.loads(line) for line in recent if line.strip()]
        except Exception as e:
            logger.error("Failed to read decision log: %s", e)
            return []

    def get_ablation_data(self) -> Dict[str, Any]:
        """Aggregate data for ablation testing: compare P&L by gate configuration."""
        decisions = self.get_recent_decisions(limit=10000)
        entries = [d for d in decisions if d.get("action") in ("ENTER", "GHOST")]

        if not entries:
            return {"total_decisions": 0}

        # Group by whether intelligence was active
        with_intel = [d for d in entries if d.get("gate_snapshot") is not None]
        without_intel = [d for d in entries if d.get("gate_snapshot") is None]

        return {
            "total_decisions": len(entries),
            "with_intelligence": len(with_intel),
            "without_intelligence": len(without_intel),
            "avg_size_multiplier": (
                sum(d.get("final_size_multiplier", 1.0) for d in with_intel) / max(len(with_intel), 1)
            ),
            "skip_rate": sum(1 for d in entries if d.get("action") == "SKIP") / max(len(entries), 1),
        }

    def _append(self, record: dict) -> None:
        """Append a JSON line to the log file."""
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.error("Failed to write decision log: %s", e)
