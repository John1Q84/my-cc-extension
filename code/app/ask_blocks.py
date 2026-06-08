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
