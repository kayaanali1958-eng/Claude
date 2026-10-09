#!/usr/bin/env python3
"""Long-term portfolio manager (paper): the money split into funds, with an AI and government slice.

Phase 2 of the plan (from $1,000; until then the crypto desk grows the account). The split:
  - FUNDS: S&P 500 (VOO) 35%, Nasdaq-100 (QQQM) 20%, chip makers (SMH) 15%: always held,
    brought back to their weights each week when they drift.
  - Growth slice, 30%: the strongest of about 120 big US stocks: everything AI needs (chips, memory, chip equipment, networking, servers,
    power, cooling, data centers, cloud builders) plus government winners (defense, space,
    nuclear, gov tech) and every other market leader. Companies whose new federal contract money
    jumped (USAspending.gov) rank higher. Each week, rank by 6-month return; a stock is
    eligible only if it is above its 200-day average (actually going up). Hold the top HOLD names;
    with fewer eligible, that part stays in cash. Every day, sell an AI stock that falls TRAIL below
    its highest close since it was bought (funds are never trail-sold: they are the long-term core).
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
    "defense and government": ["LMT", "RTX", "NOC", "GD", "LHX", "HII", "PLTR", "LDOS", "BAH", "CACI", "KTOS", "AVAV"],
    "space": ["RKLB"],
    "nuclear": ["CCJ", "BWXT"],
}
# Every other big, liquid US leader: picking the strongest from a wider list did better in the backtest
# (+35%/yr, worst drop -29%, vs +26%/yr and -32% with the AI and government list alone).
_LEADERS = ("AAPL TSLA BRK-B JPM LLY V UNH XOM MA COST HD PG JNJ ABBV NFLX CRM BAC CVX KO MRK PEP TMO ADBE WMT "
            "LIN ACN MCD CSCO ABT DHR WFC DIS INTU QCOM TXN VZ CAT AMGN IBM GE PM NOW ISRG UBER SPGI GS AMZN "
            "BKNG HON LOW UNP NEE PFE AXP MS PLD SYK BLK TJX VRTX ADP BSX MDT PANW SCHW C DE GILD KLAC ADI SBUX "
            "CB BX CRWD FTNT SNPS CDNS APP HOOD COIN MSTR SHOP").split()
UNIVERSE["other market leaders"] = [t for t in _LEADERS if all(t not in g for g in UNIVERSE.values())]
# Government contracts (USAspending.gov, the official record of federal awards): companies whose new
# contract money jumped get a ranking bonus. Name = how the company appears as a federal recipient.
GOV_NAMES = {"LMT": "LOCKHEED MARTIN", "RTX": "RAYTHEON", "NOC": "NORTHROP GRUMMAN", "GD": "GENERAL DYNAMICS",
             "LHX": "L3HARRIS", "HII": "HUNTINGTON INGALLS", "PLTR": "PALANTIR", "LDOS": "LEIDOS",
             "BAH": "BOOZ ALLEN HAMILTON", "CACI": "CACI", "KTOS": "KRATOS", "AVAV": "AEROVIRONMENT",
             "RKLB": "ROCKET LAB", "BWXT": "BWXT", "MSFT": "MICROSOFT", "AMZN": "AMAZON WEB SERVICES",
             "ORCL": "ORACLE", "GOOGL": "GOOGLE", "NVDA": "NVIDIA", "DELL": "DELL"}
GOV_FILE = ROOT / "backtests" / "gov_contracts.json"
TICKERS = [t for group in UNIVERSE.values() for t in group]
HOLD, LOOKBACK, TREND, TRAIL, COST = 4, 126, 200, 0.15, 0.001
FUNDS = {"VOO": ("S&P 500", 0.35), "QQQM": ("Nasdaq-100", 0.20), "SMH": ("chip makers", 0.15)}
AI_SLICE = 1 - sum(w for _, w in FUNDS.values())


def targets(close, i, boost=None):
    """{ticker: weight} for day i: the funds plus the AI and government picks."""
    want = {t: w for t, (_, w) in FUNDS.items()}
    for t in picks(close[TICKERS], i, boost):
        want[t] = AI_SLICE / HOLD
    return want


def label(t):
    if t in FUNDS:
        return f"{FUNDS[t][0]} fund"
    return next(g for g, ts in UNIVERSE.items() if t in ts)


def download(tickers, period):
    df = yf.download(tickers, period=period, interval="1d", progress=False, auto_adjust=True)["Close"]
    return df.dropna(how="all")


def gov_awards(refresh=False):
    """{ticker: {"recent": $ in the latest complete 90 days, "before": $ in the 90 days before, "surge": ratio}},
    refreshed weekly. Windows end 90 days back because defense awards are published with that delay."""
    import urllib.request
    from datetime import date, timedelta
    if GOV_FILE.exists() and not refresh:
        cached = json.loads(GOV_FILE.read_text(encoding="utf-8"))
        if cached.get("date", "") >= (date.today() - timedelta(days=7)).isoformat():
            return cached["awards"]
    def total(name, start, end):
        body = {"filters": {"time_period": [{"start_date": start, "end_date": end}], "award_type_codes": ["A", "B", "C", "D"],
                            "recipient_search_text": [name]}, "category": "recipient", "limit": 20}
        req = urllib.request.Request("https://api.usaspending.gov/api/v2/search/spending_by_category/recipient/",
                                     data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        return sum(r["amount"] for r in json.load(urllib.request.urlopen(req, timeout=30))["results"])
    t = date.today()
    lag = timedelta(days=90)                 # defense awards reach USAspending about 90 days late
    awards = {}
    for tk, name in GOV_NAMES.items():
        try:
            recent = total(name, (t - lag - timedelta(days=90)).isoformat(), (t - lag).isoformat())
            before = total(name, (t - lag - timedelta(days=180)).isoformat(), (t - lag - timedelta(days=91)).isoformat())
            awards[tk] = dict(recent=round(recent), before=round(before), surge=round(recent / before, 2) if before > 0 else None)
        except Exception as e:
            print(f"contracts {tk}: {e}")
    (ROOT / "backtests").mkdir(exist_ok=True)
    GOV_FILE.write_text(json.dumps(dict(date=t.isoformat(), awards=awards), indent=1), encoding="utf-8")
    return awards


def gov_boost(awards):
    """Ranking bonus: new federal contract money at least 1.5x the previous 90 days and over $100M."""
    return {tk: 1.25 for tk, a in (awards or {}).items()
            if a.get("surge") and a["surge"] >= 1.5 and a["recent"] >= 1e8}


def picks(close, i, boost=None):
    """Top HOLD eligible tickers on day i (uses closes up to and including day i). An uptrend is always
    required; a government-contract surge only moves a stock up the ranking."""
    if i < TREND:
        return []
    px = close.iloc[i]
    sma = close.iloc[i - TREND + 1:i + 1].mean()
    mom = px / close.iloc[i - LOOKBACK] - 1
    ok = (px > sma) & (mom > 0) & px.notna() & sma.notna()
    score = mom * pd.Series({t: (boost or {}).get(t, 1.0) for t in mom.index})
    return list(score[ok].sort_values(ascending=False).index[:HOLD])


def backtest():
    close = download(TICKERS + list(FUNDS) + ["SPY"], "10y")
    bench = close[["SPY", "SMH"]]
    eq, cash, hold, high, week, curve = 1.0, 1.0, {}, {}, None, []
    for i in range(TREND, len(close)):
        px = close.iloc[i]
        for t in [t for t in hold if t not in FUNDS]:          # daily trailing exit (AI stocks)
            high[t] = max(high[t], px[t])
            if px[t] < high[t] * (1 - TRAIL):
                cash += hold.pop(t) * px[t] * (1 - COST)
                del high[t]
        wk = close.index[i].isocalendar()[:2]
        if wk != week:                                         # weekly rebalance
            week = wk
            value = cash + sum(q * px[t] for t, q in hold.items())
            want = {t: w for t, w in targets(close, i).items() if not np.isnan(px[t])}
            for t in list(hold):
                if t not in want:
                    cash += hold.pop(t) * px[t] * (1 - COST)
                    high.pop(t, None)
                elif hold[t] * px[t] > value * want[t] * 1.25:  # trim a big overweight
                    sell = hold[t] - value * want[t] / px[t]
                    hold[t] -= sell
                    cash += sell * px[t] * (1 - COST)
            for t, w in want.items():
                per = value * w
                have = hold.get(t, 0) * px[t]
                if have < per * 0.8:                           # top up only when clearly underweight
                    buy = min(per - have, cash)
                    if buy > 0:
                        hold[t] = hold.get(t, 0) + buy * (1 - COST) / px[t]
                        high.setdefault(t, px[t])
                        cash -= buy
        curve.append((close.index[i], cash + sum(q * px[t] for t, q in hold.items())))
    s = pd.Series(dict(curve))
    out = ["# Long-term portfolio backtest (funds + AI slice)", "",
           "Split: " + ", ".join(f"{t} {w:.0%}" for t, (_, w) in FUNDS.items()) +
           f", AI slice {AI_SLICE:.0%} (top {HOLD} AI stocks by 6-month return above their 200-day average, "
           f"weekly; sold on a {TRAIL:.0%} drop from the high); {COST:.1%} cost per trade.", "",
           "| | total | per year | worst drop |", "|---|---|---|---|"]
    for name, series in [("Funds + growth portfolio", s), ("SPY (S&P 500)", bench.SPY.reindex(s.index)),
                         ("SMH (chip ETF)", bench.SMH.reindex(s.index))]:
        series = series / series.iloc[0]
        years = (series.index[-1] - series.index[0]).days / 365.25
        dd = (1 - series / series.cummax()).max()
        out.append(f"| {name} | {series.iloc[-1] - 1:+.0%} | {series.iloc[-1] ** (1 / years) - 1:+.0%} | -{dd:.0%} |")
    out += ["", f"From {s.index[0]:%Y-%m-%d} to {s.index[-1]:%Y-%m-%d}.",
            "Caution: the AI list was picked today, knowing AI stocks did well; real results will be lower."]
    text = "\n".join(out)
    (ROOT / "backtests").mkdir(exist_ok=True)
    (ROOT / "backtests" / "ai_portfolio_backtest.md").write_text(text + "\n", encoding="utf-8")
    print(text)


def log(text, alert=None):
    stamp = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
    with JOURNAL.open("a", encoding="utf-8") as f:
        if JOURNAL.stat().st_size == 0:
            f.write("# Long-term Portfolio Journal (paper)\n\n")
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
        start = float(os.environ.get("AI_PORTFOLIO_START") or 1000)
        st = dict(start=start, cash=start, holdings={}, last_run="", week="")
    if "--now" not in sys.argv and (now.weekday() >= 5 or now.hour < 16 or st["last_run"] == today):
        return                                                  # once per weekday, after the close
    close = download(TICKERS + list(FUNDS), "2y")
    i = len(close) - 1
    px = close.iloc[i]
    for t in [t for t in st["holdings"] if t not in FUNDS]:
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
        try:
            awards = gov_awards()
        except Exception as e:
            awards = {}
            print(f"government contracts unavailable: {e}")
        boost = gov_boost(awards)
        want = targets(close, i, boost)
        for t in list(st["holdings"]):
            h = st["holdings"][t]
            if t not in want:
                st["holdings"].pop(t)
                st["cash"] += h["qty"] * float(px[t]) * (1 - COST)
                log(f"SELL {t} @ {px[t]:,.2f}: no longer in the top {HOLD} going up "
                    f"(P&L {px[t] / h['entry'] - 1:+.1%})", f"AI portfolio: sold {t}")
            elif h["qty"] * float(px[t]) > value * want[t] * 1.25:
                sell = h["qty"] - value * want[t] / float(px[t])
                h["qty"] -= sell
                st["cash"] += sell * float(px[t]) * (1 - COST)
                log(f"TRIM {t} by ${sell * float(px[t]):,.2f} back to {want[t]:.0%} of the portfolio")
        for t, w in want.items():
            per = value * w
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
                why = (f"target {w:.0%}" if t in FUNDS else
                       f"up {px[t] / close[t].iloc[i - LOOKBACK] - 1:+.0%} in 6 months, above its 200-day average")
                if t in boost:
                    a = awards[t]
                    why += (f"; federal contracts ${a['recent'] / 1e6:,.0f}M in the latest reported quarter, "
                            f"{a['surge']:.1f}x the quarter before (USAspending.gov)")
                log(f"BUY ${buy:,.2f} of {t} ({label(t)}) @ {px[t]:,.2f}: {why}", f"Portfolio: bought {t}")
        if not any(t not in FUNDS for t in want):
            log("No AI stock is in an uptrend: the AI slice stays in cash this week.")
    value = st["cash"] + sum(h["qty"] * float(px[t]) for t, h in st["holdings"].items())
    st["last_run"], st["value"] = today, round(value, 2)
    STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")
    names = ", ".join(st["holdings"]) or "cash only"
    if now.weekday() == 4:                                      # Friday: weekly score to the phone
        notify.push("Paper portfolio this week", f"Value ${value:,.2f} ({value / st['start'] - 1:+.1%} since start). "
                    f"Holding: {names}. Practice money only.")
    print(f"{today}  Long-term portfolio (paper)  value ${value:,.2f} ({value / st['start'] - 1:+.1%}) | {names}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    notify.load_env()
    backtest() if "--backtest" in sys.argv else daily()
