# Slack Event Notify & Interactive Question — 설계 문서

- **작성일**: 2026-05-26
- **개정일**: 2026-06-05 (PoC 검증 후 양방향 설계로 전환)
- **작성자**: ymjoung
- **상태**: Draft v2 (사용자 승인 대기)
- **관련 프로젝트**: `slack-approval`

## 0. 개정 이력

| 버전 | 일자 | 변경 |
|---|---|---|
| v1 | 2026-05-26 | 초안. 워크플로우 A를 "옵션 표시 전용(단방향)"으로 설계. self-review에서 15개 모호점 식별. |
| v2 | 2026-06-05 | **PoC로 AskUserQuestion 양방향 가능 확인** → 워크플로우 A를 Slack에서 선택→Claude 반환하는 완전 양방향으로 재설계. 15개 모호점 정리. |

## 1. 배경

현재 `slack-approval`은 Claude Code의 `PermissionRequest`를 `type:http` hook으로 `localhost:8080/hook`에 직결하여, Slack에 **Approve/Deny 버튼**을 띄우고 DynamoDB polling으로 결과를 Claude에 반환한다.

이 흐름은 권한 승인(allow/deny 2지선다)에만 한정되어, 다음이 누락되어 있다.

1. **사용자 선택 요청**: Claude가 `AskUserQuestion`으로 객관식/주관식 질의를 띄울 때, Slack에는 여전히 allow/deny만 노출되거나(또는 미노출) **실제 선택지가 보이지 않는다**. 사용자가 터미널 앞에 없으면 응답이 지연된다.
2. **진행상황 가시성**: 장시간 작업의 주요 분기(완료/차단/마일스톤)를 터미널을 봐야만 알 수 있다.
3. **Plan 가시성**: `ExitPlanMode` 시 plan 본문이 Slack에 전달되지 않는다.

## 2. 목표 및 비목표

### 2.1 목표
- **(핵심)** `AskUserQuestion` 호출 시 **Claude가 실제 요청한 선택지(label/description)를 Slack 버튼으로 노출**하고, 사용자가 Slack에서 선택하면 그 값을 Claude에 답으로 반환한다 (allow/deny → 실제 선택지로 변환).
- 터미널/ Slack 어느 쪽에서 답해도 동작한다 (양쪽 race, 먼저 resolve된 쪽 채택).
- `ExitPlanMode` 시 plan 본문을 Slack에 전달한다 (표시 전용).
- 작업의 주요 분기에서 유의미한 요약을 Slack에 전달한다 (마커 기반, 표시 전용).
- 기존 `PermissionRequest` 워크플로우와 충돌하지 않는다.

### 2.2 비목표
- Slack에서 plan을 승인/반려하는 양방향 ExitPlanMode (향후. 현재는 표시만).
- 모든 turn 종료 시 알림 (noisy → 명시적 마커로 제한).
- 신규 백엔드 인프라 도입 (기존 `approval_server.py` + DynamoDB + Lambda 재사용).

## 3. PoC 검증 결과 (설계 전제)

2026-06-05 실측(`parking_lot/poc-askuserquestion/`):

