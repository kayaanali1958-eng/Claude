#!/usr/bin/env python3
"""TJR / ICT-style New York morning day-trading model on SPY and QQQ, 5-minute candles.

The model (one setup per symbol per day, the first one that completes):
  1. Liquidity: the prior regular-session high/low and the premarket (04:00-09:30 ET) high/low.
  2. Sweep: between 09:30 and 11:30 ET price trades through one of those levels.
  3. Market structure shift: after a sweep of a LOW, a 5-minute candle closes above the last swing
     high with displacement (body bigger than the 14-bar average range). Mirror image after a sweep
     of a HIGH (a short, through the inverse fund).
  4. Entry: a limit at the fair value gap the displacement left (or the MSS close if it left none),
     filled within the next 6 candles, else no trade.
  5. Stop: beyond the sweep's extreme. Target: 2R. Anything open at 15:55 ET is closed.
Execution like the live desk: the leveraged fund (SPYU 4x / TQQQ 3x long, SPXS / SQQQ 3x short),
all-in, one trade at a time, 0.1% cost per side plus 0.03% slippage, and at most 3 day trades in any
5 trading days (Robinhood's rule under $25,000).

Yahoo keeps only the last 60 days of 5-minute candles, so this is a small sample: read it as a first
check, not proof. Usage: python scripts/tjr_backtest.py
"""
import sys
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

ET = ZoneInfo("America/New_York")
LEV = {("SPY", 1): 4, ("SPY", -1): 3, ("QQQ", 1): 3, ("QQQ", -1): 3}
COST, SLIP = 0.001, 0.0003


