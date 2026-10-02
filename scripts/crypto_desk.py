#!/usr/bin/env python3
"""Crypto paper desk: runs every hour, 24/7, with no Claude usage.

Each run:
  1. Rebuilds the crypto playbook if it is older than 1 day (scripts/backtest_crypto.py, ~3 min).
  2. Manages open positions on the latest hourly bars: stop, breakeven, target or trailing stop,
     time limit (48 hours, or 7 days for trailing exits that let winners run).
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
import json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crypto_lib as cl
import crypto_live
import notify

ROOT = Path(__file__).resolve().parent.parent

def crypto_mode():
    s = (ROOT / "settings.md").read_text(encoding="utf-8") if (ROOT / "settings.md").exists() else ""
    return "live" if "\nCRYPTO_MODE: live" in "\n" + s else "paper"


LIVE = crypto_mode() == "live"
STATE = ROOT / ("crypto_live_state.json" if LIVE else "crypto_state.json")
JOURNAL = ROOT / ("crypto_live_journal.md" if LIVE else "crypto_journal.md")
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
            f.write(f"# Crypto Journal ({'LIVE' if LIVE else 'paper'})\n\n")
        f.write(f"- {stamp} · {text}\n")
    print(stamp, text)
    if alert_title:
        notify.push(alert_title, text)


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    start = START
    if LIVE:
        cap = (os.environ.get("CRYPTO_LIVE_MAX") or "0").strip().lower()
        start = 0.0 if cap == "all" else float(cap)          # "all": set from the account balance below
    return dict(start=start, cash=start, peak=start, paused=False, positions=[], closed=[],
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
        stale = age > 86400                    # rebuild daily with the newest data
    if stale:
        log("Rebuilding crypto playbook (daily)")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "backtest_crypto.py")], cwd=ROOT,
                       stdout=open(ROOT / "logs" / "backtest_crypto.log", "a", encoding="utf-8"), stderr=subprocess.STDOUT)
    return json.loads(PLAYBOOK.read_text(encoding="utf-8")).get("rules", []) if PLAYBOOK.exists() else []


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
                exit_px, why = p["stop"], "trailing stop" if p.get("trail") and p["stop"] > p["entry"] else "stop"
            elif p["target"] and b.high >= p["target"]:
                exit_px, why = p["target"], "target"
            elif p.get("trail"):
                p["high"] = max(p.get("high", p["entry"]), b.high)
                p["stop"] = cl.trail_stop(p["stop"], p["entry"], p["risk"], p["high"], b.atr, p["trail"])
            elif p["be"] and b.high >= p["entry"] + p["risk"]:
                p["stop"] = max(p["stop"], p["entry"])
            if exit_px is None and (ts - pd.Timestamp(p["opened"])).total_seconds() >= cl.hold_hours(p) * 3600:
                exit_px, why = b.close, "time limit"
            p["checked"] = str(ts)
            if exit_px is not None:
                break
        if LIVE and exit_px is None and raise_live_stop(p):
            res = crypto_live.move_stop(p["coin"], p["qty"], p["stop_order_id"], p["stop"])
            if res.get("ok"):
                p["stop_order_id"], p["live_stop"] = res.get("stop_order_id"), p["stop"]
                log(f"LIVE stop on {p['coin']} raised to {p['stop']:,.4f}")
            else:
                p["stop"] = p.get("live_stop", p["stop"])
                log(f"LIVE stop move FAILED on {p['coin']}: {res.get('error')}", "Crypto LIVE: check stop")
        if LIVE and exit_px is not None:
            res = crypto_live.sell_all(p["coin"], p["stop_order_id"], why)
            if not res.get("ok"):
                log(f"LIVE exit FAILED on {p['coin']} ({why}): {res.get('error')} — will retry next hour",
                    "Crypto LIVE: exit failed, check app")
                continue
            exit_px = float(res.get("avg_price") or exit_px)
        if exit_px is not None:
            proceeds = p["qty"] * exit_px * (1 - cl.FEE)
            pnl = proceeds - p["cost"]
            st["cash"] += proceeds
            st["day"]["realized"] += pnl
            r = pnl / (p["qty"] * p["risk"])
            st["positions"].remove(p)
            st["closed"].append(dict(p, exit=exit_px, reason=why, pnl=round(pnl, 2), R=round(r, 2), closed=p["checked"]))
            log(f"{'LIVE' if LIVE else 'PAPER'} SELL {p['qty']:.6f} {p['coin']} @ {exit_px:,.4f} ({why}) · P&L ${pnl:+.2f} ({r:+.2f}R) · "
                f"{p['strategy']}", f"Crypto {'win' if pnl > 0 else 'loss'}: {p['coin']} ${pnl:+.2f}")


def sync_balance(st, today):
    """CRYPTO_LIVE_MAX=all: once a day, set the book's cash to the account's crypto cash. A deposit or
    withdrawal moves start and peak by the same amount, so it never counts as profit, loss or drawdown."""
    if not LIVE or (os.environ.get("CRYPTO_LIVE_MAX") or "").strip().lower() != "all" or st.get("balance_date") == today:
        return
    res = crypto_live.balance()
    try:
        cash = float(res.get("cash"))
    except (TypeError, ValueError):
        log(f"Balance check failed: {res.get('error') or res}", "Crypto LIVE: balance check failed")
        return
    st["balance_date"] = today
    delta = round(cash - st["cash"], 2)
    if abs(delta) >= 0.01:
        st["cash"] = cash
        st["start"] = round(st["start"] + delta, 2)
        st["peak"] = round(st["peak"] + delta, 2)
        log(f"Book synced to the account: cash ${cash:.2f} ({'added' if delta > 0 else 'removed'} ${abs(delta):.2f})")


def replay_passed():
    """The daily rebuild replays the unseen months as the desk trades them; live needs a profit there."""
    mode = "all_in" if (os.environ.get("CRYPTO_SIZE") or "").strip().lower() == "all" else "risk_1pct"
    try:
        return bool(json.loads(PLAYBOOK.read_text(encoding="utf-8"))["replay"][mode]["profitable"])
    except Exception:
        return False


def raise_live_stop(p):
    """Move the real Robinhood stop up only in steps (breakeven, then every +0.5R), so a trailing
    stop doesn't cost a Claude call every hour. Between steps the desk sells itself if price hits
    the tighter stop it tracks (checked every 5 minutes)."""
    live = p.get("live_stop", p["stop"])
    return p["stop"] > live and (live < p["entry"] <= p["stop"] or p["stop"] - live >= 0.5 * p["risk"])


def open_new(st, data, rules):
    prices = {c: float(d.close.iloc[-1]) for c, d in data.items()}
    eq = equity(st, prices)
    if st["paused"] or (ROOT / "STOP").exists():
        return
    if LIVE and st["start"] <= 0:
        log("CRYPTO_MODE is live but the book has no money: set CRYPTO_LIVE_MAX in .env (a dollar amount or all).",
            "Crypto LIVE not configured")
        return
    if st["day"]["realized"] <= -DAILY_LOSS * st["start"]:
        return
    if LIVE and not replay_passed():
        if st.get("gate_logged") != st["day"]["date"]:
            st["gate_logged"] = st["day"]["date"]
            log("No live trades today: the playbook lost money when the last months were replayed with this "
                "sizing (see backtests/crypto_report_*.md). Checked again after each daily rebuild.",
                "Crypto LIVE: on hold (strategy failed replay)")
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
            if LIVE and (os.environ.get("CRYPTO_SIZE") or "").strip().lower() == "all":
                qty = st["cash"] / (entry * (1 + cl.FEE))     # all-in: the whole book's cash, still no leverage
            if qty * entry < 1:
                log(f"Skipped {coin} {name}: position under $1")
                continue
            cost = qty * entry * (1 + cl.FEE)
            live = {}
            if LIVE:
                res = crypto_live.buy(coin, qty * entry, stop)
                if not res.get("ok") or not res.get("filled_qty"):
                    log(f"LIVE buy FAILED {coin}: {res.get('error')}", "Crypto LIVE: buy failed")
                    continue
                qty, entry = float(res["filled_qty"]), float(res["avg_price"])
                cost = qty * entry * (1 + cl.FEE)
                risk = entry - stop
                live = dict(stop_order_id=res.get("stop_order_id"), buy_order_id=res.get("buy_order_id"), live_stop=stop)
                if risk <= 0:                            # filled at or below the stop: exit right away
                    crypto_live.sell_all(coin, live["stop_order_id"], "filled below stop")
                    log(f"LIVE {coin} filled at {entry} below stop {stop}: exited", "Crypto LIVE: bad fill, exited")
                    continue
            st["cash"] -= cost
            ex = rule["exit"]
            target = entry + ex["target"] * risk if ex["target"] else None
            st["positions"].append(dict(coin=coin, strategy=name, qty=qty, entry=entry, stop=stop, target=target,
                                        risk=risk, be=ex["be"], trail=ex.get("trail", 0), high=entry,
                                        cost=cost, opened=ts, checked=ts,
                                        situation=sit, rule=rule["when"], **live))
            w = " & ".join(f"{k}={v}" for k, v in rule["when"].items()) or "any"
            goal = f"target {target:,.4f}" if target else "no target, stop trails up"
            log(f"{'LIVE' if LIVE else 'PAPER'} BUY {qty:.6f} {coin} @ {entry:,.4f} · stop {stop:,.4f} · {goal} "
                f"({cl.exit_label(ex)}) · {name} · situation {w} · "
                f"rule unseen {rule['test']['avgR']:+.2f}R over {rule['test']['trades']}",
                f"Crypto buy: {coin}")
            break


def live_price(coin):
    """Latest trade price from Coinbase's public ticker (no key needed)."""
    import urllib.request
    try:
        req = urllib.request.Request(f"https://api.exchange.coinbase.com/products/{coin}-USD/ticker",
                                     headers={"User-Agent": "Mozilla/5.0 trading-desk"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return float(json.load(r)["price"])
    except Exception:                                  # backup: latest 1-minute bar from Yahoo
        d = yf.download(f"{coin}-USD", period="1d", interval="1m", progress=False, auto_adjust=False)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
        return float(d["Close"].dropna().iloc[-1])


def quick_manage(st):
    """Between hourly runs: manage open positions on the live price (target, breakeven, stop, 48h)."""
    for p in list(st["positions"]):
        try:
            px = live_price(p["coin"])
        except Exception as e:
            log(f"Price error {p['coin']}: {e}")
            continue
        why = None
        if px <= p["stop"]:
            why = "trailing stop" if p.get("trail") and p["stop"] > p["entry"] else "stop"
        elif p["target"] and px >= p["target"]:
            why = "target"
        elif (datetime.now(timezone.utc) - pd.Timestamp(p["opened"]).to_pydatetime()).total_seconds() >= cl.hold_hours(p) * 3600:
            why = "time limit"
        if why is None:
            if p.get("trail"):
                p["high"] = max(p.get("high", p["entry"]), px)      # trail itself moves on hourly closes
            if p["be"] and px >= p["entry"] + p["risk"] and p["stop"] < p["entry"]:
                p["stop"] = p["entry"]
                if LIVE:
                    res = crypto_live.move_stop(p["coin"], p["qty"], p["stop_order_id"], p["stop"])
                    if res.get("ok"):
                        p["stop_order_id"], p["live_stop"] = res.get("stop_order_id"), p["stop"]
                        log(f"LIVE stop on {p['coin']} raised to breakeven {p['stop']:,.4f}")
                    else:
                        p["stop"] = p.get("live_stop", p["stop"])
                        log(f"LIVE stop move FAILED on {p['coin']}: {res.get('error')}", "Crypto LIVE: check stop")
                else:
                    log(f"PAPER stop on {p['coin']} raised to breakeven {p['stop']:,.4f}")
            continue
        exit_px = p["stop"] if why.endswith("stop") else px
        if LIVE:
            res = crypto_live.sell_all(p["coin"], p["stop_order_id"], why)
            if not res.get("ok"):
                log(f"LIVE exit FAILED on {p['coin']} ({why}): {res.get('error')} - will retry", "Crypto LIVE: exit failed, check app")
                continue
            exit_px = float(res.get("avg_price") or exit_px)
        proceeds = p["qty"] * exit_px * (1 - cl.FEE)
        pnl = proceeds - p["cost"]
        st["cash"] += proceeds
        st["day"]["realized"] += pnl
        r = pnl / (p["qty"] * p["risk"])
        st["positions"].remove(p)
        now = datetime.now(timezone.utc).isoformat()
        st["closed"].append(dict(p, exit=exit_px, reason=why, pnl=round(pnl, 2), R=round(r, 2), closed=now))
        log(f"{'LIVE' if LIVE else 'PAPER'} SELL {p['qty']:.6f} {p['coin']} @ {exit_px:,.4f} ({why}) | P&L ${pnl:+.2f} ({r:+.2f}R) | {p['strategy']}",
            f"Crypto {'win' if pnl > 0 else 'loss'}: {p['coin']} ${pnl:+.2f}")


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
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
    hourly = "--full" in sys.argv or datetime.now().minute < 5
    if not hourly:                                   # 5-minute check: open positions only
        if st["positions"]:
            quick_manage(st)
        STATE.write_text(json.dumps(st, indent=2, default=str), encoding="utf-8")
        if st["positions"]:
            print(f"{datetime.now().strftime('%Y-%m-%d %H:%M')}  {'LIVE' if LIVE else 'paper'}  managed {len(st['positions'])} open position(s)")
        return
    sync_balance(st, today)
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
    STATE.write_text(json.dumps(st, indent=2, default=str), encoding="utf-8")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M')}  {'LIVE' if LIVE else 'paper'}  equity ${eq:.2f} | cash ${st['cash']:.2f} | open {len(st['positions'])} | rules {len(rules)}" + ("  PAUSED" if st["paused"] else ""))


if __name__ == "__main__":
    main()
