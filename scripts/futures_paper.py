#!/usr/bin/env python3
"""Futures practice account (paper only): learns how the desk's setups do on micro futures before
any real money goes in (the plan: futures once the account reaches $2,000).

Contracts: MES (micro S&P 500, $5 per point) and MNQ (micro Nasdaq-100, $2 per point), signals from
ES=F / NQ=F hourly bars. The same strategy library as the crypto desk learns a futures playbook
daily (learn on 2/3 of 2 years, keep what also worked on the rest). Each run (hourly, on the crypto
timer) it manages open paper positions on the bars and opens new ones when a rule matches.
  - Size: 2% of the account at risk per trade, whole contracts only, and no more contracts than the
    account covers at about $2,500 margin each (skipped if even one doesn't fit).
  - Costs: about $1.50 per contract round trip (Robinhood micro futures commission + exchange fees).
  - Exits: the rule's own target or trailing stop, as on the crypto desk.
  - Phone alerts on every paper trade, and a weekly score on Fridays.
State: futures_paper_state.json. Journal: futures_paper_journal.md. Start: FUTURES_PAPER_START in .env (default $2,000).
"""
import json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crypto_lib as cl
import notify

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "futures_paper_state.json"
JOURNAL = ROOT / "futures_paper_journal.md"
PLAYBOOK = ROOT / "backtests" / "futures_playbook.json"
CONTRACTS = {"MES": ("ES=F", 5.0), "MNQ": ("NQ=F", 2.0)}      # symbol: (data ticker, $ per point)
RISK, MARGIN, FEE_RT, MIN_STOP = 0.02, 2500.0, 1.50, 0.003


