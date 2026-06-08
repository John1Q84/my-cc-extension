# Slack Interactive Choices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude Code의 PermissionRequest를 `허용 / 허용+다시 안 묻기 / 거부` 동적 버튼으로, AskUserQuestion을 실제 옵션 버튼으로 Slack에 노출하고, Slack 선택을 Claude Code에 반환한다.

**Architecture:** 기존 `/hook`(PermissionRequest)을 확장해 `permission_suggestions`를 동적 버튼으로 만들고, AskUserQuestion용 신규 `/ask` 엔드포인트를 추가한다. 버튼 클릭은 기존 `lambda_handler.py`에 분기를 추가해 DynamoDB에 기록하고, 서버가 polling으로 회수해 hook 응답을 반환한다. Slack 블록 빌더와 파서는 순수 함수(`perm_buttons.py`, `ask_blocks.py`)로 분리해 단위 테스트한다.

**Tech Stack:** Python 3.14, FastAPI, boto3(DynamoDB), slack_sdk, AWS Lambda, pytest, bash+jq(settings merge).

**검증 완료 전제 (`docs/superpowers/specs/2026-06-08-interactive-choices-design.md` §3):**
- PermissionRequest payload에 `permission_suggestions: [{type:"addRules", rules:[{toolName, ruleContent}], behavior, destination}]`가 옴 (조건부).
- AskUserQuestion: PreToolUse hook, interactive에서 `permissionDecision:"allow" + updatedInput.answers` 주입 시 답 채택. 단일 `{질문:label}`, 멀티 `{질문:"l1,l2"}`.
- **미검증(§3.1)**: hook의 `decision.permissionRule`이 터미널 "don't ask again"과 동일하게 동작하는지. → fail-safe: `behavior:allow`는 항상 적용. Task 8 E2E에서 확인.

---

## File Structure

| 파일 | 책임 | 변경 |
|---|---|---|
| `code/app/perm_buttons.py` | permission_suggestions → 규칙 추출, 버튼 elements, 라벨 (순수) | **신규** |
| `code/app/ask_blocks.py` | AskUserQuestion → 블록/answers/action_id (순수) | **신규** |
| `code/app/approval_server.py` | `/hook` suggestion 버튼+permissionRule 응답 / 신규 `/ask`+poll_ask | 수정 |
| `code/app/lambda_handler.py` | approve_rule + ask 분기 | 수정 |
| `~/.claude/settings.json` | PreToolUse(AskUserQuestion) http hook 등록 | 수정 (jq merge) |
| `code/app/tests/test_perm_buttons.py` | perm_buttons 단위 | **신규** |
| `code/app/tests/test_ask_blocks.py` | ask_blocks 단위 | **신규** |
| `code/app/tests/test_lambda_interactive.py` | lambda 분기 단위 | **신규** |

**설계 메모:** 블록/파싱 로직을 순수 함수로 분리하는 이유 — DynamoDB/Slack I/O 없이 테스트 가능하고, `approval_server.py`와 `lambda_handler.py` 양쪽에서 재사용(DRY). 현재 `build_slack_blocks`의 하드코딩된 2버튼 actions 블록을 `perm_buttons.build_permission_buttons()`로 위임한다.

---

## Task 1: perm_buttons.py — 규칙 추출 + 버튼 빌더 (TDD)

**Files:**
- Create: `code/app/perm_buttons.py`
- Test: `code/app/tests/test_perm_buttons.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`code/app/tests/test_perm_buttons.py`:

```python
from perm_buttons import extract_rules, rule_label, build_permission_buttons


def test_extract_rules_from_addrules():
    suggestions = [
        {"type": "addRules",
         "rules": [{"toolName": "Bash", "ruleContent": "Bash(npm run *)"},
                   {"toolName": "Read", "ruleContent": "//etc/**"}],
         "behavior": "allow", "destination": "localSettings"},
    ]
    assert extract_rules(suggestions) == ["Bash(npm run *)", "//etc/**"]


def test_extract_rules_empty_when_no_suggestions():
    assert extract_rules([]) == []
    assert extract_rules(None) == []


def test_extract_rules_ignores_non_addrules():
    assert extract_rules([{"type": "other", "rules": [{"ruleContent": "x"}]}]) == []


def test_rule_label_single():
    assert rule_label(["Bash(npm run *)"]) == "Bash(npm run *)"


def test_rule_label_multiple_shows_count():
    assert rule_label(["Bash(a)", "Bash(b)", "Bash(c)"]) == "Bash(a) 외 2건"


def test_build_permission_buttons_with_rules_has_three():
    blocks = build_permission_buttons("aid-1", ["Bash(npm run *)"])
    assert blocks["type"] == "actions"
    assert blocks["block_id"] == "aid-1"
    ids = [e["action_id"] for e in blocks["elements"]]
    assert ids == ["approve_action", "approve_rule_action", "deny_action"]
    # 규칙 라벨이 don't-ask 버튼 텍스트에 포함
    rule_btn = blocks["elements"][1]
    assert "Bash(npm run *)" in rule_btn["text"]["text"]
    assert rule_btn["value"] == "approve_rule"


