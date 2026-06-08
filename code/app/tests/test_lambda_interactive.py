from lambda_handler import parse_ask_action, merge_selection


def test_parse_ask_action_single_select():
    payload = {"actions": [{"action_id": "ask::0::1", "block_id": "ask-abc::0"}]}
    assert parse_ask_action(payload) == {"ask_id": "ask-abc", "qidx": 0, "selected": [1], "is_submit": False}


def test_parse_ask_action_multi_submit():
    payload = {
        "actions": [{"action_id": "ask_submit::0", "block_id": "ask-xyz::0"}],
        "state": {"values": {"ask-xyz::0": {"ask_check::0": {
            "selected_options": [{"value": "0"}, {"value": "2"}]}}}},
    }
    assert parse_ask_action(payload) == {"ask_id": "ask-xyz", "qidx": 0, "selected": [0, 2], "is_submit": True}


# 검증 결함 #4: selections 누적/완료 임계 로직을 순수함수로 분리해 테스트
def test_merge_selection_accumulates_and_flips_status():
    # 2개 질문 중 1개만 응답 → pending
    sel, status = merge_selection({"0": [1]}, qidx=1, selected=[0], expected_count=2)
    assert sel == {"0": [1], "1": [0]}
    assert status == "answered"  # 이제 2개 모두 → answered


def test_merge_selection_partial_stays_pending():
    sel, status = merge_selection({}, qidx=0, selected=[1], expected_count=2)
    assert sel == {"0": [1]}
    assert status == "pending"


def test_merge_selection_decimal_keys_normalized():
    # DynamoDB Decimal round-trip 방어: 기존 selections에 Decimal이 섞여도 안전
    from decimal import Decimal
    sel, status = merge_selection({"0": [Decimal("1")]}, qidx=1, selected=[2], expected_count=2)
    assert status == "answered"
