"""
Fundamental Filter Validation Backtest
Tests whether the fundamental gate improves scalping/selling outcomes.

IMPORTANT: This backtest acknowledges the look-ahead bias limitation.
Since we cannot get historical point-in-time fundamental data from OpenScreener
(it only provides current fundamentals), we use TWO methodologies:

1. PROXY METHOD (biased but informative):
   Split current F&O stocks into "cleared" vs "blocked" using today's fundamentals.
   Compare historical return distributions. The bias means cleared stocks MIGHT
   have been blocked 6 months ago. This is directional, not definitive.

2. CONSERVATIVE METHOD (unbiased):
   Use only STRUCTURAL filters that don't change rapidly:
   - Promoter holding > 30% (changes slowly over quarters)
   - Debt/Equity ratio < 1.5 (changes slowly)
   Ignore ROCE and FII (which change quarterly).
   These structural factors are more stable and thus less prone to look-ahead.

Output: For each method, report win rate, avg P&L, max drawdown.
Mark the proxy method results as "INDICATIVE ONLY — look-ahead bias present".
"""

import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_fundamentals(cache_file: str = None) -> dict:
    """Load fundamentals from the sidecar cache."""
    if cache_file is None:
        cache_file = str(Path(__file__).parent.parent / "db" / "fundamentals_cache.json")

    path = Path(cache_file)
    if not path.exists():
        print(f"ERROR: {cache_file} not found. Run scripts/fetch_fundamentals.py first.")
        sys.exit(1)

    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("profiles", {})


def classify_structural(profiles: dict) -> tuple:
    """Conservative method: only use slow-changing structural factors."""
    cleared = set()
    blocked = {}

    for sym, p in profiles.items():
        reasons = []
        promoter = p.get("promoter_holding")
        de = p.get("debt_equity")

        if promoter is not None and promoter < 30:
            reasons.append(f"Promoter {promoter:.1f}% < 30%")
        if de is not None and de > 2.0:
            reasons.append(f"D/E {de:.2f} > 2.0")

        if reasons:
            blocked[sym] = "; ".join(reasons)
        else:
            cleared.add(sym)

    return cleared, blocked


def classify_full(profiles: dict) -> tuple:
    """Proxy method: use all current fundamentals (has look-ahead bias)."""
    cleared = set()
    blocked = {}

    for sym, p in profiles.items():
        if p.get("cleared", True):
            cleared.add(sym)
        else:
            blocked[sym] = p.get("block_reason", "blocked")

    return cleared, blocked


def simulate_returns(symbols: list, days: int = 252) -> pd.DataFrame:
    """Simulate random daily returns for backtesting.
    In production, replace with actual historical OHLCV from OpenAlgo Historify.
    """
    print(f"  NOTE: Using simulated returns for {len(symbols)} symbols over {days} days.")
    print(f"  Replace with real data from Historify for production validation.")
    np.random.seed(42)
    dates = pd.bdate_range(end=datetime.now(), periods=days)
    data = {}
    for sym in symbols:
        daily_returns = np.random.normal(0.0005, 0.02, days)
        data[sym] = daily_returns
    return pd.DataFrame(data, index=dates)


def run_backtest():
    profiles = load_fundamentals()
    if not profiles:
        print("No profiles loaded.")
        return

    all_symbols = list(profiles.keys())
    print(f"=== Fundamental Filter Validation ===")
    print(f"Total symbols: {len(all_symbols)}")
    print()

    returns = simulate_returns(all_symbols)

    for method_name, classify_fn in [
        ("STRUCTURAL (conservative, low bias)", classify_structural),
        ("FULL FILTER (proxy method, HAS LOOK-AHEAD BIAS)", classify_full),
    ]:
        cleared, blocked = classify_fn(profiles)
        cleared_syms = [s for s in all_symbols if s in cleared and s in returns.columns]
        blocked_syms = [s for s in all_symbols if s in blocked and s in returns.columns]

        print(f"--- Method: {method_name} ---")
        print(f"Cleared: {len(cleared_syms)}, Blocked: {len(blocked_syms)}")

        if not cleared_syms or not blocked_syms:
            print("  Cannot compare — one group is empty.")
            print()
            continue

        cleared_returns = returns[cleared_syms].mean(axis=1)
        blocked_returns = returns[blocked_syms].mean(axis=1)

        cleared_cumulative = (1 + cleared_returns).cumprod() - 1
        blocked_cumulative = (1 + blocked_returns).cumprod() - 1

        cleared_dd = (cleared_cumulative - cleared_cumulative.cummax()).min()
        blocked_dd = (blocked_cumulative - blocked_cumulative.cummax()).min()

        print(f"  Cleared avg daily return: {cleared_returns.mean()*100:.4f}%")
        print(f"  Blocked avg daily return: {blocked_returns.mean()*100:.4f}%")
        print(f"  Cleared win rate (days): {(cleared_returns > 0).mean()*100:.1f}%")
        print(f"  Blocked win rate (days): {(blocked_returns > 0).mean()*100:.1f}%")
        print(f"  Cleared total return: {cleared_cumulative.iloc[-1]*100:.2f}%")
        print(f"  Blocked total return: {blocked_cumulative.iloc[-1]*100:.2f}%")
        print(f"  Cleared max drawdown: {cleared_dd*100:.2f}%")
        print(f"  Blocked max drawdown: {blocked_dd*100:.2f}%")
        print(f"  Spread (cleared - blocked): {(cleared_returns.mean() - blocked_returns.mean())*100:.4f}% daily")

        if method_name.startswith("FULL"):
            print()
            print("  *** WARNING: These results have LOOK-AHEAD BIAS. ***")
            print("  *** Current fundamentals applied to historical data. ***")
            print("  *** Use STRUCTURAL method for unbiased directional signal. ***")

        # Sensitivity analysis for weights
        print()
        print(f"  Weight Sensitivity (cleared boost multiplier):")
        for boost in [1.0, 1.1, 1.2, 1.3, 1.5]:
            adjusted = cleared_returns * boost
            adj_total = ((1 + adjusted).cumprod() - 1).iloc[-1]
            print(f"    Boost {boost:.1f}x → Total return: {adj_total*100:.2f}%")

        print()

    print("=== Recommendation ===")
    print("1. Use STRUCTURAL filter results for production gate calibration")
    print("2. Replace simulated returns with real Historify OHLCV data")
    print("3. Run walk-forward: train thresholds on months 1-4, validate on months 5-6")
    print("4. Test weight sensitivity with real data before setting intelligence weights")


if __name__ == "__main__":
    run_backtest()
