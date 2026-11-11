"""Browser checks for staging and local theme/shot variants."""
import asyncio
import io
import os
import shutil
import threading
from pathlib import Path

import pytest

from onair.overlay.server import EventHub, OverlayServer, ev_state


@pytest.mark.parametrize('chosen_theme', ('cozy-day', 'night-studio', 'clean-stage'))
@pytest.mark.parametrize('chosen_shot', ('wide', 'medium', 'close'))
def test_show_scene_browser(tmp_path, chosen_theme, chosen_shot):
    source = os.environ.get('ONAIR_SCENE_TEST_DIR')
    if not source:
        pytest.skip('No scaffolded browser-test scene supplied')
    playwright = pytest.importorskip('playwright.sync_api')
    scene = tmp_path / 'scene'
    shutil.copytree(source, scene)
    avatar = os.environ.get('ONAIR_SCENE_TEST_AVATAR')
    if avatar:
        shutil.copyfile(avatar, scene / 'avatar.vrm')
    loop = asyncio.new_event_loop()
    hub = EventHub()
    hub.snapshot = lambda: ev_state(True, 'approval', False, False)
    server = OverlayServer(hub, lambda _: None, port=0, scene_dir=scene)
    ready = threading.Event()
    def serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.start()); ready.set(); loop.run_forever()
        loop.run_until_complete(server.stop()); loop.close()
    thread = threading.Thread(target=serve); thread.start()
    assert ready.wait(5)
    try:
        with playwright.sync_playwright() as p:
            try:
                gpu = os.environ.get('ONAIR_SCENE_TEST_GPU') == '1'
                args = (['--no-sandbox', '--use-gl=angle', '--use-angle=vulkan', '--enable-features=Vulkan', '--disable-vulkan-surface']
                        if gpu else ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader'])
                browser = p.chromium.launch(headless=True, executable_path=os.environ.get('ONAIR_CHROMIUM') or None,
                    args=args)
            except playwright.Error as exc:
                pytest.skip(f'Chromium unavailable: {exc}')
            page = browser.new_page(viewport={'width': 480, 'height': 270})
            page.add_init_script('''
const NativeEvents = window.EventSource;
window.EventSource = class extends NativeEvents { constructor(...args) { super(...args); window.__events = this; } };
''')
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('console', lambda m: errors.append(m.text) if m.type in ('warning', 'error') else None)
            for theme in (chosen_theme,):
                for shot in (chosen_shot,):
                    for transparent in (False, True):
                        page.goto(f'http://127.0.0.1:{server.port}/scene/?theme={theme}&shot={shot}' + ('&bg=transparent' if transparent else ''), wait_until='load')
                        page.wait_for_function("() => document.body.dataset.presence === 'live'", timeout=30000)
                        assert page.locator('body').get_attribute('data-theme') == theme
                        assert page.locator('body').get_attribute('data-shot') == shot
                        assert page.locator('body').get_attribute('data-tally') == 'on'
                        status = page.evaluate("() => document.querySelector('canvas').onAirStatus()")
                        assert status['deskEnabled'] is (not bool(avatar))
                        result = page.evaluate('''() => {
                          window.__events.onmessage({data: JSON.stringify({type:'state', live:true, mode:'approval', privacy:true, muted:false})});
                          return [document.body.dataset.curtain, document.body.dataset.tally,
                            getComputedStyle(document.querySelector('canvas')).visibility,
                            getComputedStyle(document.body).backgroundColor];
                        }''')
                        assert result[:3] == ['true', 'off', 'hidden']
                        assert result[3] == ('rgba(0, 0, 0, 0)' if transparent else 'rgb(52, 54, 58)')
                        if transparent and theme == 'clean-stage' and shot == 'close':
                            try:
                                from PIL import Image
                            except ImportError:
                                Image = None
                            if Image:
                                image = Image.open(io.BytesIO(page.screenshot(omit_background=True))).convert('RGBA')
                                assert image.getchannel('A').getextrema() == (0, 0)
                        page.evaluate("() => window.__events.onmessage({data: JSON.stringify({type:'state', live:false, mode:'off_air', privacy:false, muted:false})})")
                        assert page.locator('body').get_attribute('data-curtain') == 'false'
                        assert page.locator('body').get_attribute('data-presence') == 'offair'
                        assert page.locator('body').get_attribute('data-tally') == 'off'
                        page.evaluate("() => window.__events.onmessage({data: JSON.stringify({type:'state', live:true, mode:'approval', privacy:false, muted:false})})")
                        assert page.locator('body').get_attribute('data-curtain') == 'false'
            page.evaluate("() => window.__events.onmessage({data: JSON.stringify({type:'alert'})})")
            assert page.locator('body').get_attribute('data-alert') == 'true'
            page.wait_for_function("() => document.body.dataset.alert === 'false'", timeout=4000)
            page.evaluate('() => window.__events.onerror()')
            assert page.locator('body').get_attribute('data-curtain') == 'true'
            page.evaluate("() => window.__events.onmessage({data: JSON.stringify({type:'state', live:true, mode:'approval', privacy:false, muted:false})})")
            page.evaluate('() => window.__events.close()')
            assert page.locator('body').get_attribute('data-curtain') == 'true'
            assert not errors, errors
            browser.close()
    finally:
        loop.call_soon_threadsafe(loop.stop); thread.join(10)
