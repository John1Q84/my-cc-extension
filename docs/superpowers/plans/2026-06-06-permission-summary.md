# Permission Message LLM Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Slack permission 요청 카드를 raw input 나열(500자 절단)에서 **요청사항 / risk·영향도 / 사용자 확인필요** 구조화 요약(Claude Haiku 4.5 via Bedrock)으로 개선하고, context 추출이 skill 주입 텍스트를 사용자 맥락으로 오인하는 버그를 수정한다.

**Architecture:** `approval_server.py`의 `/hook`이 permission 요청을 받을 때, tool_input + (수정된) 사용자 맥락을 Bedrock Haiku 4.5에 보내 구조화 요약 JSON을 받는다. 요약 성공 시 Slack 카드에 요약 섹션을 추가하고 원본 input은 펼침/축약으로 보존; 실패·타임아웃 시 기존 raw 표시로 fallback. 모델 호출은 신규 `summarizer.py`에 격리(순수 함수 프롬프트 빌더 + Bedrock 호출 래퍼).

**Tech Stack:** Python 3.14, FastAPI, boto3 (bedrock-runtime, ap-northeast-2), slack_sdk, pytest.

**검증 완료 전제:**
- Bedrock Haiku 4.5는 on-demand 불가, **`global.anthropic.claude-haiku-4-5-20251001-v1:0` inference profile**로 ap-northeast-2 호출 성공 (기존 boto3 자격증명 재사용, ANTHROPIC_API_KEY 불필요).
- context 버그: skill 주입 텍스트는 transcript에서 `isMeta: true`인 user 메시지. 현재 `extract_user_context`는 이를 거르지 않아 "Base directory for this skill..."를 사용자 맥락으로 표시함.

---

## File Structure

| 파일 | 책임 | 변경 |
|---|---|---|
| `code/app/summarizer.py` | 요약 프롬프트 빌더(순수 함수) + Bedrock Haiku 호출 래퍼 + 응답 파싱 | **신규** |
| `code/app/approval_server.py` | `extract_user_context` isMeta 필터, `/hook`에서 요약 호출, `build_slack_blocks` 요약 섹션 추가 | 수정 |
| `code/app/tests/test_summarizer.py` | 프롬프트 빌더/파서 단위 테스트 | **신규** |
| `code/app/tests/test_extract_context.py` | isMeta 필터 회귀 테스트 | **신규** |

**설계 메모:** Bedrock 네트워크 호출은 테스트 불가하므로, `summarizer.py`를 (1) 순수 함수 `build_summary_prompt()` / `parse_summary_response()` 와 (2) I/O 래퍼 `summarize()` 로 분리한다. 단위 테스트는 (1)만 대상. `summarize()`는 모든 예외를 잡아 `None` 반환(fail-open) → 서버는 None이면 raw fallback.

---

## Task 1: extract_user_context — isMeta 필터 (TDD)

skill/system 주입 메시지(`isMeta: true`)를 건너뛰고 실제 사용자 타이핑만 추출.

**Files:**
- Modify: `code/app/approval_server.py` (`extract_user_context`, 40~61행)
- Test: `code/app/tests/test_extract_context.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`code/app/tests/test_extract_context.py`:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_extract_context.py -v`
Expected: `test_skips_meta_skill_injection` FAIL (현재는 skill 텍스트를 반환)

- [ ] **Step 3: isMeta 필터 추가**

`code/app/approval_server.py`의 `extract_user_context` 루프에서 `obj.get("type") != "user"` 체크 **아래**에 isMeta 스킵을 추가. 현재 코드(48~57행 근방):

```python
            for line in f:
                obj = json.loads(line)
                if obj.get("type") != "user":
                    continue
                content = obj.get("message", {}).get("content", [])
```

다음으로 교체:

```python
            for line in f:
                obj = json.loads(line)
                if obj.get("type") != "user":
                    continue
                if obj.get("isMeta"):  # skill/system 주입 메시지 제외
                    continue
                content = obj.get("message", {}).get("content", [])
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_extract_context.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add code/app/approval_server.py code/app/tests/test_extract_context.py
git commit -m "fix: exclude isMeta skill-injected messages from user context"
```

