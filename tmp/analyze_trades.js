const fs = require('fs');

const raw = fs.readFileSync(String.raw`C:\algo\openalgov2\openalgo\auto_trade_log_2026-02-06 (5).json`, 'utf-8');
const events = JSON.parse(raw);

// Group by tradeId
const trades = {};
const nullTradeIdEvents = [];

for (const e of events) {
  if (!e.tradeId) {
    nullTradeIdEvents.push(e);
    continue;
  }
  if (!trades[e.tradeId]) trades[e.tradeId] = { entries: [], exits: [] };
  if (e.type === 'ENTRY') trades[e.tradeId].entries.push(e);
  else if (e.type === 'EXIT') trades[e.tradeId].exits.push(e);
}

// Analyze each trade
const results = [];
let totalLoggedPnl = 0;
let totalCalcPnl = 0;
let wrongSignCount = 0;
let unmatchedEntries = [];
let orphanExits = [];
let qtyMismatchTrades = [];
const symbolStats = {};

const tradeIds = Object.keys(trades);
// Sort by first event timestamp
tradeIds.sort((a, b) => {
  const tsA = trades[a].entries[0]?.ts || trades[a].exits[0]?.ts || '';
  const tsB = trades[b].entries[0]?.ts || trades[b].exits[0]?.ts || '';
  return tsA.localeCompare(tsB);
});

for (const tid of tradeIds) {
  const t = trades[tid];
  
  if (t.exits.length === 0) {
    unmatchedEntries.push({ tradeId: tid, entries: t.entries });
    continue;
  }
  
  if (t.entries.length === 0) {
    orphanExits.push({ tradeId: tid, exits: t.exits });
    continue;
  }
  
  const exit = t.exits[0]; // assume single exit
  const symbol = exit.symbol;
  const action = exit.action; // SELL for long exit, BUY for short exit
  const side = exit.side; // CE or PE
  
  // Calculate weighted average entry price
  let totalCost = 0;
  let totalQty = 0;
  for (const entry of t.entries) {
    totalCost += entry.price * entry.qty;
    totalQty += entry.qty;
  }
  const avgEntryPrice = totalCost / totalQty;
  
  const exitPrice = exit.price;
  const exitQty = exit.qty;
  const loggedPnl = exit.pnl || 0;
  
  // Determine direction from entry action
  const entryAction = t.entries[0].action;
  let calcPnl;
  if (entryAction === 'BUY') {
    // Long trade: profit = (exit - entry) * qty
    calcPnl = (exitPrice - avgEntryPrice) * exitQty;
  } else {
    // Short trade: profit = (entry - exit) * qty
    calcPnl = (avgEntryPrice - exitPrice) * exitQty;
  }
  
  const pnlDiff = loggedPnl - calcPnl;
  const wrongSign = (loggedPnl > 0 && calcPnl < 0) || (loggedPnl < 0 && calcPnl > 0);
  if (wrongSign) wrongSignCount++;
  
  const qtyMismatch = totalQty !== exitQty;
  if (qtyMismatch) qtyMismatchTrades.push(tid);
  
  // Symbol stats
  if (!symbolStats[symbol]) symbolStats[symbol] = { count: 0, loggedPnl: 0, calcPnl: 0, trades: [] };
  symbolStats[symbol].count++;
  symbolStats[symbol].loggedPnl += loggedPnl;
  symbolStats[symbol].calcPnl += calcPnl;
  
  totalLoggedPnl += loggedPnl;
  totalCalcPnl += calcPnl;
  
  results.push({
    idx: results.length + 1,
    tradeId: tid.slice(0, 8),
    symbol: symbol,
    side: side,
    entryAction: entryAction,
    numEntries: t.entries.length,
    avgEntry: avgEntryPrice,
    exitPrice: exitPrice,
    entryQty: totalQty,
    exitQty: exitQty,
    loggedPnl: loggedPnl,
    calcPnl: calcPnl,
    diff: pnlDiff,
    wrongSign: wrongSign,
    qtyMismatch: qtyMismatch,
    reason: exit.reason,
    entryTime: t.entries[0].ts,
    exitTime: exit.ts,
  });
}

