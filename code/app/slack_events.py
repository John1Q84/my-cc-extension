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
