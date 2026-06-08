import json
from approval_server import extract_user_context


def _write(tmp_path, lines):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    return str(p)


def test_skips_meta_skill_injection(tmp_path):
    lines = [
        {"type": "user", "isMeta": False,
         "message": {"content": [{"type": "text", "text": "results 정리해줘"}]}},
        {"type": "user", "isMeta": True,
         "message": {"content": [{"type": "text", "text": "Base directory for this skill: /Users/..."}]}},
    ]
    path = _write(tmp_path, lines)
    # isMeta=True는 무시 → 실제 사용자 메시지가 반환되어야 함
    assert extract_user_context(path) == "results 정리해줘"


def test_skips_tool_result_only(tmp_path):
    lines = [
        {"type": "user", "message": {"content": [{"type": "text", "text": "배포해줘"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
    ]
    path = _write(tmp_path, lines)
    assert extract_user_context(path) == "배포해줘"


def test_returns_empty_when_no_user_text(tmp_path):
    lines = [{"type": "user", "isMeta": True,
              "message": {"content": [{"type": "text", "text": "skill stuff"}]}}]
    path = _write(tmp_path, lines)
    assert extract_user_context(path) == ""


def test_skips_interrupt_marker(tmp_path):
    lines = [
        {"type": "user", "message": {"content": [{"type": "text", "text": "실제 사용자 지시입니다"}]}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "[Request interrupted by user for tool use]"}]}},
    ]
    path = _write(tmp_path, lines)
    assert extract_user_context(path) == "실제 사용자 지시입니다"
