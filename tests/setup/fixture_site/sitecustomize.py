"""DISCLOSED TEST FIXTURE for the external network edges of a REAL stage process (``hermes gateway run``).

Put on PYTHONPATH only by tests/setup/test_stage_runtime.py; never shipped or loaded by the product. It changes
nothing in Hermes, the plugin, preflight or the guard. Active only when ``<HERMES_HOME>/../onair-fixture.json``
exists (written by that test next to the stage folder it created). It then:

- refuses every non-loopback TCP connection (no genuine Twitch, provider or other network);
- answers the anonymous Twitch IRC connection with a loopback IRC fixture: the real ``TwitchChatSource`` asks
  ``asyncio.open_connection("irc.chat.twitch.tv", 6697, ssl=<verified context>)``; exactly that call is
  redirected to the fixture's loopback port without TLS (a loopback fixture cannot hold Twitch's certificate);
- records the NAMES of the environment variables this process received (plus whether a planted owner secret
  value is present anywhere), so the test can prove the stage did not inherit the owner's environment.

The model edge needs no code here: the stage's own config names a loopback OpenAI-compatible fixture URL.
"""
import asyncio
import json
import os
import socket
from pathlib import Path

_HOME = os.environ.get("HERMES_HOME") or ""
_CFG_PATH = Path(_HOME).parent / "onair-fixture.json" if _HOME else None
_CFG = {}
if _CFG_PATH is not None and _CFG_PATH.is_file():
    _CFG = json.loads(_CFG_PATH.read_text())

if _CFG:
    _real_connect = socket.socket.connect

    def _loopback_only(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            host = address[0] if isinstance(address, tuple) else address
            if host not in ("127.0.0.1", "::1", "localhost"):
                raise OSError("fixture: non-loopback connections are disabled (%s)" % (host,))
        return _real_connect(self, address)

    socket.socket.connect = _loopback_only
    _real_open = asyncio.open_connection

    async def _fixture_open(host=None, port=None, **kw):
        if host == "irc.chat.twitch.tv":
            kw.pop("ssl", None)
            kw.pop("server_hostname", None)
            return await _real_open("127.0.0.1", int(_CFG["irc_port"]), **kw)
        return await _real_open(host, port, **kw)

    asyncio.open_connection = _fixture_open
    _planted = _CFG.get("planted_secret") or "\0"
    with open(Path(_HOME).parent / ("onair-fixture-env.%d.json" % os.getpid()), "w") as fh:
        json.dump({"names": sorted(os.environ), "planted_secret_present": any(_planted in v for v in os.environ.values()),
                   "home_inside_stage": os.path.realpath(os.environ.get("HOME", "/")).startswith(
                       os.path.realpath(_HOME) + os.sep)}, fh)
