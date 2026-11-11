import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_manifest_registers_plugin_assets():
    manifest = json.loads((ROOT / 'dashboard/manifest.json').read_text())
    assert manifest['name'] == 'on-air'
    assert manifest['api'] == 'plugin_api.py'
    assert (ROOT / 'dashboard' / manifest['entry']).is_file()
    assert (ROOT / 'dashboard' / manifest['css']).is_file()


def test_dashboard_uses_authenticated_proxy_and_epoch_guards():
    source = (ROOT / 'dashboard/dist/index.js').read_text()
    for token in ('__HERMES_PLUGINS__', 'fetchJSON', '/api/plugins/on-air/',
                  'line_id', 'epoch', 'pending', 'receipts', 'privacy', 'mute', 'stop'):
        assert token in source
    assert 'dangerouslySetInnerHTML' not in source
    for forbidden in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function'):
        assert forbidden not in source, forbidden
