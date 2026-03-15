"""
Sidecar: Fundamental Data Fetcher
Runs OUTSIDE the main OpenAlgo trading process using Playwright/openscreener.
Writes results to db/fundamentals_cache.json for the ScreenerService to read.

Usage:
    # Manual run:
    python scripts/fetch_fundamentals.py

    # Scheduled (cron/task scheduler) - recommended at 8:00 AM daily:
    # Windows Task Scheduler: python C:/algo/.../scripts/fetch_fundamentals.py
    # Linux cron: 0 8 * * 1-5 cd /path/to/openalgo && python scripts/fetch_fundamentals.py

    # With custom symbols file:
    python scripts/fetch_fundamentals.py --symbols-file symbols.txt

    # Sector-specific thresholds:
    Uses SECTOR_THRESHOLDS below instead of flat thresholds.
"""

import json
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

OUTPUT_FILE = os.getenv(
    "FUNDAMENTALS_CACHE_FILE",
    str(Path(__file__).parent.parent / "db" / "fundamentals_cache.json"),
)

# Sector-specific fundamental thresholds (fixes the "flat threshold" design concern)
SECTOR_THRESHOLDS = {
    "default": {"roce_min": 12, "debt_equity_max": 1.5, "promoter_min": 30, "fii_sell_max": 3},
    "NIFTYPVTBANK": {"roce_min": 10, "debt_equity_max": 8.0, "promoter_min": 20, "fii_sell_max": 5},
    "NIFTYPSUBANK": {"roce_min": 8, "debt_equity_max": 10.0, "promoter_min": 50, "fii_sell_max": 5},
    "NIFTYREALTY": {"roce_min": 8, "debt_equity_max": 2.0, "promoter_min": 40, "fii_sell_max": 3},
    "NIFTYIT": {"roce_min": 18, "debt_equity_max": 0.5, "promoter_min": 30, "fii_sell_max": 3},
    "NIFTYMETAL": {"roce_min": 8, "debt_equity_max": 2.0, "promoter_min": 30, "fii_sell_max": 4},
    "NIFTYENERGY": {"roce_min": 10, "debt_equity_max": 2.0, "promoter_min": 40, "fii_sell_max": 4},
}

STOCK_TO_SECTOR = {
    "HDFCBANK": "NIFTYPVTBANK", "ICICIBANK": "NIFTYPVTBANK", "KOTAKBANK": "NIFTYPVTBANK",
    "AXISBANK": "NIFTYPVTBANK", "INDUSINDBK": "NIFTYPVTBANK",
    "SBIN": "NIFTYPSUBANK", "BANKBARODA": "NIFTYPSUBANK", "PNB": "NIFTYPSUBANK",
    "TCS": "NIFTYIT", "INFY": "NIFTYIT", "HCLTECH": "NIFTYIT", "WIPRO": "NIFTYIT", "TECHM": "NIFTYIT",
    "TATASTEEL": "NIFTYMETAL", "JSWSTEEL": "NIFTYMETAL", "HINDALCO": "NIFTYMETAL",
    "RELIANCE": "NIFTYENERGY", "NTPC": "NIFTYENERGY", "ONGC": "NIFTYENERGY",
    "DLF": "NIFTYREALTY", "GODREJPROP": "NIFTYREALTY", "OBEROIRLTY": "NIFTYREALTY",
}

DEFAULT_FO_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "ITC", "SBIN",
    "BHARTIARTL", "KOTAKBANK", "LT", "AXISBANK", "MARUTI", "SUNPHARMA", "TATAMOTORS",
    "BAJFINANCE", "NTPC", "TATASTEEL", "WIPRO", "HCLTECH", "M&M", "POWERGRID",
    "ADANIENT", "ONGC", "DRREDDY", "CIPLA", "JSWSTEEL", "DIVISLAB", "HINDALCO",
    "BAJAJ-AUTO", "NESTLEIND", "BRITANNIA", "DLF", "INDUSINDBK", "COALINDIA",
    "BANKBARODA", "PNB", "TECHM", "APOLLOHOSP", "GODREJPROP",
]


def get_thresholds(symbol: str) -> dict:
    sector = STOCK_TO_SECTOR.get(symbol, "")
    return SECTOR_THRESHOLDS.get(sector, SECTOR_THRESHOLDS["default"])


