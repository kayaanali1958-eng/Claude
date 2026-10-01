# Strategy Playbook

The technical-analyst picks the regime first, then only looks for the strategies allowed in that regime. The risk-manager scores each setup with that strategy's checklist. If the regime is unclear, the answer is STAND ASIDE.

All strategies are long-only in execution. A bearish setup is taken by **buying an inverse ETF** (see settings.md), never by shorting.

## 1. Pick the regime (every run, per symbol)

| Regime | How to recognize it (5m chart, today) | Allowed strategies |
|---|---|---|
| **Trend up** | HTF bias bullish; 5m higher highs and higher lows; price above VWAP and above PDL/ONL | A (with trend), B, C long |
| **Trend down** | HTF bias bearish; 5m lower highs and lower lows; price below VWAP and below PDL/ONL | A (with trend), B, C short (inverse) |
| **Range / chop** | At least 2 touches of both a range high and a range low in the last 60 min; VWAP flat; range ≥ 0.25% wide | D only |
| **News-driven** | News-analyst condition is news-driven and a major release or speaker is within 60 min, or was in the last 30 min | E only; size halved |
| **Unclear** | None of the above clearly | STAND ASIDE |

Fridays and the afternoon before NFP/CPI/FOMC: use half size for every strategy.

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

## 3. Always stand aside when
- The data-quality check fails (stale, interpolated, or mismatched prices).
- Inside a blackout, or after 3:30 PM ET.
- Two losing trades already today (in any book), or the daily loss limit is hit.
- The setup would re-enter a trade that just stopped out on the same level.
- The regime changed within the last 15 minutes (wait for it to settle).
