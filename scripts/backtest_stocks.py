#!/usr/bin/env python3
"""Stock playbook for the leveraged desk: the same strategy library as the crypto desk, learned on
2 years of hourly SPY / QQQ / SMH bars. Live, a matching setup is traded through a 3x fund
(UPRO / TQQQ / SOXL) during market hours by scripts/crypto_desk.py.

Same method as scripts/backtest_crypto.py: learn on the first 2/3, keep what also worked on the last
1/3, then replay that last 1/3 the way the desk trades it (the replay gate for live trading).

Usage:  python scripts/backtest_stocks.py
Writes: backtests/stock_playbook.json, backtests/stock_report_<date>.md
"""
import json, sys
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crypto_lib as cl
import backtest_crypto as bc

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "backtests"


def load(sym):
    df = yf.download(sym, period="730d", interval="1h", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
    df.index = df.index.tz_convert("UTC")
    return df


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    cl.FEE = cl.STOCK_FEE
    raw = {s: load(s) for s in cl.STOCK_SIGNALS}
    trend = cl.daily_trend_series(raw["SPY"])
    prepared, rows = {}, []
    for s, d in raw.items():
        prepared[s] = cl.prepare(d, trend)
        rows += bc.collect(s, prepared[s])
        print(f"{s}: {len(d)} hourly bars")
    df = pd.DataFrame(rows)
    cut = df.ts.min() + (df.ts.max() - df.ts.min()) * 2 / 3
    rules, tested = bc.learn(df, cut)
    gate = {"all_in": bc.replay(prepared, rules, cut, True, min_stop=cl.STOCK_MIN_STOP),
            "risk_1pct": bc.replay(prepared, rules, cut, False, risk=0.01, min_stop=cl.STOCK_MIN_STOP),
            "risk_2pct": bc.replay(prepared, rules, cut, False, risk=0.02, min_stop=cl.STOCK_MIN_STOP)}
    for mode, g in gate.items():
        print(f"Replay of the unseen period ({mode}, no leverage): {g}")
    OUT.mkdir(exist_ok=True)
    stamp = date.today().isoformat()
    (OUT / "stock_playbook.json").write_text(json.dumps(dict(
        date=stamp, symbols=cl.STOCK_SIGNALS, train_until=str(cut), combinations_tested=tested,
        fee_per_side=cl.STOCK_FEE, replay=gate, rules=rules), indent=2, default=str), encoding="utf-8")
    lines = [f"# Stock playbook {stamp}", "", f"{len(rules)} rules on {', '.join(cl.STOCK_SIGNALS)} "
             f"(learned before {str(cut)[:10]}). Traded live through 3x funds.", ""]
    for mode, g in gate.items():
        lines.append(f"- **{mode}**: {g['trades']} trades, {g['win']}% wins, {g['return_pct']:+.1f}% (before leverage), "
                     f"worst drop {g['max_drop_pct']}% -> {'PASS' if g['profitable'] else 'FAIL'}")
    (OUT / f"stock_report_{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
