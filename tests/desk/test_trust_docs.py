from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / 'docs'


def test_host_route_and_scene_documentation_match_product():
    host = (DOCS / 'host-turns.md').read_text()
    desk = (DOCS / 'desk.md').read_text()
    scene = (DOCS / 'scene.md').read_text()
    assert 'Host line' in host and 'authenticated dashboard' in host
    assert 'dashboard has no host input box' not in desk
    assert 'Before any state arrives, the starter is visible' not in scene
    assert 'valid non-private off-air' in scene


def test_open_mic_documentation_states_owner_and_privacy_boundaries():
    talk = (DOCS / 'talk.md').read_text()
    safety = (DOCS / 'safety.md').read_text()
    skill = (DOCS.parent / 'onair/skills/host/SKILL.md').read_text()
    for term in ('host_mic_open', 'Mic open: listening', 'cloud', 'wake word', 'headphones', 'approval mode', 'autonomous mode'):
        assert term.lower() in talk.lower(), term
    for term in ('open mic', 'owner', 'host text', 'speech-to-text', 'privacy'):
        assert term.lower() in safety.lower(), term
    for term in ('host_mic_open', 'owner', 'never', 'microphone', 'cloud', 'approval'):
        assert term.lower() in skill.lower(), term
