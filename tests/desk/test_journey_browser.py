"""Real-browser journey for the desk bundle: setup guide, healthy approval, preflight failure,
runtime latch, privacy and a lost connection at 390x844, 1280x800 and 1920x1080.

One Chromium launch. The real dashboard/dist/index.js and style.css load into a fixture page;
only the host's React SDK is bundled. All API data is synthetic fixture data served from
``window.fixture``; nothing connects to a running desk, gateway, OBS or Twitch, and nothing here
proves a broadcast or a mute. Each state reads back the actual viewport, document overflow,
grid-item overlap, clipped text and key-control hit tests in the same evaluate as its capture.
Trusted input: a mouse click on a Copy button (clipboard read back), a mouse click on one
fixture line's Approve button (benign; checks the posted identity), and Tab / Shift+Tab across
every enabled control with a visible-focus check. Safety buttons are hit-tested, never fired.

Set ``DESK_SCREENSHOT_DIR`` to a directory outside the repository to keep the captures.
"""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE_MODULES = Path.home() / ".hermes" / "hermes-agent" / "node_modules"

BROWSER_TEST = r"""
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const esbuild = require('esbuild');
const OUT = process.env.DESK_SCREENSHOT_DIR || '';

const healthy = (extra) => Object.assign({mode:'approval', live:true, privacy:false, muted:false, epoch:7, seq:1, voice:false, tts:'none', pending:0,
  overlay_url:'http://127.0.0.1:18790/overlay', preflight:{ok:true, checks:[]}}, extra || {});
const LONG = 'W'.repeat(1200);
const many = Array.from({length:9}, (_, n) => ({line_id:'line-' + n, epoch:7, text: n === 1 ? LONG : 'Pending line ' + n + ': thanks for the question, here is a short answer.'}));
const STATES = {
  setup: {reachable:false},
  approval: {reachable:true, state:healthy({pending:9}), pending:many},
  preflight: {reachable:true, state:healthy({mode:'off_air', live:false, preflight:{ok:false, checks:[
    {name:'allowed_users', status:'fail', detail:'ONAIR_ALLOWED_USERS must be exactly onair-stage.'},
    {name:'toolsets', status:'fail', detail:'Set platform_toolsets.on_air to exactly [no_mcp].'},
    {name:'mystery_check', status:'fail', detail:'W'.repeat(300)}]}}), pending:[]},
  latched: {reachable:true, state:healthy({mode:'off_air', live:false, preflight:{ok:false, reason:'runtime_refused:tool_surface', checks:[
    {name:'runtime_latch', status:'fail', detail:'runtime_refused:tool_surface: the runtime gate refused an On Air provider request; fix the cause, then clear the latch from off air.'}]}}), pending:[]},
  privacy: {reachable:true, state:healthy({privacy:true, muted:true}), pending:[]},
  lost: {reachable:false},
};

(async () => {
  const executablePath = process.env.DESK_TEST_CHROMIUM || chromium.executablePath();
  if (!fs.existsSync(executablePath)) { console.log('SKIP: Playwright Chromium is not installed'); return; }
  const sdk = await esbuild.build({ stdin: { contents: `
      const React = require('react');
      const {createRoot} = require('react-dom/client');
      window.posts = [];
      window.__HERMES_PLUGIN_SDK__ = {React, fetchJSON: async (url, options) => {
        const f = window.fixture, p = url.replace('/api/plugins/on-air/', '');
        if (options) { window.posts.push([p, JSON.parse(options.body)]); if (!f.reachable) throw Error('503'); return {ok:true}; }
        if (!f.reachable) throw Error('503 On Air control unavailable');
        if (p === 'state') return JSON.parse(JSON.stringify(f.state));
        if (p === 'pending') return {epoch:f.state.epoch, pending:f.pending.slice()};
        if (p.startsWith('receipts')) return {receipts:[
          {kind:'line', disposition:'withheld', rule:'never_say', text:'A phrase was withheld.', ts:1700000000},
          {kind:'chat', verdict:'skip', rule:'duplicate', user:'River', text:'Repeated chat message.', ts:1700000001}]};
        if (p === 'alerts') return {alerts:{enabled:true, thank_you:true, include_messages:false, min_bits:1}};
        if (p === 'alerts/recent') return {alerts:[{kind:'raid', name:'River', amount:37}]};
        if (p === 'views/state') return {ok:true, connected:true, program_scene:'On Air: Boxes', stream:{active:false}, stage_profile:false,
          boxes:{slots:{main:'scene',sub1:'slides',sub2:'window'},arrangement:'side',available:{scene:{available:true},slides:{available:true},window:{available:true}}},
          window:{exists:true,ready:true},layout:{current:{},presets:{presentation:{ready:true},window:{ready:true},window_pip:{ready:true},side_by_side:{ready:true}}}};
        throw Error('Unexpected fixture route: ' + p);
      }};
      window.__HERMES_PLUGINS__ = {register: (name, Page) => createRoot(document.getElementById('root')).render(React.createElement(Page))};`,
    resolveDir: process.cwd() }, bundle: true, write: false, platform: 'browser', nodePaths: process.env.NODE_PATH.split(path.delimiter) });
  const html = '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{margin:0;font-family:Arial,sans-serif;background:#0f1a19;color:#ecefec}</style></head><body><a id="harness-start" href="#start" style="position:absolute;left:-10000px">harness start</a><div id="root"></div></body></html>';
  const browser = await chromium.launch({headless:true, executablePath});
  const report = [];
  try {
    for (const [width, height] of [[390,844], [1280,800], [1920,1080]]) {
      const context = await browser.newContext({viewport:{width, height}, deviceScaleFactor:1});
      await context.grantPermissions(['clipboard-read', 'clipboard-write'], {origin:'http://localhost'});
      const page = await context.newPage();
      const errors = [], consoleErrors = [];
      page.on('pageerror', e => errors.push(String(e)));
      page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') consoleErrors.push(m.text().slice(0, 300)); });
      await page.route('http://localhost/**', route => route.fulfill({status:200, contentType:'text/html', body:html}));
      await page.goto('http://localhost/desk');
      await page.evaluate(f => { window.fixture = f; }, STATES.setup);
      await page.addStyleTag({path:'dashboard/dist/style.css'});
      await page.addScriptTag({content:sdk.outputFiles[0].text});
      await page.addScriptTag({path:'dashboard/dist/index.js'});
      // Keyboard traversal in both directions with a visible focus ring on every stop.
      const traverse = async () => {
        // Start from a harness link placed before the desk, so Tab N times visits every desk control.
        await page.evaluate(() => { window.scrollTo(0, 0); document.getElementById('harness-start').focus(); });
        const expected = await page.evaluate(() => [...document.querySelectorAll('.onair-app button:not(:disabled), .onair-app a[href], .onair-app summary, .onair-app [tabindex="0"], .onair-app input:not(:disabled), .onair-app select:not(:disabled), .onair-app textarea:not(:disabled)')].filter(el => el.getClientRects().length && [...document.querySelectorAll('details:not([open])')].every(d => !d.contains(el) || el === d.querySelector(':scope > summary'))).length);
        const forward = [], back = [], bad = [];
        const probe = () => page.evaluate(() => {
          const el = document.activeElement, r = el.getBoundingClientRect(), cs = getComputedStyle(el);
          const hit = document.elementFromPoint(Math.min(innerWidth - 1, Math.max(0, r.left + Math.min(r.width / 2, 20))), Math.min(innerHeight - 1, Math.max(0, r.top + Math.min(r.height / 2, 10))));
          return {id:(el.getAttribute('aria-label') || el.textContent || el.tagName).slice(0, 60), inMain:!!el.closest('.onair-app'),
            focusRing:cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2,
            inView:r.bottom > 0 && r.top < innerHeight && r.left >= 0 && r.right <= innerWidth, hit:!!hit && (hit === el || el.contains(hit) || hit.contains(el))};
        });
        for (let i = 0; i < expected; i++) { await page.keyboard.press('Tab'); const p = await probe(); forward.push(p.id); if (!p.inMain || !p.focusRing || !p.inView || !p.hit) bad.push(['fwd', p]); }
        for (let i = 0; i < expected - 1; i++) { await page.keyboard.press('Shift+Tab'); const p = await probe(); back.push(p.id); if (!p.inMain || !p.focusRing || !p.inView || !p.hit) bad.push(['back', p]); }
        return {expected, forward, back, bad, reversed: JSON.stringify(back) === JSON.stringify(forward.slice(0, -1).reverse())};
      };
      const reachable = () => page.evaluate(() => {
        const scrollers = [...document.querySelectorAll('.onair-desk *')].filter(el => el.scrollTop).map(el => [el, el.scrollTop]);
        const controls = [...document.querySelectorAll('.onair-desk button, .onair-desk a, .onair-desk summary')]
          .filter(el => el.offsetParent !== null && !(el.closest('details:not([open])') && el.tagName !== 'SUMMARY'));
        const bad = [];
        for (const el of controls) {
          el.scrollIntoView({block:'center', inline:'nearest'});
          const r = el.getBoundingClientRect();
          const hit = document.elementFromPoint(r.left + r.width / 2, r.top + Math.min(r.height / 2, 20));
          if (!(r.width > 0 && r.height > 0 && r.left >= 0 && r.right <= innerWidth && hit && (hit === el || el.contains(hit))))
            bad.push({text:el.textContent.slice(0, 40), w:r.width, h:r.height, left:r.left, right:r.right, hit:hit && (hit.className || hit.tagName)});
        }
        // Leave the page as the capture found it: no inner list stays scrolled by this probe.
        document.querySelectorAll('.onair-desk *').forEach(el => { if (el.scrollTop) el.scrollTop = 0; });
        scrollers.forEach(([el, top]) => { el.scrollTop = top; });
        window.scrollTo(0, 0);
        return bad;
      });
      const shots = {};
      let clip = null, keyboardSetup = null, keyboardApproval = null, keyboardViews = null, approvePost = null;
      for (const name of ['setup', 'approval', 'preflight', 'latched', 'privacy', 'lost']) {
        await page.evaluate(f => { window.fixture = f; }, STATES[name]);
        await page.evaluate(() => window.scrollTo(0, 0));
        const want = {setup:'NOT CONNECTED', approval:'APPROVAL', preflight:'PREFLIGHT FAILED', latched:'RUNTIME LATCHED', privacy:'PRIVACY CUT ON', lost:'CONNECTION LOST'}[name];
        await page.waitForFunction(w => [...document.querySelectorAll('.onair-banner .onair-chip')].some(c => c.textContent === w), want, {timeout:5000});
        if (name === 'approval') await page.waitForFunction(() => document.querySelectorAll('.onair-line').length === 9);
        await page.waitForTimeout(150);
        if (name === 'setup') {
          await page.locator('.onair-setup-details > summary').click();
          await page.waitForFunction(() => document.querySelector('.onair-setup-details').open);
        }
        const snap = await page.evaluate(() => {
          window.scrollTo(0, 0);
          const rect = el => { const r = el.getBoundingClientRect(); return {left:r.left, top:r.top + scrollY, right:r.right, bottom:r.bottom + scrollY, width:r.width, height:r.height}; };
          const items = [...document.querySelectorAll('.onair-desk > *, .onair-grid > *')].filter(el => getComputedStyle(el).position !== 'absolute' && getComputedStyle(el).display !== 'contents');
          const overlaps = [];
          items.forEach((a, i) => items.slice(i + 1).forEach(b => {
            if (a.contains(b) || b.contains(a)) return;
            const x = rect(a), y = rect(b);
            if (x.height && y.height && Math.min(x.right, y.right) > Math.max(x.left, y.left) + .5 && Math.min(x.bottom, y.bottom) > Math.max(x.top, y.top) + .5)
              overlaps.push([a.className, b.className]);
          }));
          const clipped = [...document.querySelectorAll('.onair-cmd, .onair-template, .onair-alerts-more, .onair-line-text, .onair-preflight li, .onair-chip, .onair-stale, .onair-notice, h1, h2, button')]
            .filter(el => el.offsetParent !== null && (el.scrollWidth > el.clientWidth + 1))
            .map(el => el.className || el.tagName);
          const controls = [...document.querySelectorAll('.onair-desk button, .onair-desk a, .onair-desk summary')]
            .filter(el => el.offsetParent !== null && !(el.closest('details:not([open])') && el.tagName !== 'SUMMARY'));
          return {innerWidth, innerHeight, dpr:devicePixelRatio, scrollWidth:document.documentElement.scrollWidth,
            documentHeight:document.documentElement.scrollHeight, overlaps, clipped, controls:controls.length,
            small:controls.filter(el => el.tagName === 'BUTTON' && el.getBoundingClientRect().height < 44).map(el => el.textContent),
            chips:[...document.querySelectorAll('.onair-banner .onair-chip')].map(c => c.textContent),
            safety:[...document.querySelectorAll('.onair-safety button')].map(b => ({text:b.textContent, disabled:b.disabled, pressed:b.getAttribute('aria-pressed')})),
            privacyBanner:document.querySelector('.onair-privacy-banner')?.textContent || null,
            stateLine:document.querySelector('[aria-labelledby="onair-mode"] p:nth-of-type(2)')?.textContent || null,
            connect:document.querySelector('[aria-labelledby="onair-connect"]')?.textContent || null,
            alerts:document.querySelector('[aria-labelledby="onair-alerts"]')?.textContent || null,
            templates:[...document.querySelectorAll('.onair-template')].filter(el => el.offsetParent !== null).map(el => el.textContent),
            templateVarLines:[...document.querySelectorAll('.onair-template var')].filter(el => el.offsetParent !== null).map(el => el.getClientRects().length),
            notice:document.querySelector('.onair-notice')?.textContent || null};
        });
        if (OUT) {
          const file = path.join(OUT, `desk-${name}-${width}x${height}.png`);
          await page.screenshot({path:file, fullPage:true});
          const top = path.join(OUT, `desk-${name}-${width}x${height}-viewport.png`);
          await page.screenshot({path:top, fullPage:false});
          snap.files = [file, top];
        }
        snap.unreachable = await reachable();
        shots[name] = snap;
        if (name === 'lost') {
          // Trusted click on the setup disclosure; its controls must then be reachable too.
          const summary = page.locator('.onair-setup-details > summary');
          await summary.scrollIntoViewIfNeeded();
          const sbox = await summary.boundingBox();
          await page.mouse.click(sbox.x + 20, sbox.y + sbox.height / 2);
          await page.waitForFunction(() => document.querySelector('.onair-setup-details').open, null, {timeout:3000});
          snap.detailsOpen = true;
          snap.unreachableOpen = await reachable();
          snap.copyButtonsOpen = await page.evaluate(() => document.querySelectorAll('.onair-setup-details .onair-copy').length);
          if (OUT) { const f = path.join(OUT, `desk-lost-open-${width}x${height}.png`); await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({path:f, fullPage:true}); snap.files.push(f); }
        }
        shots[name] = snap;
        if (name === 'setup') {
          // Trusted input on the setup guide: mouse click on Copy, then the clipboard itself.
          const copy = page.locator('.onair-command', {hasText:'onair preflight'}).first().locator('button');
          await copy.scrollIntoViewIfNeeded();
          const box = await copy.boundingBox();
          await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
          await page.waitForFunction(() => [...document.querySelectorAll('.onair-copy')].some(b => b.textContent === 'Copied'), null, {timeout:3000});
          clip = await page.evaluate(() => navigator.clipboard.readText().catch(e => 'READ FAILED: ' + e));
          keyboardSetup = await traverse();
        }
        if (name === 'approval') {
          // Trusted click on one line's Approve (fixture-only), then the posted identity.
          await page.evaluate(() => { window.posts.length = 0; });
          const approve = page.locator('button[aria-label="Approve line 3"]');
          await approve.scrollIntoViewIfNeeded();
          const abox = await approve.boundingBox();
          await page.mouse.click(abox.x + abox.width / 2, abox.y + abox.height / 2);
          await page.waitForFunction(() => window.posts.length > 0, null, {timeout:3000});
          approvePost = await page.evaluate(() => window.posts[0]);
          keyboardApproval = await traverse();
          await page.locator('.onair-views-disclosure > summary').click();
          await page.waitForFunction(() => document.querySelector('select[aria-label="Main source"]:not(:disabled)'), null, {timeout:8000});
          keyboardViews = await traverse();
          await page.locator('.onair-views-disclosure > summary').click();
        }
      }
      report.push({width, height, shots, clip, keyboardSetup, keyboardApproval, keyboardViews, approvePost, errors, consoleErrors});
      await context.close();
    }
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
"""


