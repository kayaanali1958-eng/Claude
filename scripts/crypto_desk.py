#!/usr/bin/env python3
"""Crypto paper desk: runs every hour, 24/7, with no Claude usage.

Each run:
  1. Rebuilds the crypto playbook if it is older than 7 days (scripts/backtest_crypto.py, ~3 min).
  2. Manages open paper positions on the latest hourly bars: stop, breakeven, target, 48-hour limit.
  3. Checks every coin for a strategy signal on the last completed hour. A trade is opened only if a
     playbook rule matches: same strategy and every situation condition true right now. It uses the
     rule's exit.
  4. Sizes with 1% risk of the book, no leverage (never spends more cash than the book has),
     fractional coins allowed. Stops opening trades for the day after a 3% daily loss, and pauses
     after a 10% drop from the book's peak (alert sent; delete the pause in crypto_state.json to resume).
  5. Logs to crypto_journal.md and sends phone alerts (NTFY_TOPIC in .env).

Paper only: this script never places real orders.
Settings live in settings.md (Crypto book) and are mirrored in the constants below.
"""
import json, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crypto_lib as cl
import notify

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "crypto_state.json"
JOURNAL = ROOT / "crypto_journal.md"
PLAYBOOK = ROOT / "backtests" / "crypto_playbook.json"
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "LTC"]
START = 100.0
RISK = 0.01
DAILY_LOSS = 0.03
MAX_DRAWDOWN = 0.10
MAX_OPEN = 2


def log(text, alert_title=None):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with JOURNAL.open("a", encoding="utf-8") as f:
        if JOURNAL.stat().st_size == 0:
            f.write("# Crypto Journal (paper)\n\n")
        f.write(f"- {stamp} · {text}\n")
    print(stamp, text)
    if alert_title:
        notify.push(alert_title, text)


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return dict(start=START, cash=START, peak=START, paused=False, positions=[], closed=[],
                day=dict(date="", realized=0.0), last_signal={})


def equity(st, prices):
    return st["cash"] + sum(p["qty"] * prices.get(p["coin"], p["entry"]) for p in st["positions"])


def fetch(coin):
    df = yf.download(f"{coin}-USD", period="60d", interval="1h", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
    df.index = df.index.tz_convert("UTC")
    return df


def refresh_playbook():
    stale = True
    if PLAYBOOK.exists():
        age = time.time() - PLAYBOOK.stat().st_mtime
        stale = age > 7 * 86400
    if stale:
        log("Rebuilding crypto playbook (weekly)…")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "backtest_crypto.py")], cwd=ROOT,
                       stdout=open(ROOT / "logs" / "backtest_crypto.log", "a"), stderr=subprocess.STDOUT)
    return json.loads(PLAYBOOK.read_text()).get("rules", []) if PLAYBOOK.exists() else []


def manage(st, data):
    for p in list(st["positions"]):
        d = data.get(p["coin"])
        if d is None:
            continue
        bars = d[d.index > pd.Timestamp(p["checked"])]
        bars = bars.iloc[:-1]                                   # completed bars only
        exit_px, why = None, None
        for ts, b in bars.iterrows():
            if b.low <= p["stop"]:
                exit_px, why = p["stop"], "stop"
            elif b.high >= p["target"]:
                exit_px, why = p["target"], "target"
            elif p["be"] and b.high >= p["entry"] + p["risk"]:
                p["stop"] = max(p["stop"], p["entry"])
            if exit_px is None and (ts - pd.Timestamp(p["opened"])).total_seconds() >= cl.MAX_HOLD * 3600:
                exit_px, why = b.close, "48h limit"
            p["checked"] = str(ts)
            if exit_px is not None:
                break
        if exit_px is not None:
            proceeds = p["qty"] * exit_px * (1 - cl.FEE)
            pnl = proceeds - p["cost"]
            st["cash"] += proceeds
            st["day"]["realized"] += pnl
            r = pnl / (p["qty"] * p["risk"])
            st["positions"].remove(p)
            st["closed"].append(dict(p, exit=exit_px, reason=why, pnl=round(pnl, 2), R=round(r, 2), closed=p["checked"]))
            log(f"PAPER SELL {p['qty']:.6f} {p['coin']} @ {exit_px:,.4f} ({why}) · P&L ${pnl:+.2f} ({r:+.2f}R) · "
                f"{p['strategy']}", f"Crypto {'win' if pnl > 0 else 'loss'}: {p['coin']} ${pnl:+.2f}")


