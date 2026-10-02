#!/usr/bin/env bash
# Safe one-command update for the desk (Mac/Linux). Keeps your journal, state, lessons and keys.
set -e
cd "$(dirname "$0")/.."
mkdir -p news/update_backup
for f in desk_state.json journal.md lessons.md crypto_state.json crypto_journal.md; do [ -f "$f" ] && cp "$f" news/update_backup/; done
git fetch origin claude/robinhood-trading-mcp-0z7yb0
git reset --hard origin/claude/robinhood-trading-mcp-0z7yb0
for f in desk_state.json journal.md lessons.md crypto_state.json crypto_journal.md; do [ -f "news/update_backup/$f" ] && cp "news/update_backup/$f" .; done
pip install -q yfinance pandas tabulate websockets
echo "Updated. Your journal, state, lessons and .env were kept."
