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
