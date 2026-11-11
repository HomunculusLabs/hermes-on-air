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
    const page=await browser.newPage({viewport:{width:1440,height:900}}), errors=[], uploads=[], requests=[];
    page.on('pageerror', e=>errors.push(String(e)));
    page.on('request',r=>{if(r.method()==='POST' && r.url().endsWith('/host/mic')) requests.push(r.headers());});
    page.on('response',async r=>{if(r.request().method()==='POST' && r.url().endsWith('/host/mic')) {try {uploads.push(await r.json());}catch(_){}}});
    await page.addInitScript(()=>{
      const native=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia=async constraints=>{const stream=await native(constraints); window.__testStream=stream;window.__testConstraints=constraints;return stream;};
    });
    const status=async()=>await (await page.request.get(process.env.ONAIR_MIC_URL+'fixture/status')).json();
    const waitStatus=async expression=>{
      const until=Date.now()+30000, predicate=Function('data','return ('+expression+')'); let data;
      while(Date.now()<until) {data=await status();if(predicate(data)) return data;await page.waitForTimeout(100);}
      throw Error('Timed out waiting for '+expression+'; last state '+JSON.stringify(data));
    };
    const shot=async name=>{if(process.env.ONAIR_MIC_SHOTS) await page.screenshot({path:path.join(process.env.ONAIR_MIC_SHOTS,name+'.png')});};
    await page.goto(process.env.ONAIR_MIC_URL);
    const details=page.locator('.onair-talk-disclosure');
    await details.locator('summary').waitFor({timeout:15000});
    await page.waitForFunction(()=>document.querySelector('.onair-talk-disclosure')?.open === true);
    const open=page.getByLabel('Open mic',{exact:true}); await open.waitFor();
    assert.equal(await open.isChecked(),false);
    await open.check();
    await page.getByText('Mic open: listening',{exact:false}).first().waitFor({timeout:10000});
    assert.match(await page.title(),/MIC OPEN/);
    await details.locator('summary').click();
    assert.equal(await details.evaluate(el=>el.open),false);
    assert.equal(await page.getByText('Mic open: listening',{exact:false}).isVisible(),true,'the indicator remains visible even when Talk is collapsed');
    await details.locator('summary').click();
    assert.deepEqual(await page.evaluate(()=>window.__testConstraints),{audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
    await shot('open-mic-listening');
    await waitStatus("data.receipts.filter(r=>r.kind==='host' && r.disposition==='queued').length>=1 && data.pending.length===1");
    const first=await status(); assert(first.pending.length===1,JSON.stringify(first)); const firstDraft=first.pending[0].line_id;
    await waitStatus("data.receipts.filter(r=>r.kind==='host' && r.disposition==='queued').length>=2");
    const waiting=await status();
    assert.equal(waiting.pending[0].line_id,firstDraft,'speaking while a draft waits must preserve it');
    assert.equal(waiting.state.epoch,first.state.epoch);
    assert.equal(waiting.receipts.filter(r=>r.action==='cut_in').length,0);
    await page.waitForFunction(()=>document.body.textContent.includes('Second synthetic sentence.'),null,{timeout:10000});
    assert.deepEqual(uploads.slice(0,2).map(x=>x.transcript),['First synthetic sentence.','Second synthetic sentence.']);
    const approve=page.getByRole('button',{name:'Approve line 1'}); await approve.waitFor({timeout:10000});
    await approve.click();
    await waitStatus("data.events.some(e=>e.type==='caption' && e.text==='Hello from the co-host.') && data.state.airing !== null");
    await waitStatus("data.receipts.some(r=>r.kind==='control' && r.action==='cut_in') && data.receipts.filter(r=>r.kind==='host' && r.disposition==='queued').length>=3");
    const afterCut=await status();
    assert.equal(afterCut.state.epoch,first.state.epoch+1);
    assert(afterCut.events.some(e=>e.type==='caption_clear'));
    await page.waitForFunction(()=>document.body.textContent.includes('Another synthetic sentence.'),null,{timeout:10000});
    assert.equal(uploads[2].transcript,'Another synthetic sentence.');
    assert.equal(requests.length,3);
    assert(requests.every(r=>r['x-onair-capture']==='open' && r['x-onair-mode']==='send' && r['x-onair-boot']===first.state.voice_boot));
    assert.deepEqual(requests.map(r=>Number(r['x-onair-epoch'])),[first.state.epoch,first.state.epoch,afterCut.state.epoch]);
    assert.equal(new Set(requests.map(r=>r['x-onair-id'])).size,3);
    assert.equal(await open.isChecked(),true,'the owned cut must not close the mic');
    await waitStatus("data.pending.length===1");
    await page.getByRole('button',{name:'Approve line 1'}).click();
    await waitStatus("data.receipts.filter(r=>r.kind==='aired').length>=2");
    await page.getByRole('button',{name:/Privacy cut/}).click();
    await page.waitForFunction(()=>window.__testStream.getTracks().every(t=>t.readyState==='ended'));
    await page.getByText(/Open mic turned off/).first().waitFor();
    assert.match(await page.locator('.onair-mic-notice').innerText(),/privacy was requested/i);
    assert.equal(await open.isChecked(),false); assert.doesNotMatch(await page.title(),/MIC OPEN/);
    assert.equal(await page.getByText('Mic open: listening',{exact:false}).count(),0);
    await page.locator('.onair-open-mic').scrollIntoViewIfNeeded(); await shot('open-mic-auto-off');
    await page.reload(); await details.locator('summary').waitFor();
    if (!await details.evaluate(el=>el.open)) await details.locator('summary').click();
    assert.equal(await page.getByLabel('Open mic',{exact:true}).isChecked(),false);
    assert.deepEqual(errors,[]);
    assert((await status()).state.privacy);
  } finally {await browser.close();}
  console.log('real stage open mic fake-audio journey passed');
})().catch(e=>{console.error(e.stack||e);process.exitCode=1;});
