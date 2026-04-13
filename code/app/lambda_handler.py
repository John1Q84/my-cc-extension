"""
Slack Interactive Webhook Handler (AWS Lambda)

Slack에서 Approve/Deny 버튼 클릭 시 호출되는 Lambda 함수.
- Slack Signing Secret으로 요청 검증
- DynamoDB에 승인/거부 상태 업데이트
- Slack 메시지를 결과 텍스트로 교체
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import parse_qs
from urllib.request import Request, urlopen

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")


def verify_slack_signature(signing_secret: str, timestamp: str, body: str, signature: str) -> bool:
    if abs(time.time() - int(timestamp)) > 300:
        logger.warning("Request timestamp too old: %s", timestamp)
        return False

    sig_basestring = f"v0:{timestamp}:{body}"
    computed = "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed, signature)


def build_response_message(action: str, user_name: str, original_blocks: list) -> dict:
    emoji = ":white_check_mark:" if action == "approve" else ":x:"
    status_text = "Approved" if action == "approve" else "Denied"

    # 원본 블록에서 actions 블록을 제거하고 결과 블록으로 교체
    updated_blocks = [b for b in original_blocks if b.get("type") != "actions"]
    updated_blocks.append({"type": "divider"})
    updated_blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{emoji} *{status_text}* by *{user_name}*",
            },
        }
    )

    return {
        "replace_original": True,
        "text": f"{emoji} {status_text} by {user_name}",
        "blocks": updated_blocks,
    }


def handler(event, context):
    logger.info("Received event: %s", json.dumps(event, default=str))

    signing_secret = os.environ.get("SLACK_SIGNING_SECRET")
    table_name = os.environ.get("DYNAMODB_TABLE")

    if not signing_secret or not table_name:
        logger.error("Missing required environment variables")
        return {"statusCode": 500, "body": "Internal server error"}

    # Body 추출
    body = event.get("body", "")
    if event.get("isBase64Encoded", False):
        body = base64.b64decode(body).decode("utf-8")

    # Slack Signing Secret 검증
    headers = event.get("headers", {})
    timestamp = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")

    if not timestamp or not signature:
        logger.warning("Missing Slack signature headers")
        return {"statusCode": 401, "body": "Unauthorized"}

    if not verify_slack_signature(signing_secret, timestamp, body, signature):
        logger.warning("Invalid Slack signature")
        return {"statusCode": 401, "body": "Unauthorized"}

    # Payload 파싱
    try:
        parsed = parse_qs(body)
        payload = json.loads(parsed["payload"][0])
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        logger.error("Failed to parse payload: %s", exc)
        return {"statusCode": 400, "body": "Bad request"}

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

    user_name = payload.get("user", {}).get("name", "unknown")
    original_blocks = payload.get("message", {}).get("blocks", [])
    response_url = payload.get("response_url", "")
    status = "approved" if decision == "approve" else "denied"

    logger.info("decision=%s approval_id=%s user=%s", decision, approval_id, user_name)

    # DynamoDB 업데이트
    try:
        table = dynamodb.Table(table_name)
        table.update_item(
            Key={"approval_id": approval_id},
            UpdateExpression="SET #s = :status, decided_by = :user, decided_at = :ts",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": status,
                ":user": user_name,
                ":ts": int(time.time()),
            },
        )
    except Exception:
        logger.exception("DynamoDB update failed for approval_id=%s", approval_id)
        return {"statusCode": 500, "body": "Internal server error"}

    # response_url로 Slack 메시지 업데이트 (버튼 제거 + 결과 표시)
    if response_url and response_url.startswith("https://hooks.slack.com/"):
        response_body = build_response_message(decision, user_name, original_blocks)
        try:
            req = Request(
                response_url,
                data=json.dumps(response_body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urlopen(req)  # nosec B310 -- URL scheme validated above
            logger.info("Slack message updated via response_url for approval_id=%s", approval_id)
        except Exception:
            logger.exception("Failed to update Slack message via response_url")

    return {"statusCode": 200, "body": ""}
