# Slack Interactive Question & Event Notify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude Code의 `AskUserQuestion` 선택지를 Slack 버튼으로 노출하고 Slack에서 선택→Claude로 반환하는 완전 양방향 흐름을 구현하고, ExitPlanMode/Stop 마커 단방향 알림을 추가한다.

**Architecture:** 기존 PermissionRequest(`type:http` → `/hook` → DynamoDB polling → hookSpecificOutput 반환) 패턴을 그대로 복제하여 `/ask` 엔드포인트를 만든다. Slack 버튼 클릭은 기존 Lambda(`lambda_handler.py`)에 `ask::` 분기를 추가해 DynamoDB에 답을 누적한다. ExitPlanMode/Stop은 shell hook → `/notify` 단방향.

**Tech Stack:** Python 3.14, FastAPI, boto3(DynamoDB), slack_sdk, AWS Lambda, pytest(신규), bash hook + jq.

**검증 완료 전제 (PoC, `parking_lot/poc-askuserquestion/`):**
- AskUserQuestion → `PreToolUse` hook, interactive(default mode)에서 답 주입 성공
- 단일선택: `updatedInput.answers = {"<question>": "<label>"}`
- 멀티선택: `updatedInput.answers = {"<question>": "<label1>,<label2>"}` (콤마 join) 확인됨

---

## File Structure

| 파일 | 책임 | 변경 |
|---|---|---|
| `code/app/ask_blocks.py` | AskUserQuestion → Slack 블록 빌더, answers 빌더, ask payload 파서 (순수 함수, 테스트 대상) | **신규** |
| `code/app/markers.py` | Stop 응답 텍스트에서 마커 검출 + summary 추출 (순수 함수) | **신규** |
| `code/app/approval_server.py` | `/ask` 엔드포인트, `poll_ask()`, `/notify` 이모지 확장 | 수정 |
| `code/app/lambda_handler.py` | `ask::` action 분기 → DynamoDB answers 누적 | 수정 |
| `~/.claude/hooks/slack-event-notify.sh` | ExitPlanMode/Stop → `/notify` | **신규** |
| `~/.claude/settings.json` | PreToolUse(AskUserQuestion http, ExitPlanMode command), Stop hook 등록 | 수정 (jq merge) |
| `~/.claude/CLAUDE.md` | Notification Markers 섹션 | 수정 |
| `code/app/requirements.txt` | pytest, httpx 추가 (dev) | 수정 |
| `code/app/tests/` | 단위 테스트 | **신규** |

**설계 메모 — answers 빌더를 순수 함수로 분리하는 이유:** Slack action_id(`ask::<qidx>::<oidx>`) → label 매핑, multiSelect 콤마 join, 다중 질문 누적 로직은 DynamoDB/Slack I/O 없이 테스트 가능해야 한다. `approval_server.py`와 `lambda_handler.py` 양쪽에서 import하여 DRY를 보장한다.

---

## Task 1: pytest 인프라 셋업

**Files:**
- Modify: `code/app/requirements.txt`
- Create: `code/app/tests/__init__.py`
- Create: `code/app/pytest.ini`

- [ ] **Step 1: requirements.txt에 dev 의존성 추가**

`code/app/requirements.txt` 전체를 다음으로 교체:

```
fastapi>=0.104.0
uvicorn>=0.24.0
boto3>=1.34.0
slack-sdk>=3.27.0
pytest>=8.0.0
httpx>=0.27.0
```

- [ ] **Step 2: pytest 설정 생성**

`code/app/pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -v
```

- [ ] **Step 3: tests 패키지 초기화**

`code/app/tests/__init__.py`: (빈 파일)

```python
```

- [ ] **Step 4: venv에 의존성 설치**

Run:
```bash
cd code/app && .venv/bin/pip install -q pytest httpx && .venv/bin/pytest --version
```
Expected: `pytest 8.x.x` 출력

- [ ] **Step 5: Commit**

```bash
git add code/app/requirements.txt code/app/pytest.ini code/app/tests/__init__.py
git commit -m "test: add pytest infrastructure"
```

