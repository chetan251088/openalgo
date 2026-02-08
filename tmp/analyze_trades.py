import json
from collections import defaultdict
from datetime import datetime

with open(r'C:\algo\openalgov2\openalgo\auto_trade_log_2026-02-06 (2).json') as f:
    data = json.load(f)

# Group by tradeId
trades_by_id = defaultdict(list)
for rec in data:
    trades_by_id[rec['tradeId']].append(rec)

# Build trade objects
trades = []
for tid, records in trades_by_id.items():
    entries = [r for r in records if r['type'] == 'ENTRY']
    exits = [r for r in records if r['type'] == 'EXIT']
    if not exits:
        continue  # incomplete trade
    
    exit_rec = exits[0]
    mode = exit_rec['mode']
    
    # Compute weighted avg entry price
    total_qty_entry = sum(e['qty'] for e in entries)
    wavg_entry = sum(e['price'] * e['qty'] for e in entries) / total_qty_entry if total_qty_entry else 0
    
    exit_price = exit_rec['price']
    exit_qty = exit_rec['qty']
    
    # Computed P&L = (exitPrice - avgEntryPrice) * exitQty
    computed_pnl = (exit_price - wavg_entry) * exit_qty
    logged_pnl = exit_rec.get('pnl', 0) or 0
    
    has_average = any(e['reason'] == 'Average' for e in entries)
    
    entry_ts = min(datetime.fromisoformat(e['ts'].replace('Z', '+00:00')) for e in entries)
    exit_ts = datetime.fromisoformat(exit_rec['ts'].replace('Z', '+00:00'))
    hold_ms = exit_rec.get('holdMs', 0) or 0
    
    trades.append({
        'tradeId': tid,
        'mode': mode,
        'side': exit_rec.get('side'),
        'symbol': exit_rec.get('symbol'),
        'entries': entries,
        'exit': exit_rec,
        'avgEntryPrice': wavg_entry,
        'exitPrice': exit_price,
        'exitQty': exit_qty,
        'entryQty': total_qty_entry,
        'computedPnl': computed_pnl,
        'loggedPnl': logged_pnl,
        'pnlDiscrepancy': abs(computed_pnl - logged_pnl),
        'exitReason': exit_rec.get('reason'),
        'holdMs': hold_ms,
        'hasAverage': has_average,
        'entryTime': entry_ts,
        'exitTime': exit_ts,
    })

print("=" * 80)
print("TRADE LOG ANALYSIS: auto_trade_log_2026-02-06 (2).json")
print("=" * 80)

# 1. Total number of trades
print(f"\n{'='*80}")
print("1. TOTAL TRADES (entry/exit pairs grouped by tradeId)")
print(f"{'='*80}")
print(f"   Total trades: {len(trades)}")
total_records = len(data)
total_entries = sum(1 for r in data if r['type'] == 'ENTRY')
total_exits = sum(1 for r in data if r['type'] == 'EXIT')
print(f"   Total log records: {total_records} (Entries: {total_entries}, Exits: {total_exits})")

# 2. Paper vs LIVE split
print(f"\n{'='*80}")
print("2. PAPER vs LIVE TRADE SPLIT")
print(f"{'='*80}")
paper = [t for t in trades if t['mode'] == 'PAPER']
live = [t for t in trades if t['mode'] == 'LIVE']
print(f"   PAPER trades: {len(paper)}")
print(f"   LIVE trades:  {len(live)}")

# 3. Total P&L
print(f"\n{'='*80}")
print("3. TOTAL P&L (using logged pnl field)")
print(f"{'='*80}")
paper_pnl = sum(t['loggedPnl'] for t in paper)
live_pnl = sum(t['loggedPnl'] for t in live)
print(f"   PAPER Total P&L: ₹{paper_pnl:,.2f}")
print(f"   LIVE Total P&L:  ₹{live_pnl:,.2f}")
print(f"   COMBINED P&L:    ₹{paper_pnl + live_pnl:,.2f}")

# 4. Win/Loss ratio
print(f"\n{'='*80}")
print("4. WIN/LOSS RATIO")
print(f"{'='*80}")
for label, group in [("PAPER", paper), ("LIVE", live)]:
    wins = [t for t in group if t['loggedPnl'] > 0]
    losses = [t for t in group if t['loggedPnl'] <= 0]
    ratio = len(wins) / len(losses) if losses else float('inf')
    pct = len(wins) / len(group) * 100 if group else 0
    print(f"   {label}: {len(wins)}W / {len(losses)}L  (Win Rate: {pct:.1f}%, W/L Ratio: {ratio:.2f})")

