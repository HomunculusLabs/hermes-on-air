import asyncio
import http.client
import threading
from pathlib import Path

import pytest

from onair.overlay.server import CSP, EventHub, OverlayServer


@pytest.fixture
def scene_server(tmp_path):
    scene = tmp_path / 'scene'
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    server = OverlayServer(EventHub(), lambda _: None, port=0, scene_dir=scene)
    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.start())
        ready.set()
        loop.run_forever()
        loop.run_until_complete(server.stop())
        loop.close()
    thread = threading.Thread(target=run)
    thread.start()
    assert ready.wait(5)
    yield server, scene
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)


def request(server, path, method='GET'):
    conn = http.client.HTTPConnection('127.0.0.1', server.port, timeout=5)
    conn.request(method, path)
    response = conn.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    conn.close()
    return result


def test_scene_security_and_types(scene_server, tmp_path):
    server, scene = scene_server
    assert request(server, '/scene/')[0] == 404
    assert request(server, '/scene')[0] == 404
    scene.mkdir()
    (scene / 'index.html').write_text('room')
    (scene / 'mesh.vrm').write_bytes(b'glTF')
    (scene / '.hidden.js').write_text('secret')
    (scene / 'bad.txt').write_text('secret')
    (tmp_path / 'outside.js').write_text('secret')
    (scene / 'link.js').symlink_to(tmp_path / 'outside.js')
    (scene / 'dirlink').symlink_to(tmp_path, target_is_directory=True)
    for suffix, ctype in {'.gltf': 'model/gltf+json', '.glb': 'model/gltf-binary', '.vrm': 'model/gltf-binary',
                          '.bin': 'application/octet-stream', '.wasm': 'application/wasm', '.ktx2': 'image/ktx2',
                          '.hdr': 'image/vnd.radiance', '.mjs': 'text/javascript; charset=utf-8',
                          '.jpg': 'image/jpeg', '.webp': 'image/webp', '.json': 'application/json'}.items():
        (scene / ('asset' + suffix)).write_bytes(b'data')
        assert request(server, '/scene/asset' + suffix)[1]['Content-Type'] == ctype
    status, headers, body = request(server, '/scene/')
    assert (status, body) == (200, b'room')
    assert "worker-src 'self' blob:" in headers['Content-Security-Policy']
    assert request(server, '/overlay')[1]['Content-Security-Policy'] == CSP
    assert request(server, '/scene/mesh.vrm')[1]['Content-Type'] == 'model/gltf-binary'
    for path in ['/scene/%2e%2e/outside.js', '/scene/.hidden.js', '/scene/bad.txt', '/scene/link.js',
                 '/scene/dirlink/outside.js', '/scene/%2fetc/passwd']:
        assert request(server, path)[0] == 404, path
    assert request(server, '/scene/', 'HEAD')[2] == b''


def test_large_scene_file_is_streamed(scene_server, monkeypatch):
    server, scene = scene_server
    scene.mkdir()
    (scene / 'index.html').write_text('room')
    data = b'x' * (4 * 1024 * 1024)
    (scene / 'model.glb').write_bytes(data)
    def no_read_bytes(self):
        raise AssertionError('must stream')
    monkeypatch.setattr(Path, 'read_bytes', no_read_bytes)
    status, headers, body = request(server, '/scene/model.glb')
    assert status == 200 and int(headers['Content-Length']) == len(data) and body == data


@pytest.mark.parametrize('component', ['plugin-data', 'on-air', 'scene', 'nested', 'file'])
def test_scene_refuses_symlinks_at_every_level(tmp_path, monkeypatch, component):
    home = tmp_path / 'home'
    scene = home / 'plugin-data' / 'on-air' / 'scene'
    (scene / 'nested').mkdir(parents=True)
    (scene / 'index.html').write_text('room')
    (scene / 'nested' / 'secret.json').write_text('secret')
    target = {'plugin-data': home / 'plugin-data', 'on-air': home / 'plugin-data' / 'on-air',
              'scene': scene, 'nested': scene / 'nested', 'file': scene / 'nested' / 'secret.json'}[component]
    outside = tmp_path / 'outside'
    target.rename(outside)
    target.symlink_to(outside, target_is_directory=component != 'file')
    monkeypatch.setenv('HERMES_HOME', str(home))
    server = OverlayServer(EventHub(), lambda _: None, port=0)
    from onair.overlay.server import Request
    async def get(path):
        return await server._handle(Request('GET', path, {}, {}))
    assert asyncio.run(get('/scene/nested/secret.json')).status == 404
    if component in ('plugin-data', 'on-air', 'scene'):
        assert asyncio.run(get('/scene/')).status == 404
        assert asyncio.run(get('/scene/avatar-status.json')).status == 404


def test_portable_scene_walk_refuses_symlinks(tmp_path, monkeypatch):
    from onair import scene_paths
    root = tmp_path / 'home'
    root.mkdir()
    (root / 'linked').symlink_to(tmp_path, target_is_directory=True)
    (tmp_path / 'file.json').write_text('secret')
    monkeypatch.setattr(scene_paths.os, 'supports_dir_fd', set())
    with pytest.raises(OSError):
        scene_paths.open_scene_file(root, ('linked', 'file.json'))
    (root / 'normal.json').write_text('safe')
    with scene_paths.open_scene_file(root, ('normal.json',)) as stream:
        assert stream.read() == b'safe'
