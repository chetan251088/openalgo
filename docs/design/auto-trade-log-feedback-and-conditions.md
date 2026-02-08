# Auto Trade Log Feedback & Matched Conditions

## Feedback on auto_trade_log_2026-02-06.json

- **Volume:** ~49 round-trips (ENTRY + EXIT pairs). Same symbol for many (NIFTY10FEB2625500PE, then CE/25600CE).
- **Entry reasons:** Almost all **Momentum** (one **Average**). So the log tells you *that* it was momentum, but not *which* conditions passed (index bias, spread, imbalance, etc.).
- **Exit reasons:** **Trail SL** (system working), **Manual Close** (frequent), **TP hit** (implied when reason is different). Many **Manual Close** with negative PnL suggest either overtrading or closing too early; having exit context (e.g. “trail_sl” vs “manual_close”) helps separate system exits from human overrides.
- **Gaps for analysis:** You can’t easily answer: “What exactly was true when this entry fired?” or “Did this exit happen because of trail, TP, or manual?” So the next step is to log **what matched** at trade level.

## Matched conditions (implemented)

We now capture **at trade level** (per event) the conditions that led to the trade, in both **local/exported logs** and **DB**, so you can analyse by condition later.

### Entry

For each **ENTRY** we log a **matched_conditions** object (stored in DB as `conditions_json` and included in API/export):

- **checks_passed:** List of gate names that were satisfied before entry (e.g. `side_allowed`, `underlying_ok`, `candle_confirm_ok`, `momentum_ready`, `relative_strength_ok`, `index_bias_ok`, `spread_ok`, `imbalance_ok`, `min_move_ok`, `can_enter`).
- **signal_side** / **trade_side:** CE/PE from signal and after reverse logic.
- **trade_ltp:** LTP used for the entry.
- **reverse_trades:** Whether the playbook was in reverse mode.

For **Average** add entries we log `entry_conditions` with `"trigger": "average"` (and the same structure can be extended with add-specific checks later if needed).

### Exit

For each **EXIT** we log **matched_conditions** (same `conditions_json` in DB) with:

- **trigger:** Canonical reason key: `trail_sl`, `tp_hit`, `sl_hit`, `manual_close`, `time_exit`, `daily_max_loss`, `per_trade_max_loss`, `flip`, `average`, or `other`.
- **reason:** The human-readable reason string (e.g. "Trail SL", "Manual Close").
- **exit_ltp:** LTP at exit.

So you can filter or group by `trigger` (e.g. “only exits where trigger = trail_sl”) and compare PnL by trigger.

### Where it’s stored

- **DB:** `db/ai_scalper_logs.db`, table `auto_trade_logs`. New column **conditions_json** (TEXT) holds the JSON for that event. Filled automatically on first write after deploy (column is added in code if missing).
- **API:** The existing “fetch logs” API returns each event with **matched_conditions** when present (from `conditions_json`).
- **Exports:** Any export that uses the same API (e.g. the “download log” in the auto-trading window) will include **matched_conditions** in the JSON, so local files have the same structure for analysis.

### Example (entry)

```json
{
  "type": "ENTRY",
  "reason": "Momentum",
  "matched_conditions": {
    "checks_passed": ["side_allowed", "underlying_ok", "candle_confirm_ok", "momentum_ready", "relative_strength_ok", "index_bias_ok", "spread_ok", "imbalance_ok", "min_move_ok", "can_enter"],
    "signal_side": "PE",
    "trade_side": "PE",
    "trade_ltp": 102.25,
    "reverse_trades": false
  }
}
```

### Example (exit)

```json
{
  "type": "EXIT",
  "reason": "Trail SL",
  "pnl": 87.1,
  "matched_conditions": {
    "trigger": "trail_sl",
    "reason": "Trail SL",
    "exit_ltp": 105.7
  }
}
```

### Optional next steps

- Add **numeric context** to entry (e.g. spread value, imbalance ratio, index bias value) so you can regress PnL on those.
- Add **exit context** (e.g. open_pnl at exit, distance from TP/SL) for exit analysis.
- Expose **trigger** and **checks_passed** in the Auto Trade Analytics UI so you can filter and compare by condition without querying the DB by hand.