| 항목 | 결과 |
|---|---|
| AskUserQuestion 발화 hook | `PreToolUse`, `tool_name == "AskUserQuestion"` ✅ |
| **interactive(default mode) 답 주입** | ✅ 자동 선택 확인, 터미널 프롬프트 억제됨 |
| 답 주입 포맷 | `hookSpecificOutput.{permissionDecision:"allow", updatedInput}` 이고 `updatedInput.answers = {"<question 텍스트>": "<선택 label>"}` |
| 실측 payload 키 | `session_id`, `tool_use_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, `tool_name`, `tool_input` |
| `tool_input` 스키마 | `questions[].{question, header, multiSelect, options[].{label, description}}` |

> **미검증(구현 중 확인)**: ① multiSelect의 answers 표현(추정: label들을 콤마 join). ② `/ask` 블로킹 중 터미널에도 질문이 떠 disconnect race가 PermissionRequest처럼 동작하는지.

## 4. 요구사항

### 4.1 기능 요구사항

| ID | 설명 |
|---|---|
| FR-1 | `AskUserQuestion` 호출 시 각 질문의 `question`/`header`와 모든 옵션 `label`/`description`을 Slack에 렌더링하고, **옵션별 버튼**(단일선택) 또는 멀티선택 위젯(multiSelect=true)을 표시 |
| FR-2 | 사용자가 Slack에서 모든 질문에 답하면, `{question: label(s)}` 맵을 만들어 `updatedInput.answers`로 Claude에 반환 (`permissionDecision:"allow"`) |
| FR-3 | 터미널에서 먼저 답하면(HTTP disconnect 감지) Slack 메시지를 "터미널에서 응답됨"으로 갱신하고 hook은 정상 종료 |
| FR-4 | 1~4개 다중 질문 처리: 모든 질문이 응답될 때까지 대기, 부분 응답은 DynamoDB에 누적 |
| FR-5 | `ExitPlanMode` 호출 시 plan 본문(≤3000자)을 Slack에 전송 (표시 전용, `/notify`) |
| FR-6 | Claude 응답에 마커(`<!notify:completed|blocked|milestone>`)가 있을 때만 Stop hook이 요약을 Slack에 전송 |
| FR-7 | 마커 없는 일반 turn은 발신하지 않음 (silent) |
| FR-8 | 서버 미기동/실패 시 Claude 흐름을 차단하지 않음 (hook 실패는 fail-open) |
| FR-9 | 기존 `PermissionRequest`(`/hook`)와 독립 동작 |

### 4.2 비기능 요구사항

| ID | 설명 |
|---|---|
| NFR-1 | `/ask`(양방향 블로킹) hook timeout 300초 (PermissionRequest와 동일). `/notify`(단방향) 호출 hook timeout 10초. |
| NFR-2 | Slack 메시지/블록은 Slack 한도 내(섹션 text ≤ 3000자, 메시지당 블록 ≤ 50) |
| NFR-3 | 외부 의존성: 기존 서버 스택(FastAPI, boto3, slack_sdk) + Stop/ExitPlanMode hook용 `jq`, `curl` |
| NFR-4 | 글로벌 `~/.claude/settings.json` 등록, 모든 프로젝트 동일 동작 |
| NFR-5 | DynamoDB 항목은 기존 테이블(`claude-approval-requests`) 재사용, TTL 10분 |

## 5. 아키텍처

### 5.1 컴포넌트 다이어그램

```
┌──────────────────────────────────────────────┐
│  Claude Code (CLI, interactive 또는 -p)        │
│   PreToolUse: AskUserQuestion (type:http,blk) │──┐ (A) blocking
│   PreToolUse: ExitPlanMode    (type:command)  │  │
│   Stop:       marker scan     (type:command)  │  │
└──────────────────────────────────────────────┘  │
       │ (B,C) fire-and-forget                     │
       ▼                                           ▼
┌────────────────────────┐        ┌────────────────────────────┐
│ slack-event-notify.sh  │        │ approval_server.py (:8080)  │
│  Stop 마커 / ExitPlan  │ POST   │  POST /hook  (기존, 변경X)  │
│  → /notify (단방향)    │───────▶│  POST /ask   (신규, 양방향) │
└────────────────────────┘        │  POST /notify(기존+이모지)  │
                                   └───────┬─────────────────────┘
                            chat_postMessage│   ▲ DynamoDB polling
                                            ▼   │
                                      ┌──────────────┐   button click
                                      │ Slack Channel │──────────────┐
                                      └──────────────┘               ▼
                                                          ┌────────────────────┐
                                                          │ lambda_handler (신규│
                                                          │  ask action 분기)   │
                                                          │  → DynamoDB update  │
                                                          └────────────────────┘
```

### 5.2 컴포넌트 책임

| 컴포넌트 | 책임 |
|---|---|
| `PreToolUse(AskUserQuestion)` http hook | payload를 `/ask`에 전달, 서버 응답(JSON)을 그대로 Claude hook 출력으로 반환 (PermissionRequest 패턴 동일) |
| `approval_server.py /ask` | questions 파싱 → Slack 질문/옵션 버튼 전송 → DynamoDB polling → 모든 답 수집 시 `updatedInput.answers` 빌드 후 반환. 터미널 disconnect race 처리. |
| `lambda_handler` (ask 분기) | Slack 옵션 버튼/선택 클릭 수신 → DynamoDB에 해당 질문의 답 누적 → Slack 메시지 갱신 |
| `slack-event-notify.sh` | Stop 마커 검출 / ExitPlanMode plan 추출 → `/notify` (단방향) |
| `/notify` | mrkdwn 알림 전송 (status 이모지 확장) |

### 5.3 데이터 흐름

#### 5.3.1 워크플로우 A: AskUserQuestion 양방향 (핵심)

```
1. Claude가 AskUserQuestion 호출 → PreToolUse(type:http) → POST /ask
   payload: {session_id, tool_use_id, tool_input.questions[], cwd, transcript_path, ...}
