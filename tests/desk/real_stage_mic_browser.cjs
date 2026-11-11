const fs=require('node:fs'), path=require('node:path');
const {chromium}=require(process.env.ONAIR_PLAYWRIGHT), esbuild=require('esbuild');
const assert=require('node:assert/strict');
(async()=>{
  await esbuild.build({stdin:{contents:`
    const React=require('react'); const {createRoot}=require('react-dom/client');
    window.__HERMES_PLUGIN_SDK__={React,fetchJSON:async(url,opts)=>{
      if(url.endsWith('setup/status')) return {ok:true,desk_connected:true};
      const r=await fetch(url,opts); return await r.json();
    }};
    window.__HERMES_PLUGINS__={register:(_,Page)=>createRoot(document.getElementById('root')).render(React.createElement(Page))};`,resolveDir:process.cwd()},
    bundle:true,platform:'browser',nodePaths:process.env.NODE_PATH.split(path.delimiter),outfile:process.env.ONAIR_MIC_SDK});
  const browser=await chromium.launch({headless:true,executablePath:process.env.DESK_TEST_CHROMIUM,args:[
    '--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream','--mute-audio',
    '--use-file-for-fake-audio-capture='+process.env.ONAIR_MIC_WAV]});
  try {
    const width=Number(process.env.ONAIR_MIC_WIDTH), page=await browser.newPage({viewport:{width,height:900}});
    const errors=[];page.on('pageerror',e=>errors.push(String(e)));
    await page.goto(process.env.ONAIR_MIC_URL);
    await page.locator('.onair-secondary summary').click();
    await page.getByRole('button',{name:'Full desk (advanced)',exact:true}).click();
    await page.locator('.onair-talk-disclosure > summary').waitFor({timeout:15000});
    const readable = await page.evaluate(() => {
      const text=document.querySelector('.onair-talk-disclosure > summary');
      const parse=x=>x.match(/[\d.]+/g).slice(0,3).map(Number);
      const fg=parse(getComputedStyle(text).color), root=document.querySelector('.onair-desk');
      let node=root, bg;
      while(node) {const c=getComputedStyle(node).backgroundColor; if(!c.endsWith(', 0)') && c!=='transparent') {bg=parse(c);break;} node=node.parentElement;}
      bg=bg||[255,255,255];
      const lum=c=>c.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
      const a=lum(fg),b=lum(bg); return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    });
    assert(readable>=4.5,`Talk disclosure contrast ${readable}`);
    if (!await page.locator('.onair-talk-disclosure').evaluate(el=>el.open)) await page.locator('.onair-talk-disclosure > summary').click();
    const panel=page.locator('[aria-label="Host line"]');
    const hold=panel.locator('.onair-host-mic button');
    await hold.waitFor({timeout:15000});
    const shot=async (name,target=panel)=>{
      if(!process.env.ONAIR_MIC_SHOTS) return;
      await target.evaluate(el=>window.scrollBy(0,el.getBoundingClientRect().top-40));
      await page.screenshot({path:path.join(process.env.ONAIR_MIC_SHOTS,`${width}-${name}.png`)});
    };
    const waitStatus=async expr=>page.waitForFunction(async expression=>{
      const data=await (await fetch('/fixture/status')).json();
      return Function('data','return ('+expression+')')(data);
    },expr,{timeout:15000});
    await panel.scrollIntoViewIfNeeded();await shot('idle');
    await hold.hover();await page.mouse.down();await panel.getByText(/Listening/).first().waitFor();await shot('listening');
    await page.waitForTimeout(1100);await page.mouse.up();await panel.getByText(/Transcribing/).first().waitFor();await shot('transcribing');
    await panel.getByText(/Transcript ready/).waitFor({timeout:15000});await shot('review');
    assert.equal(await panel.locator('input:not([type])').inputValue(),'Hello from the synthetic host.');
    await panel.getByRole('button',{name:'Send to co-host'}).click();
    await waitStatus("data.receipts.some(r=>r.kind==='host' && r.disposition==='queued')");
    await panel.getByText(/Host line: Queued at position 1 at send time/).waitFor({timeout:3000});
    await panel.getByText(/Transcript ready in Host line/).waitFor({state:'hidden'});await shot('sent');
    const approve=page.getByRole('button',{name:'Approve line 1'});
    await approve.waitFor({timeout:15000});await approve.scrollIntoViewIfNeeded();await shot('draft',page.locator('[aria-labelledby="onair-queue"]'));
    const overlay=await browser.newPage({viewport:{width,height:900}});
    await overlay.addInitScript(() => {const Native=window.EventSource;window.EventSource=class extends Native {constructor(...args){super(...args);window.__events=this;}};});
    await overlay.goto(process.env.ONAIR_OVERLAY_URL);
    await overlay.waitForFunction(()=>window.__events && window.__events.readyState===1);
    await approve.click();
    await waitStatus("data.events.some(e=>e.type==='caption' && e.text==='Hello from the co-host.') && data.state.airing !== null");
    await overlay.getByText('Hello from the co-host.').waitFor();
    if(process.env.ONAIR_MIC_SHOTS) await overlay.screenshot({path:path.join(process.env.ONAIR_MIC_SHOTS,`${width}-published-overlay.png`)});
    await shot('published',page.locator('[aria-labelledby="onair-queue"]'));
    // The same owner gesture cuts the actual currently airing stage line first.
    await hold.scrollIntoViewIfNeeded();await hold.hover();await page.mouse.down();
    await panel.getByText(/Listening/).first().waitFor();await shot('cut-recording');
    await waitStatus("data.receipts.some(r=>r.kind==='control' && r.action==='cut_in') && data.events.some(e=>e.type==='caption_clear')");
    await overlay.locator('#caption').waitFor({state:'hidden'});
    await overlay.close();
    await page.waitForTimeout(1100);await page.mouse.up();await panel.getByText(/Transcript ready/).waitFor({timeout:15000});await shot('new-review');
    await panel.getByRole('button',{name:'Send to co-host'}).click();
    await waitStatus("data.receipts.filter(r=>r.kind==='host' && r.disposition==='queued').length === 2");
    // Real desk safety key requests privacy while recording; the clip is discarded.
    await hold.hover();await page.mouse.down();await panel.getByText(/Listening/).first().waitFor();
    await page.keyboard.press('p');await page.mouse.up();
    await panel.getByText(/discarded/i).first().waitFor();
    await waitStatus("data.state.privacy === true");await shot('privacy-discarded');
    const result=await (await page.request.get(process.env.ONAIR_MIC_URL+'fixture/status')).json();
    assert.equal(result.receipts.filter(r=>r.kind==='host' && r.disposition==='queued').length,2);
    assert.deepEqual(errors,[]);
  } finally {await browser.close();}
  console.log('real stage fake-audio journey passed');
})().catch(e=>{console.error(e.stack||e);process.exitCode=1;});