// Print detailed trade table
console.log('=' .repeat(200));
console.log('DETAILED TRADE ANALYSIS - auto_trade_log_2026-02-06 (5).json');
console.log('=' .repeat(200));
console.log(`Total events: ${events.length}  |  Unique tradeIds: ${tradeIds.length}  |  Completed trades: ${results.length}`);
console.log('=' .repeat(200));

const hdr = [
  '#'.padStart(3),
  'TradeId'.padEnd(10),
  'Symbol'.padEnd(30),
  'Side'.padEnd(4),
  'Dir'.padEnd(5),
  '#E'.padStart(2),
  'AvgEntry'.padStart(10),
  'ExitPx'.padStart(10),
  'EQty'.padStart(5),
  'XQty'.padStart(5),
  'LoggedPnl'.padStart(12),
  'CalcPnl'.padStart(12),
  'Diff'.padStart(12),
  'WrongSign'.padStart(10),
  'QtyMis'.padStart(6),
  'Reason'.padEnd(15),
];
console.log(hdr.join(' | '));
console.log('-'.repeat(200));

for (const r of results) {
  const row = [
    String(r.idx).padStart(3),
    r.tradeId.padEnd(10),
    r.symbol.padEnd(30),
    r.side.padEnd(4),
    r.entryAction.padEnd(5),
    String(r.numEntries).padStart(2),
    r.avgEntry.toFixed(2).padStart(10),
    r.exitPrice.toFixed(2).padStart(10),
    String(r.entryQty).padStart(5),
    String(r.exitQty).padStart(5),
    r.loggedPnl.toFixed(2).padStart(12),
    r.calcPnl.toFixed(2).padStart(12),
    r.diff.toFixed(2).padStart(12),
    (r.wrongSign ? '*** YES ***' : '').padStart(10),
    (r.qtyMismatch ? 'YES' : '').padStart(6),
    r.reason.padEnd(15),
  ];
  console.log(row.join(' | '));
}

console.log('');
console.log('=' .repeat(100));
console.log('SUMMARY');
console.log('=' .repeat(100));
console.log(`Total Completed Trades:    ${results.length}`);
console.log(`Total Logged P&L:          ₹${totalLoggedPnl.toFixed(2)}`);
console.log(`Total Calculated P&L:      ₹${totalCalcPnl.toFixed(2)}`);
console.log(`Difference (Logged-Calc):  ₹${(totalLoggedPnl - totalCalcPnl).toFixed(2)}`);
console.log(`Broker MTM:                ₹-1427`);
console.log(`SessionPnl (auto-trader):  ₹+1268`);
console.log('');

console.log(`Wrong-sign trades:         ${wrongSignCount}`);
if (wrongSignCount > 0) {
  console.log('  Trades where logged P&L sign differs from calculated:');
  for (const r of results.filter(r => r.wrongSign)) {
    console.log(`    #${r.idx} ${r.tradeId} ${r.symbol} logged=${r.loggedPnl.toFixed(2)} calc=${r.calcPnl.toFixed(2)}`);
  }
}

console.log('');
console.log(`Orphan exits (null tradeId): ${nullTradeIdEvents.length}`);
for (const e of nullTradeIdEvents) {
  console.log(`  ${JSON.stringify(e)}`);
}

console.log('');
console.log(`Unmatched entries (no exit): ${unmatchedEntries.length}`);
for (const u of unmatchedEntries) {
  for (const e of u.entries) {
    console.log(`  TradeId: ${u.tradeId.slice(0,8)} Symbol: ${e.symbol} Action: ${e.action} Qty: ${e.qty} Price: ${e.price}`);
  }
}

console.log('');
console.log(`Qty mismatch trades (entry qty != exit qty): ${qtyMismatchTrades.length}`);
for (const tid of qtyMismatchTrades) {
  const t = trades[tid];
  const totalEntryQty = t.entries.reduce((s, e) => s + e.qty, 0);
  const exitQty = t.exits[0].qty;
  console.log(`  TradeId: ${tid.slice(0,8)} Symbol: ${t.entries[0].symbol} EntryQty: ${totalEntryQty} ExitQty: ${exitQty}`);
}

