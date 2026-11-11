// Headless work-view driver. The caller owns the server and gives only synthetic data.
const fs = require('fs');
const readline = require('readline');
const {chromium} = require(process.env.ONAIR_PLAYWRIGHT);
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.ONAIR_BROWSER_EXECUTABLE});
  const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(process.env.ONAIR_PRESENT_URL);
  console.log(JSON.stringify({ready: true}));
  for await (const line of readline.createInterface({input: process.stdin})) {
    const step = JSON.parse(line);
    if (step.quit) break;
    try {
      await page.setViewportSize({width: step.width, height: step.height});
      await page.waitForFunction(([w,h]) => Math.abs(parseFloat(getComputedStyle(document.querySelector('#stage')).getPropertyValue('--window')) - Math.min(w/1920,h/1080)) < .0001, [step.width, step.height]);
      await page.waitForFunction(mode => document.querySelector('#stage').dataset.mode === mode, step.mode, {timeout: 7000});
      if (step.contains) await page.waitForFunction(text => document.querySelector('#work').textContent.includes(text), step.contains, {timeout: 7000});
      const info = await page.evaluate(() => {
        const stage = document.querySelector('#stage'), work = document.querySelector('#work');
        const panes = [...document.querySelectorAll('.work-pane')];
        const terminal = document.querySelector('.work-pane[data-kind="terminal"] .pane-content');
        const last = terminal && terminal.lastElementChild;
        const rect = el => { let r = el.getBoundingClientRect(); return [r.left,r.top,r.right,r.bottom]; };
        return {mode: stage.dataset.mode, viewport: [innerWidth, innerHeight],
          documentOverflow: document.documentElement.scrollWidth > innerWidth || document.documentElement.scrollHeight > innerHeight,
          work: rect(work), font: panes.length ? parseFloat(getComputedStyle(panes[0].querySelector('.pane-content')).fontSize) : null,
          terminalTail: !terminal || (last.getBoundingClientRect().bottom <= terminal.getBoundingClientRect().bottom + 1 && last.getBoundingClientRect().top >= terminal.getBoundingClientRect().top - 1),
          more: [...document.querySelectorAll('.pane-more')].map(e => e.textContent),
          railOverflow: (() => { const r = document.querySelector('#work-steps'); return !r.hidden && r.scrollHeight > r.clientHeight + 1; })(),
          stepCount: document.querySelectorAll('.step').length,
          injected: document.querySelectorAll('#work script, #work img, #work b').length,
          text: work.textContent, privacyClean: !document.querySelector('#work-title').textContent && !document.querySelector('#work-status').textContent && !document.querySelector('#work-panes').textContent && !document.querySelector('#work-steps').textContent};
      });
      fs.mkdirSync(process.env.ONAIR_OUT, {recursive:true});
      await page.screenshot({path: process.env.ONAIR_OUT + '/' + step.name + '-' + step.width + '.png'});
      console.log(JSON.stringify({ok:true, info, errors:errors.splice(0)}));
    } catch (e) { console.log(JSON.stringify({ok:false, error:String(e)})); }
  }
  await browser.close();
})().catch(e => {console.error(e); process.exitCode = 1;});