---

## Task 2: summarizer.py — 프롬프트 빌더 + 파서 (TDD)

**Files:**
- Create: `code/app/summarizer.py`
- Test: `code/app/tests/test_summarizer.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`code/app/tests/test_summarizer.py`:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_summarizer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'summarizer'`

- [ ] **Step 3: 순수 함수 구현**

`code/app/summarizer.py`:

```python
"""Permission 요청 요약 — Bedrock Haiku 4.5 호출 + 프롬프트/파싱 순수 함수."""

import json
import logging
import os
import re

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

BEDROCK_REGION = os.environ.get("SLACK_APPROVAL_AWS_REGION", "ap-northeast-2")
BEDROCK_MODEL_ID = os.environ.get(
    "SLACK_APPROVAL_BEDROCK_MODEL",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
)

_REQUIRED_KEYS = ("request", "risk", "confirm")


def build_summary_prompt(tool_name: str, tool_input: str, user_context: str) -> str:
    """요약 프롬프트 생성. 출력은 request/risk/confirm 3키 JSON을 요구."""
    return (
        "당신은 Claude Code의 권한 요청을 검토자가 빠르게 판단하도록 요약합니다.\n"
        "아래 도구 실행 요청을 분석해, 반드시 다음 JSON만 출력하세요(설명·코드펜스 금지):\n"
        '{\n'
        '  "request": "이 작업이 무엇을 하려는지 1~2문장 한국어 요약",\n'
        '  "risk": ["위험하거나 영향도 큰 항목을 짧게. 없으면 빈 배열"],\n'
        '  "confirm": "승인 전 사용자가 확인해야 할 핵심 1문장"\n'
        "}\n\n"
        f"[도구] {tool_name}\n"
        f"[사용자 최근 요청 맥락]\n{user_context or '(없음)'}\n\n"
        f"[도구 입력]\n{tool_input}\n"
    )


def parse_summary_response(raw: str) -> dict | None:
    """모델 응답에서 JSON을 추출·검증. 실패 시 None."""
    if not raw:
        return None
    text = raw.strip()
    # 코드펜스 제거
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        # 첫 { ~ 마지막 } 추출
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict) or not all(k in obj for k in _REQUIRED_KEYS):
        return None
    if not isinstance(obj.get("risk"), list):
        return None
    return obj
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/test_summarizer.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add code/app/summarizer.py code/app/tests/test_summarizer.py
git commit -m "feat: add permission summary prompt builder and parser"
```

---

## Task 3: summarizer.py — Bedrock 호출 래퍼 (fail-open)

순수 함수 위에 실제 Bedrock 호출을 얹는다. 모든 예외/타임아웃을 잡아 None 반환.

**Files:**
- Modify: `code/app/summarizer.py`

- [ ] **Step 1: summarize() 구현 추가**

`code/app/summarizer.py` 끝에 추가:

```python
_bedrock = boto3.client(
    "bedrock-runtime",
    region_name=BEDROCK_REGION,
    config=Config(read_timeout=12, connect_timeout=3, retries={"max_attempts": 1}),
)


def summarize(tool_name: str, tool_input: str, user_context: str) -> dict | None:
    """Bedrock Haiku로 요약. 실패 시 None (서버는 raw fallback)."""
    prompt = build_summary_prompt(tool_name, tool_input, user_context)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        resp = _bedrock.invoke_model(modelId=BEDROCK_MODEL_ID, body=json.dumps(body))
        out = json.loads(resp["body"].read())
        text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
        result = parse_summary_response(text)
        if result is None:
            logger.warning("summary parse failed; raw=%s", text[:200])
        return result
    except Exception:
        logger.exception("Bedrock summarize failed")
        return None
```

> **설계 메모:** `botocore.config.Config`로 read_timeout 12초 설정 — permission hook은 최대 300초 블로킹이 허용되지만, 요약은 그보다 훨씬 빨라야 하므로 12초 상한. 초과 시 None → raw fallback. `retries=1`로 지연 최소화.

