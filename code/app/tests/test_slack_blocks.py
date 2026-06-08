import os
import pytest

# Set required env vars before importing approval_server
os.environ.setdefault("SLACK_APPROVAL_BOT_TOKEN", "test-token")
os.environ.setdefault("SLACK_APPROVAL_CHANNEL_ID", "test-channel")

from approval_server import build_slack_blocks


def test_blank_risk_items_fall_back_to_no_risk():
    """Blank or whitespace-only risk items should fall back to '(특이사항 없음)'."""
    blocks = build_slack_blocks(
        "id",
        "Bash",
        "echo hi",
        summary={"request": "r", "risk": [""], "confirm": "c"},
    )
    risk_section = [
        b
        for b in blocks
        if b.get("type") == "section" and "영향도" in b.get("text", {}).get("text", "")
    ]
    assert risk_section, "risk section should exist"
    assert "특이사항 없음" in risk_section[0]["text"]["text"]


def test_whitespace_risk_items_fall_back():
    """Whitespace-only risk items should also fall back."""
    blocks = build_slack_blocks(
        "id",
        "Bash",
        "echo hi",
        summary={"request": "r", "risk": ["  ", "\t"], "confirm": "c"},
    )
    risk_section = [
        b
        for b in blocks
        if b.get("type") == "section" and "영향도" in b.get("text", {}).get("text", "")
    ]
    assert risk_section
    assert "특이사항 없음" in risk_section[0]["text"]["text"]


def test_valid_risk_items_are_preserved():
    """Valid risk items should be displayed normally."""
    blocks = build_slack_blocks(
        "id",
        "Bash",
        "echo hi",
        summary={"request": "r", "risk": ["Risk 1", "Risk 2"], "confirm": "c"},
    )
    risk_section = [
        b
        for b in blocks
        if b.get("type") == "section" and "영향도" in b.get("text", {}).get("text", "")
    ]
    assert risk_section
    text = risk_section[0]["text"]["text"]
    assert "Risk 1" in text
    assert "Risk 2" in text
    assert "특이사항 없음" not in text
