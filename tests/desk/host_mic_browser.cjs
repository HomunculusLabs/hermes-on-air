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
    '--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream',
    '--use-file-for-fake-audio-capture='+process.env.ONAIR_MIC_WAV]});
  try {
    const page=await browser.newPage({viewport:{width:1280,height:900}});
    const errors=[]; page.on('pageerror',e=>errors.push(String(e)));
    await page.goto(process.env.ONAIR_MIC_URL);
    await page.getByRole('button',{name:'Details',exact:true}).click();
    await page.locator('.onair-talk-disclosure > summary').click();
    const panel=page.locator('[aria-label="Host line"]');
    const button=panel.getByRole('button',{name:'Hold to talk'});
    await button.waitFor({timeout:15000}); await panel.scrollIntoViewIfNeeded();
    const shot=async name=>{if(process.env.ONAIR_MIC_SHOTS) await panel.screenshot({path:path.join(process.env.ONAIR_MIC_SHOTS,name+'.png')});};
    await shot('idle');
    await button.hover(); await page.mouse.down();
    await panel.getByText(/Listening/).first().waitFor(); await shot('listening');
    await page.waitForTimeout(1100); await page.mouse.up();
    await panel.getByText(/Transcribing/).first().waitFor(); await shot('transcribing');
    await panel.getByText(/Transcript ready/).waitFor({timeout:15000}); await shot('review');
    assert.equal(await panel.locator('input:not([type])').inputValue(),'Hello from the synthetic host.');
    assert.deepEqual(await (await page.request.get(process.env.ONAIR_MIC_URL+'fixture/calls')).json(),[]);
    await panel.getByRole('button',{name:'Send to co-host'}).click();
    await panel.getByText(/Queued at position 1/).waitFor();
    // The owner's press cuts an airing line before capturing under the new epoch.
    await page.request.post(process.env.ONAIR_MIC_URL+'fixture/airing');
    await button.hover(); await page.mouse.down();
    await panel.getByText(/Listening/).first().waitFor();
    assert.deepEqual(await (await page.request.get(process.env.ONAIR_MIC_URL+'fixture/cuts')).json(),[{epoch:7,boot:'a'.repeat(32)}]);
    await page.waitForTimeout(1100); await page.mouse.up();
    await panel.getByText(/Transcript ready/).waitFor({timeout:15000});
    // Wait for the desk poll to acknowledge the post-cut epoch before sending.
    await page.waitForTimeout(2200);
    await panel.getByRole('button',{name:'Send to co-host'}).click();
    await panel.getByText(/Queued at position 1/).waitFor();
    assert.equal((await (await page.request.get(process.env.ONAIR_MIC_URL+'fixture/calls')).json())[1].epoch,8);
    // A reviewed transcript remains bound to its original press across a cut.
    await button.hover(); await page.mouse.down(); await panel.getByText(/Listening/).first().waitFor();
    await page.waitForTimeout(1100); await page.mouse.up();
    await panel.getByText(/Transcript ready/).waitFor({timeout:15000});
    await page.request.post(process.env.ONAIR_MIC_URL+'fixture/cut');
    await page.waitForTimeout(2200);
    await panel.getByText(/Draft cleared because the show state changed/).waitFor();
    assert.equal(await panel.locator('input:not([type])').inputValue(), '');
    assert.equal((await (await page.request.get(process.env.ONAIR_MIC_URL+'fixture/calls')).json()).length, 2);
    await page.request.post(process.env.ONAIR_MIC_URL+'fixture/auto');
    await page.reload();
    await page.locator('.onair-talk-disclosure > summary').click();
    await panel.getByRole('radio',{name:/Send as soon as I release/}).click();
    await panel.getByText('Microphone mode saved.').waitFor();
    assert.equal(await panel.getByRole('radio',{name:/Send as soon as I release/}).isChecked(), true);
    const micButton=panel.locator('.onair-host-mic button');
    await micButton.click();
    await panel.getByText(/click again to transcribe/).waitFor();
    await page.waitForTimeout(1100); await micButton.click();
    await panel.getByText(/The show changed/).waitFor({timeout:15000}); await shot('refused');
    assert.deepEqual(errors,[]);
  } finally {await browser.close();}
  console.log('fake audio browser passed');
})().catch(e=>{console.error(e.stack||e);process.exitCode=1;});
