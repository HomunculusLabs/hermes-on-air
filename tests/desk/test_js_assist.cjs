// Desk semantic assistance (shadow only), in jsdom with a fake SDK.
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

const NOTE = 'Shadow only: recommendations never approve, reject or air anything. Only your Approve sends a line.';
let posts = [];
let assistRoute = 'laya'; // laya | off | down
let pending = [
  { line_id: 'l-1', epoch: 5, turn_id: 't-1', text: 'Tonight it is a lamp.', created_at: 1,
    assist: { pending: false, recommendation: 'approve', status: 'ok', provider: 'laya', confidence: 0.95, latency_ms: 31.2,
      basis: 'no injection/exfil detected · Laya checks injection/secret-exfil only; show policy not evaluated', shadow: true } },
  { line_id: 'l-2', epoch: 5, turn_id: 't-1', text: 'Welcome in!', created_at: 2, assist: { pending: true } },
  { line_id: 'l-3', epoch: 5, turn_id: 't-1', text: 'Odd one.', created_at: 3,
    assist: { pending: false, recommendation: 'approve!!', status: 'ok', provider: 'laya', confidence: 2, shadow: true } },
];
const status = (provider, state) => ({ provider, state, shadow_only: true, endpoint: provider === 'laya' ? 'http://127.0.0.1:8390' : '',
  detail: provider === 'off' ? 'Semantic assistance is off. Manual approval works as usual.' : 'Laya flags prompt injection.',
  last_test: null, counters: { requested: 3, attached: 2, unavailable: 0, stale_discarded: 1 }, note: NOTE });
window.__HERMES_PLUGIN_SDK__ = { React, fetchJSON: async (url, options) => {
  const path = url.replace('/api/plugins/on-air/', '');
  if (options) {
    posts.push([path, JSON.parse(options.body)]);
    if (path === 'assist/test') return { ok: true, test: { ok: true, info: { model: 'convaiinnovations/laya' }, latency_ms: 5 }, assist: status('laya', 'shadow') };
    if (path === 'approve') { pending = pending.filter((l) => l.line_id !== JSON.parse(options.body).line_id); return { ok: true, state: {} }; }
    return { ok: true };
  }
  if (path === 'state') return { mode: 'approval', privacy: false, muted: false, epoch: 5, live: true, seq: 1, preflight: { ok: true, checks: [] }, overlay_url: '' };
  if (path === 'pending') return { epoch: 5, pending };
  if (path.startsWith('receipts')) return { receipts: [] };
  if (path === 'alerts') return { alerts: { enabled: true, thank_you: false, include_messages: false, min_bits: 1 } };
  if (path === 'alerts/recent') return { alerts: [] };
  if (path === 'assist') {
    if (assistRoute === 'down') throw Error('unreachable');
    return { assist: assistRoute === 'off' ? status('off', 'off') : status('laya', 'shadow') };
  }
  throw Error('Unexpected route ' + path);
} };
let Page;
window.__HERMES_PLUGINS__ = { register: (name, page) => { assert.equal(name, 'on-air'); Page = page; } };
vm.runInThisContext(fs.readFileSync('dashboard/dist/index.js', 'utf8'));
const root = createRoot(document.getElementById('root'));
const settle = () => new Promise(resolve => setTimeout(resolve, 20));
const poll = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
const panel = () => document.querySelector('[aria-labelledby="onair-assist"]');
const lines = () => [...document.querySelectorAll('li.onair-line')];
const btn = (scope, label) => [...scope.querySelectorAll('button')].find(b => b.textContent === label);

(async () => {
  await act(async () => { root.render(React.createElement(Page)); await settle(); });

  // Advice sits next to the real queued line; the human controls stay as they were.
  assert.equal(lines().length, 3);
  const chip1 = lines()[0].querySelector('.onair-assist-chip');
  assert(chip1 && chip1.textContent.includes('Shadow · laya') && chip1.textContent.includes('✓ suggests approve'), chip1 && chip1.textContent);
  assert(chip1.textContent.includes('95%') && chip1.textContent.includes('31 ms') && chip1.textContent.includes('show policy not evaluated'));
  assert(lines()[1].querySelector('.onair-assist-chip').textContent.includes('Checking…'));
  // Anything outside the fixed enum, or a non-finite/out-of-range confidence, reads as review without a number.
  const chip3 = lines()[2].querySelector('.onair-assist-chip').textContent;
  assert(chip3.includes('? suggests review') && !chip3.includes('%'), chip3);
  for (const li of lines()) {
    assert(btn(li, 'Approve') && !btn(li, 'Approve').disabled);
    assert(btn(li, 'Reject') && !btn(li, 'Reject').disabled);
  }

  // An approve recommendation never acts on its own, across polls.
  await poll();
  await poll();
  assert.deepEqual(posts, []);
  assert.equal(lines().length, 3);

  // The panel: provider, endpoint, counters, the shadow note, and a harmless test.
  assert(panel().textContent.includes(NOTE));
  assert(panel().textContent.includes('Laya (local guardrail service)'));
  assert(panel().textContent.includes('http://127.0.0.1:8390'));
  assert(panel().textContent.includes('3 checked · 2 shown · 0 unavailable · 1 discarded as stale'));
  assert(panel().textContent.includes('Not tested yet.'));
  await act(async () => { btn(panel(), 'Test connection').click(); await settle(); });
  assert.deepEqual(posts, [['assist/test', {}]]);

  // The human approves exactly as before.
  await act(async () => { btn(lines()[0], 'Approve').click(); await settle(); });
  assert.deepEqual(posts[1], ['approve', { line_id: 'l-1', epoch: 5 }]);

  // Assistance unreachable: the panel says so; the queue stays fully usable.
  assistRoute = 'down';
  await poll();
  assert(panel().textContent.includes('Assistance status unavailable.'));
  assert(!btn(panel(), 'Test connection'));
  assert(lines().length === 2 && btn(lines()[0], 'Approve') && !btn(lines()[0], 'Approve').disabled);

  // Off (the default): no test button, a setup hint, manual approval untouched.
  assistRoute = 'off';
  await poll();
  assert(panel().textContent.includes('Off') && panel().textContent.includes('semantic_assist'));
  assert(!btn(panel(), 'Test connection'));
  assert(btn(lines()[0], 'Approve') && !btn(lines()[0], 'Approve').disabled);

  await act(async () => { root.unmount(); });
  console.log('desk assist behavior: passed');
  process.exit(0);
})().catch((error) => { console.error(error); process.exit(1); });
