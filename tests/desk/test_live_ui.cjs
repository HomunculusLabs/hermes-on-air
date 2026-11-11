const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/'});
global.window=dom.window;global.document=dom.window.document;Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});global.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'),{createRoot}=require('react-dom/client'),{act}=React;
let state, pending, views, calls=[], deny='', offline=false, confirm=false, confirms=[], hold=null, receipts=[], receiptHold=null, stateHold=null, stateGets=0;
const initial=()=>({mode:'approval',privacy:false,muted:false,epoch:7,live:true,show:'Test show',beats_left:26,airing:null,voice_boot:'a'.repeat(32),host_turns:{enabled:true}});
window.confirm=m=>{confirms.push(m);return confirm;};
window.__HERMES_PLUGIN_SDK__={React,fetchJSON:async(url,opt)=>{
 const p=url.replace('/api/plugins/on-air/','');
 if(opt){const body=JSON.parse(opt.body);calls.push({p,body});if(hold && p==='approve')await hold;if(deny===p || deny===p+':'+body.line_id)return {ok:false,error:'Refused by the show'};
 if(p==='approve'||p==='reject')pending=pending.filter(x=>x.line_id!==body.line_id);
 if(p==='privacy')state.privacy=body.on;if(p==='mode')state.mode=body.mode;if(p==='stop')state.mode='paused';if(p==='mute')state.muted=body.on;
 return {ok:true};}
 if(offline && (p==='state'||p==='pending'))throw Error('offline');
 if(p==='state'){stateGets++;if(stateHold)await stateHold;return {...state};}if(p==='pending')return {epoch:state.epoch,pending:[...pending]};
 if(p.startsWith('receipts')){if(receiptHold)await receiptHold;return {receipts:[...receipts]};}if(p==='views/state')return views;
 throw Error('Unexpected route '+p);
}};
vm.runInThisContext(fs.readFileSync('dashboard/dist/live.js','utf8'));
const root=createRoot(document.getElementById('root'));
const tick=()=>new Promise(r=>setTimeout(r,30));
const line=(id,turn='t',source='host')=>({line_id:id,turn_id:turn,epoch:7,source,text:'Line '+id});
const HostLine=({online})=>React.createElement('input',{'aria-label':'Host input','data-online':String(online)});
async function mount(){await act(async()=>{root.render(React.createElement(window.OnAirLive,{key:Math.random(),HostLine}));await tick();});}
async function click(text){const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===text);assert(b,text);await act(async()=>{b.click();await tick();});}
(async()=>{
 state=initial();pending=[];views=null;let releaseReceipt;receiptHold=new Promise(r=>releaseReceipt=r);await mount();
 assert(document.querySelector('.live-status-title').textContent.includes('Approval'),'slow optional receipts must not block the Live state');
 receiptHold=null;await act(async()=>{releaseReceipt();await tick();});
 let releaseState;stateGets=0;stateHold=new Promise(r=>releaseState=r);await mount();
 assert.match(document.querySelector('.live-status-title').textContent,/connecting/i,'a returning view must not claim disconnection before a failure');
 await act(async()=>{await new Promise(r=>setTimeout(r,1150));});assert.equal(stateGets,1,'a slow healthy core poll must not be superseded every second');
 stateHold=null;await act(async()=>{releaseState();await tick();});assert(document.querySelector('.live-status-title').textContent.includes('Approval'));
 stateHold=new Promise(r=>releaseState=r);await act(async()=>{await new Promise(r=>setTimeout(r,5200));});
 assert.match(document.querySelector('.live-status-title').textContent,/Disconnected/,'a timed-out core request fails closed');assert([...document.querySelectorAll('.live-controls button')].every(b=>b.disabled));
 stateHold=null;await act(async()=>{releaseState();await new Promise(r=>setTimeout(r,1100));});assert.match(document.querySelector('.live-status-title').textContent,/Approval/,'a healthy stage recovers after a timeout');
 state=initial();pending=[line('1'),line('2'),line('3'),line('4','other')];views=null;receipts=[{kind:'line',disposition:'withheld'},{kind:'line',disposition:'withheld'},{kind:'chat',disposition:'skipped'}];await mount();
 assert.match(document.querySelector('.live-count').textContent,/Next: 1 of 4/);assert.match(document.querySelector('.live-subtitle').textContent,/withheld 2/);receipts=[];
 assert(document.body.textContent.includes('Host turn'));assert(document.body.textContent.includes('2 more lines in this reply · 1 other waiting'));
 assert(!document.querySelector('.live-picture'));
 deny='approve';await click('Approve all 3');assert.equal(calls.length,1);assert(document.querySelector('.live-warning').textContent.includes('Refused by the show'));
 deny='';calls=[];await click('Approve all 3');assert.deepEqual(calls.map(x=>x.body.line_id),['1','2','3']);assert(document.body.textContent.includes('Line 4'));
 state=initial();pending=[line('1'),line('2'),line('3')];calls=[];deny='approve:2';await mount();await click('Approve all 3');
 assert.deepEqual(calls.map(x=>x.body.line_id),['1','2']);assert.match(document.querySelector('.live-warning').textContent,/Approval stopped: Refused by the show/);assert.equal(pending.length,2);deny='';
 state=initial();pending=[line('1'),line('2'),line('3')];calls=[];confirm=true;let release;hold=new Promise(r=>release=r);await mount();await click('Approve all 3');await click('Stop');await act(async()=>{release();await tick();});hold=null;
 assert.deepEqual(calls.filter(x=>x.p==='approve').map(x=>x.body.line_id),['1']);assert(calls.some(x=>x.p==='stop'));assert.match(document.querySelector('.live-warning').textContent,/Approval stopped/);
 state=initial();pending=[{...line('n'),turn_id:undefined},{...line('m'),turn_id:undefined}];await mount();assert(![...document.querySelectorAll('button')].some(b=>b.textContent.includes('Approve all')));
 state=initial();pending=[line('host-draft','host-turn')];receipts=[{kind:'host',id:'request',disposition:'queued',epoch:7,text:'What should we build?'},{kind:'host',id:'request',disposition:'dispatched',turn_id:'host-turn'}];await mount();
 assert.equal(document.querySelector('.live-context').textContent,'You asked: “What should we build?”');assert.equal(document.querySelector('.live-context').title,'You asked: “What should we build?”');
 receipts=[];await act(async()=>{await new Promise(r=>setTimeout(r,1100));});assert.equal(document.querySelector('.live-context').textContent,'Host turn');
 state=initial();pending=[line('5')];calls=[];await mount();
 const input=document.querySelector('[aria-label="Host input"]');await act(async()=>{input.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));await tick();});assert.equal(calls.length,0);
 await act(async()=>{document.body.dispatchEvent(new window.KeyboardEvent('keydown',{key:'a',bubbles:true}));await tick();});assert.equal(calls[0].p,'approve');assert(document.body.textContent.includes('Voice is being prepared'));
 // The controller sets airing before synthesis starts; only an aired receipt proves release.
 state.airing='5';await act(async()=>{await new Promise(r=>setTimeout(r,1100));});assert(document.body.textContent.includes('Voice is being prepared'));
 receipts=[{kind:'aired',line_id:'5'}];await act(async()=>{await new Promise(r=>setTimeout(r,1100));});assert(!document.body.textContent.includes('Voice is being prepared'));receipts=[];
 state=initial();pending=[line('dropped')];await mount();await click('Approve ↵A');receipts=[{kind:'line_dropped',line_id:'dropped',reason:'runtime_refused'}];await act(async()=>{await new Promise(r=>setTimeout(r,1100));});assert.match(document.querySelector('.live-warning').textContent,/runtime_refused/);assert(!document.body.textContent.includes('Voice is being prepared'));receipts=[];
 // Airing is set before synthesis; only the matching aired receipt proves words reached stream.
 state=initial();state.mode='autonomous';state.airing='now';pending=[];receipts=[{kind:'line',disposition:'released',line_id:'now',text:'Not aired yet'}];await mount();
 assert(!document.body.textContent.includes('Not aired yet'));
 receipts=[{kind:'aired',line_id:'old',text:'Old line'},{kind:'aired',line_id:'now',text:'Words on stream now'}];
 await act(async()=>{await new Promise(r=>setTimeout(r,1100));});assert.equal(document.querySelector('.live-line').textContent,'Words on stream now');assert.match(document.querySelector('.live-context').textContent,/speaking now/i);
 pending=[line('waiting')];await act(async()=>{await new Promise(r=>setTimeout(r,1100));});assert.equal(document.querySelector('.live-line').textContent,'Line waiting');assert.match(document.querySelector('.live-speaking').textContent,/Speaking now: Words on stream now/);assert(document.querySelector('.live-speaking .live-dot.is-on'));
 state.airing=null;await act(async()=>{await new Promise(r=>setTimeout(r,1100));});assert(!document.body.textContent.includes('Words on stream now'));assert(!document.querySelector('.live-speaking'));pending=[];
 state=initial();state.airing='now';receipts=[{kind:'aired',line_id:'now',text:'Approval on stream'}];await mount();assert.equal(document.querySelector('.live-line').textContent,'Approval on stream');
 state.privacy=true;await act(async()=>{await new Promise(r=>setTimeout(r,1100));});assert(!document.body.textContent.includes('Approval on stream'));receipts=[];
 state=initial();pending=[];state.host_turns.waiting=1;await mount();assert.match(document.querySelector('.live-line').textContent,/thinking/i,'queued host turn is not idle');
 state.host_turns.waiting=0;state.in_flight=true;await act(async()=>{await new Promise(r=>setTimeout(r,1100));});assert.match(document.querySelector('.live-line').textContent,/thinking/i,'in-flight turn is not idle');
 state.in_flight=false;await act(async()=>{await new Promise(r=>setTimeout(r,1100));});assert.match(document.querySelector('.live-line').textContent,/nothing waiting/i);
 state=initial();state.mode='off_air';pending=[];await mount();assert.equal(document.querySelectorAll('.live-talk input').length,1,'Off air keeps one composer');
 state=initial();await mount();offline=true;await act(async()=>{await new Promise(r=>setTimeout(r,1100));});assert.equal(document.querySelectorAll('.live-talk input').length,1,'A lost connection keeps one composer');offline=false;
 state=initial();pending=[];state.privacy=true;calls=[];confirms=[];confirm=false;await mount();assert(document.querySelector('.live-private').textContent.includes('PRIVACY: viewers see the cover'));
 await click('Privacy');await click('Stop');assert.equal(calls.length,0);assert.equal(confirms.length,2);
 confirm=true;await click('Privacy');assert.deepEqual(calls[0],{p:'privacy',body:{on:false}});
 state=initial();pending=[];calls=[];confirms=[];confirm=false;await mount();
 const modeSelect=document.querySelector('select[aria-label="Show mode"]');assert(modeSelect,'Mode is a compact selector, not a second tab bar');
 async function chooseMode(value){await act(async()=>{modeSelect.value=value;modeSelect.dispatchEvent(new window.Event('change',{bubbles:true}));await tick();});}
 await chooseMode('autonomous');assert.equal(calls.length,0);assert.match(confirms[0],/without desk approval/);
 confirm=true;await chooseMode('autonomous');assert.deepEqual(calls[0],{p:'mode',body:{mode:'autonomous'}});
 state=initial();state.mode='autonomous';pending=[line('6')];calls=[];confirm=false;await mount();await click('Approve ↵A');assert.equal(calls.length,0);
 state=initial();pending=[];calls=[];await mount();assert([...document.querySelectorAll('.live-controls button')].some(x=>x.textContent==='Mute voice'));await click('Mute voice');assert.deepEqual(calls[0],{p:'mute',body:{on:true}});
 offline=true;await mount();assert(document.body.textContent.includes('Desk cannot reach the show'));assert([...document.querySelectorAll('.live-controls button')].every(x=>x.disabled));
 await act(async()=>root.unmount());dom.window.close();console.log('live UI passed');
})().catch(e=>{console.error(e);root.unmount();dom.window.close();process.exitCode=1});
