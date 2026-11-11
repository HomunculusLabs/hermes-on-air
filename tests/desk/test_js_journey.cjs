// New-operator journey for the desk, rendered in jsdom with a fake SDK and the real
// dashboard/dist/index.js: fresh/disconnected setup guide, healthy approval with 0, many and
// maximum-length lines, privacy, preflight failure, runtime latch and recovery, lost connection
// and stale responses. Run by tests/desk/test_js_runner.py (node + the dashboard's node_modules).
// All fixture data is synthetic; nothing here proves a real broadcast, mute or gateway.
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

const SETUP_COMMANDS = [
  'hermes profile create stage',
  'hermes -p stage plugins enable on-air',
  'hermes -p stage onair setup',
  'hermes -p stage onair preflight',
  'hermes -p stage gateway run',
  'hermes -p stage dashboard --isolated',
  'hermes -p stage onair status',
];

let reachable = false;
let state = null;
let pending = [];
let receipts = [];
let posts = [];
let postReply = null;      // function(path, body) -> reply, or null for {ok: true}
let holdState = null;      // a promise the next /state read waits on
let holdStateValue = null; // what that held read finally returns
let recentAlerts = [];
let clipboard = [];
let clipboardFails = false;
let confirmOk = false;
window.confirm = () => confirmOk;
// Node has its own global navigator, so the bundle may see either object.
for (const nav of new Set([window.navigator, globalThis.navigator])) Object.defineProperty(nav, 'clipboard', { configurable: true, value: {
  writeText: async (text) => { if (clipboardFails) throw Error('denied'); clipboard.push(text); },
} });

const healthy = (extra) => Object.assign({ mode: 'approval', live: true, privacy: false, muted: false, epoch: 4, seq: 1,
  voice: false, tts: 'none', pending: 0, overlay_url: 'http://127.0.0.1:18790/overlay', preflight: { ok: true, checks: [] } }, extra || {});

window.__HERMES_PLUGIN_SDK__ = { React, fetchJSON: async (url, options) => {
  const path = url.replace('/api/plugins/on-air/', '');
  if (options) {
    posts.push([path, JSON.parse(options.body)]);
    if (!reachable) throw Error('503');
    return postReply ? postReply(path, JSON.parse(options.body)) : { ok: true };
  }
  if (path === 'state' && holdState) {
    const wait = holdState, value = holdStateValue;
    holdState = null;
    await wait;
    return value;
  }
  if (!reachable) throw Error('503 On Air control unavailable');
  if (path === 'state') return JSON.parse(JSON.stringify(state));
  if (path === 'pending') return { epoch: state.epoch, pending: pending.slice() };
  if (path.startsWith('receipts')) return { receipts };
  if (path === 'alerts') return { alerts: { enabled: true, thank_you: true, include_messages: false, min_bits: 1 } };
  if (path === 'alerts/recent') return { alerts: recentAlerts.slice() };
  throw Error('Unexpected route ' + path);
} };

let Page;
window.__HERMES_PLUGINS__ = { register: (name, page) => { assert.equal(name, 'on-air'); Page = page; } };
vm.runInThisContext(fs.readFileSync('dashboard/dist/index.js', 'utf8'));
const root = createRoot(document.getElementById('root'));
const settle = () => new Promise(resolve => setTimeout(resolve, 20));
const poll = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const text = () => document.body.textContent;
const chips = () => $$('.onair-banner .onair-chip').map(chip => chip.textContent);
const buttonNamed = (name) => $$('button').find(b => b.textContent === name);
const connect = () => $('[aria-labelledby="onair-connect"]');
const preflight = () => $('.onair-preflight');
const queuePanel = () => $('[aria-labelledby="onair-queue"]');
const modePanel = () => $('[aria-labelledby="onair-mode"]');
const notice = () => $('.onair-notice').textContent;
const alertsPanel = () => $('[aria-labelledby="onair-alerts"]');
const key = (k) => act(async () => { window.dispatchEvent(new window.KeyboardEvent('keydown', { key: k, bubbles: true })); await settle(); });
const click = (el) => act(async () => { el.click(); await settle(); });

