const fs = require('fs');
const path = require('path');

const jsonPath = path.join(__dirname, '..', 'auto_trade_log_2026-02-06 (2).json');
const data = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));

// Group by tradeId
const tradesById = {};
for (const rec of data) {
  if (!tradesById[rec.tradeId]) tradesById[rec.tradeId] = [];
  tradesById[rec.tradeId].push(rec);
}

// Build trade objects
const trades = [];
for (const [tid, records] of Object.entries(tradesById)) {
  const entries = records.filter(r => r.type === 'ENTRY');
  const exits = records.filter(r => r.type === 'EXIT');
  if (!exits.length) continue;

  const exitRec = exits[0];
  const mode = exitRec.mode;

  const totalQtyEntry = entries.reduce((s, e) => s + e.qty, 0);
  const wavgEntry = totalQtyEntry ? entries.reduce((s, e) => s + e.price * e.qty, 0) / totalQtyEntry : 0;

  const exitPrice = exitRec.price;
  const exitQty = exitRec.qty;

  const computedPnl = (exitPrice - wavgEntry) * exitQty;
  const loggedPnl = exitRec.pnl || 0;

  const hasAverage = entries.some(e => e.reason === 'Average');

  const entryTs = new Date(Math.min(...entries.map(e => new Date(e.ts).getTime())));
  const exitTs = new Date(exitRec.ts);
  const holdMs = exitRec.holdMs || 0;

  trades.push({
    tradeId: tid,
    mode,
    side: exitRec.side,
    symbol: exitRec.symbol,
    entries,
    exit: exitRec,
    avgEntryPrice: wavgEntry,
    exitPrice,
    exitQty,
    entryQty: totalQtyEntry,
    computedPnl,
    loggedPnl,
    pnlDiscrepancy: Math.abs(computedPnl - loggedPnl),
    exitReason: exitRec.reason,
    holdMs,
    hasAverage,
    entryTime: entryTs,
    exitTime: exitTs,
  });
}

