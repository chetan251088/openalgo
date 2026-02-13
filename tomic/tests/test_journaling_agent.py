from __future__ import annotations

import logging
from pathlib import Path

from tomic.agents.journaling_agent import JournalingAgent


class _Cmd:
    def __init__(self, event_id: str, correlation_id: str, payload: dict):
        self.event_id = event_id
        self.correlation_id = correlation_id
        self.payload = payload
        self.processed_at = "2026-02-13T09:06:45Z"


def _build_agent(tmp_path) -> JournalingAgent:
    agent = JournalingAgent.__new__(JournalingAgent)
    agent.logger = logging.getLogger("test.journaling_agent")
    agent._db_path = Path(tmp_path) / "tomic_journal_test.db"
    agent._db_path.parent.mkdir(parents=True, exist_ok=True)
    agent._journaled_ids = set()
    return agent


def test_init_db_adds_entry_reason_columns(tmp_path):
    agent = _build_agent(tmp_path)
    agent._init_db()

    conn = agent._get_conn()
    try:
        rows = conn.execute("PRAGMA table_info(journal_entries)").fetchall()
        names = {str(row["name"]).lower() for row in rows}
    finally:
        conn.close()

    assert "entry_reason" in names
    assert "entry_reason_meta" in names


def test_journal_trade_persists_entry_reason_and_meta(tmp_path):
    agent = _build_agent(tmp_path)
    agent._init_db()

    cmd = _Cmd(
        event_id="evt-journal-reason-1",
        correlation_id="corr-journal-reason-1",
        payload={
            "strategy_id": "TOMIC_DITM_PUT_BANKNIFTY",
            "strategy_tag": "TOMIC_DITM_PUT_BANKNIFTY",
            "instrument": "BANKNIFTY",
            "exchange": "NFO",
            "direction": "BUY",
            "quantity": 60,
            "entry_price": 101.25,
            "entry_reason": "Router: bearish: volatility leads | Signal: IV_LOW, BEARISH -> DITM Put",
            "entry_reason_meta": {
                "router_reason": "bearish: volatility leads",
                "strategy_type": "DITM_PUT",
            },
            "regime_snapshot": {"phase": "BEARISH", "score": -8, "vix": 18.4},
            "sizing_chain": [{"step": 8, "name": "margin_reserve", "reason": "free_margin=84.0% OK"}],
        },
    )

    agent._journal_trade(cmd)
    rows = agent.get_recent_trades(limit=5)
    assert len(rows) == 1
    row = rows[0]

    assert row["instrument"] == "BANKNIFTY"
    assert row["entry_reason"].startswith("Router: bearish: volatility leads")
    assert row["reason"] == row["entry_reason"]
    assert isinstance(row["entry_reason_meta"], dict)
    assert row["entry_reason_meta"]["router_reason"] == "bearish: volatility leads"