function assertNothingLive(label) {
  for (const chip of chips()) assert(!/^(LIVE|APPROVAL|AUTONOMOUS|PAUSED|OFF AIR)$/.test(chip), label + ': live-looking chip ' + chip);
  assert(!chips().some(chip => /connected$/i.test(chip) && !/not connected/i.test(chip)), label + ': claims Connected');
  assert.equal($('.onair-privacy-banner'), null, label + ': stale privacy banner');
  assert.equal($$('[aria-pressed="true"]').length, 0, label + ': stale pressed control');
  for (const name of ['Privacy cut (P)', 'Mute all (M)', 'Stop all (Esc)']) assert(buttonNamed(name).disabled, label + ': ' + name);
  assert(queuePanel().textContent.includes('Queue unavailable'), label);
  assert.equal($$('.onair-line').length, 0, label + ': stale queue rows');
  // Alert settings and recent alerts are unavailable too: no stale checkbox, draft or alert row.
  const alerts = alertsPanel().textContent;
  assert(alerts.includes('Alert settings unavailable'), label + ': stale alert settings: ' + alerts);
  assert(alerts.includes('Recent alerts unavailable'), label + ': stale recent alerts: ' + alerts);
  assert.equal(alertsPanel().querySelectorAll('input').length, 0, label + ': stale alert inputs');
  assert.equal(alertsPanel().querySelectorAll('.onair-recent-alert').length, 0, label + ': stale alert rows');
  assert.equal(buttonNamed('Save alert settings'), undefined, label + ': stale alert draft');
}

