"""Content-fit chat and adaptive alert layout."""
import os
from pathlib import Path
import pytest
from test_overlay_r2_browser import browser, populated_page
from test_overlay_security_browser import server, _send
from onair.overlay.server import ev_alert, ev_state


@pytest.mark.parametrize('theme', ['dark', 'light'])
@pytest.mark.parametrize('side', ['left', 'right'])
def test_chat_fits_and_aligns(browser, server, theme, side):
    context, page = populated_page(browser, server, theme, 'chat')
    try:
        if side == 'left':
            page.evaluate("document.documentElement.style.cssText='--chat-left:64px;--chat-right:auto;--chat-align:flex-start'")
        boxes = [item.bounding_box() for item in page.locator('#chat li').all()]
        assert max(b['width'] for b in boxes) - min(b['width'] for b in boxes) > 100
        edges = [b['x'] if side == 'left' else b['x'] + b['width'] for b in boxes]
        assert max(edges) - min(edges) < 1
        if theme == 'light':
            assert page.locator('#chat li').first.evaluate("e => getComputedStyle(e).borderLeftWidth") == '1px'
    finally:
        context.close()


@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_alert_rows_follow_real_truncation(browser, server, theme):
    context, page = populated_page(browser, server, theme, 'full')
    try:
        name = page.locator('.alert-name')
        event = page.locator('.alert-event')
        assert event.bounding_box()['y'] > name.bounding_box()['y']
        assert name.evaluate('e => e.scrollWidth > e.clientWidth')
        target = os.environ.get('OVERLAY_R2_SHOTS')
        if target:
            page.screenshot(path=str(Path(target) / ('long-name-alert-' + theme + '.png')), animations='disabled')
        _send(server, ev_state(True, 'autonomous', True, False), urgent=True)
        page.locator('#cover').wait_for(state='visible')
        _send(server, ev_state(True, 'autonomous', False, False), urgent=True)
        page.locator('#cover').wait_for(state='hidden')
        _send(server, ev_alert('short-alert', 'sub', 'viewer_42', 0, 12, '', 60000))
        page.get_by_text('viewer_42', exact=True).wait_for()
        assert abs(event.bounding_box()['y'] - name.bounding_box()['y']) < 2
        assert name.evaluate('e => e.scrollWidth <= e.clientWidth')
        if target:
            page.screenshot(path=str(Path(target) / ('short-name-alert-' + theme + '.png')), animations='disabled')
    finally:
        context.close()
