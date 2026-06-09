# Slack Thread Free-text Response Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AskUserQuestion 중복 카드를 제거하고, 질의 카드 thread에 단 자유 텍스트를 Slack Events API로 받아 dual-watch(버튼 또는 thread reply)로 Claude에 답을 전달한다.

**Architecture:** `/hook`이 AskUserQuestion이면 즉시 allow(중복 방지). 카드 발신 시 `message_ts`를 DynamoDB에 저장하고 `message_ts` GSI로 thread reply를 역조회한다. lambda에 Slack Events API 경로(`/slack/events`)를 추가해 thread reply를 `free_text`로 기록하고, 서버 poll이 status(버튼) 우선·free_text 폴백으로 답을 채택한다.

**Tech Stack:** Python (로컬 서버 3.14 venv / Lambda runtime python3.12), FastAPI, boto3(DynamoDB query/GSI), AWS Lambda, API Gateway, pytest, terraform.

**검증 완료 전제 (`docs/superpowers/specs/2026-06-09-thread-freetext-design.md`):**
- AskUserQuestion이 `/ask` + `/hook` 양쪽 발화(중복) — 로그 확인.
- DynamoDB PK=`approval_id`, GSI 없음, on-demand, TTL=`expires_at`(10분).
- lambda는 `POST /slack/interact` 하나, signing secret 검증 후 `payload=` 폼 파싱.
- `aws_lambda_permission`이 `/*/*`로 모든 라우트 커버 → 라우트만 추가하면 됨.

**구현 순서 주의:** 코드/인프라(Task 1-6)를 먼저 배포해야 Slack 앱 Event Subscriptions URL 검증(Task 7)이 통과한다.

---

## File Structure

| 파일 | 책임 | 변경 |
|---|---|---|
| `code/app/poll_decision.py` | (status, free_text, question_count) → 채택할 답 결정 (순수) | **신규** |
| `code/app/slack_events.py` | Slack event body 파싱: url_verification / message 이벤트 → free_text 추출 (순수) | **신규** |
| `code/app/approval_server.py` | `/hook` AskUserQuestion 즉시 allow; 카드 발신 시 message_ts 저장; poll에 free_text 분기 | 수정 |
| `code/app/lambda_handler.py` | event vs interactive 분기; url_verification; message → GSI 조회 → free_text 기록 | 수정 |
| `code/terraform/main.tf` | message_ts GSI; `POST /slack/events` 라우트 | 수정 |
| `code/app/tests/test_poll_decision.py` | poll_decision 단위 | **신규** |
| `code/app/tests/test_slack_events.py` | slack_events 파싱 단위 | **신규** |

**설계 메모:** "버튼 vs free_text 중 무엇을 답으로" 결정과 Slack event 파싱을 순수 함수로 분리 — DynamoDB/HTTP 없이 테스트 가능. lambda의 GSI query는 통합 코드(테스트는 파싱 함수만).

---

## Task 1: poll_decision.py — 답 채택 결정 (TDD)

서버 poll이 매 주기 "버튼 status / free_text / 없음"에서 무엇을 Claude 답으로 반환할지 결정하는 순수 함수.

**Files:**
- Create: `code/app/poll_decision.py`
- Test: `code/app/tests/test_poll_decision.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`code/app/tests/test_poll_decision.py`:

```python
from poll_decision import decide_permission, decide_ask


def test_permission_button_wins_over_freetext():
    # 버튼 status 우선
    assert decide_permission(status="approved", free_text="하지마") == ("approved", None)
    assert decide_permission(status="denied", free_text=None) == ("denied", None)


def test_permission_freetext_when_no_button():
    # 버튼 없고 free_text 있으면 deny + reason
    assert decide_permission(status="pending", free_text="X로 해줘") == ("deny_freetext", "X로 해줘")


def test_permission_none_when_nothing():
    assert decide_permission(status="pending", free_text=None) == (None, None)


def test_ask_button_wins_over_freetext():
    assert decide_ask(status="answered", free_text="자유답", question_count=1, has_selections=True) == ("answered", None)


def test_ask_freetext_single_question():
    # 단일 질문 + free_text → 자유답 채택
    assert decide_ask(status="pending", free_text="빨강 말고 초록", question_count=1, has_selections=False) == ("freetext", "빨강 말고 초록")


