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

Paper books (where trades are executed):
- QQQM: trades QQQ signals. QQQM is the same Nasdaq-100 fund at about 41% of the price. Levels are mapped by price ratio.
- TQQQ: trades QQQ signals. TQQQ is a 3x daily leveraged Nasdaq-100 fund. Levels are mapped by 3x percentage move.
- SPY signals have no vehicle a $500 book can afford; log them as "signal only" for reference.

### Level mapping (done by risk-manager at decision time)
- ratio vehicle: level_v = level_QQQ × (price_v_now ÷ price_QQQ_now)
- 3x vehicle: level_v = price_v_now × (1 + 3 × (level_QQQ ÷ price_QQQ_now − 1))
Round to cents.

Account: Agentic (Robinhood account ending 0701, the only account the agent may trade)

## Hard rules
- No margin: a position's total cost must never exceed the book's cash.
- Long only. Shorting needs margin, so a bearish bias means no trade.
- No options. No crypto. No overnight holds.
- No new entries after 3:30 PM ET. Close everything by 3:55 PM ET.
- Pattern day trader rule: the account is a margin-type account. If account equity is under $25,000, allow at most 3 day trades in any rolling 5 business days. In paper mode, track it anyway and log when a signal would have been blocked by it, but still paper-trade it.
- Never widen a stop. Never add to a loser.
