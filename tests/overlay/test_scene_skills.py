import asyncio
import importlib
import io
import os
import select
import subprocess
import sys
import urllib.request
import wave
from pathlib import Path

import pytest

from onair import cli, scene_preview
from onair.setup_agent import SKILLS


def test_all_scene_skills_registered_and_packaged():
    found = {name: (path, description) for name, path, description in SKILLS}
    for name in ('scene', 'scene-build', 'avatar'):
        path, description = found[name]
        assert path.is_file() and path.name == 'SKILL.md'
        assert description.startswith('Use when ') and len(description) <= 60
        assert f'description: {description}' in path.read_text()
    starter = Path(importlib.import_module('onair').__file__).parent / 'scene_starter'
    for name in ('room.js', 'character.js', 'lighting.js', 'events.js'):
        assert (starter / name).is_file()


def test_scene_preview_parser():
    import argparse
    parser = argparse.ArgumentParser()
    cli.setup_parser(parser)
    args = parser.parse_args(['scene', 'preview'])
    assert args.scene_action == 'preview'


@pytest.mark.skipif(os.name == 'nt', reason='Pipe readiness check uses POSIX select')
def test_scene_preview_cli_process(tmp_path):
    directory = tmp_path / 'plugin-data' / 'on-air' / 'scene'
    directory.mkdir(parents=True)
    (directory / 'index.html').write_text('private preview', encoding='utf-8')
    code = ("import argparse; from onair.cli import setup_parser, handle; "
            "p=argparse.ArgumentParser(); setup_parser(p); handle(p.parse_args(['scene','preview']))")
    env = dict(os.environ, HERMES_HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE='1')
    process = subprocess.Popen([sys.executable, '-B', '-c', code], env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding='utf-8')
    try:
        ready, _, _ = select.select([process.stdout], [], [], 10)
        assert ready, 'preview did not print a URL'
        url = process.stdout.readline().strip().removeprefix('Private synthetic preview: ')
        assert url.startswith('http://127.0.0.1:') and url.endswith('/scene/')
        with urllib.request.urlopen(url, timeout=5) as response:
            assert response.read() == b'private preview'
    finally:
        process.terminate()
        process.communicate(timeout=10)


def test_preview_serves_synthetic_events(tmp_path):
    (tmp_path / 'index.html').write_text('preview')
    async def exercise():
        preview = scene_preview.ScenePreview(tmp_path, interval=.05)
        await preview.start()
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', preview.server.port)
            writer.write(f'GET /events HTTP/1.1\r\nHost: 127.0.0.1:{preview.server.port}\r\n\r\n'.encode())
            await writer.drain()
            data = b''
            while b'"type":"caption"' not in data or b'caption_clear' not in data.split(b'"type":"caption"')[-1]:
                data += await asyncio.wait_for(reader.read(4096), 3)
            assert b'"type":"state"' in data
            assert b'"type":"audio"' in data and b'/audio/preview.wav' in data
            with wave.open(io.BytesIO(preview.clip), 'rb') as wav:
                assert wav.getnframes() > 0 and any(wav.readframes(wav.getnframes()))
            writer.close()
            await writer.wait_closed()
        finally:
            await preview.stop()
    asyncio.run(exercise())


def test_close_event_source_neutralizes_character(tmp_path):
    import shutil
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    source = (Path(__file__).parents[2] / 'onair/scene_starter/events.js').read_text()
    module = tmp_path / 'events.mjs'
    module.write_text(source.replace("from './avatar_logic.js'", "from './avatar_logic.mjs'"))
    (tmp_path / 'avatar_logic.mjs').write_text(
        (Path(__file__).parents[2] / 'onair/scene_starter/avatar_logic.js').read_text())
    script = """
import {connectEvents} from './events.mjs';
globalThis.document = {body:{dataset:{}}};
globalThis.EventSource = class { close() { this.closed = true; } };
const character = {setSpeaking(v) { this.speaking = v; }};
const e = connectEvents(character);
e.onmessage({data: JSON.stringify({type:'state', live:true, mode:'approval', privacy:false, muted:false})});
e.onmessage({data: JSON.stringify({type:'caption', id:'x', ttl_ms:10000})});
if (!character.speaking) throw Error('not speaking');
e.close();
if (character.speaking || document.body.dataset.speaking !== 'false' || !e.closed) throw Error('not neutral');
"""
    result = subprocess.run([node, '--input-type=module', '-e', script], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_preview_refuses_symlinked_scene_parent(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    outside = tmp_path / 'outside'
    scene = outside / 'on-air' / 'scene'
    scene.mkdir(parents=True)
    (scene / 'index.html').write_text('outside')
    (home / 'plugin-data').symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv('HERMES_HOME', str(home))
    preview = scene_preview.ScenePreview(home / 'plugin-data' / 'on-air' / 'scene')
    with pytest.raises(ValueError, match='No scene found'):
        asyncio.run(preview.start())
