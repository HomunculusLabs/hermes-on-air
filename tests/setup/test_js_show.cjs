// Private show + agent-issued pending plan in the REAL dashboard/dist/index.js (jsdom, fake SDK that answers
// with the producers' own shapes: setup_engine.status().pending_plan and show_runtime.status()).
// 1. QuickStart renders the agent's EXACT persisted plan (name, channel, tone, topics, effects) and approving
//    it posts that same digest. 2. A pending SHOW plan renders its effects and Start posts its digest with
//    confirm:true. 3. A running show shows its truthful activity, captions marked spoken, chat outcomes, and
//    Stop -> Resume -> Send chat reach the show routes.
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

const DIGEST = 'a'.repeat(64);
const SHOW_DIGEST = 'b'.repeat(64);
const PENDING = { digest: DIGEST, code: 'aaaaaaaa', kind: 'create', target: '/home/u/onair-stage',
  effects: ['Create a new private stage folder /home/u/onair-stage for a public co-host called Pixel.',
    'Save the co-host\'s public style: tone: friendly -> upbeat; topics: (none) -> desk lamps, soldering.'],
  answers: { channel: 'cozy_fox', agent_name: 'Pixel', use_model: false, style: { tone: 'upbeat', topics: ['desk lamps', 'soldering'] } } };
let phase = 'plan';  // plan -> saved (show planned) -> running -> paused
let showMode = 'speaking';
let startHere = false;  // the normal dashboard has no model client; a process with ctx.llm can start here
let previewLive = false;  // the setup preview running at the same time as the show
const NOT_HERE = 'Start the show from your own Hermes CLI, TUI or desktop agent chat, where your model connection is reachable.';
const startFacts = () => ({ start_here: startHere, start_next_action: startHere ? '' : NOT_HERE });
const posts = [];
let showOverride = null;  // producer-shaped status sequences for the pending-plan lifecycle (cases 5-6)
let localPending = null;
const showPlanOf = (code, digest) => ({ digest, code, effects: ['Start a PRIVATE show on this computer (127.0.0.1 only) hosted by Pixel.'], approve_with: { chat: '/onair-setup show approve ' + code } });
const stoppedWith = (plan) => ({ ok: true, running: false, ...startFacts(), show: { running: false, activity: 'stopped', activity_text: 'Not running.' }, pending_show_plan: plan });
const saved = () => (phase === 'plan' ? null : { home: '/home/u/onair-stage', channel: 'cozy_fox', agent_name: 'Pixel', style: { tone: 'upbeat' } });
const showStatus = () => {
  if (phase === 'saved') return { ok: true, running: false, ...startFacts(), show: { running: false, activity: 'stopped', activity_text: 'Not running.' },
    pending_show_plan: { digest: SHOW_DIGEST, code: 'bbbbbbbb', effects: ['Start a PRIVATE show on this computer (127.0.0.1 only) hosted by Pixel.', 'Voice: spoken with your Hermes text-to-speech (kokoro) and shown as captions.'], approve_with: { chat: '/onair-setup show approve bbbbbbbb' } } };
  if (phase === 'plan') return { ok: true, running: false, ...startFacts(), show: { activity: 'stopped' }, pending_show_plan: null };
  return { ok: true, running: true, pending_show_plan: null, show: { running: true, agent_name: 'Pixel', activity: showMode,
    activity_text: showMode === 'paused' ? 'Paused: nothing is shown or said until you press Resume.' : 'Speaking: a line is on the captions (and voice, when on).',
    model: { label: 'custom · fixture-model', answered_by: 'custom · fixture-model', requests: 3, max_requests: 40 },
    voice: { planned: true, label: 'kokoro', failures: 0 }, pending: [],
    lines: [{ caption_id: 'c1', text: 'Hey everyone, I am Pixel!', audio: true }, { caption_id: 'c2', text: 'Tin the tip first.', audio: false }],
    chat: [{ msg_id: 'm1', text: 'how does the lamp work?', answer: 'answered' }], overlay_url: 'http://127.0.0.1:9/overlay' } };
};
window.__HERMES_PLUGIN_SDK__ = { React, fetchJSON: async (url, options) => {
  const path = url.replace('/api/plugins/on-air/', '');
  if (options) {
    const body = JSON.parse(options.body || '{}');
    posts.push([path, body]);
    if (path === 'setup/quick/apply') { phase = 'saved'; return { ok: true, job: { state: 'done', steps: [], next_action: '' } }; }
    if (path === 'setup/quick/show/start') { phase = 'running'; return { ok: true }; }
    if (path === 'setup/quick/show/stop') { showMode = 'paused'; return { ok: true, mode: 'paused' }; }
    if (path === 'setup/quick/show/resume') { showMode = 'listening'; return { ok: true, mode: 'autonomous' }; }
    if (path === 'setup/quick/show/plan') { localPending = showPlanOf('dddddddd', 'd'.repeat(64)); return { ok: true, ...localPending }; }
    return { ok: true };
  }
  const preview = previewLive ? { running: true, quiescent: false, privacy: false, message: null, captions: 0, pending: 0, last_caption: '' } : { running: false, quiescent: true };
  if (path === 'setup/quick/preview/pending') return { ok: true, pending: [] };
  if (path === 'setup/quick/status') return { ok: true, job: phase === 'plan' ? null : { state: 'done', steps: [], next_action: '' }, preview,
    saved_stage: saved(), model_connection: { configured: true, label: 'custom · fixture-model', usable_here: false, next_action: 'This page cannot reach your model connection.' },
    pending_plan: phase === 'plan' ? PENDING : null };
  if (path === 'setup/quick/job') return { ok: true, job: { state: 'done', steps: [] }, preview };
  if (path === 'setup/quick/show/status') return showOverride ? showOverride() : showStatus();
  if (path === 'setup/status') return { ok: true, desk_connected: false, current_home_is_stage: false, suggested_target: '/home/u/onair-stage', practice_running: false };
  throw Error('503 On Air control unavailable');
} };
let Page;
window.__HERMES_PLUGINS__ = { register: (name, component) => { Page = component; } };
vm.runInThisContext(fs.readFileSync(require('node:path').join(__dirname, '../../dashboard/dist/index.js'), 'utf8'));

