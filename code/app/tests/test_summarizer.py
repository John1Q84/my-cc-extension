import json
from summarizer import build_summary_prompt, parse_summary_response


def test_build_summary_prompt_includes_tool_and_input():
    prompt = build_summary_prompt(
        tool_name="Bash",
        tool_input='{"command": "rm -rf results/tmp"}',
        user_context="results 정리해줘",
    )
    assert "Bash" in prompt
    assert "rm -rf results/tmp" in prompt
    assert "results 정리해줘" in prompt
    # 출력 형식 지시(JSON 키)가 프롬프트에 명시되어야 함
    assert "request" in prompt and "risk" in prompt and "confirm" in prompt


def test_parse_summary_response_valid_json():
    raw = json.dumps({
        "request": "임시 파일 정리",
        "risk": ["rm -rf — 디렉터리 삭제"],
        "confirm": "삭제 대상 확인 필요",
    }, ensure_ascii=False)
    result = parse_summary_response(raw)
    assert result["request"] == "임시 파일 정리"
    assert result["risk"] == ["rm -rf — 디렉터리 삭제"]
    assert result["confirm"] == "삭제 대상 확인 필요"


def test_parse_summary_response_json_in_codefence():
    raw = "```json\n{\"request\": \"x\", \"risk\": [], \"confirm\": \"y\"}\n```"
    result = parse_summary_response(raw)
    assert result["request"] == "x"
    assert result["risk"] == []


def test_parse_summary_response_invalid_returns_none():
    assert parse_summary_response("not json at all") is None


def test_parse_summary_response_missing_keys_returns_none():
    assert parse_summary_response('{"request": "x"}') is None
