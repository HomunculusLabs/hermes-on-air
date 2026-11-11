// Quick-start Stop result truth (jsdom, real dashboard/dist/index.js, fake SDK answering EXACTLY like the
// producer: cancel_all + preview_facts). A draining stop is reported by the backend as running:false,
// draining:true, quiescent:false (setup_engine._draining_result), so the page must keep an explicit
// "not confirmed stopped" view with a WORKING retry (it must not fall back to "Welcome back" without Stop).
// "Stopped. Nothing is running." appears ONLY after the backend confirmed a complete stop (ok:true).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/' });
global.window = dom.window;
global.document = dom.window.document;
global.IS_REACT_ACT_ENVIRONMENT = true;
window.localStorage.setItem('onair.tab.v1', JSON.stringify({ tab: 'setup' }));
const React = require('react');
const { createRoot } = require('react-dom/client');
const { act } = React;

// The producer's own texts (setup_engine._draining_result and the CURRENT cancel_all partial shape).
const DRAIN_ERROR = 'Output is off and nothing is pending, but a model request is still finishing; it can no longer show anything. The stop is not confirmed complete yet: check status again.';
const DRAINING_FACTS = { ok: false, running: false, off_air: true, pending: 0, quiescent: false, draining: true, error: DRAIN_ERROR };
let phase = 'live';  // live -> draining -> stopped (the backend's own state)
const cancelAnswers = [
  { ok: false, partial: true, draining: true, job_pending: false, preview: DRAINING_FACTS,
    note: 'The private preview is NOT confirmed stopped: ' + DRAIN_ERROR + ' Check status again.',
    error: 'The private preview is NOT confirmed stopped: ' + DRAIN_ERROR + ' Check status again.' },
  'unreachable',
  { ok: true, note: 'Nothing is running.', preview: { ok: true, running: false, quiescent: true } },
];
const posts = [];
const preview = () => (phase === 'live' ? { running: true, privacy: false, pending: 0, captions: 0, overlay_url: 'http://127.0.0.1:9/overlay', mode: 'approval' }
  : phase === 'draining' ? DRAINING_FACTS : { running: false, quiescent: true });
window.__HERMES_PLUGIN_SDK__ = { React, fetchJSON: async (url, options) => {
  const path = url.replace('/api/plugins/on-air/', '');
  if (options) {
    posts.push(path);
    if (path === 'setup/quick/cancel') {
      const answer = cancelAnswers.shift();
      if (answer === 'unreachable') throw Error('502 Bad Gateway');  // the backend stays draining meanwhile
      phase = answer.ok ? 'stopped' : 'draining';
      return answer;
    }
    return { ok: true };
  }
  if (path === 'setup/quick/status') return { ok: true, job: null, preview: preview(), saved_stage: { home: '/home/u/onair-stage', style: {} }, model_connection: { configured: false } };
  if (path === 'setup/quick/job') return { ok: true, job: null, preview: preview() };
  if (path === 'setup/quick/preview/pending') return { ok: true, pending: [] };
  if (path === 'setup/status') return { ok: true, desk_connected: false, current_home_is_stage: false, suggested_target: '/home/u/onair-stage', practice_running: false };
  throw Error('503 On Air control unavailable');
} };
let Page;
window.__HERMES_PLUGINS__ = { register: (name, component) => { Page = component; } };
vm.runInThisContext(fs.readFileSync(require('node:path').join(__dirname, '../../dashboard/dist/index.js'), 'utf8'));

const flush = async (ms = 40) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); };
const text = () => document.body.textContent;
const status = () => [...document.querySelectorAll('[role="status"]')].map((n) => n.textContent).join(' | ');
const button = (name) => [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === name);
const click = async (name) => { const b = button(name); assert(b, 'no button ' + name + ' in: ' + text().slice(0, 400)); await act(async () => { b.click(); }); await flush(); };

(async () => {
  const root = createRoot(document.getElementById('root'));
  await act(async () => { root.render(React.createElement(Page)); });
  await flush(120);
  assert(text().includes('Your private preview'), 'the live private preview screen is shown: ' + text().slice(0, 300));
  await click('Stop');  // 1: the backend says draining (cancel_all ok:false, partial, draining; facts running:false)
  assert(!text().includes('Stopped. Nothing is running.'), 'a draining stop must not claim nothing is running');
  assert(!text().includes('Welcome back'), 'a draining stop is not a stopped preview: ' + text().slice(0, 300));
  assert(text().includes('Stopping your private preview') && text().includes('Not confirmed stopped yet'), text().slice(0, 400));
  assert(text().includes(DRAIN_ERROR), 'the backend\'s own reason is shown');
  assert(status().includes('Not stopped yet: still finishing.') && status().includes('Press Stop again'), status());
  assert(button('Stop again'), 'a working retry stays available while the backend reports draining');
  await click('Stop again');  // 2: no answer from the backend; it is still draining
  assert.equal(posts.filter((x) => x === 'setup/quick/cancel').length, 2, 'the retry reached the backend');
  assert(!text().includes('Stopped. Nothing is running.') && !text().includes('Welcome back'), 'an unanswered stop stays unconfirmed');
  assert(status().includes('Not confirmed stopped.') && status().includes('could not reach') && status().includes('Press Stop again'), status());
  assert(button('Stop again'), 'retry still available after an unanswered stop');
  await click('Stop again');  // 3: the backend confirms a complete stop (positive quiescence)
  assert(status().includes('Stopped. Nothing is running.'), status());
  assert(text().includes('Welcome back') && !button('Stop again'), 'only a confirmed stop returns to the start screen');
  assert.deepEqual(posts, ['setup/quick/cancel', 'setup/quick/cancel', 'setup/quick/cancel']);
  await act(async () => { root.unmount(); });
  console.log('quick stop truth: all checks passed');
})().catch((error) => { console.error(error); process.exit(1); });
