// Reference implementation of the SMC Market Structure signal logic in plain JavaScript.
// It mirrors SMC_Market_Structure.mq5 line by line and is used to prove that the MQL5 port
// produces the same signals as the Pine Script original:
//   1) in the browser: replica on TradingView bars == Pine output (TradingView chart model)
//   2) locally:        replica on MT5 bars        == MQL5 indicator buffers
// Codes per bar: [swBull, swBear, intBull, intBear, eqh, eql, pivH, pivL]
//   breaks 1=BOS 2=CHoCH, pivH 1=H 2=HH 3=LH, pivL 1=L 2=LL 3=HL, eqh/eql 1=yes
function smcReplica(H, L, C, opt) {
  const n = H.length;
  const pivot = (src, i, len, isHigh) => {
    const p = i - len;
    if (p - len < 0) return null;
    const v = src[p];
    for (let k = 1; k <= len; k++) {
      const x = src[p - k];
      if (isHigh ? (opt.leftTie ? x >= v : x > v) : (opt.leftTie ? x <= v : x < v)) return null;
    }
    for (let k = 1; k <= len; k++) {
      const x = src[p + k];
      if (isHigh ? (opt.rightTie ? x >= v : x > v) : (opt.rightTie ? x <= v : x < v)) return null;
    }
    return v;
  };
  const mk = () => ({ hi: { ok: false, price: 0, crossed: false }, lo: { ok: false, price: 0, crossed: false }, trend: 0 });
  const sw = mk(), it = mk();
  let eqHOk = false, eqLOk = false, lastEqH = 0, lastEqL = 0;
  let atr = 0, trSum = 0, trCount = 0, atrOk = false;
  const check = (s, internal, skipHiOk, skipHi, skipLoOk, skipLo, i) => {
    let bull = 0, bear = 0;
    const up = opt.breakClose ? C[i] : H[i];
    const dn = opt.breakClose ? C[i] : L[i];
    if (s.hi.ok && !s.hi.crossed && up > s.hi.price) {
      if (!(internal && skipHiOk && s.hi.price === skipHi)) bull = s.trend === -1 ? 2 : 1;
      s.hi.crossed = true;
      s.trend = 1;
    }
    if (s.lo.ok && !s.lo.crossed && dn < s.lo.price) {
      if (!(internal && skipLoOk && s.lo.price === skipLo)) bear = s.trend === 1 ? 2 : 1;
      s.lo.crossed = true;
      s.trend = -1;
    }
    return [bull, bear];
  };
  const out = new Array(n);
  for (let i = 0; i < n; i++) {
    const tr = i === 0 ? H[i] - L[i] : Math.max(H[i] - L[i], Math.max(Math.abs(H[i] - C[i - 1]), Math.abs(L[i] - C[i - 1])));
    if (!atrOk) { trSum += tr; trCount++; if (trCount === 200) { atr = trSum / 200; atrOk = true; } }
    else atr = (1 / 200) * tr + (1 - 1 / 200) * atr;
    const r = [0, 0, 0, 0, 0, 0, 0, 0];
    const sph = pivot(H, i, opt.swingLen, true), spl = pivot(L, i, opt.swingLen, false);
    const iph = pivot(H, i, opt.intLen, true), ipl = pivot(L, i, opt.intLen, false);
    const eph = pivot(H, i, opt.eqLen, true), epl = pivot(L, i, opt.eqLen, false);
    if (sph !== null) { r[6] = !sw.hi.ok ? 1 : (sph > sw.hi.price ? 2 : 3); sw.hi = { ok: true, price: sph, crossed: false }; }
    if (spl !== null) { r[7] = !sw.lo.ok ? 1 : (spl < sw.lo.price ? 2 : 3); sw.lo = { ok: true, price: spl, crossed: false }; }
    if (iph !== null) it.hi = { ok: true, price: iph, crossed: false };
    if (ipl !== null) it.lo = { ok: true, price: ipl, crossed: false };
    const [sb, sbe] = check(sw, false, false, 0, false, 0, i);
    const [ib, ibe] = check(it, true, sw.hi.ok, sw.hi.price, sw.lo.ok, sw.lo.price, i);
    r[0] = sb; r[1] = sbe; r[2] = ib; r[3] = ibe;
    if (eph !== null) { if (eqHOk && atrOk && Math.abs(eph - lastEqH) < opt.eqThresh * atr) r[4] = 1; eqHOk = true; lastEqH = eph; }
    if (epl !== null) { if (eqLOk && atrOk && Math.abs(epl - lastEqL) < opt.eqThresh * atr) r[5] = 1; eqLOk = true; lastEqL = epl; }
    out[i] = r;
  }
  return out;
}

if (typeof module !== "undefined") module.exports = { smcReplica };
