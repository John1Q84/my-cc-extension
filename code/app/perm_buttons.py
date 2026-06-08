"""PermissionRequest permission_suggestions → Slack 버튼/규칙 (순수 함수, I/O 없음)."""


def extract_rules(permission_suggestions) -> list:
    """addRules 타입 suggestion에서 ruleContent 목록 추출."""
    rules = []
    for s in permission_suggestions or []:
        if s.get("type") == "addRules":
            for r in s.get("rules", []):
                rc = r.get("ruleContent")
                if rc:
                    rules.append(rc)
    return rules


def rule_label(rules: list) -> str:
    """버튼/미리보기용 라벨. 첫 규칙 + 나머지 건수."""
    if not rules:
        return ""
    if len(rules) == 1:
        return rules[0]
    return f"{rules[0]} 외 {len(rules) - 1}건"


def build_permission_buttons(approval_id: str, rules: list) -> dict:
    """actions 블록 생성. rules 있으면 3버튼(허용/허용+규칙/거부), 없으면 2버튼."""
    elements = [
        {"type": "button", "text": {"type": "plain_text", "text": "✅ 허용"},
         "style": "primary", "value": "approve", "action_id": "approve_action"},
    ]
    if rules:
        label = rule_label(rules)
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": f"✅ 허용 + 다시 안 묻기 ({label})"[:75]},
            "value": "approve_rule", "action_id": "approve_rule_action",
        })
    elements.append(
        {"type": "button", "text": {"type": "plain_text", "text": "❌ 거부"},
         "style": "danger", "value": "deny", "action_id": "deny_action"}
    )
    return {"type": "actions", "block_id": approval_id, "elements": elements}
