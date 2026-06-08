"""AskUserQuestion ↔ Slack 변환 순수 함수 (I/O 없음, 테스트 대상)."""


def encode_action_id(qidx: int, oidx: int) -> str:
    return f"ask::{qidx}::{oidx}"


def decode_action_id(action_id: str) -> tuple:
    _, qidx, oidx = action_id.split("::")
    return int(qidx), int(oidx)


def build_answers(questions: list, selections: dict) -> dict:
    """selections: {qidx: [oidx,...]} → {question_text: "label" 또는 "l1,l2"}.
    DynamoDB Decimal 방어: qidx/oidx를 int로 강제(검증 결함 #1)."""
    answers = {}
    for qidx, oidxs in selections.items():
        q = questions[int(qidx)]
        labels = [q["options"][int(o)]["label"] for o in oidxs]
        answers[q["question"]] = ",".join(labels)
    return answers


def build_ask_blocks(ask_id: str, questions: list) -> list:
    """AskUserQuestion questions → Slack blocks.
    단일선택: 옵션별 button. 멀티선택: checkboxes + Submit.
    block_id = {ask_id}::{qidx} (메시지 내 유일성 — Slack 요구)."""
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Claude Code 선택 요청*"}},
    ]
    for qidx, q in enumerate(questions):
        header = q.get("header", "")
        prompt = q["question"]
        title = f"*{header}* — {prompt}" if header else f"*{prompt}*"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": title}})

        if q.get("multiSelect"):
            options = [
                {"text": {"type": "plain_text", "text": opt["label"][:75]}, "value": str(oidx)}
                for oidx, opt in enumerate(q["options"])
            ]
            blocks.append({
                "type": "actions", "block_id": f"{ask_id}::{qidx}",
                "elements": [
                    {"type": "checkboxes", "options": options, "action_id": f"ask_check::{qidx}"},
                    {"type": "button", "text": {"type": "plain_text", "text": "Submit"},
                     "style": "primary", "value": "submit", "action_id": f"ask_submit::{qidx}"},
                ],
            })
        else:
            buttons = []
            for oidx, opt in enumerate(q["options"]):
                buttons.append({
                    "type": "button", "text": {"type": "plain_text", "text": opt["label"][:75]},
                    "value": str(oidx), "action_id": encode_action_id(qidx, oidx),
                })
                desc = opt.get("description", "")
                if desc:
                    blocks.append({"type": "context",
                                   "elements": [{"type": "mrkdwn", "text": f"• *{opt['label']}*: {desc}"}]})
            blocks.append({"type": "actions", "block_id": f"{ask_id}::{qidx}", "elements": buttons})
    return blocks