# 5. Avg win vs avg loss
print(f"\n{'='*80}")
print("5. AVERAGE WIN vs AVERAGE LOSS")
print(f"{'='*80}")
for label, group in [("PAPER", paper), ("LIVE", live)]:
    wins = [t['loggedPnl'] for t in group if t['loggedPnl'] > 0]
    losses = [t['loggedPnl'] for t in group if t['loggedPnl'] < 0]
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    print(f"   {label}:")
    print(f"      Avg Win:  ₹{avg_win:,.2f} ({len(wins)} trades)")
    print(f"      Avg Loss: ₹{avg_loss:,.2f} ({len(losses)} trades)")
    if avg_loss != 0:
        print(f"      Reward/Risk Ratio: {abs(avg_win/avg_loss):.2f}")

# 6. Breakdown by exit reason
print(f"\n{'='*80}")
print("6. BREAKDOWN BY EXIT REASON")
print(f"{'='*80}")
by_reason = defaultdict(list)
for t in trades:
    by_reason[t['exitReason']].append(t)

for reason in sorted(by_reason.keys()):
    group = by_reason[reason]
    total = sum(t['loggedPnl'] for t in group)
    wins = sum(1 for t in group if t['loggedPnl'] > 0)
    losses = sum(1 for t in group if t['loggedPnl'] <= 0)
    avg = total / len(group) if group else 0
    print(f"   {reason:20s}: {len(group):3d} trades | P&L: ₹{total:>10,.2f} | Avg: ₹{avg:>8,.2f} | W:{wins} L:{losses}")

# Also by mode
print(f"\n   By Mode + Reason:")
for mode_label, mode_group in [("PAPER", paper), ("LIVE", live)]:
    print(f"   --- {mode_label} ---")
    mode_by_reason = defaultdict(list)
    for t in mode_group:
        mode_by_reason[t['exitReason']].append(t)
    for reason in sorted(mode_by_reason.keys()):
        group = mode_by_reason[reason]
        total = sum(t['loggedPnl'] for t in group)
        wins = sum(1 for t in group if t['loggedPnl'] > 0)
        losses = sum(1 for t in group if t['loggedPnl'] <= 0)
        print(f"      {reason:20s}: {len(group):3d} trades | P&L: ₹{total:>10,.2f} | W:{wins} L:{losses}")

# 7. Average hold time
print(f"\n{'='*80}")
print("7. AVERAGE HOLD TIME")
print(f"{'='*80}")
all_holds = [t['holdMs'] for t in trades if t['holdMs'] > 0]
avg_hold = sum(all_holds) / len(all_holds) if all_holds else 0
print(f"   Overall avg hold time: {avg_hold/1000:.1f}s ({avg_hold:.0f}ms)")
for label, group in [("PAPER", paper), ("LIVE", live)]:
    holds = [t['holdMs'] for t in group if t['holdMs'] > 0]
    avg = sum(holds) / len(holds) if holds else 0
    print(f"   {label} avg hold time: {avg/1000:.1f}s")

# 8. Distribution of hold times
print(f"\n{'='*80}")
print("8. HOLD TIME DISTRIBUTION")
print(f"{'='*80}")
buckets = {'< 5s': 0, '5-15s': 0, '15-30s': 0, '30-60s': 0, '> 60s': 0}
for t in trades:
    s = t['holdMs'] / 1000
    if s < 5:
        buckets['< 5s'] += 1
    elif s < 15:
        buckets['5-15s'] += 1
    elif s < 30:
        buckets['15-30s'] += 1
    elif s < 60:
        buckets['30-60s'] += 1
    else:
        buckets['> 60s'] += 1

for k, v in buckets.items():
    pct = v / len(trades) * 100
    bar = '█' * int(pct / 2)
    print(f"   {k:10s}: {v:3d} ({pct:5.1f}%) {bar}")

