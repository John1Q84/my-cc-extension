#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────
# oh-my-cc-agent installer
# Usage: ./code/script/install-service.sh
# ──────────────────────────────────────────────

LABEL="com.oh-my-cc-agent"
PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/oh-my-cc-agent"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"

# 프로젝트 루트 자동 감지 (script/ 기준 2단계 상위)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
APP_DIR="$PROJECT_ROOT/code/app"
VENV_DIR="$APP_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
APPROVAL_SERVER="$APP_DIR/approval_server.py"
PLIST_TEMPLATE="$SCRIPT_DIR/com.oh-my-cc-agent.plist"

echo "=== oh-my-cc-agent installer ==="
echo "Project root: $PROJECT_ROOT"

# ── 1. 필수 환경변수 검증 ──
if [ -z "${SLACK_APPROVAL_BOT_TOKEN:-}" ]; then
    echo "ERROR: SLACK_APPROVAL_BOT_TOKEN is not set"
    echo "  export SLACK_APPROVAL_BOT_TOKEN=\"xoxb-...\""
    exit 1
fi
if [ -z "${SLACK_APPROVAL_CHANNEL_ID:-}" ]; then
    echo "ERROR: SLACK_APPROVAL_CHANNEL_ID is not set"
    echo "  export SLACK_APPROVAL_CHANNEL_ID=\"C...\""
    exit 1
fi

SLACK_APPROVAL_AWS_REGION="${SLACK_APPROVAL_AWS_REGION:-ap-northeast-2}"
DYNAMODB_TABLE="${DYNAMODB_TABLE:-claude-approval-requests}"

# ── 2. Python venv 생성 및 의존성 설치 ──
if [ ! -f "$VENV_PYTHON" ]; then
    echo "Creating Python venv..."
    python3 -m venv "$VENV_DIR"
fi
echo "Installing dependencies..."
"$VENV_PYTHON" -m pip install -q -r "$APP_DIR/requirements.txt"

# ── 3. 기존 서비스 중지 (있으면) ──
if launchctl list | grep -q "$LABEL" 2>/dev/null; then
    echo "Stopping existing service..."
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

# ── 4. plist 생성 (템플릿 치환) ──
echo "Generating plist..."
mkdir -p "$LOG_DIR"
sed \
    -e "s|__VENV_PYTHON__|$VENV_PYTHON|g" \
    -e "s|__APPROVAL_SERVER_PY__|$APPROVAL_SERVER|g" \
    -e "s|__APP_DIR__|$APP_DIR|g" \
    -e "s|__SLACK_APPROVAL_BOT_TOKEN__|$SLACK_APPROVAL_BOT_TOKEN|g" \
    -e "s|__SLACK_APPROVAL_CHANNEL_ID__|$SLACK_APPROVAL_CHANNEL_ID|g" \
    -e "s|__SLACK_APPROVAL_AWS_REGION__|$SLACK_APPROVAL_AWS_REGION|g" \
    -e "s|__DYNAMODB_TABLE__|$DYNAMODB_TABLE|g" \
    -e "s|__HOME__|$HOME|g" \
    -e "s|__LOG_DIR__|$LOG_DIR|g" \
    "$PLIST_TEMPLATE" > "$PLIST_DEST"

# ── 5. LaunchAgent 등록 및 시작 ──
echo "Loading LaunchAgent..."
launchctl load "$PLIST_DEST"

# ── 6. Health check (최대 10초 대기) ──
echo -n "Waiting for server..."
for i in $(seq 1 10); do
    if curl -sf http://localhost:8080/health > /dev/null 2>&1; then
        echo " OK"
        break
    fi
    echo -n "."
    sleep 1
done
if ! curl -sf http://localhost:8080/health > /dev/null 2>&1; then
    echo " FAILED"
    echo "Check logs: tail -f $LOG_DIR/stderr.log"
    exit 1
fi

# ── 7. Claude Code global hook 등록 ──
if [ -f "$CLAUDE_SETTINGS" ]; then
    # jq가 있는지 확인
    if ! command -v jq &> /dev/null; then
        echo "WARNING: jq not found. Claude Code hook을 수동 등록하세요."
        echo "  ~/.claude/settings.json의 hooks에 PermissionRequest 추가 필요"
    else
        # PermissionRequest hook이 이미 있는지 확인
        if jq -e '.hooks.PermissionRequest' "$CLAUDE_SETTINGS" > /dev/null 2>&1; then
            echo "PermissionRequest hook already exists in $CLAUDE_SETTINGS — skipping"
        else
            echo "Adding PermissionRequest hook to $CLAUDE_SETTINGS..."
            HOOK='[{"matcher":"","hooks":[{"type":"http","url":"http://localhost:8080/hook","timeout":300}]}]'
            jq --argjson hook "$HOOK" '.hooks.PermissionRequest = $hook' "$CLAUDE_SETTINGS" > "${CLAUDE_SETTINGS}.tmp" \
                && mv "${CLAUDE_SETTINGS}.tmp" "$CLAUDE_SETTINGS"
            echo "Hook registered."
        fi
    fi
else
    echo "WARNING: $CLAUDE_SETTINGS not found. Claude Code hook을 수동 등록하세요."
fi

echo ""
echo "=== Installation complete ==="
echo "Service: launchctl list | grep $LABEL"
echo "Logs:    tail -f $LOG_DIR/stderr.log"
echo "Health:  curl http://localhost:8080/health"
