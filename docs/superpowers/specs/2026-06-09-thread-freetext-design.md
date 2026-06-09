# Slack Thread Free-text Response — 설계 문서

- **작성일**: 2026-06-09
- **작성자**: ymjoung
- **상태**: Draft (사용자 승인 대기)
- **관련 프로젝트**: `slack-approval`
- **선행 작업**: Interactive Choices(PermissionRequest 3버튼 + AskUserQuestion 양방향, 구현 완료)

## 1. 배경

Interactive Choices 구현 후 두 가지가 드러났다.

1. **중복 카드 버그**: `AskUserQuestion` 도구가 `PreToolUse`(`/ask`)와 `PermissionRequest`(`/hook`) 양쪽 hook을 발화시켜, 옵션 버튼 카드(정상)와 무의미한 허용/거부 카드(중복)가 둘 다 뜬다.
2. **자유 텍스트 입력 부재**: Slack 버튼은 고정 선택지뿐이라, 사용자가 "옵션 말고 이렇게 해"라고 자유롭게 답할 방법이 없다. 사용자는 Slack thread reply로 자유 텍스트를 입력하는 방식을 원한다.

본 설계는 ① 중복 카드 제거, ② 카드-thread 연결, ③ thread reply 자유 텍스트를 Claude 답으로 전달하는 흐름을 정의한다.

## 2. 목표 및 비목표

### 2.1 목표
- `AskUserQuestion`에서 PermissionRequest 중복 카드를 제거한다.
- 각 질의 카드(permission/ask)를 자기 thread의 부모로 만들어, 그 thread의 reply를 해당 카드의 답으로 라우팅한다.
- Slack Events API로 thread reply를 수신해, dual-watch(버튼 또는 thread reply, 먼저 들어온 것)로 Claude에 답을 전달한다.
- 도구별 자유 텍스트 매핑: AskUserQuestion=자유답 주입, PermissionRequest=deny+reason.

### 2.2 비목표
- 세션 단위 thread 묶기(채널 noisy 개선) — 카드 단위로 한정.
- 질의와 무관한 일반 지시 전달 — 질의 카드 응답으로만.
- AskUserQuestion 주관식 외 추가 모달 UI.

## 3. 검증된 기술 사실 (설계 전제)

2026-06-09 실측:

| 사실 | 증거 |
|---|---|
| AskUserQuestion이 PreToolUse(`/ask`) + PermissionRequest(`/hook`) **양쪽 발화** | 로그: 같은 질의에 `ask_id=... posted` + `Received hook ... tool_name:AskUserQuestion` + `Created approval_id=... for tool=AskUserQuestion` |
| Slack 앱 scope = `chat:write, incoming-webhook` (발신만) | `auth.test` x-oauth-scopes |
| 라우트 = `POST /slack/interact` 하나 (버튼만) | terraform |
| DynamoDB: PK=`approval_id`, GSI 없음, on-demand, TTL=`expires_at`(10분) | terraform |
| payload에 `session_id` 존재 | PermissionRequest payload 캡처 |

### 3.1 결정 사항 (brainstorming)

| 질문 | 결정 |
|---|---|
| thread 단위 | **카드 단위** (세션 묶기 제외) |
| 자유 텍스트 전달 시점 | 질의 카드 thread reply로만 |
| 도구별 답 매핑 | AskUserQuestion=자유답 그대로 / PermissionRequest=deny+reason |
| thread_ts → 항목 조회 | `message_ts` **GSI** 추가 |
| TTL | 기존 10분(`expires_at`) 유지 |
| 버튼 vs thread reply 동시 입력 | **버튼(status) 우선** |
| Events API ack | 3초 고정(Slack 정책) → 즉시 200 + 가벼운 동기 처리 |

## 4. 아키텍처

### 4.1 컴포넌트 다이어그램

