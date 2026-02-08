import json, sys

with open(r'C:\algo\openalgov2\openalgo\auto_trade_log_2026-02-06 (5).json', 'r') as f:
    data = json.load(f)

entries = {}
trades = []
for ev in data:
    tid = ev.get('tradeId', '')
    if ev.get('type') == 'ENTRY':
        entries[tid] = ev
    elif ev.get('type') == 'EXIT' and tid in entries:
        entry = entries[tid]
        ep = float(entry.get('price', 0))
        xp = float(ev.get('price', 0))
        qty = int(ev.get('qty', entry.get('qty', 0)))
        pnl = (xp - ep) * qty
        trades.append({
            'tradeId': tid,
            'symbol': ev.get('symbol', ''),
            'entry': ep,
            'exit': xp,
            'qty': qty,
            'pnl': round(pnl, 2),
            'reason': ev.get('reason', ''),
            'logged_pnl': ev.get('pnl')
        })

total = sum(t['pnl'] for t in trades)
wins = [t for t in trades if t['pnl'] > 0]
losses = [t for t in trades if t['pnl'] <= 0]
win_pnl = sum(t['pnl'] for t in wins)
loss_pnl = sum(t['pnl'] for t in losses)

print(f'Total trades: {len(trades)}')
print(f'Wins: {len(wins)}, Losses: {len(losses)}')
print(f'Computed P&L: {total:.2f}')
print(f'Win P&L: {win_pnl:.2f}, Loss P&L: {loss_pnl:.2f}')
print()

# Estimate charges
total_orders = len(trades) * 2
brokerage = total_orders * 20
stt = sum(t['exit'] * t['qty'] * 0.000625 for t in trades)
exchange_charges = sum((t['entry'] + t['exit']) * t['qty'] * 0.00053 for t in trades)
gst = (brokerage + exchange_charges) * 0.18
stamp = sum(t['entry'] * t['qty'] * 0.00003 for t in trades)
total_charges = brokerage + stt + exchange_charges + gst + stamp

print('--- Estimated Charges ---')
print(f'Brokerage ({total_orders} orders x Rs20): {brokerage:.2f}')
print(f'STT (0.0625% sell): {stt:.2f}')
print(f'Exchange charges (~0.053%): {exchange_charges:.2f}')
print(f'GST (18% on brokerage+exchange): {gst:.2f}')
print(f'Stamp duty (0.003% buy): {stamp:.2f}')
print(f'Total estimated charges: {total_charges:.2f}')
print(f'Net P&L after charges: {total - total_charges:.2f}')
print()

print('--- Per Trade ---')
for i, t in enumerate(trades):
    logged = t['logged_pnl']
    if logged is not None:
        match = 'Y' if abs(float(logged) - t['pnl']) < 1 else 'N'
    else:
        match = '?'
    print(f"{i+1:2d}. {t['symbol']:30s} E:{t['entry']:8.2f} X:{t['exit']:8.2f} Q:{t['qty']:4d} PnL:{t['pnl']:8.2f} Log:{str(logged):>10s} {match} [{t['reason']}]")

# Check for unmatched entries
unmatched = [tid for tid in entries if tid not in [t['tradeId'] for t in trades]]
if unmatched:
    print(f'\nUnmatched ENTRY events (no EXIT): {len(unmatched)}')
    for tid in unmatched:
        e = entries[tid]
        print(f"  {e.get('symbol','')} price={e.get('price','')} qty={e.get('qty','')}")
