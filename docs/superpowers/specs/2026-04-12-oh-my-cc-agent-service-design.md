# oh-my-cc-agent 서비스 설계

## 목표

Claude Code Approval Server(`approval_server.py`)를 macOS LaunchAgent로 등록하여 로그인 시 자동 구동되고, 크래시 시 자동 복구되도록 한다. `install-service.sh` 한 줄로 venv 생성, 의존성 설치, LaunchAgent 등록, Claude Code hook 설정까지 완료한다.

## 서비스 구성

| 항목 | 값 |
|------|-----|
| 서비스명 | `oh-my-cc-agent` |
| LaunchAgent Label | `com.oh-my-cc-agent` |
| plist 위치 | `~/Library/LaunchAgents/com.oh-my-cc-agent.plist` |
| 실행 바이너리 | `<project>/code/app/.venv/bin/python` |
| 실행 대상 | `<project>/code/app/approval_server.py` |
| 포트 | `8080` |
| 자동 시작 | `RunAtLoad: true` |
| 크래시 복구 | `KeepAlive: true` |
| stdout 로그 | `~/Library/Logs/oh-my-cc-agent/stdout.log` |
| stderr 로그 | `~/Library/Logs/oh-my-cc-agent/stderr.log` |

## 환경변수

plist의 `EnvironmentVariables`로 직접 주입한다. `.zshrc`에 의존하지 않는다.

| 변수 | 필수 | 설명 |
|------|------|------|
| `SLACK_APPROVAL_BOT_TOKEN` | Y | Slack Bot User OAuth Token |
| `SLACK_APPROVAL_CHANNEL_ID` | Y | 알림을 보낼 Slack 채널 ID |
| `SLACK_APPROVAL_AWS_REGION` | N | DynamoDB 리전 (기본: `ap-northeast-2`) |
| `DYNAMODB_TABLE` | N | DynamoDB 테이블명 (기본: `claude-approval-requests`) |

`install-service.sh` 실행 시 현재 쉘의 환경변수에서 읽어 plist에 주입한다.

AWS 자격증명은 `~/.aws/credentials`를 통해 자동 로드된다 (LaunchAgent는 사용자 컨텍스트에서 실행되므로 별도 설정 불필요).

## 파일 구조

```
code/
├── app/
│   ├── approval_server.py        # 기존 (변경 없음)
│   ├── requirements.txt
│   ├── .env.example
│   └── .venv/
└── script/
    ├── install-service.sh         # 설치: venv + pip + plist + launchctl + hook
    ├── uninstall-service.sh       # 제거: launchctl unload + plist 삭제
    └── com.oh-my-cc-agent.plist   # plist 템플릿
```

## install-service.sh 동작

1. 프로젝트 루트 경로 자동 감지 (`script` 위치 기준 상대 경로)
2. 필수 환경변수 검증 (`SLACK_APPROVAL_BOT_TOKEN`, `SLACK_APPROVAL_CHANNEL_ID`)
3. Python venv 생성 (`code/app/.venv`) — 이미 있으면 스킵
4. `pip install -r requirements.txt`
5. plist 템플릿에서 경로/환경변수를 치환하여 `~/Library/LaunchAgents/`에 복사
6. 로그 디렉토리 생성 (`~/Library/Logs/oh-my-cc-agent/`)
7. `launchctl load` 실행
8. health check (`curl localhost:8080/health`) 로 구동 확인
9. Claude Code hook 설정 — 글로벌 `~/.claude/settings.json`에 PermissionRequest hook 추가 (이미 있으면 스킵). 글로벌 등록이므로 모든 프로젝트에서 Slack 승인이 활성화됨

## uninstall-service.sh 동작

1. `launchctl unload ~/Library/LaunchAgents/com.oh-my-cc-agent.plist`
2. plist 파일 삭제
3. (선택) `~/.claude/settings.json`에서 hook 제거

## plist 템플릿 (com.oh-my-cc-agent.plist)

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

`install-service.sh`가 `__PLACEHOLDER__`를 실제 값으로 `sed` 치환하여 설치한다.

## Claude Code Hook 설정

`install-service.sh`가 글로벌 `~/.claude/settings.json`에 아래 hook을 자동 등록한다. 글로벌 설정이므로 모든 Claude Code 프로젝트에서 Slack 승인이 활성화된다. 특정 프로젝트에서만 사용하려면 해당 프로젝트의 `.claude/settings.local.json`에 수동 등록한다.

```json
{
  "hooks": {
    "PermissionRequest": [
      {
        "hooks": [
          {
            "type": "http",
            "url": "http://localhost:8080/hook",
            "timeout": 300
          }
        ]
      }
    ]
  }
}
```

## 동료 설치 가이드 (README에 추가)

```bash
# 1. repo clone
git clone <repo-url>
cd oh-my-cc-agent

# 2. 환경변수 설정 (Slack token, channel ID)
export SLACK_APPROVAL_BOT_TOKEN="xoxb-..."
export SLACK_APPROVAL_CHANNEL_ID="C..."

# 3. 설치 (venv + LaunchAgent + hook 설정)
./code/script/install-service.sh

# 4. 확인
curl http://localhost:8080/health
launchctl list | grep oh-my-cc-agent
```

## 관리 명령

```bash
# 서비스 상태 확인
launchctl list | grep oh-my-cc-agent

# 수동 중지/시작
launchctl unload ~/Library/LaunchAgents/com.oh-my-cc-agent.plist
launchctl load   ~/Library/LaunchAgents/com.oh-my-cc-agent.plist

# 로그 확인
tail -f ~/Library/Logs/oh-my-cc-agent/stderr.log

# 완전 제거
./code/script/uninstall-service.sh
```