def test_build_permission_buttons_without_rules_has_two():
    blocks = build_permission_buttons("aid-2", [])
    ids = [e["action_id"] for e in blocks["elements"]]
    assert ids == ["approve_action", "deny_action"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_perm_buttons.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'perm_buttons'`

- [ ] **Step 3: 구현**

`code/app/perm_buttons.py`:

```python
"""PermissionRequest permission_suggestions → Slack 버튼/규칙 (순수 함수, I/O 없음)."""


def extract_rules(permission_suggestions) -> list:
    """addRules 타입 suggestion에서 ruleContent 목록 추출."""
    rules = []
    for s in permission_suggestions or []:
        if s.get("type") == "addRules":
            for r in s.get("rules", []):
                rc = r.get("ruleContent")
                if rc:
                    rules.append(rc)
    return rules


def rule_label(rules: list) -> str:
    """버튼/미리보기용 라벨. 첫 규칙 + 나머지 건수."""
    if not rules:
        return ""
    if len(rules) == 1:
        return rules[0]
    return f"{rules[0]} 외 {len(rules) - 1}건"


def build_permission_buttons(approval_id: str, rules: list) -> dict:
    """actions 블록 생성. rules 있으면 3버튼(허용/허용+규칙/거부), 없으면 2버튼."""
    elements = [
        {"type": "button", "text": {"type": "plain_text", "text": "✅ 허용"},
         "style": "primary", "value": "approve", "action_id": "approve_action"},
    ]
    if rules:
        label = rule_label(rules)
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": f"✅ 허용 + 다시 안 묻기 ({label})"[:75]},
            "value": "approve_rule", "action_id": "approve_rule_action",
        })
    elements.append(
        {"type": "button", "text": {"type": "plain_text", "text": "❌ 거부"},
         "style": "danger", "value": "deny", "action_id": "deny_action"}
    )
    return {"type": "actions", "block_id": approval_id, "elements": elements}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_perm_buttons.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add code/app/perm_buttons.py code/app/tests/test_perm_buttons.py
git commit -m "feat: add permission_suggestions button builder (co-worked with claude)"
```

---

## Task 2: ask_blocks.py — answers 빌더 + action_id (TDD)

**Files:**
- Create: `code/app/ask_blocks.py`
- Test: `code/app/tests/test_ask_blocks.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`code/app/tests/test_ask_blocks.py`:

```python
from ask_blocks import encode_action_id, decode_action_id, build_answers


def test_encode_decode_action_id():
    assert encode_action_id(0, 2) == "ask::0::2"
    assert decode_action_id("ask::0::2") == (0, 2)


def test_build_answers_single_select():
    questions = [{"question": "Color?", "multiSelect": False,
                  "options": [{"label": "Red"}, {"label": "Blue"}]}]
    assert build_answers(questions, {0: [1]}) == {"Color?": "Blue"}


def test_build_answers_multi_select_comma_join():
    questions = [{"question": "Toppings?", "multiSelect": True,
                  "options": [{"label": "Cheese"}, {"label": "Mushroom"}, {"label": "Onion"}]}]
    assert build_answers(questions, {0: [0, 2]}) == {"Toppings?": "Cheese,Onion"}


def test_build_answers_multiple_questions():
    questions = [
        {"question": "Color?", "multiSelect": False, "options": [{"label": "Red"}, {"label": "Blue"}]},
        {"question": "Size?", "multiSelect": False, "options": [{"label": "S"}, {"label": "L"}]},
    ]
    assert build_answers(questions, {0: [0], 1: [1]}) == {"Color?": "Red", "Size?": "L"}


def test_build_answers_coerces_decimal_indices():
    # 검증 결함 #1: DynamoDB가 oidx를 Decimal로 반환해도 안전해야 함
    from decimal import Decimal
    questions = [{"question": "Color?", "multiSelect": False,
                  "options": [{"label": "Red"}, {"label": "Blue"}]}]
    assert build_answers(questions, {Decimal("0"): [Decimal("1")]}) == {"Color?": "Blue"}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_ask_blocks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ask_blocks'`

- [ ] **Step 3: 구현 (빌더 일부)**

`code/app/ask_blocks.py`:

```python
"""AskUserQuestion ↔ Slack 변환 순수 함수 (I/O 없음, 테스트 대상)."""


def encode_action_id(qidx: int, oidx: int) -> str:
    return f"ask::{qidx}::{oidx}"


def decode_action_id(action_id: str) -> tuple:
    _, qidx, oidx = action_id.split("::")
    return int(qidx), int(oidx)


def build_answers(questions: list, selections: dict) -> dict:
    """selections: {qidx: [oidx,...]} → {question_text: "label" 또는 "l1,l2"}.
    DynamoDB Decimal 방어: qidx/oidx를 int로 강제(검증 결함 #1)."""
    answers = {}
    for qidx, oidxs in selections.items():
        q = questions[int(qidx)]
        labels = [q["options"][int(o)]["label"] for o in oidxs]
        answers[q["question"]] = ",".join(labels)
    return answers
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_ask_blocks.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add code/app/ask_blocks.py code/app/tests/test_ask_blocks.py
git commit -m "feat: add AskUserQuestion answers builder (co-worked with claude)"
```

