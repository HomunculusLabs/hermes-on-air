// Desk preflight panel and the runtime-latch clear, in jsdom with a fake SDK.
// Run by tests/desk/test_js_runner.py (node + the dashboard's node_modules).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/' });
global.window = dom.window;
global.document = dom.window.document;
global.navigator = dom.window.navigator;
global.IS_REACT_ACT_ENVIRONMENT = true;
const React = require('react');
const { createRoot } = require('react-dom/client');
const { act } = React;

const LATCH_DETAIL = 'runtime_refused:tool_surface: the runtime gate refused an On Air provider request; fix the cause, then clear the latch from off air.';
const latched = () => ({ ok: false, reason: 'runtime_refused:tool_surface', checks: [
  { name: 'enabled', status: 'pass', detail: 'On Air platform is enabled.' },
  { name: 'runtime_gate', status: 'pass', detail: 'gate registered' },
  { name: 'tts', status: 'warn', detail: 'TTS availability is not verified.' },
  { name: 'runtime_latch', status: 'fail', detail: LATCH_DETAIL },
] });
const passing = () => ({ ok: true, checks: [
  { name: 'runtime_gate', status: 'pass', detail: 'gate registered' },
  { name: 'tts', status: 'warn', detail: 'TTS availability is not verified.' },
] });

let Page;
let mode = 'off_air';
let preflight = latched();
let posts = [];
let clearReply = null; // null -> the real controller's behavior below
let receipts = [];
window.__HERMES_PLUGIN_SDK__ = { React, fetchJSON: async (url, options) => {
  const path = url.replace('/api/plugins/on-air/', '');
  if (options) {
    posts.push([path, JSON.parse(options.body)]);
    if (path === 'runtime/clear') {
      if (clearReply) return clearReply;
      if (mode !== 'off_air') return { ok: false, error: 'go off air before clearing the runtime latch' };
      if (!preflight || !preflight.reason) return { ok: false, error: 'the runtime gate is not latched' };
      preflight = passing();
      receipts.push({ kind: 'runtime_cleared', rule: 'tool_surface', epoch: 3 });
      return { ok: true, state: { mode, preflight } };
    }
    return { ok: true };
  }
  if (path === 'state') return { mode, privacy: false, muted: false, epoch: 3, live: mode === 'approval' || mode === 'autonomous', seq: 1, preflight, overlay_url: '' };
  if (path === 'pending') return { epoch: 3, pending: [] };
  if (path.startsWith('receipts')) return { receipts };
  if (path === 'alerts') return { alerts: { enabled: true, thank_you: false, include_messages: false, min_bits: 1 } };
  if (path === 'alerts/recent') return { alerts: [] };
  throw Error('Unexpected route ' + path);
} };
window.__HERMES_PLUGINS__ = { register: (name, page) => { assert.equal(name, 'on-air'); Page = page; } };
vm.runInThisContext(fs.readFileSync('dashboard/dist/index.js', 'utf8'));
const root = createRoot(document.getElementById('root'));
const settle = () => new Promise(resolve => setTimeout(resolve, 20));
const poll = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
const panel = () => document.querySelector('.onair-preflight');
const clearButton = () => [...document.querySelectorAll('button')].find(b => b.textContent === 'Clear runtime latch');
const banner = () => document.querySelector('.onair-banner').textContent;

