"""Opt-in Chromium test; only a synthesized fake capture device is used."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

from dashboard import plugin_api

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.environ.get('ONAIR_HOST_MIC_BROWSER') != '1', reason='opt-in fake audio browser test')


def test_fake_audio_browser_end_to_end(tmp_path, monkeypatch):
    import uvicorn
    assert shutil.which('espeak-ng') and shutil.which('ffprobe')
    assert os.environ.get('ONAIR_PLAYWRIGHT') and os.environ.get('DESK_TEST_CHROMIUM')
    home = tmp_path / 'home'; home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    clip = tmp_path / 'synthetic.wav'
    subprocess.run(['espeak-ng', '-w', str(clip), 'Hello from the synthetic host.'], check=True)
    mic = plugin_api._onair('host_mic')
    def transcribe(_path):
        time.sleep(1.5)
        return {'success':True, 'transcript':'Hello from the synthetic host.'}
    monkeypatch.setattr(mic, '_transcribe', transcribe)
    settings = {'host_mic':True, 'host_mic_review':True, 'epoch':7, 'airing':None}
    cuts = []
    monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self,key: settings.get(key))
    calls = []
    def forward(method, path, query, body):
        import json
        if path == '/state':
            return 200, {'mode':'approval','privacy':False,'muted':False,'epoch':settings['epoch'],'voice_boot':'a'*32,'seq':1,'live':True,'airing':settings['airing'],'host_turns':{'enabled':True}}
        if path == '/cut-in':
            cut = json.loads(body)
            if cut != {'epoch':settings['epoch'], 'boot':'a'*32}:
                return 409, {'ok':False,'message':'Stale cut.'}
            cuts.append(cut)
            settings['epoch'] += 1
            settings['airing'] = None
            return 200, {'ok':True, 'state':forward('GET', '/state', '', b'')[1]}
        if path == '/pending': return 200, {'epoch':settings['epoch'],'pending':[]}
        if path == '/host/turn':
            calls.append(json.loads(body))
            return (409, {'ok':False,'reason':'stale_epoch','message':'The show changed.'}) if not settings['host_mic_review'] or calls[-1]['epoch'] != settings['epoch'] else (200, {'ok':True,'queued':True,'id':'demo','position':1})
        if path == '/receipts': return 200, {'receipts':[]}
        if path == '/alerts/recent': return 200, {'alerts':[]}
        if path == '/alerts': return 200, {'alerts':{}}
        return 200, {}
    monkeypatch.setattr(plugin_api, '_forward', forward)
    app = FastAPI(); app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    @app.get('/')
    def page():
        return HTMLResponse('<!doctype html><html><head><link rel="stylesheet" href="/dashboard-plugins/on-air/dist/style.css"></head><body><div id="root"></div><script src="/sdk.js"></script><script src="/dashboard-plugins/on-air/dist/index.js"></script></body></html>')
    @app.get('/sdk.js')
    def sdk(): return FileResponse(tmp_path / 'sdk.js', media_type='text/javascript')
    @app.get('/dashboard-plugins/on-air/dist/{name}')
    def asset(name: str):
        assert name in ('index.js','live.js','live.css','host-mic.js','style.css')
        return FileResponse(ROOT / 'dashboard/dist' / name)
    @app.post('/fixture/auto')
    def auto(): settings['host_mic_review'] = False; return {'ok':True}
    @app.post('/fixture/cut')
    def cut(): settings['epoch'] += 1; return {'ok':True}
    @app.post('/fixture/airing')
    def airing(): settings['airing'] = 'line'; return {'ok':True}
    @app.get('/fixture/cuts')
    def cut_report(): return cuts
    @app.get('/fixture/calls')
    def report(): return calls
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True); thread.start()
    try:
        for _ in range(100):
            if server.started: break
            time.sleep(.05)
        assert server.started
        env = dict(os.environ, ONAIR_MIC_URL=f'http://127.0.0.1:{port}/', ONAIR_MIC_WAV=str(clip), ONAIR_MIC_SDK=str(tmp_path / 'sdk.js'))
        result = subprocess.run(['node', str(ROOT / 'tests/desk/host_mic_browser.cjs')], cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        import re
        assert len(calls) == 3
        assert re.fullmatch(r'[0-9a-f]{32}', calls[0]['id'])
        assert re.fullmatch(r'[0-9a-f]{32}', calls[1]['id'])
        assert calls[0]['id'] != calls[1]['id']
        assert calls == [{'text':'Hello from the synthetic host.','epoch':7,'boot':'a'*32,'id':calls[0]['id']},
                         {'text':'Hello from the synthetic host.','epoch':8,'boot':'a'*32,'id':calls[1]['id']},
                         {'text':'Hello from the synthetic host.','epoch':9,'boot':'a'*32}]
        assert cuts == [{'epoch':7, 'boot':'a'*32}]
    finally:
        server.should_exit = True; thread.join(10); sock.close()
