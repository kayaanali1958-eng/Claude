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
from zoneinfo import ZoneInfo

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
COINS = cl.TRADE_COINS
START = 100.0
RISK = 0.01
DAILY_LOSS = 0.03
MAX_OPEN = 2
# CRYPTO_LEVERAGE=2 in .env: during US market hours, a signal on these coins buys the 2x fund instead
# (whole shares, real stop order). Exits follow the coin's signals; a fund can only be sold while the
# stock market is open, so an exit at night or on a weekend is carried out at the next open.
# Leveraged funds per signal: 2x crypto funds, 4x S&P (SPYU), and 3x Nasdaq / chip funds (TQQQ, SOXL).
LEV_FUNDS = {"BTC": ("BITX", 2), "ETH": ("ETHU", 2), "XRP": ("XXRP", 2),
             "SPY": ("SPYU", 4), "QQQ": ("TQQQ", 3), "SMH": ("SOXL", 3)}     # SPYU: 4x S&P 500 (owner's choice)
STOCKS = cl.STOCK_SIGNALS
STOCK_PLAYBOOK = ROOT / "backtests" / "stock_playbook.json"
FUND_COST = 0.001
ET = ZoneInfo("America/New_York")


def leverage_on():
    return (os.environ.get("CRYPTO_LEVERAGE") or "").strip() == "2"


def market_open(now=None, entry=False):
    t = (now or datetime.now(ET)).astimezone(ET)
    if t.weekday() >= 5:
        return False
    m = t.hour * 60 + t.minute
    return 600 <= m < 900 if entry else 570 <= m < 960      # entries 10:00-15:00, exits 9:30-16:00


def fund_price(symbol):
    df = yf.download(symbol, period="1d", interval="1m", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return float(df["Close"].dropna().iloc[-1])


def close_fund(st, p, why):
    """Sell a 2x-fund position. Outside market hours, mark it and sell at the next open."""
    today = datetime.now(ET).date()
    same_day = pd.Timestamp(p["opened"]).tz_convert(ET).date() == today
    recent = [d for d in st.setdefault("day_trades", []) if (today - datetime.fromisoformat(d).date()).days < 7]
    st["day_trades"] = recent
    if same_day and len(recent) >= 3:                # pattern day trader rule: max 3 in 5 business days
        if p.get("exit_pending") != why:
            p["exit_pending"] = why
            log(f"{p['fund']} exit signal ({why}) on the day it was bought, but 3 day trades are already used "
                f"this week: holding to the next open (the stop order on Robinhood still protects it)",
                "Crypto LIVE: day-trade limit, holding overnight")
        return False
    if not market_open():
        if p.get("exit_pending") != why:
            p["exit_pending"] = why
            log(f"{p['fund']} exit signal ({why}) while the stock market is closed: selling at the next open")
        return False
    if LIVE:
        res = crypto_live.sell_fund(p["fund"], p.get("fund_stop_order_id"), why)
        if not res.get("ok"):
            log(f"LIVE exit FAILED on {p['fund']} ({why}): {res.get('error')} - will retry", "Crypto LIVE: exit failed, check app")
            return False
        px = float(res.get("avg_price") or fund_price(p["fund"]))
    else:
        px = fund_price(p["fund"])
    if same_day:
        st["day_trades"].append(today.isoformat())
    proceeds = p["fund_qty"] * px * (1 - FUND_COST)
    pnl = proceeds - p["cost"]
    st["cash"] += proceeds
    st["day"]["realized"] += pnl
    r = pnl / (p["cost"] * p.get("lev", 2) * p["risk"] / p["entry"])
    st["positions"].remove(p)
    st["sync_now"] = True
    st["closed"].append(dict(p, exit=px, reason=why, pnl=round(pnl, 2), R=round(r, 2),
                             closed=datetime.now(timezone.utc).isoformat()))
    log(f"{'LIVE' if LIVE else 'PAPER'} SELL {p['fund_qty']} {p['fund']} ({p.get('lev', 2)}x {p['coin']}) @ {px:,.2f} ({why}) | "
        f"P&L ${pnl:+.2f} ({r:+.2f}R) | {p['strategy']}", f"Crypto {'win' if pnl > 0 else 'loss'}: {p['fund']} ${pnl:+.2f}")
    return True


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
    def value(p):
        px = prices.get(p["coin"], p["entry"])
        if p.get("fund"):                                   # a leveraged fund moves lev x the signal's move
            return p["fund_qty"] * p["fund_entry"] * max(0.0, 1 + p.get("lev", 2) * (px / p["entry"] - 1))
        return p["qty"] * px
    return st["cash"] + sum(value(p) for p in st["positions"])


def fetch(coin):
    df = yf.download(f"{coin}-USD", period="60d", interval="1h", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
    df.index = df.index.tz_convert("UTC")
    return df


def fetch_stock(sym):
    df = yf.download(sym, period="60d", interval="1h", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
    df.index = df.index.tz_convert("UTC")
    return df


def refresh_stock_playbook():
    """Stock rules for the 3x funds, rebuilt daily; empty (no stock trades) unless its replay passed."""
    if not STOCK_PLAYBOOK.exists() or time.time() - STOCK_PLAYBOOK.stat().st_mtime > 86400:
        log("Rebuilding stock playbook (daily)")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "backtest_stocks.py")], cwd=ROOT,
                       stdout=open(ROOT / "logs" / "backtest_stocks.log", "a", encoding="utf-8"), stderr=subprocess.STDOUT)
    try:
        pb = json.loads(STOCK_PLAYBOOK.read_text(encoding="utf-8"))
        if (os.environ.get("CRYPTO_GATE") or "").strip().lower() != "off" and pb["replay"][size_mode()]["profitable"] is not True:
            return []
        return pb.get("rules", [])
    except Exception:
        return []


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
        if d is None or p.get("exit_pending"):
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
        if exit_px is not None and p.get("fund"):
            close_fund(st, p, why)
            continue
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
            st["sync_now"] = True
            st["closed"].append(dict(p, exit=exit_px, reason=why, pnl=round(pnl, 2), R=round(r, 2), closed=p["checked"]))
            log(f"{'LIVE' if LIVE else 'PAPER'} SELL {p['qty']:.6f} {p['coin']} @ {exit_px:,.4f} ({why}) · P&L ${pnl:+.2f} ({r:+.2f}R) · "
                f"{p['strategy']}", f"Crypto {'win' if pnl > 0 else 'loss'}: {p['coin']} ${pnl:+.2f}")


