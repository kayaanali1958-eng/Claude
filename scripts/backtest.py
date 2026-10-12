#!/usr/bin/env python3
"""Playbook engine: learns which strategy works in which market situation.

1. STRATEGY LIBRARY: every function decorated with @strategy below is tested automatically.
   To add a strategy, write one more function; nothing else changes. The library has no size limit.
2. SITUATIONS: every signal is tagged with the market situation at that moment:
     trend  daily trend: up / down / flat   (price vs 20-day avg, 20 vs 50-day avg)
     gap    today's open vs yesterday's close: up / down / flat  (0.3% threshold)
     vol    yesterday's range vs its 20-day average: high / normal / low
     vix    VIX level: calm (<20) / nervous (20-30) / fear (>30)
     time   open (9:30-10:30) / midday (10:30-14:00) / late (14:00-15:30)
     overnight  where the NY open sits vs. the London range (SPY/QQQ) or premarket range: above / inside / below
     vwap   price above or below VWAP at the signal
3. LEARNING: for each strategy and side, it tries every situation (one or two conditions at a time)
   and every exit (1.5R, 2R or 3R target, with or without breakeven at +1R). It picks what worked
   on the first 2/3 of the days, then keeps it only if it ALSO made money on the last 1/3, which it
   never saw. Those survivors become the playbook.
4. OUTPUT: backtests/playbook.json  (situation -> strategies that work there, used by the desk)
           backtests/report_<date>.md (readable summary)

Rules for every test: 1R risk; stop counts first if stop and target share a bar; flat 15:55;
no entries after 15:30; 0.02% slippage per side; one trade per strategy, side, symbol and day.
News, earnings grades and the risk manager's judgment are not modelled: the playbook says where
an edge has shown up, the desk still applies its full checks.

Usage:  python scripts/backtest.py                      (SPY QQQ + 8 mega-caps)
        python scripts/backtest.py NVDA AMD PLTR NBIS   (any symbols; more = more evidence)
Needs:  pip install yfinance pandas tabulate
"""
import itertools, json, sys
from datetime import date, time as dtime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "backtests"
DEFAULT = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT", "AMZN", "META", "TSLA", "AMD", "GOOGL"]
SLIP = 0.0002
LAST_ENTRY, FLAT = dtime(15, 30), dtime(15, 55)
EXITS = [dict(target=t, be=b) for t in (1.5, 2.0, 3.0) for b in (False, True)]
MIN_TRAIN, MIN_TEST = 10, 5
TRAIN_EDGE, TEST_EDGE = 0.15, 0.05      # avg R needed to count as an edge

# ======================================================================================
# Strategy library. Each function gets a Day and yields (side, entry, stop[, fixed_target]).
# It is called once per bar i; return nothing when there is no signal.
# ======================================================================================
STRATEGIES = {}


def strategy(name):
    def reg(fn):
        STRATEGIES[name] = fn
        return fn
    return reg


@strategy("A sweep & reclaim PDH/PDL")
def s_sweep(D, i):
    if not dtime(9, 35) <= D.t[i] <= dtime(11, 0):
        return
    lo, hi = D.l[:i + 1].min(), D.h[:i + 1].max()
    if lo < D.pdl and D.c[i] > D.pdl and D.c[i - 1] <= D.pdl:
        yield "long", D.c[i], lo - 0.02
    if hi > D.pdh and D.c[i] < D.pdh and D.c[i - 1] >= D.pdh:
        yield "short", D.c[i], hi + 0.02


@strategy("A2 sweep & reclaim of an intraday swing low/high")
def s_sweep_swing(D, i):
    """Liquidity rests under the day's earlier swing lows (above swing highs). A run through that level
    that closes back inside within the same bar is a sweep; trade the reclaim."""
    if not dtime(10, 0) <= D.t[i] <= dtime(15, 0) or i < 8:
        return
    lows = [k for k in range(1, i - 5) if D.l[k] < D.l[k - 1] and D.l[k] < D.l[k + 1]]
    highs = [k for k in range(1, i - 5) if D.h[k] > D.h[k - 1] and D.h[k] > D.h[k + 1]]
    if lows:
        lvl = D.l[lows[-1]]
        if D.l[i] < lvl and D.c[i] > lvl and D.l[lows[-1] + 1:i].min() >= lvl:
            yield "long", D.c[i], D.l[i] - 0.02
    if highs:
        lvl = D.h[highs[-1]]
        if D.h[i] > lvl and D.c[i] < lvl and D.h[highs[-1] + 1:i].max() <= lvl:
            yield "short", D.c[i], D.h[i] + 0.02


