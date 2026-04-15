# slack-approval

Claude Code의 권한 요청을 Slack으로 전달하고, Slack에서 Approve/Deny 할 수 있는 시스템.

## 개요

Claude Code 실행 중 파일 쓰기, 명령 실행 등 권한 요청이 발생하면 Slack 메시지로 알림을 받고, 버튼 클릭으로 승인/거부할 수 있다.

### PermissionRequest Hook

Claude Code는 [Hooks](https://docs.anthropic.com/en/docs/claude-code/hooks) 시스템을 통해 특정 이벤트 발생 시 외부 명령이나 HTTP 요청을 실행할 수 있다. 그 중 `PermissionRequest` hook은 Claude Code가 사용자 승인이 필요한 동작(파일 편집, Bash 실행 등)을 수행하기 전에 트리거된다.

이 프로젝트는 `PermissionRequest` hook을 HTTP 타입으로 등록하여, 권한 요청을 로컬 서버로 전달하고 Slack을 통해 원격으로 승인/거부할 수 있게 한다.

### Claude Code Settings 구조

Claude Code는 두 단계의 설정 파일을 지원한다:

| 구분 | 파일 경로 | 적용 범위 | Git 추적 |
|------|----------|----------|----------|
| **Global** | `~/.claude/settings.json` | 모든 프로젝트에 적용 | N/A (홈 디렉토리) |
| **Project Local** | `<project-root>/.claude/settings.local.json` | 해당 프로젝트에만 적용 | **제외 권장** (개인 설정) |

> `~`는 현재 사용자의 홈 디렉토리를 의미한다 (macOS: `/Users/<username>`, Linux: `/home/<username>`).

두 파일에 동일한 key가 있으면 **Project Local이 우선**한다.

#### Global 설정 (`~/.claude/settings.json`)

모든 프로젝트에서 Slack Approval을 사용하려면 global 설정에 hook을 등록한다. `install-service.sh` 스크립트가 자동으로 이 설정을 추가한다.

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

#### Project Local 설정 (`.claude/settings.local.json`)

특정 프로젝트에서만 Slack Approval을 사용하려면 프로젝트 루트에 `.claude/settings.local.json`을 생성한다.

```json
{
  "permissions": {
    "allow": [
      "Bash(npm test:*)",
      "Bash(git status:*)"
    ]
  },
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

**주요 key 설명:**

| Key | 설명 |
|-----|------|
| `permissions.allow` | 자동 허용할 도구/명령 패턴 목록 (이 목록에 매칭되면 hook을 거치지 않고 즉시 허용) |
| `hooks.PermissionRequest` | 권한 요청 시 실행할 hook 배열 |
| `hooks.PermissionRequest[].hooks[].type` | `"http"` — HTTP POST로 hook 호출 |
| `hooks.PermissionRequest[].hooks[].url` | Approval Server 주소 (`http://localhost:8080/hook`) |
| `hooks.PermissionRequest[].hooks[].timeout` | 응답 대기 시간(초). `300` = 5분 |

> **참고**: `settings.local.json`은 개인 환경 설정이므로 `.gitignore`에 포함하여 git에 올리지 않는다.

### Hook 동작 방식

- Hook이 `{"behavior": "allow"}`를 반환하면 승인, `{"behavior": "deny"}`를 반환하면 거부
- 터미널에서 직접 승인/거부하면 hook 연결이 끊기며, 서버가 이를 감지하여 Slack 메시지를 자동 업데이트

## 처음부터 배포하기 (Fresh Deployment)

> 현재 **macOS** 환경을 기준으로 작성되었다 (LaunchAgent 기반 서비스 관리). Linux에서는 systemd 등으로 대체 필요.

### 사전 요구사항

| 도구 | 설치 방법 | 확인 명령 |
|------|----------|-----------|
| Python 3.10+ | `brew install python` | `python3 --version` |
| Terraform 1.0+ | `brew install terraform` | `terraform --version` |
| AWS CLI | `brew install awscli` | `aws --version` |
| jq | `brew install jq` | `jq --version` |

AWS credentials가 설정되어 있어야 한다:

```bash
aws configure
# 또는 ~/.aws/credentials에 프로필 설정
```

### Step 1: Slack App 생성

[Slack App 설정 가이드](docs/slack-app-setup-guide.md)의 Step 1~5를 따라 Slack App을 만들고 토큰을 획득합니다.

### Step 2: 환경변수 설정

셸 프로필(예: `~/.zshrc`, `~/.bashrc`)에 다음을 추가한다:

```bash
export SLACK_APPROVAL_BOT_TOKEN="xoxb-..."
export SLACK_APPROVAL_CHANNEL_ID="C..."
export TF_VAR_slack_signing_secret="..."

# AWS 리전 변경 시 (기본값: ap-northeast-2)
# export SLACK_APPROVAL_AWS_REGION="us-east-1"
```

추가 후 `source ~/.zshrc` (또는 해당 셸 프로필)을 실행하여 반영한다.

> AWS 리전 기본값은 `ap-northeast-2` (서울)이다. Terraform(`code/terraform/variables.tf`)과 Approval Server(`code/app/approval_server.py`) 모두 동일한 리전을 바라보므로, 변경 시 양쪽 모두 맞춰야 한다.

### Step 3: 배포

```bash
./code/script/deploy.sh
```

이 스크립트가 자동으로:
1. 필수 도구 및 환경변수 검증
2. AWS 인프라 배포 (Terraform: DynamoDB + Lambda + API Gateway)
3. 로컬 서비스 설치 (Python venv + LaunchAgent + Claude Code hook)
4. API Gateway URL 출력

### Step 4: Slack Interactivity 설정

`deploy.sh` 출력에 표시된 API Gateway URL을 Slack App의 Interactivity Request URL에 등록한다.
자세한 방법은 [Slack App 설정 가이드 - Step 6](docs/slack-app-setup-guide.md#step-6-interactivity-설정) 참고.

### Step 5: 동작 확인

```bash
# 서비스 상태
curl http://localhost:8080/health

# Claude Code 실행 후 권한 요청 시 Slack 메시지 수신 확인
```

### 제거

```bash
./code/script/teardown.sh
```

## 아키텍처

```mermaid
flowchart LR
    subgraph LOCAL["Local Machine"]
        CC["Claude Code"]
        HS["Approval Server\nlocalhost:8080"]
    end

    subgraph AWS["AWS"]
        DDB["DynamoDB\nTTL 10min"]
        APIGW["API Gateway"]
        LMB["Lambda"]
    end

    subgraph SLACK["Slack"]
        BOT["Slack Bot"]
        USER["User"]
    end

    CC -- "POST /hook\nPermissionRequest" --> HS
    HS -- "chat.postMessage\nApprove/Deny 버튼" --> BOT
    HS -- "PutItem\nstatus=pending" --> DDB
    HS -. "polling\nGetItem 5s" .-> DDB

    BOT --> USER
    USER -- "버튼 클릭" --> BOT
    BOT -- "POST /slack/interact" --> APIGW
    APIGW --> LMB
    LMB -- "UpdateItem\nstatus=approved" --> DDB
    LMB -- "response_url\n메시지 업데이트" --> BOT

    DDB -. "decision" .-> HS
    HS -- "allow / deny" --> CC
```

## Workflow

```mermaid
sequenceDiagram
    participant CC as Claude Code
    participant HS as Approval Server
    participant SL as Slack
    participant AG as API Gateway
    participant LB as Lambda
    participant DB as DynamoDB

    CC->>HS: POST /hook (tool_name, input)
    HS->>DB: PutItem (pending)
    HS->>SL: chat.postMessage (버튼 메시지)

    loop polling (5s, max 5min)
        HS->>DB: GetItem
        alt client disconnected
            Note over HS: Terminal에서 처리됨
            HS->>SL: chat.update (Resolved by terminal)
            HS->>DB: UpdateItem (terminal)
        end
    end

    Note over SL: 사용자 Approve 클릭

    SL->>AG: POST /slack/interact
    AG->>LB: invoke
    LB->>DB: UpdateItem (approved)
    LB->>SL: response_url (버튼 제거 + 결과 표시)

    DB-->>HS: status=approved
    HS-->>CC: {behavior: allow}
```

자세한 내용: [docs/architecture.md](docs/architecture.md)

## 폴더 구조

```
slack-approval/
├── README.md
├── docs/
│   ├── architecture.md        # 아키텍처 다이어그램
│   ├── plan.md                # 구현 계획
│   └── slack-app-setup-guide.md # Slack App 설정 가이드
├── code/
│   ├── app/                          # 기본 리전: ap-northeast-2 (환경변수로 변경 가능)
│   │   ├── approval_server.py # 로컬 FastAPI 서버 (DynamoDB 접근 시 리전 사용)
│   │   ├── lambda_handler.py  # AWS Lambda (Slack webhook)
│   │   ├── requirements.txt   # Python 의존성
│   │   └── .env.example       # 환경변수 템플릿
│   ├── script/
│   │   ├── deploy.sh                # 전체 배포 (prerequisite → Terraform → 서비스 설치)
│   │   ├── teardown.sh              # 전체 제거 (서비스 제거 → Terraform destroy)
│   │   ├── install-service.sh       # 서비스 설치 (venv + LaunchAgent + hook)
│   │   ├── uninstall-service.sh     # 서비스 제거
│   │   └── com.oh-my-cc-agent.plist # LaunchAgent plist 템플릿
│   └── terraform/                    # 기본 리전: ap-northeast-2 (variables.tf에서 변경 가능)
│       ├── main.tf            # DynamoDB + Lambda + API GW
│       ├── variables.tf       # 변수 정의 (aws_region 등)
│       └── outputs.tf         # 출력값
└── parking_lot/               # .gitignore 대상 (git에 올리지 않음)
    └── _security_review/      # 보안 리뷰 결과
```

## 구현 계획

[docs/plan.md](docs/plan.md) 참고.

5단계로 구성:
1. AWS 인프라 (DynamoDB + Lambda + API Gateway)
2. Slack App 설정
3. 로컬 Approval Server 작성
4. Claude Code hook 등록
5. 실행 및 검증

## 서비스 설치 (oh-my-cc-agent)

macOS LaunchAgent로 등록하면 로그인 시 자동 시작, 크래시 시 자동 복구된다.

> `deploy.sh`를 사용한 경우 서비스 설치가 자동으로 포함되므로, 이 섹션은 서비스만 별도로 설치/관리할 때 참고한다.

### 설치

```bash
# 환경변수 설정 (최초 1회, 셸 프로필에 등록 권장)
export SLACK_APPROVAL_BOT_TOKEN="xoxb-..."
export SLACK_APPROVAL_CHANNEL_ID="C..."

# 설치 (venv + LaunchAgent + Claude Code global hook)
./code/script/install-service.sh
```

`install-service.sh`는 `~/.claude/settings.json`에 `PermissionRequest` hook을 자동 등록한다. 특정 프로젝트에서만 사용하려면 위의 [Claude Code Settings 구조](#claude-code-settings-구조) 섹션을 참고하여 `.claude/settings.local.json`에 직접 설정한다.

### 관리 명령

```bash
# 상태 확인
launchctl list | grep oh-my-cc-agent
curl http://localhost:8080/health

# 로그 확인 (macOS 기본 경로: ~/Library/Logs/)
tail -f ~/Library/Logs/oh-my-cc-agent/stderr.log

# 제거
./code/script/uninstall-service.sh
```

## Change Log

| 일시 | 변경사항 |
|------|----------|
| 2026-04-15_21:00 | Public repo 배포 준비: .DS_Store 제거, settings.local.json 가이드 추가, 경로 표현 일반화, 문서 개선 |
| 2026-04-13_21:50 | README 다이어그램(Mermaid) + PermissionRequest Hook 설명 + tfsec/bandit 조치 + terminal disconnect 감지 |
| 2026-04-13_11:50 | /slack-notify global skill 추가: 작업 완료 요약을 Slack 채널로 전송하는 /notify 엔드포인트 + skill |
| 2026-04-13_10:40 | 배포 패키징: deploy.sh/teardown.sh + Slack App 설정 가이드 + README 배포 워크스루 + 버튼 클릭 후 메시지 업데이트 수정 |
| 2026-04-12_23:50 | oh-my-cc-agent LaunchAgent 서비스화 + install/uninstall 스크립트 + context 표시 + polling 5초 |