2. 서버: ask_id 생성, DynamoDB에 pending 저장
   { ask_id, status:"pending", questions:[...], answers:{}, expected_count:N, cwd, expires_at }
3. 서버: Slack 메시지 빌드 — 질문마다:
     *<header>* — <question>
     [옵션1] [옵션2] ...   (단일선택: button, value=label, action_id="ask::<qidx>::<oidx>")
     멀티선택: multi_static_select + Submit 버튼
   block_id에 ask_id, qidx 인코딩
4. 서버: chat_postMessage → message_ts 보관
5. 사용자가 Slack에서 옵션 클릭
   → Lambda: payload.actions[0]에서 ask_id, qidx, 선택 label 추출
   → DynamoDB: answers[question_text] = label 누적, 모든 질문 응답 시 status="answered"
   → Slack 메시지 갱신(선택 표시, 남은 질문 안내)
6. 서버 polling:
   - status=="answered" → answers 맵 회수
   - request.is_disconnected() (터미널 응답) → Slack "터미널에서 응답됨" 갱신, allow(빈 updatedInput)로 종료
   - timeout(300s) → Slack "시간 초과" 갱신
7. 서버 반환:
   {
     "hookSpecificOutput": {
       "hookEventName": "PreToolUse",
       "permissionDecision": "allow",
       "updatedInput": { ...questions 그대로..., "answers": {q1:label1, q2:label2} }
     }
   }
8. Claude가 터미널 프롬프트 없이 해당 답으로 진행
```

#### 5.3.2 워크플로우 B: Stop 마커 → Slack (단방향, 표시)

```
1. turn 종료 → Stop hook(type:command) → slack-event-notify.sh
2. transcript_path JSONL을 tail -r로 역순, "텍스트 블록이 있는" 가장 최근 assistant 메시지까지 탐색
3. 마커 검사 (우선순위 completed > blocked > milestone). 미발견 → exit 0 (silent)
4. 마커 이후~응답 끝 텍스트(≤3000자)를 summary로 추출
5. POST /notify {title, summary, project, status}
```

#### 5.3.3 워크플로우 C: ExitPlanMode → Slack (단방향, 표시)

```
1. Claude가 ExitPlanMode 호출 → PreToolUse(type:command) → slack-event-notify.sh
2. tool_input.plan 추출(≤3000자)
3. POST /notify {title:"📋 Plan 승인 요청", summary:plan, project, status:"plan"}
   (Claude는 hook 출력이 없으므로 평소처럼 터미널에서 plan 승인 진행)
```

## 6. 인터페이스

### 6.1 `/ask` 요청 (신규, 양방향)

- **입력**: Claude Code PreToolUse payload 원형 (위 PoC 실측 스키마)
- **출력**: `hookSpecificOutput` (allow + updatedInput.answers) — `/hook`과 동일한 반환 구조

### 6.2 `/notify` 페이로드 (기존 + status 확장)

```json
{
  "title": "string (필수)",
  "summary": "string (mrkdwn, ≤3000자)",
  "project": "string (basename(cwd), 비면 'unknown')",
  "status": "completed | in_progress | blocked | question | plan | milestone"
}
```

`build_notify_blocks()` 이모지 dict에 `question/plan/milestone` 추가.

### 6.3 answers 표현 규칙

| 경우 | answers 값 |
|---|---|
| 단일선택 | `{ "<question>": "<선택 label>" }` |
| 멀티선택 | `{ "<question>": "<label1>,<label2>" }` (콤마 join — **구현 중 실측 확정**) |
| 다중 질문 | 각 question을 key로 모두 포함 |

### 6.4 마커 사양 (워크플로우 B)

| 마커 | 의미 | summary |
|---|---|---|
| `<!notify:completed>` | task/section 완료 | 마커 이후~끝 |
| `<!notify:blocked>` | 진행 불가/외부 입력 대기 | 마커 이후~끝 |
| `<!notify:milestone>` | 마일스톤 도달 | 마커 이후~끝 |

규칙(모호점 정리): **마커는 응답 내 어디든 가능**하나 추출되는 summary는 **첫 매칭 마커 다음 글자부터 응답 끝까지**. 마커 2개↑면 우선순위 `completed > blocked > milestone` 중 하나만. 통상 0~1개 가정.

## 7. 변경 사항

### 7.1 `code/app/approval_server.py`

1. **`/ask` 엔드포인트 신규** — `/hook`을 템플릿으로:
   - `build_ask_blocks(ask_id, questions)`: 질문별 섹션 + 옵션 버튼(단일) / multi_static_select+Submit(멀티)
   - DynamoDB pending 저장(`questions`, `answers={}`, `expected_count`)
   - `poll_ask(ask_id, request, message_ts)`: `answered`/disconnect/timeout 처리
   - 반환: `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","updatedInput":{...,"answers":{...}}}}`
