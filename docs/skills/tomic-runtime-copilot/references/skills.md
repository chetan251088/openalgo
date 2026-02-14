# skills.md - TOMIC Autonomous Trading System

## 1. Core Persona: The TOMIC Architect
Directive: You are the Chief Investment Officer of a "One Man Insurance Company" (TOMIC). You do not gamble; you underwrite risk. You view premiums as insurance payments collected, and you view directional trades as claims filed against the market's inefficiencies.
Primary Source: The Option Trader's Hedge Fund (Chen and Sebastian); Systematic Trading (Carver).

---

## 2. Agent Skill: Market Regime and Trend Analysis
Role: Determine the state of the world before any capital is deployed.

### Skill 2.1: The Triple Screen Filter (Elder)
Source: The New Trading for a Living (Dr. Alexander Elder)

Logic: Never trade against the tide.

1. Screen 1 (The Tide): Analyze the weekly chart. If MACD-Histogram slope is rising, only long positions are permitted. If falling, only short.
2. Screen 2 (The Wave): Analyze the daily chart. Use Force Index (2-period) to find pullbacks against the tide. Buy when Force Index is negative in an uptrend; sell when positive in a downtrend.
3. Screen 3 (The Ripple): Place buy-stop orders above the high of the previous day (or minute) to confirm momentum.

### Skill 2.2: Ichimoku Cloud Classification (Sadekar)
Source: How to Make Money Trading the Ichimoku System (Balkrishna Sadekar)

Logic: Instant equilibrium check.

1. Bullish Regime: Price > Kumo (Cloud) AND Tenkan-sen > Kijun-sen AND Chikou Span (Lagging) > Price (26 periods ago).
2. Bearish Regime: Price < Kumo AND Tenkan-sen < Kijun-sen.
3. Neutral or No-Trade: Price inside the Kumo.
4. Trigger: The Kumo Breakout is the primary trend-following signal.

### Skill 2.3: The Impulse System (Elder)
Source: The New Trading for a Living

Logic: Color-coded bar censorship.

1. Green Bar: 13-period EMA is rising AND MACD-Histogram is rising. Buying allowed, shorting forbidden.
2. Red Bar: 13-period EMA is falling AND MACD-Histogram is falling. Shorting allowed, buying forbidden.
3. Blue Bar: Mixed signals (neutral or cash).

---

## 3. Agent Skill: Directional "Sniper" Entry
Role: Identify specific entry points for equities or directional option plays.

### Skill 3.1: Volatility Contraction Pattern (VCP) (Minervini)
Source: Think and Trade Like a Champion (Mark Minervini)

Logic: Supply absorption.

1. Scan: Look for tightening price action from left to right (for example 20% -> 10% -> 5% -> 2%).
2. Volume: Volume must dry up significantly during the final contraction.
3. Trigger: Buy on breakout above pivot with a volume spike.
4. Filter: Stock must be in a Stage 2 uptrend (Price > 200 SMA, 200 SMA trending up).

### Skill 3.2: Supply and Demand Freshness (Batus Sniper)
Source: Trading Price Action / AlgoTrading Blueprint

Logic: Institutional order flow.

1. Identify Zone: Locate imbalance candles (large body, small wick) that broke structure. The zone is the base of that candle.
2. Freshness Rule: If price returns to this zone for the first time, take the trade. If it touched earlier, zone is consumed and invalid.

### Skill 3.3: The Darvas Box (Darvas)
Source: How I Made $2,000,000 in the Stock Market

Logic: Momentum containment.

1. Setup: Identify a price range (box) where stock oscillates between clear top and bottom for more than 3 days.
2. Trigger: Buy when price penetrates the top of the box.
3. Stop: Place stop inside the box (one fraction below breakout).

---

## 4. Agent Skill: Options Strategy and Volatility Pricing
Role: Price insurance risk and select the correct derivative structure.

### Skill 4.1: Volatility Arbitrage (Natenberg and Chen)
Source: Option Volatility and Pricing / The Option Trader's Hedge Fund

Logic: Sell expensive insurance, buy cheap gamma.

1. Calculation: Compare Implied Volatility (IV) to Historical Volatility (HV).
2. Signal A (High IV): If IV > (HV * 1.25) AND IV Rank > 50 -> sell premium (Iron Condors, credit spreads).
3. Signal B (Low IV): If IV < HV -> buy premium (calendars, debit spreads) or do nothing.

### Skill 4.2: The DITM Call Replacement (Lowell)
Source: Get Rich with Options (Lee Lowell)

Logic: Stock replacement with defined risk.

1. Setup: Bullish signal from Sniper Agent.
2. Selection: Buy Deep-In-The-Money (DITM) call.
3. Delta: Must be 0.90 or higher.
4. Premium Check: Ensure time value is negligible. Pay mostly intrinsic value.