def test_ask_freetext_ignored_multi_question():
    # 다중 질문 + free_text → 무시 (모호)
    assert decide_ask(status="pending", free_text="아무거나", question_count=2, has_selections=False) == (None, None)


def test_ask_none_when_nothing():
    assert decide_ask(status="pending", free_text=None, question_count=1, has_selections=False) == (None, None)


def test_ask_button_wins_during_nonatomic_write_window():
    # 검증 결함 #2: _handle_ask가 selection을 먼저 쓰고 status=answered를 나중에 쓰는
    # 비원자 윈도우에서, status='pending'이지만 selections가 차있으면 버튼 우선
    # (free_text가 먼저 있어도 버튼 선택을 폐기하지 않음)
    assert decide_ask(status="pending", free_text="자유답", question_count=1, has_selections=True) == (None, None)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_poll_decision.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'poll_decision'`

- [ ] **Step 3: 구현**

`code/app/poll_decision.py`:

```python
"""poll 루프의 답 채택 결정 (순수 함수, I/O 없음).
버튼(status) 우선, 없으면 free_text 폴백."""


def decide_permission(status: str, free_text):
    """PermissionRequest: 반환 (outcome, reason).
    outcome: 'approved'|'denied'|'deny_freetext'|None."""
    if status in ("approved", "denied"):
        return (status, None)
    if free_text:
        return ("deny_freetext", free_text)
    return (None, None)


def decide_ask(status: str, free_text, question_count: int, has_selections: bool = False):
    """AskUserQuestion: 반환 (outcome, text).
    outcome: 'answered'|'freetext'|None.
    - status=='answered' → 버튼 확정.
    - has_selections (버튼 선택이 기록됐으나 status flip 전 비원자 윈도우) → 버튼 우선,
      free_text 폐기하고 대기(None) — 검증 결함 #2.
    - free_text는 단일 질문이고 버튼 선택이 없을 때만 채택(다중은 모호 → 무시)."""
    if status == "answered":
        return ("answered", None)
    if has_selections:
        return (None, None)  # 버튼 진행 중 — answered flip 대기, free_text 폐기 안 함
    if free_text and question_count == 1:
        return ("freetext", free_text)
    return (None, None)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_poll_decision.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add code/app/poll_decision.py code/app/tests/test_poll_decision.py
git commit -m "feat: add poll answer-decision logic (button wins, freetext fallback) (co-worked with claude)"
```

---

## Task 2: slack_events.py — Slack event 파싱 (TDD)

Slack Events API body를 파싱: url_verification challenge, message 이벤트에서 (thread_ts, text, 무시여부) 추출.

**Files:**
- Create: `code/app/slack_events.py`
- Test: `code/app/tests/test_slack_events.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`code/app/tests/test_slack_events.py`:

```python
from slack_events import parse_slack_event


def test_url_verification():
    body = {"type": "url_verification", "challenge": "abc123"}
    assert parse_slack_event(body) == {"kind": "url_verification", "challenge": "abc123"}


def test_thread_reply_message():
    body = {"type": "event_callback", "event": {
        "type": "message", "text": "초록으로 해줘",
        "thread_ts": "1780000000.1", "ts": "1780000001.2", "user": "U1"}}
    assert parse_slack_event(body) == {
        "kind": "thread_reply", "thread_ts": "1780000000.1", "text": "초록으로 해줘", "user": "U1"}


def test_ignore_bot_message():
    body = {"type": "event_callback", "event": {
        "type": "message", "text": "카드", "thread_ts": "1.1", "ts": "1.2", "bot_id": "B1"}}
    assert parse_slack_event(body) == {"kind": "ignore"}


def test_ignore_message_subtype():
    body = {"type": "event_callback", "event": {
        "type": "message", "subtype": "message_changed", "thread_ts": "1.1", "ts": "1.2", "text": "x"}}
    assert parse_slack_event(body) == {"kind": "ignore"}


def test_ignore_top_level_message_no_thread():
    # thread_ts 없으면 부모 메시지 자체 → 무시 (reply만 처리)
    body = {"type": "event_callback", "event": {
        "type": "message", "text": "x", "ts": "1.2", "user": "U1"}}
    assert parse_slack_event(body) == {"kind": "ignore"}


def test_ignore_parent_echo_thread_ts_equals_ts():
    # thread_ts == ts → 부모 메시지 자신 → 무시
    body = {"type": "event_callback", "event": {
        "type": "message", "text": "x", "thread_ts": "1.2", "ts": "1.2", "user": "U1"}}
    assert parse_slack_event(body) == {"kind": "ignore"}


def test_unknown_type_ignored():
    assert parse_slack_event({"type": "something_else"}) == {"kind": "ignore"}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_slack_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'slack_events'`