- [ ] **Step 2: import 스모크 + 실제 1회 호출 검증**

Run:
```bash
cd code/app && .venv/bin/python - <<'PY'
from summarizer import summarize
r = summarize("Bash", '{"command": "rm -rf results/.tmp && pkill -f \\"http.server 8899\\""}', "results 미리보기 정리하고 서버 종료해줘")
import json; print(json.dumps(r, ensure_ascii=False, indent=2) if r else "None (fallback)")
PY
```
Expected: `request`/`risk`/`confirm` 키를 가진 JSON 출력 (Bedrock 호출 성공). None이면 자격증명/프로파일 확인.

- [ ] **Step 3: Commit**

```bash
git add code/app/summarizer.py
git commit -m "feat: add Bedrock Haiku summarize wrapper with fail-open"
```

---

## Task 4: approval_server.py — /hook에 요약 통합 + Slack 카드

**Files:**
- Modify: `code/app/approval_server.py`
- Test: (수동 — Slack 실측)

- [ ] **Step 1: import 추가**

`code/app/approval_server.py` 상단(`from slack_sdk import WebClient` 아래):

```python
from summarizer import summarize
```

- [ ] **Step 2: build_slack_blocks 시그니처 확장 — summary 파라미터 추가**

현재 `build_slack_blocks` 정의(64~70행):

```python
def build_slack_blocks(
    approval_id: str,
    tool_name: str,
    tool_input: str,
    cwd: str = "",
    user_context: str = "",
) -> list:
```

다음으로 교체(summary 인자 추가):

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

- [ ] **Step 3: 요약 섹션을 헤더 다음에 삽입**

`build_slack_blocks` 안에서 헤더 blocks 생성 직후, `if truncated_context:` **위**에 요약 블록을 추가. 현재 코드(74~83행):

```python
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Claude Code Permission Request*\n\n*Tool:* `{tool_name}`",
            },
        },
    ]

    if truncated_context:
```

다음으로 교체:

```python
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Claude Code Permission Request*\n\n*Tool:* `{tool_name}`",
            },
        },
    ]

    if summary:
        risk_lines = "\n".join(f"• {r}" for r in summary.get("risk", [])) or "• (특이사항 없음)"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*📋 요청사항*\n{summary.get('request', '')}\n\n"
                    f"*⚠️ 영향도 / Risk*\n{risk_lines}\n\n"
                    f"*✅ 확인 필요*\n{summary.get('confirm', '')}"
                )[:3000],
            },
        })
        blocks.append({"type": "divider"})

    if truncated_context:
```

- [ ] **Step 4: input 표시를 요약 유무에 따라 조정**

`build_slack_blocks`에서 input 블록 추가 부분(105~113행 근방):

```python
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Input:*\n```{truncated_input}```",
            },
        }
    )
```

다음으로 교체(요약이 있으면 원본 input은 더 길게 보존 — 1500자, 라벨도 '원본'으로):

```python
    if summary:
        # 요약이 있으면 원본은 참고용으로 더 넉넉히(1500자) 표시
        raw = tool_input[:1500] + "..." if len(tool_input) > 1500 else tool_input
        input_label = "원본 입력 (참고)"
    else:
        raw = truncated_input
        input_label = "Input"
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{input_label}:*\n```{raw}```",
            },
        }
    )
```

- [ ] **Step 5: /hook 핸들러에서 요약 호출 + 전달**

`code/app/approval_server.py`의 `hook()` 함수에서 `user_context` 추출 직후, `build_slack_blocks` 호출 전에 요약을 생성한다. 현재(288~310행 근방):

```python
    # transcript에서 사용자의 최근 요청 컨텍스트 추출
    user_context = extract_user_context(transcript_path)
```

이 줄 **아래**에 추가:

```python
    # Bedrock Haiku로 요청 요약 (실패 시 None → raw fallback)
    summary = summarize(tool_name, tool_input, user_context)
```

그리고 `build_slack_blocks` 호출(310행 근방):

```python
    blocks = build_slack_blocks(approval_id, tool_name, tool_input, cwd, user_context)
```