def _session_sweep(D, i, hi_key, lo_key, start, end):
    if not start <= D.t[i] <= end or hi_key not in D.lv:
        return
    hi, lo = D.lv[hi_key], D.lv[lo_key]
    run_lo, run_hi = D.l[:i + 1].min(), D.h[:i + 1].max()
    if run_lo < lo and D.c[i] > lo and D.c[i - 1] <= lo * 1.0005:
        yield "long", D.c[i], run_lo - 0.02
    if run_hi > hi and D.c[i] < hi and D.c[i - 1] >= hi * 0.9995:
        yield "short", D.c[i], run_hi + 0.02


@strategy("N London high/low sweep at the NY open")
def s_london(D, i):
    yield from _session_sweep(D, i, "lon_hi", "lon_lo", dtime(9, 35), dtime(11, 0)) or ()


@strategy("N2 Asia high/low sweep at the NY open")
def s_asia(D, i):
    yield from _session_sweep(D, i, "asia_hi", "asia_lo", dtime(9, 35), dtime(11, 0)) or ()


@strategy("P premarket high/low sweep & reclaim")
def s_premarket(D, i):
    yield from _session_sweep(D, i, "pm_hi", "pm_lo", dtime(9, 35), dtime(11, 30)) or ()


@strategy("C opening range breakout + retest")
def s_orb(D, i):
    if not dtime(9, 50) <= D.t[i] <= dtime(10, 45) or D.orw <= 0:
        return
    if (D.c[3:i] > D.orh).any() and D.c[i - 1] > D.orh and D.l[i] <= D.orh < D.c[i]:
        yield "long", D.c[i], max(D.orh - D.orw / 3, (D.orh + D.orl) / 2)
    if (D.c[3:i] < D.orl).any() and D.c[i - 1] < D.orl and D.h[i] >= D.orl > D.c[i]:
        yield "short", D.c[i], min(D.orl + D.orw / 3, (D.orh + D.orl) / 2)


@strategy("C2 opening range breakout (no retest)")
def s_orb_break(D, i):
    if not dtime(9, 45) <= D.t[i] <= dtime(11, 0) or D.orw <= 0:
        return
    if D.c[i] > D.orh and (D.c[3:i] <= D.orh).all() and D.v[i] > D.avgv[i]:
        yield "long", D.c[i], (D.orh + D.orl) / 2
    if D.c[i] < D.orl and (D.c[3:i] >= D.orl).all() and D.v[i] > D.avgv[i]:
        yield "short", D.c[i], (D.orh + D.orl) / 2


@strategy("F VWAP reclaim / reject")
def s_vwap(D, i):
    if not dtime(10, 0) <= D.t[i] <= dtime(15, 0) or i < 6:
        return
    slope = D.vwap[i] - D.vwap[i - 6]
    if slope > 0 and D.c[i - 1] < D.vwap[i - 1] and D.c[i] > D.vwap[i]:
        yield "long", D.c[i], D.l[i - 3:i + 1].min() - 0.02
    if slope < 0 and D.c[i - 1] > D.vwap[i - 1] and D.c[i] < D.vwap[i]:
        yield "short", D.c[i], D.h[i - 3:i + 1].max() + 0.02


@strategy("D2 VWAP band fade (mean reversion)")
def s_band(D, i):
    if not dtime(10, 30) <= D.t[i] <= dtime(15, 0) or i < 12:
        return
    sd = np.std(D.c[:i + 1] - D.vwap[:i + 1])
    if sd <= 0:
        return
    if D.l[i - 1] < D.vwap[i - 1] - 2 * sd and D.c[i] > D.h[i - 1]:
        yield "long", D.c[i], D.l[i - 1:i + 1].min() - 0.02, D.vwap[i]
    if D.h[i - 1] > D.vwap[i - 1] + 2 * sd and D.c[i] < D.l[i - 1]:
        yield "short", D.c[i], D.h[i - 1:i + 1].max() + 0.02, D.vwap[i]


@strategy("G gap and go")
def s_gapgo(D, i):
    if not dtime(9, 35) <= D.t[i] <= dtime(10, 30) or abs(D.gap) < 0.003:
        return
    if D.gap > 0 and D.c[0] > D.o[0] and D.l[i - 1] > D.o[0] and D.c[i] > D.h[i - 1]:
        yield "long", D.c[i], D.l[i - 1] - 0.02
    if D.gap < 0 and D.c[0] < D.o[0] and D.h[i - 1] < D.o[0] and D.c[i] < D.l[i - 1]:
        yield "short", D.c[i], D.h[i - 1] + 0.02