GOAL = 2000


def milestones(st, total):
    """The plan: grow the account to $1,000 here, then split it into long-term funds (ai_portfolio.py)."""
    try:
        total = float(total)
    except (TypeError, ValueError):
        return
    st["account_total"] = round(total, 2)
    hit = st.setdefault("milestones_hit", [])
    for m in (100, 250, 500, 1000, 1500, GOAL):
        if total >= m and m not in hit:
            hit.append(m)
            if m == GOAL:
                log(f"GOAL: the account is worth ${total:,.2f}. Phase 2: time to split it into long-term funds "
                    "(see ai_portfolio_journal.md for the plan it has been practising). Next: connect the AI stocks "
                    "and funds to real orders, then options.", "Account hit $2,000!")
            else:
                log(f"Milestone: the account is worth ${total:,.2f} ({total / GOAL:.0%} of the ${GOAL:,} goal).",
                    f"Account passed ${m}")


def sync_balance(st, today, force=False):
    """CRYPTO_LIVE_MAX=all: every 4 hours, right before every buy and right after every sell (or now with
    --sync), set the book's cash to the account's crypto
    cash. A deposit or withdrawal moves start and peak by the same amount, so it never counts as profit,
    loss or drawdown."""
    if not LIVE or (os.environ.get("CRYPTO_LIVE_MAX") or "").strip().lower() != "all":
        return
    last = st.get("balance_checked")
    if not force and "--sync" not in sys.argv and last and time.time() - last < 4 * 3600:
        return
    res = crypto_live.balance()
    try:
        cash = float(res.get("cash"))
    except (TypeError, ValueError):
        log(f"Balance check failed: {res.get('error') or res}", "Crypto LIVE: balance check failed")
        return
    st["balance_date"], st["balance_checked"] = today, time.time()
    milestones(st, res.get("total"))
    held = res.get("holdings")
    if isinstance(held, dict):                       # a position sold by hand in the app: forget it here too
        for p in list(st["positions"]):
            sym = p.get("fund") or p["coin"]
            try:
                have = float(held.get(sym, 0) or 0)
            except (TypeError, ValueError):
                continue
            need = p["fund_qty"] if p.get("fund") else p["qty"]
            if have < need * 0.5:
                st["positions"].remove(p)
                st["cash"] += p["cost"]          # its money is back in cash: not a deposit, not a loss
                st["closed"].append(dict(p, exit=None, reason="closed outside the desk", pnl=None, R=None,
                                         closed=datetime.now(timezone.utc).isoformat()))
                log(f"{sym} is no longer in the account (sold in the app?): removed from the desk's open trades",
                    f"Crypto desk: {sym} closed outside the desk")
    delta = round(cash - st["cash"], 2)
    if abs(delta) >= 0.01:
        st["cash"] = cash
        st["start"] = round(st["start"] + delta, 2)
        st["peak"] = round(st["peak"] + delta, 2)
        log(f"Book synced to the account: cash ${cash:.2f} ({'added' if delta > 0 else 'removed'} ${abs(delta):.2f})",
            "Crypto desk: new money detected" if delta >= 5 else None)


