"""Open capture is a separate explicit permission, never an unlocked PTT mode."""
import asyncio
import io
import json
import wave

import httpx
from fastapi import FastAPI
from dashboard import plugin_api


def test_open_capture_review_lock_and_revocation(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    mic = plugin_api._onair('host_mic')
    config = {'host_mic': True, 'host_mic_review': True, 'host_mic_open': False}
    monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self, key: config.get(key))
    calls = []
    def forward(method, path, query, body):
        if path == '/state':
            return 200, {'live': True, 'mode': 'approval', 'privacy': False, 'epoch': 7, 'voice_boot': 'a'*32}
        calls.append(json.loads(body))
        return 200, {'ok': True, 'queued': True, 'position': 1}
    monkeypatch.setattr(plugin_api, '_forward', forward)
    monkeypatch.setattr(mic, '_transcribe', lambda _: {'success': True, 'transcript': 'synthetic host'})
    out = io.BytesIO()
    with wave.open(out, 'wb') as f:
        f.setparams((1, 2, 8000, 0, 'NONE', 'not compressed')); f.writeframes(b'\0\0' * 8000)
    app = FastAPI(); app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            url = '/api/plugins/on-air/host/mic'
            headers = {'content-type':'audio/wav', 'x-onair-epoch':'7', 'x-onair-boot':'a'*32, 'x-onair-mode':'send'}
            assert (await c.get(url)).json()['open_enabled'] is False
            refused = (await c.post(url, content=out.getvalue(), headers={**headers, 'x-onair-capture':'open'})).json()
            assert not refused['ok'] and not calls
            config['host_mic_open'] = True
            assert (await c.get(url)).json()['open_enabled'] is True
            review = (await c.post(url, content=out.getvalue(), headers=headers)).json()
            assert review['review'] is True and not calls
            result = (await c.post(url, content=out.getvalue(), headers={**headers, 'x-onair-capture':'open'})).json()
            assert result['queued'] and calls == [{'text':'synthetic host', 'epoch':7, 'boot':'a'*32}]
            for capture in ('bad', 'OPEN', ''):
                assert not (await c.post(url, content=out.getvalue(), headers={**headers, 'x-onair-capture':capture})).json()['ok']
            dup = list(headers.items()) + [('x-onair-capture','open'),('x-onair-capture','open')]
            assert not (await c.post(url, content=out.getvalue(), headers=dup)).json()['ok']
            def revoke(_):
                config['host_mic_open'] = False
                return {'success':True, 'transcript':'too late'}
            monkeypatch.setattr(mic, '_transcribe', revoke)
            result = (await c.post(url, content=out.getvalue(), headers={**headers,'x-onair-capture':'open'})).json()
            assert not result['ok'] and len(calls) == 1
    asyncio.run(run())
