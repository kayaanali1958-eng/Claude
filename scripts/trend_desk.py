#!/usr/bin/env python3
"""Trend desk: holds TQQQ (3x Nasdaq-100) while the Nasdaq-100 (QQQ) is above its 200-day average, and cash
when it is below. Checked once each trading day near the close (15:40-15:58 ET), so it acts on
(almost) the day's closing price like the test did.

Why this and not the hourly setups: replayed the way the desk really trades (leverage, market hours,
gaps, costs) on months they never learned from, the hourly setups lost money (-17% over 7 months).
This rule has no settings to fit. Test 2010-2026 with fund costs: about +26% a year vs +20% for just
holding QQQ, but with a worst drop of 58% and losing years (2011 -39%, 2022 -45%). About 3 trades a year.

Called by crypto_desk.py on every 5-minute run (it does nothing outside the window). Live when
settings.md says CRYPTO_MODE: live and DESK_STRATEGY in .env is empty or "trend"; paper otherwise.
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
WINDOW = (940, 959)          # 15:40-15:58 ET, minutes after midnight


def enabled():
    return (os.environ.get("DESK_STRATEGY") or "trend").strip().lower() == "trend"


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


def signal():
    """(above, price, sma): today's price vs the 200-day average of the last 199 closes plus today."""
    closes = _closes(SIGNAL)
    price, when = _last(SIGNAL)
    today = datetime.now(ET).date()
    past = closes[closes.index.date < today]
    sma = (past.iloc[-(SMA - 1):].sum() + price) / SMA
    return price > sma, price, sma, when


def check(live, log):
    """Once per trading day near the close: hold TQQQ above the average, cash below it."""
    now = datetime.now(ET)
    m = now.hour * 60 + now.minute
    if now.weekday() >= 5 or not WINDOW[0] <= m < WINDOW[1]:
        return
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else \
        dict(done="", holding=False, shares=0.0, entry=None, cash=1000.0, history=[])
    if st.get("done") == now.date().isoformat():
        return
    try:
        above, price, sma, when = signal()
    except Exception as e:
        print(f"trend signal: {e}")
        return
    if (datetime.now(timezone.utc) - when.astimezone(timezone.utc)).total_seconds() > 900:
        st["done"] = now.date().isoformat()                 # no fresh prices: market holiday
        STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")
        return
    gap = (price / sma - 1) * 100
    if live:
        bal = crypto_live.balance()
        try:
            cash = float(bal["cash"])
            held = float((bal.get("holdings") or {}).get(FUND, 0) or 0)
        except (KeyError, TypeError, ValueError):
            log(f"Trend check: balance unavailable ({bal.get('error')}), trying again in 5 minutes")
            return
    else:
        cash, held = st["cash"], st["shares"]
    fpx = _last(FUND)[0]
    if above and held <= 0 and cash >= 5:
        if live:
            res = crypto_live.run(
                f"Stock order, not crypto (use review_equity_order before place_equity_order, never the crypto tools). "
                f"Buy {FUND} with all of the account's spendable cash (${cash:.2f}, minus $0.50 for rounding): a "
                f"dollar-based market BUY during regular hours. First get_equity_quotes {FUND}: if the ask is more "
                f"than 0.5% above {fpx:.2f}, don't buy and return ok false with error 'price moved'. Wait for the fill. "
                f"RESULT keys: ok, filled_qty, avg_price, buy_order_id.",
                tools=crypto_live.FUND_TOOLS)
            if not res.get("ok"):
                log(f"Trend BUY {FUND} failed: {res.get('error')} (retrying in 5 minutes)", "Desk: trend buy failed")
                return
            shares, fill = float(res.get("filled_qty") or 0), float(res.get("avg_price") or fpx)
        else:
            shares, fill = cash / fpx, fpx
            st["cash"] = 0.0
        st.update(holding=True, shares=shares, entry=fill)
        st["history"].append(dict(date=now.date().isoformat(), action="buy", price=fill, shares=shares))
        log(f"{'LIVE' if live else 'PAPER'} TREND BUY {shares:.4f} {FUND} @ {fill:.2f}: Nasdaq {price:.2f} is "
            f"{gap:+.1f}% vs its 200-day average {sma:.2f}. Holding while the trend is up.",
            f"Desk buy: {FUND} (Nasdaq uptrend)")
    elif not above and held > 0:
        if live:
            res = crypto_live.sell_fund(FUND, f"(any open {FUND} sell order)", "Nasdaq closed below its 200-day average")
            if not res.get("ok"):
                log(f"Trend SELL {FUND} failed: {res.get('error')} (retrying in 5 minutes)", "Desk: trend sell failed, check app")
                return
            fill = float(res.get("avg_price") or fpx)
        else:
            fill = fpx
            st["cash"] = st["shares"] * fill
        pnl = (fill - (st.get("entry") or fill)) * held
        st.update(holding=False, shares=0.0, entry=None)
        st["history"].append(dict(date=now.date().isoformat(), action="sell", price=fill, pnl=round(pnl, 2)))
        log(f"{'LIVE' if live else 'PAPER'} TREND SELL {held:.4f} {FUND} @ {fill:.2f} (P&L ${pnl:+.2f}): Nasdaq "
            f"{price:.2f} fell {gap:+.1f}% vs its 200-day average {sma:.2f}. In cash until the trend turns up.",
            f"Desk sell: {FUND} ${pnl:+.2f}")
    else:
        what = f"holding {FUND}" if held > 0 else "in cash"
        print(f"trend check: Nasdaq {gap:+.1f}% vs 200-day average, {what}, no change")
    st["done"] = now.date().isoformat()
    st["last"] = dict(date=now.date().isoformat(), price=round(price, 2), sma=round(sma, 2), above=bool(above))
    STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")


if __name__ == "__main__":
    notify.load_env()
    a, p, s, w = signal()
    print(f"Nasdaq (QQQ) {p:.2f} vs 200-day average {s:.2f}: {'ABOVE: hold TQQQ' if a else 'BELOW: cash'}")
