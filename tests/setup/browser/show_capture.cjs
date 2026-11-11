// ONE browser start: the private agent-hosted show in the REAL dashboard bundle + real plugin_api + real show
// runtime (server.py with ONAIR_SHOW_FIXTURE: model and text_to_speech edges are disclosed fixtures). Frames are
// captured BEFORE soft checks, so one failed check never loses later frames. Env: ONAIR_BASE, ONAIR_OUT,
// ONAIR_PLAYWRIGHT, DESK_TEST_CHROMIUM. Not a recording; no audio device.
const fs = require('fs');
const {chromium} = require(process.env.ONAIR_PLAYWRIGHT || 'playwright');
const BASE = process.env.ONAIR_BASE, OUT = process.env.ONAIR_OUT;
fs.mkdirSync(OUT + '/shots', {recursive: true});
const report = {source: process.env.SRC_HEAD, browserStarts: 1, recording: false, steps: [], failures: [], shots: [],
  metrics: [], consoleErrors: [], pageErrors: []};
const check = (ok, msg, extra) => { report.steps.push({check: msg, ok: !!ok, extra}); if (!ok) report.failures.push(msg + (extra ? ' ' + JSON.stringify(extra).slice(0, 500) : '')); };
const api = (page, path, body) => page.evaluate(async ([p, b]) => (await fetch(p, b === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)})).json(), ['/api/plugins/on-air/' + path, body]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function shot(page, name, sel) {
  if (sel) { try { await page.locator(sel).first().scrollIntoViewIfNeeded({timeout: 3000}); } catch (e) { /* frame anyway */ } }
  const metrics = await page.evaluate(() => ({innerWidth, innerHeight, dpr: devicePixelRatio, docWidth: document.documentElement.scrollWidth}));
  await page.screenshot({path: OUT + '/shots/' + name + '.png', fullPage: true});
  report.shots.push(name); report.metrics.push(Object.assign({name}, metrics));
  check(metrics.docWidth <= metrics.innerWidth, name + ': no document-level horizontal overflow', metrics);
}
async function waitFor(page, fn, ms) { const end = Date.now() + ms; while (Date.now() < end) { if (await fn()) return true; await sleep(200); } return false; }
(async () => {
  const browser = await chromium.launch({executablePath: process.env.DESK_TEST_CHROMIUM, args: ['--no-sandbox', '--mute-audio']});
  try {
    const ctx = await browser.newContext({viewport: {width: 1440, height: 1000}});
    const page = await ctx.newPage();
    page.on('pageerror', (e) => report.pageErrors.push(String(e)));
    page.on('console', (m) => { if (m.type() === 'error') report.consoleErrors.push(m.text().slice(0, 200)); });
    await page.goto(BASE + '/on-air');
    // the agent's side: a persisted plan with richer name/topics/style (same engine contract as onair_setup_plan)
    const plan = await api(page, 'setup/quick/plan', {answers: {channel: 'cozy_fox', agent_name: 'Pixel', use_model: false, style: {tone: 'upbeat', topics: ['desk lamps', 'soldering'], persona: 'a cheerful maker fox who loves tiny electronics'}}});
    check(plan.ok === true, 'plan made', plan.errors);
    await page.goto(BASE + '/on-air');
    await page.getByRole('button', {name: 'Start here'}).click().catch(() => {});
    await waitFor(page, async () => (await page.textContent('body')).includes('waiting for your approval'), 5000);
    await shot(page, 'desktop-1-pending-plan', '.onair-qs-plan');
    const body1 = await page.textContent('body');
    check(body1.includes('Co-host Pixel') && body1.includes('topics desk lamps, soldering') && body1.includes('tone upbeat'), 'pending plan shows name/topics/tone');
    await page.getByRole('button', {name: 'Approve: set up & preview'}).click();
    check(await waitFor(page, async () => (await page.textContent('body')).includes('Run a private show'), 30000), 'show panel reachable after setup');
    await page.fill('#onair-show-brief', 'building a desk lamp that dims when chat goes quiet');
    await page.getByRole('button', {name: 'Review the show'}).click();
    await waitFor(page, async () => (await page.textContent('body')).includes('This show will:'), 5000);
    await shot(page, 'desktop-2-show-plan', '#onair-show-title');
    await page.getByRole('button', {name: 'Start the show'}).click();
    check(await waitFor(page, async () => (await page.textContent('body')).includes("I'm Pixel"), 20000), 'model-written opening on the captions');
    await page.fill('#onair-show-chat', 'how does the lamp know chat is quiet?');
    await page.getByRole('button', {name: 'Send'}).click();
    check(await waitFor(page, async () => (await page.textContent('body')).includes('lamp listens'), 20000), 'typed private chat answered');
    await sleep(1500);
    await shot(page, 'desktop-3-show-running', '#onair-show-title');
    const st = await api(page, 'setup/quick/show/status');
    report.show = {activity: st.show.activity, lines: st.show.lines, chat: st.show.chat, model: st.show.model, voice: st.show.voice};
    check(st.show.lines.some((l) => l.audio), 'released lines carried audio events (HermesTTS via the dispatcher fixture)');
    await page.setViewportSize({width: 390, height: 844});
    await sleep(800);
    await shot(page, 'phone-1-show-running', '#onair-show-title');
    await page.getByRole('button', {name: 'Stop show'}).click();
    check(await waitFor(page, async () => (await api(page, 'setup/quick/show/status')).show.activity === 'paused', 5000), 'Stop pauses the show');
    await sleep(1200);
    await shot(page, 'phone-2-show-paused', '#onair-show-title');
    await page.getByRole('button', {name: 'Resume show'}).click();
    check(await waitFor(page, async () => (await api(page, 'setup/quick/show/status')).show.mode === 'autonomous', 5000), 'Resume continues the show');
    await page.getByRole('button', {name: 'End show'}).click();
    check(await waitFor(page, async () => (await api(page, 'setup/quick/show/status')).running === false, 10000), 'End closes the show');
    await api(page, 'setup/quick/cancel', {});
  } catch (e) {
    report.failures.push('aborted ' + String(e && e.stack || e).slice(0, 800));
  } finally {
    await browser.close();
    fs.writeFileSync(OUT + '/RESULT.json', JSON.stringify(report, null, 1));
  }
})();
