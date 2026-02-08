# AI Scalper Architecture (Simple Overview)

Date: 2026-02-05
Repo: `c:\algo\openalgov2\openalgo`

## Short Answer
- The system **is automated** when you enable **Server Agent** in the AutoTrade window.
- It runs **real-time decision logic** from WebSocket ticks.
- The LLM is **advisory only** (optional), not the primary decision engine.
- Risk rules always sit above the strategy.
- **Auto** and **manual** trades are stored in **separate databases** and UIs for clean analytics and model tuning.

## What Is Automated Today
### Real-time agent (Server Agent)
- Runs inside OpenAlgo as a background thread.
- Subscribes to CE/PE LTP ticks + underlying index ticks.
- Decides entries/exits using rule-based logic (momentum + filters).
- Executes orders through OpenAlgo services (paper or live).

### Local auto (Browser)
- Runs only inside `auto_trading_window.html`.
- Uses the same rules but never touches backend unless you toggle Server Agent.
- Good for fast UI testing.

## Key Components
### 1) Data Ingestion
- WebSocket feed from OpenAlgo proxy.
- LTP ticks for CE, PE, and underlying index.
- Optional depth feed (if broker supports it).

### 2) Feature Cache
- Stores latest ticks, volatility proxy, spread, imbalance ratio.
- Computes momentum direction + streak count.

### 3) Strategy Agent (AutoScalperAgent)
- Applies rules:
  - Momentum ticks
  - Candle confirmation
  - Underlying direction filter
  - Relative strength filter
  - Spread + imbalance checks
- Controls averaging logic (lot scaling).
- Switches sides if momentum flips.

### 4) Risk Engine
- Per-trade max loss
- Daily max loss
- Cooldown after loss
- Time-based exit if momentum fades
- Data stall fail-safe (stops if no ticks)

### 5) Execution Engine
- Sends market orders using OpenAlgo order services.
- Honors rate limits.
- Paper mode uses Analyze sandbox.

### 6) Learning Layer (Lightweight RL)
- Logs every entry/exit to SQLite (`db/ai_scalper_ledger.db`).
- Uses a bandit tuner to adjust parameters over time.
- Chooses from multiple �arms� (tight, loose, trend, etc.).
- Only runs in Server Agent mode.

### 7) Trade Log Storage (Auto vs Manual — Separate)
- **Auto trades** (AutoTrade window, paper or live):
  - Logged to `db/ai_scalper_logs.db` via `AutoTradeLogStore` (async queue + worker).
  - Frontend batches events and sends to `POST /ai_scalper/logs`; analytics at `GET /ai_scalper/analytics`.
  - React: **Auto Analytics** (`/auto-trade/analytics`), **Auto Tuning** (`/auto-trade/tuning`).
- **Manual trades** (Scalping window + Chart window):
  - Logged to `db/manual_trade_logs.db` via `ManualTradeLogStore` (same async pattern).
  - Frontend queues and sends to `POST /manual_trades/logs`; analytics at `GET /manual_trades/analytics`.
  - React: **Manual Analytics** (`/manual-trades/analytics`).
- Separation keeps model tuning and analysis correct (no mixing auto vs manual in one stream).

### 8) Model Tuning Pipeline (Optional)
- Runs on **auto-trade logs only** (not manual).
- Service: `services/ai_scalper/model_tuner.py`; runs stored in `db/ai_scalper_tuning.db`.
- Scheduler: `model_tuner_scheduler.py` (APScheduler); can run on interval or on-demand.
- Providers: OpenAI, Anthropic, Ollama (cloud or local).
- Suggests parameter changes; auto-apply only in paper mode (safety).
- API: `POST /ai_scalper/model/run`, `GET /ai_scalper/model/status`, `GET /ai_scalper/model/recommendations`, `POST /ai_scalper/model/apply`.

### 9) LLM Advisor (Optional)
- Can be OpenAI / Anthropic / Ollama.
- Provides **parameter suggestions only**.
- Never blocks or delays execution.
- Can auto-apply or keep suggestions pending.

