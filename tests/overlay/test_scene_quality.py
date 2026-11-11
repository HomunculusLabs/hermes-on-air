"""Quality fallback decisions use sustained frame intervals, not a single stall."""
import asyncio
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest


def test_quality_fallback_policy(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    source = Path(__file__).parents[2] / 'onair/scene_starter/quality.js'
    (tmp_path / 'quality.mjs').write_text(source.read_text())
    script = r'''
import {createQualityController} from './quality.mjs';
function check(condition, why) { if (!condition) throw Error(why); }
let changes = [];
const auto = createQualityController('auto', value => changes.push(value));
check(auto.mode === 'high' && changes.join() === 'high', 'auto start');
for (let t=0;t<=1000;t+=50) auto.sample(t, true);
auto.sample(1100, false);
for (let t=1200;t<=2500;t+=16) auto.sample(t, true);
check(auto.mode === 'high', 'isolated slow period or hidden page');
for (let t=2600;t<=5600;t+=50) auto.sample(t, true);
check(auto.mode === 'low' && changes.join() === 'high,low', 'sustained slow fallback');
for (let t=5616;t<=9000;t+=16) auto.sample(t, true);
check(auto.mode === 'low' && changes.length === 2, 'fallback must not oscillate');
const high = createQualityController('high', () => {});
for (let t=0;t<=5000;t+=100) high.sample(t, true);
check(high.mode === 'high', 'forced high');
const low = createQualityController('low', () => {});
check(low.mode === 'low', 'forced low');
const invalid = createQualityController('invalid', () => {});
for (let t=0;t<=2500;t+=50) invalid.sample(t, true);
check(invalid.mode === 'low', 'invalid should behave as auto');
const verySlow = createQualityController('auto', () => {});
for (let t=0;t<=2500;t+=500) verySlow.sample(t, true);
check(verySlow.mode === 'low', 'very slow rendering must still downgrade');
const stalled = createQualityController('auto', () => {});
stalled.sample(0, true); stalled.sample(3000, true);
check(stalled.mode === 'high', 'one stall alone is not sustained');
stalled.sample(6000, true);
check(stalled.mode === 'low', 'repeated multi-second frames must downgrade');
'''
    result = subprocess.run([node, '--input-type=module', '-e', script], cwd=tmp_path,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_quality_in_browser(tmp_path):
    source = os.environ.get('ONAIR_SCENE_TEST_DIR')
    if not source:
        pytest.skip('No scaffolded browser-test scene supplied')
    playwright = pytest.importorskip('playwright.sync_api')
    from onair.overlay.server import EventHub, OverlayServer, ev_state

    scene = tmp_path / 'scene'
    shutil.copytree(source, scene)
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
            browser = p.chromium.launch(headless=True, executable_path=os.environ.get('ONAIR_CHROMIUM') or None,
                args=['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader'])
            page = browser.new_page(viewport={'width': 480, 'height': 270})
            page.add_init_script('''
window.requestAnimationFrame = callback => setTimeout(() => callback(performance.now()), 60);
const NativeEvents = window.EventSource;
window.EventSource = class extends NativeEvents { constructor(...args) { super(...args); window.__events = this; } };
''')
            url = f'http://127.0.0.1:{server.port}/scene/'
            for requested, expected in [('low', 'low'), ('high', 'high'), ('auto', 'low')]:
                page.goto(url + '?quality=' + requested, wait_until='domcontentloaded')
                page.wait_for_function("() => document.body.dataset.presence === 'live'")
                if requested == 'auto':
                    page.wait_for_timeout(1200)
                    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
                    page.wait_for_timeout(1000)
                    assert page.locator('body').get_attribute('data-quality') == 'high'
                page.wait_for_function('(v) => document.body.dataset.quality === v', arg=expected, timeout=10000)
                status = page.evaluate("() => document.querySelector('canvas').onAirStatus()")
                if expected == 'low':
                    assert status['pixelRatio'] == .5 and status['shadowsEnabled'] is False
                    assert page.evaluate("() => document.querySelector('canvas').width") == 240
                    page.evaluate("() => window.__events.onmessage({data: JSON.stringify({type:'alert'})})")
                    assert page.locator('body').get_attribute('data-alert') == 'true'
                    page.wait_for_function("() => document.body.dataset.alert === 'false'", timeout=5000)
                else:
                    page.wait_for_timeout(2500)
                    assert page.locator('body').get_attribute('data-quality') == 'high'
                    assert status['pixelRatio'] == 2 and status['shadowsEnabled'] is True
            browser.close()
    finally:
        loop.call_soon_threadsafe(loop.stop); thread.join(10)