```
┌──────────────────────────────────────────────┐
│  Claude Code                                  │
│   PreToolUse: AskUserQuestion → /ask (http)   │
│   PermissionRequest → /hook (http)            │
└───────────────────────┬───────────────────────┘
                         ▼ (blocking poll)
        ┌────────────────────────────────────┐
        │ approval_server.py (:8080)          │
        │  /hook: AskUserQuestion 즉시 allow   │  ← ① 중복 버그 수정
        │  /hook poll: status OR free_text    │  ← ④ dual-watch
        │  /ask poll: answered OR free_text   │  ← ④ dual-watch
        │  카드 발신 시 message_ts 저장(GSI)    │  ← ② 카드-thread
        └───────┬──────────────────────▲───────┘
       chat_postMessage│        DynamoDB│ (status/free_text/message_ts GSI)
                       ▼                │
                 ┌──────────────┐  ┌────┴─────────────────────┐
                 │ Slack Channel │  │ lambda_handler           │
                 │  + thread     │  │  /slack/interact (버튼)   │
                 └──────┬────────┘  │  /slack/events (thread)  │ ← ③ Events API
            button click│  thread   │   url_verification 응답   │
                        │  reply    │   message → GSI 조회 →    │
                        └───────────▶   free_text 기록          │
                                    └──────────────────────────┘
```

### 4.2 컴포넌트 책임

| 컴포넌트 | 책임 |
|---|---|
| `/hook` (수정) | AskUserQuestion이면 즉시 allow(중복 방지). 그 외 기존 + poll에 free_text 분기 |
| `/ask` (수정) | 카드 발신 시 message_ts 저장. poll에 free_text 분기 |
| `lambda /slack/events` (신규) | Slack Events API 수신: url_verification, message 이벤트 → GSI 조회 → free_text 기록 |
| `message_ts` GSI (신규) | thread_ts로 항목 역조회 |
| poll 결정 로직 (순수함수 분리) | "버튼 vs free_text 중 무엇을 답으로" 결정 — 테스트 대상 |

### 4.3 데이터 흐름

#### 4.3.1 ① 중복 카드 제거

```
/hook 수신 → tool_name == "AskUserQuestion"이면:
  return {hookSpecificOutput:{hookEventName:"PermissionRequest", decision:{behavior:"allow"}}}
  (카드 미발신 — /ask가 전담)
```

#### 4.3.2 ② 카드-thread 연결

```
/hook, /ask가 chat_postMessage로 카드 발신 → 반환된 message_ts를
DynamoDB 항목에 저장(message_ts 속성). 이 ts가 곧 그 카드 thread의 부모.
```

#### 4.3.3 ③ thread reply 수신

```
1. 사용자가 카드 thread에 댓글 → Slack Events API → POST /slack/events
2. lambda: signing secret 검증
3. body.type:
   - "url_verification" → {challenge} 반환 (Slack endpoint 검증)
   - "event_callback" + event.type=="message":
       - bot_id 있으면(자신 메시지) 무시 — 루프 방지
       - subtype 있으면(편집/삭제 등) 무시
       - thread_ts 없으면(부모 자체) 무시 — reply만
       - message_ts GSI로 thread_ts 조회 → 항목 있으면:
           SET free_text = event.text, decided_by = user, decided_at = ts
       - 즉시 200 반환 (가벼운 처리, 3초 내)
4. 멱등: free_text 덮어쓰기는 idempotent (재전송/다중 reply 안전)
```

#### 4.3.4 ④ dual-watch 답 처리

```
AskUserQuestion (/ask poll_ask), 매 주기:
  1. status=="answered" (버튼) → answers 반환  [우선]
  2. free_text 존재 AND 단일 질문 → updatedInput.answers = {그 질문: free_text} 반환
     (다중 질문이면 free_text 무시 — 버튼 응답만 채택)
  3. disconnect → 빈 allow
  4. timeout → 빈 allow

PermissionRequest (/hook poll_dynamodb), 매 주기:
  1. status in (approved/denied) (버튼) → 기존 [우선]
  2. free_text 존재 → deny + reason=free_text
  3. terminal/timeout → 기존
```

## 5. 인터페이스

### 5.1 DynamoDB 항목 (확장)

| 속성 | 용도 |
|---|---|
| `approval_id` (PK) | 기존 |
| `message_ts` (GSI hash key, 신규) | 카드 발신 ts → thread reply 역조회 |
| `free_text` (신규) | thread reply 텍스트 |
| `status`, `selections`, `expected_count`, `expires_at` 등 | 기존 |

### 5.2 GSI

```hcl
global_secondary_index {
  name            = "message_ts-index"
  hash_key        = "message_ts"
  projection_type = "ALL"
}
attribute { name = "message_ts"; type = "S" }
```

### 5.3 lambda 이벤트 분기

```
body가 "payload=" 폼 → interactive(버튼, 기존)
body가 JSON:
  type=="url_verification" → {"challenge": body["challenge"]}
  type=="event_callback" → message 이벤트 처리(위 4.3.3)
```