---

## Task 3: ask_blocks.py — Slack 블록 빌더 (TDD)

**Files:**
- Modify: `code/app/ask_blocks.py`
- Test: `code/app/tests/test_ask_blocks.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`code/app/tests/test_ask_blocks.py` 끝에 추가:

```python
from ask_blocks import build_ask_blocks


def test_single_select_has_option_buttons_unique_block_ids():
    questions = [
        {"question": "Which color?", "header": "Color", "multiSelect": False,
         "options": [{"label": "Red", "description": "warm"}, {"label": "Blue", "description": "cool"}]},
    ]
    blocks = build_ask_blocks("ask-1", questions)
    actions = [b for b in blocks if b["type"] == "actions"]
    assert len(actions) == 1
    assert [e["action_id"] for e in actions[0]["elements"]] == ["ask::0::0", "ask::0::1"]
    assert actions[0]["block_id"] == "ask-1::0"
    assert actions[0]["elements"][0]["text"]["text"] == "Red"


def test_multi_question_unique_block_ids():
    questions = [
        {"question": "Color?", "multiSelect": False, "options": [{"label": "Red"}, {"label": "Blue"}]},
        {"question": "Size?", "multiSelect": False, "options": [{"label": "S"}, {"label": "L"}]},
    ]
    blocks = build_ask_blocks("ask-7", questions)
    block_ids = [b["block_id"] for b in blocks if b["type"] == "actions"]
    assert block_ids == ["ask-7::0", "ask-7::1"]


