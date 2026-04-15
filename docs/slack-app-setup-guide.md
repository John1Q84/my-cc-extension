# Slack App 설정 가이드

Claude Code Approval 시스템에 필요한 Slack App을 생성하고 설정하는 가이드.

## 사전 준비

- Slack 워크스페이스 Admin 권한

## Step 1: Slack App 생성

1. https://api.slack.com/apps 접속
2. **Create New App** 클릭
3. **From scratch** 선택
4. App Name: `Claude Code Approval` (원하는 이름 사용 가능)
5. 워크스페이스 선택 후 **Create App** 클릭

## Step 2: Bot Token Scopes 설정

1. 좌측 메뉴 **OAuth & Permissions** 클릭
2. 스크롤하여 **Scopes** 섹션 찾기
3. **Bot Token Scopes** > **Add an OAuth Scope** 클릭
4. `chat:write` 추가

## Step 3: App 설치 및 Bot Token 획득

1. 좌측 메뉴 **OAuth & Permissions** 클릭
2. 페이지 상단 **Install to Workspace** 클릭
3. 권한 허용
4. **Bot User OAuth Token** 복사 (`xoxb-...` 형태)

```bash
export SLACK_APPROVAL_BOT_TOKEN="xoxb-복사한-토큰"
```

## Step 4: Signing Secret 획득

1. 좌측 메뉴 **Basic Information** 클릭
2. **App Credentials** 섹션 > **Signing Secret** > **Show** 클릭
3. 값 복사

```bash
export TF_VAR_slack_signing_secret="복사한-시크릿"
```

## Step 5: 채널 설정

1. Slack에서 알림 받을 채널을 생성하거나 기존 채널 선택
2. 채널에 Bot 초대: `/invite @Claude Code Approval`
3. 채널 ID 복사:
   - 채널명 우클릭 > **채널 세부정보 열기**
   - 하단에 표시되는 채널 ID (예: `C062KQN6RB7`)

```bash
export SLACK_APPROVAL_CHANNEL_ID="C복사한-채널-ID"
```

## Step 6: Interactivity 설정

> `deploy.sh` 실행 후 출력되는 API Gateway URL이 필요합니다. 배포를 먼저 완료하세요.

1. 좌측 메뉴 **Interactivity & Shortcuts** 클릭
2. **Interactivity** 토글 **ON**
3. **Request URL** 입력:

```
https://<api-id>.execute-api.<region>.amazonaws.com/slack/interact
```

> `deploy.sh` 실행 마지막에 이 URL이 출력됩니다.

4. **Save Changes** 클릭

## 환경변수 요약

셸 프로필(예: `~/.zshrc`, `~/.bashrc`)에 아래 내용을 추가하여 영구 설정한다:

```bash
# Claude Code Slack Approval
export SLACK_APPROVAL_BOT_TOKEN="xoxb-..."
export SLACK_APPROVAL_CHANNEL_ID="C..."
export TF_VAR_slack_signing_secret="..."

# 선택 (기본값 있음)
# export SLACK_APPROVAL_AWS_REGION="ap-northeast-2"
# export DYNAMODB_TABLE="claude-approval-requests"
```

추가 후 `source ~/.zshrc` (또는 해당 셸 프로필)을 실행하여 반영한다.

## 트러블슈팅

| 증상 | 확인 사항 |
|------|----------|
| Bot이 채널에 메시지를 보내지 못함 | 채널에 Bot이 초대되었는지 확인 (`/invite @앱이름`) |
| 버튼 클릭 후 반응 없음 | Interactivity Request URL이 올바른지 확인 |
| Lambda 에러 | CloudWatch 로그 확인: `aws logs tail /aws/lambda/claude-approval-webhook --region ap-northeast-2` |
| Signing Secret 불일치 | `TF_VAR_slack_signing_secret`과 Slack App의 Signing Secret이 동일한지 확인 |
| 로컬 서버 미응답 | `curl http://localhost:8080/health` 및 `launchctl list \| grep oh-my-cc-agent` 확인 (macOS) |