(async () => {
  // 1. Fresh desk, no show: a setup guide with existing commands, not just "unavailable".
  await act(async () => { root.render(React.createElement(Page)); await settle(); });
  await act(async () => { [...document.querySelectorAll('.onair-tab')].find(b => b.textContent === 'Details').click(); await settle(); });
  assert(connect(), 'setup guide shown when no show answers');
  assert.equal(connect().querySelector('h2').textContent, 'Connect this desk to a show');
  assert(chips().includes('NOT CONNECTED'), chips());
  assertNothingLive('fresh');
  assert.deepEqual($$('.onair-cmd').map(el => el.textContent), SETUP_COMMANDS);
  // Installing into the new profile depends on where On Air came from: a template, never copy-ready.
  const templates = $$('.onair-template');
  assert.deepEqual(templates.map(el => el.textContent), ['hermes -p stage plugins install <git-url> --enable']);
  assert.equal(templates[0].closest('.onair-command').querySelector('button'), null, 'a placeholder command has no Copy button');
  assert.equal(templates[0].querySelector('var').textContent, '<git-url>');
  const guide = connect().textContent;
  for (const phrase of ['dedicated stage profile', 'Plugins are per profile', 'file://', 'already installed', 'same profile', 'Captions only', 'voice: false', 'Voice (optional)', 'OBS (optional)', 'checks again every second'])
    assert(guide.includes(phrase), phrase);
  // No token handling, endpoint picker or new remote-control surface.
  for (const forbidden of ['desk.token', 'Bearer', 'token']) assert(!text().toLowerCase().includes(forbidden.toLowerCase()), forbidden);
  const fields = $$('input:not([type="checkbox"]):not([type="number"]), textarea, select');
  assert(fields.every(el => el.closest('.onair-views') && el.disabled), 'only disabled Views selectors are shown');
  assert(notice().includes('Not connected'));
  // Copy puts exactly the command on the clipboard and says so accessibly.
  const copyPreflight = $$('.onair-command').find(row => row.textContent.includes('onair preflight')).querySelector('button');
  assert.equal(copyPreflight.getAttribute('aria-label'), 'Copy command: hermes -p stage onair preflight');
  await click(copyPreflight);
  assert.deepEqual(clipboard, ['hermes -p stage onair preflight']);
  assert.equal(copyPreflight.textContent, 'Copied');
  assert($('.onair-copy-status').textContent.includes('Copied to the clipboard: hermes -p stage onair preflight'));
  assert.equal($('.onair-copy-status').parentElement.tagName, 'MAIN');
  assert.equal($('.onair-copy-status').getAttribute('aria-live'), 'polite');
  clipboardFails = true;
  const copyGateway = $$('.onair-command').find(row => row.textContent.includes('gateway run')).querySelector('button');
  await click(copyGateway);
  assert(notice().includes('Copy failed'), notice());
  assert.equal(window.getSelection().toString(), 'hermes -p stage gateway run');
  clipboardFails = false;
  // Safety keys and approvals send nothing while nothing is connected.
  for (const k of ['p', 'm', 'Escape', 'a', 'r']) await key(k);
  assert.deepEqual(posts, []);

  // 2. Healthy approval with an empty queue.
  reachable = true;
  state = healthy();
  pending = [];
  await poll();
  assert.equal(connect(), null, 'setup guide hidden once connected');
  assert(chips().includes('LIVE') && chips().includes('APPROVAL'), chips());
  assert(!chips().some(chip => /chat activity/.test(chip)), 'activity is labeled as observed events, not chat');
  assert(queuePanel().textContent.includes('0 waiting'));
  assert(queuePanel().textContent.includes('No lines waiting.'));
  assert(modePanel().textContent.includes('No voice (captions only)'), 'voice false reads as no voice, captions only');
  assert(alertsPanel().textContent.includes('No recent alerts.'), 'an answered empty list is not unavailable');
  const modeHelp = modePanel().querySelector('.onair-mode-help').textContent;
  for (const phrase of ['Off air', 'Paused', 'Approval', 'Autonomous', 'confirmation']) assert(modeHelp.includes(phrase), phrase);

  // 3. Many and maximum-length lines: real count, every row has its own identity-bound actions.
  const long = 'W'.repeat(3000);
  pending = Array.from({ length: 12 }, (_, n) => ({ line_id: 'l' + n, epoch: 4, text: n === 2 ? long : n === 5 ? '<b>not bold</b> line ' + n : 'Line ' + n }));
  state = healthy({ pending: 12 });
  await poll();
  assert(queuePanel().textContent.includes('12 waiting'));
  assert.equal($$('.onair-line').length, 12);
  $$('.onair-line').forEach((row, i) => {
    assert.deepEqual([...row.querySelectorAll('button')].map(b => b.getAttribute('aria-label')), ['Approve line ' + (i + 1), 'Reject line ' + (i + 1)]);
  });
  assert.equal($$('.onair-line-text')[2].textContent, long);
  assert.equal($('b'), null);
  await click($$('.onair-line')[3].querySelector('.onair-approve'));
  assert.deepEqual(posts.at(-1), ['approve', { line_id: 'l3', epoch: 4 }]);
  assert(notice().includes('Approve — approve confirmed'), notice());
  await key('ArrowDown'); await key('ArrowDown');
  await key('r');
  assert.deepEqual(posts.at(-1), ['reject', { line_id: pending[$$('.onair-line').findIndex(r => r.classList.contains('onair-selected'))].line_id, epoch: 4 }]);
  // A reply without an explicit ok:true is not a confirmation.
  postReply = () => ({});
  await key('a');
  assert(notice().includes('NOT confirmed'), notice());
  postReply = () => undefined;
  await key('a');
  assert(notice().includes('NOT confirmed'), notice());
  postReply = null;

  // 4. Privacy: banner, chip, state line and control agree; mute and stop stay separate.
  pending = [];
  state = healthy({ privacy: true });
  await poll();
  const banner = $('.onair-privacy-banner').textContent;
  assert.equal(banner, "PRIVACY CUT ON · viewers see 'Be right back' · audio blocked · pending cleared");
  assert(chips().includes('PRIVACY CUT ON'), chips());
  assert(modePanel().textContent.includes('Privacy cut ON · Audio blocked by privacy'));
  assert(buttonNamed('Uncover (P)').getAttribute('aria-pressed') === 'true');
  assert(buttonNamed('Mute all (M)') && buttonNamed('Stop all (Esc)'));
  assert.notEqual(buttonNamed('Mute all (M)'), buttonNamed('Stop all (Esc)'));
  state = healthy({ privacy: true, muted: true });
  await poll();
  assert(modePanel().textContent.includes('Privacy cut ON · Muted'));
  assert(chips().includes('MUTED'));
  await key('p');
  assert.deepEqual(posts.at(-1), ['privacy', { on: false }]);
  assert(notice().includes('Privacy off — privacy confirmed'), notice());
  state = healthy({ privacy: false });
  await poll();
  assert.equal($('.onair-privacy-banner'), null);
  assert(modePanel().textContent.includes('Privacy cut off · Sound on') === false, 'captions-only stage never claims sound on');
  assert(modePanel().textContent.includes('Privacy cut off · No voice (captions only)'));

  // 5. Preflight failure: the actual checks, a fix per check and the recovery path.
  state = healthy({ mode: 'off_air', live: false, preflight: { ok: false, checks: [
    { name: 'allowed_users', status: 'fail', detail: 'ONAIR_ALLOWED_USERS must be exactly onair-stage.' },
    { name: 'toolsets', status: 'fail', detail: 'Set platform_toolsets.on_air to exactly [no_mcp].' },
    { name: 'tts', status: 'warn', detail: 'not verified' },
    { name: 'mystery_check', status: 'fail', detail: 'W'.repeat(400) },
  ] } });
  await poll();
  assert(preflight().textContent.includes('Preflight failed · arming refused'));
  const failing = $$('.onair-preflight-checks > li');
  assert.deepEqual(failing.map(li => li.querySelector('code').textContent), ['allowed_users', 'toolsets', 'mystery_check']);
  assert(failing[0].querySelector('.onair-check-fix').textContent.includes(".env"));
  assert(failing[1].querySelector('.onair-check-fix').textContent.includes('[no_mcp]'));
  assert.equal(failing[2].querySelector('.onair-check-fix'), null, 'no invented advice for an unknown check');
  assert(failing[2].textContent.includes('W'.repeat(400)));
  const recovery = preflight().textContent;
  for (const phrase of ['hermes -p stage onair preflight', 'hermes -p stage gateway run', 'Approval']) assert(recovery.includes(phrase), phrase);
  assert(chips().includes('PREFLIGHT FAILED'));
  assert.equal(buttonNamed('Clear runtime latch'), undefined);
  postReply = (path) => path === 'mode' ? { ok: false, error: 'preflight failed: ONAIR_ALLOWED_USERS must be exactly onair-stage.' } : { ok: true };
  await click(buttonNamed('Approval'));
  assert.deepEqual(posts.at(-1), ['mode', { mode: 'approval' }]);
  assert(notice().includes('Arm into Approval — mode NOT confirmed: preflight failed'), notice());
  postReply = null;

  // 6. Runtime latch: rule named and explained; clear is explicit, off air only, never auto-arms.
  const latched = (rule) => ({ ok: false, reason: 'runtime_refused:' + rule, checks: [
    { name: 'runtime_latch', status: 'fail', detail: 'runtime_refused:' + rule + ': the runtime gate refused an On Air provider request; fix the cause, then clear the latch from off air.' }] });
  state = healthy({ mode: 'approval', preflight: latched('private_context') });
  await poll();
  assert(preflight().textContent.includes('Runtime gate latched · arming refused'));
  assert(preflight().querySelector('.onair-latch-rule').textContent.includes('Private context'));
  assert(preflight().textContent.includes('memory'), 'private_context explains memory blocks');
  assert(preflight().textContent.includes('Go off air to clear the runtime latch'));
  assert.equal(buttonNamed('Clear runtime latch'), undefined);
  state = healthy({ mode: 'off_air', live: false, preflight: latched('tool_surface') });
  await poll();
  assert(preflight().querySelector('.onair-latch-rule').textContent.includes('Tool surface'));
  assert(preflight().textContent.includes('tool schemas'));
  const before = posts.length;
  // Confirmed POST, but the refreshed state still reports the latch: that is not success.
  postReply = () => ({ ok: true, state: {} });
  await click(buttonNamed('Clear runtime latch'));
  assert.deepEqual(posts.slice(before), [['runtime/clear', {}]]);
  assert(notice().includes('still reports the runtime latch'), notice());
  assert(preflight() && buttonNamed('Clear runtime latch'), 'panel stays while the state still reports the latch');
  // Refused clear.
  postReply = () => ({ ok: false, error: 'go off air before clearing the runtime latch' });
  await click(buttonNamed('Clear runtime latch'));
  assert(notice().includes('runtime/clear NOT confirmed: go off air before clearing the runtime latch'), notice());
  assert(preflight());
  // Real clear: refreshed state shows no latch; show stays OFF AIR; nothing arms by itself.
  postReply = (path) => { if (path === 'runtime/clear') state = healthy({ mode: 'off_air', live: false, preflight: { ok: true, checks: [] } }); return { ok: true }; };
  await click(buttonNamed('Clear runtime latch'));
  assert(notice().includes('runtime/clear confirmed'), notice());
  assert(notice().includes('choose Approval'), notice());
  assert.equal(preflight(), null);
  assert(chips().includes('OFF AIR') && chips().includes('NOT LIVE'));
  await poll();
  assert(!posts.slice(before).some(([path]) => path === 'mode'), 'no auto-arm or auto-autonomous after a clear');
  postReply = null;

  // 7. Lost connection after a live state: honest labels, stale summary, no live-looking controls.
  state = healthy({ privacy: true, pending: 2 });
  pending = [{ line_id: 'x1', epoch: 4, text: 'stale line one' }, { line_id: 'x2', epoch: 4, text: 'stale line two' }];
  recentAlerts = [{ kind: 'raid', name: 'River', amount: 37 }];
  await poll();
  assert(chips().includes('LIVE'));
  assert(alertsPanel().textContent.includes('River raided · 37 viewers'));
  // An unsaved alert edit (a draft) must not survive the connection.
  const thankYou = [...alertsPanel().querySelectorAll('label')].find(l => l.textContent.includes('Thank-you lines')).querySelector('input');
  await click(thankYou);
  assert.equal(thankYou.checked, false);
  assert(buttonNamed('Save alert settings'), 'editing an alert setting offers a save');
  reachable = false;
  await poll();
  assert(chips().includes('CONNECTION LOST'), chips());
  assertNothingLive('lost');
  assert(!text().includes('River raided'), 'no stale recent alert anywhere');
  assert(chips().includes('STATE UNKNOWN'), chips());
  assert(/^Last action: .* \((just now|\d+ s ago)\)$/.test(notice()), 'an old confirmation reads as history: ' + notice());
  assert(!/[.!?] ·/.test(notice()), 'no doubled punctuation: ' + notice());
  assert.equal($('.onair-notice [aria-live]').textContent.includes(' s ago'), false, 'the ticking age stays out of the live region');
  assert.equal(connect().querySelector('h2').textContent, 'Connection to the show lost');
  // Exact wording: SHOW controls are disabled; local Copy and the setup disclosure still work.
  assert(connect().textContent.includes('All show controls are disabled'), connect().textContent);
  assert(!text().includes('Every control is disabled'), 'no claim that local Copy/disclosure are disabled');
  const lostCopy = connect().querySelector('.onair-command button');
  assert(lostCopy && !lostCopy.disabled, 'Copy for the status command stays usable while lost');
  const lostDetails = connect().querySelector('details.onair-setup-details');
  assert(lostDetails && !lostDetails.querySelector('summary').hasAttribute('disabled'), 'setup disclosure stays usable');
  const stale = connect().querySelector('.onair-stale').textContent;
  assert(stale.includes('Last confirmed'), stale);
  for (const phrase of ['APPROVAL', 'Privacy cut ON', '2 pending']) assert(stale.includes(phrase), phrase);
  assert(connect().textContent.includes('Ctrl+C'), 'tells the operator how to cut at the source');
  assert(connect().textContent.includes('hermes -p stage onair status'));
  assert(!text().includes('stale line one'));
  const postsWhileLost = posts.length;
  for (const k of ['p', 'm', 'Escape', 'a']) await key(k);
  assert.equal(posts.length, postsWhileLost, 'nothing is sent while the connection is lost');

  // 8. Reconnect, then a stale in-flight read resolves after the connection is gone again.
  reachable = true;
  state = healthy();
  pending = [];
  recentAlerts = [];
  await poll();
  assert(chips().includes('LIVE') && connect() === null);
  const thankYouAgain = [...alertsPanel().querySelectorAll('label')].find(l => l.textContent.includes('Thank-you lines')).querySelector('input');
  assert.equal(thankYouAgain.checked, true, 'reconnected settings come from the show, not the lost draft');
  assert.equal(buttonNamed('Save alert settings'), undefined, 'the lost draft is gone');
  let release;
  holdState = new Promise(resolve => { release = resolve; });
  holdStateValue = healthy({ mode: 'autonomous' });
  await poll(); // this poll's /state read is now held
  await key('Escape'); // stop fires anyway; its refresh supersedes the held poll
  assert.deepEqual(posts.at(-1), ['stop', {}]);
  reachable = false;
  await poll();
  await act(async () => { release(); await settle(); });
  await poll();
  assert(chips().includes('CONNECTION LOST'), 'stale read never restores a live state: ' + chips());
  assert(!chips().includes('AUTONOMOUS'));
  assertNothingLive('stale read');

  // 9. A confirmed safety action whose refresh fails says the state is unknown.
  reachable = true; state = healthy(); await poll();
  postReply = (path) => { if (path === 'mute') reachable = false; return { ok: true }; };
  await key('m');
  assert(notice().includes('Mute — mute confirmed'), notice());
  assert(notice().includes('current state unknown'), notice());
  assert(chips().includes('CONNECTION LOST'));
  postReply = null;

  // 10. Mid-refresh disagreement (state and queue epochs differ) is not a connected state.
  reachable = true; state = healthy(); await poll();
  const realFetch = window.__HERMES_PLUGIN_SDK__.fetchJSON;
  window.__HERMES_PLUGIN_SDK__.fetchJSON = async (url, options) => url.endsWith('/pending') ? { epoch: 99, pending: [] } : realFetch(url, options);
  await poll();
  // The show answered, so this is not a lost connection: say the state is unconfirmed.
  assert(chips().includes('STATE UNCONFIRMED'), chips());
  assert(!chips().includes('CONNECTION LOST'), chips());
  assert.equal(connect().querySelector('h2').textContent, 'Show state unconfirmed');
  assert(connect().textContent.includes('changed mid-refresh'));
  assertNothingLive('unconfirmed');
  window.__HERMES_PLUGIN_SDK__.fetchJSON = realFetch;
  await poll();
  assert(chips().includes('LIVE'));

  await act(async () => root.unmount());
  console.log('journey: setup guide, copy, 0/many/long queue, privacy agreement, preflight fixes, latch recovery, lost connection, stale reads passed');
})().catch(e => { console.error(e); try { root.unmount(); } catch (_) {} process.exitCode = 1; });