function fmt(n) {
  return n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function pad(s, n) { return (s + '').padEnd(n); }
function padStart(s, n) { return (s + '').padStart(n); }

const EQ = '='.repeat(80);
const DASH = '-';

console.log(EQ);
console.log("TRADE LOG ANALYSIS: auto_trade_log_2026-02-06 (2).json");
console.log(EQ);

// 1
console.log(`\n${EQ}`);
console.log("1. TOTAL TRADES (entry/exit pairs grouped by tradeId)");
console.log(EQ);
console.log(`   Total trades: ${trades.length}`);
const totalRecords = data.length;
const totalEntries = data.filter(r => r.type === 'ENTRY').length;
const totalExits = data.filter(r => r.type === 'EXIT').length;
console.log(`   Total log records: ${totalRecords} (Entries: ${totalEntries}, Exits: ${totalExits})`);

// 2
console.log(`\n${EQ}`);
console.log("2. PAPER vs LIVE TRADE SPLIT");
console.log(EQ);
const paper = trades.filter(t => t.mode === 'PAPER');
const live = trades.filter(t => t.mode === 'LIVE');
console.log(`   PAPER trades: ${paper.length}`);
console.log(`   LIVE trades:  ${live.length}`);

// 3
console.log(`\n${EQ}`);
console.log("3. TOTAL P&L (using logged pnl field)");
console.log(EQ);
const paperPnl = paper.reduce((s, t) => s + t.loggedPnl, 0);
const livePnl = live.reduce((s, t) => s + t.loggedPnl, 0);
console.log(`   PAPER Total P&L: \u20B9${fmt(paperPnl)}`);
console.log(`   LIVE Total P&L:  \u20B9${fmt(livePnl)}`);
console.log(`   COMBINED P&L:    \u20B9${fmt(paperPnl + livePnl)}`);

// 4
console.log(`\n${EQ}`);
console.log("4. WIN/LOSS RATIO");
console.log(EQ);
for (const [label, group] of [["PAPER", paper], ["LIVE", live]]) {
  const wins = group.filter(t => t.loggedPnl > 0);
  const losses = group.filter(t => t.loggedPnl <= 0);
  const ratio = losses.length ? (wins.length / losses.length) : Infinity;
  const pct = group.length ? (wins.length / group.length * 100) : 0;
  console.log(`   ${label}: ${wins.length}W / ${losses.length}L  (Win Rate: ${pct.toFixed(1)}%, W/L Ratio: ${ratio.toFixed(2)})`);
}

// 5
console.log(`\n${EQ}`);
console.log("5. AVERAGE WIN vs AVERAGE LOSS");
console.log(EQ);
for (const [label, group] of [["PAPER", paper], ["LIVE", live]]) {
  const wins = group.filter(t => t.loggedPnl > 0).map(t => t.loggedPnl);
  const losses = group.filter(t => t.loggedPnl < 0).map(t => t.loggedPnl);
  const avgWin = wins.length ? wins.reduce((a,b) => a+b, 0) / wins.length : 0;
  const avgLoss = losses.length ? losses.reduce((a,b) => a+b, 0) / losses.length : 0;
  console.log(`   ${label}:`);
  console.log(`      Avg Win:  \u20B9${fmt(avgWin)} (${wins.length} trades)`);
  console.log(`      Avg Loss: \u20B9${fmt(avgLoss)} (${losses.length} trades)`);
  if (avgLoss !== 0) {
    console.log(`      Reward/Risk Ratio: ${Math.abs(avgWin / avgLoss).toFixed(2)}`);
  }
}

// 6
console.log(`\n${EQ}`);
console.log("6. BREAKDOWN BY EXIT REASON");
console.log(EQ);
const byReason = {};
for (const t of trades) {
  if (!byReason[t.exitReason]) byReason[t.exitReason] = [];
  byReason[t.exitReason].push(t);
}

for (const reason of Object.keys(byReason).sort()) {
  const group = byReason[reason];
  const total = group.reduce((s, t) => s + t.loggedPnl, 0);
  const wins = group.filter(t => t.loggedPnl > 0).length;
  const losses = group.filter(t => t.loggedPnl <= 0).length;
  const avg = group.length ? total / group.length : 0;
  console.log(`   ${pad(reason, 20)}: ${padStart(group.length, 3)} trades | P&L: \u20B9${padStart(fmt(total), 10)} | Avg: \u20B9${padStart(fmt(avg), 8)} | W:${wins} L:${losses}`);
}

console.log(`\n   By Mode + Reason:`);
for (const [modeLabel, modeGroup] of [["PAPER", paper], ["LIVE", live]]) {
  console.log(`   --- ${modeLabel} ---`);
  const modeByReason = {};
  for (const t of modeGroup) {
    if (!modeByReason[t.exitReason]) modeByReason[t.exitReason] = [];
    modeByReason[t.exitReason].push(t);
  }
  for (const reason of Object.keys(modeByReason).sort()) {
    const group = modeByReason[reason];
    const total = group.reduce((s, t) => s + t.loggedPnl, 0);
    const wins = group.filter(t => t.loggedPnl > 0).length;
    const losses = group.filter(t => t.loggedPnl <= 0).length;
    console.log(`      ${pad(reason, 20)}: ${padStart(group.length, 3)} trades | P&L: \u20B9${padStart(fmt(total), 10)} | W:${wins} L:${losses}`);
  }
}

// 7
console.log(`\n${EQ}`);
console.log("7. AVERAGE HOLD TIME");
console.log(EQ);
const allHolds = trades.filter(t => t.holdMs > 0).map(t => t.holdMs);
const avgHold = allHolds.length ? allHolds.reduce((a,b) => a+b, 0) / allHolds.length : 0;
console.log(`   Overall avg hold time: ${(avgHold/1000).toFixed(1)}s (${avgHold.toFixed(0)}ms)`);
for (const [label, group] of [["PAPER", paper], ["LIVE", live]]) {
  const holds = group.filter(t => t.holdMs > 0).map(t => t.holdMs);
  const avg = holds.length ? holds.reduce((a,b) => a+b, 0) / holds.length : 0;
  console.log(`   ${label} avg hold time: ${(avg/1000).toFixed(1)}s`);
}

// 8
console.log(`\n${EQ}`);
console.log("8. HOLD TIME DISTRIBUTION");
console.log(EQ);
const buckets = {'< 5s': 0, '5-15s': 0, '15-30s': 0, '30-60s': 0, '> 60s': 0};
for (const t of trades) {
  const s = t.holdMs / 1000;
  if (s < 5) buckets['< 5s']++;
  else if (s < 15) buckets['5-15s']++;
  else if (s < 30) buckets['15-30s']++;
  else if (s < 60) buckets['30-60s']++;
  else buckets['> 60s']++;
}
for (const [k, v] of Object.entries(buckets)) {
  const pct = v / trades.length * 100;
  const bar = '\u2588'.repeat(Math.floor(pct / 2));
  console.log(`   ${pad(k, 10)}: ${padStart(v, 3)} (${padStart(pct.toFixed(1), 5)}%) ${bar}`);
}

// 9
console.log(`\n${EQ}`);
console.log("9. TOP 5 BIGGEST WINNERS");
console.log(EQ);
const sortedByPnl = [...trades].sort((a, b) => b.loggedPnl - a.loggedPnl);
for (let i = 0; i < Math.min(5, sortedByPnl.length); i++) {
  const t = sortedByPnl[i];
  console.log(`   #${i+1}: \u20B9${padStart(fmt(t.loggedPnl), 10)} | ${pad(t.mode, 5)} | ${pad(t.symbol, 30)} | ${pad(t.exitReason, 20)} | Hold: ${(t.holdMs/1000).toFixed(1)}s`);
}

console.log(`\n   TOP 5 BIGGEST LOSERS`);
for (let i = 0; i < Math.min(5, sortedByPnl.length); i++) {
  const t = sortedByPnl[sortedByPnl.length - 1 - i];
  // Python code uses sorted_by_pnl[-5:] which gives the last 5 items in ascending order
  // Let me match Python exactly
}
// Match Python: sorted_by_pnl[-5:] gives last 5 items of descending sort = 5 worst, in ascending order
const bottom5 = sortedByPnl.slice(-5);
for (let i = 0; i < bottom5.length; i++) {
  const t = bottom5[i];
  console.log(`   #${i+1}: \u20B9${padStart(fmt(t.loggedPnl), 10)} | ${pad(t.mode, 5)} | ${pad(t.symbol, 30)} | ${pad(t.exitReason, 20)} | Hold: ${(t.holdMs/1000).toFixed(1)}s`);
}

// 10
console.log(`\n${EQ}`);
console.log("10. P&L DISCREPANCY CHECK (computed vs logged)");
console.log(EQ);
const THRESHOLD = 0.01;
let discrepancies = trades.filter(t => t.pnlDiscrepancy > THRESHOLD).map(t => [t, t.pnlDiscrepancy]);
console.log(`   Trades with discrepancy > \u20B90.01: ${discrepancies.length} out of ${trades.length}`);
if (discrepancies.length) {
  discrepancies.sort((a, b) => b[1] - a[1]);
  console.log(`\n   ${pad('TradeId', 40)} | ${padStart('Computed', 12)} | ${padStart('Logged', 12)} | ${padStart('Diff', 12)} | ${pad('Mode', 5)} | Reason`);
  console.log(`   ${'-'.repeat(40)} | ${'-'.repeat(12)} | ${'-'.repeat(12)} | ${'-'.repeat(12)} | ${'-'.repeat(5)} | ${'-'.repeat(15)}`);
  for (let i = 0; i < Math.min(20, discrepancies.length); i++) {
    const [t, disc] = discrepancies[i];
    console.log(`   ${pad(t.tradeId, 40)} | \u20B9${padStart(fmt(t.computedPnl), 10)} | \u20B9${padStart(fmt(t.loggedPnl), 10)} | \u20B9${padStart(fmt(disc), 10)} | ${pad(t.mode, 5)} | ${t.exitReason}`);
  }
  const totalDisc = discrepancies.reduce((s, d) => s + d[1], 0);
  const avgDisc = discrepancies.length ? totalDisc / discrepancies.length : 0;
  const maxDisc = Math.max(...discrepancies.map(d => d[1]));
  console.log(`\n   Summary: avg discrepancy \u20B9${fmt(avgDisc)}, max \u20B9${fmt(maxDisc)}`);
  console.log(`   NOTE: The logged pnl appears to include running/cumulative adjustments`);
  console.log(`         beyond simple (exitPrice - entryPrice) * qty`);
} else {
  console.log("   No discrepancies found!");
}

// 11
console.log(`\n${EQ}`);
console.log("11. TRAIL SL LOSS ANALYSIS");
console.log(EQ);
const trailSlTrades = trades.filter(t => t.exitReason === 'Trail SL');
const trailSlLosses = trailSlTrades.filter(t => t.loggedPnl < 0);
const trailSlWins = trailSlTrades.filter(t => t.loggedPnl > 0);
const trailLossPct = trades.length ? trailSlLosses.length / trades.length * 100 : 0;
const trailLossOfTrail = trailSlTrades.length ? trailSlLosses.length / trailSlTrades.length * 100 : 0;

console.log(`   Total Trail SL exits: ${trailSlTrades.length}`);
const trailWinAvg = trailSlWins.length ? trailSlWins.reduce((s,t) => s + t.loggedPnl, 0) / trailSlWins.length : 0;
const trailLossAvg = trailSlLosses.length ? trailSlLosses.reduce((s,t) => s + t.loggedPnl, 0) / trailSlLosses.length : 0;
console.log(`   Trail SL wins:   ${trailSlWins.length} (avg \u20B9${fmt(trailWinAvg)})`);
console.log(`   Trail SL losses: ${trailSlLosses.length} (avg \u20B9${fmt(trailLossAvg)})`);
console.log(`   % of ALL trades exited at loss via Trail SL: ${trailLossPct.toFixed(1)}%`);
console.log(`   % of Trail SL exits that were losses: ${trailLossOfTrail.toFixed(1)}%`);

if (trailSlLosses.length) {
  console.log(`\n   Trail SL loss details:`);
  const sortedLosses = [...trailSlLosses].sort((a, b) => a.loggedPnl - b.loggedPnl);
  for (const t of sortedLosses) {
    console.log(`      \u20B9${padStart(fmt(t.loggedPnl), 10)} | ${pad(t.mode, 5)} | ${pad(t.symbol, 30)} | Hold: ${(t.holdMs/1000).toFixed(1)}s | AvgEntry: ${t.avgEntryPrice.toFixed(2)} -> Exit: ${t.exitPrice.toFixed(2)}`);
  }
  console.log(`\n   INSIGHT: ${trailLossOfTrail.toFixed(0)}% of Trail SL exits resulted in losses.`);
  if (trailLossOfTrail > 30) {
    console.log(`   \u26A0\uFE0F  This suggests the trailing stop may be TOO TIGHT, locking in losses before`);
    console.log(`      the position has enough room to move into profit.`);
  } else {
    console.log(`   Trail SL seems reasonably calibrated.`);
  }
}

console.log(`\n   By Mode:`);
for (const [label, group] of [["PAPER", paper], ["LIVE", live]]) {
  const tsTrades = group.filter(t => t.exitReason === 'Trail SL');
  const tsLosses = tsTrades.filter(t => t.loggedPnl < 0);
  const pct = tsTrades.length ? tsLosses.length / tsTrades.length * 100 : 0;
  console.log(`   ${label}: ${tsLosses.length} losses / ${tsTrades.length} Trail SL trades (${pct.toFixed(1)}% loss rate)`);
}

// 12
console.log(`\n${EQ}`);
console.log("12. AVERAGING (SCALE-IN) ANALYSIS");
console.log(EQ);
const avgTrades = trades.filter(t => t.hasAverage);
const nonAvgTrades = trades.filter(t => !t.hasAverage);
console.log(`   Trades with Averaging: ${avgTrades.length}`);
console.log(`   Trades without Averaging: ${nonAvgTrades.length}`);

if (avgTrades.length) {
  const avgPnl = avgTrades.reduce((s, t) => s + t.loggedPnl, 0);
  const avgWins = avgTrades.filter(t => t.loggedPnl > 0).length;
  const avgLosses = avgTrades.filter(t => t.loggedPnl <= 0).length;
  const avgAvg = avgPnl / avgTrades.length;
  console.log(`\n   Averaged trades total P&L: \u20B9${fmt(avgPnl)}`);
  console.log(`   Averaged trades avg P&L:   \u20B9${fmt(avgAvg)}`);
  console.log(`   Win/Loss: ${avgWins}W / ${avgLosses}L (${(avgWins/avgTrades.length*100).toFixed(1)}% win rate)`);

  console.log(`\n   Detail of each averaged trade:`);
  for (const t of avgTrades) {
    const entryDetails = t.entries.map(e => `${e.qty}@${e.price}`).join(' + ');
    const result = t.loggedPnl > 0 ? 'WIN' : 'LOSS';
    console.log(`      ${pad(t.mode, 5)} | ${pad(result, 4)} | \u20B9${padStart(fmt(t.loggedPnl), 10)} | Entries: ${entryDetails} -> Exit: ${t.exitQty}@${t.exitPrice} | ${t.exitReason}`);
  }

  if (nonAvgTrades.length) {
    const nonAvgPnl = nonAvgTrades.reduce((s, t) => s + t.loggedPnl, 0) / nonAvgTrades.length;
    console.log(`\n   Comparison:`);
    console.log(`      Avg P&L (with averaging):    \u20B9${fmt(avgAvg)}`);
    console.log(`      Avg P&L (without averaging):  \u20B9${fmt(nonAvgPnl)}`);
  }
}

// Summary
console.log(`\n${EQ}`);
console.log("EXECUTIVE SUMMARY");
console.log(EQ);
const totalPnl = trades.reduce((s, t) => s + t.loggedPnl, 0);
const winsAll = trades.filter(t => t.loggedPnl > 0).length;
const lossesAll = trades.filter(t => t.loggedPnl <= 0).length;
console.log(`   Total Trades: ${trades.length} (${paper.length} Paper, ${live.length} Live)`);
console.log(`   Combined P&L: \u20B9${fmt(totalPnl)}`);
console.log(`   Paper P&L:    \u20B9${fmt(paperPnl)}  |  Live P&L: \u20B9${fmt(livePnl)}`);
console.log(`   Overall Win Rate: ${winsAll}/${trades.length} = ${(winsAll/trades.length*100).toFixed(1)}%`);

function fmtTime(d) {
  return d.toISOString().slice(11, 19);
}
console.log(`   Session Time: ${fmtTime(trades[0].entryTime)} - ${fmtTime(trades[trades.length-1].exitTime)} UTC`);
console.log(`   Avg Hold: ${(avgHold/1000).toFixed(1)}s`);
console.log(`   Trail SL Loss Rate: ${trailLossOfTrail.toFixed(0)}% of Trail SL exits are losses`);
if (discrepancies.length) {
  console.log(`   P&L Discrepancies: ${discrepancies.length} trades have computed \u2260 logged PnL`);
}
