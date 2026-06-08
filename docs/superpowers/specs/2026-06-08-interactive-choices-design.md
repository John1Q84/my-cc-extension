# Slack Interactive Choices — 설계 문서

- **작성일**: 2026-06-08
- **작성자**: ymjoung
- **상태**: Draft (사용자 승인 대기)
- **관련 프로젝트**: `slack-approval`
- **선행 작업**: Permission 요약 카드(완료), Plan 1 AskUserQuestion 양방향(작성됨)

## 1. 배경

`slack-approval`은 Claude Code의 `PermissionRequest`를 Slack 카드로 띄워 Approve/Deny 승인을 처리한다. 그러나 Claude Code가 사용자에게 제시하는 **객관식 선택지**가 Slack에 충실히 반영되지 않는다:

1. **PermissionRequest**: 터미널은 보통 `Yes` / `Yes, and don't ask again for <rule>` / `No` 3지선다를 보여주지만 Slack은 **Approve/Deny 2버튼**뿐이다. "다시 묻지 않기"를 Slack에서 선택할 수 없다.
2. **AskUserQuestion**: Claude가 객관식/주관식 질의를 띄울 때, Slack에는 선택지가 전혀 노출되지 않는다.

목표는 **자리를 비웠을 때 Slack에서 진행 중인 작업을 이어가는 "작업 연속성"**이며, 그 1차 단계로 **Claude Code가 요청하는 다양한 객관식을 Slack에서 선택 가능하게** 만든다.

> **범위 외 (HOLD)**: Slack에서 자유 텍스트로 추가 지시를 세션에 전달하는 기능. 합의된 향후 설계는 "Slack custom message를 DynamoDB에 저장 → Claude가 입력 요청 시 세션+DynamoDB state를 dual-watch, 먼저 들어온 답 수용". 본 spec에는 포함하지 않는다.

## 2. 목표 및 비목표

### 2.1 목표
- **PermissionRequest**: `permission_suggestions`를 읽어 `Allow` / `Allow + don't ask again (<rule>)` / `Deny` **동적 버튼**을 제시하고, "다시 안 묻기" 선택 시 그 규칙을 hook 응답에 반영한다.
- **AskUserQuestion**: 질문과 모든 옵션(label/description)을 Slack 동적 버튼/위젯으로 제시하고, Slack에서 선택한 답을 Claude에 반환한다(터미널 프롬프트 억제).
- 터미널/Slack 어느 쪽에서 답해도 동작한다(먼저 resolve된 쪽 채택).
- 기존 Approve/Deny 승인 흐름과 충돌하지 않는다(회귀 0).

### 2.2 비목표
- Slack에서 자유 텍스트 지시 전달(HOLD).
- AskUserQuestion 주관식(자유 입력) 답변(옵션 선택만 지원).
- 신규 백엔드 인프라(기존 FastAPI 서버 + DynamoDB + Lambda 재사용).

## 3. 검증된 기술 사실 (설계 전제)

2026-06-08 실측:

| 사실 | 증거 | 확실성 |
|---|---|---|
| `PermissionRequest` payload에 `permission_suggestions[]`가 옴 | 운영 로그 80건 + 전체 캡처 | ✅ |
| suggestion 구조: `{type:"addRules", rules:[{toolName, ruleContent}], behavior:"allow", destination:"localSettings"}` | 전체 payload 캡처 | ✅ |
| PermissionRequest payload 기본 키: `session_id, transcript_path, cwd, permission_mode, hook_event_name, tool_name, tool_input` (+ 조건부 `permission_suggestions`) | 캡처 | ✅ |
| 승인 시 규칙이 settings에 영구 저장 → 다시 안 물음 | `.claude/settings.local.json`에 `Read(//etc/**)` 등 실제 누적 확인 (터미널 경로) | ✅ |
| AskUserQuestion: `PreToolUse` hook, interactive에서 `updatedInput.answers` 주입 시 터미널 프롬프트 억제하고 답 채택 | PoC (단일+멀티선택) | ✅ |
| AskUserQuestion answers 포맷: 단일 `{질문:label}`, 멀티 `{질문:"l1,l2"}`(콤마 join) | PoC | ✅ |
| hook 응답의 `decision.permissionRule` 반환 가능 | 공식 docs + claude-code-guide | ⚠️ 문서상 |

