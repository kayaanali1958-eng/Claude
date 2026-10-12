#!/usr/bin/env python3
"""Adaptive Nasdaq desk: reads the market once each trading day near the close (15:40-15:58 ET) and holds
whatever fits it, with all the cash:
  - uptrend (QQQ above its 200-day average) and calm (20-day volatility under 25% a year): TQQQ, 3x
  - uptrend but wild: QQQ, 1x (the swings would eat a 3x fund)
  - downtrend: cash, except buying panic dips (2-day RSI under 10) in QQQ 1x, sold when RSI is over 70

Why: the hourly setups lost money when replayed the way the desk really trades (-17% over 7 months).
This rule was chosen on 2010-2018 (+20%/yr) and then checked on 2019-2026, which it never saw: +42%/yr
vs +23% for holding QQQ, worst drop -47% (losing years: 2011 -26%, 2022 -33%). Shorting the Nasdaq in
downtrends was tested and lost money, so it never shorts. About 12 switches a year.

Called by crypto_desk.py on every 5-minute run (it does nothing outside the window). Live when
settings.md says CRYPTO_MODE: live, unless DESK_STRATEGY=hourly is in .env; paper otherwise.
State: trend_state.json; log: the desk's journal; phone alerts through notify.py.
"""
import json, os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

import crypto_live
import notify

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "trend_state.json"
ET = ZoneInfo("America/New_York")
SIGNAL, FUND, SMA = "QQQ", "TQQQ", 200
CALM_VOL = 0.25
FUNDS = ("TQQQ", "QQQ")
WINDOW = (940, 959)          # 15:40-15:58 ET, minutes after midnight


def enabled():
    return (os.environ.get("DESK_STRATEGY") or "adaptive").strip().lower() in ("adaptive", "trend")


def _closes(sym):
    d = yf.download(sym, period="2y", interval="1d", progress=False, auto_adjust=True)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    return d["Close"].dropna()


def _last(sym):
    d = yf.download(sym, period="1d", interval="1m", progress=False, auto_adjust=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    c = d["Close"].dropna()
    return float(c.iloc[-1]), c.index[-1].to_pydatetime()


def signal(dip=False):
    """What to hold now: (target, why, price, sma, when, dip). Today's price stands in for today's close."""
    closes = _closes(SIGNAL)
    price, when = _last(SIGNAL)
    today = datetime.now(ET).date()
    c = pd.concat([closes[closes.index.date < today], pd.Series([price], index=[pd.Timestamp(today)])])
    sma = c.iloc[-SMA:].mean()
    vol = c.pct_change().iloc[-20:].std() * (252 ** 0.5)
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=0.5).mean(), (-d.clip(upper=0)).ewm(alpha=0.5).mean()
    rsi2 = float((100 - 100 / (1 + up / dn)).iloc[-1])
    if price > sma:
        dip = False
        target, why = ("TQQQ", f"uptrend and calm (volatility {vol:.0%})") if vol < CALM_VOL else \
                      ("QQQ", f"uptrend but wild (volatility {vol:.0%}): 1x")
    else:
        if not dip and rsi2 < 10:
            dip = True
        elif dip and rsi2 > 70:
            dip = False
        target, why = ("QQQ", f"downtrend, buying a panic dip (RSI2 {rsi2:.0f})") if dip else \
                      ("CASH", f"downtrend (RSI2 {rsi2:.0f}): cash")
    return target, why, price, sma, when, dip


