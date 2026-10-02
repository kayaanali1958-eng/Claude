#!/usr/bin/env python3
"""Crypto playbook engine: learns which strategy works in which crypto market situation.

Same method as scripts/backtest.py, on 2 years of hourly bars:
  - every strategy in scripts/crypto_lib.py, long only (Robinhood crypto can't be shorted)
  - every situation (trend, volatility, session, weekend, BTC trend), one or two conditions at a time
  - every exit (1.5R / 2R / 3R target, with or without breakeven at +1R), max hold 48 hours
  - learned on the first 2/3 of the time, kept only if it also made money on the last 1/3
  - 0.2% cost per side (Robinhood's crypto spread)

Usage:  python scripts/backtest_crypto.py                  (BTC ETH SOL XRP DOGE AVAX LINK LTC)
        python scripts/backtest_crypto.py BTC ETH          (any Robinhood coins)
Writes: backtests/crypto_playbook.json, backtests/crypto_report_<date>.md
"""
import itertools, json, sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crypto_lib as cl

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "backtests"
DEFAULT = ["BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "LTC"]
FEATURES = ["trend", "vol", "session", "weekend", "btc"]
MIN_TRAIN, MIN_TEST = 20, 8
TRAIN_EDGE, TEST_EDGE = 0.15, 0.05


def load(coin):
    df = yf.download(f"{coin}-USD", period="730d", interval="1h", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
    df.index = df.index.tz_convert("UTC")
    return df


def collect(coin, d):
    rows, busy_until = [], {}
    for i in range(150, len(d) - 1):
        for name, entry, stop in cl.signals_at(d, i):
            if busy_until.get(name, -1) >= i:          # one open trade per strategy per coin
                continue
            res = cl.simulate(d, i, entry, stop)
            busy_until[name] = i + cl.MAX_HOLD
            rows.append(dict(coin=coin, ts=d.index[i], strategy=name, **cl.situation(d, i),
                             **{f"R_{t}_{b}": r for (t, b), r in res.items()}))
    return rows


def learn(df, cut):
    train, test = df[df.ts < cut], df[df.ts >= cut]
    singles = [((f, v),) for f in FEATURES for v in sorted(df[f].unique())]
    conds = [()] + singles + [(a[0], b[0]) for a, b in itertools.combinations(singles, 2) if a[0][0] != b[0][0]]
    rules, tested = {}, 0
    for strat, g_tr in train.groupby("strategy"):
        g_te = test[test.strategy == strat]
        for cond in conds:
            m_tr = np.ones(len(g_tr), bool)
            m_te = np.ones(len(g_te), bool)
            for f, v in cond:
                m_tr &= (g_tr[f] == v).to_numpy()
                m_te &= (g_te[f] == v).to_numpy()
            if m_tr.sum() < MIN_TRAIN:
                continue
            for ex in cl.EXITS:
                col = f"R_{ex['target']}_{ex['be']}"
                tested += 1
                tr = g_tr[col].to_numpy()[m_tr]
                te = g_te[col].to_numpy()[m_te]
                if tr.mean() < TRAIN_EDGE or len(te) < MIN_TEST or te.mean() <= TEST_EDGE:
                    continue
                r = dict(strategy=strat, when=dict(cond), exit=ex,
                         train=dict(trades=int(len(tr)), avgR=round(float(tr.mean()), 2), win=round(float((tr > 0).mean() * 100))),
                         test=dict(trades=int(len(te)), avgR=round(float(te.mean()), 2), win=round(float((te > 0).mean() * 100))))
                k = (strat, tuple(sorted(r["when"].items())))
                if k not in rules or r["test"]["avgR"] > rules[k]["test"]["avgR"]:
                    rules[k] = r
    simple = {}
    for r in rules.values():
        k = (r["strategy"], r["exit"]["target"], r["exit"]["be"], r["train"]["trades"], r["test"]["trades"], r["test"]["avgR"])
        if k not in simple or len(r["when"]) < len(simple[k]["when"]):
            simple[k] = r
    ranked = sorted(simple.values(), key=lambda r: r["test"]["avgR"] * np.sqrt(r["test"]["trades"]), reverse=True)
    return ranked, tested


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    coins = [c.upper().replace("-USD", "") for c in sys.argv[1:]] or DEFAULT
    raw = {c: load(c) for c in sorted(set(coins) | {"BTC"})}
    btc_trend = cl.daily_trend_series(raw["BTC"])
    rows = []
    for c in coins:
        if raw[c].empty:
            print(f"{c}: no data"); continue
        d = cl.prepare(raw[c], btc_trend)
        r = collect(c, d)
        print(f"{c}: {len(d)} hourly bars, {len(r)} signals")
        rows += r
    df = pd.DataFrame(rows)
    cut = df.ts.min() + (df.ts.max() - df.ts.min()) * 2 / 3
    rules, tested = learn(df, cut)
    base = df.groupby("strategy")["R_2.0_False"].agg(["count", "mean"]).round(2).rename(
        columns={"count": "signals", "mean": "avg R (raw, 2R)"}).reset_index()

    OUT.mkdir(exist_ok=True)
    stamp = date.today().isoformat()
    (OUT / "crypto_playbook.json").write_text(json.dumps(dict(
        date=stamp, coins=coins, train_until=str(cut), combinations_tested=tested,
        strategies_in_library=list(cl.STRATEGIES), fee_per_side=cl.FEE, max_hold_hours=cl.MAX_HOLD, rules=rules),
        indent=2, default=str), encoding="utf-8")
    lines = [f"# Crypto playbook {stamp}", "",
             f"{len(cl.STRATEGIES)} strategies × situations × exits = {tested} combinations on {', '.join(coins)} "
             f"({len(df)} signals, 2 years of hourly bars, {cl.FEE * 100:.1f}% cost per side). "
             f"Learned before {str(cut)[:10]}, checked after.", "",
             f"## Playbook: {len(rules)} rules with an edge on unseen data", ""]
    if rules:
        lines += ["| # | situation | strategy | exit | learned (trades, avg R) | unseen (trades, avg R, win%) |",
                  "|---|---|---|---|---|---|"]
        for n, r in enumerate(rules[:30], 1):
            w = " & ".join(f"{k}={v}" for k, v in r["when"].items()) or "any situation"
            ex = f"{r['exit']['target']}R" + (" +BE" if r["exit"]["be"] else "")
            lines.append(f"| {n} | {w} | {r['strategy']} | {ex} | {r['train']['trades']}, {r['train']['avgR']:+.2f} | "
                         f"{r['test']['trades']}, {r['test']['avgR']:+.2f}, {r['test']['win']}% |")
    else:
        lines.append("_Nothing held up on unseen data. The crypto desk will not open trades._")
    lines += ["", "## Every strategy, no filters (baseline)", "", base.to_markdown(index=False), "",
              f"Caution: {tested} combinations tested; some pass by luck. Trust rules with many unseen trades that keep "
              "passing every week."]
    (OUT / f"crypto_report_{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:22]))


if __name__ == "__main__":
    main()