def test_multi_select_uses_checkboxes_and_submit():
    questions = [
        {"question": "Toppings?", "header": "Topping", "multiSelect": True,
         "options": [{"label": "Cheese"}, {"label": "Onion"}]},
    ]
    blocks = build_ask_blocks("ask-9", questions)
    has_checkboxes = any(e.get("type") == "checkboxes"
                         for b in blocks if b["type"] == "actions" for e in b["elements"])
    assert has_checkboxes
    submit_ids = [e["action_id"] for b in blocks if b["type"] == "actions"
                  for e in b["elements"] if e.get("action_id", "").startswith("ask_submit::")]
    assert submit_ids == ["ask_submit::0"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_ask_blocks.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_ask_blocks'`

- [ ] **Step 3: 구현 추가**

`code/app/ask_blocks.py` 끝에 추가:

```python
def build_ask_blocks(ask_id: str, questions: list) -> list:
    """AskUserQuestion questions → Slack blocks.
    단일선택: 옵션별 button. 멀티선택: checkboxes + Submit.
    block_id = {ask_id}::{qidx} (메시지 내 유일성 — Slack 요구)."""
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Claude Code 선택 요청*"}},
    ]
    for qidx, q in enumerate(questions):
        header = q.get("header", "")
        prompt = q["question"]
        title = f"*{header}* — {prompt}" if header else f"*{prompt}*"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": title}})

        if q.get("multiSelect"):
            options = [
                {"text": {"type": "plain_text", "text": opt["label"][:75]}, "value": str(oidx)}
                for oidx, opt in enumerate(q["options"])
            ]
            blocks.append({
                "type": "actions", "block_id": f"{ask_id}::{qidx}",
                "elements": [
                    {"type": "checkboxes", "options": options, "action_id": f"ask_check::{qidx}"},
                    {"type": "button", "text": {"type": "plain_text", "text": "Submit"},
                     "style": "primary", "value": "submit", "action_id": f"ask_submit::{qidx}"},
                ],
            })
        else:
            buttons = []
            for oidx, opt in enumerate(q["options"]):
                buttons.append({
                    "type": "button", "text": {"type": "plain_text", "text": opt["label"][:75]},
                    "value": str(oidx), "action_id": encode_action_id(qidx, oidx),
                })
                desc = opt.get("description", "")
                if desc:
                    blocks.append({"type": "context",
                                   "elements": [{"type": "mrkdwn", "text": f"• *{opt['label']}*: {desc}"}]})
            blocks.append({"type": "actions", "block_id": f"{ask_id}::{qidx}", "elements": buttons})
    return blocks
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_ask_blocks.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add code/app/ask_blocks.py code/app/tests/test_ask_blocks.py
git commit -m "feat: add Slack block builder for AskUserQuestion (co-worked with claude)"
```

---

## Task 4: lambda_handler.py — approve_rule + ask 분기 (TDD)

기존 approve/deny를 보존하고, approve_rule(규칙 적용)과 ask 계열(selections 누적)을 추가한다.

**Files:**
- Modify: `code/app/lambda_handler.py`
- Test: `code/app/tests/test_lambda_interactive.py`

> **검증 결함 #2 반영:** `lambda_handler.py:25`의 `dynamodb = boto3.resource("dynamodb")`는 region 없이 import 시점에 실행되어, 테스트가 `lambda_handler`를 import하면 `botocore.exceptions.NoRegionError`로 **pytest collection 전체가 중단**된다(repo에 conftest/region 설정 없음). 프로덕션 Lambda는 런타임이 region을 주지만 테스트는 아니므로, approval_server와 동일한 fallback을 추가한다. Step 0에서 먼저 처리.

- [ ] **Step 0: lambda_handler에 region fallback 추가 (테스트 import 가능하게)**

`code/app/lambda_handler.py`의 `dynamodb = boto3.resource("dynamodb")` (25행)을 다음으로 교체:

```python
# AWS_REGION 충돌 방지: 전용 변수 우선, 없으면 ap-northeast-2 (Lambda 런타임은 자체 region 주입)
_AWS_REGION = os.environ.get("SLACK_APPROVAL_AWS_REGION") or os.environ.get("AWS_REGION") or "ap-northeast-2"
dynamodb = boto3.resource("dynamodb", region_name=_AWS_REGION)
```

(`os`는 이미 import됨 — 파일 상단 확인.)

검증: `cd code/app && .venv/bin/python -c "import lambda_handler; print('import OK')"` → `import OK`

- [ ] **Step 1: 실패하는 테스트 작성**

`code/app/tests/test_lambda_interactive.py`:

```python
from lambda_handler import parse_ask_action, merge_selection


def test_parse_ask_action_single_select():
    payload = {"actions": [{"action_id": "ask::0::1", "block_id": "ask-abc::0"}]}
    assert parse_ask_action(payload) == {"ask_id": "ask-abc", "qidx": 0, "selected": [1], "is_submit": False}


def test_parse_ask_action_multi_submit():
    payload = {
        "actions": [{"action_id": "ask_submit::0", "block_id": "ask-xyz::0"}],
        "state": {"values": {"ask-xyz::0": {"ask_check::0": {
            "selected_options": [{"value": "0"}, {"value": "2"}]}}}},
    }
    assert parse_ask_action(payload) == {"ask_id": "ask-xyz", "qidx": 0, "selected": [0, 2], "is_submit": True}


# 검증 결함 #4: selections 누적/완료 임계 로직을 순수함수로 분리해 테스트
def test_merge_selection_accumulates_and_flips_status():
    # 2개 질문 중 1개만 응답 → pending
    sel, status = merge_selection({"0": [1]}, qidx=1, selected=[0], expected_count=2)
    assert sel == {"0": [1], "1": [0]}
    assert status == "answered"  # 이제 2개 모두 → answered


def test_merge_selection_partial_stays_pending():
    sel, status = merge_selection({}, qidx=0, selected=[1], expected_count=2)
    assert sel == {"0": [1]}
    assert status == "pending"


def test_merge_selection_decimal_keys_normalized():
    # DynamoDB Decimal round-trip 방어: 기존 selections에 Decimal이 섞여도 안전
    from decimal import Decimal
    sel, status = merge_selection({"0": [Decimal("1")]}, qidx=1, selected=[2], expected_count=2)
    assert status == "answered"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_lambda_interactive.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_ask_action'` (Step 0 덕분에 NoRegionError가 아닌 import-name 오류로 정상 실패)

- [ ] **Step 3: parse_ask_action + merge_selection 구현**

`code/app/lambda_handler.py`의 `dynamodb = boto3.resource(...)` 아래(handler 정의 위)에 추가:

```python
def parse_ask_action(payload: dict) -> dict:
    """Slack ask action → {ask_id, qidx, selected:[oidx], is_submit}.
    단일선택: action_id=ask::qidx::oidx, block_id=ask_id::qidx.
    멀티 Submit: action_id=ask_submit::qidx, block_id=ask_id::qidx,
                state.values[block_id][ask_check::qidx].selected_options[].value."""
    action = payload["actions"][0]
    action_id = action["action_id"]
    block_id = action["block_id"]
    ask_id = block_id.split("::")[0]

    if action_id.startswith("ask_submit::"):
        qidx = int(action_id.split("::")[1])
        state = payload.get("state", {}).get("values", {}).get(block_id, {})
        opts = state.get(f"ask_check::{qidx}", {}).get("selected_options", [])
        selected = sorted(int(o["value"]) for o in opts)
        return {"ask_id": ask_id, "qidx": qidx, "selected": selected, "is_submit": True}

    _, qidx_s, oidx_s = action_id.split("::")
    return {"ask_id": ask_id, "qidx": int(qidx_s), "selected": [int(oidx_s)], "is_submit": False}


def merge_selection(existing: dict, qidx: int, selected: list, expected_count: int) -> tuple:
    """기존 selections에 qidx 응답 병합 → (selections, status). 순수함수(테스트 대상).
    검증 결함 #4: 누적/완료 임계 로직 격리. (참고: 실제 원자적 쓰기는 _handle_ask가 담당)"""
    selections = dict(existing)
    selections[str(qidx)] = selected
    status = "answered" if len(selections) >= expected_count else "pending"
    return selections, status
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_lambda_interactive.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: handler에 분기 연결**

`code/app/lambda_handler.py`의 `handler()`에서 현재 "Action 추출" 블록(아래)을 찾는다:

```python
    # Action 추출
    try:
        action = payload["actions"][0]
        decision = action["value"]  # "approve" or "deny"
        approval_id = action["block_id"]
    except (KeyError, IndexError) as exc:
        logger.error("Failed to extract action data: %s", exc)
        return {"statusCode": 400, "body": "Bad request"}

    if decision not in ("approve", "deny"):
        logger.error("Unexpected action value: %s", decision)
        return {"statusCode": 400, "body": "Bad request"}
```

이 블록 **앞에** ask/approve_rule 분기를 삽입:

```python
    # ask 계열 분기 (AskUserQuestion 양방향)
    first_action_id = payload.get("actions", [{}])[0].get("action_id", "")
    if first_action_id.startswith(("ask::", "ask_submit::")):
        return _handle_ask(payload, table_name)
```

그리고 기존 블록의 value 검증을 approve_rule 허용으로 확장:

```python
    if decision not in ("approve", "deny", "approve_rule"):
        logger.error("Unexpected action value: %s", decision)
        return {"statusCode": 400, "body": "Bad request"}
```

그리고 status 매핑 부분:

```python
    status = "approved" if decision == "approve" else "denied"
```

다음으로 교체:

```python
    status = "denied" if decision == "deny" else "approved"
    apply_rule = decision == "approve_rule"
```

DynamoDB update_item을 apply_rule 포함하도록 교체. 현재:

```python
        table.update_item(
            Key={"approval_id": approval_id},
            UpdateExpression="SET #s = :status, decided_by = :user, decided_at = :ts",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": status,
                ":user": user_name,
                ":ts": int(time.time()),
            },
        )
