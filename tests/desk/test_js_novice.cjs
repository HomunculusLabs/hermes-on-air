// Novice journey contract in jsdom with a fake SDK and the real dashboard/dist/index.js:
// first run opens the wizard, tutorial Next follows confirmed practice facts only, Skip and
// Replay send nothing, and the unconnected desk offers setup/practice instead of a command wall.
// The real backend + real browser journey is exercised separately (see docs/desk.md).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/' });
global.window = dom.window;
global.document = dom.window.document;
global.IS_REACT_ACT_ENVIRONMENT = true;
const React = require('react');
const { createRoot } = require('react-dom/client');
const { act } = React;

let facts = { ok: true, running: false };
const posts = [];
const HOME_CMD = 'HERMES_HOME=/home/u/onair-stage hermes ';
const READINESS = { ok: true, home: '/home/u/onair-stage', summary: 'Settings saved. Ready to practise. Not ready for a real show yet: 3 steps left, in order below.',
  command_note: 'Elsewhere on the desk, "hermes -p stage …" is the same command for a stage made as a profile called stage.',
  rows: [{ key: 'plugin', label: 'On Air plugin in the stage', state: 'action', detail: 'Not installed.', step: 1 },
    { key: 'runtime', label: 'Stage running and reading chat', state: 'untested', detail: 'Not started.', step: 3 }],
  steps: [{ key: 'plugin', number: 1, title: 'Install On Air into the stage folder', note: 'Replace <git-url> with the Git address.', command: null,
    template: [HOME_CMD + 'plugins install ', '<git-url>', ' --enable'] },
  { key: 'preflight', number: 2, title: 'Run the safety check (preflight)', note: 'It does not connect to anything.', command: HOME_CMD + 'onair preflight', template: null },
  { key: 'gateway', number: 3, title: 'Start the stage', note: 'Keep that terminal open.', command: HOME_CMD + 'gateway run', template: null }] };
window.__HERMES_PLUGIN_SDK__ = { React, fetchJSON: async (url, options) => {
  const path = url.replace('/api/plugins/on-air/', '');
  if (options) {
    posts.push(path);
    if (path === 'practice/start') { facts = { ok: true, running: true, facts: { pending: 0, captions: 0, rejected: 0 } }; return facts; }
    if (path === 'setup/readiness') return READINESS;
    if (path === 'setup/plan') return { ok: true, target: '/home/u/onair-stage', summary: ['Create /home/u/onair-stage'], untouched: ['Nothing else changes.'], creates: ['/home/u/onair-stage/config.yaml'], config_preview: 'onair: {}' };
    if (path === 'setup/apply') return { ok: true, home: '/home/u/onair-stage', readiness: READINESS };
    return { ok: true };
  }
  if (path === 'setup/status') return { ok: true, desk_connected: false, current_home_is_stage: false, suggested_target: '/home/u/onair-stage', practice_running: false };
  if (path === 'practice/facts') return facts;
  throw Error('503 On Air control unavailable');
} };
let Page;
window.__HERMES_PLUGINS__ = { register: (name, component) => { Page = component; } };
vm.runInThisContext(fs.readFileSync(require('node:path').join(__dirname, '../../dashboard/dist/index.js'), 'utf8'));

const flush = async (ms = 30) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); };
const text = () => document.body.textContent;
const button = (name) => [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === name);
const click = async (name) => { const b = button(name); assert(b, 'no button ' + name + ' in: ' + text().slice(0, 300)); await act(async () => { b.click(); }); await flush(); };

