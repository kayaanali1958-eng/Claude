# Strategy Playbook

The technical-analyst picks the regime first (strategies A–G), then only looks for the strategies allowed in that regime. The risk-manager scores each setup with that strategy's checklist. If the regime is unclear, the answer is STAND ASIDE.

All strategies are long-only in execution. A bearish setup is taken by **buying an inverse ETF** (see settings.md), never by shorting.

## 0. Automatic strategy selection (the playbook)
`backtests/playbook.json` is rebuilt every Friday by `scripts/backtest.py`. It tests every strategy in its library, long and short, in every market situation and with every exit, and keeps only the combinations that made money on days they were not tuned on.

Every run, for each symbol, the technical-analyst:
1. Works out the **situation** exactly as the playbook defines it: `trend` (up/down/flat: yesterday's close vs. its 20-day average, and the 20-day vs. the 50-day), `gap` (up/down/flat at ±0.3%), `vol` (yesterday's range vs. its 20-day average: high above 1.3×, low below 0.7×), `vix` (calm under 20, nervous 20–30, fear above 30), `time` (open before 10:30, midday until 14:00, late after), `vwap` (above/below).
2. Finds the playbook rules whose `when` conditions all match, and looks **first** for those strategies, with the rule's exit (target and breakeven).
3. Uses the regime table below only for strategies the playbook can't test (B, E, H, which depend on judgment or news).

When several match, take the rule with the best unseen-data result, then the most unseen trades. New strategies get added to the library in `scripts/backtest.py`; the playbook decides automatically where each one works.

### Playbook strategy library (exact rules in `scripts/backtest.py`)
- **A** sweep & reclaim of the prior-day high/low · **A2** sweep & reclaim of the day's last swing low/high (liquidity under/over intraday swings) · **C** ORB + retest · **C2** ORB breakout, no retest · **F** VWAP reclaim/reject
- **D2** VWAP band fade: price stretched 2 standard deviations from VWAP, then a reversal bar; target VWAP
- **G** gap and go · **G2** gap fill · **I** prior-day high/low breakout on volume · **J** midday range breakout after 13:30
- **K** opening drive: a strong first 15 minutes (±0.4%) continued at 9:50 on the right side of VWAP
- **L** first pullback after a new high/low of day · **M** inside-bar breakout with VWAP

## 1. Pick the regime (every run, per symbol)

| Regime | How to recognize it (5m chart, today) | Allowed strategies |
|---|---|---|
| **Trend up** | HTF bias bullish; 5m higher highs and higher lows; price above VWAP and above PDL/ONL | A (with trend), B, C, F, G, H long |
| **Trend down** | HTF bias bearish; 5m lower highs and lower lows; price below VWAP and below PDL/ONL | A (with trend), B, C, F, G short (inverse) |
| **Range / chop** | At least 2 touches of both a range high and a range low in the last 60 min; VWAP flat; range ≥ 0.25% wide | D, G (gap fill only) |
| **News-driven** | News-analyst condition is news-driven and a major release or speaker is within 60 min, or was in the last 30 min; or policy-watch reports HIGH headline risk | E only; size halved |
| **Unclear** | None of the above clearly | STAND ASIDE |

Fridays and the afternoon before NFP/CPI/FOMC: use half size for every strategy. The macro-strategist's lean is a tiebreaker only: when two setups compete, prefer the one in the direction of the lean.

Where the styles come from: A and B are the ICT / TJR-style liquidity model, C is the classic opening range breakout, D is range mean reversion, E is post-news continuation, F is the VWAP playbook used by institutional desks, G covers the common opening gap plays.

**Let winners run (all strategies):** at TP1 sell half and move the stop to breakeven. At TP2 sell half of what's left and keep the last quarter as a **runner** when the day is strong: regime is trend in the trade's direction, or the catalyst grade is +2. Trail the runner's stop under each new 5m higher low (above each lower high for inverse trades) and exit on the trail, at a 5m close back through VWAP, or at 15:55. Otherwise exit everything at TP2. One-share positions have no runner: they exit at TP2.

**Picking between setups:** take at most one new trade per signal symbol at a time. If several qualify, take the highest checklist score, then the higher R:R to TP2, then the one aligned with the macro lean.

## 2. Strategies

### A. Liquidity sweep reversal (ICT)
- **When:** 9:35–11:00 main window; 13:45–15:15 only if very clean.
- **Long:** sell-side swept (PDL, ONL, session low, equal lows) and closed back above; bullish displacement with BOS/CHoCH on 5m or 1m; enter at the FVG/OB retrace.
- **Short (inverse):** buy-side swept (PDH, ONH, session high, equal highs) and closed back below; bearish displacement with BOS; enter at the bearish FVG retrace.
- **Stop:** beyond the sweep extreme. **TP1:** nearest opposing liquidity. **TP2:** HTF draw.
- **Min R:R:** 2:1 to TP2.
- **Checklist (need 6/7):** HTF bias aligned · clear sweep · displacement/BOS · valid FVG/OB · target at opposing liquidity · ≥2:1 · outside blackout.