---

## Task 2: ask_blocks.py — answers 빌더 (순수 함수, TDD)

`ask::<qidx>::<oidx>` action_id 인코딩과 DynamoDB에 누적된 선택들을 `{question: label(s)}` answers 맵으로 변환하는 로직.

**Files:**
- Create: `code/app/ask_blocks.py`
- Test: `code/app/tests/test_ask_blocks.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`code/app/tests/test_ask_blocks.py`:

```python
from ask_blocks import build_answers, encode_action_id, decode_action_id


def test_encode_decode_action_id():
    aid = encode_action_id(0, 2)
    assert aid == "ask::0::2"
    assert decode_action_id("ask::0::2") == (0, 2)


def test_build_answers_single_select():
    # questions[0].question = "Color?", 선택: qidx 0 -> oidx 1 (label "Blue")
    questions = [
        {"question": "Color?", "multiSelect": False,
         "options": [{"label": "Red"}, {"label": "Blue"}]},
    ]
    selections = {0: [1]}  # qidx -> [oidx,...]
    answers = build_answers(questions, selections)
    assert answers == {"Color?": "Blue"}


def test_build_answers_multi_select_comma_join():
    questions = [
        {"question": "Toppings?", "multiSelect": True,
         "options": [{"label": "Cheese"}, {"label": "Mushroom"}, {"label": "Onion"}]},
    ]
    selections = {0: [0, 2]}
    answers = build_answers(questions, selections)
    assert answers == {"Toppings?": "Cheese,Onion"}


def test_build_answers_multiple_questions():
    questions = [
        {"question": "Color?", "multiSelect": False, "options": [{"label": "Red"}, {"label": "Blue"}]},
        {"question": "Size?", "multiSelect": False, "options": [{"label": "S"}, {"label": "L"}]},
    ]
    selections = {0: [0], 1: [1]}
    answers = build_answers(questions, selections)
    assert answers == {"Color?": "Red", "Size?": "L"}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_ask_blocks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ask_blocks'`

- [ ] **Step 3: 최소 구현**

`code/app/ask_blocks.py`:

```python
"""AskUserQuestion ↔ Slack 변환 순수 함수 (I/O 없음, 테스트 대상)."""


def encode_action_id(qidx: int, oidx: int) -> str:
    return f"ask::{qidx}::{oidx}"


def decode_action_id(action_id: str) -> tuple[int, int]:
    _, qidx, oidx = action_id.split("::")
    return int(qidx), int(oidx)


def build_answers(questions: list, selections: dict) -> dict:
    """selections: {qidx: [oidx,...]} → {question_text: "label" 또는 "l1,l2"}."""
    answers = {}
    for qidx, oidxs in selections.items():
        q = questions[qidx]
        labels = [q["options"][o]["label"] for o in oidxs]
        answers[q["question"]] = ",".join(labels)
    return answers
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_ask_blocks.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add code/app/ask_blocks.py code/app/tests/test_ask_blocks.py
git commit -m "feat: add answers builder for AskUserQuestion"
```

---

## Task 3: ask_blocks.py — Slack 블록 빌더 (TDD)

질문/옵션을 Slack 블록으로 렌더링. 단일선택은 옵션별 button, 멀티선택은 multi_static_select + Submit.

**Files:**
- Modify: `code/app/ask_blocks.py`
- Test: `code/app/tests/test_ask_blocks.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`code/app/tests/test_ask_blocks.py` 끝에 추가:

```python
from ask_blocks import build_ask_blocks


def test_build_ask_blocks_single_select_has_option_buttons():
    questions = [
        {"question": "Which color?", "header": "Color", "multiSelect": False,
         "options": [{"label": "Red", "description": "warm"}, {"label": "Blue", "description": "cool"}]},
    ]
    blocks = build_ask_blocks("ask-123", questions)
    # actions 블록에 옵션 수만큼 버튼, action_id가 ask::0::0, ask::0::1
    actions = [b for b in blocks if b["type"] == "actions"]
    assert len(actions) == 1
    btn_ids = [e["action_id"] for e in actions[0]["elements"]]
    assert btn_ids == ["ask::0::0", "ask::0::1"]
    # block_id는 {ask_id}::{qidx} (메시지 내 유일성 보장)
    assert actions[0]["block_id"] == "ask-123::0"
    # 버튼 텍스트는 label
    assert actions[0]["elements"][0]["text"]["text"] == "Red"