2. **`build_notify_blocks()` status_emoji 확장**: `question/plan/milestone` 추가.

### 7.2 `code/app/lambda_handler.py`

- action_id가 `ask::<qidx>::<oidx>` 형태면 ask 분기:
  - block_id에서 ask_id, payload에서 선택 label 추출
  - DynamoDB: `answers.<question>` 누적, 모든 응답 시 `status="answered"`
  - Slack 메시지 갱신(선택 echo + 잔여 질문). 기존 approve/deny 분기는 그대로.

### 7.3 신규 파일 `~/.claude/hooks/slack-event-notify.sh`

Stop 마커 + ExitPlanMode plan → `/notify` (단방향). (v1 스크립트 유지하되 macOS `tail -r`, 텍스트 블록 역순 탐색 반영.)

```bash
#!/usr/bin/env bash
set -u
PAYLOAD="$(cat)"
NOTIFY_URL="http://localhost:8080/notify"
event="$(jq -r '.hook_event_name // empty' <<<"$PAYLOAD")"
tool="$(jq -r '.tool_name // empty' <<<"$PAYLOAD")"
cwd="$(jq -r '.cwd // empty' <<<"$PAYLOAD")"
project="$(basename "${cwd:-unknown}")"; [[ -z "$project" ]] && project="unknown"

post(){ curl -s -m 10 -X POST "$NOTIFY_URL" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg t "$1" --arg s "$2" --arg p "$project" --arg st "$3" \
        '{title:$t,summary:$s,project:$p,status:$st}')" >/dev/null 2>&1 || true; }

case "$event" in
  PreToolUse)
    [[ "$tool" == "ExitPlanMode" ]] || exit 0
    plan="$(jq -r '.tool_input.plan // ""' <<<"$PAYLOAD" | head -c 3000)"
    post "📋 Plan 승인 요청" "$plan" "plan"
    ;;
  Stop)
    t="$(jq -r '.transcript_path // empty' <<<"$PAYLOAD")"; [[ -f "$t" ]] || exit 0
    # 텍스트 블록이 있는 가장 최근 assistant 메시지 탐색
    last_text="$(tail -r "$t" 2>/dev/null | while IFS= read -r line; do
        txt="$(jq -r 'select(.type=="assistant") | .message.content // [] |
                      map(select(.type=="text").text) | join("\n")' <<<"$line" 2>/dev/null)"
        [[ -n "$txt" ]] && { printf '%s' "$txt"; break; }
      done)"
    [[ -z "$last_text" ]] && exit 0
    if   grep -q '<!notify:completed>' <<<"$last_text"; then title="✅ 작업 완료"; st="completed"; m='<!notify:completed>'
    elif grep -q '<!notify:blocked>'   <<<"$last_text"; then title="🚫 작업 차단"; st="blocked";   m='<!notify:blocked>'
    elif grep -q '<!notify:milestone>' <<<"$last_text"; then title="🎯 마일스톤 도달"; st="milestone"; m='<!notify:milestone>'
    else exit 0; fi
    summary="$(awk -v m="$m" 'BEGIN{f=0} f{print} index($0,m){f=1}' <<<"$last_text" | head -c 3000)"
    [[ -z "$summary" ]] && summary="(요약 없음)"
    post "$title" "$summary" "$st"
    ;;
esac
exit 0
```

### 7.4 `~/.claude/settings.json` (jq 머지로 적용)

JSON은 주석 불가 → 기존 배열에 **append**:

```bash
# PreToolUse: AskUserQuestion → /ask (http, 블로킹), ExitPlanMode → script
# Stop: 기존 항목 유지 + slack-event-notify.sh 추가
jq '
  .hooks.PreToolUse += [
    {matcher:"AskUserQuestion", hooks:[{type:"http", url:"http://localhost:8080/ask", timeout:300}]},
    {matcher:"ExitPlanMode",    hooks:[{type:"command", command:"~/.claude/hooks/slack-event-notify.sh", timeout:10}]}
  ] |
  .hooks.Stop += [
    {matcher:"", hooks:[{type:"command", command:"~/.claude/hooks/slack-event-notify.sh", timeout:10}]}
  ]
' ~/.claude/settings.json > /tmp/settings.json && mv /tmp/settings.json ~/.claude/settings.json
```

