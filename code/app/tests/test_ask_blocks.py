from ask_blocks import encode_action_id, decode_action_id, build_answers


def test_encode_decode_action_id():
    assert encode_action_id(0, 2) == "ask::0::2"
    assert decode_action_id("ask::0::2") == (0, 2)


def test_build_answers_single_select():
    questions = [{"question": "Color?", "multiSelect": False,
                  "options": [{"label": "Red"}, {"label": "Blue"}]}]
    assert build_answers(questions, {0: [1]}) == {"Color?": "Blue"}


def test_build_answers_multi_select_comma_join():
    questions = [{"question": "Toppings?", "multiSelect": True,
                  "options": [{"label": "Cheese"}, {"label": "Mushroom"}, {"label": "Onion"}]}]
    assert build_answers(questions, {0: [0, 2]}) == {"Toppings?": "Cheese,Onion"}


def test_build_answers_multiple_questions():
    questions = [
        {"question": "Color?", "multiSelect": False, "options": [{"label": "Red"}, {"label": "Blue"}]},
        {"question": "Size?", "multiSelect": False, "options": [{"label": "S"}, {"label": "L"}]},
    ]
    assert build_answers(questions, {0: [0], 1: [1]}) == {"Color?": "Red", "Size?": "L"}


def test_build_answers_coerces_decimal_indices():
    # 검증 결함 #1: DynamoDB가 oidx를 Decimal로 반환해도 안전해야 함
    from decimal import Decimal
    questions = [{"question": "Color?", "multiSelect": False,
                  "options": [{"label": "Red"}, {"label": "Blue"}]}]
    assert build_answers(questions, {Decimal("0"): [Decimal("1")]}) == {"Color?": "Blue"}
