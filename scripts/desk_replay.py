#!/usr/bin/env python3
"""Replays the live desk as it really trades, on the months the playbooks never learned from.

Walk-forward: rules learned on the first half, checked on the third quarter, and the desk replayed on
the last quarter, which nothing was chosen on. Unlike the playbook gates (which trade the raw coin/ETF 24/7 without leverage), this follows the live
setup: CRYPTO_FUNDS_ONLY, all-in, one trade at a time, entries only 10:00-15:00 ET on weekdays,
the leveraged or inverse fund for each signal (LEV_FUNDS in crypto_desk.py), fund costs, and exits
that happen while the stock market is closed carried out at the next open (gap risk included).

Usage:  python scripts/desk_replay.py            (prints the report; nothing is traded)
        python scripts/desk_replay.py --variants (also tries a few rule/exit variants side by side)
"""
import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crypto_lib as cl
import backtest_crypto as bc
import backtest_stocks as bs

ET = ZoneInfo("America/New_York")
FUND_COST = 0.001
LEV = {"BTC": 2, "ETH": 2, "XRP": 2, "-BTC": 2, "-ETH": 2,
       "SPY": 4, "QQQ": 3, "SMH": 3, "-SPY": 3, "-QQQ": 3, "-SMH": 3}


def entry_ok(t):
    t = t.astimezone(ET)
    m = t.hour * 60 + t.minute
    return t.weekday() < 5 and 600 <= m < 900


def market_open(t):
    t = t.astimezone(ET)
    m = t.hour * 60 + t.minute
    return t.weekday() < 5 and 570 <= m < 960


def next_open(t):
    t = t.astimezone(ET)
    while True:
        t = t + timedelta(minutes=30)
        t = t.replace(minute=30 if t.minute >= 30 else 0, second=0, microsecond=0)
        if market_open(t):
            return t


def learn(raw, trend_sym, shorts, fee):
    cl.FEE = fee
    up = cl.daily_trend_series(raw[trend_sym])
    dn = cl.daily_trend_series(cl.mirror(raw[trend_sym]))
    prep, rows = {}, []
    for s, d in raw.items():
        prep[s] = cl.prepare(d, up)
        rows += bc.collect(s, prep[s])
        if s in shorts:
            prep["-" + s] = cl.prepare(cl.mirror(d), dn)
            rows += bc.collect("-" + s, prep["-" + s])
    df = pd.DataFrame(rows)
    # Walk-forward, so the replay is truly unseen: rules are learned on the first half and kept only if
    # they also worked in the third quarter; the last quarter is never used to pick anything.
    end = df.ts.min() + (df.ts.max() - df.ts.min()) * 3 / 4
    df = df[df.ts < end]
    cut = df.ts.min() + (end - df.ts.min()) * 2 / 3
    rules, _ = bc.learn_sides(df, cut)
    return prep, rules, end


def events(prep, rules, cut, is_stock, exit_override=None, min_test=0):
    """Every trade the desk could take: (entry time, exit time, symbol, fund multiple)."""
    out = []
    for sym, d in prep.items():
        if sym not in LEV:
            continue
        lev = LEV[sym]
        H, L, C, O, A = (d[k].to_numpy() for k in ("high", "low", "close", "open", "atr"))
        idx = d.index
        for i in range(max(int(idx.searchsorted(cut)), 150), len(d) - 1):
            t_sig = idx[i] + timedelta(hours=1)                 # the hourly bar has closed
            if not entry_ok(t_sig):
                continue
            sit = cl.situation(d, i)
            for name, e, s in cl.signals_at(d, i):
                if not cl.tradeable(sit, e, s, cl.STOCK_MIN_STOP if is_stock else None):
                    continue
                r = bc.match(rules, name, sym, sit)
                if not r or r["test"]["trades"] < min_test:
                    continue
                ex = {"trail": 0, **(exit_override or r["exit"])}
                rk = e - s
                tgt = e + ex["target"] * rk if ex["target"] else np.inf
                end = min(len(d) - 1, i + cl.hold_hours(ex))
                st, hw, px, j = s, e, C[end], end
                for j in range(i + 1, end + 1):
                    if L[j] <= st:
                        px = min(st, O[j]); break                  # a gap through the stop fills at the open
                    if H[j] >= tgt:
                        px = tgt; break
                    hw = max(hw, H[j])
                    if ex["trail"]:
                        st = cl.trail_stop(st, e, rk, hw, A[j], ex["trail"])
                    elif ex["be"] and H[j] >= e + rk:
                        st = max(st, e)
                t_exit = idx[j]
                if not is_stock and not market_open(t_exit):     # the fund can only be sold at the next open
                    t_exit = next_open(t_exit)
                    k = idx.searchsorted(t_exit.astimezone(idx.tz))
                    if k < len(d):
                        px = O[k]                                 # sold at market at the open
                mult = max(0.0, 1 + lev * (px / e - 1)) * (1 - FUND_COST) / (1 + FUND_COST)
                out.append((t_sig, t_exit, sym, mult))
                break
    return out


