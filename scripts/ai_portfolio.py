#!/usr/bin/env python3
"""AI-infrastructure portfolio manager (paper): holds the strongest AI build-out stocks, drops the weak.

The theme: everything AI needs - chips, memory, chip equipment, networking, servers, power, cooling,
data centers and the cloud companies building them. The rules (no Claude usage):
  - Each week (first run of a new week), rank the universe by 6-month return. A stock is eligible
    only if it is above its 200-day average (it is actually going up). Hold the top HOLD names in
    equal weights; with fewer eligible, the rest stays in cash.
  - Every day, sell a holding that falls TRAIL below its highest close since it was bought.
  - 0.1% cost per trade. Fractional shares. Phone alert on every buy and sell.

Usage:  python scripts/ai_portfolio.py              (daily run; acts once per weekday after 4 PM ET)
        python scripts/ai_portfolio.py --backtest   (the same rules on history vs SPY and SMH)
Paper only: tracked in ai_portfolio_state.json / ai_portfolio_journal.md. Start: AI_PORTFOLIO_START in .env.
"""
import json, os, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "ai_portfolio_state.json"
JOURNAL = ROOT / "ai_portfolio_journal.md"
UNIVERSE = {
    "chips": ["NVDA", "AMD", "AVGO", "MRVL", "ARM", "TSM"],
    "memory": ["MU"],
    "chip equipment": ["ASML", "AMAT", "LRCX"],
    "networking and servers": ["ANET", "SMCI", "DELL"],
    "power and cooling": ["VRT", "ETN", "CEG", "VST"],
    "data centers": ["EQIX", "DLR"],
    "cloud builders": ["MSFT", "GOOGL", "AMZN", "META", "ORCL"],
}
TICKERS = [t for group in UNIVERSE.values() for t in group]
HOLD, LOOKBACK, TREND, TRAIL, COST = 4, 126, 200, 0.15, 0.001


def download(tickers, period):
    df = yf.download(tickers, period=period, interval="1d", progress=False, auto_adjust=True)["Close"]
    return df.dropna(how="all")


def picks(close, i):
    """Top HOLD eligible tickers on day i (uses closes up to and including day i)."""
    if i < TREND:
        return []
    px = close.iloc[i]
    sma = close.iloc[i - TREND + 1:i + 1].mean()
    mom = px / close.iloc[i - LOOKBACK] - 1
    ok = (px > sma) & (mom > 0) & px.notna() & sma.notna()
    return list(mom[ok].sort_values(ascending=False).index[:HOLD])


def backtest():
    close = download(TICKERS + ["SPY", "SMH"], "10y")
    bench = close[["SPY", "SMH"]]
    close = close[TICKERS]
    eq, cash, hold, high, week, curve = 1.0, 1.0, {}, {}, None, []
    for i in range(TREND, len(close)):
        px = close.iloc[i]
        for t in list(hold):                                   # daily trailing exit
            high[t] = max(high[t], px[t])
            if px[t] < high[t] * (1 - TRAIL):
                cash += hold.pop(t) * px[t] * (1 - COST)
                del high[t]
        wk = close.index[i].isocalendar()[:2]
        if wk != week:                                         # weekly rebalance
            week = wk
            value = cash + sum(q * px[t] for t, q in hold.items())
            want = picks(close, i)
            for t in list(hold):
                if t not in want:
                    cash += hold.pop(t) * px[t] * (1 - COST)
                    del high[t]
            per = value / HOLD
            for t in want:
                have = hold.get(t, 0) * px[t]
                if have < per * 0.8:                           # top up only when clearly underweight
                    buy = min(per - have, cash)
                    if buy > 0:
                        hold[t] = hold.get(t, 0) + buy * (1 - COST) / px[t]
                        high.setdefault(t, px[t])
                        cash -= buy
        curve.append((close.index[i], cash + sum(q * px[t] for t, q in hold.items())))
    s = pd.Series(dict(curve))
    out = ["# AI-infrastructure portfolio backtest", "",
           f"Rules: top {HOLD} by 6-month return among stocks above their 200-day average, weekly; "
           f"sell on a {TRAIL:.0%} drop from the high; {COST:.1%} cost per trade.", "",
           "| | total | per year | worst drop |", "|---|---|---|---|"]
    for name, series in [("AI portfolio", s), ("SPY (S&P 500)", bench.SPY.reindex(s.index)),
                         ("SMH (chip ETF)", bench.SMH.reindex(s.index))]:
        series = series / series.iloc[0]
        years = (series.index[-1] - series.index[0]).days / 365.25
        dd = (1 - series / series.cummax()).max()
        out.append(f"| {name} | {series.iloc[-1] - 1:+.0%} | {series.iloc[-1] ** (1 / years) - 1:+.0%} | -{dd:.0%} |")
    out += ["", f"From {s.index[0]:%Y-%m-%d} to {s.index[-1]:%Y-%m-%d}.",
            "Caution: this list was picked today, knowing AI stocks did well; real results will be lower."]
    text = "\n".join(out)
    (ROOT / "backtests").mkdir(exist_ok=True)
    (ROOT / "backtests" / "ai_portfolio_backtest.md").write_text(text + "\n", encoding="utf-8")
    print(text)