### 10) UI / Monitoring
- **AutoTrade panel** (Jinja/HTML):
  - Auto P&L (open + realized)
  - Last signal
  - Trades/min
  - Learning dashboard
  - Recent trades
- Trade log export (browser)
- Learning replay (server)
- **React** (separate analytics/tuning):
  - `/auto-trade/analytics` — Auto trade analytics (equity, PnL distribution, side/reason/time breakdowns).
  - `/auto-trade/tuning` — Model tuning: run LLM analysis, view recommendations, apply (paper/live).
  - `/manual-trades/analytics` — Manual trade analytics (Scalping + Chart window trades only).

## What It Is NOT
- Not a pure �AI decision maker� (LLM doesn�t trade for you).
- No heavy reinforcement learning with deep neural nets.
- No latency advantage beyond OpenAlgo + broker API limits.

## Typical Automation Flow
1. Enable **Server Agent**.
2. Click **AutoTrade ON**.
3. Agent reads ticks ? evaluates rules.
4. If conditions pass ? market entry placed.
5. Risk engine manages exit (TP/SL/flip/time loss).
6. Trades logged into DB.
7. Learning tuner updates playbook if enabled.


## Diagram (Simple Box View)

```text
            
            ???????????????????????????
            ?   WebSocket Proxy Feed  ?
            ?  (CE/PE + Underlying)   ?
            ???????????????????????????
                         ? ticks
                         ?
            ???????????????????????????
            ?      Feature Cache       ?
            ?  momentum / spread / etc ?
            ???????????????????????????
                         ?
                         ?
            ???????????????????????????
            ?   AutoScalper Agent     ?
            ? rules + playbooks       ?
            ???????????????????????????
                    ?         ?
          advice    ?         ? risk checks
                    ?         ?
            ????????????????? ??????????????????????
            ?  LLM Advisor  ? ?    Risk Engine      ?
            ? (optional)    ? ? loss/timeout/cooldn ?
            ????????????????? ??????????????????????
                    ?                    ?
                    ??????????????????????
                               ?
                    ??????????????????????
                    ?  Execution Engine  ?
                    ? (paper or live)    ?
                    ??????????????????????
                               ?
                    ??????????????????????
                    ?  OpenAlgo Orders   ?
                    ??????????????????????

                 ???????????????????????????
                 ?  Learning (SQLite DB)   ?
                 ? db/ai_scalper_ledger.db ?
                 ???????????????????????????

     ┌─────────────────────────────────────────────────────────┐
     │  Logging & Analytics (separate DBs)                      │
     │  Auto:  db/ai_scalper_logs.db  → /auto-trade/analytics  │
     │  Manual: db/manual_trade_logs.db → /manual-trades/analytics │
     │  Tuning: db/ai_scalper_tuning.db → /auto-trade/tuning    │
     └─────────────────────────────────────────────────────────┘
```

## Files Involved
- **Agent & execution:** `services/ai_scalper/agent.py`, `risk_engine.py`, `execution_engine.py`, `learning.py`, `playbooks.py`
- **Auto trade logs:** `services/ai_scalper/log_store.py` (→ `db/ai_scalper_logs.db`)
- **Manual trade logs:** `services/manual_trade_log_store.py` (→ `db/manual_trade_logs.db`), `blueprints/manual_trades.py`
- **Model tuning:** `services/ai_scalper/model_tuner.py`, `model_tuner_scheduler.py` (→ `db/ai_scalper_tuning.db`)
- **API & UI:** `blueprints/ai_scalper.py`, `auto_trading_window.html`
- **React:** `frontend/src/pages/AutoTradeAnalytics.tsx`, `AutoTradeModelTuning.tsx`, `ManualTradeAnalytics.tsx`

## If You Want More �AI�
Possible next upgrades:
- Online regime detection (trend/chop) with playbook switching.
- Trade clustering to learn best hours/conditions.
- Deeper ML model for entry scoring (still advisory).
- Separate hedge / volatility watcher agent.

---
This is the current architecture in simple form. If you want, I can also add a diagram version with boxes and arrows.