### B. Trend pullback continuation
- **When:** 9:45–11:30 and 13:45–15:15.
- **Setup:** a 5m BOS in the trend direction, then a pullback into the FVG/OB it left, or to VWAP, that holds (a 1m rejection candle or 1m CHoCH back in the trend direction).
- **Stop:** beyond the pullback extreme. **TP1:** the last swing high (low). **TP2:** next liquidity in the trend direction.
- **Min R:R:** 2:1 to TP2.
- **Checklist (need 6/7):** regime is trend · 5m BOS in trend direction · pullback into FVG/OB/VWAP · rejection or 1m CHoCH confirms · price on the trend side of VWAP · ≥2:1 · outside blackout.

### C. Opening range breakout and retest
- **When:** entries 9:50–10:45 only.
- **Setup:** 15-minute opening range (9:30–9:45). A 5m close outside the range with above-average volume, then a retest of the range edge that holds.
- **Stop:** back inside the range by 1/3 of its width (or the middle of the range if tighter). **TP1:** 1× range width. **TP2:** 2× range width or next HTF liquidity, whichever comes first.
- **Min R:R:** 2:1 to TP2.
- **Checklist (need 5/6):** breakout direction matches HTF bias (or HTF neutral) · 5m close outside with volume · retest holds · target clear of PDH/PDL in the way · ≥2:1 · outside blackout.

### D. Range fade (mean reversion)
- **When:** 10:30–15:00, regime = range only.
- **Setup:** price pokes beyond a range edge, closes back inside on 5m, and a 1m CHoCH confirms. Long at the low edge, short (inverse) at the high edge.
- **Stop:** beyond the poke extreme. **TP1:** VWAP or range midpoint. **TP2:** the opposite edge (minus a few cents).
- **Risk:** half of normal (0.5%). **Min R:R:** 1.5:1 to TP2.
- **Checklist (need 5/6):** regime is range (2+ touches per side) · poke and 5m close back inside · 1m CHoCH confirms · VWAP flat · ≥1.5:1 · outside blackout.

### E. Post-news continuation
- **When:** only after a release or speaker's blackout ends.
- **Setup:** the move the news created (a 5m close beyond the pre-news range) holds a retest of that range edge after the blackout. No fading the news move.
- **Stop:** back inside the pre-news range. **TP1:** 1× pre-news range height. **TP2:** next liquidity.
- **Risk:** half of normal. **Min R:R:** 2:1 to TP2.
- **Checklist (need 5/6):** blackout over · 5m close beyond pre-news range · retest holds · direction matches HTF bias or the news clearly changed it · ≥2:1 · no second release within 30 min.

### F. VWAP reclaim / reject (trend day)
- **When:** 10:00–15:00, regime = trend.
- **Long:** in a trend-up regime, price pulls back below VWAP, then a 5m candle closes back above VWAP and the next 1m pullback holds above it. Enter at VWAP + a few cents.
- **Short (inverse):** mirror in trend down: a 5m close back below VWAP after a pop above it.
- **Stop:** beyond the pullback extreme. **TP1:** the session high (low). **TP2:** next HTF liquidity.
- **Min R:R:** 2:1 to TP2.
- **Checklist (need 5/6):** regime is trend · VWAP sloping in trend direction · 5m reclaim/reject close · 1m hold confirms · ≥2:1 · outside blackout.

### G. Opening gap play
- **When:** entries 9:35–10:30 only. Gap = today's open vs prior close, at least 0.3%.
- **Gap and go:** gap in the HTF bias direction, first 5m candle closes in the gap direction, and the first pullback holds above (below) the opening print. Stop beyond the pullback; TP1 = premarket high (low); TP2 = next HTF liquidity.
- **Gap fill:** gap against the HTF bias, or into a major level (PDH/PDL, weekly high/low), and the first 15 minutes fail to extend. Enter on a 5m close back toward the prior close; stop beyond the opening extreme; TP1 = half the gap; TP2 = prior close (the full fill).
- **Min R:R:** 2:1 to TP2.
- **Checklist (need 5/6):** gap ≥ 0.3% · direction rule above met · first-candle / failure-to-extend confirmation · clear stop level · ≥2:1 · outside blackout.

### H. Catalyst continuation (news / earnings)
- **When:** 9:45–11:30 and 13:45–15:00, on scanner stocks (or SPY/QQQ when a mega-cap report moves the index).
- **Needs:** earnings-analyst grade +2 or +1 with reaction "confirms" (long). Grade -1/-2 is signal only for single stocks (no shorting). "Conflicted" means no trade.
- **Setup:** after the first 15 minutes, a pullback to VWAP or the opening-range edge that holds, then a 5m close back in the catalyst's direction. Never buy the first spike.
- **Stop:** below the pullback low (at least 0.3% away). **TP1:** the high of day. **TP2:** next daily level (prior high, 52-week high, gap measured move).
- **Size:** full size only for grade +2; half size for +1.
- **Min R:R:** 2:1 to TP2.
- **Checklist (need 5/6):** grade ≥ +1 and confirms · relative volume ≥ 2 · pullback holds VWAP/OR edge · 5m close in catalyst direction · ≥2:1 · outside blackout.

**News and earnings always count:** a setup that goes against an earnings-analyst grade of ±2 is rejected, and a policy-watch HIGH headline makes the regime news-driven.

## 3. Always stand aside when
- The data-quality check fails (stale, interpolated, or mismatched prices).
- Inside a blackout, or after 3:30 PM ET.
- Two losing trades already today (in any book), or the daily loss limit is hit.
- The setup would re-enter a trade that just stopped out on the same level.
- The regime changed within the last 15 minutes (wait for it to settle).