def load(ticker, period):
    df = yf.download(ticker, period=period, interval="1h", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
    df.index = df.index.tz_convert("UTC")
    return df


def build_playbook():
    import backtest_crypto as bc
    cl.FEE = 0.0001                                   # futures costs are a few cents per point
    raw = {s: load(t, "730d") for s, (t, _) in CONTRACTS.items()}
    trend = cl.daily_trend_series(raw["MES"])
    prepared, rows = {}, []
    for s, d in raw.items():
        prepared[s] = cl.prepare(d, trend)
        rows += bc.collect(s, prepared[s])
    df = pd.DataFrame(rows)
    cut = df.ts.min() + (df.ts.max() - df.ts.min()) * 2 / 3
    rules, _ = bc.learn(df, cut)
    gate = bc.replay(prepared, rules, cut, False, risk=RISK, min_stop=MIN_STOP)
    PLAYBOOK.parent.mkdir(exist_ok=True)
    PLAYBOOK.write_text(json.dumps(dict(date=datetime.now().date().isoformat(), train_until=str(cut),
                                        replay=gate, rules=rules), indent=2, default=str), encoding="utf-8")
    return rules


def log(text, alert=None):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with JOURNAL.open("a", encoding="utf-8") as f:
        if JOURNAL.stat().st_size == 0:
            f.write("# Futures Practice Journal (paper)\n\n")
        f.write(f"- {stamp} · {text}\n")
    print(stamp, text)
    if alert:
        notify.push(alert, text)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    notify.load_env()
    JOURNAL.touch()
    if STATE.exists():
        st = json.loads(STATE.read_text(encoding="utf-8"))
    else:
        start = float(os.environ.get("FUTURES_PAPER_START") or 2000)
        st = dict(start=start, cash=start, positions=[], closed=[], last_signal={}, week="")
    if not PLAYBOOK.exists() or time.time() - PLAYBOOK.stat().st_mtime > 86400:
        build_playbook()
    rules = json.loads(PLAYBOOK.read_text(encoding="utf-8"))["rules"]
    raw = {s: load(t, "60d") for s, (t, _) in CONTRACTS.items()}
    trend = cl.daily_trend_series(raw["MES"])
    data = {s: cl.prepare(d, trend) for s, d in raw.items()}

    for p in list(st["positions"]):                   # manage open paper positions on completed bars
        d = data[p["sym"]]
        bars = d[d.index > pd.Timestamp(p["checked"])].iloc[:-1]
        exit_px, why = None, None
        for ts, b in bars.iterrows():
            if b.low <= p["stop"]:
                exit_px, why = p["stop"], "stop"
            elif p["target"] and b.high >= p["target"]:
                exit_px, why = p["target"], "target"
            elif p["trail"]:
                p["high"] = max(p["high"], b.high)
                p["stop"] = cl.trail_stop(p["stop"], p["entry"], p["risk"], p["high"], b.atr, p["trail"])
            elif p["be"] and b.high >= p["entry"] + p["risk"]:
                p["stop"] = max(p["stop"], p["entry"])
            if exit_px is None and (ts - pd.Timestamp(p["opened"])).total_seconds() >= cl.hold_hours(p) * 3600:
                exit_px, why = b.close, "time limit"
            p["checked"] = str(ts)
            if exit_px is not None:
                break
        if exit_px is not None:
            pnl = (exit_px - p["entry"]) * p["mult"] * p["n"] - FEE_RT * p["n"]
            st["cash"] += pnl
            st["positions"].remove(p)
            st["closed"].append(dict(p, exit=exit_px, reason=why, pnl=round(pnl, 2)))
            log(f"PAPER SELL {p['n']} {p['sym']} @ {exit_px:,.2f} ({why}) | P&L ${pnl:+,.2f} | {p['strategy']}",
                f"Futures practice {'win' if pnl > 0 else 'loss'}: {p['sym']} ${pnl:+,.2f}")

    for sym, d in data.items():                       # new entries, one position per contract
        if any(p["sym"] == sym for p in st["positions"]):
            continue
        i = len(d) - 2
        ts = str(d.index[i])
        if st["last_signal"].get(sym) == ts:
            continue
        st["last_signal"][sym] = ts
        sit = cl.situation(d, i)
        mult = CONTRACTS[sym][1]
        for name, entry, stop in cl.signals_at(d, i):
            if not cl.tradeable(sit, entry, stop, MIN_STOP):
                continue
            rule = next((r for r in rules if r["strategy"] == name and all(sit.get(k) == v for k, v in r["when"].items())), None)
            if not rule:
                continue
            used = sum(p["n"] for p in st["positions"]) * MARGIN
            n = min(int(RISK * st["cash"] / ((entry - stop) * mult)), int((st["cash"] - used) // MARGIN))
            if n < 1:
                log(f"{sym} {name}: setup skipped, the account can't cover one contract at 2% risk")
                break
            ex = rule["exit"]
            target = entry + ex["target"] * (entry - stop) if ex["target"] else None
            st["positions"].append(dict(sym=sym, n=n, mult=mult, strategy=name, entry=entry, stop=stop,
                                        risk=entry - stop, target=target, be=ex["be"], trail=ex.get("trail", 0),
                                        high=entry, opened=ts, checked=ts))
            log(f"PAPER BUY {n} {sym} @ {entry:,.2f} · stop {stop:,.2f} (risk ${(entry - stop) * mult * n:,.0f}) · "
                f"{cl.exit_label(ex)} · {name}", f"Futures practice buy: {n} {sym}")
            break

    eq = st["cash"] + sum((float(data[p["sym"]].close.iloc[-1]) - p["entry"]) * p["mult"] * p["n"] for p in st["positions"])
    now = datetime.now()
    week = "%d-W%02d" % now.isocalendar()[:2]
    if now.weekday() == 4 and now.hour >= 16 and st["week"] != week:
        st["week"] = week
        n = len(st["closed"])
        wins = sum(1 for c in st["closed"] if c["pnl"] > 0)
        notify.push("Futures practice this week", f"Paper account ${eq:,.2f} ({eq / st['start'] - 1:+.1%} since start), "
                    f"{n} trades, {wins} wins. Practice money only.")
    STATE.write_text(json.dumps(st, indent=2, default=str), encoding="utf-8")
    print(f"{now:%Y-%m-%d %H:%M}  futures practice  equity ${eq:,.2f} | open {len(st['positions'])} | rules {len(rules)}")


if __name__ == "__main__":
    main()