def risk_pct():
    """CRYPTO_SIZE in .env: empty = 1% risk per trade, "5%" = 5% risk, all = all the cash (None)."""
    if not LIVE:
        return RISK
    v = (os.environ.get("CRYPTO_SIZE") or "").strip().lower().rstrip("%")
    if v == "all":
        return None
    try:
        return min(max(float(v) / 100, 0.005), 0.10)
    except ValueError:
        return RISK


def limits():
    """Safety limits that fit the size. Above 2% risk (or all-in) it holds one trade at a time, which
    made more in the replay; the daily stop allows about 3 losses and the pause sits just past the
    worst drop the replay saw (41%), so it only stops the desk when something is really wrong."""
    pct = risk_pct()
    if pct is None or pct > 0.02:
        return dict(max_open=1, daily=0.15, drawdown=0.45, gate="all_in")
    if pct > 0.01:
        return dict(max_open=MAX_OPEN, daily=0.06, drawdown=0.35, gate="risk_2pct")
    return dict(max_open=MAX_OPEN, daily=DAILY_LOSS, drawdown=0.25, gate="risk_1pct")


def size_mode():
    return limits()["gate"]


def replay_passed():
    """The daily rebuild replays the unseen months as the desk trades them; live needs a profit there."""
    if (os.environ.get("CRYPTO_GATE") or "").strip().lower() == "off":
        return True                      # the owner chose to trade even though the replay lost money
    try:
        return json.loads(PLAYBOOK.read_text(encoding="utf-8"))["replay"][size_mode()]["profitable"] is True
    except Exception:
        return False


def raise_live_stop(p):
    """Move the real Robinhood stop up only in steps (breakeven, then every +0.5R), so a trailing
    stop doesn't cost a Claude call every hour. Between steps the desk sells itself if price hits
    the tighter stop it tracks (checked every 5 minutes)."""
    if p.get("fund"):
        return False                     # the fund keeps its first stop; the desk sells it on the trail
    live = p.get("live_stop", p["stop"])
    return p["stop"] > live and (live < p["entry"] <= p["stop"] or p["stop"] - live >= 0.5 * p["risk"])