console.log('');
console.log('=' .repeat(100));
console.log('P&L BREAKDOWN BY SYMBOL');
console.log('=' .repeat(100));
const symHdr = [
  'Symbol'.padEnd(35),
  'Count'.padStart(6),
  'LoggedPnl'.padStart(14),
  'CalcPnl'.padStart(14),
  'Diff'.padStart(14),
];
console.log(symHdr.join(' | '));
console.log('-'.repeat(100));

const symbols = Object.keys(symbolStats).sort();
for (const sym of symbols) {
  const s = symbolStats[sym];
  const row = [
    sym.padEnd(35),
    String(s.count).padStart(6),
    s.loggedPnl.toFixed(2).padStart(14),
    s.calcPnl.toFixed(2).padStart(14),
    (s.loggedPnl - s.calcPnl).toFixed(2).padStart(14),
  ];
  console.log(row.join(' | '));
}

// DEEPER ANALYSIS: Why broker shows -1427 but session shows +1268
console.log('');
console.log('=' .repeat(100));
console.log('ROOT CAUSE ANALYSIS: Broker -₹1427 vs Session +₹1268');
console.log('=' .repeat(100));

// Check for BUY (long) vs SELL (short) trades  
let longTrades = results.filter(r => r.entryAction === 'BUY');
let shortTrades = results.filter(r => r.entryAction === 'SELL');
console.log(`Long trades (BUY entry):   ${longTrades.length}, Logged P&L: ₹${longTrades.reduce((s,r) => s+r.loggedPnl, 0).toFixed(2)}, Calc P&L: ₹${longTrades.reduce((s,r) => s+r.calcPnl, 0).toFixed(2)}`);
console.log(`Short trades (SELL entry):  ${shortTrades.length}, Logged P&L: ₹${shortTrades.reduce((s,r) => s+r.loggedPnl, 0).toFixed(2)}, Calc P&L: ₹${shortTrades.reduce((s,r) => s+r.calcPnl, 0).toFixed(2)}`);

// Check for large discrepancies
console.log('');
console.log('Trades with large |diff| > 50:');
for (const r of results.filter(r => Math.abs(r.diff) > 50)) {
  console.log(`  #${r.idx} ${r.tradeId} ${r.symbol} logged=${r.loggedPnl.toFixed(2)} calc=${r.calcPnl.toFixed(2)} diff=${r.diff.toFixed(2)} entries=${r.numEntries} eQty=${r.entryQty} xQty=${r.exitQty}`);
}

// Check if the pnl in logged is per-unit or total
console.log('');
console.log('Checking if logged pnl might be per-unit (not total):');
let totalPerUnitPnl = 0;
for (const r of results) {
  const perUnit = r.loggedPnl / r.exitQty;
  const totalFromPerUnit = perUnit; // it's already per unit if this theory is right
  totalPerUnitPnl += r.loggedPnl * r.exitQty; // if logged is per-unit, multiply by qty
}
console.log(`  If logged pnl is per-unit, total would be: ₹${totalPerUnitPnl.toFixed(2)}`);

// Check average entry calculation - maybe the log calculates differently for multi-entry
console.log('');
console.log('Multi-entry trade details:');
for (const r of results.filter(r => r.numEntries > 1)) {
  const t = trades[Object.keys(trades).find(k => k.startsWith(r.tradeId))];
  // Actually tradeId was sliced, need full id
  console.log(`  #${r.idx} ${r.tradeId} ${r.symbol} numEntries=${r.numEntries}`);
}

// Look for multi-entry trades from full trade IDs
for (const tid of tradeIds) {
  const t = trades[tid];
  if (t.entries.length > 1 && t.exits.length > 0) {
    console.log(`\n  TradeId: ${tid.slice(0,8)}, Symbol: ${t.entries[0].symbol}`);
    for (const e of t.entries) {
      console.log(`    ENTRY: price=${e.price} qty=${e.qty} action=${e.action}`);
    }
    const exit = t.exits[0];
    const totalEntryQty = t.entries.reduce((s, e) => s + e.qty, 0);
    const avgPrice = t.entries.reduce((s, e) => s + e.price * e.qty, 0) / totalEntryQty;
    console.log(`    EXIT:  price=${exit.price} qty=${exit.qty} action=${exit.action} loggedPnl=${exit.pnl}`);
    console.log(`    AvgEntry=${avgPrice.toFixed(2)} CalcPnl=${((exit.price - avgPrice) * exit.qty).toFixed(2)} (if long)`);
  }
}