### 3.1 미검증 항목과 흡수 방식

**미검증**: hook 응답의 `permissionRule`이 터미널 "don't ask again"과 **동일하게** 규칙을 등록하는지 (probe 환경에서 hook 발화 재현 실패 — 누적 allow 규칙 + `-p` 모드 비발화 때문).

**설계상 흡수**: "Allow + don't ask again" 버튼은 항상 `behavior:"allow"`를 포함하므로, `permissionRule`이 동작하지 않더라도 **이번 호출은 정상 허용**된다. 최악의 경우는 "다음에 또 물어봄"일 뿐 — 위험 0(fail-safe degrade). 구현 후 운영 `:8080`에서 1회 확인하여, 동작하지 않으면 버튼 라벨을 "허용(이번만)"으로 정직하게 조정한다.

## 4. 아키텍처

### 4.1 컴포넌트 다이어그램

```
┌──────────────────────────────────────────────┐
│  Claude Code (interactive)                    │
│   PermissionRequest (http) ──────────┐        │
│   PreToolUse: AskUserQuestion (http) ─┼──┐     │
└───────────────────────────────────────┼──┼─────┘
                                         ▼  ▼
                        ┌────────────────────────────────┐
                        │ approval_server.py (:8080)      │
                        │  POST /hook  (확장: suggestion 버튼)│
                        │  POST /ask   (신규: 양방향 질의)   │
                        │  순수함수: perm_buttons, ask_blocks│
                        └───────┬────────────────▲─────────┘
                  chat_postMessage│   DynamoDB poll│
                                  ▼                │
                            ┌──────────────┐  button click
                            │ Slack Channel │──────────────┐
                            └──────────────┘               ▼
                                                ┌────────────────────┐
                                                │ lambda_handler      │
                                                │  approve/deny(기존) │
                                                │  approve_rule(신규) │
                                                │  ask::/ask_submit:: │
                                                │  → DynamoDB update  │
                                                └────────────────────┘
```

### 4.2 컴포넌트 책임

| 컴포넌트 | 책임 |
|---|---|
| `/hook` (확장) | permission_suggestions 파싱 → 동적 버튼. polling 결과로 allow / allow+permissionRule / deny 반환 |
| `/ask` (신규) | questions 파싱 → 버튼/위젯. polling으로 answers 수집 → `updatedInput.answers` 반환 |
| `perm_buttons.py` (신규, 순수) | permission_suggestions → 버튼 elements + 규칙 추출/요약 |
| `ask_blocks.py` (신규, 순수) | questions → Slack 블록, action_id 인코딩, selections → answers 빌더 |
| `lambda_handler.py` (확장) | 버튼 클릭을 approve/deny/approve_rule/ask 분기 → DynamoDB |

### 4.3 데이터 흐름

#### 4.3.1 워크플로우 A: PermissionRequest 동적 버튼

```
1. /hook 수신, body.permission_suggestions 파싱
2. addRules suggestion의 rules[].ruleContent 추출 (perm_buttons)
3. DynamoDB pending 저장 (기존 + suggestion 보관)
4. Slack 동적 버튼:
     [✅ 허용]                      value=approve,      action_id=approve_action
     [✅ 허용 + 다시 안 묻기]         value=approve_rule, action_id=approve_rule_action  (suggestion 있을 때만)
     [❌ 거부]                      value=deny,         action_id=deny_action
   카드에 규칙 미리보기: `Bash(npm run *)` (다중이면 "외 N건")
5. 사용자 클릭 → lambda → DynamoDB status 갱신 (+ approve_rule 시 apply_rule=true)
6. /hook poll_dynamodb 회수 → 응답:
     approved      → {hookSpecificOutput:{hookEventName:"PermissionRequest", decision:{behavior:"allow"}}}
     approved+rule → {... decision:{behavior:"allow", permissionRule:<ruleContent>}}
     denied        → {... decision:{behavior:"deny", reason:"Denied via Slack"}}
     terminal/timeout → 기존 동작(terminal 갱신 / deny)
```

