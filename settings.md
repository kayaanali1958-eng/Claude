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

## Stocks book (paper)
Start: $500 paper. Trades the market-scanner's "stocks in play" (up to 5 symbols a day), the actual shares.
- Bullish setups only: no shorting, and no inverse funds for single stocks. Bearish stock setups are logged as signal only.
- Per-trade cash limit: the whole book ($500), so the scanner only picks stocks priced $10–$500.
- Same risk rules as the other books: 1% risk ($5), half size where strategies.md says so, max daily loss $15, max 3 trades a day.
- Single stocks move more than ETFs: the stop must sit beyond the setup's invalidation level and at least 0.3% from entry.

### Level mapping (done by risk-manager at decision time)
Let m = level_QQQ ÷ price_QQQ_now − 1 (the level's % distance from QQQ now).
- QQQM: level_v = level_QQQ × (price_QQQM_now ÷ price_QQQ_now)
- TQQQ: level_v = price_TQQQ_now × (1 + 3m)
- PSQ:  level_v = price_PSQ_now × (1 − m)
- SQQQ: level_v = price_SQQQ_now × (1 − 3m)
For a bearish signal the QQQ stop is above entry, so the inverse stop lands below entry, which makes it an ordinary long stop. Round to cents.

## Long-term book (paper, run by portfolio-manager)
Start: $500 paper. Monthly contribution: $100 (paper), invested on the first trading day of each month.
Core fund: SPY (at least 80% of the book). Satellites: congress-trades ideas scoring 4–5, max 20% of the book and 5% per name.
This book is separate from the day-trading books and never uses leverage, inverse funds or options.

Account: Agentic (Robinhood account ending 0701, the only account the agent may trade)

CRYPTO_MODE: paper
<!-- paper = simulate. live = real crypto orders on the Agentic account through Claude Code, capped at CRYPTO_LIVE_MAX in .env (a dollar amount, or all = the whole account cash, re-checked daily). -->

## Crypto book (run by scripts/crypto_desk.py: open positions every 5 minutes, new trades on each hourly close, 24/7)
Paper start: $100. Live: the book is CRYPTO_LIVE_MAX from .env: a dollar amount (the most the desk may ever put in), or `all` (the whole account cash, never margin, re-synced once a day so deposits are used), tracked in `crypto_live_state.json` / `crypto_live_journal.md`. Every live buy gets a real stop order on Robinhood immediately. Coins traded: BTC, ETH, XRP, DOGE, AVAX, LTC (fractional amounts). SOL and LINK are learned from but not traded (the desk did better without them).
- Trades only when a rule in `backtests/crypto_playbook.json` matches the coin's current situation (trend, volatility, session, weekend, BTC trend). The playbook is rebuilt weekly from 2 years of hourly data.
- Long only, no leverage (Robinhood crypto has none). 1% risk per trade, at most 2 open positions, max hold 48 hours.
- 2x funds: `CRYPTO_LEVERAGE=2` in .env. During US market hours (entries 10:00-15:00 ET) a signal on BTC/ETH/XRP buys whole shares of BITX/ETHU/XXRP (2x daily funds) with a real stop order; exits follow the coin, and an exit while the stock market is closed is sold at the next open (gap risk). Replay 2024-26: about double the profit of coins, but worst drop 63% and worst trade -36%.
- Swing, not day trading: forcing exits by 3 PM lost money in every replay; the setups need time to run.
- Live sizing: `CRYPTO_SIZE=all` in .env puts all the book's cash into each trade (so one trade at a time; the loss on a trade is the stop distance, usually 1–4% of the book). Empty = 1% risk.
- Stops opening trades for the day after a 3% loss; pauses everything after a 10% drop from the book's peak (phone alert).
- Costs: 0.2% per buy and per sell is assumed (Robinhood's spread).
- Log: `crypto_journal.md`; state: `crypto_state.json`.

## The plan: $1,000, then long-term funds
- **Phase 1 (until the Agentic account is worth $1,000):** the crypto desk grows the cash. It checks the whole account value once a day and alerts at $100, $250, $500 and $1,000.
- **Phase 2 (from $1,000):** split into long-term funds, run by scripts/ai_portfolio.py: S&P 500 (VOO) 35%, Nasdaq-100 (QQQM) 20%, chip makers (SMH) 15%, and an AI slice of 30%: the 4 strongest AI build-out stocks (chips, memory, chip equipment, networking/servers, power/cooling, data centers, cloud builders) above their 200-day average, ranked by 6-month return, weekly; an AI stock is sold on a 15% drop from its high. Funds are rebalanced weekly when they drift.
- Until the switch it runs as paper (AI_PORTFOLIO_START in .env, default $1,000) so the plan is already tested. Backtest: backtests/ai_portfolio_backtest.md.

## Evidence gate
- Paper mode: every strategy may trade, so the desk keeps collecting evidence.
- **Live mode: only setups that match a rule in `backtests/playbook.json`** (strategy, side, situation and exit). Everything else is signal only. The playbook is rebuilt every Friday after the close.
- Market fear check: if the VIX is above 30, every trade is half size; above 40, no new trades.

## Hard rules
- No margin: a position's total cost must never exceed the book's cash.
- Buy only, never short (shorting needs margin). Bearish setups are taken by buying the inverse ETF.
- No options. No overnight holds. No crypto except the crypto paper book below.
- No new entries after 3:30 PM ET. Close everything by 3:55 PM ET.
- Pattern day trader rule: the account is a margin-type account. If account equity is under $25,000, allow at most 3 day trades in any rolling 5 business days. In paper mode, track it anyway and log when a signal would have been blocked by it, but still paper-trade it.
- Never widen a stop. Never add to a loser.
