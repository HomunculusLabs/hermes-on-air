// ONE non-recording headless Chromium start. Driven line-by-line from test_page_browser.py over stdin:
// each line is {"name", "expect": <JS expression evaluated in the page>, "shot": bool, "viewport": [w, h]?}. Replies one JSON line.
// Env: ONAIR_PLAYWRIGHT (existing playwright package), ONAIR_PRESENT_URL, ONAIR_OUT.
const fs = require('fs');
const readline = require('readline');
const {chromium} = require(process.env.ONAIR_PLAYWRIGHT || 'playwright');

(async () => {
  const out = process.env.ONAIR_OUT;
  fs.mkdirSync(out, {recursive: true});
  const browser = await chromium.launch({headless: true});
  const context = await browser.newContext({viewport: {width: 1920, height: 1080}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror ' + String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console ' + m.text().slice(0, 200)); });
  await page.goto(process.env.ONAIR_PRESENT_URL, {waitUntil: 'load'});
  const send = (o) => process.stdout.write(JSON.stringify(o) + '\n');
  send({ready: true});
  const rl = readline.createInterface({input: process.stdin});
  for await (const line of rl) {
    const step = JSON.parse(line);
    if (step.name === 'quit') break;
    let ok = false, value = null, err = null;
    try {
      if (step.viewport) await page.setViewportSize({width: step.viewport[0], height: step.viewport[1]});
      // Poll from the driver: the page's policy forbids evaluating strings inside it, which an in-page wait needs
      // as soon as the condition is not already true on the first look.
      const until = Date.now() + 8000;
      while (!(await page.evaluate(step.expect))) {
        if (Date.now() > until) throw new Error('timed out waiting for: ' + step.expect);
        await new Promise((r) => setTimeout(r, 50));
      }
      ok = true;
      value = await page.evaluate(() => ({
        mode: document.getElementById('stage').dataset.mode,
        heading: document.getElementById('heading').textContent,
        code: document.getElementById('code').textContent,
        standby: document.getElementById('standby-title').textContent,
        media: document.querySelectorAll('audio,video,iframe,object,embed').length,
        injected: document.querySelectorAll('#slide b, #slide script').length,
        slideHidden: document.getElementById('slide').hidden,
        standbyHidden: document.getElementById('standby').hidden,
        fit: document.getElementById('stage').dataset.fit,
        // Geometry in window pixels: the stage box, and the lowest edge of anything visible on the slide.
        stage: (() => { const b = document.getElementById('stage').getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom].map(Math.round); })(),
        lowest: Math.round(Math.max(0, ...[...document.getElementById('slide').children]
          .filter((e) => !e.hidden).map((e) => e.getBoundingClientRect().bottom))),
        window: [window.innerWidth, window.innerHeight],
      }));
      if (step.shot) await page.screenshot({path: out + '/' + step.name + '.png'});
    } catch (e) { err = String(e).slice(0, 300); }
    send({name: step.name, ok, value, err, errors: errors.splice(0)});
  }
  await browser.close();
  send({closed: true, browserStarts: 1, recording: false});
})().catch((e) => { process.stdout.write(JSON.stringify({fatal: String(e).slice(0, 400)}) + '\n'); process.exit(1); });
