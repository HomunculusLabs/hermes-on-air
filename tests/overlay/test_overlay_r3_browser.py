"""Additional compositor and caption paging checks."""
import os
from pathlib import Path
import pytest
from test_overlay_r2_browser import browser, populated_page
from test_overlay_security_browser import server, page, _send
from onair.overlay.server import ev_caption, ev_audio, ev_state, ev_caption_clear


def test_long_caption_pages(browser, server):
    context, page = populated_page(browser, server, 'dark', 'full')
    try:
        page.clock.install()
        text = ' '.join('word%03d' % n for n in range(40))
        _send(server, ev_caption('paging', 'host', text, 60000))
        page.wait_for_function("document.querySelector('#caption-text').textContent.startsWith('word000')")
        pages = [page.locator('#caption-text').text_content()]
        target = os.environ.get('OVERLAY_R2_SHOTS')
        if target:
            page.screenshot(path=str(Path(target) / 'long-caption-page-1.png'), animations='disabled')
        for _ in range(40):
            page.clock.fast_forward(1000)
            current = page.locator('#caption-text').text_content()
            if current and current != pages[-1]:
                pages.append(current)
                if target and len(pages) == 2:
                    page.screenshot(path=str(Path(target) / 'long-caption-page-2.png'), animations='disabled')
            if ' '.join(pages) == text:
                break
        assert len(pages) > 1
        assert ' '.join(pages) == text
        page.clock.fast_forward(2000)
        assert page.locator('#caption-text').text_content() == pages[-1]
        _send(server, ev_caption('replacement', 'host', 'New caption', 60000))
        page.get_by_text('New caption', exact=True).wait_for()
        page.clock.fast_forward(10000)
        assert page.locator('#caption-text').text_content() == 'New caption'
    finally:
        context.close()


@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_r3_per_message_backing_and_typography(browser, server, theme):
    context, page = populated_page(browser, server, theme, 'full')
    try:
        style = page.locator('#chat li').last.evaluate("e => ({background:getComputedStyle(e).backgroundColor, opacity:+getComputedStyle(e).opacity})")
        assert style['background'] != 'rgba(0, 0, 0, 0)'
        assert style['opacity'] >= .75
        assert page.locator('#live').evaluate("e => getComputedStyle(e).fontSize") == '16px'
        assert page.locator('.dot').evaluate("e => getComputedStyle(e).width") == '10px'
    finally:
        context.close()


def luminance(rgb):
    def linear(value):
        v = value / 255
        return v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
    return sum(weight * linear(value) for weight, value in zip((.2126, .7152, .0722), rgb))


@pytest.mark.parametrize('theme', ['dark', 'light'])
@pytest.mark.parametrize('background', [0, 255], ids=['black', 'white'])
def test_composited_chat_contrast(browser, server, theme, background):
    from io import BytesIO
    Image = pytest.importorskip('PIL.Image')
    context, page = populated_page(browser, server, theme, 'full')
    try:
        page.evaluate("value => { let e=document.documentElement; e.style.setProperty('background-image', `linear-gradient(90deg, #e5a344 0%, #485c9a 55%, rgb(${value},${value},${value}) 55%, rgb(${value},${value},${value}) 100%)`, 'important'); }", background)
        img = Image.open(BytesIO(page.screenshot(animations='disabled'))).convert('RGB')
        for item in (page.locator('#chat li').first, page.locator('#chat li').last):
            box = item.bounding_box()
            sample = img.getpixel((int(box['x'] + 8), int(box['y'] + 8)))
            opacity = item.evaluate('e => +getComputedStyle(e).opacity')
            fills = [(255, 255, 255), (255, 224, 161)] if theme == 'dark' else [(20, 20, 20), (120, 66, 0)]
            for fill in fills:
                actual = tuple(round(v * opacity + background * (1 - opacity)) for v in fill)
                a, b = sorted((luminance(actual), luminance(sample)))
                ratio = (b + .05) / (a + .05)
                print(f'contrast {theme} background={background} fill={fill} ratio={ratio:.2f}')
                assert ratio >= 4.5, (theme, background, sample, actual, ratio)
    finally:
        context.close()


def test_audio_paging_and_cancellation(server, page):
    page.evaluate("""() => {
      window.Audio = class {
        constructor() { this.paused = true; this.duration = 20; this.currentTime = 0; this.events = {}; window.testClip = this; }
        addEventListener(name, fn) { this.events[name] = fn; }
        play() { this.paused = false; this.events.playing(); return Promise.resolve(); }
        pause() { this.paused = true; }
        removeAttribute() {} load() {}
      };
    }""")
    text = ' '.join('word%03d' % n for n in range(80))
    _send(server, ev_caption('audio-paging', 'host', text, 60000))
    page.wait_for_function("document.querySelector('#caption-text').textContent.startsWith('word000')")
    first = page.locator('#caption-text').text_content()
    _send(server, ev_audio('clip', '/audio/clip.wav', 'audio-paging'))
    page.wait_for_function('window.testClip && !window.testClip.paused')
    page.evaluate("() => { window.testClip.currentTime = 10; window.testClip.events.timeupdate(); }")
    middle = page.locator('#caption-text').text_content()
    assert middle != first and middle in text
    _send(server, ev_state(True, 'autonomous', True, False), urgent=True)
    page.locator('#cover').wait_for(state='visible')
    assert page.locator('#caption-text').text_content() == ''
    page.evaluate("() => { window.testClip.currentTime = 19; window.testClip.events.timeupdate(); }")
    assert page.locator('#caption-text').text_content() == ''


