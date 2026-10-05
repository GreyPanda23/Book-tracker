#!/bin/bash
# Stop and remove the weekly price update from this Mac.
LABEL="com.booktracker.weekly"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/com.booktracker.sync" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/com.booktracker.sync.plist"
echo "Removed the weekly price update and the database sync."