def run(ev, start=100.0):
    ev = sorted(ev, key=lambda x: x[0])
    eq = peak = start
    mdd, trades, busy_until, monthly = 0.0, [], None, {}
    for t0, t1, sym, mult in ev:
        if busy_until is not None and t0 < busy_until:
            continue
        before = eq
        eq *= mult
        busy_until = t1
        trades.append((t0, sym, mult))
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
        key = t0.astimezone(ET).strftime("%Y-%m")
        monthly[key] = monthly.get(key, 1.0) * (eq / before)
    wins = sum(m > 1 for _, _, m in trades)
    return dict(trades=len(trades), win=round(100 * wins / len(trades)) if trades else 0,
                total_pct=round((eq / start - 1) * 100, 1), max_drop_pct=round(mdd * 100),
                monthly={k: round((v - 1) * 100, 1) for k, v in sorted(monthly.items())},
                longs=sum(not s.startswith("-") for _, s, _ in trades),
                shorts=sum(s.startswith("-") for _, s, _ in trades))


def show(label, res):
    m = res["monthly"]
    months = len(m) or 1
    print(f"\n## {label}\n{res['trades']} trades ({res['longs']} long, {res['shorts']} short), {res['win']}% wins, "
          f"total {res['total_pct']:+.1f}% over {months} months, worst drop {res['max_drop_pct']}%")
    print("  by month: " + ", ".join(f"{k} {v:+.1f}%" for k, v in m.items()))


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sraw = {s: bs.load(s) for s in cl.STOCK_SIGNALS}
    sprep, srules, scut = learn(sraw, "SPY", cl.STOCK_SIGNALS, cl.STOCK_FEE)
    craw = {c: bc.load(c) for c in bc.DEFAULT}
    cprep, crules, ccut = learn(craw, "BTC", cl.SHORT_COINS, 0.002)
    cut = max(scut, ccut)                                        # unseen by both playbooks
    print(f"# Desk replay: the live setup on unseen data from {str(cut)[:10]}")
    base = events(sprep, srules, cut, True) + events(cprep, crules, cut, False)
    show("Live desk (as configured)", run(base))
    if "--variants" not in sys.argv:
        return
    show("S&P + Nasdaq only (SPYU/TQQQ, SPXS/SQQQ)", run([e for e in base if e[2] in ("SPY", "QQQ", "-SPY", "-QQQ")]))
    show("Longs only", run([e for e in base if not e[2].startswith("-")]))
    show("Stock funds only", run([e for e in base if e[2] in ("SPY", "QQQ", "SMH", "-SPY", "-QQQ", "-SMH")]))
    show("Crypto funds only", run([e for e in base if e[2] not in ("SPY", "QQQ", "SMH", "-SPY", "-QQQ", "-SMH")]))
    for k in (2.0, 3.0):
        ev = events(sprep, srules, cut, True, dict(target=0, be=True, trail=k)) + \
             events(cprep, crules, cut, False, dict(target=0, be=True, trail=k))
        show(f"Every trade exits with a {k}xATR trailing stop", run(ev))
    ev = events(sprep, srules, cut, True, min_test=15) + events(cprep, crules, cut, False, min_test=15)
    show("Only rules with 15+ unseen trades", run(ev))


if __name__ == "__main__":
    main()
