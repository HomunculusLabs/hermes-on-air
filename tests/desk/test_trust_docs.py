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