def apply_gate(symbol: str, data: dict) -> dict:
    """Apply sector-specific fundamental pass/fail rules."""
    th = get_thresholds(symbol)
    reasons = []

    roce = data.get("roce")
    if roce is not None and roce < th["roce_min"]:
        reasons.append(f"ROCE {roce:.1f}% < {th['roce_min']}%")

    de = data.get("debt_equity")
    if de is not None and de > th["debt_equity_max"]:
        reasons.append(f"D/E {de:.2f} > {th['debt_equity_max']}")

    prom = data.get("promoter_holding")
    if prom is not None and prom < th["promoter_min"]:
        reasons.append(f"Promoter {prom:.1f}% < {th['promoter_min']}%")

    fii = data.get("fii_change_qoq")
    if fii is not None and fii < -th["fii_sell_max"]:
        reasons.append(f"FII selling {abs(fii):.1f}% > {th['fii_sell_max']}%")

    data["cleared"] = len(reasons) == 0
    data["block_reason"] = "; ".join(reasons) if reasons else ""
    data["sector"] = STOCK_TO_SECTOR.get(symbol, "unknown")
    data["thresholds_used"] = th
    return data


def safe_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def fetch_all(symbols: list) -> dict:
    """Fetch fundamental data for all symbols using openscreener."""
    try:
        from openscreener import Stock
    except ImportError:
        print("ERROR: openscreener not installed. Run: pip install openscreener && python -m playwright install chromium")
        sys.exit(1)

    profiles = {}
    batch_size = 5

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        print(f"  Fetching batch {i // batch_size + 1}/{(len(symbols) + batch_size - 1) // batch_size}: {batch}")

        try:
            stock_batch = Stock.batch(batch)
            data = stock_batch.fetch(["summary", "ratios", "shareholding", "quarterly_results"])

            for sym in batch:
                sym_data = data.get(sym, {})
                summary = sym_data.get("summary", {})
                ratios = sym_data.get("ratios", {})
                shareholding = sym_data.get("shareholding", [])
                quarterly = sym_data.get("quarterly_results", [])

                summary_ratios = summary.get("ratios", {}) if isinstance(summary, dict) else {}

                profile = {
                    "symbol": sym,
                    "roce": safe_float(ratios.get("roce_percent")),
                    "pe": safe_float(ratios.get("pe") or summary_ratios.get("pe")),
                    "debt_equity": safe_float(ratios.get("debt_to_equity")),
                    "market_cap": safe_float(summary_ratios.get("market_cap")),
                    "promoter_holding": None,
                    "fii_change_qoq": None,
                    "quarterly_profit_growth": None,
                }

                if shareholding and len(shareholding) >= 1:
                    latest = shareholding[0] if isinstance(shareholding[0], dict) else {}
                    profile["promoter_holding"] = safe_float(latest.get("promoter"))
                    if len(shareholding) >= 2:
                        prev = shareholding[1] if isinstance(shareholding[1], dict) else {}
                        curr_fii = safe_float(latest.get("fii"))
                        prev_fii = safe_float(prev.get("fii"))
                        if curr_fii is not None and prev_fii is not None:
                            profile["fii_change_qoq"] = round(curr_fii - prev_fii, 2)

                if quarterly and len(quarterly) >= 2:
                    curr_q = quarterly[0] if isinstance(quarterly[0], dict) else {}
                    prev_q = quarterly[1] if isinstance(quarterly[1], dict) else {}
                    curr_profit = safe_float(curr_q.get("net_profit"))
                    prev_profit = safe_float(prev_q.get("net_profit"))
                    if curr_profit is not None and prev_profit is not None and prev_profit != 0:
                        profile["quarterly_profit_growth"] = round(((curr_profit - prev_profit) / abs(prev_profit)) * 100, 2)

                profile = apply_gate(sym, profile)
                profiles[sym] = profile

        except Exception as e:
            print(f"  ERROR: Batch failed: {e}")
            for sym in batch:
                profiles[sym] = {"symbol": sym, "cleared": True, "block_reason": f"fetch_error: {e}"}

    return profiles


def main():
    parser = argparse.ArgumentParser(description="Fetch fundamental data for F&O stocks")
    parser.add_argument("--symbols-file", help="Path to file with one symbol per line")
    parser.add_argument("--output", default=OUTPUT_FILE, help="Output JSON file path")
    args = parser.parse_args()

    if args.symbols_file:
        with open(args.symbols_file) as f:
            symbols = [line.strip().upper() for line in f if line.strip()]
    else:
        symbols = DEFAULT_FO_SYMBOLS

    print(f"=== Fundamental Data Fetcher ===")
    print(f"Symbols: {len(symbols)}")
    print(f"Output: {args.output}")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()

    profiles = fetch_all(symbols)

    cleared = sum(1 for p in profiles.values() if p.get("cleared"))
    blocked = sum(1 for p in profiles.values() if not p.get("cleared"))

    output = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(),
        "symbol_count": len(profiles),
        "cleared_count": cleared,
        "blocked_count": blocked,
        "profiles": profiles,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")

    print(f"\nDone. {cleared} cleared, {blocked} blocked out of {len(profiles)} symbols.")
    print(f"Cache written to: {args.output}")


if __name__ == "__main__":
    main()
