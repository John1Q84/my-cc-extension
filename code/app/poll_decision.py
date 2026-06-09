"""poll 루프의 답 채택 결정 (순수 함수, I/O 없음).
버튼(status) 우선, 없으면 free_text 폴백."""


def decide_permission(status: str, free_text):
    """PermissionRequest: 반환 (outcome, reason).
    outcome: 'approved'|'denied'|'deny_freetext'|None."""
    if status in ("approved", "denied"):
        return (status, None)
    if free_text:
        return ("deny_freetext", free_text)
    return (None, None)


def decide_ask(status: str, free_text, question_count: int, has_selections: bool = False):
    """AskUserQuestion: 반환 (outcome, text).
    outcome: 'answered'|'freetext'|None.
    - status=='answered' → 버튼 확정.
    - has_selections (버튼 선택이 기록됐으나 status flip 전 비원자 윈도우) → 버튼 우선,
      free_text 폐기하고 대기(None) — 검증 결함 #2.
    - free_text는 단일 질문이고 버튼 선택이 없을 때만 채택(다중은 모호 → 무시)."""
    if status == "answered":
        return ("answered", None)
    if has_selections:
        return (None, None)  # 버튼 진행 중 — answered flip 대기, free_text 폐기 안 함
    if free_text and question_count == 1:
        return ("freetext", free_text)
    return (None, None)