(async () => {
  receipts = [{ kind: 'runtime_refused', rule: 'tool_surface', epoch: 2, session: 's-1' }];
  await act(async () => { root.render(React.createElement(Page)); await settle(); });
  await act(async () => { document.querySelector('.onair-secondary summary').click(); [...document.querySelectorAll('.onair-secondary button')].find(b => b.textContent === 'Full desk (advanced)').click(); await settle(); });

  // Latched and off air: the reason, the failing check only, and the clear button.
  assert(panel(), 'preflight panel shown');
  assert.equal(panel().getAttribute('role'), 'alert');
  assert(panel().textContent.includes('Runtime gate latched'));
  assert(panel().textContent.includes('runtime_refused:tool_surface'));
  const checks = [...panel().querySelectorAll('.onair-preflight-checks li')];
  assert.equal(checks.length, 1);
  assert.equal(checks[0].querySelector('code').textContent, 'runtime_latch');
  assert(checks[0].textContent.includes('clear the latch from off air'));
  for (const hidden of ['runtime_gate', 'enabled', 'TTS availability']) assert(!panel().textContent.includes(hidden), hidden);
  assert(banner().includes('RUNTIME LATCHED'));
  assert(clearButton() && !clearButton().disabled);
  const receiptPanel = document.querySelector('[aria-labelledby="onair-receipts"]');
  assert(receiptPanel.textContent.includes('Runtime gate refused a provider request (Tool surface)'));

  // A refused clear keeps the panel and says why.
  clearReply = { ok: false, error: 'the runtime gate is not latched' };
  await act(async () => { clearButton().click(); await settle(); });
  assert.deepEqual(posts, [['runtime/clear', {}]]);
  assert(document.querySelector('.onair-notice').textContent.includes('runtime/clear NOT confirmed: the runtime gate is not latched'));
  assert(panel() && clearButton());

  // The clear: posts an empty object, then the refreshed state shows no panel or button.
  clearReply = null;
  await act(async () => { clearButton().click(); await settle(); });
  assert.deepEqual(posts[1], ['runtime/clear', {}]);
  assert(document.querySelector('.onair-notice').textContent.includes('runtime/clear confirmed'));
  assert.equal(panel(), null);
  assert.equal(clearButton(), undefined);
  assert(!banner().includes('RUNTIME LATCHED') && !banner().includes('PREFLIGHT FAILED'));
  assert(receiptPanel.textContent.includes('Runtime latch cleared (Tool surface)'));

  // Latched while not off air (forced): the reason shows, the button does not.
  for (const m of ['approval', 'paused', 'autonomous']) {
    mode = m; preflight = latched();
    await poll();
    assert(panel() && panel().textContent.includes('runtime_refused:tool_surface'), m);
    assert.equal(clearButton(), undefined, m);
    assert(panel().textContent.includes('Go off air to clear the runtime latch'), m);
  }

  // A failing check without a runtime reason (e.g. no gate at startup): shown, no clear button.
  mode = 'off_air';
  preflight = { ok: false, checks: [
    { name: 'runtime_gate', status: 'fail', detail: 'The runtime gate is not registered <img src=x onerror=alert(1)>' },
    { name: 'runtime_tools', status: 'fail', detail: 'Tool isolation needs the runtime gate; see runtime_gate.' },
    { name: 'tts', status: 'warn', detail: 'not verified' },
  ] };
  await poll();
  assert(panel().textContent.includes('Preflight failed'));
  assert.deepEqual([...panel().querySelectorAll('li code')].map(c => c.textContent), ['runtime_gate', 'runtime_tools']);
  assert(panel().textContent.includes('<img src=x onerror=alert(1)>'));
  assert.equal(document.querySelector('img'), null);
  assert(!panel().textContent.includes('Reason:'));
  assert(banner().includes('PREFLIGHT FAILED'));
  assert.equal(clearButton(), undefined);

  // The post-clear refresh exception is a failed preflight, not a remaining latch.
  mode = 'off_air';
  preflight = {ok:false, reason:'preflight_error', checks:[{name:'preflight',status:'fail',detail:'Unable to verify safety; retry arming after resolving the preflight error.'}]};
  await poll();
  assert(panel().textContent.includes('Preflight failed'));
  assert(panel().textContent.includes('preflight_error'));
  assert(banner().includes('PREFLIGHT FAILED') && !banner().includes('RUNTIME LATCHED'));
  assert.equal(clearButton(), undefined);

  // A reason that is not a runtime latch never offers the clear.
  preflight = { ok: false, reason: 'startup_failed', checks: [] };
  await poll();
  assert(panel().textContent.includes('startup_failed'));
  assert.equal(clearButton(), undefined);

  // Malformed or passing preflight: nothing to show.
  for (const value of [null, 'bad', 7, [], { ok: true, checks: 'x' }, { ok: false, reason: 5, checks: [null, 'x', { status: 'warn' }] }, passing()]) {
    preflight = value;
    await poll();
    assert.equal(panel(), null, JSON.stringify(value));
    assert.equal(clearButton(), undefined, JSON.stringify(value));
  }
  assert.equal(posts.length, 2);
  await act(async () => root.unmount());
  console.log('preflight: latch reason, failing checks, clear button off air only, refused clear, safe text passed');
})().catch(e => { console.error(e); root.unmount(); process.exitCode = 1; });