def test_alert_animation_durations(browser, server):
    context, page = populated_page(browser, server, 'dark', 'full')
    try:
        target = os.environ.get('OVERLAY_R2_SHOTS')
        if target:
            page.screenshot(path=str(Path(target) / 'long-name-alert.png'), animations='disabled')
        duration = page.locator('#alert').evaluate('e => getComputedStyle(e).animationDuration')
        assert duration == '0.25s'
        page.locator('#alert').evaluate("e => e.classList.add('exiting')")
        assert page.locator('#alert').evaluate('e => getComputedStyle(e).animationDuration') == '0.25s'
    finally:
        context.close()


def test_caption_clear_cancels_page_timer(server, page):
    page.clock.install()
    text = ' '.join('word%03d' % n for n in range(60))
    _send(server, ev_caption('to-stop', 'host', text, 60000))
    page.wait_for_function("document.querySelector('#caption-text').textContent.startsWith('word000')")
    _send(server, ev_caption_clear())
    page.locator('#caption').wait_for(state='hidden')
    page.clock.fast_forward(30000)
    assert page.locator('#caption').is_hidden()
    assert page.locator('#caption-text').text_content() == ''


def test_caption_expiry_waits_for_all_pages_and_last_hold(server, page):
    page.clock.install()
    text = ' '.join('word%03d' % n for n in range(80))
    _send(server, ev_caption('short-ttl-pages', 'host', text, 12000))
    page.wait_for_function("document.querySelector('#caption-text').textContent.startsWith('word000')")
    seen = [page.locator('#caption-text').text_content()]
    for tick in range(100):
        page.clock.run_for(1000)
        current = page.locator('#caption-text').text_content()
        if current and current != seen[-1]:
            seen.append(current)
            if ' '.join(seen) == text:
                assert tick >= 12
                assert page.locator('#caption').is_visible()
                page.clock.run_for(1700)
                assert page.locator('#caption').is_visible()
        if page.locator('#caption').is_hidden():
            break
    assert len(seen) > 1
    assert ' '.join(seen) == text
    assert page.locator('#caption').is_hidden()


def test_short_caption_expires_at_ttl(server, page):
    page.clock.install()
    _send(server, ev_caption('short-ttl', 'host', 'Short caption', 2000))
    page.get_by_text('Short caption', exact=True).wait_for()
    page.clock.run_for(1900)
    assert page.locator('#caption').is_visible()
    page.clock.run_for(600)
    assert page.locator('#caption').is_hidden()


def test_replacement_cancels_expired_caption_paging(server, page):
    page.clock.install()
    text = ' '.join('word%03d' % n for n in range(80))
    _send(server, ev_caption('old-pages', 'host', text, 1000))
    page.wait_for_function("document.querySelector('#caption-text').textContent.startsWith('word000')")
    page.clock.run_for(2000)
    _send(server, ev_caption('replacement', 'host', 'Replacement caption', 60000))
    page.get_by_text('Replacement caption', exact=True).wait_for()
    page.clock.run_for(45000)
    assert page.locator('#caption-text').text_content() == 'Replacement caption'
    assert page.locator('#caption').is_visible()


def test_caption_has_readable_backing_on_bright_frame(browser, server):
    from io import BytesIO
    Image = pytest.importorskip('PIL.Image')
    context, page = populated_page(browser, server, 'dark', 'full')
    try:
        page.evaluate("() => { document.documentElement.style.setProperty('background', '#fff', 'important'); }")
        page.wait_for_timeout(400)
        box = page.locator('#caption').bounding_box()
        img = Image.open(BytesIO(page.screenshot(animations='disabled'))).convert('RGB')
        sample = img.getpixel((int(box['x'] + 4), int(box['y'] + box['height'] / 2)))
        assert (1.05 / (luminance(sample) + .05)) >= 4.5, sample
    finally:
        context.close()


def test_show_marker_is_opt_in_and_never_broadcast_status(browser, server):
    context, page = populated_page(browser, server, 'dark', 'full')
    try:
        assert not page.locator('#live').is_visible()
        page.goto(server[0].overlay_url + '?status=1')
        page.wait_for_function("document.body.classList.contains('live')")
        assert page.locator('#live').is_visible()
        assert page.locator('#live-label').text_content() == 'ON'
        _send(server, ev_state(True, 'approval', False, False))
        page.wait_for_function("document.querySelector('#live-label').textContent === 'APPROVAL'")
    finally:
        context.close()