- [ ] **Step 3: 구현**

`code/app/slack_events.py`:

```python
"""Slack Events API body 파싱 (순수 함수, I/O 없음)."""


def parse_slack_event(body: dict) -> dict:
    """반환:
    - {kind:'url_verification', challenge}
    - {kind:'thread_reply', thread_ts, text, user}
    - {kind:'ignore'}  (bot 메시지/subtype/부모자체/기타)"""
    btype = body.get("type")
    if btype == "url_verification":
        return {"kind": "url_verification", "challenge": body.get("challenge", "")}
    if btype != "event_callback":
        return {"kind": "ignore"}

    event = body.get("event", {})
    if event.get("type") != "message":
        return {"kind": "ignore"}
    if event.get("bot_id") or event.get("subtype"):
        return {"kind": "ignore"}  # 자신 메시지/편집·삭제 무시

    thread_ts = event.get("thread_ts")
    ts = event.get("ts")
    # thread reply만: thread_ts 있고, 부모 자신(thread_ts==ts)이 아님
    if not thread_ts or thread_ts == ts:
        return {"kind": "ignore"}

    return {
        "kind": "thread_reply",
        "thread_ts": thread_ts,
        "text": event.get("text", ""),
        "user": event.get("user", "unknown"),
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_slack_events.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add code/app/slack_events.py code/app/tests/test_slack_events.py
git commit -m "feat: add Slack event parser (url_verification, thread reply, ignore bot/subtype) (co-worked with claude)"
```

---

## Task 3: 중복 카드 제거 — /hook이 AskUserQuestion이면 즉시 allow

**Files:**
- Modify: `code/app/approval_server.py`

- [ ] **Step 1: /hook 초입에 AskUserQuestion 분기 추가**

`code/app/approval_server.py`의 `hook()` 함수에서 `tool_name` 파싱 직후를 찾는다. 현재:

```python
    # PermissionRequest payload 파싱
    tool_name = body.get("tool_name", "unknown")
    tool_input = json.dumps(body.get("tool_input", {}), ensure_ascii=False, indent=2)
    cwd = body.get("cwd", "")
    transcript_path = body.get("transcript_path", "")
```

`tool_name` 줄 아래에 분기 삽입:

```python
    # PermissionRequest payload 파싱
    tool_name = body.get("tool_name", "unknown")
    # AskUserQuestion은 PreToolUse(/ask)가 전담 — PermissionRequest 중복 카드 방지
    if tool_name == "AskUserQuestion":
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                       "decision": {"behavior": "allow"}}}
    tool_input = json.dumps(body.get("tool_input", {}), ensure_ascii=False, indent=2)
    cwd = body.get("cwd", "")
    transcript_path = body.get("transcript_path", "")
```

- [ ] **Step 2: import 스모크 + 전체 테스트**

Run:
```bash
cd code/app && .venv/bin/python -c "import approval_server; print('import OK')"
cd code/app && .venv/bin/pytest tests/ -q
```
Expected: import OK, 전체 통과.

- [ ] **Step 3: 동작 검증 (격리)**

Run:
```bash
cd code/app && SLACK_APPROVAL_BOT_TOKEN=test SLACK_APPROVAL_CHANNEL_ID=test .venv/bin/python - <<'PY'
import asyncio, json
from unittest.mock import patch
import approval_server as s

class FakeReq:
    async def json(self): return {"tool_name": "AskUserQuestion", "tool_input": {"questions": []}}
    async def is_disconnected(self): return False

# AskUserQuestion이면 카드 발신 없이 즉시 allow (slack.chat_postMessage 호출 안 됨)
with patch.object(s.slack, "chat_postMessage") as m:
    r = asyncio.get_event_loop().run_until_complete(s.hook(FakeReq()))
    assert r["hookSpecificOutput"]["decision"]["behavior"] == "allow", r
    assert not m.called, "AskUserQuestion이면 카드를 보내면 안 됨"
print("PASS: AskUserQuestion 중복 카드 제거 확인")
PY
```
Expected: `PASS: AskUserQuestion 중복 카드 제거 확인`

