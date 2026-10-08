#!/usr/bin/env bash
# Runs the trading desk 24/7 on a small Linux cloud server (Ubuntu 22.04 or 24.04), so the laptop
# can be off. Run on the server as a normal user with sudo:
#   curl -fsSL https://raw.githubusercontent.com/kayaanali1958-eng/Claude/claude/robinhood-trading-mcp-0z7yb0/scripts/setup_server.sh | bash
# Then follow the three steps it prints at the end (log in to Claude, paste .env, turn the laptop desk off).
set -eu
BRANCH=claude/robinhood-trading-mcp-0z7yb0
DESK="$HOME/trading-desk"

echo "== Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq git python3 python3-venv python3-pip curl psmisc ca-certificates >/dev/null
if ! command -v node >/dev/null || [ "$(node -v | cut -c2- | cut -d. -f1)" -lt 18 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - >/dev/null
  sudo apt-get install -y -qq nodejs >/dev/null
fi

if [ "$(awk '/MemTotal/ {print $2}' /proc/meminfo)" -lt 2000000 ] && [ ! -f /swapfile ]; then
  echo "== Small server (free tier): adding 2 GB of swap so the daily backtests fit in memory"
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== Installing Claude Code (places the live orders through the Robinhood connector)"
sudo npm install -g @anthropic-ai/claude-code >/dev/null

echo "== Getting the desk"
if [ ! -d "$DESK/.git" ]; then
  git clone -q -b "$BRANCH" https://github.com/kayaanali1958-eng/Claude "$DESK"
fi
cd "$DESK"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q yfinance pandas tabulate websockets

echo "== Live crypto/funds mode on this server"
sed -i 's/^CRYPTO_MODE: .*/CRYPTO_MODE: live/' settings.md
touch .env
mkdir -p logs

echo "== Firewall: only SSH open, so the dashboard (port 8765) is not public"
sudo apt-get install -y -qq ufw >/dev/null
sudo ufw allow OpenSSH >/dev/null
sudo ufw --force enable >/dev/null

echo "== Timer: the desk every 5 minutes (same as the laptop's ClaudeCryptoDesk task)"
LINE="*/5 * * * * cd $DESK && PATH=/usr/local/bin:/usr/bin:/bin .venv/bin/python scripts/crypto_desk.py >> logs/crypto_desk.log 2>&1 # trading-desk"
( crontab -l 2>/dev/null | grep -v '# trading-desk' ; echo "$LINE" ) | crontab -

cat <<'DONE'

==========================================================================
 Installed. Three steps left:

 1) Log in to Claude (the same account as the laptop, so the Robinhood
    connector comes with it):
        claude
    follow the login link, then type /exit.

 2) Paste your settings: on the LAPTOP run   Get-Content .env
    then on this server run   nano ~/trading-desk/.env
    paste everything, save with Ctrl+O, Enter, exit with Ctrl+X.
    (Also copy crypto_live_state.json the same way if a trade is open.)

 3) Turn OFF the laptop desk so the two never trade at the same time.
    On the laptop:   schtasks /Change /TN ClaudeCryptoDesk /DISABLE

 Dashboard on your phone from anywhere (private): install the free Tailscale
 app on your phone and run   curl -fsSL https://tailscale.com/install.sh | sh
 then   sudo tailscale up   here, and open  http://<server-tailscale-ip>:8765
 (allow it once with:  sudo ufw allow in on tailscale0 to any port 8765)

 Check it any time:   tail -n 5 ~/trading-desk/logs/crypto_desk.log
 Turn it off:         crontab -l | grep -v '# trading-desk' | crontab -
==========================================================================
DONE
