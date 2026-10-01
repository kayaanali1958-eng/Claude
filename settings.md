# Desk Settings

The desk manager and every subagent read this file at the start of every run. Change values here only.

MODE: paper
<!-- paper = simulate and log every order, place nothing. live = place real orders. -->
<!-- The timer script only grants the order-placing tools when this line reads exactly "MODE: live". -->

Trading capital: $500 per paper book
<!-- Each paper book below starts with this amount and is tracked separately. -->

Max risk per trade: 1%
Max daily loss: $15 per book
Max trades per day: 3 (signals; one signal opens a trade in every book)

## Instruments
Signal symbols (analysis only): SPY, QQQ

Strategies: see strategies.md (regime first, then the allowed strategies).

Paper books (where trades are executed). Every QQQ signal opens a trade in both books:

| Book | Bullish QQQ signal buys | Bearish QQQ signal buys |
|---|---|---|
| QQQM book (1x) | QQQM (same fund as QQQ, ~41% of the price) | PSQ (1x inverse Nasdaq-100) |
| TQQQ book (3x) | TQQQ (3x Nasdaq-100) | SQQQ (3x inverse Nasdaq-100) |

- SPY signals have no vehicle a $500 book can afford; log them as "signal only" for reference.

### Level mapping (done by risk-manager at decision time)
Let m = level_QQQ ÷ price_QQQ_now − 1 (the level's % distance from QQQ now).
- QQQM: level_v = level_QQQ × (price_QQQM_now ÷ price_QQQ_now)
- TQQQ: level_v = price_TQQQ_now × (1 + 3m)
- PSQ:  level_v = price_PSQ_now × (1 − m)
- SQQQ: level_v = price_SQQQ_now × (1 − 3m)
For a bearish signal the QQQ stop is above entry, so the inverse stop lands below entry, which makes it an ordinary long stop. Round to cents.

Account: Agentic (Robinhood account ending 0701, the only account the agent may trade)

## Hard rules
- No margin: a position's total cost must never exceed the book's cash.
- Buy only, never short (shorting needs margin). Bearish setups are taken by buying the inverse ETF.
- No options. No crypto. No overnight holds.
- No new entries after 3:30 PM ET. Close everything by 3:55 PM ET.
- Pattern day trader rule: the account is a margin-type account. If account equity is under $25,000, allow at most 3 day trades in any rolling 5 business days. In paper mode, track it anyway and log when a signal would have been blocked by it, but still paper-trade it.
- Never widen a stop. Never add to a loser.