- [ ] **Step 4: Commit**

```bash
git add code/app/approval_server.py
git commit -m "fix: /hook returns allow for AskUserQuestion to avoid duplicate card (co-worked with claude)"
```

---

## Task 4: terraform — message_ts GSI + /slack/events 라우트 + IAM Query 권한

**Files:**
- Modify: `code/terraform/main.tf`

> **검증 결함 #1 반영 (CRITICAL):** lambda가 GSI를 query하려면 IAM 정책에 `dynamodb:Query` 액션과 **GSI 인덱스 ARN**(`<table>/index/*`)이 둘 다 필요하다. 현재 정책은 `UpdateItem`/`GetItem`만, 리소스는 베이스 테이블 ARN만 갖고 있어, 빠뜨리면 thread reply가 `AccessDeniedException`으로 **조용히 전부 실패**(except가 삼킴)한다. Step 0에서 먼저 수정.

- [ ] **Step 0: lambda IAM 정책에 dynamodb:Query + GSI ARN 추가**

`code/terraform/main.tf`의 `data "aws_iam_policy_document" "lambda_policy"` 첫 statement(66~74행)를 찾는다. 현재:

```hcl
data "aws_iam_policy_document" "lambda_policy" {
  statement {
    effect = "Allow"
    actions = [
      "dynamodb:UpdateItem",
      "dynamodb:GetItem",
    ]
    resources = [aws_dynamodb_table.approval_requests.arn]
  }
```

다음으로 교체 (Query 액션 추가 + 인덱스 ARN 추가):

```hcl
data "aws_iam_policy_document" "lambda_policy" {
  statement {
    effect = "Allow"
    actions = [
      "dynamodb:UpdateItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
    ]
    resources = [
      aws_dynamodb_table.approval_requests.arn,
      "${aws_dynamodb_table.approval_requests.arn}/index/*",
    ]
  }
```

> GSI Query는 **인덱스 child-resource ARN**(`table/index/*`)으로 인가된다. 베이스 테이블 ARN만으로는 액션을 추가해도 거부된다 — 둘 다 필요.

- [ ] **Step 1: DynamoDB 테이블에 message_ts 속성 + GSI 추가**

`code/terraform/main.tf`의 `aws_dynamodb_table.approval_requests` 블록을 찾는다. 현재:

```hcl
resource "aws_dynamodb_table" "approval_requests" {
  name         = "claude-approval-requests"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "approval_id"

  attribute {
    name = "approval_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
```

`attribute "approval_id"` 블록 아래에 message_ts 속성 + GSI를 추가 (ttl 위):

```hcl
resource "aws_dynamodb_table" "approval_requests" {
  name         = "claude-approval-requests"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "approval_id"

  attribute {
    name = "approval_id"
    type = "S"
  }

  attribute {
    name = "message_ts"
    type = "S"
  }

  global_secondary_index {
    name            = "message_ts-index"
    hash_key        = "message_ts"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
```

> **주의:** DynamoDB는 GSI hash_key 속성이 정의되어 있어야 한다. on-demand 테이블이라 GSI도 on-demand 상속. 기존 항목에 message_ts 없으면 GSI에 안 들어가는데(sparse index), 정상 — message_ts 있는 카드만 색인됨.

- [ ] **Step 2: /slack/events 라우트 추가**

`code/terraform/main.tf`의 `aws_apigatewayv2_route.slack_interact` 블록을 찾는다. 현재:

```hcl
resource "aws_apigatewayv2_route" "slack_interact" {
  api_id    = aws_apigatewayv2_api.approval_api.id
  route_key = "POST /slack/interact"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}
```

그 **아래**에 events 라우트 추가:

```hcl
resource "aws_apigatewayv2_route" "slack_events" {
  api_id    = aws_apigatewayv2_api.approval_api.id
  route_key = "POST /slack/events"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}
```

