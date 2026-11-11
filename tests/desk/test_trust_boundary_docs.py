from pathlib import Path


def test_same_user_terminal_boundary_is_explicit():
    docs = Path(__file__).resolve().parents[2] / 'docs'
    for name in ('host-turns.md', 'desk.md'):
        text = (docs / name).read_text()
        assert '## Trust boundary' in text
        assert 'unrestricted terminal' in text
        assert 'same user' in text
        assert 'approval mode' in text