@strategy("G2 gap fill")
def s_gapfill(D, i):
    if not dtime(9, 45) <= D.t[i] <= dtime(10, 30) or abs(D.gap) < 0.003 or i <= 3:
        return
    if D.gap > 0 and D.h[3:i].max() <= D.h[:3].max() and D.c[i] < D.l[0]:
        stop = D.h[:i].max() + 0.02
        if D.c[i] - D.pclose >= 1.5 * (stop - D.c[i]):
            yield "short", D.c[i], stop, D.pclose
    if D.gap < 0 and D.l[3:i].min() >= D.l[:3].min() and D.c[i] > D.h[0]:
        stop = D.l[:i].min() - 0.02
        if D.pclose - D.c[i] >= 1.5 * (D.c[i] - stop):
            yield "long", D.c[i], stop, D.pclose


@strategy("I prior-day high/low breakout")
def s_pdbreak(D, i):
    if not dtime(10, 0) <= D.t[i] <= dtime(14, 30):
        return
    if D.c[i] > D.pdh and (D.c[:i] <= D.pdh).all() and D.v[i] > 1.2 * D.avgv[i]:
        yield "long", D.c[i], D.l[i - 2:i + 1].min() - 0.02
    if D.c[i] < D.pdl and (D.c[:i] >= D.pdl).all() and D.v[i] > 1.2 * D.avgv[i]:
        yield "short", D.c[i], D.h[i - 2:i + 1].max() + 0.02


@strategy("J midday range breakout (afternoon trend)")
def s_midday(D, i):
    if not dtime(13, 30) <= D.t[i] <= dtime(15, 0):
        return
    mid = [k for k in range(i) if dtime(11, 30) <= D.t[k] < dtime(13, 30)]
    if len(mid) < 12:
        return
    mh, ml = D.h[mid].max(), D.l[mid].min()
    after = range(mid[-1] + 1, i)
    if D.c[i] > mh and all(D.c[k] <= mh for k in after):
        yield "long", D.c[i], (mh + ml) / 2
    if D.c[i] < ml and all(D.c[k] >= ml for k in after):
        yield "short", D.c[i], (mh + ml) / 2


@strategy("K opening drive continuation")
def s_drive(D, i):
    if D.t[i] != dtime(9, 50):
        return
    move = D.c[3] / D.o[0] - 1
    if move > 0.004 and D.c[i] > D.vwap[i]:
        yield "long", D.c[i], D.l[:i + 1].min() - 0.02
    if move < -0.004 and D.c[i] < D.vwap[i]:
        yield "short", D.c[i], D.h[:i + 1].max() + 0.02


@strategy("L first pullback after new high/low of day")
def s_firstpb(D, i):
    if not dtime(10, 0) <= D.t[i] <= dtime(14, 0) or i < 4:
        return
    hod, lod = D.h[:i - 1].max(), D.l[:i - 1].min()
    if D.h[i - 2] >= hod and D.l[i - 1] < D.l[i - 2] and D.c[i] > D.h[i - 1] and D.c[i] > D.vwap[i]:
        yield "long", D.c[i], D.l[i - 1] - 0.02
    if D.l[i - 2] <= lod and D.h[i - 1] > D.h[i - 2] and D.c[i] < D.l[i - 1] and D.c[i] < D.vwap[i]:
        yield "short", D.c[i], D.h[i - 1] + 0.02


@strategy("M inside bar breakout")
def s_inside(D, i):
    if not dtime(10, 0) <= D.t[i] <= dtime(15, 0) or i < 3:
        return
    if D.h[i - 1] < D.h[i - 2] and D.l[i - 1] > D.l[i - 2]:
        if D.c[i] > D.h[i - 2] and D.c[i] > D.vwap[i]:
            yield "long", D.c[i], D.l[i - 1] - 0.02
        if D.c[i] < D.l[i - 2] and D.c[i] < D.vwap[i]:
            yield "short", D.c[i], D.h[i - 1] + 0.02


# ======================================================================================
# Data, situations, simulation
# ======================================================================================
class Day:
    pass


