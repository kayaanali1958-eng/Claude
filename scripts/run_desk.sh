#!/usr/bin/env bash
# One desk-manager run. The timer calls this every 5 minutes; it exits early
# outside Mon–Fri 9:00 AM–4:05 PM ET, so the OS schedule doesn't need to know about time zones.
set -u
DESK_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DESK_DIR" || exit 1
CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude || echo claude)}"

DOW=$(TZ=America/New_York date +%u)          # 1=Mon … 7=Sun
HM=$(TZ=America/New_York date +%H%M)
NOW_ET=$(TZ=America/New_York date '+%Y-%m-%d %H:%M %Z (%A)')
[ "$DOW" -le 5 ] || exit 0
[ "$HM" -ge 0900 ] && [ "$HM" -le 1605 ] || exit 0
[ -f "$DESK_DIR/STOP" ] && exit 0            # kill switch: touch STOP

# Skip if the previous run is still going (stale after 20 minutes).
LOCK="$DESK_DIR/.desk.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +20 2>/dev/null)" ]; then rm -rf "$LOCK"; mkdir "$LOCK" || exit 0
  else exit 0; fi
fi
trap 'rm -rf "$LOCK"' EXIT

# Order-placing tools are granted only when settings.md says exactly "MODE: live".
RH_READ="get_accounts get_portfolio get_equity_positions get_equity_orders get_equity_quotes get_equity_historicals get_index_quotes get_index_historicals get_earnings_calendar get_politician_trades get_equity_fundamentals get_earnings_results get_equity_analyst_ratings get_scans get_scanner_filter_specs get_scanner_datapoints preview_scan run_scan review_equity_order"
RH_WRITE="place_equity_order cancel_equity_order"
RH_TOOLS="$RH_READ"
if grep -qx 'MODE: live' settings.md; then RH_TOOLS="$RH_READ $RH_WRITE"; fi

ALLOWED="Read Write Edit Task Agent WebSearch WebFetch"
for p in mcp__robinhood-trading__ mcp__RobinHood__ mcp__claude_ai_RobinHood__; do
  for t in $RH_TOOLS; do ALLOWED="$ALLOWED $p$t"; done
done

mkdir -p logs news
# Keep the live news listener running (no-op if already running or no keys in .env).
if [ -f .env ] && ! { [ -f news/listener.pid ] && kill -0 "$(cat news/listener.pid)" 2>/dev/null; }; then
  nohup python3 scripts/news_listener.py >> logs/news_listener.log 2>&1 &
  echo $! > news/listener.pid
fi
# Give the agents the newest 200 items.
[ -f news/live.jsonl ] && tail -n 200 news/live.jsonl > news/latest.jsonl
# Every weekday after the close: rebuild the playbook with today's data before the review.
BT_SYMBOLS="SPY QQQ NVDA AAPL MSFT AMZN META TSLA AMD GOOGL"
if [ "$HM" -ge 1600 ] && [ ! -f "backtests/report_$(date +%F).md" ]; then
  python3 scripts/backtest.py $BT_SYMBOLS >> logs/backtest.log 2>&1
fi
LOG="logs/desk-$(TZ=America/New_York date +%F).log"
echo "===== run $NOW_ET =====" >> "$LOG"
"$CLAUDE_BIN" -p "Desk run. Current time: $NOW_ET. Follow CLAUDE.md exactly for one run, then stop." \
  --allowedTools $ALLOWED \
  --permission-mode dontAsk \
  >> "$LOG" 2>&1
RC=$?
echo "===== exit $RC =====" >> "$LOG"
python3 scripts/notify.py "$RC" >> logs/notify.log 2>&1
