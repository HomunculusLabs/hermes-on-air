// Quick-start Stop truth for the NON-draining unconfirmed answers (jsdom, real dashboard/dist/index.js, fake
// SDK answering with the producer's exact shapes and texts):
//   A. cancel_all with a setup job that accepted the cancel but has NOT reached a terminal state
//      (job_pending, job shown 'cancelling') while the preview itself is quiescent: NOT a complete stop;
//   B. a routed stop whose owning copy did not confirm (setup_engine._routed_stop: ok:false, running:true in the
//      stop answer) while the refreshed facts say running:false WITHOUT a draining flag.
// In both the page must keep the "not confirmed stopped" view with a WORKING Stop again (independent of the
// draining flag), and say "Stopped. Nothing is running." only after the backend's ok:true.
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

// The producer's own texts (setup_engine.cancel_all / cancel / _routed_stop).
const JOB_PENDING = 'The setup job has NOT finished cancelling yet (it stops at its next step). Check status again.';
const OWNER_SILENT = 'The preview\'s owner did not confirm the stop; it may still be running. Try Stop again, or stop it where it was started.';
const ROUTED_PARTIAL = 'The private preview is NOT confirmed stopped: ' + OWNER_SILENT + ' Check status again.';
const job = (state) => ({ id: 'j1', digest: 'd', state, steps: [{ key: 'stage', label: 'Make the stage', state: state === 'cancelled' ? 'cancelled' : 'running' }], error: null, next_action: null, home: '/home/u/onair-stage', kind: 'quick' });

let scenario = 'A';
let phase = 'busy';
const answers = {
  A: [
    { ok: false, partial: true, draining: false, job_pending: true, job: job('cancelling'), note: JOB_PENDING, error: JOB_PENDING,
      preview: { ok: true, running: false, quiescent: true, note: 'The private preview is not running.' } },
    { ok: true, job: job('cancelled'), note: 'Nothing is running.', preview: { ok: true, running: false, quiescent: true } },
  ],
  B: [
    { ok: false, partial: true, draining: false, job_pending: false, note: ROUTED_PARTIAL, error: ROUTED_PARTIAL,
      preview: { ok: false, running: true, routed_to_owner: true, error: OWNER_SILENT } },
    { ok: true, note: 'Nothing is running.', preview: { ok: true, running: false, routed_to_owner: true, pending: 0, quiescent: true } },
  ],
};
const posts = [];
const facts = () => {
  if (scenario === 'A') return phase === 'busy' ? { running: false, quiescent: true } : { running: false, quiescent: true };
  // B: live owner first; after the unconfirmed routed stop the refreshed facts are running:false, NO draining flag
  return phase === 'busy' ? { running: true, privacy: false, pending: 0, captions: 0, overlay_url: 'http://127.0.0.1:9/overlay', mode: 'approval', owner: 'other_entry' }
    : { running: false, mode: 'off_air', owner: 'other_entry', input_kind: 'private_test' };
};
const currentJob = () => (scenario === 'A' ? (phase === 'done' ? job('cancelled') : job('running')) : null);
window.__HERMES_PLUGIN_SDK__ = { React, fetchJSON: async (url, options) => {
  const path = url.replace('/api/plugins/on-air/', '');
  if (options) {
    posts.push(scenario + ':' + path);
    if (path === 'setup/quick/cancel') {
      const answer = answers[scenario].shift();
      phase = answer.ok ? 'done' : 'unconfirmed';
      return answer;
    }
    return { ok: true };
  }
  if (path === 'setup/quick/status') return { ok: true, job: currentJob(), preview: facts(), saved_stage: scenario === 'B' ? { home: '/home/u/onair-stage', style: {} } : null, model_connection: { configured: false } };
  if (path === 'setup/quick/job') return { ok: true, job: currentJob(), preview: facts() };
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

async function run(which, firstButton, expectBusy, reason, endsWith) {
  scenario = which; phase = 'busy';
  const host = document.createElement('div');
  document.body.innerHTML = '';
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(React.createElement(Page)); });
  await flush(120);
  assert(text().includes(expectBusy), which + ': busy screen first: ' + text().slice(0, 300));
  await click(firstButton);  // the backend answers ok:false/partial WITHOUT draining
  assert(!text().includes('Stopped. Nothing is running.'), which + ': an unconfirmed stop never claims nothing is running');
  assert(!text().includes('Welcome back'), which + ': not a stopped screen: ' + text().slice(0, 300));
  assert(text().includes('Not confirmed stopped yet'), which + ': ' + text().slice(0, 400));
  assert(text().includes(reason), which + ': the backend\'s own reason is shown: ' + text().slice(0, 400));
  assert(status().includes('Not confirmed stopped.') && status().includes('Press Stop again'), which + ': ' + status());
  assert(button('Stop again'), which + ': a working retry stays available without any draining flag');
  await flush(120);  // a later status refresh (still not draining) must not drop the retry
  assert(button('Stop again') && !text().includes('Welcome back'), which + ': retry survives refresh');
  await click('Stop again');
  assert.equal(posts.filter((x) => x === which + ':setup/quick/cancel').length, 2, which + ': the retry reached the backend');
  assert(status().includes('Stopped. Nothing is running.'), which + ': ' + status());
  assert(!button('Stop again') && text().includes(endsWith), which + ': only a confirmed stop leaves the unconfirmed view: ' + text().slice(0, 300));
  await act(async () => { root.unmount(); });
}

(async () => {
  await run('A', 'Cancel', 'Setting up', JOB_PENDING, 'Last setup: cancelled');
  await run('B', 'Stop', 'Your private preview', OWNER_SILENT, 'Welcome back');
  console.log('quick stop partial truth: all checks passed');
})().catch((error) => { console.error(error); process.exit(1); });
