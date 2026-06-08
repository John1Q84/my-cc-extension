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
    # DynamoDB Decimal round-trip 방어: 기존 selections의 Decimal 키를 str로 정규화해야
    # 동일 qidx가 중복 카운트되지 않는다. Decimal("1") 키 + qidx=1 응답은 같은 질문이므로
    # 키가 1개("1")로 합쳐져야 하고, expected_count=2에는 못 미쳐 pending이어야 한다.
    from decimal import Decimal
    sel, status = merge_selection({Decimal("1"): [0]}, qidx=1, selected=[2], expected_count=2)
    assert sel == {"1": [2]}  # Decimal("1") 키가 "1"로 정규화되어 덮어써짐, 중복 없음
    assert status == "pending"  # 질문 1개만 응답됨 (2개 중) → 거짓 answered 방지


def test_merge_selection_mixed_decimal_and_str_keys():
    # Decimal과 str 키가 섞여 있어도 모두 str로 정규화되어 정확히 카운트된다.
    from decimal import Decimal
    sel, status = merge_selection({Decimal("0"): [1]}, qidx=1, selected=[2], expected_count=2)
    assert sel == {"0": [1], "1": [2]}  # 키 2개 (서로 다른 질문)
    assert status == "answered"