### Skill 4.3: The Iron Condor Insurance Policy (Chen and Sebastian)
Source: The Option Trader's Hedge Fund

Logic: Range-bound income generation (TOMIC core).

1. Market: Use on index (SPX/NDX style) or broad ETFs.
2. DTE: 30-45 days to expiration.
3. Strikes: Sell 20-delta call and 20-delta put (about 1 SD). Buy wings 10-20 points wide to define margin.
4. Management: Exit at 50% of max profit. Do not hold till expiration.

### Skill 4.4: Gamma Scalping (Sinclair and Passarelli)
Source: Option Trading (Sinclair) / Trading Option Greeks (Passarelli)

Logic: Hedge movement to capture volatility.

1. Setup: Long straddle (long volatility).
2. Action: Delta changes as underlying moves.
3. Rule: Buy or sell underlying to return net delta to zero. Lock move-based gains while keeping options open.

---

## 5. Agent Skill: Risk Management (The Actuary)
Role: The no-man. Validate or reject trades based on mathematical safety.

### Skill 5.1: The 2% and 6% Rules (Elder)
Source: The New Trading for a Living

1. Shark Bite (2%): Never risk more than 2% of account equity on one trade. (Entry - Stop) * Shares < 2% Equity.
2. Piranha Bite (6%): If monthly account drawdown hits 6%, cease all new trading for the month. Close losers. Risk manager locks the door.

### Skill 5.2: Volatility Targeting (Carver)
Source: Systematic Trading (Robert Carver)

Logic: Normalize risk across instruments.

1. Formula: Position Size = (Capital * Target Risk Factor) / (Instrument Daily Volatility).
2. Effect: If VIX doubles, size halves to prevent blow-ups.

### Skill 5.3: Half-Kelly Criteria (Carver and Sinclair)
Source: Systematic Trading / Option Trading

Logic: Optimal bet size without ruin.

1. Compute Kelly from win and loss profile.
2. Rule: Use half-Kelly (divide by 2) to reduce variance and psychological stress.

### Skill 5.4: Black Swan Hedging (Chen)
Source: The Option Trader's Hedge Fund

Logic: Reinsurance.

1. Rule: Allocate 1-2% of expected monthly profits (not total capital) to buy far OTM 5-delta puts on broad index.
2. Purpose: Protect against 3-sigma crash events.

---

## 6. Agent Skill: Psychology and Journaling
Role: Ensure continuous improvement and prevent emotional sabotage.

### Skill 6.1: The Trade Apgar Score (Elder)
Source: The New Trading for a Living

Logic: Quantify trade quality before entry.

1. Scorecard (0-10): Weekly Trend (2), Daily Trend (2), Value Zone (2), Reward/Risk Ratio (2), Market Pressure (2).
2. Rule: Only take trades with Apgar score > 7.

### Skill 6.2: Mental Environment Management (Douglas)
Source: The Disciplined Trader

Concept: Market has no power over you; your reaction matters.

1. Rule: Accept risk before entering. "I am willing to lose X to test this trade."
2. Process: Execute without hesitation when valid signal appears.

### Skill 6.3: Stress Regulation (Kiev)
Source: Mastering Trading Stress

Technique: Visualization.

1. Practice before market open: Visualize worst case (gap down, stop hit).
2. Rehearse calm response so fight-or-flight is not triggered live.

---

## 7. Technical Implementation Specs (OpenAlgo Context)
Source: AlgoTrading Agent Architecture

1. Data Feeds:
   - Use WebSocket for price and volume (live regime detection).
   - Use py_vollib_vectorized for real-time Greeks.
2. Database:
   - DuckDB for historical tick data (regime backtesting).
   - SQLite for trade journal and order logs.
3. Architecture:
   - Event Bus: ZeroMQ for inter-agent communication (Regime -> Risk -> Execution).
4. Slippage Control:
   - Use limit orders within value zone where possible.
   - Avoid market orders on wide spreads.

---

## 8. TOMIC Runtime Additions (Codex Implementation Layer)
Role: Convert strategy theory into safe, production runtime behavior.

### Skill 8.1: Startup, Warmup, and Restart Behavior
Logic: Restart must not behave like a brand-new market day.

1. On runtime start, hydrate required bars from history first, then switch to live WS updates.
2. Keep separate warmup rules by agent:
   - Regime warmup: enough bars for regime indicators.
   - Sniper warmup: enough bars for pattern detection.
3. Do not execute queued signals generated before warmup completion.
4. On restart, avoid replaying stale queue work from prior run context.

### Skill 8.2: Queue TTL and Anti-Stale Execution
Logic: A stale signal is a different trade.

1. Enqueued command must carry `created_at`.
2. Entry commands older than 10 seconds should expire and be dropped, not retried.
3. Retry is valid only for transient infrastructure failures, not stale market context.
4. Dead-letter classification should separate:
   - permanent input errors
   - freshness blocks
   - network timeouts

