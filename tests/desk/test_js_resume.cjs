// Reproduce setup, stop and resume without unmounting the quick-start form.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost/' });
global.window = dom.window;
global.document = dom.window.document;
global.IS_REACT_ACT_ENVIRONMENT = true;
const React = require('react');
const { createRoot } = require('react-dom/client');
const { act } = React;
let saved = false, preview = false;
const plans = [];
window.__HERMES_PLUGIN_SDK__ = { React, fetchJSON: async (url, options) => {
  const path = url.replace('/api/plugins/on-air/', '');
  if (options) {
    const body = JSON.parse(options.body || '{}');
    if (path === 'setup/quick/plan') { plans.push(body.answers); return { ok: true, digest: 'a', effects: ['Plan'], approve_with: { chat: '/approve' }, kind: saved ? 'resume' : 'create' }; }
    if (path === 'setup/quick/apply') { saved = true; preview = true; return { ok: true, job: { state: 'running', steps: [] } }; }
    if (path === 'setup/quick/cancel') { preview = false; return { ok: true, note: 'Nothing is running.' }; }
    return { ok: true };
  }
  if (path === 'setup/quick/status') return { ok: true, job: saved ? { state: 'done', steps: [] } : null, preview: { running: preview }, saved_stage: saved ? { home: '/tmp/stage', channel: 'sample', style: {} } : null, model_connection: {} };
  if (path === 'setup/quick/job') return { ok: true, job: { state: 'done', steps: [] }, preview: { running: preview } };
  if (path === 'setup/quick/preview/pending') return { ok: true, pending: [] };
  if (path === 'setup/quick/show/status') return { ok: true, running: false, show: { running: false } };
  if (path === 'setup/status') return { ok: true, desk_connected: false };
  throw Error('unavailable');
} };
let Page;
window.__HERMES_PLUGINS__ = { register: (_, page) => { Page = page; } };
vm.runInThisContext(fs.readFileSync(require('node:path').join(__dirname, '../../dashboard/dist/index.js'), 'utf8'));
const wait = async (ms = 60) => act(async () => { await new Promise(r => setTimeout(r, ms)); });
const click = async name => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === name); assert(b, name); await act(async () => b.click()); await wait(); };
(async () => {
  const root = createRoot(document.getElementById('root'));
  await act(async () => root.render(React.createElement(Page)));
  await wait(100);
  const field = document.querySelector('input[name="channel"]') || document.querySelector('#onair-qs-channel');
  assert(field, 'channel input');
  await act(async () => { Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(field, 'sample'); field.dispatchEvent(new window.Event('input', { bubbles: true })); });
  await click('Set up & preview');
  assert.equal(plans[0].channel, 'sample');
  await click('Approve: set up & preview');
  await wait(650);
  await click('Stop');
  await click('Resume preview');
  assert.equal(plans.length, 2);
  assert(!Object.hasOwn(plans[1], 'channel'), 'resume must not submit the hidden original channel');
  await act(async () => root.unmount());
  console.log('resume regression passed');
})().catch(e => { console.error(e); process.exit(1); });