const flush = async (ms = 60) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); };
const text = () => document.body.textContent;
const button = (name) => [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === name);
const click = async (name) => { const b = button(name); assert(b, 'no button ' + name + ' in: ' + text().slice(0, 600)); await act(async () => { b.click(); }); await flush(); };

(async () => {
  const root = createRoot(document.getElementById('root'));
  await act(async () => { root.render(React.createElement(Page)); });
  await flush(150);
  // 1. the agent's exact persisted plan, with its richer name/topics/style
  assert(text().includes('A setup plan is waiting for your approval'), text().slice(0, 500));
  assert(text().includes('Co-host Pixel') && text().includes('twitch.tv/cozy_fox') && text().includes('tone upbeat') && text().includes('topics desk lamps, soldering'), text().slice(0, 800));
  for (const e of PENDING.effects) assert(text().includes(e), 'effect shown: ' + e);
  assert(text().includes('/onair-setup approve aaaaaaaa'));
  await click('Approve: set up & preview');
  const apply = posts.find(([p]) => p === 'setup/quick/apply');
  assert(apply && apply[1].digest === DIGEST && apply[1].confirm === true, JSON.stringify(posts));
  // 2. the pending show plan (from the agent) and its one approval
  await act(async () => { root.unmount(); });
  const root2 = createRoot(document.getElementById('root'));
  await act(async () => { root2.render(React.createElement(Page)); });
  await flush(1300);
  assert(text().includes('Run a private show') && text().includes('This show will:'), text().slice(0, 800));
  assert(text().includes('hosted by Pixel') && text().includes('/onair-setup show approve bbbbbbbb'), text().slice(0, 800));
  // 2a. normal dashboard (no model client here): no Start that would be refused; the agent-chat code is primary
  assert(!button('Start the show') && !button('Review the show'), 'no dead-end Start/Review here: ' + text().slice(0, 900));
  const primary = document.querySelector('.onair-show-chat-start');
  assert(primary && primary.textContent.includes('type this in your Hermes agent chat') && primary.querySelector('code').textContent === '/onair-setup show approve bbbbbbbb', primary && primary.textContent);
  assert(!/canned stand-in reply/i.test(document.querySelector('[aria-labelledby="onair-show-title"]').textContent), 'show panel never offers the canned stand-in');
  const showSec0 = document.querySelector('section[aria-labelledby="onair-show-title"]');
  const setupSec0 = document.querySelector('section[aria-labelledby="onair-qs-title"]');
  assert(showSec0 && setupSec0 && !setupSec0.contains(showSec0) && (showSec0.compareDocumentPosition(setupSec0) & window.Node.DOCUMENT_POSITION_FOLLOWING), 'show section is its own first section');
  // 2b. a process that does have your model connection: Start the show here
  startHere = true;
  await flush(1200);
  await click('Start the show');
  const start = posts.find(([p]) => p === 'setup/quick/show/start');
  assert(start && start[1].digest === SHOW_DIGEST && start[1].confirm === true, JSON.stringify(posts));
  await flush(1200);
  // 3. the running show: truthful activity, spoken captions, chat outcomes, Stop/Resume/Send
  assert(text().includes('Private show: Pixel') && text().includes('Speaking'), text().slice(0, 900));
  const items = [...document.querySelectorAll('ol li')].map((li) => li.textContent);
  assert(items.some((t) => t.startsWith('\u{1F50A} Hey everyone')), 'spoken line marked: ' + JSON.stringify(items));
  assert(items.some((t) => t === 'Tin the tip first.'), 'caption-only line not marked spoken: ' + JSON.stringify(items));
  assert(text().includes('how does the lamp work? \u2014 answered'));
  assert(text().includes('Voice: kokoro') && text().includes('3/40 requests'), text().slice(0, 900));
  await click('Stop show');
  await flush(1200);
  assert(text().includes('Paused \u2014 nothing is shown or said') && button('Resume show') && !button('Stop show'), text().slice(0, 900));
  await click('Resume show');
  await flush(1200);
  assert(button('Stop show'), 'Stop is reachable again after Resume');
  const input = document.getElementById('onair-show-chat');
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  await act(async () => { setter.call(input, 'what are you building?'); input.dispatchEvent(new window.Event('input', { bubbles: true })); });
  await click('Send');
  const say = posts.find(([p]) => p === 'setup/quick/show/say');
  assert(say && say[1].text === 'what are you building?', JSON.stringify(posts));
  assert.deepEqual(posts.map(([p]) => p).filter((p) => p.startsWith('setup/quick/show/')), ['setup/quick/show/start', 'setup/quick/show/stop', 'setup/quick/show/resume', 'setup/quick/show/say']);
  await act(async () => { root2.unmount(); });
  // 4. show running WHILE the setup preview is live: the show is its own first section and its Stop show is the
  //    first stop control; the preview's Pause/Stop are labelled as preview-only and hold no show control.
  previewLive = true;
  const root3 = createRoot(document.getElementById('root'));
  await act(async () => { root3.render(React.createElement(Page)); });
  await flush(1300);
  const showSec = document.querySelector('section[aria-labelledby="onair-show-title"]');
  const prevSec = document.querySelector('section[aria-labelledby="onair-qs-title"]');
  assert(showSec && prevSec && prevSec.textContent.includes('Your private preview'), text().slice(0, 900));
  assert(!prevSec.contains(showSec) && !showSec.contains(prevSec), 'show is not nested in the preview');
  assert(showSec.compareDocumentPosition(prevSec) & window.Node.DOCUMENT_POSITION_FOLLOWING, 'show comes before the preview');
  const stops = [...document.querySelectorAll('button')].filter((b) => /^(Stop|Pause|Unpause|End)/.test(b.textContent.trim()));
  assert.equal(stops[0].textContent.trim(), 'Stop show', 'first stop-type control: ' + stops.map((b) => b.textContent).join(' | '));
  assert(showSec.contains(stops[0]));
  assert(![...prevSec.querySelectorAll('button')].some((b) => / show$/.test(b.textContent.trim())), 'no show control inside the preview');
  const prevGroup = prevSec.querySelector('[role="group"]');
  assert(prevGroup.getAttribute('aria-label').includes('not the show') && prevGroup.textContent.startsWith('Setup preview only:'), prevGroup.outerHTML);
  await click('Stop show');
  assert.equal(posts[posts.length - 1][0], 'setup/quick/show/stop', 'the first Stop reaches the SHOW: ' + JSON.stringify(posts.slice(-2)));
  showOverride = () => ({ok:true, running:null, state:'unknown', show:null, pending_show_plan:null, start_here:false,
    start_next_action:'Show state cannot be verified. Use the owning agent\'s Stop or End; do not start another show until the owner confirms it stopped.'});
  await flush(1200);
  const unknownSection = document.querySelector('section[aria-labelledby="onair-show-title"]');
  assert(unknownSection.textContent.includes('Show state cannot be verified'), unknownSection.textContent);
  assert(!unknownSection.textContent.includes('Shows start from your agent chat here') && !button('Start the show'), 'unknown must not offer a new show');
  showOverride = null;
  await act(async () => { root3.unmount(); });
  // 5. the persisted show plan's lifecycle on the normal dashboard (no model client): the primary action always
  //    follows pending_show_plan, so a used or replaced approval code is never kept on screen.
  startHere = false; previewLive = false; showMode = 'speaking';
  const codeShown = () => { const el = document.querySelector('.onair-show-chat-start code'); return el ? el.textContent : null; };
  const chatStartStep = () => { const el = document.querySelector('.onair-show-chat-start'); return !!(el && el.textContent.includes('Shows start from your agent chat here') && el.textContent.includes(NOT_HERE)); };
  showOverride = () => stoppedWith(showPlanOf('bbbbbbbb', SHOW_DIGEST));
  const root4 = createRoot(document.getElementById('root'));
  await act(async () => { root4.render(React.createElement(Page)); });
  await flush(1300);
  assert.equal(codeShown(), '/onair-setup show approve bbbbbbbb', text().slice(0, 900));
  // 5a. used in agent chat -> show running (pending:null) -> ended (pending:null): old code gone, start step back
  showOverride = () => showStatus();
  await flush(1200);
  assert(text().includes('Private show: Pixel') && !text().includes('approve bbbbbbbb'), 'running show, no old code: ' + text().slice(0, 900));
  showOverride = () => stoppedWith(null);
  await flush(1200);
  assert(!text().includes('approve bbbbbbbb'), 'consumed code is not offered after the show ended: ' + text().slice(0, 900));
  assert(chatStartStep(), 'start-from-agent-chat step restored: ' + text().slice(0, 900));
  assert(!button('Start the show') && !button('Review the show'));
  // 5b. the agent re-plans: the new code replaces the old one
  showOverride = () => stoppedWith(showPlanOf('bbbbbbbb', SHOW_DIGEST));
  await flush(1200);
  assert.equal(codeShown(), '/onair-setup show approve bbbbbbbb');
  showOverride = () => stoppedWith(showPlanOf('cccccccc', 'c'.repeat(64)));
  await flush(1200);
  assert.equal(codeShown(), '/onair-setup show approve cccccccc', text().slice(0, 900));
  assert(!text().includes('approve bbbbbbbb'), 'replaced code is gone');
  // 5c. used without the tab ever seeing it run (pending goes straight to null): cleared too
  showOverride = () => stoppedWith(null);
  await flush(1200);
  assert(!text().includes('approve cccccccc') && chatStartStep(), 'used code cleared: ' + text().slice(0, 900));
  await act(async () => { root4.unmount(); });
  // 6. ordinary start_here control: a plan made on this page is kept while it matches the persisted pending plan,
  //    and Start posts its digest; a persisted replacement made elsewhere supersedes it.
  startHere = true; localPending = null;
  showOverride = () => stoppedWith(localPending);
  const root5 = createRoot(document.getElementById('root'));
  await act(async () => { root5.render(React.createElement(Page)); });
  await flush(1300);
  const brief = document.getElementById('onair-show-brief');
  assert(brief && button('Review the show'), text().slice(0, 900));
  await act(async () => { setter.call(brief, 'desk lamps'); brief.dispatchEvent(new window.Event('input', { bubbles: true })); });
  await click('Review the show');
  await flush(2200);  // two polls: the local plan still matches the persisted pending plan
  assert(text().includes('This show will:') && button('Start the show'), 'local plan kept while it matches: ' + text().slice(0, 900));
  const planPost = posts.filter(([p]) => p === 'setup/quick/show/plan').pop();
  assert(planPost && planPost[1].answers.brief === 'desk lamps', JSON.stringify(posts.slice(-3)));
  const nStart = posts.filter(([p]) => p === 'setup/quick/show/start').length;
  await click('Start the show');
  const start2 = posts.filter(([p]) => p === 'setup/quick/show/start');
  assert(start2.length === nStart + 1 && start2[start2.length - 1][1].digest === 'd'.repeat(64) && start2[start2.length - 1][1].confirm === true, JSON.stringify(start2));
  await act(async () => { root5.unmount(); });
  localPending = null;
  const root6 = createRoot(document.getElementById('root'));
  await act(async () => { root6.render(React.createElement(Page)); });
  await flush(1300);
  await act(async () => { const b2 = document.getElementById('onair-show-brief'); setter.call(b2, 'soldering'); b2.dispatchEvent(new window.Event('input', { bubbles: true })); });
  await click('Review the show');
  await flush(1200);
  assert(text().includes('This show will:') && button('Start the show'));
  localPending = showPlanOf('eeeeeeee', 'e'.repeat(64));  // the agent re-planned elsewhere
  await flush(1200);
  await click('Start the show');
  const start3 = posts.filter(([p]) => p === 'setup/quick/show/start').pop();
  assert.equal(start3[1].digest, 'e'.repeat(64), 'a superseded local plan is dropped for the persisted one');
  await act(async () => { root6.unmount(); });
  console.log('private show GUI: all checks passed');
})().catch((error) => { console.error(error); process.exit(1); });
