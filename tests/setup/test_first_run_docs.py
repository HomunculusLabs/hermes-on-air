"""First-run recovery and manifest warning are documented at the entry point."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_install_documents_next_session_and_gateway_recovery():
    quickstart = (ROOT / "docs/quickstart.md").read_text()
    skill = (ROOT / "onair/skills/setup/SKILL.md").read_text()
    for text in (quickstart, skill):
        assert "next session" in text and "new Hermes session" in text
        assert "gateway" in text and "restart" in text
        assert all(state in text for state in ("ready", "untested", "unavailable", "usable_here"))
    assert "before approval" in skill and "paid" in skill