def test_build_ask_blocks_multi_question_unique_block_ids():
    questions = [
        {"question": "Color?", "multiSelect": False, "options": [{"label": "Red"}, {"label": "Blue"}]},
        {"question": "Size?", "multiSelect": False, "options": [{"label": "S"}, {"label": "L"}]},
    ]
    blocks = build_ask_blocks("ask-7", questions)
    block_ids = [b["block_id"] for b in blocks if b["type"] == "actions"]
    assert block_ids == ["ask-7::0", "ask-7::1"]  # 중복 없음 (Slack 요구사항)


def test_build_ask_blocks_multi_select_uses_checkboxes_and_submit():
    questions = [
        {"question": "Toppings?", "header": "Topping", "multiSelect": True,
         "options": [{"label": "Cheese"}, {"label": "Onion"}]},
    ]
    blocks = build_ask_blocks("ask-9", questions)
    # multiSelect → checkboxes element + submit 버튼
    has_checkboxes = any(
        e.get("type") == "checkboxes"
        for b in blocks if b["type"] == "actions"
        for e in b["elements"]
    )
    assert has_checkboxes
    submit_ids = [
        e["action_id"]
        for b in blocks if b["type"] == "actions"
        for e in b["elements"] if e.get("action_id", "").startswith("ask_submit::")
    ]
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
    단일선택: 옵션별 button (action_id=ask::qidx::oidx).
    멀티선택: checkboxes + Submit 버튼 (action_id=ask_submit::qidx)."""
    blocks = [
        {"type": "section",
         "text": {"type": "mrkdwn", "text": "*Claude Code 선택 요청*"}},
    ]
    for qidx, q in enumerate(questions):
        header = q.get("header", "")
        prompt = q["question"]
        title = f"*{header}* — {prompt}" if header else f"*{prompt}*"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": title}})

        if q.get("multiSelect"):
            options = [
                {"text": {"type": "plain_text", "text": opt["label"]},
                 "value": str(oidx)}
                for oidx, opt in enumerate(q["options"])
            ]
            blocks.append({
                "type": "actions",
                "block_id": f"{ask_id}::{qidx}",
                "elements": [
                    {"type": "checkboxes", "options": options,
                     "action_id": f"ask_check::{qidx}"},
                    {"type": "button",
                     "text": {"type": "plain_text", "text": "Submit"},
                     "style": "primary", "value": "submit",
                     "action_id": f"ask_submit::{qidx}"},
                ],
            })
        else:
            buttons = []
            for oidx, opt in enumerate(q["options"]):
                desc = opt.get("description", "")
                buttons.append({
                    "type": "button",
                    "text": {"type": "plain_text", "text": opt["label"][:75]},
                    "value": str(oidx),
                    "action_id": encode_action_id(qidx, oidx),
                })
                if desc:
                    blocks.append({"type": "context",
                                   "elements": [{"type": "mrkdwn",
                                                 "text": f"• *{opt['label']}*: {desc}"}]})
            blocks.append({"type": "actions", "block_id": f"{ask_id}::{qidx}", "elements": buttons})
    return blocks
```

> **block_id 규칙(단일/멀티 공통):** `{ask_id}::{qidx}`. Slack은 한 메시지 내 block_id 유일성을 요구하므로 다중 질문에서 충돌을 피한다. Lambda는 `block_id.split("::")[0]`으로 ask_id를 복원한다. 단일선택 버튼의 qidx는 action_id(`ask::qidx::oidx`)에서 읽는다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_ask_blocks.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add code/app/ask_blocks.py code/app/tests/test_ask_blocks.py
git commit -m "feat: add Slack block builder for AskUserQuestion"
```

---

## Task 4: markers.py — Stop 마커 파서 (TDD)

**Files:**
- Create: `code/app/markers.py`
- Test: `code/app/tests/test_markers.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`code/app/tests/test_markers.py`:

```python
from markers import detect_marker


def test_no_marker_returns_none():
    assert detect_marker("일반 응답 텍스트입니다.") is None


def test_completed_marker():
    text = "작업했습니다.\n<!notify:completed>\n배포 검증 완료. 다음 단계 진행."
    result = detect_marker(text)
    assert result["status"] == "completed"
    assert result["title"] == "✅ 작업 완료"
    assert result["summary"] == "배포 검증 완료. 다음 단계 진행."


def test_priority_completed_over_blocked():
    text = "<!notify:blocked>\nA\n<!notify:completed>\nB"
    result = detect_marker(text)
    assert result["status"] == "completed"


def test_blocked_marker_summary_to_end():
    text = "<!notify:blocked>\n권한 부족\n추가 줄"
    result = detect_marker(text)
    assert result["status"] == "blocked"
    assert result["summary"] == "권한 부족\n추가 줄"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_markers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'markers'`

- [ ] **Step 3: 최소 구현**

`code/app/markers.py`:

```python
"""Stop hook 응답 텍스트에서 notify 마커 검출 (순수 함수)."""

_MARKERS = [
    ("<!notify:completed>", "completed", "✅ 작업 완료"),
    ("<!notify:blocked>", "blocked", "🚫 작업 차단"),
    ("<!notify:milestone>", "milestone", "🎯 마일스톤 도달"),
]


def detect_marker(text: str) -> dict | None:
    """우선순위 completed > blocked > milestone. 첫 매칭 마커 이후~끝을 summary로.
    마커 없으면 None."""
    for marker, status, title in _MARKERS:
        idx = text.find(marker)
        if idx == -1:
            continue
        summary = text[idx + len(marker):].strip()
        return {"status": status, "title": title, "summary": summary[:3000]}
    return None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_markers.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add code/app/markers.py code/app/tests/test_markers.py
git commit -m "feat: add Stop notify marker parser"
```

---

## Task 5: lambda_handler.py — ask 분기 추가

Slack 옵션 버튼/Submit 클릭을 받아 DynamoDB의 `selections`에 누적하고, 모든 질문 응답 시 `status="answered"`로 전환. 기존 approve/deny 분기는 보존.

**Files:**
- Modify: `code/app/lambda_handler.py`
- Test: `code/app/tests/test_lambda_ask.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`code/app/tests/test_lambda_ask.py`:

```python
from lambda_handler import parse_ask_action


def test_parse_ask_action_single_select():
    # 단일선택 버튼 클릭: action_id=ask::0::1, block_id={ask_id}::{qidx}
    action = {"action_id": "ask::0::1", "block_id": "ask-abc::0"}
    payload = {"actions": [action]}
    result = parse_ask_action(payload)
    assert result == {"ask_id": "ask-abc", "qidx": 0, "selected": [1], "is_submit": False}


def test_parse_ask_action_multi_submit():
    # 멀티선택 Submit: action_id=ask_submit::0, 체크된 값들이 state에 존재
    payload = {
        "actions": [{"action_id": "ask_submit::0", "block_id": "ask-xyz::0"}],
        "state": {"values": {"ask-xyz::0": {"ask_check::0": {
            "selected_options": [{"value": "0"}, {"value": "2"}]
        }}}},
    }
    result = parse_ask_action(payload)
    assert result == {"ask_id": "ask-xyz", "qidx": 0, "selected": [0, 2], "is_submit": True}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_lambda_ask.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_ask_action'`

- [ ] **Step 3: parse_ask_action 구현 추가**

`code/app/lambda_handler.py`의 `import` 블록 아래(`dynamodb = ...` 위)에 추가:

```python
def parse_ask_action(payload: dict) -> dict:
    """Slack ask action(payload) → {ask_id, qidx, selected:[oidx], is_submit}.
    단일선택: action_id=ask::qidx::oidx, block_id=ask_id.
    멀티선택 Submit: action_id=ask_submit::qidx, block_id=ask_id::qidx,
                     state.values[block_id][ask_check::qidx].selected_options[].value."""
    action = payload["actions"][0]
    action_id = action["action_id"]
    block_id = action["block_id"]

    if action_id.startswith("ask_submit::"):
        qidx = int(action_id.split("::")[1])
        ask_id = block_id.split("::")[0]
        state = payload.get("state", {}).get("values", {}).get(block_id, {})
        opts = state.get(f"ask_check::{qidx}", {}).get("selected_options", [])
        selected = sorted(int(o["value"]) for o in opts)
        return {"ask_id": ask_id, "qidx": qidx, "selected": selected, "is_submit": True}

    # 단일선택 버튼: action_id=ask::qidx::oidx, block_id={ask_id}::{qidx}
    _, qidx_s, oidx_s = action_id.split("::")
    ask_id = block_id.split("::")[0]
    return {"ask_id": ask_id, "qidx": int(qidx_s),
            "selected": [int(oidx_s)], "is_submit": False}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_lambda_ask.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: handler에 ask 분기 연결**

`code/app/lambda_handler.py`의 `handler()` 함수에서 action 추출 부분을 찾는다. 현재 코드(101~114행 근방):

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

이 블록 **앞에** ask 분기를 삽입(action_id가 ask 계열이면 별도 처리 후 early return):

```python
    # ask 계열 action 분기 (AskUserQuestion 양방향)
    first_action_id = payload.get("actions", [{}])[0].get("action_id", "")
    if first_action_id.startswith(("ask::", "ask_submit::")):
        return _handle_ask(payload, table_name)
```

- [ ] **Step 6: _handle_ask 구현**

`code/app/lambda_handler.py`의 `handler` 함수 정의 **위**에 추가:

```python
def _handle_ask(payload: dict, table_name: str) -> dict:
    """ask 버튼/Submit 클릭 → DynamoDB selections 누적, 완료 시 status=answered."""
    parsed = parse_ask_action(payload)
    ask_id = parsed["ask_id"]
    user_name = payload.get("user", {}).get("name", "unknown")
    response_url = payload.get("response_url", "")

    table = dynamodb.Table(table_name)
    try:
        # selections 맵에 qidx -> selected 누적
        item = table.get_item(Key={"approval_id": ask_id}).get("Item", {})
        selections = item.get("selections", {})
        selections[str(parsed["qidx"])] = parsed["selected"]
        expected = int(item.get("expected_count", 1))
        new_status = "answered" if len(selections) >= expected else "pending"
        table.update_item(
            Key={"approval_id": ask_id},
            UpdateExpression="SET selections = :sel, #s = :st, decided_by = :u, decided_at = :ts",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":sel": selections, ":st": new_status,
                ":u": user_name, ":ts": int(time.time()),
            },
        )
    except Exception:
        logger.exception("ask update failed for %s", ask_id)
        return {"statusCode": 500, "body": "Internal server error"}

    # Slack 메시지 갱신(간단 echo)
    if response_url and response_url.startswith("https://hooks.slack.com/"):
        done = new_status == "answered"
        msg = (":white_check_mark: 모든 질문 응답 완료" if done
               else f":ballot_box_with_check: 질문 {parsed['qidx']+1} 응답됨 (by {user_name})")
        try:
            req = Request(response_url,
                          data=json.dumps({"replace_original": False, "text": msg}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
            urlopen(req)  # nosec B310
        except Exception:
            logger.exception("ask slack update failed")

    return {"statusCode": 200, "body": ""}
```

- [ ] **Step 7: 전체 lambda 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/ -v`
Expected: PASS (전체 통과)

- [ ] **Step 8: Commit**

```bash
git add code/app/lambda_handler.py code/app/tests/test_lambda_ask.py
git commit -m "feat: handle AskUserQuestion selections in lambda webhook"
```

---

## Task 6: approval_server.py — /ask 엔드포인트 + poll_ask

**Files:**
- Modify: `code/app/approval_server.py`
- Test: `code/app/tests/test_server_ask.py`

- [ ] **Step 1: 실패하는 테스트 작성 (answers 빌드 통합)**

`code/app/tests/test_server_ask.py`:

```python
from ask_blocks import build_answers


def test_server_builds_answers_from_dynamo_selections():
    # DynamoDB selections({"0":[1]}) + questions → updatedInput.answers
    questions = [
        {"question": "Color?", "multiSelect": False,
         "options": [{"label": "Red"}, {"label": "Blue"}]},
    ]
    dynamo_selections = {"0": [1]}
    # 서버는 str key를 int로 정규화하여 build_answers 호출
    selections = {int(k): v for k, v in dynamo_selections.items()}
    answers = build_answers(questions, selections)
    assert answers == {"Color?": "Blue"}
```

- [ ] **Step 2: 테스트 실패/통과 확인 (이미 ask_blocks 존재하므로 통과해야 함)**

Run: `cd code/app && .venv/bin/pytest tests/test_server_ask.py -v`
Expected: PASS (1 passed) — 이 테스트는 서버가 사용할 정규화 패턴을 고정하는 회귀 테스트.

- [ ] **Step 3: approval_server.py에 import 및 /notify 이모지 확장**

`code/app/approval_server.py` 상단 import 블록(`from slack_sdk import WebClient` 아래)에 추가:

```python
from ask_blocks import build_ask_blocks, build_answers
```

`build_notify_blocks()`의 `status_emoji` dict(214행 근방)를 다음으로 교체:

```python
    status_emoji = {
        "completed": ":white_check_mark:",
        "in_progress": ":hourglass_flowing_sand:",
        "blocked": ":no_entry:",
        "question": ":grey_question:",
        "plan": ":clipboard:",
        "milestone": ":dart:",
    }.get(status, ":memo:")
```

- [ ] **Step 4: poll_ask 함수 추가**

`code/app/approval_server.py`의 `poll_dynamodb` 함수 **아래**에 추가:

```python
async def poll_ask(ask_id: str, request: Request, message_ts: str, blocks: list, questions: list) -> dict | None:
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
                selections = {int(k): v for k, v in item.get("selections", {}).items()}
                return build_answers(questions, selections)
        except Exception:
            logger.exception("ask poll failed for %s", ask_id)

    logger.warning("ask_id=%s timed out", ask_id)
    update_slack_message(message_ts, blocks, ":alarm_clock:", "시간 초과")
    return None
```

- [ ] **Step 5: /ask 엔드포인트 추가**

`code/app/approval_server.py`의 `@app.post("/hook")` 함수 **위**에 추가:

```python
@app.post("/ask")
async def ask(request: Request):
    body = await request.json()
    tool_input = body.get("tool_input", {})
    questions = tool_input.get("questions", [])
    cwd = body.get("cwd", "")

    if not questions:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "allow"}}

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
        # 터미널 응답/타임아웃 → 빈 allow (Claude가 터미널 답 또는 기본 처리)
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "allow"}}

    update_slack_message(message_ts, blocks, ":white_check_mark:", "응답 완료")
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "updatedInput": {**tool_input, "answers": answers},
    }}
```

> **DynamoDB 주의:** `selections`는 빈 dict로 시작. boto3는 빈 Map을 허용한다. `questions`는 JSON 문자열로 저장(중첩 list/dict TTL 단순화). `poll_ask`는 `questions`를 인자로 받으므로 DynamoDB의 questions 문자열을 다시 파싱할 필요 없음(메모리 보유분 사용).

- [ ] **Step 6: import 정합성 확인 — 서버 기동 스모크 테스트**

Run:
```bash
cd code/app && .venv/bin/python -c "import approval_server; print('import OK')"
```
Expected: `import OK` (SLACK 토큰 없어도 import는 통과; client 생성은 None 토큰 허용)

> 만약 환경변수 미설정으로 import 실패 시: `SLACK_APPROVAL_BOT_TOKEN=test SLACK_APPROVAL_CHANNEL_ID=test .venv/bin/python -c "import approval_server"` 로 재확인.

- [ ] **Step 7: 전체 단위 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 8: Commit**

```bash
git add code/app/approval_server.py code/app/tests/test_server_ask.py
git commit -m "feat: add /ask endpoint for interactive AskUserQuestion over Slack"
```

---

## Task 7: slack-event-notify.sh — ExitPlanMode/Stop 단방향 hook

**Files:**
- Create: `~/.claude/hooks/slack-event-notify.sh`

- [ ] **Step 1: hook 스크립트 작성**

`~/.claude/hooks/slack-event-notify.sh`:

```bash
#!/usr/bin/env bash
# Claude Code → Slack: ExitPlanMode(plan) / Stop(marker) 단방향 발신
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

- [ ] **Step 2: 실행 권한 부여**

Run: `chmod +x ~/.claude/hooks/slack-event-notify.sh && echo OK`
Expected: `OK`

- [ ] **Step 3: Stop 마커 동작 검증 (mock payload 주입)**

Run:
```bash
# 마커 포함 mock transcript 생성
TMP=$(mktemp /tmp/transcript-XXXX.jsonl)
printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"text","text":"진행함\n<!notify:milestone>\nPhase 1 완료"}]}}' > "$TMP"
# 서버 미기동이어도 silent fail (curl || true) → exit 0 확인
echo "{\"hook_event_name\":\"Stop\",\"transcript_path\":\"$TMP\",\"cwd\":\"/tmp/demo\"}" | ~/.claude/hooks/slack-event-notify.sh; echo "exit=$?"
rm -f "$TMP"
```
Expected: `exit=0` (서버 미기동 시에도 차단 없음)

- [ ] **Step 4: Commit (프로젝트에 hook 사본 보관)**

> `~/.claude/hooks/`는 git 밖이므로, 재배포용 사본을 프로젝트에 둔다.

```bash
mkdir -p code/hooks
cp ~/.claude/hooks/slack-event-notify.sh code/hooks/slack-event-notify.sh
git add code/hooks/slack-event-notify.sh
git commit -m "feat: add ExitPlanMode/Stop notify hook"
```

---

## Task 8: settings.json + CLAUDE.md 통합 + 수동 E2E 검증

**Files:**
- Modify: `~/.claude/settings.json`
- Modify: `~/.claude/CLAUDE.md`

- [ ] **Step 1: settings.json 백업**

Run: `cp ~/.claude/settings.json ~/.claude/settings.json.bak && echo backed-up`
Expected: `backed-up`

- [ ] **Step 2: jq로 hook 등록 (append)**

Run:
```bash
jq '
  .hooks.PreToolUse += [
    {matcher:"AskUserQuestion", hooks:[{type:"http", url:"http://localhost:8080/ask", timeout:300}]},
    {matcher:"ExitPlanMode",    hooks:[{type:"command", command:"~/.claude/hooks/slack-event-notify.sh", timeout:10}]}
  ] |
  .hooks.Stop += [
    {matcher:"", hooks:[{type:"command", command:"~/.claude/hooks/slack-event-notify.sh", timeout:10}]}
  ]
' ~/.claude/settings.json > /tmp/cc-settings.json && mv /tmp/cc-settings.json ~/.claude/settings.json
jq '.hooks.PreToolUse, .hooks.Stop' ~/.claude/settings.json
```
Expected: 새 AskUserQuestion(http :8080/ask), ExitPlanMode, Stop 항목이 보임. 기존 항목 유지.

- [ ] **Step 3: CLAUDE.md에 Notification Markers 섹션 추가**

`~/.claude/CLAUDE.md` 끝에 추가:

```markdown
## Notification Markers
다음 시점에 응답에 마커를 남긴다 (Slack 자동 발신):
- task/section 완료 → `<!notify:completed>` 다음 줄에 1~3줄 요약
- 진행 불가/외부 입력 필요 → `<!notify:blocked>` 다음 줄에 사유
- 마일스톤 도달 → `<!notify:milestone>` 다음 줄에 내용
일반 대화/단순 질문엔 남기지 않는다. "조용히 진행"/"알림 끄기" 요청 시 중단.
```

- [ ] **Step 4: DynamoDB 스키마 확인 (selections/questions 신규 속성)**

> 기존 테이블은 `approval_id`(PK)만 정의된 on-demand 테이블이므로 신규 속성(selections, questions, expected_count)은 스키마 변경 없이 저장 가능. 확인만:

Run:
```bash
aws dynamodb describe-table --table-name claude-approval-requests \
  --region "${SLACK_APPROVAL_AWS_REGION:-ap-northeast-2}" \
  --query 'Table.KeySchema' --output json
```
Expected: `approval_id` HASH key만 존재 (추가 속성은 자유 — 변경 불필요)

- [ ] **Step 5: Lambda 재배포 (ask 분기 반영)**

Run:
```bash
cd code/terraform && terraform plan -out=tfplan && terraform apply tfplan
```
Expected: `aws_lambda_function.approval_webhook` 갱신(source_code_hash 변경). 다른 리소스 변경 없음.

- [ ] **Step 6: 서버 재기동**

Run:
```bash
launchctl kickstart -k "gui/$(id -u)/com.oh-my-cc-agent" 2>/dev/null || \
  (cd code/app && .venv/bin/python approval_server.py &) ; sleep 2
curl -s localhost:8080/health
```
Expected: `{"status":"ok"}`

- [ ] **Step 7: 수동 E2E — 단일선택 양방향**

새 터미널에서:
```bash
claude
```
입력: `AskUserQuestion으로 Red/Blue 중 선호 색을 물어봐줘. 반드시 도구 호출.`

확인:
1. Slack에 질문 + [Red][Blue] 버튼 노출
2. Slack에서 [Blue] 클릭 → 메시지 "응답됨" 갱신
3. 터미널의 Claude가 Blue를 선택한 것으로 진행 (터미널 프롬프트 자동 해소)

- [ ] **Step 8: 수동 E2E — 멀티선택 + 터미널 우선 응답**

- 멀티선택: `AskUserQuestion으로 multiSelect 토핑(Cheese/Mushroom/Onion) 물어봐줘` → Slack 체크박스+Submit → 복수 선택 후 Submit → Claude가 콤마 조합 수신
- 터미널 우선: 질문 뜬 뒤 Slack 클릭 없이 **터미널에서 직접** 선택 → Slack 메시지 "터미널에서 응답됨"으로 갱신되는지

- [ ] **Step 9: 수동 E2E — ExitPlanMode / Stop 마커**

- Plan mode 진입 후 plan 제시 → Slack에 "📋 Plan 승인 요청" + plan 본문 도달
- 응답에 `<!notify:completed>` 남기는 작업 → Stop hook이 Slack에 "✅ 작업 완료" 발신

- [ ] **Step 10: hands-off.md Change Log 갱신 + Commit**

`hands-off.md` 최상단에 구현 완료 Change Log 추가(타임스탬프, 변경 파일 테이블). 그 후:

```bash
git add hands-off.md
git commit -m "docs: record interactive question implementation"
```

> 롤백 필요 시: `cp ~/.claude/settings.json.bak ~/.claude/settings.json` 로 hook 등록 원복.

---

## 검증 체크리스트 (실행자용)

- [ ] 단위 테스트 전체 통과: `cd code/app && .venv/bin/pytest tests/ -v`
- [ ] 단일선택 Slack→Claude 반환 동작
- [ ] 멀티선택 콤마 join 반환 동작
- [ ] 다중 질문(2개+) 모두 응답 시에만 반환
- [ ] 터미널 우선 응답 시 Slack "터미널에서 응답됨" 갱신
- [ ] 서버 미기동 시 ExitPlanMode/Stop hook이 Claude 흐름 차단 안 함
- [ ] ExitPlanMode plan / Stop 마커 알림 도달
- [ ] 기존 PermissionRequest approve/deny 회귀 없음
```
