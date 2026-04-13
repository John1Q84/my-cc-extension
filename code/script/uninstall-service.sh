#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────
# oh-my-cc-agent uninstaller
# Usage: ./code/script/uninstall-service.sh
# ──────────────────────────────────────────────

LABEL="com.oh-my-cc-agent"
PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"

echo "=== oh-my-cc-agent uninstaller ==="

# ── 1. 서비스 중지 ──
if launchctl list | grep -q "$LABEL" 2>/dev/null; then
    echo "Stopping service..."
    launchctl unload "$PLIST_DEST"
    echo "Service stopped."
else
    echo "Service not running."
fi

# ── 2. plist 삭제 ──
if [ -f "$PLIST_DEST" ]; then
    rm "$PLIST_DEST"
    echo "Removed $PLIST_DEST"
else
    echo "plist not found — already removed."
fi

# ── 3. Claude Code hook 제거 (선택) ──
read -p "Remove PermissionRequest hook from ~/.claude/settings.json? [y/N] " answer
if [[ "$answer" =~ ^[Yy]$ ]]; then
    if command -v jq &> /dev/null && [ -f "$CLAUDE_SETTINGS" ]; then
        jq 'del(.hooks.PermissionRequest)' "$CLAUDE_SETTINGS" > "${CLAUDE_SETTINGS}.tmp" \
            && mv "${CLAUDE_SETTINGS}.tmp" "$CLAUDE_SETTINGS"
        echo "Hook removed."
    else
        echo "WARNING: jq not found or settings.json missing. 수동 제거하세요."
    fi
fi

echo ""
echo "=== Uninstall complete ==="