// Hypothesis: logged pnl uses a running/cumulative approach vs actual fill prices
// Let's check trade #1 manually
console.log('');
console.log('MANUAL VERIFICATION - First 5 trades:');
for (let i = 0; i < Math.min(5, results.length); i++) {
  const r = results[i];
  const tid = tradeIds.find(k => k.startsWith(r.tradeId));
  const t = trades[tid];
  const entries = t.entries;
  const exit = t.exits[0];
  
  console.log(`\nTrade #${r.idx}: ${r.symbol} ${r.entryAction}`);
  for (const e of entries) {
    console.log(`  Entry: price=${e.price} qty=${e.qty}`);
  }
  console.log(`  Exit:  price=${exit.price} qty=${exit.qty}`);
  console.log(`  Logged PnL: ${r.loggedPnl.toFixed(2)}`);
  console.log(`  Calc PnL (standard): ${r.calcPnl.toFixed(2)}`);
  
  // Maybe logged pnl divides by lot size? NIFTY lot = 75, BANKNIFTY lot = 30
  // qty=130... hmm not standard lot sizes
  if (r.loggedPnl !== 0) {
    const impliedMultiplier = r.loggedPnl / (exit.price - r.avgEntry);
    console.log(`  Price diff: ${(exit.price - r.avgEntry).toFixed(4)}`);
    console.log(`  Implied multiplier (loggedPnl / priceDiff): ${impliedMultiplier.toFixed(4)}`);
    console.log(`  Ratio loggedPnl/calcPnl: ${(r.loggedPnl / r.calcPnl).toFixed(6)}`);
  }
}

// The gap is broker -1427 vs session +1268, that's a ₹2695 difference
// Let's see if there are any SELL-entry (short) trades where the pnl calc might be inverted
console.log('');
console.log('CRITICAL CHECK: Trades where entry action is SELL (shorting):');
for (const r of results.filter(r => r.entryAction === 'SELL')) {
  console.log(`  #${r.idx} ${r.tradeId} ${r.symbol} side=${r.side} avgEntry=${r.avgEntry.toFixed(2)} exitPx=${r.exitPrice.toFixed(2)} loggedPnl=${r.loggedPnl.toFixed(2)} calcPnl=${r.calcPnl.toFixed(2)}`);
}

// Check: does the auto-trader only sum positive pnl? Or skip losses?
console.log('');
let positivePnlSum = results.filter(r => r.loggedPnl > 0).reduce((s,r) => s + r.loggedPnl, 0);
let negativePnlSum = results.filter(r => r.loggedPnl < 0).reduce((s,r) => s + r.loggedPnl, 0);
console.log(`Sum of positive logged P&Ls: ₹${positivePnlSum.toFixed(2)}`);
console.log(`Sum of negative logged P&Ls: ₹${negativePnlSum.toFixed(2)}`);
console.log(`Net logged P&L:              ₹${(positivePnlSum + negativePnlSum).toFixed(2)}`);

// Check: maybe some events are mode=PAPER that shouldn't count
const modes = {};
for (const e of events) {
  modes[e.mode] = (modes[e.mode] || 0) + 1;
}
console.log('');
console.log('Event modes:', JSON.stringify(modes));

// Check for duplicate exits
console.log('');
console.log('TradeIds with multiple exits:');
for (const tid of tradeIds) {
  if (trades[tid].exits.length > 1) {
    console.log(`  ${tid.slice(0,8)}: ${trades[tid].exits.length} exits`);
    for (const e of trades[tid].exits) {
      console.log(`    EXIT: price=${e.price} qty=${e.qty} pnl=${e.pnl}`);
    }
  }
}
