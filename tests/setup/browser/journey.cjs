// ONE browser start, two contexts: desktop 1280 first run and phone 390 returning user, against the real
// plugin_api + engine (server.py). Raw frames are captured BEFORE assertions; a failed check is recorded and
// the next independent leg still runs. Env: ONAIR_BASE, ONAIR_OUT, ONAIR_PLAYWRIGHT, DESK_TEST_CHROMIUM.
const fs = require('fs');
const {chromium} = require(process.env.ONAIR_PLAYWRIGHT || 'playwright');
const {previewGeometry, geometryProblems} = require('./geometry.cjs');
const BASE = process.env.ONAIR_BASE, URL = BASE + '/on-air', OUT = process.env.ONAIR_OUT;
fs.mkdirSync(OUT + '/shots', {recursive: true});
const report = {source: process.env.SRC_HEAD, browserStarts: 1, recording: false, legs: {}, steps: [], failures: [],
  geometry: [], overlay: [], consoleErrors: [], responses4xx5xx: [], shots: []};
let phase = 'start';
const check = (ok, msg, extra) => { report.steps.push(Object.assign({phase, check: msg, ok: !!ok}, extra || {})); if (!ok) report.failures.push(phase + ': ' + msg + (extra ? ' ' + JSON.stringify(extra).slice(0, 600) : '')); return !!ok; };
const api = (page, path) => page.evaluate(async (p) => (await fetch(p)).json(), '/api/plugins/on-air/' + path);
function watch(page) {
  page.on('pageerror', (e) => report.failures.push(phase + ': pageerror ' + String(e)));
  page.on('console', (m) => { if (m.type() === 'error') report.consoleErrors.push({phase, text: m.text().slice(0, 200)}); });
  page.on('response', (r) => { if (r.status() >= 400) report.responses4xx5xx.push({phase, path: new globalThis.URL(r.url()).pathname, status: r.status()}); });
}
async function shot(page, name, sel) {
  if (sel) { try { await page.locator(sel).first().scrollIntoViewIfNeeded({timeout: 3000}); } catch (e) { /* frame anyway */ } }
  const p = OUT + '/shots/' + name + '.png';
  await page.screenshot({path: p, fullPage: true});
  report.shots.push(name);
}
async function geometry(page, label, opts) {
  const g = await previewGeometry(page);
  report.geometry.push(Object.assign({label}, g));
  for (const p of geometryProblems(g, opts)) check(false, 'geometry ' + label + ': ' + p);
  if (!geometryProblems(g, opts).length) check(true, 'geometry ' + label + ' (screen in card' + (opts && opts.caption ? ', caption in screen, not over PRIVATE tag' : '') + ')');
  return g;
}
async function leg(name, fn) {
  phase = name;
  try { await fn(); report.legs[name] = 'ran'; } catch (e) { report.legs[name] = 'aborted'; report.failures.push(name + ': aborted ' + String(e && e.stack || e).slice(0, 800)); }
}
// Subscribe a real overlay client BEFORE Show, then Show, then read what that same client received.
async function overlayLeg(ctx, page, label, approve) {
  const st = await api(page, 'setup/quick/status');
  const ovUrl = st.preview && st.preview.overlay_url;
  check(ovUrl && !/:18790\//.test(ovUrl + '/'), label + ' private overlay on an ephemeral port', {ovUrl: String(ovUrl).replace(/\?.*/, '')});
  const ov = await ctx.newPage(); watch(ov);
  await ov.goto(ovUrl); await ov.waitForTimeout(1200);
  const before = await ov.evaluate(() => document.body.innerText);
  await ov.screenshot({path: OUT + '/shots/' + label + '-overlay-before.png'}); report.shots.push(label + '-overlay-before');
  const pending = (await api(page, 'setup/quick/status')).preview.last_caption;
  check(!/canned setup-test reply/.test(before), label + ' overlay shows nothing before approval', {before: before.slice(0, 120)});
  await approve();
  let after = '';
  for (let i = 0; i < 40 && !/canned setup-test reply/.test(after); i++) { await ov.waitForTimeout(250); after = await ov.evaluate(() => document.body.innerText); }
  await ov.screenshot({path: OUT + '/shots/' + label + '-overlay-after.png'}); report.shots.push(label + '-overlay-after');
  const got = /canned setup-test reply/.test(after);
  report.overlay.push({label, url: String(ovUrl).replace(/\?.*/, ''), before: before.slice(0, 160), after: after.slice(0, 160), receivedAfterApproval: got, captionBefore: pending});
  check(got, label + ' subscribed overlay received the approved caption');
  await ov.close();
}

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.DESK_TEST_CHROMIUM, args: ['--no-sandbox', '--disable-dev-shm-usage']});
  try {
    const desk = await browser.newContext({viewport: {width: 1280, height: 900}});
    const page = await desk.newPage(); watch(page);
    await leg('desktop-1280', async () => {
      await page.goto(URL); await page.getByRole('heading', {name: 'Start On Air'}).waitFor({timeout: 15000});
      await shot(page, 'desktop-1-entry', '.onair-qs');
      check(await page.getByText('ask your own Hermes agent').count(), 'entry: agent-first note');
      await page.locator('#onair-qs-channel').click(); await page.keyboard.type('cozy_fox');
      await page.getByLabel('Tone').selectOption('relaxed'); await page.getByLabel('Replies').selectOption('less_often');
      await page.getByRole('button', {name: 'Set up & preview'}).click();
      await page.getByText('This will:').waitFor({timeout: 15000});
      await shot(page, 'desktop-2-plan', '.onair-qs-plan');
      const effects = await page.locator('.onair-qs-plan li').allTextContents();
      check(effects.some((e) => e.includes('reply pacing: normal -> less_often')), 'plan: style effects listed', {effects});
      check(await page.getByText(/\/onair-setup approve [0-9a-f]{8}/).count(), 'plan: chat approval code shown');
      const none = await api(page, 'setup/quick/status');
      check(none.job === null && none.saved_stage === null, 'plan changed nothing before approval');
      await page.getByRole('button', {name: 'Approve: set up & preview'}).click();
      try { await page.locator('.onair-qs-progress').waitFor({timeout: 1500}); await shot(page, 'desktop-3-progress', '.onair-qs-progress'); } catch (e) { report.steps.push({phase, check: 'progress frame not caught (job finished first)', ok: true}); }
      await page.getByRole('button', {name: 'Show this reply'}).waitFor({timeout: 30000});
      await shot(page, 'desktop-4-preview', '.onair-qs-causal');
      await geometry(page, 'desktop-preview');
      const st = await api(page, 'setup/quick/status');
      const se = st.preview.style_in_effect;
      const want = 'Style in use: tone ' + se.tone + ' \u00b7 reply pacing ' + String(se.reply_pacing).replace('_', ' ') + ' \u00b7 ' + se.reply_length + ' replies.';
      check(await page.getByText(want, {exact: true}).count(), 'style line equals backend', {want});
      check(st.saved_stage.style.reply_pacing === 'less_often' && se.routine_gap_s === 300, 'saved + consumer style');
      check(st.real_stage && st.real_stage.set_up === false && st.real_stage.steps.length > 0, 'status lists the remaining real-show steps', {n: st.real_stage && st.real_stage.steps.length});
      await overlayLeg(desk, page, 'desktop', async () => { await page.getByRole('button', {name: 'Show this reply'}).click(); await page.locator('.onair-preview-caption').waitFor({timeout: 15000}); });
      await page.waitForTimeout(300);
      await shot(page, 'desktop-5-shown', '.onair-qs-causal');
      await geometry(page, 'desktop-shown', {caption: true});
      await page.getByRole('button', {name: 'Pause'}).click();
      await page.getByRole('button', {name: 'Unpause'}).waitFor({timeout: 10000});
      await shot(page, 'desktop-6-result-paused', '.onair-qs-causal');
      check((await api(page, 'setup/quick/job')).preview.privacy === true, 'pause read back');
      await page.getByRole('button', {name: 'Stop'}).click();
      await page.getByRole('heading', {name: 'Welcome back'}).waitFor({timeout: 15000});
      await shot(page, 'desktop-7-stopped', '.onair-qs');
      check((await api(page, 'setup/quick/status')).preview.running === false, 'stop: preview not running');
    });
    await desk.close();
    const phone = await browser.newContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, deviceScaleFactor: 2});
    const p2 = await phone.newPage(); watch(p2);
    await leg('phone-390', async () => {
      await p2.goto(URL); await p2.getByRole('heading', {name: 'Welcome back'}).waitFor({timeout: 15000});
      await shot(p2, 'phone-1-entry', '.onair-qs');
      check(await p2.getByText('Saved: twitch.tv/cozy_fox').count(), 'phone entry: saved channel');
      await p2.getByRole('button', {name: 'Resume preview'}).tap();
      await p2.getByText('This will:').waitFor({timeout: 15000});
      await shot(p2, 'phone-2-plan', '.onair-qs-plan');
      await p2.getByRole('button', {name: 'Approve: set up & preview'}).tap();
      await p2.getByRole('button', {name: 'Show this reply'}).waitFor({timeout: 30000});
      await shot(p2, 'phone-3-preview', '.onair-qs-causal');
      await geometry(p2, 'phone-preview');
      const st = await api(p2, 'setup/quick/status');
      check(st.job.kind === 'resume' && st.preview.style_in_effect.routine_gap_s === 300, 'phone resume keeps style');
      await overlayLeg(phone, p2, 'phone', async () => { await p2.getByRole('button', {name: 'Show this reply'}).tap(); await p2.locator('.onair-preview-caption').waitFor({timeout: 15000}); });
      await p2.waitForTimeout(300);
      await shot(p2, 'phone-4-shown', '.onair-preview-screen');
      await geometry(p2, 'phone-shown', {caption: true});
      await shot(p2, 'phone-5-result', '.onair-qs-causal');
      await p2.getByRole('button', {name: 'Stop'}).tap();
      await p2.getByRole('heading', {name: 'Welcome back'}).waitFor({timeout: 15000});
      await shot(p2, 'phone-6-stopped', '.onair-qs');
      check((await api(p2, 'setup/quick/status')).preview.running === false, 'phone stop: preview not running');
    });
    await phone.close();
  } finally {
    await browser.close();
    fs.writeFileSync(OUT + '/RESULT.json', JSON.stringify(report, null, 2));
    console.log('FAILURES ' + report.failures.length);
  }
})();
