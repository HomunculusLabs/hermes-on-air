"""Build tokens bind each served page to its own reconnect snapshots."""
import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from onair.overlay.server import EventHub, OverlayServer, Request, ev_state
from onair.presentation.deck import Program
from onair.presentation import surface as ps

ROOT = Path(__file__).resolve().parents[2]


def req(path):
    return Request('GET', path, {}, {})


def html_token(response):
    match = re.search(rb'data-build="([0-9a-f]{12})"', response.body)
    assert match
    return match.group(1).decode()


def test_build_reconnect_node():
    node = shutil.which('node')
    modules = Path(os.environ.get('ONAIR_HERMES_SDK', str(Path.home() / '.hermes/hermes-agent'))) / 'node_modules'
    if not node or not modules.is_dir():
        pytest.skip('node and dashboard dependencies are not available')
    result = subprocess.run([node, str(Path(__file__).with_name('test_build_reconnect.cjs'))], cwd=ROOT,
                            env={**os.environ, 'NODE_PATH': str(modules)}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_presentation_token_matches_page_state_and_sse_and_changes_at_restart(tmp_path, monkeypatch):
    web = tmp_path / 'web'
    shutil.copytree(ROOT / 'onair/presentation/web', web)
    monkeypatch.setattr(ps, 'WEB', web)

    async def run():
        surface = ps.PresentationSurface(Program())
        await surface.start()
        try:
            page = await surface._handle(req('/present'))
            token = html_token(page)
            snap = json.loads((await surface._handle(req('/present/state'))).body)
            frame = json.loads(surface._frame()[1][6:])
            assert token == snap['build'] == frame['build']
            original = (await surface._handle(req('/present/app.js'))).body
            (web / 'app.js').write_text('changed')
            assert (await surface._handle(req('/present/app.js'))).body == original
            assert surface.snapshot()['build'] == token
        finally:
            await surface.stop()
        replacement = ps.PresentationSurface(Program())
        await replacement.start()
        try:
            assert replacement.snapshot()['build'] != token
        finally:
            await replacement.stop()
    asyncio.run(run())


def test_overlay_token_is_in_reconnect_state_not_other_events(tmp_path):
    web = tmp_path / 'web'
    shutil.copytree(ROOT / 'onair/overlay/web', web)

    async def run():
        hub = EventHub()
        server = OverlayServer(hub, lambda _: None, port=0, web_dir=web)
        await server.start()
        try:
            token = html_token(await server._handle(req('/overlay')))
            reader, writer = await asyncio.open_connection('127.0.0.1', server.port)
            writer.write(f'GET /events HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n\r\n'.encode())
            await writer.drain()
            await reader.readuntil(b'\r\n\r\n')
            wire_frame = await reader.readuntil(b'\n\n')
            wire_state = json.loads(next(line[6:] for line in wire_frame.splitlines() if line.startswith(b'data: ')))
            assert wire_state['build'] == token and wire_state['type'] == 'state'
            writer.close()
            await writer.wait_closed()
            stream = await server._handle(req('/events'))
            q = stream.subscribe()
            try:
                kind, frame = await q.get()
                payload = stream.transform(kind, frame)
                state = json.loads(next(line[6:] for line in payload.splitlines() if line.startswith('data: ')))
                assert state['type'] == 'state' and state['build'] == token
                assert stream.transform('caption', 'sample') == 'sample'
            finally:
                stream.unsubscribe(q)
            original = (await server._handle(req('/overlay/overlay.js'))).body
            (web / 'overlay.js').write_text('changed')
            assert (await server._handle(req('/overlay/overlay.js'))).body == original
        finally:
            await server.stop()
        replacement = OverlayServer(EventHub(), lambda _: None, port=0, web_dir=web)
        await replacement.start()
        try:
            assert html_token(await replacement._handle(req('/overlay'))) != token
        finally:
            await replacement.stop()
    asyncio.run(run())