> `aws_lambda_permission.api_gateway`가 이미 `source_arn = ".../*/*"`로 모든 라우트를 커버하므로 권한 추가 불필요.

- [ ] **Step 3: terraform plan 확인 (apply는 Task 6 후)**

Run:
```bash
cd code/terraform && terraform plan -no-color 2>&1 | grep -E "will be|message_ts|slack_events|global_secondary" | head
```
Expected: GSI(`message_ts-index`) 추가 + `aws_apigatewayv2_route.slack_events` 생성 표시. (apply는 lambda 코드 변경 후 Task 6에서 함께)

- [ ] **Step 4: Commit**

```bash
git add code/terraform/main.tf
git commit -m "feat: add message_ts GSI and /slack/events route (co-worked with claude)"
```

---

## Task 5: lambda — Slack Events 경로 처리

interactive(버튼)와 events를 분기하고, message thread reply를 GSI로 조회해 free_text 기록.

**Files:**
- Modify: `code/app/lambda_handler.py`

- [ ] **Step 1: slack_events 파서를 lambda에서 사용 — 단, lambda는 단일 파일 배포**

> **중요:** lambda는 `lambda_handler.py` 단일 파일로 zip 배포된다(terraform `source_file`). `slack_events.py`를 import하면 zip에 포함되지 않아 런타임 ImportError. 따라서 `parse_slack_event` 로직을 lambda_handler.py에 **인라인 복제**한다(서버용 slack_events.py와 동일 로직, DRY보다 배포 단순성 우선). Task 2의 테스트가 순수 로직을 이미 검증하므로, 여기선 동일 함수를 lambda에 두고 통합 동작만 본다.

`code/app/lambda_handler.py`의 `parse_ask_action` 정의 **위**에 추가:

```python
def parse_slack_event(body: dict) -> dict:
    """Slack Events API body 파싱 (slack_events.py와 동일 로직 — lambda 단일파일 배포용 인라인).
    반환: url_verification / thread_reply / ignore."""
    btype = body.get("type")
    if btype == "url_verification":
        return {"kind": "url_verification", "challenge": body.get("challenge", "")}
    if btype != "event_callback":
        return {"kind": "ignore"}
    event = body.get("event", {})
    if event.get("type") != "message":
        return {"kind": "ignore"}
    if event.get("bot_id") or event.get("subtype"):
        return {"kind": "ignore"}
    thread_ts = event.get("thread_ts")
    ts = event.get("ts")
    if not thread_ts or thread_ts == ts:
        return {"kind": "ignore"}
    return {"kind": "thread_reply", "thread_ts": thread_ts,
            "text": event.get("text", ""), "user": event.get("user", "unknown")}
```

- [ ] **Step 2: _handle_event 구현 (GSI 조회 → free_text 기록)**

`code/app/lambda_handler.py`의 `handler` 정의 **위**에 추가:

```python
def _handle_event(body: dict, table_name: str) -> dict:
    """Slack Events: url_verification 응답 또는 thread reply → free_text 기록.
    가볍게 처리하고 즉시 200 (Slack 3초 ack)."""
    parsed = parse_slack_event(body)
    if parsed["kind"] == "url_verification":
        return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"challenge": parsed["challenge"]})}
    if parsed["kind"] != "thread_reply":
        return {"statusCode": 200, "body": ""}  # 무시해도 200 (재전송 방지)

    table = dynamodb.Table(table_name)
    try:
        # message_ts GSI로 thread_ts(=부모 카드 ts)에 해당하는 pending 항목 조회
        resp = table.query(
            IndexName="message_ts-index",
            KeyConditionExpression="message_ts = :mt",
            ExpressionAttributeValues={":mt": parsed["thread_ts"]},
        )
        items = resp.get("Items", [])
        if not items:
            return {"statusCode": 200, "body": ""}  # 매칭 없음 — 무시
        approval_id = items[0]["approval_id"]
        table.update_item(
            Key={"approval_id": approval_id},
            UpdateExpression="SET free_text = :ft, decided_by = :u, decided_at = :ts",
            ExpressionAttributeValues={
                ":ft": parsed["text"], ":u": parsed["user"], ":ts": int(time.time()),
            },
        )
    except Exception:
        logger.exception("event free_text update failed")
        return {"statusCode": 200, "body": ""}  # 실패해도 200 (Slack 재전송 방지)
    return {"statusCode": 200, "body": ""}
```

