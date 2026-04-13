# Implementation Plan

## 목표

Claude Code의 `PermissionRequest` hook을 Slack과 연동하여, Claude가 권한을 요청할 때 Slack 메시지로 알림을 받고 버튼으로 Approve/Deny 할 수 있는 시스템 구축.

## 구성 요소

| 컴포넌트 | 기술 | 위치 |
|---------|------|------|
| Approval Server | Python (FastAPI) | Local |
| Webhook Handler | Python (Lambda) | AWS |
| Slack Bot | Slack App (Bot Token) | Slack |
| Approval State | DynamoDB | AWS |
| Webhook Endpoint | API Gateway (HTTP API) | AWS |

---

## Phase 1: AWS 인프라 구성

### 1-1. DynamoDB 테이블 생성

```
테이블명: claude-approval-requests
PK: approval_id (String)
TTL 속성: expires_at (Number, epoch seconds)
```

- TTL: 10분 (미응답 시 자동 삭제)
- 저장 항목: `approval_id`, `status` (pending/approved/denied), `tool_name`, `tool_input`, `expires_at`

### 1-2. Lambda 함수 작성

- 트리거: API Gateway POST `/slack/interact`
- 역할: Slack Interactive payload 파싱 → DynamoDB UpdateItem
- 런타임: Python 3.12
- 위치: `code/app/lambda_handler.py`

### 1-3. API Gateway 설정

- 타입: HTTP API (REST API보다 단순, 비용 저렴)
- 라우트: `POST /slack/interact`
- 통합: Lambda
- 이 URL을 Slack App의 Interactivity Request URL로 등록

---

## Phase 2: Slack App 설정

### 2-1. Slack App 생성

1. https://api.slack.com/apps → Create New App
2. **Bot Token Scopes** 추가:
   - `chat:write` (메시지 전송)
3. **Interactivity & Shortcuts** 활성화:
   - Request URL: `https://<api-gw-id>.execute-api.<region>.amazonaws.com/slack/interact`
4. App을 워크스페이스에 설치 → `Bot User OAuth Token` 복사

### 2-2. 환경변수 설정

```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C...
DYNAMODB_TABLE=claude-approval-requests
AWS_REGION=ap-northeast-2
```

---

## Phase 3: Approval Server (로컬) 작성

- 위치: `code/app/approval_server.py`
- 역할:
  1. `POST /hook` 수신 (Claude Code PermissionRequest hook)
  2. DynamoDB에 `pending` 상태로 approval_id 저장
  3. Slack에 Approve/Deny 버튼 메시지 전송
  4. DynamoDB polling (1초 간격, 최대 5분)
  5. 결과를 Claude Code에 HTTP 응답으로 반환

### 응답 형식 (Claude Code PermissionRequest hook 스펙)

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": {
      "behavior": "allow"
    }
  }
}
```

---

## Phase 4: Claude Code Hook 설정

`~/.claude/settings.json`에 추가:

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

---

## Phase 5: 실행 방법

```bash
# 1. 환경변수 설정
cp code/app/.env.example code/app/.env
# .env 파일에 SLACK_BOT_TOKEN, SLACK_CHANNEL_ID 등 입력

# 2. Approval Server 실행
cd code/app
pip install -r requirements.txt
python approval_server.py

# 3. Claude Code 실행 (별도 터미널)
claude
```

---

## 파일 구조

```
slack-approval/
├── README.md
├── docs/
│   ├── architecture.md
│   └── plan.md                  ← 이 파일
├── code/
│   ├── app/
│   │   ├── approval_server.py   ← 로컬 FastAPI 서버
│   │   ├── lambda_handler.py    ← AWS Lambda
│   │   ├── requirements.txt
│   │   └── .env.example
│   └── terraform/               ← DynamoDB + API GW + Lambda IaC
│       ├── main.tf
│       ├── variables.tf
│       └── outputs.tf
└── parking_lot/
```

---

## 구현 순서 요약

```
1. Terraform으로 DynamoDB + Lambda + API GW 배포
2. Slack App 생성 및 API GW URL 등록
3. approval_server.py 작성 및 로컬 실행
4. ~/.claude/settings.json에 hook 등록
5. Claude Code 실행 후 동작 확인
```

---

## 고려사항

- **timeout**: hook timeout을 300초(5분)로 설정. 미응답 시 자동 deny 처리
- **보안**: Slack Signing Secret으로 webhook 요청 검증 (Lambda에서 처리)
- **로컬 서버 자동 시작**: launchd(macOS) 또는 별도 스크립트로 백그라운드 실행 가능
- **멀티 요청**: approval_id(UUID)로 동시 다중 요청 구분 가능