```

다음으로 교체:

```python
        table.update_item(
            Key={"approval_id": approval_id},
            UpdateExpression="SET #s = :status, decided_by = :user, decided_at = :ts, apply_rule = :ar",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": status,
                ":user": user_name,
                ":ts": int(time.time()),
                ":ar": apply_rule,
            },
        )
```

- [ ] **Step 5b: build_response_message가 approve_rule을 처리하도록 수정**

현재 `build_response_message` 상단(43~45행):

```python
def build_response_message(action: str, user_name: str, original_blocks: list) -> dict:
    emoji = ":white_check_mark:" if action == "approve" else ":x:"
    status_text = "Approved" if action == "approve" else "Denied"
```

다음으로 교체 (deny만 ❌, approve_rule은 "rule added" 표기):

```python
def build_response_message(action: str, user_name: str, original_blocks: list) -> dict:
    emoji = ":x:" if action == "deny" else ":white_check_mark:"
    status_text = {
        "deny": "Denied",
        "approve_rule": "Approved (rule added)",
    }.get(action, "Approved")
```

- [ ] **Step 6: _handle_ask 구현**

`code/app/lambda_handler.py`의 `handler` 정의 **위**에 추가:

> **검증 결함 #3 반영 (lost-update race):** 각 Slack 클릭은 독립 Lambda 호출이라, 다중질문 카드에서 두 클릭이 동시에 `selections={}`를 읽고 전체 map을 `SET selections = :sel`로 덮어쓰면 한쪽 답이 유실된다. 이를 막기 위해 **전체 map 교체 대신 qidx 키만 원자적으로 갱신**(`SET selections.#q = :sel`)하고, `ReturnValues="ALL_NEW"`로 갱신 후 전체 selections를 받아 완료를 재계산한다. `/ask`의 `put_item`이 `selections={}`를 먼저 초기화하므로 부모 map은 항상 존재한다.

```python
def _handle_ask(payload: dict, table_name: str) -> dict:
    """ask 버튼/Submit → DynamoDB selections 누적, 완료 시 status=answered.
    원자적 per-key 갱신으로 다중질문 동시클릭 lost-update 방지."""
    parsed = parse_ask_action(payload)
    ask_id = parsed["ask_id"]
    qidx = parsed["qidx"]
    user_name = payload.get("user", {}).get("name", "unknown")
    response_url = payload.get("response_url", "")

    table = dynamodb.Table(table_name)
    try:
        # qidx 키만 원자적으로 set (전체 map 미교체) → 동시 클릭 안전
        resp = table.update_item(
            Key={"approval_id": ask_id},
            UpdateExpression="SET selections.#q = :sel, decided_by = :u, decided_at = :ts",
            ExpressionAttributeNames={"#q": str(qidx)},
            ExpressionAttributeValues={
                ":sel": parsed["selected"], ":u": user_name, ":ts": int(time.time()),
            },
            ReturnValues="ALL_NEW",
        )
        item = resp.get("Attributes", {})
        selections = item.get("selections", {})
        expected = int(item.get("expected_count", 1))
        new_status = "answered" if len(selections) >= expected else "pending"
        # 완료 시에만 status 갱신 (별도 update — pending은 put_item 초기값 유지)
        if new_status == "answered":
            table.update_item(
                Key={"approval_id": ask_id},
                UpdateExpression="SET #s = :st",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":st": "answered"},
            )
    except Exception:
        logger.exception("ask update failed for %s", ask_id)
        return {"statusCode": 500, "body": "Internal server error"}

    if response_url and response_url.startswith("https://hooks.slack.com/"):
        done = new_status == "answered"
        msg = (":white_check_mark: 모든 질문 응답 완료" if done
               else f":ballot_box_with_check: 질문 {qidx + 1} 응답됨 (by {user_name})")
        try:
            req = Request(response_url,
                          data=json.dumps({"replace_original": False, "text": msg}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
            urlopen(req)  # nosec B310
        except Exception:
            logger.exception("ask slack update failed")
    return {"statusCode": 200, "body": ""}
```

