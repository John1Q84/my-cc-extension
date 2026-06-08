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

# AWS_REGION 충돌 방지: 전용 변수 우선, 없으면 ap-northeast-2 (Lambda 런타임은 자체 region 주입)
_AWS_REGION = os.environ.get("SLACK_APPROVAL_AWS_REGION") or os.environ.get("AWS_REGION") or "ap-northeast-2"
dynamodb = boto3.resource("dynamodb", region_name=_AWS_REGION)


def parse_ask_action(payload: dict) -> dict:
    """Slack ask action → {ask_id, qidx, selected:[oidx], is_submit}.
    단일선택: action_id=ask::qidx::oidx, block_id=ask_id::qidx.
    멀티 Submit: action_id=ask_submit::qidx, block_id=ask_id::qidx,
                state.values[block_id][ask_check::qidx].selected_options[].value."""
    action = payload["actions"][0]
    action_id = action["action_id"]
    block_id = action["block_id"]
    ask_id = block_id.split("::")[0]

    if action_id.startswith("ask_submit::"):
        qidx = int(action_id.split("::")[1])
        state = payload.get("state", {}).get("values", {}).get(block_id, {})
        opts = state.get(f"ask_check::{qidx}", {}).get("selected_options", [])
        selected = sorted(int(o["value"]) for o in opts)
        return {"ask_id": ask_id, "qidx": qidx, "selected": selected, "is_submit": True}

    _, qidx_s, oidx_s = action_id.split("::")
    return {"ask_id": ask_id, "qidx": int(qidx_s), "selected": [int(oidx_s)], "is_submit": False}


def merge_selection(existing: dict, qidx: int, selected: list, expected_count: int) -> tuple:
    """기존 selections에 qidx 응답 병합 → (selections, status). 순수함수(테스트 대상).
    검증 결함 #4: 누적/완료 임계 로직 격리. (참고: 실제 원자적 쓰기는 _handle_ask가 담당)"""
    selections = dict(existing)
    selections[str(qidx)] = selected
    status = "answered" if len(selections) >= expected_count else "pending"
    return selections, status


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
    emoji = ":x:" if action == "deny" else ":white_check_mark:"
    status_text = {
        "deny": "Denied",
        "approve_rule": "Approved (rule added)",
    }.get(action, "Approved")

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


def _handle_ask(payload: dict, table_name: str) -> dict:
    """ask 버튼/Submit → DynamoDB selections 누적, 완료 시 status=answered.
    원자적 per-key 갱신으로 다중질문 동시클릭 lost-update 방지."""
    parsed = parse_ask_action(payload)
    ask_id = parsed["ask_id"]
    qidx = parsed["qidx"]
    user_name = payload.get("user", {}).get("name", "unknown")
    response_url = payload.get("response_url", "")

    table = dynamodb.Table(table_name)
    try:
        # qidx 키만 원자적으로 set (전체 map 미교체) → 동시 클릭 안전
        resp = table.update_item(
            Key={"approval_id": ask_id},
            UpdateExpression="SET selections.#q = :sel, decided_by = :u, decided_at = :ts",
            ExpressionAttributeNames={"#q": str(qidx)},
            ExpressionAttributeValues={
                ":sel": parsed["selected"], ":u": user_name, ":ts": int(time.time()),
            },
            ReturnValues="ALL_NEW",
        )
        item = resp.get("Attributes", {})
        selections = item.get("selections", {})
        expected = int(item.get("expected_count", 1))
        new_status = "answered" if len(selections) >= expected else "pending"
        # 완료 시에만 status 갱신 (별도 update — pending은 put_item 초기값 유지)
        if new_status == "answered":
            table.update_item(
                Key={"approval_id": ask_id},
                UpdateExpression="SET #s = :st",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":st": "answered"},
            )
    except Exception:
        logger.exception("ask update failed for %s", ask_id)
        return {"statusCode": 500, "body": "Internal server error"}

    if response_url and response_url.startswith("https://hooks.slack.com/"):
        done = new_status == "answered"
        msg = (":white_check_mark: 모든 질문 응답 완료" if done
               else f":ballot_box_with_check: 질문 {qidx + 1} 응답됨 (by {user_name})")
        try:
            req = Request(response_url,
                          data=json.dumps({"replace_original": False, "text": msg}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
            urlopen(req)  # nosec B310
        except Exception:
            logger.exception("ask slack update failed")
    return {"statusCode": 200, "body": ""}


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

    # ask 계열 분기 (AskUserQuestion 양방향)
    first_action_id = payload.get("actions", [{}])[0].get("action_id", "")
    if first_action_id.startswith(("ask::", "ask_submit::")):
        return _handle_ask(payload, table_name)

    # Action 추출
    try:
        action = payload["actions"][0]
        decision = action["value"]  # "approve" or "deny"
        approval_id = action["block_id"]
    except (KeyError, IndexError) as exc:
        logger.error("Failed to extract action data: %s", exc)
        return {"statusCode": 400, "body": "Bad request"}

    if decision not in ("approve", "deny", "approve_rule"):
        logger.error("Unexpected action value: %s", decision)
        return {"statusCode": 400, "body": "Bad request"}

    user_name = payload.get("user", {}).get("name", "unknown")
    original_blocks = payload.get("message", {}).get("blocks", [])
    response_url = payload.get("response_url", "")
    status = "denied" if decision == "deny" else "approved"
    apply_rule = decision == "approve_rule"

    logger.info("decision=%s approval_id=%s user=%s", decision, approval_id, user_name)

    # DynamoDB 업데이트
    try:
        table = dynamodb.Table(table_name)
        table.update_item(
            Key={"approval_id": approval_id},
            UpdateExpression="SET #s = :status, decided_by = :user, decided_at = :ts, apply_rule = :ar",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": status,
                ":user": user_name,
                ":ts": int(time.time()),
                ":ar": apply_rule,
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
