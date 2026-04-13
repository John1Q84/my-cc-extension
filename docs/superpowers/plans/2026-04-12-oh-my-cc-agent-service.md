# oh-my-cc-agent 서비스 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** approval_server.py를 macOS LaunchAgent 서비스로 등록하고, install/uninstall 스크립트로 원클릭 설치할 수 있게 한다.

**Architecture:** plist 템플릿 + sed 치환 방식의 install script. 기존 `~/.claude/settings.json`에 PermissionRequest hook을 jq로 병합 추가한다.

**Tech Stack:** bash, launchd(LaunchAgent), jq(JSON 병합), python venv

---

### Task 1: plist 템플릿 작성

**Files:**
- Create: `code/script/com.oh-my-cc-agent.plist`

- [ ] **Step 1: plist 템플릿 파일 생성**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.oh-my-cc-agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>__VENV_PYTHON__</string>
        <string>__APPROVAL_SERVER_PY__</string>
    </array>
    <key>WorkingDirectory</key>
    <string>__APP_DIR__</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>SLACK_APPROVAL_BOT_TOKEN</key>
        <string>__SLACK_APPROVAL_BOT_TOKEN__</string>
        <key>SLACK_APPROVAL_CHANNEL_ID</key>
        <string>__SLACK_APPROVAL_CHANNEL_ID__</string>
        <key>SLACK_APPROVAL_AWS_REGION</key>
        <string>__SLACK_APPROVAL_AWS_REGION__</string>
        <key>DYNAMODB_TABLE</key>
        <string>__DYNAMODB_TABLE__</string>
        <key>HOME</key>
        <string>__HOME__</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>__LOG_DIR__/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>__LOG_DIR__/stderr.log</string>
</dict>
</plist>
```

`HOME` 환경변수를 포함한다 — LaunchAgent에서 `~/.aws/credentials`를 찾기 위해 필요.

- [ ] **Step 2: 파일 생성 확인**

Run: `cat code/script/com.oh-my-cc-agent.plist | head -5`
Expected: XML 헤더와 plist 시작 태그 출력

---

### Task 2: install-service.sh 작성

**Files:**
- Create: `code/script/install-service.sh`

- [ ] **Step 1: install-service.sh 작성**

```bash
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
```

- [ ] **Step 2: 실행 권한 부여**

Run: `chmod +x code/script/install-service.sh`

- [ ] **Step 3: 파일 확인**

Run: `head -5 code/script/install-service.sh`
Expected: shebang과 set 명령 출력

---

### Task 3: uninstall-service.sh 작성

**Files:**
- Create: `code/script/uninstall-service.sh`

- [ ] **Step 1: uninstall-service.sh 작성**

```bash
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
```

- [ ] **Step 2: 실행 권한 부여**

Run: `chmod +x code/script/uninstall-service.sh`

---

### Task 4: 기존 서버 프로세스 정리 후 설치 테스트

- [ ] **Step 1: 기존 프로세스 정리**

현재 수동으로 띄운 approval_server 프로세스가 있으면 종료:

```bash
pkill -f "approval_server" 2>/dev/null || true
```

확인: `curl http://localhost:8080/health` → connection refused

- [ ] **Step 2: install-service.sh 실행**

```bash
source ~/.zshrc  # 환경변수 로드
./code/script/install-service.sh
```

Expected output:
```
=== oh-my-cc-agent installer ===
Project root: ...
Installing dependencies...
Generating plist...
Loading LaunchAgent...
Waiting for server... OK
Adding PermissionRequest hook to ~/.claude/settings.json...
Hook registered.

=== Installation complete ===
```

- [ ] **Step 3: 서비스 상태 확인**

Run: `launchctl list | grep oh-my-cc-agent`
Expected: PID와 함께 `com.oh-my-cc-agent` 출력

Run: `curl -s http://localhost:8080/health`
Expected: `{"status":"ok"}`

- [ ] **Step 4: 글로벌 settings.json에 hook 추가 확인**

Run: `jq '.hooks.PermissionRequest' ~/.claude/settings.json`
Expected:
```json
[
  {
    "matcher": "",
    "hooks": [
      {
        "type": "http",
        "url": "http://localhost:8080/hook",
        "timeout": 300
      }
    ]
  }
]
```

- [ ] **Step 5: 크래시 복구 테스트**

서버 프로세스를 강제 종료 후 자동 재시작 확인:

```bash
pkill -f "approval_server"
sleep 3
curl -s http://localhost:8080/health
```

Expected: `{"status":"ok"}` (KeepAlive가 자동 재시작)

---

### Task 5: README.md 업데이트

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README에 서비스 설치 섹션 추가**

Change Log 바로 위에 아래 섹션 추가:

```markdown
## 서비스 설치 (oh-my-cc-agent)

LaunchAgent로 등록하면 Mac 로그인 시 자동 시작, 크래시 시 자동 복구됩니다.

### 설치

\```bash
# 환경변수 설정 (최초 1회)
export SLACK_APPROVAL_BOT_TOKEN="xoxb-..."
export SLACK_APPROVAL_CHANNEL_ID="C..."

# 설치 (venv + LaunchAgent + Claude Code hook)
./code/script/install-service.sh
\```

### 관리 명령

\```bash
# 상태 확인
launchctl list | grep oh-my-cc-agent
curl http://localhost:8080/health

# 로그 확인
tail -f ~/Library/Logs/oh-my-cc-agent/stderr.log

# 제거
./code/script/uninstall-service.sh
\```
```

- [ ] **Step 2: Change Log 업데이트**

Change Log 테이블에 추가:

```markdown
| 2026-04-12_23:30 | oh-my-cc-agent LaunchAgent 서비스화 + install/uninstall 스크립트 추가 |
```
