#!/usr/bin/env bash
# Installs the 5-minute desk timer on macOS (launchd) or Linux (cron).
# Windows: run scripts/install_timer.ps1 instead.
set -eu
DESK_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLAUDE_BIN="$(command -v claude)" || { echo "claude not found on PATH"; exit 1; }
RUNNER="$DESK_DIR/scripts/run_desk.sh"
chmod +x "$RUNNER"

case "$(uname -s)" in
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/com.claude.tradingdesk.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.claude.tradingdesk</string>
  <key>ProgramArguments</key><array><string>$RUNNER</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>CLAUDE_BIN</key><string>$CLAUDE_BIN</string>
    <key>PATH</key><string>$(dirname "$CLAUDE_BIN"):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartInterval</key><integer>300</integer>
  <key>WorkingDirectory</key><string>$DESK_DIR</string>
</dict></plist>
PLIST
    launchctl bootout "gui/$(id -u)/com.claude.tradingdesk" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    CPLIST="$HOME/Library/LaunchAgents/com.claude.cryptodesk.plist"
    cat > "$CPLIST" <<CPLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.claude.cryptodesk</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>-c</string><string>mkdir -p logs; python3 scripts/crypto_desk.py >> logs/crypto_desk.log 2>&1</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>WorkingDirectory</key><string>$DESK_DIR</string>
</dict></plist>
CPLIST
    launchctl bootout "gui/$(id -u)/com.claude.cryptodesk" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$CPLIST"
    echo "Crypto desk installed (hourly). Turn off with:  launchctl bootout gui/\$(id -u)/com.claude.cryptodesk"
    echo "Installed (macOS launchd). Turn off with:"
    echo "  launchctl bootout gui/\$(id -u)/com.claude.tradingdesk"
    ;;
  Linux)
    LINE="*/5 * * * * CLAUDE_BIN=$CLAUDE_BIN PATH=$(dirname "$CLAUDE_BIN"):/usr/local/bin:/usr/bin:/bin $RUNNER # claude-trading-desk"
    CLINE="*/5 * * * * cd $DESK_DIR && mkdir -p logs && python3 scripts/crypto_desk.py >> logs/crypto_desk.log 2>&1 # claude-crypto-desk"
    ( crontab -l 2>/dev/null | grep -v 'claude-trading-desk' | grep -v 'claude-crypto-desk' ; echo "$LINE" ; echo "$CLINE" ) | crontab -
    echo "Crypto desk installed (hourly). Turn off with:  crontab -l | grep -v claude-crypto-desk | crontab -"
    echo "Installed (cron). Turn off with:"
    echo "  crontab -l | grep -v claude-trading-desk | crontab -"
    ;;
  *) echo "Unsupported OS: $(uname -s). On Windows run scripts/install_timer.ps1"; exit 1 ;;
esac
echo "Pause without uninstalling: touch \"$DESK_DIR/STOP\"   (resume: rm \"$DESK_DIR/STOP\")"
