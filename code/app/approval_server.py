"""
Claude Code Approval Server

Claude Code의 PermissionRequest hook을 수신하여 Slack으로 Approve/Deny 요청을 전송하고,
DynamoDB polling으로 결과를 반환하는 로컬 FastAPI 서버.
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid

import boto3
from fastapi import FastAPI, Request
from slack_sdk import WebClient
from summarizer import summarize
from perm_buttons import extract_rules, build_permission_buttons
from ask_blocks import build_ask_blocks, build_answers
from poll_decision import decide_permission, decide_ask

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Claude Code Approval Server")

# Configuration (zsh 환경변수에서 로드)
SLACK_BOT_TOKEN = os.environ.get("SLACK_APPROVAL_BOT_TOKEN")
SLACK_CHANNEL_ID = os.environ.get("SLACK_APPROVAL_CHANNEL_ID")
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "claude-approval-requests")
# AWS_REGION과 충돌 방지: 전용 변수 우선, 없으면 ap-northeast-2 고정
AWS_REGION = os.environ.get("SLACK_APPROVAL_AWS_REGION", "ap-northeast-2")

POLL_INTERVAL = 5  # seconds
POLL_TIMEOUT = 300  # 5 minutes

# AWS / Slack clients
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(DYNAMODB_TABLE)
slack = WebClient(token=SLACK_BOT_TOKEN)


def extract_user_context(transcript_path: str) -> str:
    """transcript JSONL에서 가장 최근 사용자 텍스트 메시지를 추출."""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""

    last_user_text = ""
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line)
                if obj.get("type") != "user":
                    continue
                if obj.get("isMeta"):  # skill/system 주입 메시지 제외
                    continue
                content = obj.get("message", {}).get("content", [])
                if not isinstance(content, list):
                    continue
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        text = c["text"]
                        # Skip interrupt markers
                        if text.strip().startswith("[Request interrupted"):
                            continue
                        # 첨부 이미지 placeholder([Image #N]) 제거 — 실제 텍스트만 유지
                        text = re.sub(r"\[Image #\d+\]", "", text)
                        text = re.sub(r"\s{2,}", " ", text).strip(" ,")
                        # placeholder만 있던 경우(빈 텍스트) → 이전 메시지 유지
                        if text:
                            last_user_text = text
    except Exception:
        logger.exception("Failed to read transcript: %s", transcript_path)

    return last_user_text


def build_slack_blocks(
    approval_id: str,
    tool_name: str,
    tool_input: str,
    cwd: str = "",
    user_context: str = "",
    summary: dict | None = None,
    rules: list | None = None,
) -> list:
    truncated_input = tool_input[:2800] + "..." if len(tool_input) > 2800 else tool_input
    truncated_context = user_context[:300] + "..." if len(user_context) > 300 else user_context

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
        risk_items = [r for r in summary.get("risk", []) if str(r).strip()]
        risk_lines = "\n".join(f"• {r}" for r in risk_items) or "• (특이사항 없음)"
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
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Context:*\n> {truncated_context}",
                },
            }
        )

    if cwd:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"*cwd:* `{cwd}`"},
                ],
            }
        )

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

    blocks.append(build_permission_buttons(approval_id, rules or []))

    return blocks


def update_slack_message(message_ts: str, original_blocks: list, emoji: str, text: str):
    """Slack 메시지에서 버튼을 제거하고 결과 블록으로 교체."""
    updated_blocks = [b for b in original_blocks if b.get("type") != "actions"]
    updated_blocks.append({"type": "divider"})
    updated_blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{emoji} *{text}*"},
        }
    )
    try:
        slack.chat_update(
            channel=SLACK_CHANNEL_ID,
            ts=message_ts,
            text=text,
            blocks=updated_blocks,
        )
        logger.info("Slack message updated: ts=%s text=%s", message_ts, text)
    except Exception:
        logger.exception("Failed to update Slack message ts=%s", message_ts)


async def poll_dynamodb(
    approval_id: str,
    request: Request,
    message_ts: str,
    blocks: list,
) -> str:
    elapsed = 0
    while elapsed < POLL_TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

        # 터미널에서 처리된 경우 (Claude Code가 HTTP 연결을 끊음)
        if await request.is_disconnected():
            logger.info("Client disconnected for approval_id=%s (resolved by terminal)", approval_id)
            update_slack_message(message_ts, blocks, ":desktop_computer:", "Resolved by terminal")
            try:
                table.update_item(
                    Key={"approval_id": approval_id},
                    UpdateExpression="SET #s = :status, decided_at = :ts",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={
                        ":status": "terminal",
                        ":ts": int(time.time()),
                    },
                )
            except Exception:
                logger.exception("DynamoDB update failed for terminal resolution")
            return "terminal"

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

    # Timeout: Slack 메시지 업데이트
    logger.warning("approval_id=%s timed out", approval_id)
    update_slack_message(message_ts, blocks, ":alarm_clock:", "Timed out")
    return "timeout"


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

    logger.warning("ask_id=%s timed out", ask_id)
    update_slack_message(message_ts, blocks, ":alarm_clock:", "시간 초과")
    return None


@app.get("/health")
async def health():
    return {"status": "ok"}


def build_notify_blocks(title: str, summary: str, project: str, status: str) -> list:
    status_emoji = {
        "completed": ":white_check_mark:",
        "in_progress": ":hourglass_flowing_sand:",
        "blocked": ":no_entry:",
    }.get(status, ":memo:")

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{status_emoji} *{title}*",
            },
        },
    ]

    if project:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"*project:* `{project}`"},
                ],
            }
        )

    if summary:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": summary[:3000],
                },
            }
        )

    return blocks


@app.post("/notify")
async def notify(request: Request):
    body = await request.json()
    title = body.get("title", "Task Update")
    summary = body.get("summary", "")
    project = body.get("project", "")
    status = body.get("status", "completed")

    blocks = build_notify_blocks(title, summary, project, status)

    try:
        slack.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            text=f"{title}: {summary[:200]}",
            blocks=blocks,
        )
        logger.info("Notify sent: title=%s project=%s", title, project)
        return {"status": "ok", "message": "Notification sent"}
    except Exception:
        logger.exception("Failed to send notification")
        return {"status": "error", "message": "Failed to send notification"}


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
    try:
        table.update_item(
            Key={"approval_id": ask_id},
            UpdateExpression="SET message_ts = :mt",
            ExpressionAttributeValues={":mt": message_ts},
        )
    except Exception:
        logger.exception("message_ts 저장 실패 ask_id=%s", ask_id)

    answers = await poll_ask(ask_id, request, message_ts, blocks, questions)

    if answers is None:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}

    update_slack_message(message_ts, blocks, ":white_check_mark:", "응답 완료")
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "updatedInput": {**tool_input, "answers": answers},
    }}


@app.post("/hook")
async def hook(request: Request):
    body = await request.json()
    logger.info("Received hook: %s", json.dumps(body, default=str)[:500])

    # PermissionRequest payload 파싱
    tool_name = body.get("tool_name", "unknown")
    # AskUserQuestion은 PreToolUse(/ask)가 전담 — PermissionRequest 중복 카드 방지
    if tool_name == "AskUserQuestion":
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                       "decision": {"behavior": "allow"}}}
    tool_input = json.dumps(body.get("tool_input", {}), ensure_ascii=False, indent=2)
    cwd = body.get("cwd", "")
    transcript_path = body.get("transcript_path", "")

    permission_suggestions = body.get("permission_suggestions", [])
    rules = extract_rules(permission_suggestions)

    # transcript에서 사용자의 최근 요청 컨텍스트 추출
    user_context = extract_user_context(transcript_path)

    # Bedrock Haiku로 요청 요약 (실패 시 None → raw fallback)
    # 동기 boto3 호출을 스레드로 오프로드 → 단일 이벤트 루프 차단 방지(동시 승인 폴링 유지)
    summary = await asyncio.to_thread(summarize, tool_name, tool_input, user_context)
    logger.info("Summary %s for tool=%s", "generated" if summary else "unavailable (raw fallback)", tool_name)

    approval_id = str(uuid.uuid4())
    expires_at = int(time.time()) + 600  # TTL 10분

    # DynamoDB에 pending 상태 저장
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
    logger.info("Created approval_id=%s for tool=%s", approval_id, tool_name)

    # Slack 메시지 전송
    blocks = build_slack_blocks(approval_id, tool_name, tool_input, cwd, user_context, summary, rules)
    slack_resp = slack.chat_postMessage(
        channel=SLACK_CHANNEL_ID,
        text=f"Claude Code permission request: {tool_name}",
        blocks=blocks,
    )
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

    # DynamoDB polling (터미널 disconnect 감지 포함)
    result = await poll_dynamodb(approval_id, request, message_ts, blocks)

    if result.startswith("freetext:"):
        reason = result[len("freetext:"):]
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny", "reason": reason}}}

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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080)
