"""Behavioral coverage for the client guards using actual SSE frames and Chromium."""

import os

import pytest

from onair.overlay.server import ev_caption_clear, ev_chat
from test_overlay_security_browser import server, page, _send


def _inject(server, events):
    """Enqueue exact frames on the server loop without changing normal publish semantics."""
    def enqueue():
        hub = server[1]
        assert hub.subscriber_count() == 1
        q = next(iter(hub._subs))
        for ev in events:
            q.put_nowait((ev["type"], hub._frame(ev)))
    server[2](enqueue)


def test_replayed_and_regressive_frames_do_not_render(server, page):
    page.wait_for_function("document.querySelector('#cover').hidden")
    original = _send(server, ev_chat("original", "viewer", "original message"))
    page.get_by_text("original message", exact=True).wait_for(state="visible")
    # Both rejected frames have distinct payloads; a later valid event is an
    # ordering barrier proving the browser consumed everything ahead of it.
    replay = dict(ev_chat("replay", "viewer", "duplicate message"), seq=original["seq"])
    older = dict(ev_chat("older", "viewer", "older message"), seq=original["seq"] - 1)
    _inject(server, [replay, older])
    _send(server, ev_chat("fresh", "viewer", "fresh message"))
    page.get_by_text("fresh message", exact=True).wait_for(state="visible")
    assert page.locator("#chat li span").all_text_contents() == ["original message", "fresh message"]


def test_content_before_initial_state_is_not_handled(server):
    # Replace this fixture's subscription only: put a content frame before the
    # genuine snapshot. No production server behavior is changed.
    def install():
        hub = server[1]
        subscribe = hub.subscribe
        def with_early_content():
            q = subscribe()
            q.get_nowait()
            # A positive sequence lower than the snapshot, which is normally 1.
            # Stamp a fresh state instead so both frames have valid ordering.
            state = hub._stamp(hub.snapshot())
            early = dict(ev_caption_clear(), seq=state["seq"] - 1)
            q.put_nowait((early["type"], hub._frame(early)))
            q.put_nowait((state["type"], hub._frame(state)))
            return q
        hub.subscribe = with_early_content
    server[2](install)
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, executable_path=os.environ.get("OVERLAY_TEST_CHROMIUM") or None)
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Playwright Chromium is not installed")
            raise
        context = browser.new_context()
        page = context.new_page()
        # Observe DOM handling, not client internals; a caption_clear writes the
        # hidden attribute even when the caption was already hidden.
        page.add_init_script("""document.addEventListener('DOMContentLoaded', () => {
            window.clearWrites = 0;
            new MutationObserver(records => { window.clearWrites += records.length; })
              .observe(document.querySelector('#caption'), {attributes: true, attributeFilter: ['hidden']});
        });""")
        try:
            page.goto(server[0].overlay_url, wait_until="load")
            page.wait_for_function("document.querySelector('#cover').hidden")
            _send(server, ev_chat("fresh", "viewer", "fresh after state"))
            page.get_by_text("fresh after state", exact=True).wait_for(state="visible")
            assert page.evaluate("window.clearWrites") == 0
        finally:
            context.close()
            browser.close()
