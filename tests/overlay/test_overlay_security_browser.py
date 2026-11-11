"""Overlay contract tests against the real loopback server and, when installed, Chromium.

Run browser coverage with pytest and the Python Playwright package installed, plus a
Playwright Chromium browser. No Twitch, provider, desk, or external network is needed.
"""

import asyncio
import os
import re
import threading
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import urlopen

import pytest

from onair.overlay.server import (
    EventHub, OverlayServer, ev_alert, ev_audio, ev_caption, ev_caption_clear,
    ev_chat, ev_state,
)

WEB = Path(__file__).resolve().parents[2] / "onair" / "overlay" / "web"


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inline = []
        self.handlers = []
        self._script = False
        self._external = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.handlers.extend(k for k in attrs if k.lower().startswith("on"))
        if tag == "script":
            self._script = True
            self._external = bool(attrs.get("src"))

    def handle_data(self, data):
        if self._script and not self._external and data.strip():
            self.inline.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self._script = False


@pytest.fixture
def server():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    async def start():
        hub = EventHub()
        hub.snapshot = lambda: ev_state(True, "autonomous", False, False)
        server = OverlayServer(hub, lambda _id: None, port=0)
        await server.start()
        return hub, server

    hub, instance = asyncio.run_coroutine_threadsafe(start(), loop).result(5)

    def call(fn):
        async def invoke():
            return fn()
        return asyncio.run_coroutine_threadsafe(invoke(), loop).result(5)

    yield instance, hub, call
    asyncio.run_coroutine_threadsafe(instance.stop(), loop).result(5)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
    loop.close()


def test_http_csp_and_static_security(server):
    instance, _, _ = server
    with urlopen(instance.overlay_url, timeout=5) as response:
        assert response.status == 200
        csp = response.headers["Content-Security-Policy"]
        directives = {p[0]: set(p[1:]) for d in csp.split(";") if (p := d.split())}
        assert directives["default-src"] == {"'none'"}
        for directive in ("script-src", "style-src", "connect-src", "media-src"):
            assert directives[directive] == {"'self'"}
        for directive in ("base-uri", "form-action", "frame-ancestors"):
            assert directives[directive] == {"'none'"}
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        page = response.read().decode()
    parsed = _Scripts()
    parsed.feed(page)
    assert not parsed.inline
    assert not parsed.handlers


def test_web_sources_do_not_use_html_injection_sinks():
    files = list(WEB.rglob("*.js")) + list(WEB.rglob("*.mjs"))
    assert files, "overlay JavaScript missing"
    forbidden = re.compile(r"\b(?:innerHTML|outerHTML|insertAdjacentHTML|document\.write)\b")
    for file in files:
        assert not forbidden.search(file.read_text(encoding="utf-8")), file.name


@pytest.fixture
def page(server):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True, executable_path=os.environ.get("OVERLAY_TEST_CHROMIUM") or None
            )
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Playwright Chromium is not installed")
            raise
        context = browser.new_context()
        page = context.new_page()
        # Observe real media calls without relying on audio devices or autoplay permission.
        page.add_init_script("""(() => {
            window.__media = {plays: 0, pauses: 0};
            HTMLMediaElement.prototype.play = function () { window.__media.plays++; return Promise.resolve(); };
            HTMLMediaElement.prototype.pause = function () { window.__media.pauses++; };
        })();""")
        page.goto(server[0].overlay_url, wait_until="load")
        page.wait_for_timeout(100)
        # The next event must have a subscriber; don't race EventSource startup.
        for _ in range(100):
            if server[2](lambda: server[1].subscriber_count()) >= 1:
                break
            page.wait_for_timeout(20)
        else:
            pytest.fail("overlay did not connect to /events")
        yield page
        context.close()
        browser.close()


def _send(server, event, urgent=False):
    return server[2](lambda: server[1].publish(event, urgent=urgent))


def test_sse_renders_text_not_markup_and_clears_caption(server, page):
    payload = '<img src=x onerror="window.injected=1">'
    _send(server, ev_caption("c1", "speaker", payload, 30000))
    page.get_by_text(payload, exact=True).wait_for(state="visible")
    assert page.locator("img[src=x]").count() == 0
    assert page.evaluate("window.injected") is None
    _send(server, ev_chat("m1", "viewer_42", "a chat message"))
    page.get_by_text("a chat message", exact=True).wait_for(state="visible")
    _send(server, ev_alert("a1", "bits", "viewer_42", 50, 0, "", 30000))
    page.get_by_text(re.compile(r"viewer_42.*50")).wait_for(state="visible")
    _send(server, ev_caption_clear())
    page.get_by_text(payload, exact=True).wait_for(state="hidden")


