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
let Page;
let epoch = 7;
let pending = [{ line_id: 'l1', epoch: 7, source: 'host', text: '<img src=x onerror=alert(1)> safe text' }];
let posts = [];
let offline = false;
let receiptsOffline = false;
let held = null;
let confirmOk = false;
let privacy = false;
let recentData = [
  {kind:'raid', name:'River', amount:37},
  {kind:'giftbomb', name:'Sky', amount:5},
  {kind:'bits', name:'Ash', amount:100},
  {kind:'sub', name:'Fern', months:1},
  {kind:'resub', name:'Moss', months:7},
  {kind:'subgift', name:'Lake'}
];
let receiptData = [
  {kind:'line', disposition:'withheld', rule:'never_say', text:'<script>no</script>', ts: Date.now()/1000 - 65},
  {kind:'chat', verdict:'skip', rule:'mystery_rule', user:'Viewer', text:'hello', ts: Date.now()/1000 - 10},
  {kind:'chat', verdict:'worthy', rule:'question', user:'Other', text:'question'}
];
const settings = { enabled: true, thank_you: true, include_messages: false, min_bits: 1, during_privacy: 'drop' };
window.confirm = () => confirmOk;
window.__HERMES_PLUGIN_SDK__ = { React, fetchJSON: async (url, options) => {
  const path = url.replace('/api/plugins/on-air/', '');
  if (options) {
    posts.push([path, JSON.parse(options.body)]);
    if (path === 'stop' && held) await held;
    if (path === 'approve' || path === 'reject') pending = [];
    return {ok: true};
  }
  if (offline && (path === 'state' || path === 'pending')) throw Error('offline');
  if (path === 'state') return {mode: 'approval', privacy, muted: false, epoch, live: true, seq: 3, rundown: 'exhausted', rundown_warning: 'No more prepared material', overlay_url: 'http://127.0.0.1:18790/overlay'};
  if (path === 'pending') return {epoch, pending};
  if (path.startsWith('receipts')) { if (receiptsOffline) throw Error('receipts unavailable'); return {receipts: receiptData, guard_counts: {withheld: 900, skipped: 800}}; }
  if (path === 'alerts') return {alerts: settings};
  if (path === 'alerts/recent') return {alerts: recentData};
  throw Error('Unexpected route ' + path);
}};
window.__HERMES_PLUGINS__ = {register: (name, page) => { assert.equal(name, 'on-air'); Page = page; }};
vm.runInThisContext(fs.readFileSync('dashboard/dist/index.js', 'utf8'));
const root = createRoot(document.getElementById('root'));
const settle = () => new Promise(resolve => setTimeout(resolve, 20));
(async () => {
  await act(async () => { root.render(React.createElement(Page)); await settle(); });
  await act(async () => { [...document.querySelectorAll('.onair-tab')].find(b => b.textContent === 'Details').click(); await settle(); });
  assert(document.body.textContent.includes('RUNDOWN: exhausted'));
  assert([...document.querySelectorAll('[role="alert"]')].some(el => el.textContent.includes('No more prepared material')));
  const guard = document.querySelector('[aria-labelledby="onair-guard"]');
  const queuePanel = document.querySelector('[aria-labelledby="onair-queue"]');
  const alertPanel = document.querySelector('[aria-labelledby="onair-alerts"]');
  const receiptPanel = document.querySelector('[aria-labelledby="onair-receipts"]');
  assert(queuePanel.textContent.includes('1 waiting'));
  assert(queuePanel.textContent.includes('Host turn'));
  assert(queuePanel.textContent.includes('Selected'));
  assert(queuePanel.querySelector('.onair-approve'));
  assert(guard.textContent.includes('1 line withheld · 1 chat skipped'));
  assert(!guard.textContent.includes('900'));
  assert(!guard.querySelector('.onair-scroll-hint'));
  assert(!guard.querySelector('.onair-overflow'));
  assert(!receiptPanel.textContent.includes('never_say'));
  assert(receiptPanel.textContent.includes('Never-say phrase'));
  assert.equal(alertPanel.querySelectorAll('.onair-recent-alert').length, 3);
  await act(async () => { [...alertPanel.querySelectorAll('button')].find(b => b.textContent.includes('more')).click(); });
  const alertRows = [...alertPanel.querySelectorAll('.onair-recent-alert')].map(row => row.textContent);
  for (const [name, expected] of [['River', '37 viewers'], ['Sky', '5 gift subs'], ['Ash', '100 bits'], ['Fern', '1 month'], ['Moss', '7 months'], ['Lake', 'gifted a sub']]) {
    assert(alertRows.some(row => row.includes(name) && row.includes(expected)), name + ': ' + expected);
  }
  assert.equal(alertPanel.querySelectorAll('.onair-recent-alert').length, 6);
  assert(!document.body.textContent.includes('Arming always'));
  privacy = true;
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
  assert(document.querySelector('.onair-privacy-banner').textContent.includes('audio blocked'));
  assert(document.querySelector('.onair-safety').textContent.includes('Privacy is active'));
  privacy = false;
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
  assert(guard.textContent.includes('Line withheld'));
  assert(guard.textContent.includes('Chat skipped'));
  assert(guard.textContent.includes('Never-say phrase'));
  assert(guard.textContent.includes('Unknown rule'));
  assert([...guard.querySelectorAll('code')].some(code => code.textContent === 'never_say'));
  assert(guard.textContent.includes('mystery_rule'));
  assert(guard.textContent.includes('1 min ago'));
  assert(guard.textContent.includes('Viewer'));
  assert(guard.textContent.includes('latest 60 receipts'));
  assert.equal(guard.querySelector('script'), null);
  assert.equal(guard.querySelectorAll('.onair-guard-name').length, 1);
  assert.equal(guard.querySelectorAll('.onair-guard-text').length, 2);
  assert.equal(guard.querySelectorAll('.onair-withheld').length, 1);
  assert.equal(guard.querySelectorAll('.onair-skipped').length, 1);
  receiptData = Array.from({length: 25}, (_, n) => ({kind:'chat', verdict:'skip', rule: n === 24 ? 'toString' : n === 23 ? 'duplicate' : 'slur', text:'item ' + n}));
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
  assert(guard.textContent.includes('25 chat skipped'));
  assert.equal(guard.querySelectorAll('li').length, 25);
  assert(guard.textContent.includes('item 24'));
  assert(guard.textContent.includes('item 0'));
  assert(guard.textContent.includes('Unknown rule'));
  const duplicate = [...guard.querySelectorAll('li')].find(li => li.textContent.includes('item 23'));
  assert(duplicate.textContent.includes('Repeat of their earlier message'));
  assert.equal(duplicate.querySelector('code').textContent, 'duplicate');
  const list = guard.querySelector('.onair-guard-list');
  list.getBoundingClientRect = () => ({top: 0, bottom: 100});
  [...list.children].forEach((row, i) => {
    row.getBoundingClientRect = () => ({top: i * 20, bottom: (i + 1) * 20});
  });
  await act(async () => { window.dispatchEvent(new window.Event('resize')); });
  assert(guard.textContent.includes('7 more'));
  assert.equal(list.querySelectorAll('.onair-guard-entry').length, 18);
  assert.equal(list.querySelectorAll('[aria-hidden="true"]').length, 0);
  const showAll = [...guard.querySelectorAll('button')].find(b => b.textContent.includes('Show all'));
  await act(async () => { showAll.click(); });
  assert.equal(list.querySelectorAll('.onair-guard-entry').length, 25);
  assert.equal(list.querySelectorAll('[aria-hidden="true"]').length, 0);
  assert(guard.textContent.includes('Show fewer'));
  assert(guard.textContent.includes('Time unavailable'));
  receiptData = Array.from({length: 65}, (_, n) => ({kind:'line', disposition:'withheld', rule:'duplicate', text:'line ' + n}));
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
  assert(guard.textContent.includes('60 lines withheld · 0 chat skipped'));
  assert.equal(guard.querySelectorAll('li').length, 60);
  assert(!guard.textContent.includes('line 0'));
  receiptData = [];
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
  assert(guard.textContent.includes('0 lines withheld · 0 chat skipped'));
  assert(!guard.querySelector('.onair-scroll-hint'));
  receiptsOffline = true;
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
  assert(guard.textContent.includes('Receipts unavailable'));
  assert(!guard.textContent.includes('0 lines withheld'));
  assert(receiptPanel.textContent.includes('Receipts unavailable'));
  receiptsOffline = false;
  assert(document.body.textContent.includes('APPROVAL'));
  assert(document.body.textContent.includes('LIVE'));
  assert(document.body.textContent.includes('activity: observed'));
  assert(document.body.textContent.includes('http://127.0.0.1:18790/overlay'));
  assert(document.body.textContent.includes('<img src=x onerror=alert(1)>'));
  assert.equal(document.querySelector('img'), null);
  await act(async () => { window.dispatchEvent(new window.KeyboardEvent('keydown', {key: 'a', bubbles: true})); await settle(); });
  assert.deepEqual(posts[0], ['approve', {line_id: 'l1', epoch: 7}]);
  assert(queuePanel.textContent.includes('Voice is being prepared...'));
  receiptData = [{kind:'aired',line_id:'unrelated'}];
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
  assert(queuePanel.textContent.includes('Voice is being prepared...'),'an unrelated receipt cannot clear the status');
  receiptData.push({kind:'aired',line_id:'l1'});
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
  assert(!queuePanel.textContent.includes('Voice is being prepared...'),'the matching aired receipt clears the status');
  epoch = 8; pending = [{line_id: 'l2', epoch: 8, text: 'Next line'}];
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
  assert(document.body.textContent.includes('Next line'));
  // autonomous stays unreachable from approval without confirm, and from nowhere else
  assert([...document.querySelectorAll('button')].find(b => b.textContent === 'Autonomous').disabled === false);
  confirmOk = false;
  await act(async () => { [...document.querySelectorAll('button')].find(b => b.textContent === 'Autonomous').click(); await settle(); });
  assert(!posts.some(p => p[0] === 'mode'));
  let release;
  held = new Promise(resolve => {release = resolve});
  await act(async () => { [...document.querySelectorAll('button')].find(b => b.textContent === 'Stop all (Esc)').click(); await settle(); });
  assert(!document.body.textContent.includes('Next line'));
  await act(async () => { window.dispatchEvent(new window.KeyboardEvent('keydown', {key: 'a', bubbles: true})); await settle(); });
  assert.equal(posts.filter(p => p[0] === 'approve').length, 1);
  pending = []; epoch = 9;
  await act(async () => { release(); await settle(); });
  assert.equal(posts[posts.length - 1][0], 'stop');
  offline = true;
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
  assert(document.body.textContent.includes('CONNECTION LOST'));
  assert([...document.querySelectorAll('button')].find(b => b.textContent === 'Stop all (Esc)').disabled);
  await act(async () => root.unmount());
  console.log('behavior: registration, safe text, epoch approval, overlay link, mode gate, stop race, disconnect passed');
})().catch(e => {console.error(e); root.unmount(); process.exitCode = 1;});