> **참고:** `merge_selection` 순수함수(Step 3)는 누적/임계 로직의 단위 테스트용이며, `_handle_ask`는 동시성 안전을 위해 DynamoDB 원자 연산을 직접 쓴다(같은 로직, 다른 실행 경로). 둘의 완료 판정(`len >= expected_count`)은 동일하다.

- [ ] **Step 7: 전체 lambda 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 8: Commit**

```bash
git add code/app/lambda_handler.py code/app/tests/test_lambda_interactive.py
git commit -m "feat: handle approve_rule and AskUserQuestion selections in lambda (co-worked with claude)"
```

---

## Task 5: approval_server.py — /hook에 동적 버튼 + permissionRule 응답

**Files:**
- Modify: `code/app/approval_server.py`

- [ ] **Step 1: import 추가**

`code/app/approval_server.py` 상단(`from summarizer import summarize` 아래):

```python
from perm_buttons import extract_rules, build_permission_buttons
```

- [ ] **Step 2: build_slack_blocks 시그니처에 rules 추가**

현재:

```python
def build_slack_blocks(
    approval_id: str,
    tool_name: str,
    tool_input: str,
    cwd: str = "",
    user_context: str = "",
    summary: dict | None = None,
) -> list:
```

다음으로 교체:

```python
def build_slack_blocks(
    approval_id: str,
    tool_name: str,
    tool_input: str,
    cwd: str = "",
    user_context: str = "",
    summary: dict | None = None,
    rules: list | None = None,
) -> list:
```

- [ ] **Step 3: 하드코딩된 actions 블록을 perm_buttons로 위임**

현재 `build_slack_blocks` 끝의 actions 블록(approve_action/deny_action 하드코딩, 약 146~167행):

```python
    blocks.append(
        {
            "type": "actions",
            "block_id": approval_id,
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "value": "approve",
                    "action_id": "approve_action",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Deny"},
                    "style": "danger",
                    "value": "deny",
                    "action_id": "deny_action",
                },
            ],
        }
    )

    return blocks
```

다음으로 교체:

```python
    blocks.append(build_permission_buttons(approval_id, rules or []))

    return blocks
```

- [ ] **Step 4: /hook에서 suggestion 파싱 + rules 전달 + DynamoDB 보관**

`hook()`에서 `tool_input` 파싱 직후(`cwd = body.get("cwd", "")` 근처)에 추가:

```python
    permission_suggestions = body.get("permission_suggestions", [])
    rules = extract_rules(permission_suggestions)
```

`build_slack_blocks(...)` 호출에 rules 추가:

```python
    blocks = build_slack_blocks(approval_id, tool_name, tool_input, cwd, user_context, summary, rules)
```

DynamoDB `put_item`의 Item dict에 rules 보관. 현재:

```python
    table.put_item(
        Item={
            "approval_id": approval_id,
            "status": "pending",
            "tool_name": tool_name,
            "tool_input": tool_input[:1000],
            "cwd": cwd,
            "user_context": user_context[:500],
            "expires_at": expires_at,
            "created_at": int(time.time()),
        }
    )
```

`"created_at": ...` 줄 아래에 `"rules"`를 추가:

```python
    table.put_item(
        Item={
            "approval_id": approval_id,
            "status": "pending",
            "tool_name": tool_name,
            "tool_input": tool_input[:1000],
            "cwd": cwd,
            "user_context": user_context[:500],
            "expires_at": expires_at,
            "created_at": int(time.time()),
            "rules": json.dumps(rules, ensure_ascii=False),
        }
    )
```

- [ ] **Step 5: polling 결과에 permissionRule 반영**

`hook()` 끝의 응답 분기. 현재:

```python
    if result == "approved":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }
    else:
        reason = "Denied via Slack" if result == "denied" else "Timed out waiting for approval"
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny", "reason": reason},
            }
        }
```

다음으로 교체:

```python
    if result == "approved":
        decision = {"behavior": "allow"}
        # approve_rule로 승인된 경우 DynamoDB의 apply_rule 확인 → permissionRule 부착
        try:
            item = table.get_item(Key={"approval_id": approval_id}).get("Item", {})
            if item.get("apply_rule") and rules:
                decision["permissionRule"] = rules[0]  # 첫 규칙(단일 문자열). 배열 허용은 E2E 확인
        except Exception:
            logger.exception("apply_rule lookup failed for %s", approval_id)
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}
    else:
        reason = "Denied via Slack" if result == "denied" else "Timed out waiting for approval"
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny", "reason": reason},
            }
        }
```

