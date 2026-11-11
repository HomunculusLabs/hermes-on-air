"""R2 1080p Chromium checks and opt-in screenshot capture.

Run: OVERLAY_R2_SHOTS=/path/to/shots python -m pytest tests/overlay/test_overlay_r2_browser.py -v
The busy backdrop is test-only: the browser intercepts the same-origin CSS response
and adds a deterministic multitone pattern for URLs with bg=test. Production is
not modified and the overlay's actual SSE path is used for all fixture content.
"""

import os
from pathlib import Path
from urllib.parse import urlencode

import pytest

from onair.overlay.server import ev_alert, ev_caption, ev_chat, ev_state
from test_overlay_security_browser import server, _send  # reuse the real loopback harness

CAPTION = "Welcome to the show! Tonight we explore creative ideas and share what we learn."
CHAT = [
    ("viewer_one", "Hello everyone!"),
    ("viewer_two", "Looking forward to today's show."),
    ("viewer_three", "This is a longer message with plenty of ordinary words to exercise wrapping while keeping the whole conversation readable on the screen."),
    ("viewer_four", "Thanks for explaining that."),
    ("viewer_five", "What a fun idea!"),
]
ALERT_NAME = "CommunitySupporterWithAnEspeciallyLongDisplayName" * 4
BUSY_CSS = """
html { background-color: #8292a5 !important;
 background-image: repeating-linear-gradient(135deg, #f3c64e 0 45px, #55439b 45px 90px, #db6580 90px 135px, #408d84 135px 180px, #e6e9ed 180px 225px, #293044 225px 270px) !important; }
"""


@pytest.fixture
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, executable_path=os.environ.get("OVERLAY_TEST_CHROMIUM") or None)
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Playwright Chromium is not installed")
            raise
        yield browser
        browser.close()


def populated_page(browser, server, theme, layout=None, busy=True):
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1,
                                  reduced_motion="reduce")
    page = context.new_page()

    def css_route(route):
        response = route.fetch()
        route.fulfill(response=response, body=response.text() + BUSY_CSS)

    if busy:
        page.route("**/overlay/overlay.css", css_route)
    params = {"bg": "test", "theme": theme}
    if layout is not None:
        params["layout"] = layout
    page.goto(server[0].overlay_url + "?" + urlencode(params), wait_until="load")
    page.wait_for_function("document.querySelector('#cover').hidden")
    assert page.evaluate("new URL(location.href).searchParams.get('bg')") == "test"
    if busy:
        assert "gradient" in page.locator("html").evaluate("e => getComputedStyle(e).backgroundImage")
    _send(server, ev_caption("r2-caption", "host", CAPTION, 60000))
    for index, (name, message) in enumerate(CHAT):
        _send(server, ev_chat(f"r2-chat-{index}", name, message))
    _send(server, ev_alert("r2-alert", "raid", ALERT_NAME, 1234, 0, "", 60000))
    # Wait on DOM content rather than visibility: single-surface layouts hide peers.
    page.wait_for_function("name => document.querySelector('#alert').textContent.includes(name)", arg=ALERT_NAME)
    page.wait_for_function("document.querySelector('#chat').children.length === 5")
    page.wait_for_function("document.querySelector('#caption-text').textContent.length > 0")
    page.evaluate("document.fonts.ready")
    return context, page


def inside(inner, outer, tolerance=1):
    return (inner['x'] >= outer['x'] - tolerance and inner['y'] >= outer['y'] - tolerance
            and inner['x'] + inner['width'] <= outer['x'] + outer['width'] + tolerance
            and inner['y'] + inner['height'] <= outer['y'] + outer['height'] + tolerance)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("layout", [None, "full", "captions", "chat", "alerts"], ids=["default", "full", "captions", "chat", "alerts"])
def test_r2_layouts_on_busy_background(browser, server, theme, layout):
    context, page = populated_page(browser, server, theme, layout)
    try:
        assert page.locator("body").get_attribute("data-theme") == theme
        assert page.locator("body").get_attribute("data-layout") == (layout or "full")
        visible = {"caption": layout in (None, "full", "captions"),
                   "chat": layout in (None, "full", "chat"),
                   "alert": layout in (None, "full", "alerts")}
        for surface, expected in visible.items():
            assert page.locator("#" + surface).is_visible() == expected
        assert page.locator("#caption-text").text_content() == CAPTION
        assert page.locator("#chat li").count() == 5
        assert [page.locator("#chat li").nth(i).locator("span").text_content() for i in range(5)] == [m for _, m in CHAT]
        assert ALERT_NAME in page.locator("#alert").text_content()
        viewport = {"x": 0, "y": 0, "width": 1920, "height": 1080}
        for surface, expected in visible.items():
            if expected:
                assert inside(page.locator("#" + surface).bounding_box(), viewport), surface
        if visible["caption"]:
            metrics = page.locator("#caption-text").evaluate("e => ({height:e.getBoundingClientRect().height, line:parseFloat(getComputedStyle(e).lineHeight), scroll:e.scrollHeight, client:e.clientHeight, overflow:getComputedStyle(e).overflowY})")
            assert metrics["height"] > metrics["line"] * 1.5, metrics
            assert metrics["height"] <= metrics["line"] * 2 + 2, metrics
            assert metrics["overflow"] == "visible" or metrics["scroll"] <= metrics["client"] + 4, metrics
        if visible["chat"]:
            chat = page.locator("#chat").bounding_box()
            for item in page.locator("#chat li").all():
                assert inside(item.bounding_box(), chat)
        if visible["alert"]:
            assert page.locator("#alert").evaluate("e => e.scrollWidth <= e.clientWidth + 1 && e.scrollHeight <= e.clientHeight + 1")
            assert page.locator(".alert-name").evaluate("e => e.scrollWidth > e.clientWidth && getComputedStyle(e).textOverflow === 'ellipsis'")
        target = os.environ.get("OVERLAY_R2_SHOTS")
        if target:
            target = Path(target)
            target.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(target / f"layout-{layout or 'default'}-{theme}.png"), animations="disabled")
            if layout == "full":
                page.screenshot(path=str(target / f"busy-{theme}.png"), animations="disabled")
    finally:
        context.close()


def test_r2_typography_contract(browser, server):
    context, page = populated_page(browser, server, "dark", "full")
    try:
        assert page.locator("#caption-text").evaluate("e => parseFloat(getComputedStyle(e).fontSize)") >= 36
        assert page.locator("#caption-text").evaluate("e => getComputedStyle(e).webkitTextStrokeWidth") in ("3px", "4px")
        assert page.locator("#chat").evaluate("e => parseFloat(getComputedStyle(e).fontSize)") >= 24
        assert page.locator("#alert .alert-name").count() == 1
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("layout", ["full", "captions", "chat", "alerts", "privacy"])
def test_r2_transparent_and_privacy_shots(browser, server, theme, layout):
    context, page = populated_page(browser, server, theme, "full" if layout == "privacy" else layout, busy=False)
    try:
        assert page.locator("html").evaluate("e => getComputedStyle(e).backgroundColor") == "rgba(0, 0, 0, 0)"
        if layout == "privacy":
            _send(server, ev_state(True, "autonomous", True, False), urgent=True)
            page.locator("#cover").wait_for(state="visible")
        target = os.environ.get("OVERLAY_R2_SHOTS")
        if target:
            page.screenshot(path=str(Path(target) / f"{layout}-{theme}.png"), animations="disabled", omit_background=True)
    finally:
        context.close()