(async () => {
  const root = createRoot(document.getElementById('root'));
  await act(async () => { root.render(React.createElement(Page)); });
  await flush(80);
  // Start here is still available from the default Live tab.
  await act(async () => { document.querySelector('.onair-secondary summary').click(); });
  await click('Start here');
  assert(text().includes('Start On Air') && button('Set up & preview'), 'first run should open the quick start');
  assert(text().includes('ask your own Hermes agent'), 'quick start names the agent-first path');
  await click('Step-by-step setup instead');
  assert(text().includes('Step 1 of 6: Welcome'), 'step-by-step setup should open the wizard');
  assert(button('Try a practice show') && button('Set up my Twitch co-host'));

  await click('Try a practice show');
  await flush(1100);
  assert(text().includes('Start the practice show'));
  const next = () => [...document.querySelectorAll('.onair-tour button')].find((b) => b.textContent === 'Next');
  assert(next().disabled, 'Next must wait for the practice show');
  assert.equal([...document.querySelectorAll('button')].filter((b) => b.textContent.trim() === 'Start practice show').length, 1, 'one start action');
  const before = posts.length;
  await click('Skip tutorial');
  await click('Replay tutorial');
  assert.equal(posts.length, before, 'skip/replay must not send anything');
  assert(next().disabled);

  await click('Start practice show');
  assert.deepEqual(posts.slice(before), ['practice/start']);
  facts = { ok: true, running: true, facts: { pending: 1, captions: 0, rejected: 0, messages_sent: 1, messages_total: 3 } };
  await flush(1100);
  assert(!next().disabled, 'Next unlocks after a draft is actually waiting');
  facts = { ok: true, running: true, facts: { pending: 0, approved: 1, captions: 1, last_caption: 'Hello caption', rejected: 0, messages_sent: 1, messages_total: 3 } };
  await flush(1100);
  assert(!next().disabled, 'Next stays unlocked after the draft has already been approved');
  await act(async () => { next().click(); });
  await flush();
  assert(text().includes('Approve the draft'));
  assert(!next().disabled, 'an already approved caption completes step two');
  assert(text().includes('Hello caption'), 'history retains the emitted caption');
  assert(text().includes('Current caption: open the practice overlay to check.'));
  facts = { ok: true, running: true, facts: { mode: 'paused', pending: 0, approved: 1, captions: 1, last_caption: 'Hello caption', rejected: 0, messages_sent: 1, messages_total: 3 } };
  await flush(1100);
  assert(text().includes('Current overlay: cleared.') && text().includes('Last emitted caption:'), 'cleared output and history must be distinguished');
  await act(async () => { next().click(); });
  await flush();
  await act(async () => { next().click(); });
  await flush();
  assert.equal([...document.querySelectorAll('button')].filter((b) => b.textContent.trim() === 'Bring in the next message').length, 1, 'one next-message action');
  assert(!posts.some((p) => /^(approve|reject|privacy|mute|stop|mode)$/.test(p)), 'no real desk control was posted');

  await act(async () => { document.querySelector('.onair-secondary summary').click(); });
  await click('Full desk (advanced)');
  await flush(80);
  assert(text().includes('NOT CONNECTED'));
  assert(button('Open the setup wizard') && button('Try a practice show'), 'unconnected desk offers setup and practice');
  await click('Open the setup wizard');
  assert(text().includes('Start On Air') || text().includes('Step '), 'desk reopens setup');
  await act(async () => { root.unmount(); });

  // Step 6 remaining actions: exact commands via the shared command view with a working Copy;
  // templates are marked "Fill in" and never copyable; nothing breaks words mid-word.
  const copiedValues = [];
  const clip = { writeText: async (v) => { copiedValues.push(v); } };
  for (const nav of [window.navigator, globalThis.navigator]) Object.defineProperty(nav, 'clipboard', { value: clip, configurable: true });
  window.localStorage.setItem('onair.setup.v1', JSON.stringify({ step: 4, answers: { target: '/home/u/onair-stage' } }));
  window.localStorage.setItem('onair.tab.v1', JSON.stringify({ tab: 'setup' }));
  const root2 = createRoot(document.getElementById('root'));
  await act(async () => { root2.render(React.createElement(Page)); });
  await flush(80);
  const box = document.querySelector('.onair-confirm input');
  assert(box, 'review step shows the confirmation: ' + text().slice(0, 300));
  await act(async () => { box.click(); });
  await click('Create stage folder');
  await flush(80);
  assert(posts.includes('setup/apply'), 'confirmed apply');
  assert(text().includes('What is left, in this order'), text().slice(0, 400));
  assert(!text().includes('<on-air source>'));
  const items = [...document.querySelectorAll('.onair-ready-steps > li')];
  assert.equal(items.length, READINESS.steps.length, 'one list item per remaining step');
  const tpl = items[0];
  assert(tpl.querySelector('kbd.onair-template var').textContent === '<git-url>');
  assert(tpl.textContent.includes('Fill in') && !tpl.querySelector('button.onair-copy'), 'template is not copyable');
  const gw = items[2];
  assert.equal(gw.querySelector('kbd.onair-cmd').textContent, HOME_CMD + 'gateway run');
  const copyBtn = gw.querySelector('button.onair-copy');
  assert(copyBtn && copyBtn.getAttribute('aria-label') === 'Copy command: ' + HOME_CMD + 'gateway run');
  await act(async () => { copyBtn.click(); });
  await flush();
  assert.deepEqual(copiedValues, [HOME_CMD + 'gateway run'], 'Copy writes the exact command');
  assert.equal(gw.querySelector('button.onair-copy').textContent, 'Copied');
  assert(text().includes('step 1 below') && text().includes('step 3 below'), 'rows point at the numbered steps');
  const css = fs.readFileSync(require('node:path').join(__dirname, '../../dashboard/dist/style.css'), 'utf8');
  assert(!/break-all/.test(css), 'no rule may split command words mid-word');
  const cmdRule = css.split('\n').find((l) => l.includes('.onair-wizard kbd.onair-cmd'));
  assert(cmdRule && /white-space:\s*pre-wrap/.test(cmdRule) && /overflow-wrap:\s*break-word/.test(cmdRule), 'wizard commands wrap at spaces');
  await act(async () => { root2.unmount(); });
  console.log('novice journey: all checks passed');
})().catch((error) => { console.error(error); process.exit(1); });