def open_new(st, data, rules):
    prices = {c: float(d.close.iloc[-1]) for c, d in data.items()}
    eq = equity(st, prices)
    if st["paused"]:
        return
    if st["day"]["realized"] <= -DAILY_LOSS * st["start"]:
        return
    for coin, d in data.items():
        if len(st["positions"]) >= MAX_OPEN or any(p["coin"] == coin for p in st["positions"]):
            continue
        i = len(d) - 2                                          # last completed hour
        ts = str(d.index[i])
        if st["last_signal"].get(coin) == ts:
            continue
        st["last_signal"][coin] = ts
        sit = cl.situation(d, i)
        for name, entry, stop in cl.signals_at(d, i):
            rule = next((r for r in rules if r["strategy"] == name and all(sit.get(k) == v for k, v in r["when"].items())), None)
            if not rule:
                continue
            risk = entry - stop
            qty = min(RISK * eq / risk, st["cash"] / (entry * (1 + cl.FEE)))      # no leverage
            if qty * entry < 1:
                log(f"Skipped {coin} {name}: position under $1")
                continue
            cost = qty * entry * (1 + cl.FEE)
            st["cash"] -= cost
            target = entry + rule["exit"]["target"] * risk
            st["positions"].append(dict(coin=coin, strategy=name, qty=qty, entry=entry, stop=stop, target=target,
                                        risk=risk, be=rule["exit"]["be"], cost=cost, opened=ts, checked=ts,
                                        situation=sit, rule=rule["when"]))
            w = " & ".join(f"{k}={v}" for k, v in rule["when"].items()) or "any"
            log(f"PAPER BUY {qty:.6f} {coin} @ {entry:,.4f} · stop {stop:,.4f} · target {target:,.4f} "
                f"({rule['exit']['target']}R{' +BE' if rule['exit']['be'] else ''}) · {name} · situation {w} · "
                f"rule unseen {rule['test']['avgR']:+.2f}R over {rule['test']['trades']}",
                f"Crypto buy: {coin}")
            break


def main():
    notify.load_env()
    (ROOT / "logs").mkdir(exist_ok=True)
    JOURNAL.touch()
    st = load_state()
    today = datetime.now(timezone.utc).date().isoformat()
    if st["day"]["date"] != today:
        if st["day"]["date"]:
            closed = [c for c in st["closed"] if c["closed"][:10] == st["day"]["date"]]
            log(f"Day {st['day']['date']}: {len(closed)} trades, realized ${st['day']['realized']:+.2f}, cash ${st['cash']:.2f}",
                "Crypto daily recap" if closed else None)
        st["day"] = dict(date=today, realized=0.0)
    rules = refresh_playbook()
    btc = fetch("BTC")
    btc_trend = cl.daily_trend_series(btc)
    data = {}
    for c in COINS:
        try:
            raw = btc if c == "BTC" else fetch(c)
            if len(raw) > 200:
                data[c] = cl.prepare(raw, btc_trend)
        except Exception as e:
            log(f"Data error {c}: {e}")
    manage(st, data)
    open_new(st, data, rules)
    prices = {c: float(d.close.iloc[-1]) for c, d in data.items()}
    eq = equity(st, prices)
    st["peak"] = max(st["peak"], eq)
    if not st["paused"] and eq < st["peak"] * (1 - MAX_DRAWDOWN):
        st["paused"] = True
        log(f"PAUSED: equity ${eq:.2f} is 10% below peak ${st['peak']:.2f}. Review, then set paused=false.", "Crypto desk paused")
    st["equity"] = round(eq, 2)
    STATE.write_text(json.dumps(st, indent=2, default=str))
    print(f"equity ${eq:.2f} · cash ${st['cash']:.2f} · open {len(st['positions'])} · rules {len(rules)}")


if __name__ == "__main__":
    main()