- [ ] **Step 3: handler에서 events vs interactive 분기**

`code/app/lambda_handler.py`의 `handler()`에서 signing 검증 직후, payload 파싱(`parse_qs`) **전에** events 분기를 삽입한다. 현재 (signing 검증 후):

```python
    # Payload 파싱
    try:
        parsed = parse_qs(body)
        payload = json.loads(parsed["payload"][0])
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        logger.error("Failed to parse payload: %s", exc)
        return {"statusCode": 400, "body": "Bad request"}
```

이 블록 **앞에** 삽입:

```python
    # Slack Events API(thread reply 등)는 JSON body — interactive(payload= 폼)와 분기
    # body가 JSON으로 파싱되고 "type" 키가 있으면 event 경로
    try:
        json_body = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        json_body = None
    if isinstance(json_body, dict) and "type" in json_body and "payload" not in body[:20]:
        return _handle_event(json_body, table_name)

    # Payload 파싱 (interactive 버튼)
    try:
        parsed = parse_qs(body)
        payload = json.loads(parsed["payload"][0])
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        logger.error("Failed to parse payload: %s", exc)
        return {"statusCode": 400, "body": "Bad request"}
```

> **분기 근거:** interactive는 `payload={...}` 폼 인코딩(JSON 파싱 실패), events는 순수 JSON(`{"type":...}`). `json.loads` 성공 + `type` 키로 구분. `"payload" not in body[:20]`는 혹시 모를 폼 바디 방어.

- [ ] **Step 4: url_verification 단위 동작 확인**

Run:
```bash
cd code/app && .venv/bin/python - <<'PY'
from lambda_handler import parse_slack_event
assert parse_slack_event({"type":"url_verification","challenge":"X"}) == {"kind":"url_verification","challenge":"X"}
assert parse_slack_event({"type":"event_callback","event":{"type":"message","text":"t","thread_ts":"1.1","ts":"1.2","user":"U"}})["kind"] == "thread_reply"
assert parse_slack_event({"type":"event_callback","event":{"type":"message","bot_id":"B","thread_ts":"1.1","ts":"1.2"}})["kind"] == "ignore"
print("PASS: lambda parse_slack_event")
PY
```
Expected: `PASS: lambda parse_slack_event` (단, lambda_handler import에 region fallback 필요 — 이미 적용됨)

- [ ] **Step 5: Commit**

```bash
git add code/app/lambda_handler.py
git commit -m "feat: handle Slack Events (url_verification, thread reply → free_text) in lambda (co-worked with claude)"
```

---

## Task 6: approval_server — message_ts 저장 + poll free_text 분기

**Files:**
- Modify: `code/app/approval_server.py`

- [ ] **Step 1: import 추가**

`code/app/approval_server.py` 상단(`from ask_blocks import ...` 아래):

```python
from poll_decision import decide_permission, decide_ask
```

- [ ] **Step 2: /hook put_item에 message_ts 저장**

`/hook`은 카드 발신 후 message_ts를 얻는다. 현재 put_item이 발신 **전**이라 message_ts가 없다 → 발신 후 update_item으로 저장한다. `slack_resp = slack.chat_postMessage(...)` 직후 `message_ts = slack_resp["ts"]` 다음에 추가:

```python
    message_ts = slack_resp["ts"]
    logger.info("Slack message sent for approval_id=%s ts=%s", approval_id, message_ts)
    # thread reply 역조회용 message_ts 저장 (GSI)
    try:
        table.update_item(
            Key={"approval_id": approval_id},
            UpdateExpression="SET message_ts = :mt",
            ExpressionAttributeValues={":mt": message_ts},
        )
    except Exception:
        logger.exception("message_ts 저장 실패 approval_id=%s", approval_id)
```

- [ ] **Step 3: /ask put_item에 message_ts 저장**

`/ask`도 동일하게 `message_ts = slack_resp["ts"]` 직후에 추가:

```python
    message_ts = slack_resp["ts"]
    logger.info("ask_id=%s posted, ts=%s", ask_id, message_ts)
    try:
        table.update_item(
            Key={"approval_id": ask_id},
            UpdateExpression="SET message_ts = :mt",
            ExpressionAttributeValues={":mt": message_ts},
        )
    except Exception:
        logger.exception("message_ts 저장 실패 ask_id=%s", ask_id)
```