다음으로 교체:

```python
    blocks = build_slack_blocks(approval_id, tool_name, tool_input, cwd, user_context, summary)
```

- [ ] **Step 6: import 스모크 테스트**

Run:
```bash
cd code/app && .venv/bin/python -c "import approval_server; print('import OK')"
```
Expected: `import OK`

- [ ] **Step 7: 전체 단위 테스트 통과 확인**

Run: `cd code/app && .venv/bin/pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 8: Commit**

```bash
git add code/app/approval_server.py
git commit -m "feat: show LLM-summarized request/risk/confirm in permission card"
```

---

## Task 5: 서버 재기동 + 수동 E2E 검증

**Files:** (없음 — 검증만)

- [ ] **Step 1: IAM 권한 확인 (bedrock:InvokeModel)**

> 서버 LaunchAgent가 쓰는 자격증명(`ymjoung` user)에 Bedrock 호출 권한이 있어야 한다. PoC에서 호출 성공했으므로 통상 OK. 확인:

Run:
```bash
aws bedrock-runtime invoke-model \
  --region ap-northeast-2 \
  --model-id "global.anthropic.claude-haiku-4-5-20251001-v1:0" \
  --body '{"anthropic_version":"bedrock-2023-05-31","max_tokens":10,"messages":[{"role":"user","content":"OK"}]}' \
  --cli-binary-format raw-in-base64-out /tmp/bedrock-out.json >/dev/null 2>&1 && echo "InvokeModel OK" || echo "InvokeModel DENIED — IAM 정책 확인"
```
Expected: `InvokeModel OK`

- [ ] **Step 2: 서버 재기동**

Run:
```bash
launchctl kickstart -k "gui/$(id -u)/com.oh-my-cc-agent" 2>/dev/null && sleep 2 && curl -s localhost:8080/health
```
Expected: `{"status":"ok"}`

- [ ] **Step 3: 수동 E2E — Bash 권한 요청**

다른 세션/터미널에서 권한이 필요한 Bash 명령(긴 복합 명령)을 유발한다. 예: Claude에게 `여러 파일을 정리하는 복잡한 bash 명령을 한 줄로 실행해줘`.

확인:
1. Slack 카드에 *📋 요청사항* / *⚠️ 영향도 / Risk* / *✅ 확인 필요* 섹션이 표시됨
2. 위험 명령(rm, pkill 등)이 risk에 식별됨
3. Context가 더 이상 "Base directory for this skill..."가 아니라 실제 사용자 요청을 반영(또는 비어 있음)
4. 원본 입력이 1500자까지 보존됨
5. [Approve]/[Deny] 버튼 정상 동작 (기존 회귀 없음)

- [ ] **Step 4: 수동 E2E — Bedrock 실패 fallback**

Run (서버 환경에서 잘못된 모델 ID로 강제 실패 시뮬레이션은 생략; 대신 코드 경로 신뢰). 최소 확인:
- 서버 로그에 요약 성공/실패가 기록되는지: `tail -f ~/Library/Logs/oh-my-cc-agent/*.log` 에서 `Bedrock summarize` 또는 정상 카드 전송 로그 확인

- [ ] **Step 5: hands-off.md Change Log 갱신 + Commit**

`hands-off.md` 최상단에 구현 완료 Change Log(타임스탬프, 변경 파일, 검증 결과) 추가 후:

```bash
git add hands-off.md
git commit -m "docs: record permission summary implementation"
```

---

## 검증 체크리스트 (실행자용)

- [ ] 단위 테스트 전체 통과: `cd code/app && .venv/bin/pytest tests/ -v`
- [ ] isMeta skill 텍스트가 context에서 제외됨
- [ ] Slack 카드에 요청사항/risk/확인필요 3섹션 표시
- [ ] 위험 명령(rm -rf, pkill, curl|sh 등)이 risk로 식별됨
- [ ] Bedrock 실패/타임아웃 시 raw 표시로 fallback (Claude 흐름 비차단)
- [ ] 원본 input 1500자 보존
- [ ] 기존 Approve/Deny 회귀 없음
```