def test_desk_journey_in_browser():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    paths = [str(NODE_MODULES), str(Path.home() / "node_modules")]
    env = dict(os.environ, NODE_PATH=os.pathsep.join(paths))
    probe = subprocess.run([node, "-e", "for(const m of ['playwright','esbuild','react','react-dom/client']) require.resolve(m)"],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    if probe.returncode:
        pytest.skip("Playwright, esbuild or React Node modules are unavailable")
    out = env.get("DESK_SCREENSHOT_DIR")
    if out:
        out_path = Path(out).resolve()
        assert ROOT not in out_path.parents and out_path != ROOT, "keep screenshots outside the repository"
        out_path.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([node, "--max-old-space-size=1536", "-e", BROWSER_TEST], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=170)
    assert result.returncode == 0, result.stdout + result.stderr
    if result.stdout.startswith("SKIP:"):
        pytest.skip(result.stdout.strip())
    report = json.loads(result.stdout)
    if out:
        # Bind the captures to the exact source they rendered.
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "dashboard/dist"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        sha = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in ("dashboard/dist/index.js", "dashboard/dist/style.css")}
        (Path(out) / "journey-report.json").write_text(json.dumps({"head": head, "bundle_uncommitted_changes": bool(dirty), "sha256": sha, "viewports": report}, indent=1))
    assert [(r["width"], r["height"]) for r in report] == [(390, 844), (1280, 800), (1920, 1080)]
    for r in report:
        label = f"{r['width']}x{r['height']}"
        assert r["errors"] == [], (label, r["errors"])
        for name, snap in r["shots"].items():
            where = (label, name)
            assert (snap["innerWidth"], snap["innerHeight"], snap["dpr"]) == (r["width"], r["height"], 1), where
            assert snap["scrollWidth"] <= snap["innerWidth"], (where, "document overflow", snap["scrollWidth"])
            assert snap["overlaps"] == [], (where, snap["overlaps"])
            assert snap["clipped"] == [], (where, snap["clipped"])
            assert snap["unreachable"] == [], (where, snap["unreachable"])
            assert snap["small"] == [], (where, "buttons under 44px", snap["small"])
            assert len(snap["safety"]) == 3, where
        setup, lost = r["shots"]["setup"], r["shots"]["lost"]
        assert setup["chips"] == ["NOT CONNECTED"] and "Connect this desk to a show" in setup["connect"], label
        assert setup["templates"] == ["hermes -p stage plugins install <git-url> --enable"], (label, setup["templates"])
        assert setup["templateVarLines"] == [1], (label, "the <git-url> placeholder must not break across lines", setup["templateVarLines"])
        assert lost["chips"] == ["CONNECTION LOST", "STATE UNKNOWN"] and "Last confirmed" in lost["connect"], label
        assert "All show controls are disabled" in lost["connect"] and "Every control is disabled" not in lost["connect"], (label, lost["connect"])
        assert lost["notice"].startswith("Last action: "), (label, "an old confirmation must read as history", lost["notice"])
        assert lost["detailsOpen"] and lost["copyButtonsOpen"] == 7 and lost["unreachableOpen"] == [], (label, lost.get("unreachableOpen"))
        assert "Alert settings unavailable" in lost["alerts"] and "Recent alerts unavailable" in lost["alerts"], (label, lost["alerts"])
        assert "River raided" not in lost["alerts"], (label, "stale recent alert", lost["alerts"])
        assert "River raided" in r["shots"]["approval"]["alerts"], label
        for snap in (setup, lost):
            assert all(b["disabled"] and b["pressed"] in (None, "false") for b in snap["safety"]), (label, snap["safety"])
            assert snap["privacyBanner"] is None, label
        privacy = r["shots"]["privacy"]
        assert privacy["privacyBanner"] == "PRIVACY CUT ON · viewers see 'Be right back' · audio blocked · pending cleared", label
        assert privacy["stateLine"].startswith("Privacy cut ON · Muted"), (label, privacy["stateLine"])
        assert "PRIVACY CUT ON" in privacy["chips"] and "MUTED" in privacy["chips"], label
        assert r["clip"] == "hermes -p stage onair preflight", (label, r["clip"])
        assert r["approvePost"] == ["approve", {"line_id": "line-2", "epoch": 7}], (label, r["approvePost"])
        for name in ("keyboardSetup", "keyboardApproval", "keyboardViews"):
            k = r[name]
            assert k["expected"] >= 5 and k["bad"] == [] and k["reversed"], (label, name, k)
        assert {"Main source", "Sub 1 source", "Sub 2 source", "Swap Main and Sub 1", "Stack", "Side"} <= set(r["keyboardViews"]["forward"]), label