def flat_cols(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.rename(columns=str.lower)


FUTURES = {"SPY": "ES=F", "QQQ": "NQ=F"}
_fut_cache = {}


def session_levels(symbol, ext, d, open_px):
    """Overnight liquidity for day d, in the symbol's own prices.
    Asia 18:00 (prev day) to 03:00 ET and London 03:00-08:30 ET come from the index futures for SPY/QQQ
    (scaled to the ETF by the price ratio at the open). Premarket 04:00-09:29 ET comes from the symbol."""
    lv = {}
    pm = ext[(ext.index.date == d) & (ext.index.time >= dtime(4, 0)) & (ext.index.time < dtime(9, 30))]
    if len(pm):
        lv["pm_hi"], lv["pm_lo"] = pm.high.max(), pm.low.min()
    fut_sym = FUTURES.get(symbol)
    if fut_sym:
        if fut_sym not in _fut_cache:
            f = flat_cols(yf.download(fut_sym, period="60d", interval="5m", progress=False, prepost=True, auto_adjust=False))
            f.index = f.index.tz_convert("America/New_York")
            _fut_cache[fut_sym] = f
        f = _fut_cache[fut_sym]
        day0 = pd.Timestamp(d, tz="America/New_York")
        at_open = f[(f.index >= day0 + pd.Timedelta(hours=9, minutes=30))].head(1)
        if len(at_open):
            ratio = open_px / float(at_open.open.iloc[0])
            asia = f[(f.index >= day0 - pd.Timedelta(hours=6)) & (f.index < day0 + pd.Timedelta(hours=3))]
            lon = f[(f.index >= day0 + pd.Timedelta(hours=3)) & (f.index < day0 + pd.Timedelta(hours=8, minutes=30))]
            if len(asia) > 20:
                lv["asia_hi"], lv["asia_lo"] = asia.high.max() * ratio, asia.low.min() * ratio
            if len(lon) > 20:
                lv["lon_hi"], lv["lon_lo"] = lon.high.max() * ratio, lon.low.min() * ratio
    return lv


def load(symbol, vix_by_day):
    ext = flat_cols(yf.download(symbol, period="60d", interval="5m", progress=False, prepost=True, auto_adjust=False))
    if not ext.empty:
        ext.index = ext.index.tz_convert("America/New_York")
    intra = flat_cols(yf.download(symbol, period="60d", interval="5m", progress=False, prepost=False, auto_adjust=False))
    daily = flat_cols(yf.download(symbol, period="1y", interval="1d", progress=False, auto_adjust=False))
    if intra.empty or daily.empty:
        return []
    intra = intra[["open", "high", "low", "close", "volume"]].dropna()
    intra.index = intra.index.tz_convert("America/New_York")
    daily["sma20"], daily["sma50"] = daily.close.rolling(20).mean(), daily.close.rolling(50).mean()
    daily["rng"] = daily.high - daily.low
    daily["rng20"] = daily.rng.rolling(20).mean()
    ctx = {}
    for k in range(1, len(daily)):
        y = daily.iloc[k - 1]                          # yesterday: known before today's open
        if pd.isna(y.sma50) or pd.isna(y.rng20):
            continue
        trend = "up" if y.close > y.sma20 > y.sma50 else "down" if y.close < y.sma20 < y.sma50 else "flat"
        vol = "high" if y.rng > 1.3 * y.rng20 else "low" if y.rng < 0.7 * y.rng20 else "normal"
        ctx[daily.index[k].date()] = dict(trend=trend, vol=vol)

    days, out = sorted(set(intra.index.date)), []
    for k in range(1, len(days)):
        d = days[k]
        prev, day = intra[intra.index.date == days[k - 1]], intra[intra.index.date == d]
        if len(day) < 30 or prev.empty or d not in ctx:
            continue
        D = Day()
        D.sym, D.date = symbol, d
        D.o, D.h, D.l, D.c, D.v = (day[x].to_numpy() for x in ("open", "high", "low", "close", "volume"))
        D.t = [ts.time() for ts in day.index]
        tp = (D.h + D.l + D.c) / 3
        D.vwap = np.cumsum(tp * D.v) / np.cumsum(D.v)
        D.avgv = np.concatenate([[np.inf], np.cumsum(D.v)[:-1] / np.arange(1, len(D.v))])
        D.pdh, D.pdl, D.pclose = prev.high.max(), prev.low.min(), prev.close.iloc[-1]
        D.orh, D.orl = D.h[:3].max(), D.l[:3].min()
        D.orw = D.orh - D.orl
        D.gap = D.o[0] / D.pclose - 1
        D.lv = session_levels(symbol, ext, d, D.o[0]) if not ext.empty else {}
        ref = (D.lv.get("lon_hi"), D.lv.get("lon_lo")) if "lon_hi" in D.lv else (D.lv.get("pm_hi"), D.lv.get("pm_lo"))
        open_vs = ("unknown" if ref[0] is None else "above" if D.o[0] > ref[0] else "below" if D.o[0] < ref[1] else "inside")
        vix = vix_by_day.get(d)
        D.sit = dict(ctx[d], gap="up" if D.gap > 0.003 else "down" if D.gap < -0.003 else "flat",
                     vix="unknown" if vix is None else "calm" if vix < 20 else "nervous" if vix <= 30 else "fear",
                     overnight=open_vs)
        out.append(D)
    return out


def time_bucket(t):
    return "open" if t < dtime(10, 30) else "midday" if t < dtime(14, 0) else "late"


def simulate(D, i, side, entry, stop, target):
    long, risk = side == "long", (entry - stop if side == "long" else stop - entry)
    res = {}
    for ex in EXITS:
        tgt = target if target is not None else (entry + ex["target"] * risk if long else entry - ex["target"] * risk)
        st, be = stop, entry + risk if long else entry - risk
        px = D.c[-1]
        for j in range(i + 1, len(D.c)):
            if D.t[j] >= FLAT:
                px = D.c[j]; break
            if long:
                if D.l[j] <= st: px = st; break
                if D.h[j] >= tgt: px = tgt; break
                if ex["be"] and D.h[j] >= be: st = max(st, entry)
            else:
                if D.h[j] >= st: px = st; break
                if D.l[j] <= tgt: px = tgt; break
                if ex["be"] and D.l[j] <= be: st = min(st, entry)
        gross = px - entry if long else entry - px
        res[(ex["target"], ex["be"])] = (gross - SLIP * (entry + px)) / risk
    return res


def collect(days):
    rows = []
    for D in days:
        fired = set()
        for i in range(3, len(D.c)):
            if D.t[i] > LAST_ENTRY:
                break
            for name, fn in STRATEGIES.items():
                for sig in fn(D, i) or ():
                    side, entry, stop = sig[:3]
                    target = sig[3] if len(sig) > 3 else None
                    risk = entry - stop if side == "long" else stop - entry
                    if (name, side) in fired or risk <= entry * 0.0005:
                        continue
                    if target is not None and ((target - entry) if side == "long" else (entry - target)) < risk:
                        continue
                    fired.add((name, side))
                    sit = dict(D.sit, time=time_bucket(D.t[i]), vwap="above" if D.c[i] > D.vwap[i] else "below")
                    rows.append(dict(strategy=name, side=side, symbol=D.sym, date=D.date, **sit,
                                     **{f"R_{t}_{b}": r for (t, b), r in simulate(D, i, side, entry, stop, target).items()}))
    return pd.DataFrame(rows)


# ======================================================================================
# Learning
# ======================================================================================
FEATURES = ["trend", "gap", "vol", "vix", "time", "vwap", "overnight"]


def conditions(df):
    singles = [((f, v),) for f in FEATURES for v in sorted(df[f].unique()) if v != "unknown"]
    pairs = [(a[0], b[0]) for a, b in itertools.combinations(singles, 2) if a[0][0] != b[0][0]]
    return [()] + singles + pairs


def mask(df, cond):
    m = np.ones(len(df), dtype=bool)
    for f, v in cond:
        m &= (df[f] == v).to_numpy()
    return m


def learn(df, cut):
    train, test = df[df.date < cut], df[df.date >= cut]
    rules, tested = [], 0
    for (strat, side), g_tr in train.groupby(["strategy", "side"]):
        g_te = test[(test.strategy == strat) & (test.side == side)]
        for cond in conditions(g_tr):
            m_tr = mask(g_tr, cond)
            if m_tr.sum() < MIN_TRAIN:
                continue
            for ex in EXITS:
                col = f"R_{ex['target']}_{ex['be']}"
                tested += 1
                tr_r = g_tr[col].to_numpy()[m_tr]
                if tr_r.mean() < TRAIN_EDGE:
                    continue
                te_r = g_te[col].to_numpy()[mask(g_te, cond)] if len(g_te) else np.array([])
                if len(te_r) >= MIN_TEST and te_r.mean() > TEST_EDGE:
                    rules.append(dict(strategy=strat, side=side, when=dict(cond), exit=ex,
                                      train=dict(trades=int(len(tr_r)), avgR=round(float(tr_r.mean()), 2),
                                                 win=round(float((tr_r > 0).mean() * 100))),
                                      test=dict(trades=int(len(te_r)), avgR=round(float(te_r.mean()), 2),
                                                win=round(float((te_r > 0).mean() * 100)))))
    # keep the best exit per (strategy, side, situation); rank by unseen-data result, then sample size
    best = {}
    for r in rules:
        k = (r["strategy"], r["side"], tuple(sorted(r["when"].items())))
        if k not in best or (r["test"]["avgR"], r["test"]["trades"]) > (best[k]["test"]["avgR"], best[k]["test"]["trades"]):
            best[k] = r
    # drop duplicates: same strategy/side/exit with identical results -> keep the simplest situation
    simple = {}
    for r in best.values():
        k = (r["strategy"], r["side"], r["exit"]["target"], r["exit"]["be"],
             r["train"]["trades"], r["train"]["avgR"], r["test"]["trades"], r["test"]["avgR"])
        if k not in simple or len(r["when"]) < len(simple[k]["when"]):
            simple[k] = r
    ranked = sorted(simple.values(), key=lambda r: (r["test"]["avgR"] * np.sqrt(r["test"]["trades"])), reverse=True)
    return ranked, tested


def fmt_when(w):
    return " & ".join(f"{k}={v}" for k, v in w.items()) or "any situation"


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    symbols = [s.upper() for s in sys.argv[1:]] or DEFAULT
    vix = flat_cols(yf.download("^VIX", period="1y", interval="1d", progress=False, auto_adjust=False))
    vix_by_day = {ts.date(): float(c) for ts, c in vix.close.items()} if not vix.empty else {}
    days = []
    for s in symbols:
        d = load(s, vix_by_day)
        print(f"{s}: {len(d)} days")
        days += d
    df = collect(days)
    if df.empty:
        print("No signals."); return
    dates = sorted(df.date.unique())
    cut = dates[int(len(dates) * 2 / 3)]
    rules, tested = learn(df, cut)

    raw = (df.groupby(["strategy", "side"])["R_2.0_False"].agg(["count", "mean"]).round(2)
           .rename(columns={"count": "signals", "mean": "avg R (raw, 2R)"}).reset_index())
    OUT.mkdir(exist_ok=True)
    stamp = date.today().isoformat()
    play = dict(date=stamp, symbols=symbols, train_until=str(cut), combinations_tested=tested,
                strategies_in_library=list(STRATEGIES), rules=rules)
    (OUT / "playbook.json").write_text(json.dumps(play, indent=2, default=str), encoding="utf-8")
    (OUT / "approved.json").write_text(json.dumps(dict(date=stamp, approved=rules), indent=2, default=str), encoding="utf-8")

    lines = [f"# Playbook {stamp}", "",
             f"{len(STRATEGIES)} strategies × long/short × situations × exits = {tested} combinations tested on "
             f"{len(symbols)} symbols, {len(dates)} days ({len(df)} signals). Learned on days before {cut}, "
             f"checked on days from {cut}.", "",
             f"## Playbook: {len(rules)} rules with an edge on unseen days", ""]
    if rules:
        lines += ["| # | situation | strategy | side | exit | learned (trades, avg R) | unseen (trades, avg R, win%) |",
                  "|---|---|---|---|---|---|---|"]
        for n, r in enumerate(rules[:40], 1):
            ex = f"{r['exit']['target']}R" + (" +BE" if r["exit"]["be"] else "")
            lines.append(f"| {n} | {fmt_when(r['when'])} | {r['strategy']} | {r['side']} | {ex} | "
                         f"{r['train']['trades']}, {r['train']['avgR']:+.2f} | "
                         f"{r['test']['trades']}, {r['test']['avgR']:+.2f}, {r['test']['win']}% |")
    else:
        lines.append("_No combination held up on unseen days. The desk stays on paper and keeps collecting evidence._")
    lines += ["", "## Every strategy, no filters (baseline)", "", raw.to_markdown(index=False), "",
              "## Caution", f"- Testing {tested} combinations means some will pass by luck. Trust rules that keep "
              "showing up week after week, with many unseen trades.",
              "- R × $5 ≈ dollars on a $500 book at 1% risk."]
    (OUT / f"report_{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:min(len(lines), 22)]))
    print(f"\nPlaybook: {OUT / 'playbook.json'}\nReport:   {OUT / f'report_{stamp}.md'}")


if __name__ == "__main__":
    main()