- [ ] **Step 4: poll_dynamodb에 free_text 분기**

`poll_dynamodb`의 status 확인 블록을 free_text 포함으로 교체. 현재:

```python
        try:
            resp = table.get_item(Key={"approval_id": approval_id})
            item = resp.get("Item", {})
            status = item.get("status", "pending")
            if status in ("approved", "denied"):
                logger.info("approval_id=%s status=%s", approval_id, status)
                return status
        except Exception:
            logger.exception("DynamoDB GetItem failed for %s", approval_id)
```

다음으로 교체:

```python
        try:
            resp = table.get_item(Key={"approval_id": approval_id})
            item = resp.get("Item", {})
            status = item.get("status", "pending")
            outcome, reason = decide_permission(status, item.get("free_text"))
            if outcome == "approved" or outcome == "denied":
                logger.info("approval_id=%s status=%s", approval_id, outcome)
                return outcome
            if outcome == "deny_freetext":
                logger.info("approval_id=%s resolved by thread free_text", approval_id)
                update_slack_message(message_ts, blocks, ":speech_balloon:", "thread 응답으로 처리됨")
                # reason을 호출부가 쓸 수 있도록 status 문자열에 실어 반환
                return "freetext:" + reason
        except Exception:
            logger.exception("DynamoDB GetItem failed for %s", approval_id)
```

- [ ] **Step 5: /hook 응답에서 freetext: 처리**

`hook()`의 `result = await poll_dynamodb(...)` 이후 분기. 현재:

```python
    result = await poll_dynamodb(approval_id, request, message_ts, blocks)

    if result == "approved":
```

`result == "approved"` 분기 **앞에** freetext 처리 추가:

```python
    result = await poll_dynamodb(approval_id, request, message_ts, blocks)

    if result.startswith("freetext:"):
        reason = result[len("freetext:"):]
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny", "reason": reason}}}

    if result == "approved":
```

- [ ] **Step 6: poll_ask에 free_text 분기**

`poll_ask`의 status 확인 블록을 교체. 현재:

```python
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
```

다음으로 교체:

```python
        try:
            item = table.get_item(Key={"approval_id": ask_id}).get("Item", {})
            status = item.get("status", "pending")
            # 비원자 write 윈도우 대비: selections 차있으면 버튼 우선(검증 결함 #2)
            has_selections = bool(item.get("selections"))
            outcome, text = decide_ask(status, item.get("free_text"), len(questions), has_selections)
            if outcome == "answered":
                # boto3 resource는 숫자를 Decimal로 역직렬화 → oidx int 강제 (답변 유실 방지)
                selections = {
                    int(k): [int(o) for o in v]
                    for k, v in item.get("selections", {}).items()
                }
                return build_answers(questions, selections)
            if outcome == "freetext":
                logger.info("ask_id=%s resolved by thread free_text", ask_id)
                return {questions[0]["question"]: text}
        except Exception:
            logger.exception("ask poll failed for %s", ask_id)
```

> `poll_ask`는 answers dict를 반환(없으면 None)한다. freetext도 `{질문: text}` dict를 반환하므로 `/ask`의 기존 `if answers is None` 분기가 그대로 동작한다.

- [ ] **Step 7: import 스모크 + 전체 테스트**

Run:
```bash
cd code/app && .venv/bin/python -c "import approval_server; print('import OK')"
cd code/app && .venv/bin/pytest tests/ -q
```
Expected: import OK, 전체 통과.

- [ ] **Step 8: Commit**

```bash
git add code/app/approval_server.py
git commit -m "feat: store message_ts and resolve poll via thread free_text (dual-watch) (co-worked with claude)"
```

---

## Task 7: 배포 + Slack 앱 설정 + E2E

**Files:** (없음 — 배포·설정·검증)

- [ ] **Step 1: terraform apply (GSI + 라우트 + lambda 재배포)**

Run:
```bash
cd code/terraform && terraform apply -auto-approve -no-color 2>&1 | grep -E "Apply complete|message_ts|slack_events|lambda_function|Error"
```
Expected: GSI 생성 + slack_events 라우트 생성 + lambda 갱신. `Apply complete`.

