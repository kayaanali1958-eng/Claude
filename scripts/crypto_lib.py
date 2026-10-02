"""Shared crypto strategy library, situations and indicators (1-hour bars, UTC).

Liquidity: stops and resting orders cluster just beyond obvious levels (prior-day high/low, the Asia
session range, equal highs/lows). Price often runs those levels (a "sweep") and then reverses.
T9-T11 trade that reversal; breakouts (T2, T6) trade the moves that don't reverse.

Used by scripts/backtest_crypto.py (learning) and scripts/crypto_desk.py (paper trading), so the
desk trades exactly what was tested. Long only: Robinhood crypto can't be shorted.

To add a strategy: write one more function with @strategy. The playbook decides where it works.
"""
import numpy as np
import pandas as pd

FEE = 0.002          # 0.2% per side: Robinhood's crypto spread is built into the price
MAX_HOLD = 48        # hours
EXITS = [dict(target=t, be=b) for t in (1.5, 2.0, 3.0) for b in (False, True)]
STRATEGIES = {}


def strategy(name):
    def reg(fn):
        STRATEGIES[name] = fn
        return fn
    return reg


# ---------------- indicators ----------------
def prepare(df, btc_daily_trend=None):
    """df: hourly OHLCV indexed by UTC timestamps. Adds indicators and situation columns."""
    df = df.copy()
    c, h, l, v = df.close, df.high, df.low, df.volume
    df["ema20"], df["ema50"] = c.ewm(span=20).mean(), c.ewm(span=50).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["atr100"] = tr.rolling(100).mean()
    delta = c.diff()
    up, dn = delta.clip(lower=0).rolling(14).mean(), (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    df["bb_up"], df["bb_mid"], df["bb_lo"] = mid + 2 * sd, mid, mid - 2 * sd
    df["bbw"] = (df.bb_up - df.bb_lo) / mid
    df["hi24"], df["lo24"] = h.rolling(24).max().shift(), l.rolling(24).min().shift()
    df["avgv"] = v.rolling(48).mean().shift()
    day = df.index.floor("D")
    tp = (h + l + c) / 3
    df["vwap"] = (tp * v).groupby(day).cumsum() / v.groupby(day).cumsum()
    # prior UTC day high/low (liquidity resting above/below)
    dd = df.resample("1D").agg({"high": "max", "low": "min"})
    df["pdh"] = day.map(dd.high.shift(1))
    df["pdl"] = day.map(dd.low.shift(1))
    # equal lows: two swing lows within 0.1% of each other in the last 48 hours (stops cluster below)
    swing = (l < l.shift(1)) & (l < l.shift(-1))
    sl = l.where(swing.shift(1, fill_value=False)).ffill()        # last confirmed swing low
    sl_prev = l.where(swing.shift(1, fill_value=False)).dropna().shift(1).reindex(df.index).ffill()
    df["eq_low"] = np.where((sl - sl_prev).abs() / sl < 0.001, np.minimum(sl, sl_prev), np.nan)
    # Asia range (00-08 UTC) of the same day
    asia = df[df.index.hour < 8]
    df["asia_hi"] = day.map(asia.high.groupby(asia.index.floor("D")).max())
    df["asia_lo"] = day.map(asia.low.groupby(asia.index.floor("D")).min())
    # daily trend, known at the start of each day (yesterday's values)
    d = df.resample("1D").agg({"close": "last"})
    d["s20"], d["s50"] = d.close.rolling(20).mean(), d.close.rolling(50).mean()
    tr_d = np.where((d.close > d.s20) & (d.s20 > d.s50), "up", np.where((d.close < d.s20) & (d.s20 < d.s50), "down", "flat"))
    d["trend"] = pd.Series(tr_d, index=d.index).shift(1)
    df["trend"] = day.map(d.trend).fillna("flat")
    # situation
    ratio = df.atr / df.atr100
    df["vol"] = np.where(ratio > 1.3, "high", np.where(ratio < 0.8, "low", "normal"))
    hr = df.index.hour
    df["session"] = np.where(hr < 8, "asia", np.where(hr < 13, "europe", np.where(hr < 21, "us", "late")))
    df["weekend"] = np.where(df.index.dayofweek >= 5, "yes", "no")
    df["btc"] = day.map(btc_daily_trend).fillna("flat") if btc_daily_trend is not None else df["trend"]
    return df


def daily_trend_series(df):
    p = prepare(df)
    return p.trend.groupby(p.index.floor("D")).first()


# ---------------- strategies: fn(df, i) -> (entry, stop) or None, using bars up to i ----------------
@strategy("T1 trend pullback to EMA20")
def s_trend_pb(d, i):
    r, p = d.iloc[i], d.iloc[i - 1]
    if r.ema20 > r.ema50 and p.low <= p.ema20 and r.close > r.ema20 and r.close > p.high:
        return r.close, min(p.low, r.close - 1.5 * r.atr)


@strategy("T2 24h breakout on volume")
def s_break24(d, i):
    r = d.iloc[i]
    if r.close > r.hi24 and r.volume > 1.5 * r.avgv and d.iloc[i - 1].close <= r.hi24:
        return r.close, r.close - 1.5 * r.atr


@strategy("T3 oversold bounce (RSI)")
def s_rsi(d, i):
    r, p = d.iloc[i], d.iloc[i - 1]
    if p.rsi < 30 and r.close > p.high and r.close > r.open:
        return r.close, min(p.low, r.low) - 0.2 * r.atr


@strategy("T4 Bollinger squeeze breakout")
def s_squeeze(d, i):
    r = d.iloc[i]
    w = d.bbw.iloc[i - 50:i]
    if len(w) == 50 and d.bbw.iloc[i - 1] <= w.min() * 1.05 and r.close > r.bb_up:
        return r.close, r.bb_mid


@strategy("T5 VWAP reclaim in uptrend")
def s_vwap(d, i):
    r, p = d.iloc[i], d.iloc[i - 1]
    if r.ema20 > r.ema50 and p.close < p.vwap and r.close > r.vwap:
        return r.close, min(p.low, r.low) - 0.2 * r.atr


@strategy("T6 Asia range breakout")
def s_asia(d, i):
    r = d.iloc[i]
    if 8 <= d.index[i].hour < 16 and not np.isnan(r.asia_hi) and r.close > r.asia_hi and d.iloc[i - 1].close <= r.asia_hi:
        return r.close, max((r.asia_hi + r.asia_lo) / 2, r.close - 2 * r.atr)


@strategy("T7 volume spike momentum")
def s_spike(d, i):
    s, r = d.iloc[i - 1], d.iloc[i]
    if s.close / s.open - 1 > 0.015 and s.volume > 3 * s.avgv and r.low > (s.open + s.close) / 2 and r.close > s.close:
        return r.close, (s.open + s.close) / 2


@strategy("T8 Bollinger lower-band reversal")
def s_bb_rev(d, i):
    r, p = d.iloc[i], d.iloc[i - 1]
    if p.close < p.bb_lo and r.close > r.bb_lo and r.close > p.high:
        return r.close, min(p.low, r.low) - 0.2 * r.atr


@strategy("T9 sweep of prior-day low & reclaim")
def s_sweep_pdl(d, i):
    r, p = d.iloc[i], d.iloc[i - 1]
    if np.isnan(r.pdl):
        return
    lo = d.low.iloc[max(0, i - 3):i + 1].min()
    if lo < r.pdl and r.close > r.pdl and p.close <= r.pdl * 1.002:
        return r.close, lo - 0.2 * r.atr


@strategy("T10 Asia-low sweep in Europe/US & reclaim")
def s_sweep_asia(d, i):
    r = d.iloc[i]
    if not 8 <= d.index[i].hour < 20 or np.isnan(r.asia_lo):
        return
    lo = d.low.iloc[max(0, i - 3):i + 1].min()
    if lo < r.asia_lo and r.close > r.asia_lo and d.iloc[i - 1].close <= r.asia_lo * 1.002:
        return r.close, lo - 0.2 * r.atr


@strategy("T11 equal-lows sweep & reclaim")
def s_sweep_eq(d, i):
    r, p = d.iloc[i], d.iloc[i - 1]
    lvl = p.eq_low
    if np.isnan(lvl):
        return
    if r.low < lvl and r.close > lvl and r.close > r.open:
        return r.close, r.low - 0.2 * r.atr


def signals_at(d, i):
    """All strategies that fire on bar i: list of (name, entry, stop)."""
    out = []
    for name, fn in STRATEGIES.items():
        try:
            s = fn(d, i)
        except (IndexError, KeyError):
            s = None
        if s and np.isfinite(s[0]) and np.isfinite(s[1]) and s[0] - s[1] > s[0] * 0.003:
            out.append((name, float(s[0]), float(s[1])))
    return out


def situation(d, i):
    r = d.iloc[i]
    return dict(trend=r.trend, vol=r.vol, session=r.session, weekend=r.weekend, btc=r.btc)


def simulate(d, i, entry, stop):
    """R for every exit variant, walking forward from bar i+1 (fees included)."""
    risk, res = entry - stop, {}
    H, L, C = d.high.to_numpy(), d.low.to_numpy(), d.close.to_numpy()
    end = min(len(d) - 1, i + MAX_HOLD)
    for ex in EXITS:
        tgt, st, px = entry + ex["target"] * risk, stop, C[end]
        for j in range(i + 1, end + 1):
            if L[j] <= st:
                px = st; break
            if H[j] >= tgt:
                px = tgt; break
            if ex["be"] and H[j] >= entry + risk:
                st = max(st, entry)
        res[(ex["target"], ex["be"])] = (px - entry - FEE * (entry + px)) / risk
    return res
