# Hands-off

## 2026-06-08_15:40 — CHECKPOINT cp-20260608-1540: Permission 요약 완료 + Interactive Choices 설계·계획 검증완료

### Change Log

| 영역 | 상태 |
|---|---|
| **Plan 2 — Permission 요약 카드** | ✅ 구현·검증 완료 (Bedrock Haiku 요약, isMeta 필터, 1500자 보존). 실제 Slack 프로덕션 카드로 검증 |
| 최종 리뷰 결함 3건 | ✅ 수정 (interrupt 마커, 동기 호출 to_thread, 빈 risk 불릿) + fallback 500→2800자 |
| 이미지 placeholder 버그 | ✅ 수정 (`[Image #N]` 제거, 실제 텍스트 유지) |
| **Interactive Choices** (PermissionRequest 3버튼 + AskUserQuestion 양방향) | 📋 spec + plan 작성, **적대적 검증(22 에이전트) 통과 → 4개 결함 반영**. 구현 착수 직전 |
| 단위 테스트 | 15 passed |

### git (브랜치: feature/permission-summary, 12 커밋, main 미머지)

- `694ad49` plan + 검증 결함 4건 반영 (Decimal coercion, lambda region, 원자 selections, merge 테스트)
- `2854345` fix: [Image #N] placeholder 제거
- `ac53721` docs: interactive choices 설계 spec
- (이전: Plan 2 요약 카드 9개 커밋)

### 검증된 핵심 사실 (Interactive Choices 구현 전제)

- PermissionRequest payload에 `permission_suggestions: [{type:addRules, rules:[{toolName,ruleContent}], behavior, destination}]` 옴 (조건부)
- AskUserQuestion: PreToolUse hook, interactive에서 `permissionDecision:allow + updatedInput.answers` 주입 시 답 채택
- **미검증**: hook `permissionRule` 실효성 → fail-safe로 흡수, Task 8 E2E 확인
- **CRITICAL 회피**: DynamoDB Decimal 역직렬화 → build_answers int() coercion (검증으로 사전 차단)

### 다음 단계

- Interactive Choices 구현 (subagent-driven, plan: `docs/superpowers/plans/2026-06-08-interactive-choices.md`)
- 보류(HOLD): Slack 자유텍스트 전달 (DynamoDB+세션 dual-watch 패턴)

---

## 2026-06-06_11:35 — Permission 메시지 LLM 요약 요구 + Bedrock 검증

### 신규 요구사항 (사용자)

Slack permission/질의 카드 개선: ① input 500자 절단 → 전체 컨텍스트 파악 가능하게, ② raw input 나열 대신 **요청사항 / risk·영향도 / 사용자 확인필요**를 구조화 요약. (스크린샷 2건: Bash 긴 명령이 `...` 절단, Context에 skill 주입 텍스트가 사용자 맥락으로 오인됨)

### 결정 & 검증

| 항목 | 결과 |
|---|---|
| 요약 방식 | **LLM 요약 (Haiku 4.5)** 결정. 실패 시 raw 표시 fallback. |
| 모델 호출 경로 | ANTHROPIC_API_KEY 없음 → **Bedrock**. on-demand 불가, **`global.anthropic.claude-haiku-4-5-20251001-v1:0` inference profile**로 ap-northeast-2 호출 성공 ✅ (기존 boto3 자격증명 재사용, 신규 키 불필요) |
| context 버그 | `extract_user_context`가 skill/system-reminder 주입 텍스트를 사용자 메시지로 오인 → 실제 타이핑만 추출하도록 수정 필요 |

### 다음 단계

- 별도 plan(permission 요약 개선) 작성 → 구현. AskUserQuestion 양방향 plan과 독립.

---

## 2026-06-05_18:53 — AskUserQuestion 양방향 PoC 검증 완료 (설계 방향 전환)

### 작업 요약

사용자 신규 요구: "Slack에 allow/deny만 보이는 것 → Claude Code가 실제 요청한 선택지가 보이게". draft의 워크플로우 A(표시 전용 단방향)를 **완전 양방향**(Slack에서 옵션 선택 → Claude로 반환)으로 전환 가능한지 PoC로 검증.

### PoC 결과 (확정 사실)

| 검증 항목 | 결과 |
|---|---|
| AskUserQuestion → `PreToolUse` hook 발화 (critical #1) | ✅ 확정 (`tool_name="AskUserQuestion"`) |
| **interactive(default mode)에서 답 주입 → 터미널 프롬프트 억제** | ✅ **자동 선택 확인** (guide 에이전트의 "interactive 불가" 주장은 오류) |
| 답 주입 포맷 | `hookSpecificOutput.updatedInput.answers = {"<question>": "<label>"}` + `permissionDecision:"allow"` |
| payload 실측 키 | `session_id`, `tool_use_id`, `transcript_path`, `cwd`, `permission_mode`, `tool_input.questions[].{question,header,multiSelect,options[].{label,description}}` |
| 라운드트립 가능성 | 필수 키 모두 존재 → PermissionRequest와 동일한 양방향 가능 |

PoC 자산: `parking_lot/poc-askuserquestion/` (test-hook.sh, settings.json, captured-payload.json)

### 아키텍처 단서

- 기존 PermissionRequest hook은 `type:"http"` → `localhost:8080/hook` 직결, timeout 300s 블로킹. 서버 JSON 응답이 곧 hook 출력.
- → 양방향 AskUserQuestion도 shell script 없이 **`type:http` hook → 서버 신규 엔드포인트(`/ask`)**로 동일 패턴 재사용이 최적.

### 다음 단계

- spec revision: 워크플로우 A를 양방향으로 재작성 + 기존 15개 모호점 정리.
- 미검증(refinement): 서버가 폴링하며 블로킹하는 동안 터미널에도 질문이 떠서 disconnect fallback이 되는지(PermissionRequest는 됨, AskUserQuestion도 동일 추정).

---

## 2026-05-26_22:30 — Slack Event Notify 설계 (Draft, 재검토 필요)

### 작업 요약

Claude Code의 추가 이벤트(AskUserQuestion, ExitPlanMode, 주요 분기)를 Slack에 자동 전달하는 워크플로우 설계를 시작. spec 문서 초안 작성 완료 후 self-review에서 15건의 모호함/불일치 항목 발견. **다음 세션에서 spec revision부터 재개.**

### 결과물

- `docs/superpowers/specs/2026-05-26-slack-event-notify-design.md` (Draft, 미커밋)

### 진행 상태

| 단계 | 상태 |
|---|---|
| Brainstorming (요구사항/접근 결정) | ✅ 완료 |
| Spec 초안 작성 | ✅ 완료 |
| Spec self-review | ⚠️ 진행 중 (15개 모호점 식별) |
| Spec 사용자 승인 | ⏸ 보류 |
| Implementation plan (writing-plans) | ⏸ 미착수 |
| 구현 | ⏸ 미착수 |

### 결정된 설계 사항 (확정)

- **트리거**: AskUserQuestion + ExitPlanMode + Stop hook (마커 기반)
- **Stop hook 필터링**: Claude가 응답에 명시 마커(`<!notify:completed|blocked|milestone>`)를 남긴 경우만 발신
- **아키텍처**: hook 스크립트 → 기존 `localhost:8080/notify` (approval_server.py 재사용)
- **신규 파일**: `~/.claude/hooks/slack-event-notify.sh`
- **변경 파일**: `~/.claude/settings.json`, `~/.claude/CLAUDE.md`, `code/app/approval_server.py` (status 이모지 매핑 확장)
- **spec 저장 위치**: `docs/superpowers/specs/`

### 다음 세션 시작 시 우선 처리 (critical)

다음 4개 항목이 설계 결정 자체에 영향:

1. **AskUserQuestion이 PreToolUse로 발화하는지 검증** — 실제로는 Notification hook일 가능성. Claude Code 문서/실측 확인 필요. 잘못 가정하면 워크플로우 A 미동작.
2. **마커 위치 모순** — spec 5.2 "어디에 와도 무방" vs CLAUDE.md "마지막 줄" vs 코드 "마커 다음 줄~끝까지를 summary로". 일관되게 정리 필요.
3. **assistant turn에 text block이 없는 경우(마지막이 tool_use only)** — 코드는 가장 최근 assistant 라인 1건만 보므로 마커 누락 가능. 텍스트 있는 assistant turn까지 역순 탐색하도록 수정.
4. **settings.json 변경 표현** — 주석 포함 JSON은 비유효. 구현 시 어떻게 머지할지(jq / 수동) 명시.

### 추가 식별된 모호함 (minor, 함께 정리)

5. FR-1의 `header` 필드를 코드가 미사용
6. 다중 questions / multiSelect 처리 미정의
7. summary 길이 1500 vs NFR-4의 3000 불일치
8. `in_progress` status 사용처 미명시
9. 8장 트레이드오프의 `tac` 항목은 이미 해결됨(잔존 텍스트)
10. timeout 5초 적용 범위 모호
11. `cwd` 빈 문자열 fallback 미정의
12. 동시성 테스트 기준 모호
13. 워크플로우 C와 Stop 마커 중복 발신 방지 정책 미정의
14. 신규 status가 기존 `/slack-notify` skill에 미치는 영향 미명시
15. transcript JSONL 스키마 안정성 검증 필요

### 참고

- 사용자 요청: "오늘 작업 완료 불필요. idea를 정확히 구현할 설계 필요."
- 사용자 요청: "사용자 질의 시 옵션까지 Slack 전달 + 주요 분기마다 유의미 정보 Slack 전달 두 가지 모두 구현"
- 마커 판별 정책: "Claude가 명시적 마커를 응답에 남긴 때만" (hybrid 거부)
- 문서 위치: 현재 프로젝트 docs/ (Skill base는 거부)
