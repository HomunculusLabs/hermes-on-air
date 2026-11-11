"""Optional real-browser scene checks with a scaffold and Chromium."""
import asyncio
import csv
import io
import math
import os
import shutil
import struct
import threading
import wave
from pathlib import Path

import pytest

from onair.overlay.server import EventHub, OverlayServer, ev_audio, ev_caption, ev_caption_clear, ev_state


def burst_clip():
    rate = 8000
    samples = bytearray()
    for i in range(rate * 3):
        t = i / rate
        value = .7 * math.sin(2 * math.pi * 220 * t) if t < 1 or 2 <= t < 3 else 0
        samples.extend(struct.pack('<h', int(value * 32767)))
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(samples)
    return buf.getvalue()


def test_scene_browser(tmp_path):
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
    clip = burst_clip()
    server = OverlayServer(hub, lambda _: (clip, 'audio/wav'), port=0, scene_dir=scene)
    ready = threading.Event()
    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.start())
        ready.set()
        loop.run_forever()
        loop.run_until_complete(server.stop())
        loop.close()
    thread = threading.Thread(target=run)
    thread.start()
    assert ready.wait(5)
    def publish(event):
        loop.call_soon_threadsafe(hub.publish, event)
    output = Path(os.environ.get('ONAIR_SCENE_SHOTS', str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
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
            page = browser.new_page(viewport={'width': 960, 'height': 540})
            page.add_init_script('''
window.__plays = 0; HTMLMediaElement.prototype.play = function () { window.__plays++; return Promise.resolve(); };
const NativeEvents = window.EventSource;
window.EventSource = class extends NativeEvents { constructor(...args) { super(...args); window.__events = this; } };
const originalFetch = window.fetch;
window.fetch = async function(url, ...args) {
  if (String(url).startsWith('/audio/')) {
    window.__audioStart = performance.now();
    if (window.__holdAudio) await new Promise(resolve => window.__releaseAudio = resolve);
  }
  const response = await originalFetch.call(this, url, ...args);
  if (String(url).startsWith('/audio/')) {
    const read = response.arrayBuffer.bind(response);
    response.arrayBuffer = async () => { const bytes = await read(); window.__audioReads = (window.__audioReads || 0) + 1; return bytes; };
  }
  return response;
};
window.__samples = [];
setInterval(() => { if (window.__audioStart) window.__samples.push([performance.now() - window.__audioStart, Number(document.body.dataset.mouth || 0), Number(document.body.dataset.avatarBlink || 0), Number(document.body.dataset.avatarAa || 0)]); }, 50);
''')
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
            page.goto(f'http://127.0.0.1:{server.port}/scene/', wait_until='domcontentloaded', timeout=30000)
            page.wait_for_selector('canvas')
            page.wait_for_function("() => !!document.body.dataset.avatar && !!window.__events")
            assert page.locator('body').get_attribute('data-avatar') == ('vrm' if avatar else 'starter')
            if avatar:
                assert page.locator('body').get_attribute('data-avatar-pose') == 'relaxed'
                assert float(page.locator('body').get_attribute('data-avatar-left-arm-y')) < -.7
                assert float(page.locator('body').get_attribute('data-avatar-right-arm-y')) < -.7
            # Spy on the actual character implementation while forwarding every call.
            # Delivering events synchronously lets us inspect privacy before another frame.
            wiring = page.evaluate('''() => {
              const c = window.__sceneChar, calls = [];
              for (const name of ['setPresence', 'setMood', 'glance', 'react']) {
                const original = c[name].bind(c);
                c[name] = (...args) => { calls.push([name, ...args]); return original(...args); };
              }
              const send = event => window.__events.onmessage({data: JSON.stringify(event)});
              const state = (live, privacy) => send({type:'state', live, privacy, muted:false, mode:live?'approval':'off_air'});
              state(false, false); state(true, false);
              send({type:'chat'}); send({type:'chat'}); send({type:'alert'});
              send({type:'caption', id:'wire', text:'Wonderful!', ttl_ms:10000});
              state(true, true);
              const under = {visibility:getComputedStyle(document.querySelector('canvas')).visibility,
                curtain:document.body.dataset.curtain, status:document.querySelector('canvas').onAirStatus(),
                mouths:document.body.dataset.avatarMouths};
              window.__events.onerror();
              const disconnected = {curtain:document.body.dataset.curtain, presence:document.body.dataset.presence};
              state(true, true);
              const privacyReconnect = document.body.dataset.curtain;
              state(false, false);
              const held = document.body.dataset.curtain;
              const offair = {presence:document.body.dataset.presence, tally:document.body.dataset.tally,
                speaking:document.body.dataset.speaking, status:document.querySelector('canvas').onAirStatus()};
              state(true, false);
              return {calls, under, disconnected, privacyReconnect, offair, held, restored:document.body.dataset.curtain};
            }''')
            assert ['setPresence', 'offair'] in wiring['calls']
            assert ['setPresence', 'live'] in wiring['calls']
            assert ['setPresence', 'privacy'] in wiring['calls']
            assert len([c for c in wiring['calls'] if c[0] == 'glance']) == 1
            assert ['react', 'alert'] in wiring['calls']
            assert ['setMood', 'happy'] in wiring['calls']
            assert wiring['under']['visibility'] == 'hidden' and wiring['under']['curtain'] == 'true'
            assert wiring['under']['status']['mouthScale'] == .14
            if avatar:
                assert wiring['under']['status']['aa'] == 0
                assert all(value == 0 for value in wiring['under']['status']['mouthValues'].values())
                assert all(float(v) == 0 for v in wiring['under']['mouths'].split(','))
            assert wiring['disconnected'] == {'curtain': 'true', 'presence': 'privacy'}
            assert wiring['privacyReconnect'] == 'true'
            assert wiring['held'] == 'false' and wiring['restored'] == 'false'
            assert wiring['offair']['presence'] == 'offair' and wiring['offair']['tally'] == 'off'
            assert wiring['offair']['speaking'] == 'false' and wiring['offair']['status']['mouthScale'] == .14
            if avatar:
                page.evaluate("""() => window.__events.onmessage({data: JSON.stringify(
                  {type:'caption', id:'mood', text:'Wonderful!', ttl_ms:10000})})""")
                page.wait_for_function("() => (document.body.dataset.avatarMood || '').startsWith('happy:') && Number(document.body.dataset.avatarMood.split(':')[1]) > .2")
                channels = page.locator('body').get_attribute('data-avatar-mood-channels') or ''
                expected_channel = os.environ.get('ONAIR_SCENE_EXPECT_MOOD_CHANNEL')
                if expected_channel:
                    assert expected_channel in channels.split(','), channels
                if 'Expr.mouth_soft_smile' in channels:
                    mouths = page.evaluate("() => document.querySelector('canvas').onAirStatus().mouthValues")
                    assert mouths['Expr.mouth_soft_smile'] > 0
                    closed = page.evaluate("""() => {
                      window.__events.onmessage({data: JSON.stringify({type:'state',live:true,mode:'approval',privacy:true,muted:false})});
                      return document.querySelector('canvas').onAirStatus().mouthValues;
                    }""")
                    assert all(value == 0 for value in closed.values())
                    page.evaluate("""() => window.__events.onmessage({data: JSON.stringify(
                      {type:'state',live:true,mode:'approval',privacy:false,muted:false})})""")
            page.wait_for_timeout(500)
            publish(ev_caption('c1', 'cohost', 'A test caption', 8000))
            publish(ev_audio('a1', '/audio/a1.wav', 'c1'))
            page.wait_for_function('() => !!window.__audioStart')
            # Wait for an actual post-clip sample rather than assuming the browser
            # timer and the sampling interval have both run after a fixed wall delay.
            page.wait_for_function("() => window.__samples.some(row => row[0] >= 3600)", timeout=10000)
            samples = page.evaluate('() => window.__samples')
            def levels(a, b):
                return [row[1] for row in samples if a <= row[0] < b]
            if avatar:
                assert max(row[2] for row in samples) > .2, samples
                assert max(row[3] for row in samples) > .2, samples
                assert min(row[3] for row in samples) < .1, samples
                # Discover bind availability from the same loader, not expression names.
                unbound = page.evaluate("""async () => {
                  const {GLTFLoader} = await import('./vendor/three/examples/jsm/loaders/GLTFLoader.js');
                  const {VRMLoaderPlugin} = await import('./vendor/three-vrm.module.js');
                  const loader = new GLTFLoader(); loader.register(p => new VRMLoaderPlugin(p));
                  const vrm = (await loader.loadAsync('./avatar.vrm')).userData.vrm;
                  return ['aa','ih','ou','ee','oh'].filter(n =>
                    vrm.expressionManager.getExpression(n) && !vrm.expressionManager.getExpression(n).binds.length);
                }""")
                page.evaluate("""() => { const c = window.__sceneChar; c.setSpeaking(true);
                  c.setVoice(new Float32Array(100).fill(1), performance.now(), 20, 2000); }""")
                page.wait_for_timeout(300)
                weights = page.evaluate("() => window.__sceneChar.status().mouthValues")
                assert all(weights[n] == 0 for n in unbound), (unbound, weights)
                # A caption may outlive its clip: its mouth must nevertheless close.
                page.evaluate("""() => window.__sceneChar.setVoice(
                  new Float32Array([1]), performance.now(), 20, 20)""")
                page.wait_for_timeout(170)
                ended = page.evaluate("() => window.__sceneChar.status().mouthValues")
                assert all(value == 0 for value in ended.values()), ended
            for a, b, low, high in [(350, 800, .6, 1.01), (1350, 1800, 0, .08),
                                    (2350, 2800, .6, 1.01), (3250, 3600, 0, .08)]:
                values = levels(a, b)
                assert values and all(low <= v <= high for v in values), (a, values, samples)
            assert page.locator('body').get_attribute('data-speaking') == 'true'
            evidence = os.environ.get('ONAIR_SCENE_EVIDENCE_CSV')
            if evidence:
                envelope = page.evaluate("""async () => {
                  const {decodeVoice} = await import('./events.js');
                  const bytes = await (await fetch('/audio/a1.wav')).arrayBuffer();
                  const voice = decodeVoice(bytes);
                  return {levels: Array.from(voice.levels), windowMs: voice.windowMs};
                }""")
                with open(evidence, 'w', newline='') as f:
                    writer = csv.writer(f); writer.writerow(['elapsed_ms', 'decoded_envelope', 'mouth_openness'])
                    for t, value, _, _ in samples:
                        index = int(t / envelope['windowMs'])
                        level = envelope['levels'][index] if 0 <= index < len(envelope['levels']) else 0
                        writer.writerow([round(t, 2), level, value])
            def neutral():
                page.wait_for_function("() => document.body.dataset.speaking === 'false' && Number(document.body.dataset.mouth) === 0")
                actual = page.evaluate("() => document.querySelector('canvas').onAirStatus()")
                assert actual['mouthScale'] == .14 and (not avatar or actual['aa'] == 0)
                if avatar:
                    assert all(value == 0 for value in actual['mouthValues'].values())
                    assert all(float(v) == 0 for v in page.locator('body').get_attribute('data-avatar-mouths').split(','))
            def start(label):
                reads = page.evaluate('() => window.__audioReads || 0')
                publish(ev_state(True, 'approval', False, False))
                publish(ev_caption(label, 'cohost', 'Voice active', 10000))
                publish(ev_audio('a1', '/audio/a1.wav', label))
                page.wait_for_function('(n) => (window.__audioReads || 0) > n', arg=reads)
                page.wait_for_timeout(100)
                page.wait_for_function("() => Number(document.body.dataset.mouth) > .6")
            start('clear')
            publish(ev_caption_clear()); neutral()
            publish(ev_audio('a1', '/audio/a1.wav', 'clear'))
            page.wait_for_timeout(250); neutral()
            for label, state in [('stop', ev_state(False, 'off_air', False, False)),
                                 ('privacy', ev_state(True, 'approval', True, False)),
                                 ('muted', ev_state(True, 'approval', False, True))]:
                start(label); publish(state); neutral()
            # A later caption invalidates an older in-flight voice fetch.
            page.evaluate('() => { window.__holdAudio = true; window.__releaseAudio = null; }')
            publish(ev_state(True, 'approval', False, False))
            publish(ev_caption('pending', 'cohost', 'Pending', 10000))
            publish(ev_audio('a1', '/audio/a1.wav', 'pending'))
            page.wait_for_function('() => !!window.__releaseAudio')
            publish(ev_state(False, 'off_air', False, False)); neutral()
            page.evaluate('() => { window.__holdAudio = false; window.__releaseAudio(); }')
            page.wait_for_timeout(500); neutral()
            page.evaluate('() => { window.__holdAudio = true; window.__releaseAudio = null; }')
            publish(ev_state(True, 'approval', False, False))
            publish(ev_caption('older', 'cohost', 'Old line', 10000))
            publish(ev_audio('a1', '/audio/a1.wav', 'older'))
            page.wait_for_function('() => !!window.__releaseAudio')
            publish(ev_caption('newer', 'cohost', 'New text-only line', 10000))
            page.wait_for_timeout(200)
            page.evaluate('() => { window.__holdAudio = false; window.__releaseAudio(); }')
            page.wait_for_timeout(3400)
            page.wait_for_function("() => Number(document.body.dataset.mouth) > .6", timeout=3000)
            start('close')
            page.evaluate('() => window.__events.close()'); neutral()
            page.reload(wait_until='domcontentloaded', timeout=60000)
            page.wait_for_function("() => !!window.__events && window.__events.readyState === 1 && document.body.dataset.presence === 'live'")
            start('disconnect')
            asyncio.run_coroutine_threadsafe(server.stop(), loop).result(5)
            neutral()
            assert page.evaluate('() => window.__plays') == 0
            assert not errors, errors
            browser.close()
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)