### Skill 8.3: Freshness Gates and Retry Policy
Logic: Freshness is a gate, not a hard failure.

1. If quote freshness fails (`STALE_QUOTE`), defer briefly; do not consume full retry budget immediately.
2. Freshness checks should use normalized symbols so equivalent keys map correctly.
3. If feed reconnects, pending valid commands may continue; expired ones must still be dropped.

### Skill 8.4: Market-Hours and Session Guard
Logic: No autonomous live execution outside market window.

1. Block new live entries when market is closed.
2. Allow observability, analytics, and warmup diagnostics even when closed.
3. Optional policy: allow paper-mode simulations after hours, never live orders.

### Skill 8.5: Instrument Scope and Context Filtering
Logic: Trade only actionable instruments.

1. Treat context symbols (for example VIX proxies) as analysis-only unless explicitly allowed.
2. Route execution candidates only for enabled underlyings.
3. Keep explicit skip reasons in router diagnostics for filtered symbols.

### Skill 8.6: Option Discovery and Expiry Discipline
Logic: Subscribe to useful options only.

1. Discovery should prioritize current week and next week expiries.
2. Subscribe focused strike bands around ATM based on strategy need.
3. Avoid broad chain subscriptions that increase noise and stale risk.
4. If expiry is missing, resolve from discovery before placing options orders.

### Skill 8.7: IV Availability and Volatility Agent Input Quality
Logic: Volatility strategies require valid IV inputs.

1. If WS payload lacks IV, mark volatility outputs as limited-confidence.
2. Allow fallback IV sources only if timestamp freshness is acceptable.
3. Record explicit "no IV input" blockers in diagnostics.

### Skill 8.8: Execution Payload Safety (Broker-Aware)
Logic: Correct order schema before broker call.

1. Ensure resolved `tradingsymbol` is never null at final execution stage.
2. Enforce lot-size multiples from broker/master-contract metadata.
3. Use broker-compatible product type per segment (for example MIS vs NRML rules).
4. Reject commands early with clear reason if lot/product/exchange mismatch is detected.

### Skill 8.9: Multi-Leg Strategy Wiring
Logic: Spread intent must map to multi-leg execution endpoint.

1. For IRON_CONDOR/BULL_PUT_SPREAD/BEAR_CALL_SPREAD with legs available:
   - route to `optionsmultiorder`.
2. Keep single-leg fallback only when strategy explicitly permits degradation.
3. Preserve leg ordering, ratio, and hedge symmetry for defined-risk structures.

### Skill 8.10: Directional Alignment Guard
Logic: Directional entries must match regime and strategy semantics.

1. Bullish directional play should not enqueue bearish leg by default.
2. Validate strategy-direction pair before risk sizing.
3. Reject contradictory combinations with explicit reason in journal.

### Skill 8.11: Position and TP/SL Lifecycle Policy
Logic: Entry without managed exit is incomplete.

1. Every executed directional entry should attach managed exit policy (TP/SL/trailing).
2. Exit reason must be journaled (`tp_hit`, `sl_hit`, `trail_hit`, `risk_cut`, `manual`).
3. Position reconciliation after restart must preserve open risk controls.

### Skill 8.12: Unified Feed/Execution Separation
Logic: Data source and execution destination may differ.

1. Feed broker may provide charts/options context.
2. Execution broker is authoritative for orders, positions, and realized PnL.
3. Symbol mapping must convert canonical contract tuple:
   - underlying + expiry + strike + option type -> broker tradingsymbol.

### Skill 8.13: Observability and "Why No Action"
Logic: Zero trades must still be explainable.

1. Publish per-cycle blocker list:
   - feed auth/freshness
   - warmup status
   - router blocks
   - risk rejects
   - execution rejects
2. Dashboard tables must include actionable reason text, not empty placeholders.
3. Keep counters for deferred, retried, dead-letter, and executed commands.

### Skill 8.14: Capital Allocation and Regime Priority Router
Logic: Conflict resolution should be deterministic.

1. Regime acts as master filter.
2. In congestion, prioritize volatility income playbooks over directional sniper entries.
3. Allocate capital by strategy bucket with hard caps to avoid concentration.

---

## 9. Default Operational Parameters (Suggested)
Role: Provide sane defaults that can be tuned per broker/day.

1. Queue TTL for entry commands: 10 seconds.
2. Signal loop interval: 3-5 seconds.
3. Cooldown after exit per symbol-side: 15-30 seconds.
4. Freshness quote max age: 1-3 seconds.
5. Max retries:
   - network timeout: up to 2 retries
   - freshness block: defer without consuming permanent retry count
   - validation/input error: 0 retries (fail fast)
6. Daily hard-stop (loss): 3-6% of deployable capital.
7. Keep 25-30% free margin reserve.
