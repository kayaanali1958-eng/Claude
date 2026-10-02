#!/usr/bin/env python3
"""Backtest and filter-search the desk's rule-based strategies on real 5-minute bars.

What it does
  1. Runs each strategy (A sweep, C ORB retest, F VWAP, G gap-and-go, G gap fill), long and short,
     under every combination of the accuracy filters below.
  2. Walk-forward check: picks the best filter set on the first 2/3 of the days (train) and then
     scores it on the last 1/3 it has never seen (test). Only strategies that make money on BOTH
     are marked PASS. This guards against fitting noise.
  3. Writes backtests/report_<date>.md (human report) and backtests/approved.json (what passed,
     with its filters) for the performance-reviewer.

Filters searched (accuracy)
  trend    trade only with the daily trend (price vs. 20-day average, 20 vs. 50-day average)
  vwap     longs only above VWAP, shorts only below
  rvol     entry bar volume at least 1.5x the session average so far
  window   morning only (entries before 11:30)
Exits searched (profit)
  target   1.5R, 2R or 3R
  be       move the stop to breakeven once price reaches +1R

Rules: 1R risk; if stop and target fall inside one bar the stop counts first; flat at 15:55; no
entries after 15:30; 0.02% slippage per side; one trade per strategy, side, symbol and day.
Not modelled: news blackouts, earnings grades, the risk manager's judgment. Treat as a screen.

Usage:  python scripts/backtest.py                 (SPY QQQ)
        python scripts/backtest.py NVDA AMD TSLA   (any symbols; more symbols = more evidence)
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
SLIP = 0.0002
LAST_ENTRY, FLAT = dtime(15, 30), dtime(15, 55)
MIN_TRADES = 8                     # per half, to count as evidence

FILTERS = {"trend": [False, True], "vwap": [False, True], "rvol": [False, True], "morning": [False, True]}
EXITS = {"target": [1.5, 2.0, 3.0], "be": [False, True]}
GRID = [dict(zip(list(FILTERS) + list(EXITS), v)) for v in itertools.product(*FILTERS.values(), *EXITS.values())]


# ---------- data ----------
def load(symbol):
    intra = yf.download(symbol, period="60d", interval="5m", progress=False, prepost=False, auto_adjust=False)
    daily = yf.download(symbol, period="1y", interval="1d", progress=False, auto_adjust=False)
    for df in (intra, daily):
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
    if intra.empty or daily.empty:
        return None, None
    intra = intra.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
    intra.index = intra.index.tz_convert("America/New_York")
    daily = daily.rename(columns=str.lower)
    daily["sma20"], daily["sma50"] = daily.close.rolling(20).mean(), daily.close.rolling(50).mean()
    # trend known at the start of each day = yesterday's values
    trend = np.where((daily.close > daily.sma20) & (daily.sma20 > daily.sma50), 1,
                     np.where((daily.close < daily.sma20) & (daily.sma20 < daily.sma50), -1, 0))
    daily["trend_next"] = pd.Series(trend, index=daily.index).shift(1)
    trend_by_day = {d.date(): t for d, t in daily["trend_next"].items() if not pd.isna(t)}
    return intra, trend_by_day


# ---------- signal generation (raw candidates, filters applied later) ----------
def candidates(sym, intra, trend_by_day):
    """Every raw entry signal, with the context the filters need."""
    out = []
    days = sorted(set(intra.index.date))
    for k in range(1, len(days)):
        d = days[k]
        prev = intra[intra.index.date == days[k - 1]]
        day = intra[intra.index.date == d]
        if len(day) < 20 or prev.empty:
            continue
        o, h, l, c, v = (day[x].to_numpy() for x in ("open", "high", "low", "close", "volume"))
        times = [ts.time() for ts in day.index]
        tp = (h + l + c) / 3
        vwap = np.cumsum(tp * v) / np.cumsum(v)
        avgv = np.concatenate([[np.nan], np.cumsum(v)[:-1] / np.arange(1, len(v))])
        pdh, pdl, pclose = prev.high.max(), prev.low.min(), prev.close.iloc[-1]
        orh, orl = h[:3].max(), l[:3].min()
        orw = orh - orl
        gap = o[0] / pclose - 1
        trend = trend_by_day.get(d, 0)
        ctx = dict(sym=sym, date=d, trend=trend, h=h, l=l, c=c, times=times)
        swept_lo = swept_hi = None
        broke_up = broke_dn = False
        seen = set()

        def sig(strategy, side, i, entry, stop, fixed_target=None):
            key = strategy + side
            if key in seen:
                return
            seen.add(key)
            risk = entry - stop if side == "long" else stop - entry
            if risk <= entry * 0.0005:
                return
            out.append(dict(ctx, strategy=strategy, side=side, i=i, entry=entry, stop=stop, risk=risk,
                            fixed_target=fixed_target, above_vwap=c[i] > vwap[i],
                            rvol=(v[i] / avgv[i]) if avgv[i] else 0, t=times[i]))

        for i in range(3, len(c)):
            t = times[i]
            if t > LAST_ENTRY:
                break
            # A: sweep of prior day low/high, close back inside (9:35-11:00)
            if dtime(9, 35) <= t <= dtime(11, 0):
                if l[i] < pdl:
                    swept_lo = min(swept_lo or l[i], l[i])
                if h[i] > pdh:
                    swept_hi = max(swept_hi or h[i], h[i])
                if swept_lo and c[i] > pdl:
                    sig("A sweep", "long", i, c[i], swept_lo - 0.02)
                if swept_hi and c[i] < pdh:
                    sig("A sweep", "short", i, c[i], swept_hi + 0.02)
            # C: ORB with volume, then a retest that holds (9:50-10:45)
            if dtime(9, 50) <= t <= dtime(10, 45) and orw > 0:
                if c[i] > orh and v[i] > avgv[i]:
                    broke_up = True
                if c[i] < orl and v[i] > avgv[i]:
                    broke_dn = True
                if broke_up and c[i - 1] > orh and l[i] <= orh < c[i]:
                    sig("C ORB retest", "long", i, c[i], max(orh - orw / 3, (orh + orl) / 2))
                if broke_dn and c[i - 1] < orl and h[i] >= orl > c[i]:
                    sig("C ORB retest", "short", i, c[i], min(orl + orw / 3, (orh + orl) / 2))
            # F: VWAP reclaim/reject with the VWAP slope (10:00-15:00)
            if dtime(10, 0) <= t <= dtime(15, 0) and i >= 6:
                slope = vwap[i] - vwap[i - 6]
                if slope > 0 and c[i - 1] < vwap[i - 1] and c[i] > vwap[i]:
                    sig("F VWAP", "long", i, c[i], l[i - 3:i + 1].min() - 0.02)
                if slope < 0 and c[i - 1] > vwap[i - 1] and c[i] < vwap[i]:
                    sig("F VWAP", "short", i, c[i], h[i - 3:i + 1].max() + 0.02)
            # G: opening gap >= 0.3% (9:35-10:30)
            if dtime(9, 35) <= t <= dtime(10, 30) and abs(gap) >= 0.003:
                if gap > 0 and c[0] > o[0] and l[i - 1] > o[0] and c[i] > h[i - 1]:
                    sig("G gap&go", "long", i, c[i], l[i - 1] - 0.02)
                if gap < 0 and c[0] < o[0] and h[i - 1] < o[0] and c[i] < l[i - 1]:
                    sig("G gap&go", "short", i, c[i], h[i - 1] + 0.02)
                if t >= dtime(9, 45) and i > 3:
                    if gap > 0 and h[3:i].max() <= h[:3].max() and c[i] < l[0]:
                        stop = h[:i].max() + 0.02
                        if c[i] - pclose >= 1.5 * (stop - c[i]):
                            sig("G gap fill", "short", i, c[i], stop, pclose)
                    elif gap < 0 and l[3:i].min() >= l[:3].min() and c[i] > h[0]:
                        stop = l[:i].min() - 0.02
                        if pclose - c[i] >= 1.5 * (c[i] - stop):
                            sig("G gap fill", "long", i, c[i], stop, pclose)
    return out


# ---------- filters and exits ----------
def passes(s, cfg):
    long = s["side"] == "long"
    if cfg["trend"] and s["trend"] != (1 if long else -1):
        return False
    if cfg["vwap"] and s["above_vwap"] != long:
        return False
    if cfg["rvol"] and s["rvol"] < 1.5:
        return False
    if cfg["morning"] and s["t"] > dtime(11, 30):
        return False
    return True


def result_R(s, cfg):
    h, l, c, times, i = s["h"], s["l"], s["c"], s["times"], s["i"]
    long, entry, risk = s["side"] == "long", s["entry"], s["risk"]
    stop = s["stop"]
    target = s["fixed_target"] if s["fixed_target"] else (entry + cfg["target"] * risk if long else entry - cfg["target"] * risk)
    be_trigger = entry + risk if long else entry - risk
    exit_px = c[-1]
    for j in range(i + 1, len(c)):
        if times[j] >= FLAT:
            exit_px = c[j]
            break
        if long:
            if l[j] <= stop:
                exit_px = stop; break
            if h[j] >= target:
                exit_px = target; break
            if cfg["be"] and h[j] >= be_trigger:
                stop = max(stop, entry)
        else:
            if h[j] >= stop:
                exit_px = stop; break
            if l[j] <= target:
                exit_px = target; break
            if cfg["be"] and l[j] <= be_trigger:
                stop = min(stop, entry)
    gross = exit_px - entry if long else entry - exit_px
    return (gross - SLIP * (entry + exit_px)) / risk


def score(sigs, cfg):
    rs = [result_R(s, cfg) for s in sigs if passes(s, cfg)]
    if not rs:
        return dict(trades=0, win=0, avgR=0.0, totalR=0.0, dd=0.0)
    eq = np.cumsum(rs)
    return dict(trades=len(rs), win=round(100 * np.mean(np.array(rs) > 0)), avgR=round(float(np.mean(rs)), 2),
                totalR=round(float(np.sum(rs)), 1), dd=round(float((np.maximum.accumulate(eq) - eq).max()), 1))


def describe(cfg):
    f = [k for k in FILTERS if cfg[k]]
    return (", ".join(f) or "no filters") + f" · target {cfg['target']}R" + (" · breakeven at 1R" if cfg["be"] else "")


# ---------- main ----------
def main():
    symbols = [s.upper() for s in sys.argv[1:]] or ["SPY", "QQQ"]
    sigs = []
    for sym in symbols:
        intra, trend = load(sym)
        if intra is None:
            print(f"{sym}: no data"); continue
        s = candidates(sym, intra, trend)
        print(f"{sym}: {len(s)} raw signals")
        sigs += s
    if not sigs:
        print("No signals."); return
    all_days = sorted({s["date"] for s in sigs})
    cut = all_days[int(len(all_days) * 2 / 3)]
    train = [s for s in sigs if s["date"] < cut]
    test = [s for s in sigs if s["date"] >= cut]

    rows, approved = [], []
    base = dict(trend=False, vwap=False, rvol=False, morning=False, target=2.0, be=False)
    for strat, side in sorted({(s["strategy"], s["side"]) for s in sigs}):
        tr = [s for s in train if s["strategy"] == strat and s["side"] == side]
        te = [s for s in test if s["strategy"] == strat and s["side"] == side]
        raw = score(tr + te, base)
        best_cfg, best = None, None
        for cfg in GRID:
            sc = score(tr, cfg)
            if sc["trades"] >= MIN_TRADES and (best is None or sc["totalR"] > best["totalR"]):
                best_cfg, best = cfg, sc
        if best_cfg is None:
            rows.append([strat, side, raw["trades"], raw["avgR"], "-", "-", "-", "not enough trades"])
            continue
        oos = score(te, best_cfg)
        verdict = "PASS" if best["avgR"] > 0.1 and oos["avgR"] > 0 and oos["trades"] >= 3 else "FAIL"
        rows.append([strat, side, raw["trades"], raw["avgR"], describe(best_cfg),
                     f"{best['trades']} tr, {best['avgR']:+.2f}R", f"{oos['trades']} tr, {oos['avgR']:+.2f}R", verdict])
        if verdict == "PASS":
            approved.append(dict(strategy=strat, side=side, filters=best_cfg, train=best, test=oos))

    OUT.mkdir(exist_ok=True)
    stamp = date.today().isoformat()
    table = pd.DataFrame(rows, columns=["strategy", "side", "raw trades", "raw avg R", "best filters (train)",
                                        "train", "test (unseen)", "verdict"])
    lines = [f"# Backtest {stamp}", "",
             f"Symbols: {', '.join(symbols)} · last {len(all_days)} trading days of 5-minute bars · "
             f"train before {cut}, test from {cut}", "",
             "Raw = strategy with no filters. Best filters are chosen on the train days only, then scored on the "
             "unseen test days. PASS = positive on both (train avg > +0.10R, test avg > 0, at least 3 test trades).", "",
             table.to_markdown(index=False), "",
             f"**Passed:** {', '.join(a['strategy'] + ' ' + a['side'] for a in approved) or 'none'}", "",
             "## How to use this",
             "- Only PASS combinations have evidence. The performance-reviewer proposes trading those (with their filters) and pausing FAIL ones; you approve in lessons.md.",
             "- At $5 risk per trade, R × $5 ≈ dollars on a $500 book.",
             "- Small samples lie. Re-run weekly with more symbols (e.g., the scanner's stocks) and trust results that keep passing."]
    (OUT / f"report_{stamp}.md").write_text("\n".join(lines) + "\n")
    (OUT / "approved.json").write_text(json.dumps(dict(date=stamp, symbols=symbols, cut=str(cut), approved=approved),
                                                  indent=2, default=str))
    print("\n".join(lines[:9]))
    print(f"\nReport: {OUT / f'report_{stamp}.md'}")


if __name__ == "__main__":
    main()
