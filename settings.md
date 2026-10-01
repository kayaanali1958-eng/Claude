# Desk Settings

The desk manager and every subagent read this file at the start of every run. Change values here only.

MODE: paper
<!-- paper = simulate and log every order, place nothing. live = place real orders. -->
<!-- The timer script only grants the order-placing tools when this line reads exactly "MODE: live". -->

Trading capital: $10000
<!-- Paper-mode placeholder. Before switching to live, set this to no more than the Agentic account's -->
<!-- unleveraged buying power. In live mode, risk-manager rejects every trade if capital > unleveraged buying power. -->

Max risk per trade: 1%
Max daily loss: $200
Max trades per day: 3

Instruments: SPY, QQQ

Account: Agentic (Robinhood account ending 0701, the only account the agent may trade)

## Hard rules
- No margin: a position's total cost must never exceed unleveraged buying power (live) or capital (paper).
- Long only. Shorting needs margin, so a bearish bias means no trade.
- No options. No crypto. No overnight holds.
- No new entries after 3:30 PM ET. Close everything by 3:55 PM ET.
- Pattern day trader rule: the account is a margin-type account. If account equity is under $25,000, allow at most 3 day trades in any rolling 5 business days. Any PDT warning from review_equity_order means REJECT.
- Never widen a stop. Never add to a loser.
