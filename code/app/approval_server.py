"""
Claude Code Approval Server

Claude Code의 PermissionRequest hook을 수신하여 Slack으로 Approve/Deny 요청을 전송하고,
DynamoDB polling으로 결과를 반환하는 로컬 FastAPI 서버.
"""

import asyncio
import json
import logging
import os
import time
import uuid

import boto3
from fastapi import FastAPI, Request
from slack_sdk import WebClient
from summarizer import summarize

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
                        last_user_text = c["text"]
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
) -> list:
    truncated_input = tool_input[:500] + "..." if len(tool_input) > 500 else tool_input
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
            if status in ("approved", "denied"):
                logger.info("approval_id=%s status=%s", approval_id, status)
                return status
        except Exception:
            logger.exception("DynamoDB GetItem failed for %s", approval_id)

    # Timeout: Slack 메시지 업데이트
    logger.warning("approval_id=%s timed out", approval_id)
    update_slack_message(message_ts, blocks, ":alarm_clock:", "Timed out")
    return "timeout"


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


@app.post("/hook")
async def hook(request: Request):
    body = await request.json()
    logger.info("Received hook: %s", json.dumps(body, default=str)[:500])

    # PermissionRequest payload 파싱
    tool_name = body.get("tool_name", "unknown")
    tool_input = json.dumps(body.get("tool_input", {}), ensure_ascii=False, indent=2)
    cwd = body.get("cwd", "")
    transcript_path = body.get("transcript_path", "")

    # transcript에서 사용자의 최근 요청 컨텍스트 추출
    user_context = extract_user_context(transcript_path)

    # Bedrock Haiku로 요청 요약 (실패 시 None → raw fallback)
    summary = summarize(tool_name, tool_input, user_context)
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
        }
    )
    logger.info("Created approval_id=%s for tool=%s", approval_id, tool_name)

    # Slack 메시지 전송
    blocks = build_slack_blocks(approval_id, tool_name, tool_input, cwd, user_context, summary)
    slack_resp = slack.chat_postMessage(
        channel=SLACK_CHANNEL_ID,
        text=f"Claude Code permission request: {tool_name}",
        blocks=blocks,
    )
    message_ts = slack_resp["ts"]
    logger.info("Slack message sent for approval_id=%s ts=%s", approval_id, message_ts)

    # DynamoDB polling (터미널 disconnect 감지 포함)
    result = await poll_dynamodb(approval_id, request, message_ts, blocks)

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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080)