- [ ] **Step 2: API Gateway events URL 확인**

Run:
```bash
cd code/terraform && terraform output 2>/dev/null | grep -i url
# 또는: aws apigatewayv2 get-apis --query "Items[?Name=='claude-approval-api'].ApiEndpoint" --output text
```
events URL = `<ApiEndpoint>/slack/events`. 기록.

- [ ] **Step 3: 서버 재기동 (poll free_text 분기 반영)**

Run:
```bash
cd code/app && .venv/bin/python -c "import approval_server; print('OK')"
launchctl kickstart -k "gui/$(id -u)/com.oh-my-cc-agent" && sleep 2 && curl -s localhost:8080/health
```
Expected: `{"status":"ok"}`

- [ ] **Step 4: Slack 앱 설정 (사용자 작업)**

api.slack.com → 앱 `ohmyccagent`:
1. **OAuth & Permissions → Bot Token Scopes**: `channels:history` 추가 (비공개 채널이면 `groups:history`)
2. **Event Subscriptions → Enable Events ON**
3. **Request URL**: Step 2의 `<ApiEndpoint>/slack/events` 입력 → "Verified" 확인 (lambda url_verification이 응답)
4. **Subscribe to bot events**: `message.channels` 추가 (비공개면 `message.groups`)
5. **Save Changes** → 상단 "Reinstall" 배너 클릭해 재설치
6. 재설치 후 bot token 바뀌면 `SLACK_APPROVAL_BOT_TOKEN` 갱신 + 서버 재기동

> bot이 해당 채널에 멤버여야 message 이벤트를 받는다. 채널에 `/invite @ohmyccagent` 확인.

- [ ] **Step 5: E2E — AskUserQuestion 중복 제거 + thread 자유답**

새 세션에서 단일 질문 AskUserQuestion 유발:
1. Slack에 **카드 1개만** 뜨는지(중복 허용/거부 카드 없음) 확인
2. 카드 **thread에 자유 텍스트** 작성(예: "옵션 말고 X로 해줘") → Claude가 그 텍스트를 답으로 진행하는지 확인
3. 버튼 클릭 경로도 여전히 동작하는지 확인

- [ ] **Step 6: E2E — PermissionRequest thread reply = deny+reason**

권한 요청 카드의 thread에 자유 텍스트 → Claude가 deny + 그 reason을 받는지 확인.

- [ ] **Step 7: E2E — 버튼 우선 + 회귀**

- 버튼 클릭과 thread reply가 둘 다 있을 때 버튼이 우선되는지(버튼 먼저 누르면 thread 무시).
- 기존 PermissionRequest approve/deny, AskUserQuestion 버튼 흐름 회귀 없음.

- [ ] **Step 8: hands-off.md 갱신 + Commit**

`hands-off.md` 최상단에 구현 완료 Change Log(타임스탬프, 변경 파일, E2E 결과, Slack 설정 변경) 추가 후:

```bash
git add hands-off.md
git commit -m "docs: record thread free-text implementation (co-worked with claude)"
```

---

## 검증 체크리스트 (실행자용)

- [ ] 단위 테스트 전체 통과: `cd code/app && .venv/bin/pytest tests/ -v`
- [ ] poll_decision: 버튼 우선, free_text 폴백, 다중질문 free_text 무시
- [ ] slack_events: url_verification, thread reply, bot/subtype/부모 무시
- [ ] AskUserQuestion 카드 1개만 (중복 제거)
- [ ] **IAM: lambda에 dynamodb:Query + GSI ARN 부여됨 (검증 결함 #1, CRITICAL)** — terraform apply 후 thread reply가 AccessDenied 없이 free_text 기록되는지 E2E로 확인
- [ ] message_ts가 DynamoDB에 저장되고 GSI로 조회됨
- [ ] thread 자유 텍스트 → AskUserQuestion 자유답 / PermissionRequest deny+reason
- [ ] 버튼 vs thread 동시 시 버튼 우선 (decide_ask has_selections — 검증 결함 #2)
- [ ] Slack Events URL 검증 통과 (url_verification)
- [ ] 기존 버튼 흐름 회귀 없음
- [ ] bot 자기 메시지 무시 (무한 루프 없음)
```
