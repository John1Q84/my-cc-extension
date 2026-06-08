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


from ask_blocks import build_ask_blocks


def test_single_select_has_option_buttons_unique_block_ids():
    questions = [
        {"question": "Which color?", "header": "Color", "multiSelect": False,
         "options": [{"label": "Red", "description": "warm"}, {"label": "Blue", "description": "cool"}]},
    ]
    blocks = build_ask_blocks("ask-1", questions)
    actions = [b for b in blocks if b["type"] == "actions"]
    assert len(actions) == 1
    assert [e["action_id"] for e in actions[0]["elements"]] == ["ask::0::0", "ask::0::1"]
    assert actions[0]["block_id"] == "ask-1::0"
    assert actions[0]["elements"][0]["text"]["text"] == "Red"


def test_multi_question_unique_block_ids():
    questions = [
        {"question": "Color?", "multiSelect": False, "options": [{"label": "Red"}, {"label": "Blue"}]},
        {"question": "Size?", "multiSelect": False, "options": [{"label": "S"}, {"label": "L"}]},
    ]
    blocks = build_ask_blocks("ask-7", questions)
    block_ids = [b["block_id"] for b in blocks if b["type"] == "actions"]
    assert block_ids == ["ask-7::0", "ask-7::1"]


def test_multi_select_uses_checkboxes_and_submit():
    questions = [
        {"question": "Toppings?", "header": "Topping", "multiSelect": True,
         "options": [{"label": "Cheese"}, {"label": "Onion"}]},
    ]
    blocks = build_ask_blocks("ask-9", questions)
    has_checkboxes = any(e.get("type") == "checkboxes"
                         for b in blocks if b["type"] == "actions" for e in b["elements"])
    assert has_checkboxes
    submit_ids = [e["action_id"] for b in blocks if b["type"] == "actions"
                  for e in b["elements"] if e.get("action_id", "").startswith("ask_submit::")]
    assert submit_ids == ["ask_submit::0"]
