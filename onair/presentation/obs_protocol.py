"""Minimal OBS WebSocket v5 client; error messages never contain server data.

Provenance: ported from the earlier On Air OBS lane, file ``onair/obs/protocol.py`` at commit
8dba56927c3bbe6fb9abea97d3c6607c621bb052 (sha256 88acd56f8adf230b1c2061203a1007e55f27c9f9a71ded97fc2dfa27b8646ceb),
MIT License, Copyright (c) 2026 HomunculusLabs (see LICENSE). Only this generic protocol client is reused;
the old privacy/microphone bridge (``onair/obs/bridge.py``) is NOT imported or ported.

Port changes (all additive): ``event_subscriptions`` is a constructor argument (default 0: this controller
reads state by request and does not depend on events); a failed request raises ``ObsRequestError`` carrying
only the numeric v5 ``requestStatus.code`` (never the server's comment text); the request timeout is a
constructor argument (default 3 s, unchanged).
"""
import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import socket
from urllib.parse import urlsplit
from uuid import uuid4


def validate_url(url, allow_remote=False):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme not in ('ws', 'wss') or not host or parsed.username is not None or parsed.password is not None:
            raise ValueError
        if parsed.fragment or parsed.query:
            raise ValueError
        parsed.port  # validate port
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
            if host.lower() == 'localhost':
                try:
                    addresses = socket.getaddrinfo(host, parsed.port or 4455, type=socket.SOCK_STREAM)
                    local = bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_loopback for item in addresses)
                except (OSError, ValueError):
                    local = False
        if not allow_remote and not local:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('invalid or disallowed OBS URL') from None
    return url


def _digest(text):
    return base64.b64encode(hashlib.sha256(text.encode('utf-8')).digest()).decode('ascii')


class ObsError(Exception):
    pass


class ObsAuthError(ObsError):
    pass


class ObsRequestError(ObsError):
    """OBS answered a request with a failure status. ``code`` is the numeric v5 RequestStatus only."""

    def __init__(self, code):
        super().__init__(f'OBS request failed (code {code})')
        self.code = code


class Client:
    def __init__(self, url, password='', allow_remote=False, event_subscriptions=0, request_timeout=3):
        self.url = validate_url(url, allow_remote)
        self._password = password or ''
        self._events = int(event_subscriptions)
        self._timeout = float(request_timeout)
        self.ws = None

    async def __aenter__(self):
        from websockets.asyncio.client import connect
        quiet = logging.Logger('onair.obs.transport', logging.CRITICAL + 1)
        quiet.addHandler(logging.NullHandler())
        try:
            self.ws = await connect(self.url, open_timeout=3, close_timeout=1, max_size=1048576, logger=quiet)
            hello = json.loads(await asyncio.wait_for(self.ws.recv(), 3))
            if hello.get('op') != 0:
                raise ObsError('unexpected hello')
            identify = {'rpcVersion': 1, 'eventSubscriptions': self._events}
            auth = hello.get('d', {}).get('authentication')
            if auth is not None:
                identify['authentication'] = _digest(_digest(self._password + auth['salt']) + auth['challenge'])
            await self.ws.send(json.dumps({'op': 1, 'd': identify}))
            try:
                reply = json.loads(await asyncio.wait_for(self.ws.recv(), 3))
            except Exception as exc:
                if auth is not None and getattr(getattr(exc, 'rcvd', None), 'code', None) == 4009:
                    raise ObsAuthError('OBS authentication failed') from None
                raise ObsError(f'{type(exc).__name__}: OBS identify failed') from None
            if reply.get('op') != 2:
                raise ObsAuthError('OBS authentication failed') if auth is not None else ObsError('OBS identify failed')
            return self
        except asyncio.CancelledError:
            if self.ws is not None:
                await self.ws.close()
            raise
        except Exception as exc:
            if self.ws is not None:
                try:
                    await self.ws.close()
                except Exception:
                    pass
            if isinstance(exc, ObsAuthError):
                raise ObsAuthError('OBS authentication failed') from None
            raise ObsError(f'{type(exc).__name__}: OBS transport failed') from None

    async def __aexit__(self, *args):
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception as exc:
                raise ObsError(f'{type(exc).__name__}: OBS close failed') from None

    async def request(self, kind, data=None):
        async def exchange():
            ident = uuid4().hex
            payload = {'requestType': kind, 'requestId': ident, 'requestData': data or {}}
            await self.ws.send(json.dumps({'op': 6, 'd': payload}))
            while True:
                msg = json.loads(await self.ws.recv())
                if msg.get('op') != 7 or msg.get('d', {}).get('requestId') != ident:
                    continue
                response = msg['d']
                status = response.get('requestStatus', {})
                if status.get('result') is not True:
                    code = status.get('code')
                    raise ObsRequestError(code if isinstance(code, int) else None)
                return response.get('responseData') or {}
        try:
            return await asyncio.wait_for(exchange(), self._timeout)
        except ObsRequestError as exc:
            raise ObsRequestError(exc.code) from None
        except Exception as exc:
            raise ObsError(f'{type(exc).__name__}: OBS request failed') from None