### 5.4 자유 텍스트 매핑

| 도구 | 응답 |
|---|---|
| AskUserQuestion (단일 질문) | `updatedInput.answers = {질문텍스트: free_text}` |
| AskUserQuestion (다중 질문) | free_text **무시** — 텍스트 하나를 여러 질문에 매핑하는 건 모호하므로 버튼 응답만 채택 |
| PermissionRequest | `decision: {behavior:"deny", reason: free_text}` |

> **다중 질문 + 자유 텍스트**: 어느 질문에 대한 답인지 모호하므로 무시한다(버튼으로만 응답). 자유 텍스트는 단일 질문 카드에서만 답으로 채택. poll_decision이 questions 수를 보고 분기.

## 6. 변경 사항

### 6.1 신규/변경 파일

| 파일 | 변경 |
|---|---|
| `code/app/approval_server.py` | `/hook` AskUserQuestion 즉시 allow; `/hook`·`/ask` 카드 발신 시 message_ts 저장; poll_dynamodb·poll_ask에 free_text 분기 |
| `code/app/lambda_handler.py` | body 분기(interactive vs events); url_verification; message 이벤트 → GSI 조회 → free_text 기록 |
| `code/app/poll_decision.py` (신규, 순수) | "버튼 vs free_text 중 답 결정" 로직 — 테스트 대상 |
| `code/terraform/main.tf` | message_ts 속성 + GSI; `POST /slack/events` 라우트 |
| 테스트 | poll_decision 단위, lambda event 파싱 단위 |

### 6.2 Slack 앱 설정 (사용자 작업, 코드 외)

1. api.slack.com → 앱 → Event Subscriptions 활성화
2. Request URL: `<API Gateway>/slack/events` (URL 검증 통과 필요)
3. Subscribe to bot events: `message.channels` (비공개 채널이면 `message.groups`)
4. OAuth scope 추가: `channels:history` (또는 `groups:history`) → 앱 재설치
5. 재설치 후 새 bot token이면 환경변수 갱신 + 서버 재기동

## 7. 테스트 전략

| 테스트 | 방법 |
|---|---|
| poll_decision (단위) | (status, free_text) 조합 → 어느 답 채택. 버튼 우선, free_text 폴백, 둘 다/없음 |
| lambda event 파싱 (단위) | url_verification→challenge, event_callback message→free_text 추출, bot_id/subtype/thread_ts없음 무시 |
| 중복 카드 제거 (단위/통합) | /hook에 AskUserQuestion payload → 카드 미발신, 즉시 allow |
| message_ts 저장 (통합) | 카드 발신 후 항목에 message_ts 기록 확인 |
| E2E | ① AskUserQuestion 카드 1개만(중복X) ② 카드 thread에 자유 텍스트 → Claude가 그 텍스트를 답으로 진행 ③ PermissionRequest thread reply → deny+reason ④ 버튼/thread 동시 시 버튼 우선 ⑤ 기존 버튼 흐름 회귀 없음 |

## 8. 트레이드오프 및 위험

| 항목 | 위험 | 완화 |
|---|---|---|
| Events API 3초 ack | 처리 느리면 Slack 재전송 폭증 | 처리 가볍게(GSI 조회+쓰기만, LLM 호출 금지), 즉시 200 |
| 무한 루프 | bot 자신 메시지를 또 처리 | bot_id 있으면 무시 |
| 이벤트 재전송 | 중복 free_text 기록 | 덮어쓰기 idempotent |
| GSI eventual consistency | 카드 발신 직후 thread reply가 GSI에 아직 없을 수 있음 | reply는 사람이 카드 본 뒤라 수초 지연 → 실무상 무방. 미조회 시 무시(다음 reply나 버튼) |
| Slack 앱 재설치 | scope 변경 시 bot token 변경 가능 | 재설치 후 토큰 갱신 절차 문서화 |
| signing secret 검증 | events 경로 미검증 시 위조 | interact와 동일 검증 재사용 |
| 버튼 vs free_text 경합 | 동시 입력 모호 | 버튼 우선 정책 명시 |

## 9. 향후 작업 (out of scope)

- 세션 단위 thread 묶기(채널 noisy 개선)
- 질의 무관 일반 지시 전달(dual-watch 확장)
- thread reply에 이미지/첨부 처리
