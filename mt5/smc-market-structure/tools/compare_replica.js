// Compares MQL5 indicator output (written by SMC_Verify.mq5) with the JavaScript reference replica.
// Usage: node compare_replica.js <mt5_signals.csv>
// The replica's pivot tie rules are the ones confirmed against TradingView's Pine output.
const fs = require("fs");
const { smcReplica } = require("./smc_replica.js");

const NAMES = ["swBull", "swBear", "intBull", "intBear", "eqh", "eql", "pivH", "pivL"];
const rows = fs.readFileSync(process.argv[2], "utf8").trim().split(/\r?\n/).slice(1).map((l) => l.split(",").map(Number));
const H = rows.map((r) => r[2]), L = rows.map((r) => r[3]), C = rows.map((r) => r[4]);
const rep = smcReplica(H, L, C, { swingLen: 25, intLen: 5, eqLen: 3, eqThresh: 0.1, breakClose: true, leftTie: false, rightTie: true });

let events = 0, mism = 0;
const per = {}, first = [];
for (let i = 0; i < rows.length - 1; i++) { // the last bar is still forming in MT5
  for (let k = 0; k < 8; k++) {
    const a = rows[i][5 + k], b = rep[i][k];
    if (a) events++;
    if (a !== b) {
      mism++;
      per[NAMES[k]] = (per[NAMES[k]] || 0) + 1;
      if (first.length < 10) first.push({ i, time: new Date(rows[i][0] * 1000).toISOString(), col: NAMES[k], mql5: a, replica: b });
    }
  }
}
const counts = {};
NAMES.forEach((n, k) => { counts[n] = rows.slice(0, -1).filter((r) => r[5 + k]).length; });
console.log(JSON.stringify({ bars: rows.length - 1, from: new Date(rows[0][0] * 1000).toISOString(), to: new Date(rows[rows.length - 2][0] * 1000).toISOString(), mql5Events: events, eventsByType: counts, mismatches: mism, perColumn: per, firstMismatches: first }, null, 1));
process.exit(mism ? 1 : 0);
