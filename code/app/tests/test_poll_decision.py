from poll_decision import decide_permission, decide_ask


def test_permission_button_wins_over_freetext():
    # 버튼 status 우선
    assert decide_permission(status="approved", free_text="하지마") == ("approved", None)
    assert decide_permission(status="denied", free_text=None) == ("denied", None)


def test_permission_freetext_when_no_button():
    # 버튼 없고 free_text 있으면 deny + reason
    assert decide_permission(status="pending", free_text="X로 해줘") == ("deny_freetext", "X로 해줘")


def test_permission_none_when_nothing():
    assert decide_permission(status="pending", free_text=None) == (None, None)


def test_ask_button_wins_over_freetext():
    assert decide_ask(status="answered", free_text="자유답", question_count=1, has_selections=True) == ("answered", None)


def test_ask_freetext_single_question():
    # 단일 질문 + free_text → 자유답 채택
    assert decide_ask(status="pending", free_text="빨강 말고 초록", question_count=1, has_selections=False) == ("freetext", "빨강 말고 초록")


def test_ask_freetext_ignored_multi_question():
    # 다중 질문 + free_text → 무시 (모호)
    assert decide_ask(status="pending", free_text="아무거나", question_count=2, has_selections=False) == (None, None)


def test_ask_none_when_nothing():
    assert decide_ask(status="pending", free_text=None, question_count=1, has_selections=False) == (None, None)


def test_ask_button_wins_during_nonatomic_write_window():
    # 검증 결함 #2: _handle_ask가 selection을 먼저 쓰고 status=answered를 나중에 쓰는
    # 비원자 윈도우에서, status='pending'이지만 selections가 차있으면 버튼 우선
    # (free_text가 먼저 있어도 버튼 선택을 폐기하지 않음)
    assert decide_ask(status="pending", free_text="자유답", question_count=1, has_selections=True) == (None, None)