# 9. Biggest winners and losers
print(f"\n{'='*80}")
print("9. TOP 5 BIGGEST WINNERS")
print(f"{'='*80}")
sorted_by_pnl = sorted(trades, key=lambda t: t['loggedPnl'], reverse=True)
for i, t in enumerate(sorted_by_pnl[:5]):
    print(f"   #{i+1}: ₹{t['loggedPnl']:>10,.2f} | {t['mode']:5s} | {t['symbol']:30s} | {t['exitReason']:20s} | Hold: {t['holdMs']/1000:.1f}s")

print(f"\n   TOP 5 BIGGEST LOSERS")
for i, t in enumerate(sorted_by_pnl[-5:]):
    print(f"   #{i+1}: ₹{t['loggedPnl']:>10,.2f} | {t['mode']:5s} | {t['symbol']:30s} | {t['exitReason']:20s} | Hold: {t['holdMs']/1000:.1f}s")

# 10. P&L discrepancy check
print(f"\n{'='*80}")
print("10. P&L DISCREPANCY CHECK (computed vs logged)")
print(f"{'='*80}")
THRESHOLD = 0.01
discrepancies = [(t, t['pnlDiscrepancy']) for t in trades if t['pnlDiscrepancy'] > THRESHOLD]
print(f"   Trades with discrepancy > ₹0.01: {len(discrepancies)} out of {len(trades)}")
if discrepancies:
    discrepancies.sort(key=lambda x: x[1], reverse=True)
    print(f"\n   {'TradeId':40s} | {'Computed':>12s} | {'Logged':>12s} | {'Diff':>12s} | {'Mode':5s} | Reason")
    print(f"   {'-'*40} | {'-'*12} | {'-'*12} | {'-'*12} | {'-'*5} | {'-'*15}")
    for t, disc in discrepancies[:20]:
        print(f"   {t['tradeId']:40s} | ₹{t['computedPnl']:>10,.2f} | ₹{t['loggedPnl']:>10,.2f} | ₹{disc:>10,.2f} | {t['mode']:5s} | {t['exitReason']}")
    
    # Summary
    total_disc = sum(d for _, d in discrepancies)
    avg_disc = total_disc / len(discrepancies) if discrepancies else 0
    max_disc = max(d for _, d in discrepancies)
    print(f"\n   Summary: avg discrepancy ₹{avg_disc:,.2f}, max ₹{max_disc:,.2f}")
    print(f"   NOTE: The logged pnl appears to include running/cumulative adjustments")
    print(f"         beyond simple (exitPrice - entryPrice) * qty")
else:
    print("   No discrepancies found!")

# 11. Trail SL losses
print(f"\n{'='*80}")
print("11. TRAIL SL LOSS ANALYSIS")
print(f"{'='*80}")
trail_sl_trades = [t for t in trades if t['exitReason'] == 'Trail SL']
trail_sl_losses = [t for t in trail_sl_trades if t['loggedPnl'] < 0]
trail_sl_wins = [t for t in trail_sl_trades if t['loggedPnl'] > 0]
trail_loss_pct = len(trail_sl_losses) / len(trades) * 100 if trades else 0
trail_loss_of_trail = len(trail_sl_losses) / len(trail_sl_trades) * 100 if trail_sl_trades else 0

print(f"   Total Trail SL exits: {len(trail_sl_trades)}")
print(f"   Trail SL wins:   {len(trail_sl_wins)} (avg ₹{sum(t['loggedPnl'] for t in trail_sl_wins)/len(trail_sl_wins) if trail_sl_wins else 0:,.2f})")
print(f"   Trail SL losses: {len(trail_sl_losses)} (avg ₹{sum(t['loggedPnl'] for t in trail_sl_losses)/len(trail_sl_losses) if trail_sl_losses else 0:,.2f})")
print(f"   % of ALL trades exited at loss via Trail SL: {trail_loss_pct:.1f}%")
print(f"   % of Trail SL exits that were losses: {trail_loss_of_trail:.1f}%")

