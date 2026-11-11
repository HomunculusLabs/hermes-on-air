/* Synthetic screenshot fixture; loads the real plugin, Live and microphone assets. */
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),{chromium}=require('playwright'),esbuild=require('esbuild');
const output=process.env.ONAIR_LIVE_SHOTS;if(!output)throw Error('Set ONAIR_LIVE_SHOTS');
const assets='dashboard/dist/';
// Use the host's actual default theme and design-system font tokens, not a fixture-only font.
const sdk=process.env.ONAIR_HERMES_SDK;if(!sdk)throw Error('Set ONAIR_HERMES_SDK');
const hostCss=fs.readFileSync(path.join(sdk,'web/src/index.css'),'utf8');
const themeVars=hostCss.match(/:root\s*\{([\s\S]*?)\n\}/)[1];
const dsCss=fs.readFileSync(path.join(sdk,'node_modules/@nous-research/ui/dist/ui/globals.css'),'utf8');
const dsFont=dsCss.match(/--font-sans\s*:\s*[^;]+;/)[0];
(async()=>{
 const bundle=await esbuild.build({stdin:{contents:`
 const React=require('react'),{createRoot}=require('react-dom/client');
 const q=new URLSearchParams(location.search), scenario=q.get('scenario');
 const line=(id,source,turn='reply')=>({line_id:id,epoch:7,turn_id:turn,source,text:'Ooh, let’s build something small and silly first. A tiny synth that plays a note whenever chat says hello.'});
 const st=window.fixtureState={mode:scenario==='off'?'off_air':scenario==='onstream'?'autonomous':'approval',privacy:scenario==='privacy',muted:false,epoch:7,live:scenario!=='off',show:'your stage',beats_left:26,airing:scenario==='onstream'?'one':null,voice_boot:'a'.repeat(32),host_turns:{enabled:true}};
 let pending=window.fixturePending=['empty','off','privacy','lost','onstream'].includes(scenario)?[]:[line('one',scenario==='chat'?'chat':scenario==='beat'?'beat':'host')];
 if(scenario==='chat'){pending[0].user='River';pending[0].question='What should we build next?'}
 if(scenario==='host')pending[0].host_text='What should we build next?';
 if(scenario==='multi')pending.push(line('two','host'),line('three','host'),line('four','chat','different'));
 const boxes={slots:{main:'scene',sub1:'slides',sub2:'window'},arrangement:'stack',inset_corner:'bottom-right',canvas:{width:1920,height:1080},geometry:{main:{bounds:{x:0,y:0,width:1920,height:1080}},sub1:{bounds:{x:1488,y:566,width:384,height:216}},sub2:{bounds:{x:1488,y:302,width:384,height:216}}},available:{scene:{available:true},slides:{available:true},window:{available:true}}};
 window.fixtureCalls=[];
 window.__HERMES_PLUGIN_SDK__={React,fetchJSON:async(url,opts)=>{const p=url.replace('/api/plugins/on-air/','');
  if(opts){const body=typeof opts.body==='string'?JSON.parse(opts.body):{};window.fixtureCalls.push({path:p,body});if(p==='privacy')st.privacy=body.on;if(p==='host/mic/mode')return {ok:true,mode:body.mode,locked:false};if(p==='host/mic')return {ok:true,queued:true,position:1,transcript:'Test line'};if(scenario==='refusal' && p==='approve')return {ok:false,error:'The show refused this line.'};if(p==='approve'||p==='reject')pending=pending.filter(x=>x.line_id!==body.line_id);if(scenario==='preparing' && p==='approve')st.airing=body.line_id;return {ok:true};}
  if(scenario==='lost'&&(p==='state'||p==='pending'))throw Error('offline');
  if(p==='state')return {...st};if(p==='pending')return {epoch:7,pending};if(p.startsWith('receipts'))return {receipts:scenario==='onstream'?[{kind:'aired',line_id:'one',text:line('one','host').text}]:[]};
  if(p==='views/state')return {ok:true,connected:true,program_scene:'On Air: Boxes',boxes};if(p==='host/mic')return {enabled:true,open_enabled:scenario!=='no-open',cut_in_on_talk:true};if(p==='host/mic/mode')return {ok:true,mode:'send',locked:false};if(p==='setup/status')return {ok:true,desk_connected:true};throw Error('fixture route '+p);
 }};
 window.__HERMES_PLUGINS__={register:(name,App)=>createRoot(document.getElementById('root')).render(React.createElement(App))};
 `,resolveDir:process.cwd()},bundle:true,write:false,platform:'browser',nodePaths:process.env.NODE_PATH.split(path.delimiter)});
 const app=bundle.outputFiles[0].text;
 // Always provide fake audio: no browser launch in this harness may use a real device.
 const audioDir=fs.mkdtempSync(path.join(process.env.TMPDIR || os.tmpdir(),'onair-audio-'));
 const audio=process.env.ONAIR_FAKE_AUDIO || path.join(audioDir,'fixture.wav');
 if(!process.env.ONAIR_FAKE_AUDIO){
  const rate=48000,count=rate*6,wav=Buffer.alloc(44+count*2);
  wav.write('RIFF',0);wav.writeUInt32LE(36+count*2,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(rate,24);wav.writeUInt32LE(rate*2,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(count*2,40);
  for(let i=0;i<count;i++)wav.writeInt16LE(Math.floor(i/rate)%3===1?Math.round(5000*Math.sin(2*Math.PI*440*i/rate)):0,44+i*2);
  fs.writeFileSync(audio,wav);
 }
 let browser;
 try {browser=await chromium.launch({headless:true,executablePath:process.env.DESK_TEST_CHROMIUM,args:['--no-sandbox','--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream','--use-file-for-fake-audio-capture='+audio]});}
 catch(error){fs.rmSync(audioDir,{recursive:true,force:true});throw error;}
 const scenarios=process.env.ONAIR_LIVE_ON_ONLY?['mic-on']:process.env.ONAIR_LIVE_OFF_ONLY?['off']:process.env.ONAIR_LIVE_FOCUS?['mic-off','mic-on','more','mic-privacy','no-open']:['host','chat','beat','multi','preparing','empty','onstream','off','privacy','refusal','lost','mic-off','mic-on','more','mic-privacy','no-open'];
 const variants=process.env.ONAIR_LIVE_PHONE_ONLY?[['phone',390,900],['phone-light',390,900]]:process.env.ONAIR_LIVE_PHONE_LIGHT?[['phone-light',390,900]]:[['dark',1440,900],['light',1440,900],['phone',390,900],['phone-light',390,900],...(process.env.ONAIR_LIVE_FOCUS?[]:[['wide1100',1100,900],['wide1920',1920,1080]])];
 const report=[];
 try {
  for(const [theme,width,height] of variants){
   const page=await browser.newPage({viewport:{width,height}});const errors=[];page.on('pageerror',e=>errors.push(String(e)));
   await page.addInitScript(()=>{const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);window.fixtureTracks=[];navigator.mediaDevices.getUserMedia=async constraints=>{const stream=await original(constraints);window.fixtureTracks.push(...stream.getTracks());return stream;};});
  await page.route('https://live.test/**',async route=>{
    const u=new URL(route.request().url());let body,type='text/javascript';
    if(u.pathname==='/'){
     type='text/html';const light=theme==='light'||theme==='phone-light';const vars=light?'--color-foreground:#202125;--color-background:#f7f7fa;--color-muted-foreground:#555b64;--color-secondary:#e5e5ec;--color-secondary-foreground:#202125;--color-border:#9298a2;--color-card:#dedfe4;--color-primary:#654bb0;--color-primary-foreground:#fff;--color-destructive:#a32936':'--color-foreground:#f1f0f5;--color-background:#111116;--color-muted-foreground:#b5b4c0;--color-secondary:#24232d;--color-secondary-foreground:#f4f3f7;--color-border:#595864;--color-card:#252530;--color-primary:#8064ca;--color-primary-foreground:#fff;--color-destructive:#ff9292';
     body='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>:root{'+themeVars+dsFont+vars+'}body{margin:0;background:var(--color-background);color:var(--color-foreground);font-family:var(--theme-font-sans)} button,input,strong{font-family:var(--font-sans)}</style><link rel="stylesheet" href="/style.css"></head><body><div id="root"></div><script src="/fixture.js"></script><script src="/index.js"></script></body></html>';
    }else if(u.pathname==='/fixture.js')body=app;
    else {const name=u.pathname.split('/').pop();if(!['index.js','live.js','host-mic.js','style.css','live.css'].includes(name)){await route.abort();return;}body=fs.readFileSync(assets+name);type=name.endsWith('.css')?'text/css':'text/javascript';}
    await route.fulfill({status:200,contentType:type,body});
   });
   for(const scenario of (theme.startsWith('wide')?['host']:scenarios)){
    errors.length=0;await page.goto('https://live.test/?scenario='+scenario);await page.waitForSelector('.onair-live');
    await page.waitForFunction(s=>s==='lost'?document.querySelector('.live-subtitle')?.textContent.includes('cannot reach'):document.querySelector('.live-status-title')?.textContent.includes(s==='off'?'Off air':s==='privacy'?'PRIVACY':s==='onstream'?'Autonomous':'Approval'),scenario);
    if(scenario==='preparing'||scenario==='refusal'){await page.locator('.live-approve').click();await page.waitForFunction(s=>document.querySelector(s==='preparing'?'.live-line':'.live-warning').textContent.includes(s==='preparing'?'prepared':'refused'),scenario);}
    if(['mic-off','mic-on','more','mic-privacy','no-open'].includes(scenario)) {
    await page.waitForSelector('.compact-mic');
    if(scenario==='no-open') {if(!await page.getByRole('button',{name:'Hold to talk'}).isVisible())throw Error('PTT fallback missing');}
    else {
     const pill=page.locator('.onair-mic-pill');await pill.waitFor();
     if(await pill.getAttribute('aria-pressed')!=='false')throw Error('Mic must initially be off');
     if(scenario==='more') {
      await page.locator('.onair-mic-more summary').focus();await page.keyboard.press('Enter');
      if(!await page.getByText('After recording').isVisible() || !await page.getByRole('button',{name:'Hold to talk'}).isVisible())throw Error('More controls missing: '+await page.locator('.compact-mic').evaluate(e=>e.outerHTML));
      await page.getByRole('checkbox',{name:'Keyboard shortcuts'}).check();
      await page.getByRole('radio',{name:/Review before sending/}).check();
      await page.waitForFunction(()=>window.fixtureCalls.some(c=>c.path==='host/mic/mode'&&c.body.mode==='review'));
      const overlaps=await page.evaluate(()=>{const a=document.querySelector('.live-approve').getBoundingClientRect(),b=document.querySelector('.onair-mic-popover').getBoundingClientRect();return a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;});
      if(overlaps)throw Error('More covers Approve: '+theme);
     }
     if(scenario==='mic-on'||scenario==='mic-privacy') {
      await pill.click();await page.waitForSelector('.onair-live-mic-badge');
      await page.waitForFunction(()=>window.fixtureTracks.some(t=>t.readyState==='live') && document.querySelector('.onair-mic-pill meter'));
      if(await pill.getAttribute('aria-pressed')!=='true')throw Error('Mic pill not on');
      await page.waitForFunction(()=>document.querySelector('.onair-mic-pill meter').value>0,null,{timeout:10000});
      await page.waitForTimeout(1200);
      if(!await page.locator('.onair-live-mic-badge').isVisible() || !await page.evaluate(()=>window.fixtureTracks.some(t=>t.readyState==='live')))throw Error('Live track lost its safety indicator during refresh');
      const coversSafety=await page.evaluate(()=>{const a=document.querySelector('.onair-live-mic-badge').getBoundingClientRect();return [...document.querySelectorAll('.live-controls > button')].some(e=>{const b=e.getBoundingClientRect();return a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;});});
      if(coversSafety)throw Error('Mic indicator covers safety controls');
      if(scenario==='mic-privacy') {
       await page.getByRole('button',{name:'Privacy'}).click();
       await page.waitForFunction(()=>window.fixtureTracks.length && window.fixtureTracks.every(t=>t.readyState==='ended') && !document.querySelector('.onair-live-mic-badge'));
       await page.waitForFunction(()=>document.querySelector('.live-private'));
      }
     }
    }
   }
   if(['mic-off','mic-on','more','no-open'].includes(scenario)){
    const layout=await page.evaluate(()=>{
     const selectors=['.compact-mic > button','.onair-mic-more summary','.onair-field input','.onair-host-line > button:not(.onair-cut-button)','.onair-cut-button','.live-view > button'];
     return selectors.map(selector=>{const e=document.querySelector(selector),r=e.getBoundingClientRect();return {selector,x:r.x,y:r.y,right:r.right,bottom:r.bottom,height:r.height};});
    });
    if(layout.some(r=>r.height<43.5||r.height>56.5))throw Error('Composer target height: '+JSON.stringify(layout));
    const picture=await page.locator('.live-picture').boundingBox();if(!picture || picture.height>layout[2].height+1)throw Error('Picture exceeds composer row height');
    for(let i=0;i<layout.length;i++)for(let j=i+1;j<layout.length;j++){const a=layout[i],b=layout[j];if(a.x<b.right&&a.right>b.x&&a.y<b.bottom&&a.bottom>b.y)throw Error('Composer controls overlap: '+JSON.stringify([a,b]));}
   }
   if(await page.locator('.live-talk .onair-field input, .live-composer-placeholder input').count()!==1)throw Error('Composer must not be duplicated');
   await page.waitForTimeout(150);
   const fonts=await page.evaluate(()=>{const family=getComputedStyle(document.querySelector('.onair-live')).fontFamily;return [...document.querySelectorAll('.live-status-title strong,.live-line,.live-controls button,.live-talk input')].map(e=>({family:getComputedStyle(e).fontFamily,expected:family}));});
   if(fonts.some(x=>x.family!==x.expected))throw Error('Live inherited a different font: '+JSON.stringify(fonts));
   if(scenario==='mic-on')await page.waitForFunction(()=>document.querySelector('.onair-mic-pill meter').value>.05,null,{timeout:10000});const file=`${theme}-${scenario}.png`;await page.screenshot({path:path.join(output,file),fullPage:true});
    const metrics=await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio,documentWidth:document.documentElement.scrollWidth,documentHeight:document.documentElement.scrollHeight,text:document.querySelector('.onair-live').innerText,small:[...document.querySelectorAll('.onair-live button')].filter(b=>!b.closest('details:not([open])') && b.getClientRects().length && b.getBoundingClientRect().height<43.5).map(b=>b.textContent)}));
    if(scenario==='empty'){
     const before=await page.locator('.live-bottom').evaluate(el=>el.getBoundingClientRect().top+scrollY);
     await page.evaluate(()=>window.fixturePending.push(...[1,2,3,4].map(n=>({line_id:'incoming'+n,epoch:7,turn_id:n===4?'other':'new',source:'beat',text:'A new line arrives.'}))));
     await page.waitForSelector('.live-approve');
     const after=await page.locator('.live-bottom').evaluate(el=>el.getBoundingClientRect().top+scrollY);
     if(Math.abs(before-after)>1)throw Error('Footer moved when a line arrived: '+before+' to '+after);
    }
    if(scenario==='host'){
     const expected=await page.evaluate(()=>[...document.querySelectorAll('.onair-app button:not(:disabled), .onair-app select:not(:disabled), .onair-app input:not(:disabled):not([type=radio]), .onair-app input[type=radio]:checked:not(:disabled), .onair-app [tabindex="0"], .onair-app summary')].filter(el=>el.getClientRects().length && (!el.closest('details:not([open])') || el.tagName==='SUMMARY')).map((el,i)=>{el.dataset.liveFocus=String(i);return i;}));
     const seen=[], bad=[];await page.locator('.onair-secondary summary').focus();
     for(let i=0;i<expected.length+2;i++){
      const item=await page.evaluate(()=>{const e=document.activeElement;if(!e || !e.closest('.onair-app'))return null;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {id:Number(e.dataset.liveFocus),ring:s.outlineStyle!=='none'&&parseFloat(s.outlineWidth)>=2,visible:r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight};});
      if(!item)break;seen.push(item.id);if(i>0&&(!item.ring||!item.visible))bad.push(item);await page.keyboard.press('Tab');
     }
     if(bad.length||expected.some(x=>!seen.includes(x)))throw Error('Keyboard focus failed: '+JSON.stringify({expected,seen,bad}));
     await page.locator('[data-live-focus="'+seen.at(-1)+'"]').focus();
     for(let i=seen.length-2;i>=0;i--){await page.keyboard.press('Shift+Tab');const active=await page.evaluate(()=>Number(document.activeElement.dataset.liveFocus));if(active!==seen[i])throw Error('Reverse keyboard order failed');}
     const field=page.locator('.onair-host-line > label.onair-field input');const send=page.locator('.onair-host-line > button:not(.onair-cut-button)');
     if(!await send.isDisabled())throw Error('Empty Send must be disabled');
     await field.fill('Hello');if(await send.isDisabled())throw Error('Send must enable for text');
     const colors=await send.evaluate(e=>({background:getComputedStyle(e).backgroundColor,primary:getComputedStyle(document.querySelector('.live-approve')).backgroundColor}));
     if(colors.background!==colors.primary)throw Error('Enabled Send is not primary');
     await field.fill('');await field.focus();await page.keyboard.press('a');
     if((await page.evaluate(()=>window.fixtureCalls)).some(c=>c.path==='approve'))throw Error('Typing triggered approval');
     await page.locator('.live-context').focus();await page.keyboard.press('Enter');
     await page.waitForFunction(()=>window.fixtureCalls.some(c=>c.path==='approve'));
    }
    if(scenario==='more'){
    const holdButton=page.getByRole('button',{name:'Hold to talk'});await holdButton.click();
    await page.waitForFunction(()=>window.fixtureTracks.some(t=>t.readyState==='live'));
    await page.getByRole('button',{name:/Listening/}).click();
    await page.waitForFunction(()=>window.fixtureTracks.length && window.fixtureTracks.every(t=>t.readyState==='ended') && window.fixtureCalls.some(c=>c.path==='host/mic'));
    await page.getByRole('radio',{name:/Send as soon as I release/}).check();
    await page.waitForFunction(()=>window.fixtureCalls.some(c=>c.path==='host/mic/mode' && c.body.mode==='send'));
    await page.locator('.live-line').focus();await page.keyboard.down('v');
    await page.waitForFunction(()=>window.fixtureTracks.length>=2 && window.fixtureTracks.some(t=>t.readyState==='live'));
    await page.keyboard.up('v');await page.waitForFunction(()=>window.fixtureTracks.every(t=>t.readyState==='ended') && window.fixtureCalls.filter(c=>c.path==='host/mic').length>=2);
   }
   if(scenario==='mic-on'){await page.locator('.onair-mic-pill').click();await page.waitForFunction(()=>window.fixtureTracks.every(t=>t.readyState==='ended')&&!document.querySelector('.onair-live-mic-badge'));}
   report.push({theme,scenario,file,metrics,errors:[...errors]});
   }
   await page.close();
  }
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
  if(report.some(x=>x.errors.length||x.metrics.documentWidth>x.metrics.width||x.metrics.small.length))throw Error('Browser errors, horizontal overflow or small buttons; see report');
  console.log(`captured ${report.length} synthetic screenshots`);
 }finally{await browser.close();fs.rmSync(audioDir,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1});