def enter(st, coin, name, entry, stop, rule, sit, ts, eq):
    """Size and place one entry. Returns "done", "skip", or "wait" (Robinhood's spread is too wide now)."""
    if LIVE and time.time() - st.get("balance_checked", 0) > 300:       # fresh balance before every buy
        sync_balance(st, datetime.now(timezone.utc).date().isoformat(), force=True)
        eq = st["cash"] + sum(p["cost"] for p in st["positions"])
    risk = entry - stop
    pct = risk_pct()
    mode = "all_in" if pct is None else "risk"
    if mode == "all_in":
        qty = st["cash"] / (entry * (1 + cl.FEE))     # all-in: the whole book's cash, still no leverage
    else:
        qty = min(pct * eq / risk, st["cash"] / (entry * (1 + cl.FEE)))      # no leverage
    if qty * entry < 1:
        log(f"Skipped {coin} {name}: position under $1")
        return "skip"
    cost = qty * entry * (1 + cl.FEE)
    live = {}
    fund, lev = LEV_FUNDS.get(coin, (None, 1)) if leverage_on() and market_open(entry=True) else (None, 1)
    if coin in STOCKS and not fund:
        return "skip"                     # stock signals are only ever traded through a leveraged fund
    if fund:
        try:
            fpx = fund_price(fund)
        except Exception as e:
            log(f"No price for {fund}: {e}; trading the coin instead")
            fund = None
    if fund:
        if mode == "all_in":
            budget = st["cash"]
        else:
            budget = cost / lev                                # lev x fund: 1/lev of the size keeps the same risk
        shares = int(budget / (fpx * (1 + FUND_COST)))         # whole shares so a stop order is allowed
        fstop = round(fpx * (1 - lev * (entry - stop) / entry), 2)
        if shares < 1:
            if coin in STOCKS:
                log(f"{fund} costs ${fpx:,.2f}: under one share with this cash, skipped")
                return "skip"
            log(f"{fund} costs ${fpx:,.2f}: under one share with this cash, trading {coin} instead")
            fund = None
    if fund:
        if LIVE:
            res = crypto_live.buy_fund(fund, shares, fstop, fpx)
            if not res.get("ok") or not res.get("filled_qty"):
                if "spread too wide" in str(res.get("error")):
                    return "wait"
                log(f"LIVE buy FAILED {fund}: {res.get('error')}", "Crypto LIVE: buy failed")
                return "skip"
            shares, fpx = int(float(res["filled_qty"])), float(res["avg_price"])
            live = dict(fund_stop_order_id=res.get("stop_order_id"), buy_order_id=res.get("buy_order_id"))
        fcost = shares * fpx * (1 + FUND_COST)
        st["cash"] -= fcost
        ex = rule["exit"]
        target = entry + ex["target"] * risk if ex["target"] else None
        st["positions"].append(dict(coin=coin, fund=fund, lev=lev, fund_qty=shares, fund_entry=fpx, fund_stop=fstop,
                                    strategy=name, qty=qty, entry=entry, stop=stop, target=target, risk=risk,
                                    be=ex["be"], trail=ex.get("trail", 0), high=entry, cost=fcost,
                                    opened=ts, checked=ts, situation=sit, rule=rule["when"], **live))
        log(f"{'LIVE' if LIVE else 'PAPER'} BUY {shares} {fund} ({lev}x {coin}) @ {fpx:,.2f} = ${fcost:,.2f} · "
            f"stop {fstop:,.2f} · {cl.exit_label(ex)} on {coin} · {name}", f"Desk buy: {fund} ({lev}x {coin})")
        return "done"
    if (os.environ.get("CRYPTO_FUNDS_ONLY") or "").strip() == "1":
        return "skip"                  # coins cost ~0.9% per side on Robinhood: trade only the 2x funds
    if LIVE:
        res = crypto_live.buy(coin, qty * entry, stop, live_price(coin))
        if not res.get("ok") or not res.get("filled_qty"):
            if "spread too wide" in str(res.get("error")):
                return "wait"
            quiet = "not filled" in str(res.get("error"))
            log(f"LIVE buy skipped {coin}: {res.get('error')}", None if quiet else "Crypto LIVE: buy failed")
            return "skip"
        qty, entry = float(res["filled_qty"]), float(res["avg_price"])
        cost = qty * entry * (1 + cl.FEE)
        risk = entry - stop
        live = dict(stop_order_id=res.get("stop_order_id"), buy_order_id=res.get("buy_order_id"), live_stop=stop)
        if risk <= 0:                            # filled at or below the stop: exit right away
            crypto_live.sell_all(coin, live["stop_order_id"], "filled below stop")
            log(f"LIVE {coin} filled at {entry} below stop {stop}: exited", "Crypto LIVE: bad fill, exited")
            return "skip"
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
    return "done"


WAIT_MIN, WAIT_EVERY = 60, 10          # spread too wide: retry every 10 minutes for up to an hour


def retry_waiting(st):
    """Entries put on hold because Robinhood's spread was too wide: retry while the setup still holds."""
    if news_blackout():
        return
    for coin, w in list(st.get("waiting", {}).items()):
        now = time.time()
        if now > w["until"] or any(p["coin"] == coin for p in st["positions"]) or len(st["positions"]) >= limits()["max_open"]:
            log(f"{coin}: spread stayed too wide for {WAIT_MIN} minutes, setup dropped")
            del st["waiting"][coin]
            continue
        if now < w["next"]:
            continue
        try:
            px = live_price(coin)
        except Exception:
            continue
        if px <= w["stop"] or px > w["entry"] * 1.01:          # the setup is gone: below the stop or ran away
            log(f"{coin}: price moved to {px:,.4f} while waiting for a fair spread, setup dropped")
            del st["waiting"][coin]
            continue
        eq = st["cash"] + sum(p["cost"] for p in st["positions"])
        res = enter(st, coin, w["name"], w["entry"], w["stop"], w["rule"], w["sit"], w["ts"], eq)
        if res == "wait":
            w["next"] = now + WAIT_EVERY * 60
        else:
            del st["waiting"][coin]


CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"   # free weekly economic calendar
CALENDAR = ROOT / "news" / "econ_calendar.json"
BLACKOUT_MIN = 30


def news_blackout():
    """The high-impact US event (Fed, CPI, jobs report...) within 30 minutes of now, or None.
    New entries wait it out; open trades keep their stops. Calendar refreshed every 6 hours."""
    import urllib.request
    CALENDAR.parent.mkdir(exist_ok=True)
    if not CALENDAR.exists() or time.time() - CALENDAR.stat().st_mtime > 6 * 3600:
        try:
            req = urllib.request.Request(CALENDAR_URL, headers={"User-Agent": "Mozilla/5.0"})
            CALENDAR.write_text(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"), encoding="utf-8")
        except Exception as e:
            print(f"economic calendar unavailable: {e}")
    try:
        events = json.loads(CALENDAR.read_text(encoding="utf-8"))
    except Exception:
        return None
    now = datetime.now(timezone.utc)
    for e in events:
        if e.get("country") != "USD" or e.get("impact") != "High":
            continue
        try:
            t = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        except Exception:
            continue
        if abs((now - t).total_seconds()) <= BLACKOUT_MIN * 60:
            return f"{e.get('title', 'event')} at {t.astimezone(ET):%H:%M} ET"
    return None


def open_new(st, data, rules, stock_rules=None):
    prices = {c: float(d.close.iloc[-1]) for c, d in data.items()}
    eq = equity(st, prices)
    if st["paused"] or (ROOT / "STOP").exists():
        return
    if LIVE and st["start"] <= 0:
        log("CRYPTO_MODE is live but the book has no money: set CRYPTO_LIVE_MAX in .env (a dollar amount or all).",
            "Crypto LIVE not configured")
        return
    lim = limits()
    if st["day"]["realized"] <= -lim["daily"] * st["start"]:
        return
    event = news_blackout()
    if event:
        if st.get("blackout_logged") != event:
            st["blackout_logged"] = event
            log(f"News blackout: no new trades within {BLACKOUT_MIN} min of {event}")
        return
    if LIVE and not replay_passed() and not stock_rules:
        if st.get("gate_logged") != st["day"]["date"]:
            st["gate_logged"] = st["day"]["date"]
            log("No live trades today: the playbook lost money when the last months were replayed with this "
                "sizing (see backtests/crypto_report_*.md). Checked again after each daily rebuild.",
                "Crypto LIVE: on hold (strategy failed replay)")
        return
    for coin, d in data.items():
        if len(st["positions"]) >= lim["max_open"] or any(p["coin"] == coin for p in st["positions"]) \
                or coin in st.get("waiting", {}):
            continue
        i = len(d) - 2                                          # last completed hour
        ts = str(d.index[i])
        if st["last_signal"].get(coin) == ts:
            continue
        st["last_signal"][coin] = ts
        is_stock = coin in STOCKS
        if is_stock and not (stock_rules and market_open(entry=True)):
            continue
        if not is_stock and LIVE and not replay_passed():
            continue
        sit = cl.situation(d, i)
        book = stock_rules if is_stock else rules
        for name, entry, stop in cl.signals_at(d, i):
            if not cl.tradeable(sit, entry, stop, cl.STOCK_MIN_STOP if is_stock else None):
                continue
            rule = next((r for r in book if r["strategy"] == name and all(sit.get(k) == v for k, v in r["when"].items())), None)
            if not rule:
                continue
            res = enter(st, coin, name, entry, stop, rule, sit, ts, eq)
            if res == "wait":
                st.setdefault("waiting", {})[coin] = dict(name=name, entry=entry, stop=stop, rule=rule, sit=sit, ts=ts,
                                                          until=time.time() + WAIT_MIN * 60,
                                                          next=time.time() + WAIT_EVERY * 60)
                log(f"{coin} {name}: Robinhood's spread is too wide right now, waiting for a fair price "
                    f"(retry every {WAIT_EVERY} min for up to {WAIT_MIN} min)")
                break
            if res == "done":
                break


def save_15m(coins):
    """Keep a growing history of 15-minute candles (Coinbase, no key) in backtests/m15/, so faster
    entries can be tested properly once there is enough of it. Never used for trading."""
    import urllib.request
    out = ROOT / "backtests" / "m15"
    out.mkdir(parents=True, exist_ok=True)
    for coin in coins:
        try:
            req = urllib.request.Request(f"https://api.exchange.coinbase.com/products/{coin}-USD/candles?granularity=900",
                                         headers={"User-Agent": "Mozilla/5.0"})
            rows = json.loads(urllib.request.urlopen(req, timeout=15).read())
            new = pd.DataFrame(rows, columns=["t", "low", "high", "open", "close", "volume"])
            f = out / f"{coin}.csv"
            old = pd.read_csv(f) if f.exists() else new.iloc[:0]
            pd.concat([old, new]).drop_duplicates("t").sort_values("t").to_csv(f, index=False)
        except Exception as e:
            print(f"15m save {coin}: {e}")


def live_price(coin):
    """Latest trade price from Coinbase's public ticker (no key needed); stocks from Yahoo 1-minute bars."""
    import urllib.request
    if coin in STOCKS:
        return fund_price(coin)
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
        if p.get("exit_pending"):
            close_fund(st, p, p["exit_pending"])
            continue
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
                if LIVE and not p.get("fund"):
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
        if p.get("fund"):
            close_fund(st, p, why)
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
        st["sync_now"] = True
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
    hourly = "--full" in sys.argv or "--sync" in sys.argv or datetime.now().minute < 5
    if not hourly:                                   # 5-minute check: open positions and waiting entries
        if st["positions"]:
            quick_manage(st)
        if st.get("waiting"):
            retry_waiting(st)
        if st.pop("sync_now", False):                # fresh balance right after a sell
            sync_balance(st, today, force=True)
        STATE.write_text(json.dumps(st, indent=2, default=str), encoding="utf-8")
        if st["positions"]:
            print(f"{datetime.now().strftime('%Y-%m-%d %H:%M')}  {'LIVE' if LIVE else 'paper'}  managed {len(st['positions'])} open position(s)")
        return
    sync_balance(st, today)
    # The AI-infrastructure paper portfolio rides on this timer; it acts once per weekday after 4 PM ET.
    subprocess.run([sys.executable, str(ROOT / "scripts" / "ai_portfolio.py")], cwd=ROOT,
                   stdout=open(ROOT / "logs" / "ai_portfolio.log", "a", encoding="utf-8"), stderr=subprocess.STDOUT)
    # Futures practice account (paper only, for the plan after $2,000), also hourly on this timer.
    subprocess.run([sys.executable, str(ROOT / "scripts" / "futures_paper.py")], cwd=ROOT,
                   stdout=open(ROOT / "logs" / "futures_paper.log", "a", encoding="utf-8"), stderr=subprocess.STDOUT)
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
    stock_rules = None
    if leverage_on():
        stock_rules = refresh_stock_playbook()
        try:
            spy = fetch_stock("SPY")
            spy_trend = cl.daily_trend_series(spy)
            for s_ in STOCKS:
                raw = spy if s_ == "SPY" else fetch_stock(s_)
                if len(raw) > 200:
                    data[s_] = cl.prepare(raw, spy_trend)
        except Exception as e:
            log(f"Stock data error: {e}")
    manage(st, data)
    if st.pop("sync_now", False):                    # fresh balance right after a sell
        sync_balance(st, today, force=True)
    if st.get("waiting"):
        retry_waiting(st)
    open_new(st, data, rules, stock_rules)
    save_15m(COINS)
    prices = {c: float(d.close.iloc[-1]) for c, d in data.items()}
    eq = equity(st, prices)
    st["peak"] = max(st["peak"], eq)
    dd = limits()["drawdown"]
    if not st["paused"] and eq < st["peak"] * (1 - dd):
        st["paused"] = True
        log(f"PAUSED: equity ${eq:.2f} is {dd:.0%} below peak ${st['peak']:.2f}. Review, then set paused=false.", "Crypto desk paused")
    st["equity"] = round(eq, 2)
    STATE.write_text(json.dumps(st, indent=2, default=str), encoding="utf-8")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M')}  {'LIVE' if LIVE else 'paper'}  equity ${eq:.2f} | cash ${st['cash']:.2f} | open {len(st['positions'])} | rules {len(rules)}"
          + (f" + {len(stock_rules)} stock" if stock_rules else "") + ("  PAUSED" if st["paused"] else ""))


if __name__ == "__main__":
    main()
