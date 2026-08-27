from pathlib import Path

from dg.cli import command_names

SKILL = Path(__file__).resolve().parent.parent / "SKILL.md"


def test_skill_doc_covers_every_command():
    text = SKILL.read_text()
    missing = [c for c in command_names() if c not in text]
    assert missing == [], f"SKILL.md does not document: {missing}"


def test_skill_doc_is_ingestible():
    text = SKILL.read_text()
    assert text.startswith("---")
    assert "name: dg" in text and "description:" in text
    # protocol must teach the core loop explicitly
    for token in ("dg next --json", "dg start", "dg done", "--note", "dg validate"):
        assert token in text, f"SKILL.md missing core ritual element: {token}"