if trail_sl_losses:
    print(f"\n   Trail SL loss details:")
    for t in sorted(trail_sl_losses, key=lambda x: x['loggedPnl']):
        print(f"      ₹{t['loggedPnl']:>10,.2f} | {t['mode']:5s} | {t['symbol']:30s} | Hold: {t['holdMs']/1000:.1f}s | AvgEntry: {t['avgEntryPrice']:.2f} -> Exit: {t['exitPrice']:.2f}")
    
    print(f"\n   INSIGHT: {trail_loss_of_trail:.0f}% of Trail SL exits resulted in losses.")
    if trail_loss_of_trail > 30:
        print(f"   ⚠️  This suggests the trailing stop may be TOO TIGHT, locking in losses before")
        print(f"      the position has enough room to move into profit.")
    else:
        print(f"   Trail SL seems reasonably calibrated.")

# By mode
print(f"\n   By Mode:")
for label, group in [("PAPER", paper), ("LIVE", live)]:
    ts_trades = [t for t in group if t['exitReason'] == 'Trail SL']
    ts_losses = [t for t in ts_trades if t['loggedPnl'] < 0]
    pct = len(ts_losses) / len(ts_trades) * 100 if ts_trades else 0
    print(f"   {label}: {len(ts_losses)} losses / {len(ts_trades)} Trail SL trades ({pct:.1f}% loss rate)")

# 12. Average (scaling in) analysis
print(f"\n{'='*80}")
print("12. AVERAGING (SCALE-IN) ANALYSIS")
print(f"{'='*80}")
avg_trades = [t for t in trades if t['hasAverage']]
non_avg_trades = [t for t in trades if not t['hasAverage']]
print(f"   Trades with Averaging: {len(avg_trades)}")
print(f"   Trades without Averaging: {len(non_avg_trades)}")

if avg_trades:
    avg_pnl = sum(t['loggedPnl'] for t in avg_trades)
    avg_wins = sum(1 for t in avg_trades if t['loggedPnl'] > 0)
    avg_losses = sum(1 for t in avg_trades if t['loggedPnl'] <= 0)
    avg_avg = avg_pnl / len(avg_trades)
    print(f"\n   Averaged trades total P&L: ₹{avg_pnl:,.2f}")
    print(f"   Averaged trades avg P&L:   ₹{avg_avg:,.2f}")
    print(f"   Win/Loss: {avg_wins}W / {avg_losses}L ({avg_wins/len(avg_trades)*100:.1f}% win rate)")
    
    print(f"\n   Detail of each averaged trade:")
    for t in avg_trades:
        num_entries = len(t['entries'])
        entry_details = " + ".join(f"{e['qty']}@{e['price']}" for e in t['entries'])
        result = "WIN" if t['loggedPnl'] > 0 else "LOSS"
        print(f"      {t['mode']:5s} | {result:4s} | ₹{t['loggedPnl']:>10,.2f} | Entries: {entry_details} -> Exit: {t['exitQty']}@{t['exitPrice']} | {t['exitReason']}")

    # Compare with non-averaged
    if non_avg_trades:
        non_avg_pnl = sum(t['loggedPnl'] for t in non_avg_trades) / len(non_avg_trades)
        print(f"\n   Comparison:")
        print(f"      Avg P&L (with averaging):    ₹{avg_avg:,.2f}")
        print(f"      Avg P&L (without averaging):  ₹{non_avg_pnl:,.2f}")

# Summary
print(f"\n{'='*80}")
print("EXECUTIVE SUMMARY")
print(f"{'='*80}")
total_pnl = sum(t['loggedPnl'] for t in trades)
wins_all = sum(1 for t in trades if t['loggedPnl'] > 0)
losses_all = sum(1 for t in trades if t['loggedPnl'] <= 0)
print(f"   Total Trades: {len(trades)} ({len(paper)} Paper, {len(live)} Live)")
print(f"   Combined P&L: ₹{total_pnl:,.2f}")
print(f"   Paper P&L:    ₹{paper_pnl:,.2f}  |  Live P&L: ₹{live_pnl:,.2f}")
print(f"   Overall Win Rate: {wins_all}/{len(trades)} = {wins_all/len(trades)*100:.1f}%")
print(f"   Session Time: {trades[0]['entryTime'].strftime('%H:%M:%S')} - {trades[-1]['exitTime'].strftime('%H:%M:%S')} UTC")
print(f"   Avg Hold: {avg_hold/1000:.1f}s")
print(f"   Trail SL Loss Rate: {trail_loss_of_trail:.0f}% of Trail SL exits are losses")
if discrepancies:
    print(f"   P&L Discrepancies: {len(discrepancies)} trades have computed ≠ logged PnL")