> **주의:** `result == "approved"`는 lambda가 status를 approve_rule이든 approve든 모두 `"approved"`로 저장하므로(Task 4 Step 5), 여기선 추가로 `apply_rule` 플래그를 본다. `poll_dynamodb`는 status만 보고 approved를 반환하므로 변경 불필요.

- [ ] **Step 6: import 스모크 + 전체 테스트**

Run:
```bash
cd code/app && .venv/bin/python -c "import approval_server; print('import OK')"
cd code/app && .venv/bin/pytest tests/ -v
```
Expected: import OK, 전체 통과.

- [ ] **Step 7: Commit**

```bash
git add code/app/approval_server.py
git commit -m "feat: dynamic permission buttons with permissionRule in /hook (co-worked with claude)"
```

---

## Task 6: approval_server.py — /ask 엔드포인트 + poll_ask

**Files:**
- Modify: `code/app/approval_server.py`

- [ ] **Step 1: import 추가**

상단에 추가:

```python
from ask_blocks import build_ask_blocks, build_answers
```

- [ ] **Step 2: poll_ask 함수 추가**

`poll_dynamodb` 함수 **아래**에 추가:

```python
async def poll_ask(ask_id: str, request: Request, message_ts: str, blocks: list, questions: list):
    """ask 응답 polling. answered면 answers dict 반환, terminal/timeout이면 None."""
    elapsed = 0
    while elapsed < POLL_TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

        if await request.is_disconnected():
            logger.info("ask_id=%s resolved by terminal", ask_id)
            update_slack_message(message_ts, blocks, ":desktop_computer:", "터미널에서 응답됨")
            return None

        try:
            item = table.get_item(Key={"approval_id": ask_id}).get("Item", {})
            if item.get("status") == "answered":
                # 검증 결함 #1 (CRITICAL): boto3 resource API는 저장된 숫자를 Decimal로
                # 역직렬화한다. 키뿐 아니라 oidx VALUE도 int로 강제하지 않으면
                # build_answers의 options[Decimal] 인덱싱이 TypeError → 답변 전부 유실.
                selections = {
                    int(k): [int(o) for o in v]
                    for k, v in item.get("selections", {}).items()
                }
                return build_answers(questions, selections)
        except Exception:
            logger.exception("ask poll failed for %s", ask_id)

    logger.warning("ask_id=%s timed out", ask_id)
    update_slack_message(message_ts, blocks, ":alarm_clock:", "시간 초과")
    return None
```

- [ ] **Step 3: /ask 엔드포인트 추가**

`@app.post("/hook")` 함수 **위**에 추가:

```python
@app.post("/ask")
async def ask(request: Request):
    body = await request.json()
    tool_input = body.get("tool_input", {})
    questions = tool_input.get("questions", [])
    cwd = body.get("cwd", "")

    if not questions:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}

    ask_id = str(uuid.uuid4())
    expires_at = int(time.time()) + 600
    table.put_item(Item={
        "approval_id": ask_id,
        "status": "pending",
        "questions": json.dumps(questions, ensure_ascii=False),
        "selections": {},
        "expected_count": len(questions),
        "cwd": cwd,
        "expires_at": expires_at,
        "created_at": int(time.time()),
    })

    blocks = build_ask_blocks(ask_id, questions)
    slack_resp = slack.chat_postMessage(
        channel=SLACK_CHANNEL_ID,
        text=f"Claude Code 선택 요청 ({len(questions)}개 질문)",
        blocks=blocks,
    )
    message_ts = slack_resp["ts"]
    logger.info("ask_id=%s posted, ts=%s", ask_id, message_ts)

    answers = await poll_ask(ask_id, request, message_ts, blocks, questions)

    if answers is None:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}

    update_slack_message(message_ts, blocks, ":white_check_mark:", "응답 완료")
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "updatedInput": {**tool_input, "answers": answers},
    }}
```

> **DynamoDB 주의:** `selections`는 빈 dict로 시작(boto3 빈 Map 허용). `questions`는 JSON 문자열로 저장하지만 `poll_ask`는 메모리의 questions 인자를 쓰므로 재파싱 불필요.

- [ ] **Step 4: import 스모크 + 전체 테스트**

Run:
```bash
cd code/app && .venv/bin/python -c "import approval_server; print('import OK')"
cd code/app && .venv/bin/pytest tests/ -v
```
Expected: import OK, 전체 통과.

- [ ] **Step 5: Commit**

```bash
git add code/app/approval_server.py
git commit -m "feat: add /ask endpoint for interactive AskUserQuestion over Slack (co-worked with claude)"
```

---

## Task 7: settings.json — AskUserQuestion hook 등록

**Files:**
- Modify: `~/.claude/settings.json`

