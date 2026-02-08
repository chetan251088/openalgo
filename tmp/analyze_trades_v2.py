import json
from collections import defaultdict

with open(r'C:\algo\openalgov2\openalgo\auto_trade_log_2026-02-06 (5).json', 'r') as f:
    data = json.load(f)

trades = defaultdict(list)
orphan_exits = []

for entry in data:
    tid = entry.get('tradeId')
    if tid:
        trades[tid].append(entry)
    else:
        orphan_exits.append(entry)

print('='*130)
print(f'Total JSON entries: {len(data)}')
print(f'Unique tradeIds: {len(trades)}')
print(f'Orphan exits (no tradeId): {len(orphan_exits)}')
print()

results = []
trade_num = 0
unmatched = []

for tid, events in trades.items():
    entries = [e for e in events if e['type'] == 'ENTRY']
    exits = [e for e in events if e['type'] == 'EXIT']
    
    if not exits:
        unmatched.append(('NO EXIT', tid, entries))
        continue
    
    total_entry_qty = sum(e['qty'] for e in entries)
    weighted_entry_price = sum(e['price'] * e['qty'] for e in entries) / total_entry_qty if total_entry_qty else 0
    
    exit_event = exits[0]
    exit_price = exit_event['price']
    exit_qty = exit_event['qty']
    logged_pnl = exit_event['pnl']
    
    calc_pnl = (exit_price - weighted_entry_price) * exit_qty
    
    trade_num += 1
    disc = abs(logged_pnl - calc_pnl) > 0.01
    sign_mm = (logged_pnl > 0 and calc_pnl < 0) or (logged_pnl < 0 and calc_pnl > 0)
    
    results.append({
        'num': trade_num, 'symbol': entries[0]['symbol'], 'side': entries[0]['side'],
        'mode': entries[0]['mode'], 'entry_price': weighted_entry_price,
        'exit_price': exit_price, 'entry_qty': total_entry_qty, 'exit_qty': exit_qty,
        'logged_pnl': logged_pnl, 'calc_pnl': calc_pnl, 'reason': exit_event['reason'],
        'num_entries': len(entries), 'discrepancy': disc, 'sign_mismatch': sign_mm, 'tradeId': tid
    })

hdr = f"{'#':>3} | {'Symbol':<26} | {'Side':>4} | {'EntryP':>8} | {'ExitP':>8} | {'Qty':>4} | {'Logged PnL':>12} | {'Calc PnL':>12} | {'Match':>8} | {'Reason':<14}"
print(hdr)
print('-'*130)

total_logged = 0
total_calc = 0
mismatch_count = 0
sign_mismatch_count = 0
symbol_counts = defaultdict(int)

for r in results:
    total_logged += r['logged_pnl']
    total_calc += r['calc_pnl']
    symbol_counts[r['symbol']] += 1
    
    match_str = 'OK' if not r['discrepancy'] else 'DIFF'
    if r['sign_mismatch']:
        match_str = 'SIGN!!'
        sign_mismatch_count += 1
    if r['discrepancy']:
        mismatch_count += 1
    
    avg_note = f' ({r["num_entries"]}x avg)' if r['num_entries'] > 1 else ''
    print(f"{r['num']:>3} | {r['symbol']:<26} | {r['side']:>4} | {r['entry_price']:>8.2f} | {r['exit_price']:>8.2f} | {r['exit_qty']:>4} | {r['logged_pnl']:>12.2f} | {r['calc_pnl']:>12.2f} | {match_str:>8} | {r['reason']:<14}{avg_note}")

if orphan_exits:
    print()
    print('ORPHAN EXITS (no tradeId):')
    for o in orphan_exits:
        total_logged += o['pnl']
        print(f"    Symbol: {o['symbol']}, Price: {o['price']}, Qty: {o['qty']}, PnL: {o['pnl']:.2f}, Reason: {o['reason']}")

print()
print('='*130)
print('SUMMARY')
print('='*130)
print(f'Total completed trades:     {len(results)}')
print(f'Orphan exits (no tradeId):  {len(orphan_exits)}')
print(f'Trades with PnL mismatch:   {mismatch_count}')
print(f'Trades with SIGN mismatch:  {sign_mismatch_count}')
print(f'Unmatched entries (no exit): {len(unmatched)}')
print()
print(f'Total Logged PnL (all exits):  {total_logged:>12.2f}')
print(f'Total Calculated PnL:          {total_calc:>12.2f}')
print(f'Discrepancy (Logged - Calc):   {total_logged - total_calc:>12.2f}')
print()
print(f'Broker MTM reported:           -1427.00')
print(f'Auto-trader sessionPnl:        +1268.00')
print(f'Gap broker vs auto-trader:     {-1427 - 1268:>12.2f}')
print()
print('TRADES BY SYMBOL:')
for sym, cnt in sorted(symbol_counts.items()):
    print(f'  {sym:<28} : {cnt} trades')
print(f'  Total unique symbols/strikes: {len(symbol_counts)}')
print()
print('='*130)
print('FLAGGED TRADES (Logged PnL vs Calculated PnL mismatch):')
print('='*130)
for r in results:
    if r['discrepancy']:
        diff = r['logged_pnl'] - r['calc_pnl']
        sign_note = ' *** SIGN MISMATCH ***' if r['sign_mismatch'] else ''
        print(f"  #{r['num']:>2} {r['symbol']:<26} Logged:{r['logged_pnl']:>10.2f}  Calc:{r['calc_pnl']:>10.2f}  Diff:{diff:>10.2f}  Reason:{r['reason']}{sign_note}")

print()
print('='*130)
print('PnL BY SYMBOL (Calculated / what broker sees):')
print('='*130)
sym_logged = defaultdict(float)
sym_calc = defaultdict(float)
for r in results:
    sym_logged[r['symbol']] += r['logged_pnl']
    sym_calc[r['symbol']] += r['calc_pnl']
for o in orphan_exits:
    sym_logged[o['symbol']] += o['pnl']

for sym in sorted(set(list(sym_logged.keys()) + list(sym_calc.keys()))):
    print(f"  {sym:<28} Logged: {sym_logged.get(sym,0):>10.2f}  Calc: {sym_calc.get(sym,0):>10.2f}  Diff: {sym_logged.get(sym,0)-sym_calc.get(sym,0):>10.2f}")

# Check multi-entry trades
print()
print('='*130)
print('MULTI-ENTRY TRADES (qty mismatch entry vs exit):')
print('='*130)
for tid, events in trades.items():
    entries_here = [e for e in events if e['type']=='ENTRY']
    exits_here = [e for e in events if e['type']=='EXIT']
    entry_qty = sum(e['qty'] for e in entries_here)
    exit_qty = sum(e['qty'] for e in exits_here)
    if entry_qty != exit_qty or len(entries_here) > 1:
        print(f"  TradeId: {tid[:20]}... Symbol: {entries_here[0]['symbol']}")
        for e in entries_here:
            print(f"    ENTRY: price={e['price']}, qty={e['qty']}, reason={e['reason']}")
        for e in exits_here:
            print(f"    EXIT:  price={e['price']}, qty={e['qty']}, reason={e['reason']}, pnl={e['pnl']:.2f}")
        print(f"    Entry total qty: {entry_qty}, Exit total qty: {exit_qty}, UNEXITED: {entry_qty - exit_qty}")
        print()