#### 4.3.2 워크플로우 B: AskUserQuestion 양방향 (Plan 1 채택)

```
1. AskUserQuestion 호출 → PreToolUse(http) → POST /ask
2. /ask: questions 파싱, DynamoDB pending 저장(questions JSON, selections={}, expected_count=len)
3. Slack 위젯 (ask_blocks):
     단일선택: 옵션별 button (action_id=ask::qidx::oidx, block_id={ask_id}::{qidx})
     멀티선택: checkboxes + Submit (action_id=ask_submit::qidx)
4. 사용자 클릭 → lambda → selections[qidx] 누적, len>=expected_count 시 status=answered
5. poll_ask:
     answered → selections→answers 빌드 → {hookSpecificOutput:{hookEventName:"PreToolUse",
                  permissionDecision:"allow", updatedInput:{...questions, answers:{질문:label(s)}}}}
     터미널 응답(disconnect) → Slack "터미널에서 응답됨" 갱신, {permissionDecision:"allow"} (빈)
     timeout → {permissionDecision:"allow"} (빈)
```

## 5. 인터페이스

### 5.1 PermissionRequest 응답 (확장)

```json
{ "hookSpecificOutput": { "hookEventName": "PermissionRequest",
    "decision": { "behavior": "allow", "permissionRule": "Bash(npm run *)" } } }
```
`permissionRule`은 approve_rule 선택 시에만 포함. 단일 문자열(첫 규칙). 배열 허용 여부는 구현 후 운영 검증.

### 5.2 AskUserQuestion 응답 (신규)

```json
{ "hookSpecificOutput": { "hookEventName": "PreToolUse", "permissionDecision": "allow",
    "updatedInput": { "questions": [...], "answers": { "질문 텍스트": "선택 label" } } } }
```

### 5.3 permission_suggestions 입력 스키마 (실측)

```json
{ "permission_suggestions": [
    { "type": "addRules",
      "rules": [ { "toolName": "Bash", "ruleContent": "Bash(npm run *)" } ],
      "behavior": "allow", "destination": "localSettings" } ] }
```

### 5.4 action_id / value 규칙

| 버튼 | action_id | value | block_id |
|---|---|---|---|
| 허용 | approve_action | approve | approval_id |
| 허용+규칙 | approve_rule_action | approve_rule | approval_id |
| 거부 | deny_action | deny | approval_id |
| Ask 단일옵션 | ask::{qidx}::{oidx} | str(oidx) | {ask_id}::{qidx} |
| Ask 멀티 Submit | ask_submit::{qidx} | submit | {ask_id}::{qidx} |

## 6. 변경 사항

### 6.1 신규 파일

- `code/app/perm_buttons.py` — 순수함수:
  - `extract_rules(permission_suggestions) -> list[str]`: addRules의 ruleContent 목록
  - `build_permission_buttons(approval_id, rules) -> list`: actions 블록 elements (규칙 있으면 3버튼, 없으면 2버튼)
  - `rule_label(rules) -> str`: 버튼/미리보기용 라벨 (첫 규칙 + "외 N건")
- `code/app/ask_blocks.py` — 순수함수 (Plan 1):
  - `encode_action_id/decode_action_id`, `build_answers(questions, selections)`, `build_ask_blocks(ask_id, questions)`

### 6.2 변경 파일

