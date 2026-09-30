#!/bin/bash
# Install the weekly price update on this Mac.
# Run from the project folder:   bash scheduler/macos/install.sh
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/../.." && pwd)"
LABEL="com.booktracker.weekly"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ -x "$PROJECT/.venv/bin/python" ]; then
  PYTHON="$PROJECT/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

case "$PROJECT" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*)
    echo "⚠️  The project is inside Documents/Desktop/Downloads. macOS privacy protection can stop"
    echo "   background jobs from reading those folders. Moving it to e.g. ~/BookTracker is safer."
    ;;
esac

echo "Project: $PROJECT"
echo "Python:  $PYTHON"
"$PYTHON" -c "import booktracker" 2>/dev/null || { echo "❌ Python can't import the project - did you run 'pip install -r requirements.txt'?"; exit 1; }

mkdir -p "$PROJECT/logs" "$HOME/Library/LaunchAgents"
sed -e "s|__PROJECT__|$PROJECT|g" -e "s|__PYTHON__|$PYTHON|g" \
    "$PROJECT/scheduler/macos/$LABEL.plist" > "$TARGET"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"
launchctl enable "gui/$(id -u)/$LABEL"

echo "✅ Installed. Prices will update every Sunday at 09:00 (or at the next login/wake if missed)."
echo "   Logs:        $PROJECT/logs/update_prices.log"
echo "   Run it now:  launchctl kickstart -k gui/$(id -u)/$LABEL"
echo "   Remove it:   bash scheduler/macos/uninstall.sh"
