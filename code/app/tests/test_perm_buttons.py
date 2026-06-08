from perm_buttons import extract_rules, rule_label, build_permission_buttons


def test_extract_rules_from_addrules():
    suggestions = [
        {"type": "addRules",
         "rules": [{"toolName": "Bash", "ruleContent": "Bash(npm run *)"},
                   {"toolName": "Read", "ruleContent": "//etc/**"}],
         "behavior": "allow", "destination": "localSettings"},
    ]
    assert extract_rules(suggestions) == ["Bash(npm run *)", "//etc/**"]


def test_extract_rules_empty_when_no_suggestions():
    assert extract_rules([]) == []
    assert extract_rules(None) == []


def test_extract_rules_ignores_non_addrules():
    assert extract_rules([{"type": "other", "rules": [{"ruleContent": "x"}]}]) == []


def test_rule_label_single():
    assert rule_label(["Bash(npm run *)"]) == "Bash(npm run *)"


def test_rule_label_multiple_shows_count():
    assert rule_label(["Bash(a)", "Bash(b)", "Bash(c)"]) == "Bash(a) 외 2건"


def test_build_permission_buttons_with_rules_has_three():
    blocks = build_permission_buttons("aid-1", ["Bash(npm run *)"])
    assert blocks["type"] == "actions"
    assert blocks["block_id"] == "aid-1"
    ids = [e["action_id"] for e in blocks["elements"]]
    assert ids == ["approve_action", "approve_rule_action", "deny_action"]
    # 규칙 라벨이 don't-ask 버튼 텍스트에 포함
    rule_btn = blocks["elements"][1]
    assert "Bash(npm run *)" in rule_btn["text"]["text"]
    assert rule_btn["value"] == "approve_rule"


def test_build_permission_buttons_without_rules_has_two():
    blocks = build_permission_buttons("aid-2", [])
    ids = [e["action_id"] for e in blocks["elements"]]
    assert ids == ["approve_action", "deny_action"]