def load(sym):
    d = yf.download(sym, period="60d", interval="5m", prepost=True, progress=False, auto_adjust=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.rename(columns=str.lower)[["open", "high", "low", "close"]].dropna()
    d.index = d.index.tz_convert(ET)
    return d


def setups(sym, d, target_r=2.0, last_entry="11:30", bias=None):
    out = []
    days = sorted(set(d.index.date))
    prev_hi = prev_lo = None
    for day in days:
        x = d[d.index.date == day]
        rth = x.between_time("09:30", "15:55")
        pre = x.between_time("04:00", "09:25")
        if len(rth) < 60:
            prev_hi = prev_lo = None
            continue
        levels_lo = [v for v in (prev_lo, pre.low.min() if len(pre) else None) if v is not None]
        levels_hi = [v for v in (prev_hi, pre.high.max() if len(pre) else None) if v is not None]
        o, h, l, c = (rth[k].to_numpy() for k in ("open", "high", "low", "close"))
        t = rth.index
        rng = pd.Series(h - l).rolling(14, min_periods=5).mean().to_numpy()
        done = False
        swept_lo = swept_hi = None                     # extreme of the sweep so far
        for i in range(2, len(rth)):
            if done or t[i].strftime("%H:%M") > last_entry:
                break
            if any(l[i] < v for v in levels_lo):
                swept_lo = l[i] if swept_lo is None else min(swept_lo, l[i])
            if any(h[i] > v for v in levels_hi):
                swept_hi = h[i] if swept_hi is None else max(swept_hi, h[i])
            disp = abs(c[i] - o[i]) > (rng[i - 1] if not np.isnan(rng[i - 1]) else np.inf)
            for side, swept in ((1, swept_lo), (-1, swept_hi)):
                if bias is not None and bias.get(day, 0) != side:
                    continue                           # trade only with the daily trend (HTF bias)
                if swept is None or not disp:
                    continue
                seg = slice(max(0, i - 12), i)
                if side == 1 and c[i] > o[i] and c[i] > h[seg].max():
                    gap_top = l[i] if l[i] > h[i - 2] else None       # bullish FVG: low[i] > high[i-2]
                    entry_lvl = h[i - 2] if gap_top else c[i]
                    stop = swept * (1 - 0.0005)
                elif side == -1 and c[i] < o[i] and c[i] < l[seg].min():
                    gap = h[i] < l[i - 2]
                    entry_lvl = l[i - 2] if gap else c[i]
                    stop = swept * (1 + 0.0005)
                else:
                    continue
                risk = (entry_lvl - stop) * side
                if risk <= entry_lvl * 0.0008:
                    continue
                # fill: the limit is touched within the next 6 candles
                fill_j = None
                for j in range(i + 1, min(i + 7, len(rth))):
                    if (side == 1 and l[j] <= entry_lvl) or (side == -1 and h[j] >= entry_lvl):
                        fill_j = j
                        break
                done = True
                if fill_j is None:
                    break
                e = entry_lvl * (1 + side * SLIP)
                tgt = entry_lvl + side * target_r * risk
                px, k = c[-1], len(rth) - 1
                for k in range(fill_j, len(rth)):
                    if (side == 1 and l[k] <= stop) or (side == -1 and h[k] >= stop):
                        px = stop; break
                    if k > fill_j and ((side == 1 and h[k] >= tgt) or (side == -1 and l[k] <= tgt)):
                        px = tgt; break
                px = px * (1 - side * SLIP)
                move = side * (px / e - 1)
                lev = LEV[(sym, side)]
                mult = max(0.0, 1 + lev * move) * (1 - COST) / (1 + COST)
                out.append(dict(day=day, time=t[fill_j], sym=sym, side="long" if side == 1 else "short",
                                r=round(move * e / risk, 2), mult=mult, exit=t[k]))
                break
        prev_hi, prev_lo = rth.high.max(), rth.low.min()
    return out


def daily_bias(sym):
    """+1 when yesterday's close was above the 20-day average and that average is rising, -1 the mirror."""
    d = yf.download(sym, period="1y", interval="1d", progress=False, auto_adjust=True)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    c = d["Close"].dropna()
    s20 = c.rolling(20).mean()
    b = np.where((c > s20) & (s20 > s20.shift(5)), 1, np.where((c < s20) & (s20 < s20.shift(5)), -1, 0))
    b = pd.Series(b, index=c.index).shift(1)               # known before the session opens
    return {k.date(): int(v) for k, v in b.dropna().items()}


def run(trades, start=735.0):
    trades = sorted(trades, key=lambda x: x["time"])
    eq, peak, mdd, taken, busy, dt = start, start, 0.0, [], None, []
    for tr in trades:
        if busy is not None and tr["time"] < busy:
            continue
        recent = [d for d in dt if (pd.Timestamp(tr["day"]) - pd.Timestamp(d)).days < 7]
        if len(recent) >= 3:                           # 3 day trades per 5 trading days
            continue
        eq *= tr["mult"]
        busy = tr["exit"]
        dt.append(tr["day"])
        taken.append(tr)
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    return eq, mdd, taken


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    data = {s: load(s) for s in ("SPY", "QQQ")}
    first, last = min(d.index[0] for d in data.values()), max(d.index[-1] for d in data.values())
    print(f"# TJR / ICT NY-morning model, 5-minute candles, {first:%Y-%m-%d} to {last:%Y-%m-%d}")
    bias = {s: daily_bias(s) for s in data}
    for label, tr_r, b in (("no bias", 2.0, False), ("with daily bias", 2.0, True), ("with daily bias", 3.0, True)):
        trades = [t for s, d in data.items() for t in setups(s, d, tr_r, bias=bias[s] if b else None)]
        eq, mdd, taken = run(trades)
        rs = [t["r"] for t in trades]
        wins = sum(t["mult"] > 1 for t in taken)
        print(f"\n## {label}, target {tr_r}R: {len(trades)} setups (avg {np.mean(rs) if rs else 0:+.2f}R before costs); "
              f"traded {len(taken)} within the day-trade limit, {wins} wins")
        print(f"   $735 -> ${eq:,.2f} ({(eq / 735 - 1) * 100:+.1f}%), worst drop {mdd * 100:.0f}%")
        for t in taken:
            print(f"   {t['time']:%m-%d %H:%M} {t['sym']} {t['side']:5s} {t['r']:+.2f}R  fund x{t['mult']:.3f}")


if __name__ == "__main__":
    main()
