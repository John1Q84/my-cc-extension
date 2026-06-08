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
