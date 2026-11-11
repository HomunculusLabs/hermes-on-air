"""Desk alert controls use live stage state and enforce the control boundary."""
import asyncio
import json

import pytest

from onair.control import ControlServer
from onair.stage import StageController
from onair.types import SupportEvent


async def request(server, method, path, body=None, *, token=True, origin=None, host=None):
    reader, writer = await asyncio.open_connection('127.0.0.1', server.http.port)
    raw = b'' if body is None else body if isinstance(body, bytes) else json.dumps(body).encode()
    headers = [f'{method} {path} HTTP/1.1', f'Host: {host or "127.0.0.1:" + str(server.http.port)}',
               f'Content-Length: {len(raw)}']
    if token:
        headers.append(f'Authorization: Bearer {server.token}')
    if origin is not None:
        headers.append(f'Origin: {origin}')
    writer.write(('\r\n'.join(headers) + '\r\n\r\n').encode() + raw)
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, data = response.split(b'\r\n\r\n', 1)
    return int(head.split(b' ')[1]), data


def test_alert_routes_live_updates_and_guarded_recent():
    async def run():
        c = StageController(clock=lambda: 1000.0)
        server = ControlServer(c, None)
        await server.start()
        try:
            status, data = await request(server, 'GET', '/alerts')
            assert status == 200
            assert json.loads(data)['alerts']['enabled'] is True
            status, data = await request(server, 'POST', '/alerts', {'min_bits': 50, 'thank_you': False})
            assert status == 200 and json.loads(data)['alerts']['min_bits'] == 50
            assert c.cfg.alerts.min_bits == 50 and c.cfg.alerts.thank_you is False
            c.set_mode('approval')
            event = SupportEvent('twitch', 'chan', 'ev1', 'bits', 'id', 'viewer_42', 1000.0,
                                 amount=100, message='private viewer message')
            assert c.ingest_support(event) == 'collected'
            c._alerts_tick(1003.0)
            status, data = await request(server, 'GET', '/alerts/recent')
            assert status == 200
            alerts = json.loads(data)['alerts']
            assert len(alerts) == 1 and alerts[0]['amount'] == 100
            assert b'private viewer message' not in data
            assert c._thanks == []
        finally:
            await server.stop()
    asyncio.run(run())


@pytest.mark.parametrize('body', [b'{', b'null', b'[]', b'{}', b'{"enabled":true,"min_bits":0}',
                                  b'{"enabled":1}', b'{"min_bits":true}', b'{"min_bits":1.5}',
                                  b'{"during_privacy":"bad"}', b'{"unknown":true}', b'{"enabled":null}'])
def test_invalid_updates_are_atomic(body):
    async def run():
        c = StageController()
        server = ControlServer(c, None)
        await server.start()
        try:
            original = c.cfg
            status, _ = await request(server, 'POST', '/alerts', body)
            assert status == 400 and c.cfg == original
        finally:
            await server.stop()
    asyncio.run(run())


def test_auth_origin_host_methods_and_limit():
    async def run():
        server = ControlServer(StageController(), None)
        await server.start()
        try:
            for path in ('/alerts', '/alerts/recent'):
                assert (await request(server, 'GET', path, token=False))[0] == 401
                assert (await request(server, 'GET', path, origin='http://evil.test'))[0] == 403
                assert (await request(server, 'GET', path, host='evil.test'))[0] == 421
            assert (await request(server, 'POST', '/alerts', {'enabled': False}, token=False))[0] == 401
            assert (await request(server, 'POST', '/alerts', {'enabled': False}, origin='null'))[0] == 403
            assert server.controller.cfg.alerts.enabled is True
            assert (await request(server, 'PUT', '/alerts'))[0] == 405
            assert (await request(server, 'POST', '/alerts/recent', {}))[0] == 405
            assert (await request(server, 'GET', '/alerts/recent?limit=nope'))[0] == 400
        finally:
            await server.stop()
    asyncio.run(run())


def test_recent_limit_and_history_are_bounded_copies():
    from onair.overlay.server import ev_alert

    c = StageController()
    for i in range(205):
        c.hub.publish(ev_alert(str(i), 'sub', 'someone', 0, 1, '', 8000))
    assert len(c.recent_alerts(500)) == 200
    recent = c.recent_alerts(2)
    assert [a['id'] for a in recent] == ['203', '204']
    recent[0]['name'] = 'changed'
    assert c.recent_alerts(2)[0]['name'] == 'someone'