def check(live, log):
    """Once per trading day near the close: hold what fits the market (TQQQ, QQQ or cash)."""
    now = datetime.now(ET)
    m = now.hour * 60 + now.minute
    if now.weekday() >= 5 or not WINDOW[0] <= m < WINDOW[1]:
        return
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else \
        dict(done="", symbol=None, shares=0.0, entry=None, cash=1000.0, dip=False, history=[])
    if st.get("done") == now.date().isoformat():
        return
    try:
        target, why, price, sma, when, dip = signal(st.get("dip", False))
    except Exception as e:
        print(f"trend signal: {e}")
        return
    if (datetime.now(timezone.utc) - when.astimezone(timezone.utc)).total_seconds() > 900:
        st["done"] = now.date().isoformat()                 # no fresh prices: market holiday
        STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")
        return
    if live:
        bal = crypto_live.balance()
        try:
            cash = float(bal["cash"])
            held = {f: float((bal.get("holdings") or {}).get(f, 0) or 0) for f in FUNDS}
        except (KeyError, TypeError, ValueError):
            log(f"Adaptive check: balance unavailable ({bal.get('error')}), trying again in 5 minutes")
            return
    else:
        cash, held = st["cash"], {f: (st["shares"] if st.get("symbol") == f else 0.0) for f in FUNDS}
    tag = "LIVE" if live else "PAPER"
    for f, qty in held.items():                             # sell what no longer fits the market
        if qty > 0 and f != target:
            fpx = _last(f)[0]
            if live:
                res = crypto_live.sell_fund(f, f"(any open {f} sell order)", f"market changed: {why}")
                if not res.get("ok"):
                    log(f"Adaptive SELL {f} failed: {res.get('error')} (retrying in 5 minutes)", "Desk: sell failed, check app")
                    return
                fill = float(res.get("avg_price") or fpx)
                cash += qty * fill
            else:
                fill = fpx
                cash = qty * fill
                st.update(cash=cash, shares=0.0, symbol=None)
            entry = st.get("entry") if st.get("symbol") == f else None
            pnl = (fill - entry) * qty if entry else 0.0
            st["history"].append(dict(date=now.date().isoformat(), action="sell", symbol=f, price=fill, pnl=round(pnl, 2)))
            st.update(symbol=None, shares=0.0, entry=None)
            log(f"{tag} SELL {qty:.4f} {f} @ {fill:.2f} (P&L ${pnl:+.2f}): {why}.", f"Desk sell: {f} ${pnl:+.2f}")
            held[f] = 0.0
    if target != "CASH" and held.get(target, 0) <= 0 and cash >= 5:
        fpx = _last(target)[0]
        if live:
            res = crypto_live.run(
                f"Stock order, not crypto (use review_equity_order before place_equity_order, never the crypto tools). "
                f"Buy {target} with all of the account's spendable cash (check get_portfolio buying power, minus $0.50 "
                f"for rounding): a dollar-based market BUY during regular hours. First get_equity_quotes {target}: if the "
                f"ask is more than 0.5% above {fpx:.2f}, don't buy and return ok false with error 'price moved'. Wait for "
                f"the fill. RESULT keys: ok, filled_qty, avg_price, buy_order_id.",
                tools=crypto_live.FUND_TOOLS)
            if not res.get("ok"):
                log(f"Adaptive BUY {target} failed: {res.get('error')} (retrying in 5 minutes)", "Desk: buy failed")
                return
            shares, fill = float(res.get("filled_qty") or 0), float(res.get("avg_price") or fpx)
        else:
            shares, fill = cash / fpx, fpx
            st["cash"] = 0.0
        st.update(symbol=target, shares=shares, entry=fill)
        st["history"].append(dict(date=now.date().isoformat(), action="buy", symbol=target, price=fill, shares=shares))
        log(f"{tag} BUY {shares:.4f} {target} @ {fill:.2f}: Nasdaq {price:.2f} vs 200-day {sma:.2f}, {why}.",
            f"Desk buy: {target} ({why.split(' (')[0]})")
    elif target == "CASH" or held.get(target, 0) > 0:
        print(f"adaptive check: {why}; holding {target}, no change")
    st["dip"] = dip
    st["done"] = now.date().isoformat()
    st["last"] = dict(date=now.date().isoformat(), price=round(price, 2), sma=round(sma, 2), target=target, why=why)
    STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")


if __name__ == "__main__":
    notify.load_env()
    t, why, p, s, w, _ = signal()
    print(f"Nasdaq (QQQ) {p:.2f} vs 200-day average {s:.2f}: {why} -> hold {t}")