def log(text, alert=None):
    stamp = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
    with JOURNAL.open("a", encoding="utf-8") as f:
        if JOURNAL.stat().st_size == 0:
            f.write("# AI Portfolio Journal (paper)\n\n")
        f.write(f"- {stamp} · {text}\n")
    print(stamp, text)
    if alert:
        notify.push(alert, text)


def daily():
    now = datetime.now(ZoneInfo("America/New_York"))
    today = now.date().isoformat()
    JOURNAL.touch()
    if STATE.exists():
        st = json.loads(STATE.read_text(encoding="utf-8"))
    else:
        start = float(os.environ.get("AI_PORTFOLIO_START") or 100)
        st = dict(start=start, cash=start, holdings={}, last_run="", week="")
    if "--now" not in sys.argv and (now.weekday() >= 5 or now.hour < 16 or st["last_run"] == today):
        return                                                  # once per weekday, after the close
    close = download(TICKERS, "2y")
    i = len(close) - 1
    px = close.iloc[i]
    for t in list(st["holdings"]):
        h = st["holdings"][t]
        h["high"] = max(h["high"], float(px[t]))
        if px[t] < h["high"] * (1 - TRAIL):
            st["cash"] += h["qty"] * float(px[t]) * (1 - COST)
            log(f"SELL {t} @ {px[t]:,.2f}: down {TRAIL:.0%} from its high {h['high']:,.2f} "
                f"(P&L {px[t] / h['entry'] - 1:+.1%})", f"AI portfolio: sold {t}")
            del st["holdings"][t]
    week = "%d-W%02d" % now.isocalendar()[:2]
    if week != st["week"]:
        st["week"] = week
        value = st["cash"] + sum(h["qty"] * float(px[t]) for t, h in st["holdings"].items())
        want = picks(close, i)
        for t in list(st["holdings"]):
            if t not in want:
                h = st["holdings"].pop(t)
                st["cash"] += h["qty"] * float(px[t]) * (1 - COST)
                log(f"SELL {t} @ {px[t]:,.2f}: no longer in the top {HOLD} going up "
                    f"(P&L {px[t] / h['entry'] - 1:+.1%})", f"AI portfolio: sold {t}")
        per = value / HOLD
        for t in want:
            h = st["holdings"].get(t)
            have = h["qty"] * float(px[t]) if h else 0
            buy = min(per - have, st["cash"])
            if have < per * 0.8 and buy > 0.5:
                qty = buy * (1 - COST) / float(px[t])
                if h:
                    h["entry"] = (h["entry"] * h["qty"] + float(px[t]) * qty) / (h["qty"] + qty)
                    h["qty"] += qty
                else:
                    st["holdings"][t] = dict(qty=qty, entry=float(px[t]), high=float(px[t]), bought=today)
                st["cash"] -= buy
                mom = px[t] / close[t].iloc[i - LOOKBACK] - 1
                group = next(g for g, ts in UNIVERSE.items() if t in ts)
                log(f"BUY ${buy:,.2f} of {t} ({group}) @ {px[t]:,.2f}: up {mom:+.0%} in 6 months, above its 200-day average",
                    f"AI portfolio: bought {t}")
        if not want:
            log("No AI stock is in an uptrend: holding cash this week.")
    value = st["cash"] + sum(h["qty"] * float(px[t]) for t, h in st["holdings"].items())
    st["last_run"], st["value"] = today, round(value, 2)
    STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")
    names = ", ".join(st["holdings"]) or "cash only"
    print(f"{today}  AI portfolio (paper)  value ${value:,.2f} ({value / st['start'] - 1:+.1%}) | {names}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    notify.load_env()
    backtest() if "--backtest" in sys.argv else daily()