- [ ] **Step 1: 백업**

Run: `cp ~/.claude/settings.json ~/.claude/settings.json.bak && echo backed-up`
Expected: `backed-up`

- [ ] **Step 2: jq로 PreToolUse(AskUserQuestion) http hook 등록**

Run:
```bash
jq '.hooks.PreToolUse = ((.hooks.PreToolUse // []) + [
  {matcher:"AskUserQuestion", hooks:[{type:"http", url:"http://localhost:8080/ask", timeout:300}]}
])' ~/.claude/settings.json > /tmp/cc-settings.json && mv /tmp/cc-settings.json ~/.claude/settings.json
jq '.hooks.PreToolUse' ~/.claude/settings.json
```
Expected: AskUserQuestion http :8080/ask 항목이 보임. 기존 PreToolUse 항목 유지.

> **주의:** 기존 PreToolUse에 다른 hook(예: Bash sensitive-info-guard)이 있으면 보존되어야 한다. `+`로 append하므로 안전. 확인: 기존 항목이 그대로 있는지 출력에서 검증.

- [ ] **Step 3: 서버 재기동**

Run:
```bash
launchctl kickstart -k "gui/$(id -u)/com.oh-my-cc-agent" && sleep 2 && curl -s localhost:8080/health
```
Expected: `{"status":"ok"}`

> 롤백: `cp ~/.claude/settings.json.bak ~/.claude/settings.json` 후 재기동.

---

## Task 8: 수동 E2E 검증 (운영 :8080)

**Files:** (없음 — 검증만)

- [ ] **Step 1: PermissionRequest 3버튼 표시**

권한이 필요한(suggestion 동반) Bash 명령을 유발. Slack 카드에 `✅ 허용` / `✅ 허용 + 다시 안 묻기 (<규칙>)` / `❌ 거부` 3버튼이 보이는지 확인. suggestion 없는 요청은 2버튼인지 확인.

- [ ] **Step 2: approve_rule 동작 (미검증 항목 §3.1 확인)**

`✅ 허용 + 다시 안 묻기` 클릭 → 작업 허용됨 확인. **그 후 동일 명령을 재실행**해 권한을 다시 묻는지 확인:
- 안 물음 → `permissionRule` 동작 ✅
- 다시 물음 → permissionRule 미동작. **이 경우 Task 5 Step 5의 버튼 라벨을 "허용(이번만)"으로 조정하고 permissionRule 부착 코드를 제거하는 후속 커밋 필요** (별도 보고).

- [ ] **Step 3: AskUserQuestion 단일선택 양방향**

세션에서 단일 질문 유발 → Slack 옵션 버튼 클릭 → Claude가 그 답으로 진행(터미널 프롬프트 억제) 확인.

- [ ] **Step 4: AskUserQuestion 멀티선택**

multiSelect 질문 → checkboxes 복수 선택 + Submit → Claude가 콤마 조합 수신 확인.

- [ ] **Step 4b: 다중 질문 양방향 (Decimal·race 검증)**

2~4개 질문(단일+멀티 혼합) 유발 → Slack에서 각 질문을 빠르게 연속 클릭 → **모든 질문 응답 시에만** Claude가 진행하고 answers 맵이 완전한지 확인. (검증 결함 #1 Decimal coercion + #3 per-key 원자 갱신이 실제로 동작하는지 — 답이 유실되거나 timeout되면 회귀.)

- [ ] **Step 5: 터미널 우선 응답 + 회귀**

- ask 질문 뜬 뒤 Slack 미클릭, 터미널에서 직접 응답 → Slack "터미널에서 응답됨" 갱신.
- 기존 PermissionRequest approve/deny가 정상 동작(회귀 없음).

- [ ] **Step 6: hands-off.md 갱신 + Commit**

`hands-off.md` 최상단에 구현 완료 Change Log(타임스탬프, 변경 파일, E2E 결과, 미검증 항목 결론) 추가 후:

```bash
git add hands-off.md
git commit -m "docs: record interactive choices implementation (co-worked with claude)"
```

---

## 검증 체크리스트 (실행자용)

- [ ] lambda_handler import 가능 (region fallback — 검증 결함 #2)
- [ ] 단위 테스트 전체 통과: `cd code/app && .venv/bin/pytest tests/ -v`
- [ ] build_answers가 Decimal 인덱스에서 동작 (검증 결함 #1)
- [ ] merge_selection 누적/임계 로직 테스트 (검증 결함 #4)
- [ ] PermissionRequest 3버튼(suggestion 있을 때) / 2버튼(없을 때)
- [ ] approve_rule → permissionRule 부착 (E2E에서 실효성 확인, 안 되면 라벨 조정)
- [ ] AskUserQuestion 단일/멀티 양방향
- [ ] 다중 질문 동시클릭 시 답 유실 없음 (per-key 원자 갱신 — 검증 결함 #3)
- [ ] 터미널 우선 응답 시 Slack 갱신
- [ ] 기존 approve/deny 회귀 없음
- [ ] settings.json 기존 hook 보존
```
