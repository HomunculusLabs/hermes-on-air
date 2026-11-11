"""Browser-level regression for the real desk bundle and stylesheet.

Requires Node, Playwright, esbuild, React and a Playwright Chromium installation.
All API data is local fixture data; this test never connects to a running desk.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE_MODULES = Path(os.environ.get("ONAIR_HERMES_SDK", str(Path.home() / ".hermes" / "hermes-agent"))) / "node_modules"

# Bundling only supplies the host's React SDK. The desk JS and CSS are loaded
# directly from dashboard/dist; no production layout or component is replaced.
BROWSER_TEST = r"""
const fs = require('node:fs');
const { chromium } = require('playwright');
const esbuild = require('esbuild');

(async () => {
  const executablePath = process.env.DESK_TEST_CHROMIUM || chromium.executablePath();
  if (!fs.existsSync(executablePath)) {
    console.log('SKIP: Playwright Chromium is not installed');
    return;
  }
  const bundle = await esbuild.build({
    stdin: { contents: `
      const React = require('react');
      const {createRoot} = require('react-dom/client');
      const pending = window.pendingFixture = [
        {line_id:'line-1', epoch:7, text:'Welcome everyone, thanks for joining the stream.'},
        {line_id:'line-2', epoch:7, text:'Let us take a look at the next question from chat.'},
        {line_id:'line-3', epoch:7, text:'That is a good point; we can come back to it shortly.'}
      ];
      const receipts = [
        {kind:'line', disposition:'withheld', rule:'never_say', text:'A phrase was withheld.', ts:1700000000},
        {kind:'chat', verdict:'skip', rule:'duplicate', user:'River', text:'Repeated chat message.', ts:1700000001},
        {kind:'line', disposition:'withheld', rule:'privacy', text:'Private details withheld.', ts:1700000002},
        {kind:'chat', verdict:'skip', rule:'slur', user:'Sky', text:'Message held for review.', ts:1700000003},
        {kind:'line', disposition:'withheld', rule:'injection', text:'Instruction attempt withheld.', ts:1700000004}
      ];
      window.__HERMES_PLUGIN_SDK__ = {React, fetchJSON: async (url) => {
        const path = url.replace('/api/plugins/on-air/', '');
        if (path === 'state') return {mode:'approval', privacy:false, muted:false, epoch:7, live:true, seq:3, voice_boot:'a'.repeat(32), host_turns:{enabled:true}, overlay_url:'http://127.0.0.1:18790/overlay'};
        if (path === 'pending') return {epoch:7, pending:pending.slice()};
        if (path.startsWith('receipts')) return {receipts};
        if (path === 'alerts') return {alerts:{enabled:true, thank_you:true, include_messages:false, min_bits:1, during_privacy:'drop'}};
        if (path === 'alerts/recent') return {alerts:[{kind:'raid', name:'River', amount:37}, {kind:'bits', name:'Sky', amount:100}]};
        throw Error('Unexpected fixture route: ' + path);
      }};
      window.__HERMES_PLUGINS__ = {register: (name, Page) => {
        if (name !== 'on-air') throw Error('Unexpected plugin: ' + name);
        createRoot(document.getElementById('root')).render(React.createElement(Page));
      }};`, resolveDir: process.cwd() },
    bundle: true, write: false, platform: 'browser', nodePaths: process.env.NODE_PATH.split(require('node:path').delimiter)
  });
  let browser;
  try { browser = await chromium.launch({headless:true, executablePath}); }
  catch (error) {
    if (/Executable doesn't exist|browserType.launch: Executable/.test(String(error))) {
      console.log('SKIP: Playwright Chromium is not installed'); return;
    }
    throw error;
  }
  try {
    const snapshots = [];
    for (const [width, height] of [[1280,800], [1440,900], [1920,1080]]) {
      const page = await browser.newPage({viewport:{width,height}});
      await page.setContent('<!doctype html><html><head><style>body{margin:0;font-family:Arial,sans-serif}</style></head><body><div id="root"></div></body></html>');
      await page.addStyleTag({path:'dashboard/dist/style.css'});
      await page.addScriptTag({content:bundle.outputFiles[0].text});
      await page.addScriptTag({path:'dashboard/dist/index.js'});
      await page.getByRole('button', {name:'Details', exact:true}).click();
      await page.waitForFunction(() => document.querySelectorAll('[aria-labelledby="onair-queue"] .onair-line').length === 3 && document.querySelectorAll('[aria-labelledby="onair-guard"] .onair-guard-entry').length > 0);
      await page.waitForTimeout(200);
      const talk=page.locator('.onair-talk-disclosure');
      if (await talk.count() !== 1) throw Error('Talk must be available');
      const armed=await page.locator('.onair-chip.onair-live').count()>0;
      if (await talk.evaluate(el => el.open) !== armed) throw Error('Talk starts open only for an armed show');
      // One synchronous read per viewport: no scrolling or individual locator moves.
      snapshots.push(await page.evaluate(() => {
        const rect = el => { const r = el.getBoundingClientRect(); return {left:r.left, top:r.top, right:r.right, bottom:r.bottom, width:r.width, height:r.height}; };
        const panels = [...document.querySelectorAll('.onair-panel')].filter(el => !el.closest('details:not([open])')).map(el => {
          const r = rect(el), x = (r.left+r.right)/2, y = (r.top+r.bottom)/2;
          const inViewport = x >= 0 && x < innerWidth && y >= 0 && y < innerHeight;
          const hit = inViewport ? document.elementFromPoint(x,y) : null;
          return {name:el.getAttribute('aria-labelledby'), rect:r, inViewport, centerHit:!inViewport || !!hit && (el === hit || el.contains(hit)), hit:hit && (hit.id || hit.className || hit.tagName)};
        });
        const guard = document.querySelector('[aria-labelledby="onair-guard"]');
        const list = guard.querySelector('.onair-events');
        const queue = document.querySelector('[aria-labelledby="onair-queue"]');
        const queueList = queue.querySelector('.onair-list');
        const queueRows = [...queueList.querySelectorAll('.onair-line')].map(row => ({
          rect:rect(row), buttons:[...row.querySelectorAll('button')].map(button => ({
            label:button.getAttribute('aria-label'), rect:rect(button)
          }))
        }));
        return {width:innerWidth, height:innerHeight, scrollX, scrollY,
          documentWidth:document.documentElement.scrollWidth, panels,
          header:rect(document.getElementById('onair-guard')),
          list:rect(list), listScrollTop:list.scrollTop, rows:[...list.querySelectorAll('.onair-guard-entry')].map(rect),
          queue:rect(queue), talk:rect(document.querySelector('.onair-talk-disclosure')), queueList:rect(queueList), queueRows};
      }));
      // Check the other real panels with Advanced open; closed disclosure content has no visible box.
      await page.locator('.onair-advanced > summary').click();
      snapshots[snapshots.length - 1].advancedPanels = await page.evaluate(() => [...document.querySelectorAll('.onair-panel')]
        .filter(el => !el.closest('details:not([open])')).map(el => {
          const r = el.getBoundingClientRect();
          return {name:el.getAttribute('aria-labelledby'), rect:{left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height}};
        }));
      await page.locator('.onair-advanced > summary').click();
      await page.evaluate(() => { window.scrollTo(0, 0); for (let i=4; i<=10; i++) window.pendingFixture.push({line_id:'line-'+i, epoch:7, text:'A pending line '.repeat(i)}); });
      await page.waitForFunction(() => document.querySelectorAll('.onair-line').length === 10);
      await page.waitForTimeout(150);
      const initial = await page.evaluate(() => {
        const list = document.querySelector('.onair-queue-scroll');
        const bottom = list.getBoundingClientRect().bottom;
        return {below:[...list.children].filter(row => row.getBoundingClientRect().bottom > bottom + 0.5).length,
          hint:document.querySelector('.onair-scroll-hint')?.textContent || ''};
      });
      if (!initial.below || !initial.hint.includes(String(initial.below)+' more')) throw Error(JSON.stringify(initial));
      for (let i=0; i<9; i++) await page.keyboard.press('ArrowDown');
      await page.waitForTimeout(150);
      const overflow = await page.evaluate(() => {
        const list = document.querySelector('.onair-queue-scroll');
        const box = list.getBoundingClientRect();
        const rows = [...list.children];
        const selected = list.querySelector('.onair-selected').getBoundingClientRect();
        const below = rows.filter(row => row.getBoundingClientRect().bottom > box.bottom + 0.5).length;
        return {selectedVisible: selected.top >= box.top - 0.5 && selected.bottom <= box.bottom + 0.5,
          selected:{top:selected.top,bottom:selected.bottom,height:selected.height,width:selected.width},
          box:{top:box.top,bottom:box.bottom,height:box.height},
          scrollTop:list.scrollTop, below, hint:document.querySelector('.onair-scroll-hint')?.textContent || ''};
      });
      if (!overflow.selectedVisible || overflow.scrollTop <= 0 || (overflow.below ? !overflow.hint.includes(String(overflow.below)+' more') : !!overflow.hint)) throw Error(JSON.stringify(overflow));
      await page.close();
    }
    console.log(JSON.stringify(snapshots));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
"""


def test_latched_panel_contrast():
    """The warning and clear control remain readable under light and dark host themes."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    env = dict(os.environ, NODE_PATH=os.pathsep.join([str(NODE_MODULES), str(Path.home() / "node_modules"), os.environ.get("NODE_PATH", "")]))
    probe = subprocess.run([node, "-e", "for(const m of ['playwright','esbuild','react','react-dom/client']) require.resolve(m)"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    if probe.returncode:
        pytest.skip("Playwright, esbuild or React Node modules are unavailable")
    script = BROWSER_TEST.replace(
        "mode:'approval', privacy:false, muted:false, epoch:7, live:true, seq:3",
        "mode:'off_air', privacy:false, muted:false, epoch:7, live:false, seq:3, preflight:{ok:false,reason:'runtime_refused:tool_surface',checks:[{name:'runtime_latch',status:'fail',detail:'Fix the cause before clearing.'}]}",
    ).replace(
        "await page.waitForTimeout(200);",
        """await page.waitForTimeout(200);
        for (const theme of ['light', 'dark']) {
          await page.evaluate(theme => {
            document.documentElement.style.setProperty('--color-foreground', theme === 'light' ? '#111' : '#eee');
            document.documentElement.style.setProperty('--color-muted-foreground', theme === 'light' ? '#555' : '#aaa');
            document.documentElement.style.setProperty('--color-secondary', theme === 'light' ? '#eee' : '#333');
          }, theme);
          const result = await page.evaluate(() => {
            const panel = document.querySelector('.onair-preflight');
            const button = panel.querySelector('button');
            const help = panel.querySelector('.onair-help');
            const rgb = s => s.match(/\\d+/g).slice(0,3).map(Number).map(x => { x /= 255; return x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4; });
            const lum = s => { const c = rgb(s); return .2126*c[0] + .7152*c[1] + .0722*c[2]; };
            const contrast = (a,b) => { const x=lum(a), y=lum(b); return (Math.max(x,y)+.05)/(Math.min(x,y)+.05); };
            const ps=getComputedStyle(panel), bs=getComputedStyle(button), hs=getComputedStyle(help);
            const badge = [...document.querySelectorAll('.onair-chip')].find(el => el.textContent === 'RUNTIME LATCHED');
            const cs = getComputedStyle(badge);
            if (cs.backgroundColor === 'rgba(0, 0, 0, 0)') throw Error('Latch badge lacks an opaque backing');
            return [contrast(ps.color,ps.backgroundColor),contrast(hs.color,ps.backgroundColor),contrast(bs.color,bs.backgroundColor),contrast(cs.color,cs.backgroundColor)];
          });
          if (result.some(value => value < 4.5)) throw Error('Latch contrast '+theme+': '+result);
        }
        """,
    )
    result = subprocess.run([node, "-e", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    if result.stdout.startswith("SKIP:"):
        pytest.skip(result.stdout.strip())
    assert len(json.loads(result.stdout)) == 3


def test_desk_layout_regression():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    paths = [str(NODE_MODULES), str(Path.home() / "node_modules")]
    if os.environ.get("NODE_PATH"):
        paths.append(os.environ["NODE_PATH"])
    env = dict(os.environ, NODE_PATH=os.pathsep.join(paths))
    probe = subprocess.run(
        [node, "-e", "for(const m of ['playwright','esbuild','react','react-dom/client']) require.resolve(m)"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    if probe.returncode:
        pytest.skip("Playwright, esbuild or React Node modules are unavailable")
    result = subprocess.run(
        [node, "-e", BROWSER_TEST], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if result.stdout.startswith("SKIP:"):
        pytest.skip(result.stdout.strip())
    snapshots = json.loads(result.stdout)
    assert len(snapshots) == 3
    expected_panels = {f"onair-{name}" for name in ("safety", "queue", "guard", "mode")}

    def contained(inner, outer):
        return (inner['width'] > 0 and inner['height'] > 0
                and inner['left'] >= outer['left'] and inner['right'] <= outer['right']
                and inner['top'] >= outer['top'] and inner['bottom'] <= outer['bottom'])

    for snap in snapshots:
        label = f"{snap['width']}x{snap['height']}"
        assert (snap['scrollX'], snap['scrollY'], snap['listScrollTop']) == (0, 0, 0), label
        assert snap['documentWidth'] <= snap['width'], f"{label}: horizontal overflow: {snap['documentWidth']}"
        assert abs(snap['talk']['top'] - snap['queue']['top']) <= 1 and snap['talk']['right'] <= snap['queue']['left'], (label, 'Talk must be beside the approval queue', snap['talk'], snap['queue'])
        panels = snap['panels']
        expanded = snap['advancedPanels']
        assert {f"onair-{name}" for name in ("safety", "queue", "guard", "mode", "alerts", "receipts")} <= {panel['name'] for panel in expanded}, label
        for i, panel in enumerate(expanded):
            a = panel['rect']
            assert a['width'] > 0 and a['height'] > 0, (label, panel)
            for other in expanded[i + 1:]:
                b = other['rect']
                assert not (min(a['right'], b['right']) > max(a['left'], b['left']) and min(a['bottom'], b['bottom']) > max(a['top'], b['top'])), (label, panel, other)
        # Safety is also a panel but has its own heading; include every visible panel.
        assert expected_panels <= {panel['name'] for panel in panels}, label
        for i, panel in enumerate(panels):
            a = panel['rect']
            assert a['width'] > 0 and a['height'] > 0, (label, panel)
            assert panel['centerHit'], (label, panel)
            for other in panels[i + 1:]:
                b = other['rect']
                overlap = min(a['right'], b['right']) > max(a['left'], b['left']) and min(a['bottom'], b['bottom']) > max(a['top'], b['top'])
                assert not overlap, (label, panel, other)
        assert len(snap['rows']) >= 1, (label, snap['rows'])
        for row in snap['rows']:
            assert contained(row, snap['list']), (label, 'guard row outside list', row, snap['list'])
        if snap['width'] == 1440:
            viewport = {'left': 0, 'top': 0, 'right': snap['width'], 'bottom': snap['height']}
            assert len(snap['queueRows']) == 3, (label, snap['queueRows'])
            for index, row in enumerate(snap['queueRows'], 1):
                assert contained(row['rect'], snap['queueList']), (label, 'queue row outside list', row)
                assert contained(row['rect'], snap['queue']), (label, 'queue row outside panel', row)
                assert contained(row['rect'], viewport), (label, 'queue row outside viewport', row)
                assert len(row['buttons']) == 2, (label, row)
                assert {button['label'] for button in row['buttons']} == {f'Approve line {index}', f'Reject line {index}'}, (label, row)
                for button in row['buttons']:
                    assert contained(button['rect'], row['rect']), (label, 'queue button outside row', button, row)
        if snap['width'] in (1440, 1920):
            header = snap['header']
            assert header['width'] > 0 and header['height'] > 0 and header['top'] >= 0 and header['bottom'] <= snap['height'] and header['left'] >= 0 and header['right'] <= snap['width'], (label, header)
            visible_rows = [r for r in snap['rows'] if r['width'] > 0 and r['height'] > 0 and r['top'] >= max(0, snap['list']['top']) and r['bottom'] <= min(snap['height'], snap['list']['bottom']) and r['left'] >= 0 and r['right'] <= snap['width']]
            minimum = 2 if snap['width'] == 1440 else 4
            assert len(visible_rows) >= minimum, (label, f"{len(visible_rows)} full guard rows; need {minimum}", snap)
