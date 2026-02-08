# Auto Trade Log Feedback (2026-02-06)

Review of `auto_trade_log_2026-02-06.json` and what was added for condition capture.

---

## Log summary (from your export)

- **~49 ENTRY / ~49 EXIT** events (paired round-trips).
- **Entry reasons:** `Momentum` (most), one `Average`.
- **Exit reasons:** `Trail SL`, `Manual Close` (many), and system exits like `TP hit`.
- **Symbols:** NIFTY10FEB2625500PE, NIFTY10FEB2625500CE, NIFTY10FEB2625600CE (PE then CE, different strikes).
- **Side:** PE and CE; qty 130; TP/SL points 8/4 on entries.

## Observations

1. **Trail SL** exits are working: several exits with positive PnL and reason "Trail SL" (e.g. +71.5, +106.16, +198.9, +87.1).
2. **Manual Close** appears often, sometimes with **negative PnL** (e.g. -1224, -1313, -916). That suggests either discretionary exits cutting losses or closing too early; having **exit conditions** logged will show whether those were user-driven (manual_close) vs system (trail_sl, tp_hit).
3. **Mix of PE and CE** and strike changes (25500 → 25600) within the day is visible; linking to **entry conditions** (e.g. signal_side, trade_side, index_bias, momentum) will help see why CE vs PE was chosen.
4. **holdMs** is present on exits (e.g. 3730 ms, 35 s, 29 s); useful for hold-time analysis once conditions are joined.

## What was added for analysis

### 1. **Matched conditions at trade level**

- **ENTRY:** Each log now includes **entry conditions** (in DB and in exported JSON):
  - `checks_passed`: list of filters that passed (side_allowed, underlying_ok, candle_confirm_ok, momentum_ready, relative_strength_ok, index_bias_ok, spread_ok, imbalance_ok, min_move_ok, can_enter).
  - `signal_side`, `trade_side`, `trade_ltp`, `reverse_trades`.
  - For **Average** entries: `trigger: "average"`, `reason: "Average"`.
- **EXIT:** Each log now includes **exit conditions**:
  - `trigger`: canonical trigger (e.g. `trail_sl`, `tp_hit`, `sl_hit`, `manual_close`, `time_exit`, `daily_max_loss`, `per_trade_max_loss`, `flip`, `other`).
  - `reason`: exact reason string (e.g. "Trail SL", "Manual Close").
  - `exit_ltp`: LTP at exit.

### 2. **Where it is stored**

- **DB:** `db/ai_scalper_logs.db`, table `auto_trade_logs`, column **`conditions_json`** (JSON string). Fetches (and hence exports) include it as **`matched_conditions`**.
- **Local / export:** The same payload is written to the log store and returned by the API, so **downloaded JSON** (e.g. from Auto Trading window) will contain **`matched_conditions`** for each event once you run with the new code.

### 3. **How to use it**

- **By exit trigger:** Filter or group by `matched_conditions.trigger` (e.g. `manual_close` vs `trail_sl`) and compare PnL, hold time, symbol.
- **By entry context:** Use `matched_conditions.checks_passed`, `trade_side`, `trade_ltp` to see what the system required to enter; correlate with later PnL.
- **SQL (e.g. sqlite3):**  
  `SELECT reason, json_extract(conditions_json,'$.trigger') AS trigger, pnl, hold_ms FROM auto_trade_logs WHERE event_type='EXIT' ORDER BY ts DESC LIMIT 50;`

---

## Suggested next steps

1. Run a session with the new build and export logs again; confirm **`matched_conditions`** appears in the JSON and in the DB.
2. Add simple analytics (e.g. PnL by `trigger`, or by `trade_side`) using the new fields.
3. Review **Manual Close** exits with negative PnL: check if they align with your intent or if TP/SL/trail rules could be tuned to reduce early manual cuts.