- `code/app/approval_server.py`:
  - `/hook`: `permission_suggestions` 파싱, `build_permission_buttons`로 동적 버튼, DynamoDB에 suggestion 보관, polling 결과로 `permissionRule` 포함 응답
  - `build_slack_blocks`: actions 블록을 perm_buttons로 위임(규칙 미리보기 포함)
  - 신규 `@app.post("/ask")` + `poll_ask()`
  - import: `from perm_buttons import ...`, `from ask_blocks import ...`
- `code/app/lambda_handler.py`:
  - action value/id 분기: `approve_rule` (status=approved, apply_rule=true), `ask::`/`ask_submit::` (selections 누적)
  - 기존 approve/deny 보존
- `~/.claude/settings.json` (jq merge): PreToolUse(AskUserQuestion) → http :8080/ask 등록
  ```bash
  jq '.hooks.PreToolUse += [{matcher:"AskUserQuestion",
    hooks:[{type:"http", url:"http://localhost:8080/ask", timeout:300}]}]' ...
  ```

### 6.3 DynamoDB (기존 테이블 재사용, on-demand → 스키마 변경 불필요)

신규 속성: `apply_rule`(bool), `rules`(list, JSON), `questions`(JSON str), `selections`(map), `expected_count`(int).

## 7. 테스트 전략

| 테스트 | 방법 |
|---|---|
| perm_buttons (단위) | suggestion 파싱, 규칙 추출, 3버튼/2버튼 분기, 라벨 "외 N건" |
| ask_blocks (단위) | action_id 인코딩/디코딩, build_answers(단일/멀티/다중질문), 블록 block_id 유일성 |
| lambda 분기 (단위) | approve_rule 파싱, ask::/ask_submit:: 파싱 + selections 누적 |
| 통합 | build_slack_blocks/build_ask_blocks 렌더 격리 검증(Slack 발신 없이) |
| E2E (운영 :8080) | ① PermissionRequest 3버튼 표시·클릭 ② **approve_rule 후 동일명령 재호출 안 물어보는지(미검증 항목 확인)** ③ AskUserQuestion 단일/멀티 양방향 ④ 기존 approve/deny 회귀 없음 |

## 8. 트레이드오프 및 위험

| 항목 | 위험 | 완화 |
|---|---|---|
| `permissionRule` 미동작 가능성 | "다시 안 묻기"가 안 될 수 있음 | fail-safe: behavior:allow는 항상 적용(이번 호출 허용). E2E에서 확인 후 라벨 조정 |
| 다중 규칙 | permissionRule 단수만 받을 수 있음 | 첫 규칙부터. 배열 허용 여부 E2E 확인 |
| suggestion 부재 | 일부 PermissionRequest엔 suggestion 없음 | "다시 안 묻기" 버튼 생략, 2버튼 graceful degrade |
| 글로벌 hook 등록 | 모든 프로젝트에서 /ask 발동 | 의도된 동작. 끄려면 settings에서 제거 |
| Slack 블록 한도 | 4질문×다옵션 시 50블록 초과 가능 | 옵션 수 제한/섹션 분할 |
| lambda 분기 증가 | 기존 approve/deny 회귀 위험 | 기존 경로 보존 + 단위 테스트 |

## 9. 향후 작업 (out of scope)

- Slack 자유 텍스트 지시 전달 (HOLD — DynamoDB + 세션 dual-watch 패턴)
- AskUserQuestion 주관식 답변 (Slack 모달)
- ExitPlanMode 양방향, Stop 마커 알림 (별도 spec 2026-05-26-slack-event-notify)
- 알림 채널 분리

## 10. 부록 — 실측 payload

PermissionRequest (permission_suggestions 포함):
```json
{
  "tool_name": "Bash",
  "tool_input": { "command": "cd /tmp && cat /etc/hostname ..." },
  "permission_suggestions": [
    { "type": "addRules",
      "rules": [ {"toolName":"Read","ruleContent":"//etc/**"},
                 {"toolName":"Read","ruleContent":"//dev/**"} ],
      "behavior": "allow", "destination": "localSettings" } ]
}
```