### 7.5 `~/.claude/CLAUDE.md` — Notification Markers 섹션 추가

```markdown
## Notification Markers
다음 시점에 응답에 마커를 남긴다 (Slack 자동 발신):
- task/section 완료 → `<!notify:completed>` 다음 줄에 1~3줄 요약
- 진행 불가/외부 입력 필요 → `<!notify:blocked>` 다음 줄에 사유
- 마일스톤 도달 → `<!notify:milestone>` 다음 줄에 내용
일반 대화/단순 질문엔 남기지 않는다. "조용히 진행"/"알림 끄기" 요청 시 중단.
```

## 8. 테스트 전략

| 테스트 | 방법 |
|---|---|
| AskUserQuestion 단일선택 양방향 | 실제 세션에서 단일 질문 → Slack 버튼 클릭 → Claude가 그 답으로 진행 확인 |
| 다중 질문(2~4) | 모든 질문 응답 전에는 미반환, 전부 응답 시 answers 맵 완성 확인 |
| 멀티선택 | multiSelect=true → 위젯 선택 → answers 콤마 join 형식 실측·확정 |
| 터미널 우선 응답 | Slack 미클릭 상태에서 터미널 응답 → disconnect 감지, Slack "터미널 응답" 갱신 |
| ExitPlanMode 표시 | Plan mode 승인 요청 → Slack plan 본문 도달, 터미널 흐름 정상 |
| Stop 마커 매칭/미발견 | mock JSONL stdin 주입 → 마커 시 /notify 호출, 무마커 시 미호출 |
| 마지막 turn이 tool_use only | 텍스트 블록 있는 직전 assistant까지 역순 탐색되는지 |
| 서버 비활성(fail-open) | 서버 미기동 시 hook 실패가 Claude 흐름 차단 안 함 (단, /ask는 http hook 특성상 동작 확인 필요) |
| 한도 절단 | 3000자 초과 plan/summary 절단 |

## 9. 트레이드오프 및 위험

| 항목 | 위험 | 완화 |
|---|---|---|
| `/ask` 블로킹(300s) | 서버 미기동 시 http hook이 어떻게 동작하는지 PermissionRequest와 동일 가정 | PermissionRequest와 동일 패턴이므로 동등 동작 추정. 구현 시 미기동 케이스 실측 |
| multiSelect answers 포맷 미확정 | 콤마 join 가정이 틀리면 멀티선택 오동작 | 구현 초기에 실측 PoC로 확정 |
| 다중 질문 race | 질문 일부만 답하고 터미널서 마저 답하는 혼합 시나리오 | disconnect 우선: 터미널 응답 시 Slack 진행분 폐기, allow(빈 updatedInput) |
| 마커 의존 | Claude가 마커 누락 시 알림 누락 | CLAUDE.md 명시 + feedback memory |
| 글로벌 hook | 모든 프로젝트 발동 | 의도된 동작. 끄려면 settings에서 제거 |
| Slack 블록 한도 | 4질문×다옵션 시 블록 수 초과 가능 | 질문당 옵션 버튼 수 제한/섹션 분할, 50블록 이내 보장 |

## 10. 향후 작업 (out of scope)

- ExitPlanMode 양방향 (Slack에서 plan 승인/반려)
- 주관식(자유 텍스트) 답변을 Slack 모달로 입력
- Slack thread로 task 단위 묶음
- 알림 채널 분리 / 사용자별 마커 토글

## 11. 부록 — PoC 실측 payload (AskUserQuestion, mode=default)

```json
{
  "session_id": "be064b76-...",
  "transcript_path": "/Users/.../<session>.jsonl",
  "cwd": "/Users/ymjoung/workspace/claude-code-project/slack-approval",
  "permission_mode": "default",
  "hook_event_name": "PreToolUse",
  "tool_name": "AskUserQuestion",
  "tool_input": {
    "questions": [
      {
        "question": "Which color do you prefer?",
        "header": "Color",
        "multiSelect": false,
        "options": [
          {"label": "Red",  "description": "The color red."},
          {"label": "Blue", "description": "The color blue."}
        ]
      }
    ]
  },
  "tool_use_id": "toolu_..."
}
```

반환(주입) 형식:
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "updatedInput": {
      "questions": [ ... 원본 ... ],
      "answers": { "Which color do you prefer?": "Red" }
    }
  }
}
```