def test_privacy_cut_clears_surfaces_and_stops_audio(server, page):
    _send(server, ev_caption("c1", "speaker", "caption before cut", 30000))
    _send(server, ev_chat("m1", "viewer_42", "chat before cut"))
    _send(server, ev_alert("a1", "sub", "viewer_42", 0, 1, "", 30000))
    _send(server, ev_audio("clip1", "/audio/clip1.wav", "c1"))
    page.get_by_text("caption before cut", exact=True).wait_for(state="visible")
    page.get_by_text("chat before cut", exact=True).wait_for(state="visible")
    page.get_by_text(re.compile("viewer_42.*subscribed")).wait_for(state="visible")
    page.wait_for_function("window.__media.plays > 0")
    _send(server, ev_state(True, "autonomous", True, False), urgent=True)
    page.get_by_text("caption before cut", exact=True).wait_for(state="hidden")
    assert page.locator("#cover").is_visible()
    assert not page.get_by_text("chat before cut", exact=True).is_visible()
    assert not page.get_by_text(re.compile("viewer_42.*subscribed")).is_visible()
    assert page.evaluate("window.__media.pauses") > 0
    _send(server, ev_caption("c2", "speaker", "during privacy", 30000))
    _send(server, ev_chat("m2", "viewer_42", "hidden chat"))
    _send(server, ev_alert("a2", "bits", "viewer_42", 20, 0, "", 30000))
    _send(server, ev_audio("clip2", "/audio/clip2.wav", "c2"))
    _send(server, ev_state(True, "autonomous", False, False))
    # Ordering barrier: this caption is handled after all privacy events.
    _send(server, ev_caption("c3", "speaker", "after privacy", 30000))
    page.get_by_text("after privacy", exact=True).wait_for(state="visible")
    assert not page.get_by_text("during privacy", exact=True).is_visible()
    assert not page.get_by_text("hidden chat", exact=True).is_visible()
    assert not page.get_by_text(re.compile("viewer_42.*20")).is_visible()
    assert page.evaluate("window.__media.plays") == 1


def test_reconnection_receives_state_without_replay(server, page):
    _send(server, ev_caption("old", "speaker", "old caption", 30000))
    page.get_by_text("old caption", exact=True).wait_for(state="visible")
    server[2](server[1].close_all)
    # EventSource automatically reconnects, without replaying historical content.
    for _ in range(250):
        if server[2](lambda: server[1].subscriber_count()) >= 1:
            break
        page.wait_for_timeout(20)
    else:
        pytest.fail("EventSource did not reconnect")
    fresh = page.context.new_page()
    fresh.goto(server[0].overlay_url, wait_until="load")
    fresh.wait_for_timeout(300)
    assert not fresh.get_by_text("old caption", exact=True).is_visible()
    _send(server, ev_caption("new", "speaker", "new caption", 30000))
    fresh.get_by_text("new caption", exact=True).wait_for(state="visible")
    page.get_by_text("new caption", exact=True).wait_for(state="visible")
    fresh.close()


@pytest.mark.parametrize('change', [
    {'privacy': None}, {'privacy': 'false'}, {'live': 1}, {'muted': None},
    {'mode': 'unknown'}, {'live': False},
])
def test_malformed_snapshot_covers_until_fresh_state(server, page, change):
    _send(server, ev_caption('before', 'host', 'before invalid state', 30000))
    page.get_by_text('before invalid state', exact=True).wait_for(state='visible')
    event = {'type': 'state', 'live': True, 'mode': 'approval', 'privacy': False, 'muted': False}
    event.update(change)
    if change == {'privacy': None}:
        del event['privacy']
    def publish_malformed():
        hub = server[1]
        hub._seq += 1
        event['seq'] = hub._seq
        frame = hub._frame(event)
        for queue in list(hub._subs):
            queue.put_nowait(('state', frame))
    server[2](publish_malformed)
    page.locator('#cover').wait_for(state='visible')
    assert not page.get_by_text('before invalid state', exact=True).is_visible()
    _send(server, ev_caption('blocked', 'host', 'blocked caption', 30000))
    _send(server, ev_state(True, 'approval', False, False))
    _send(server, ev_caption('after', 'host', 'fresh caption', 30000))
    page.get_by_text('fresh caption', exact=True).wait_for(state='visible')
    assert not page.get_by_text('blocked caption', exact=True).is_visible()


def test_alerts_queue_in_order(server, page):
    _send(server, ev_alert("one", "bits", "viewer_42", 100, 0, "", 700))
    _send(server, ev_alert("two", "raid", "viewer_42", 34, 0, "", 700))
    page.get_by_text(re.compile(r"100 bits")).wait_for(state="visible")
    assert not page.get_by_text(re.compile(r"34 viewers")).is_visible()
    page.get_by_text(re.compile(r"34 viewers")).wait_for(state="visible")
    assert not page.get_by_text(re.compile(r"100 bits")).is_visible()
    page.get_by_text(re.compile(r"34 viewers")).wait_for(state="hidden")


def test_long_caption_fits_two_lines(server, page):
    text = "A complete caption with ordinary words. " * 12
    _send(server, ev_caption("long", "host", text, 30000))
    page.locator("#caption-text").wait_for(state="visible")
    result = page.locator("#caption-text").evaluate("e => ({h:e.offsetHeight, line:parseFloat(getComputedStyle(e).lineHeight), text:e.textContent})")
    assert text.startswith(result["text"])
    assert result["h"] <= result["line"] * 2 + 2


def test_privacy_cut_is_atomic_under_burst(server, page):
    _send(server, ev_caption("voice", "host", "speaking", 30000))
    _send(server, ev_audio("clip", "/audio/clip.wav", "voice"))
    page.wait_for_function("window.__media.plays > 0")
    page.evaluate("""() => {
      window.__cut = [];
      new MutationObserver(() => {
        const cover = document.querySelector('#cover');
        if (!cover.hidden) window.__cut.push({
          paused: window.__media.pauses,
          caption: document.querySelector('#caption').hidden,
          chat: document.querySelector('#chat').children.length,
          alert: document.querySelector('#alert').hidden
        });
      }).observe(document.body, {attributes:true, childList:true, subtree:true});
    }""")
    for i in range(100):
        _send(server, ev_chat(str(i), "viewer_42", "message"))
    _send(server, ev_state(True, "autonomous", True, False), urgent=True)
    page.wait_for_function("window.__cut.length > 0")
    observations = page.evaluate("window.__cut")
    assert all(x["paused"] > 0 and x["caption"] and x["chat"] == 0 and x["alert"] for x in observations)
