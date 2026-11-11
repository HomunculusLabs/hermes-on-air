const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<!doctype html><title>Desk</title><div id="root"></div>',{url:'http://localhost/'});
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
Object.defineProperty(global,'crypto',{value:require('node:crypto').webcrypto,configurable:true});
global.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'), {act}=React, {createRoot}=require('react-dom/client');
const boot='a'.repeat(32);let state={epoch:1,voice_boot:boot,live:true,privacy:false,mode:'approval',airing:'line'};
let now=0, level=0, processAudio, interval, cuts=[], uploads=[], stopped=0, uploadMode='success', releaseUpload, ended, statuses=[];
Object.defineProperty(global.performance,'now',{value:()=>now,configurable:true});
const nativeInterval=global.setInterval, nativeClear=global.clearInterval;
global.setInterval=(fn,ms)=>{if(ms===50){interval=fn;return 12345;}return nativeInterval(fn,ms);};
global.clearInterval=id=>{if(id===12345){interval=null;return;}nativeClear(id);};
const track={stop(){stopped++},addEventListener(name,callback){if(name==='ended') ended=callback;}};
const stream={getTracks:()=>[track],getAudioTracks:()=>[track]};
navigator.mediaDevices={getUserMedia:async()=>stream};
class Context {
  constructor(){this.sampleRate=48000;this.destination={};}
  createMediaStreamSource(){return {connect(){},disconnect(){}};}
  createAnalyser(){return {fftSize:2048,getFloatTimeDomainData(a){a.fill(level);}};}
  createScriptProcessor(){const p={connect(){},disconnect(){}};processAudio=(samples)=>p.onaudioprocess({inputBuffer:{getChannelData:()=>samples}});return p;}
  createGain(){return {gain:{value:1},connect(){},disconnect(){}};}
  close(){return Promise.resolve();}
}
window.AudioContext=Context;
window.__HERMES_PLUGIN_SDK__={React,fetchJSON:async(url,opts)=>{
  if(url.endsWith('/host/mic/mode')) return {ok:true,mode:'review',locked:true};
  if(url.endsWith('/host/mic')&&!opts) return {enabled:true,open_enabled:true,cut_in_on_talk:true};
  if(url.endsWith('/state')) return {...state};
  if(url.endsWith('/cut-in')) {cuts.push(JSON.parse(opts.body));state={...state,epoch:state.epoch+1,airing:null};return {ok:true,cut:true,state:{...state}};}
  if(url.endsWith('/host/mic')) {
    uploads.push(opts);
    if(uploadMode==='held') return await new Promise(resolve=>{releaseUpload=resolve;});
    if(uploadMode==='refuse') return {ok:false,transcript:'synthetic',message:'Stage refused the line.'};
    return {ok:true,queued:true,position:1,transcript:'synthetic'};
  }
  throw Error(url);
}};
vm.runInThisContext(fs.readFileSync('dashboard/dist/host-mic.js','utf8'));
const root=createRoot(document.getElementById('root'));
const tick=()=>new Promise(r=>setTimeout(r,0));
async function frames(count,value){for(let i=0;i<count;i++) await act(async()=>{level=value;now+=50;processAudio(new Float32Array(2400).fill(value));interval();await tick();});}
const render=(online=true)=>root.render(React.createElement(window.OnAirHostMic,{data:state,online,onReview(){},onOpenStatus:value=>statuses.push(value)}));
(async()=>{
  await act(async()=>{render();await tick();});
  const detailsMic=document.querySelector('.onair-host-mic');
  assert.deepEqual([...detailsMic.children].map(e=>e.tagName),['DIV','FIELDSET','P','P','BUTTON','P','P']);
  assert.equal(detailsMic.querySelector('legend').textContent,'After recording');
  assert(!detailsMic.querySelector('.onair-mic-more'));
  const toggle=document.querySelector('.onair-open-mic input');assert(toggle && !toggle.checked);
  assert.match(document.querySelector('.onair-open-mic').textContent,/transcript directly.*Review below applies to hold-to-talk/);
  await act(async()=>{toggle.click();await tick();});
  assert(toggle.checked && /Mic open: listening/.test(document.body.textContent));
  document.title='New desk title'; await frames(1,0);assert.match(document.title,/MIC OPEN/);
  await frames(20,0);
  await frames(7,.1);assert.equal(cuts.length,0,'350 ms must not cut an airing line');
  await frames(3,.1);assert.equal(cuts.length,1,'400 ms continuous speech must request a cut');
  assert.deepEqual(cuts[0],{epoch:1,boot,only_if_airing:true});
  await frames(17,0);assert.equal(uploads.length,1);
  assert.equal(uploads[0].headers['X-OnAir-Epoch'],'2');assert.equal(uploads[0].headers['X-OnAir-Boot'],boot);
  assert.equal(uploads[0].headers['X-OnAir-Capture'],'open');assert.equal(uploads[0].headers['X-OnAir-Mode'],'send');
  const audio=await uploads[0].body.arrayBuffer();const view=new DataView(audio);assert.equal(view.getUint32(24,true),16000);
  let firstNonzero=0; while(firstNonzero<(audio.byteLength-44)/2 && view.getInt16(44+2*firstNonzero,true)===0) firstNonzero++;
  assert(firstNonzero>=4000 && firstNonzero<=5600,'the speech onset retains about 300 ms pre-roll');
  await frames(10,.1);await frames(17,0);assert.equal(uploads.length,2);assert.equal(cuts.length,1,'no cut when a draft waits and nothing airs');
  uploadMode='held';
  for(const value of [.05,.06,.07,.08]) {await frames(10,value);await frames(17,0);}
  assert.equal(uploads.length,3,'only one upload may be in flight');
  assert.match(document.body.textContent,/oldest waiting utterance dropped/);
  const answer={ok:true,queued:true,position:1,transcript:'synthetic'};
  await act(async()=>{releaseUpload(answer);await tick();});assert.equal(uploads.length,4);
  await act(async()=>{releaseUpload(answer);await tick();});assert.equal(uploads.length,5);
  await act(async()=>{releaseUpload(answer);await tick();});assert.equal(uploads.length,5);
  async function peak(item) {const data=new DataView(await item.body.arrayBuffer());let max=0;for(let i=44;i<data.byteLength;i+=2) max=Math.max(max,Math.abs(data.getInt16(i,true)));return Math.round(max/32767*100);}
  assert.deepEqual(await Promise.all(uploads.slice(2).map(peak)),[5,7,8],'oldest waiting clip is dropped, remaining clips stay ordered');
  assert.match(document.body.textContent,/oldest waiting utterance dropped/,'drop notice remains visible after later uploads');
  uploadMode='success';
  await frames(7,.1);await frames(17,0);assert.equal(uploads.length,5,'speech shorter than 400 ms is discarded');
  await frames(562,.1);await frames(17,0);assert.equal(uploads.length,6,'a long utterance is cut at the cap');
  const capped=new DataView(await uploads[5].body.arrayBuffer());
  assert(capped.getUint32(40,true)/32000 <= 30,'the WAV stays under the server duration limit');
  uploadMode='refuse';
  for(let i=0;i<3;i++) {await frames(10,.1);await frames(16,0);}
  assert.equal(stopped,1);assert.match(document.body.textContent,/three refused uploads/);assert(!toggle.checked);
  uploadMode='success'; await act(async()=>{render();await tick();});
  await act(async()=>{toggle.click();await tick();});assert(toggle.checked,'open mic restarted for hidden-tab test');
  let hidden=false, offset=0; const originalNow=Date.now; Date.now=()=>originalNow()+offset;
  Object.defineProperty(document,'hidden',{configurable:true,get:()=>hidden});
  await act(async()=>{hidden=true;document.dispatchEvent(new window.Event('visibilitychange'));await tick();});
  await act(async()=>{offset=61000;hidden=false;document.dispatchEvent(new window.Event('visibilitychange'));await tick();});
  Date.now=originalNow;
  assert.equal(stopped,2);assert.match(statuses.at(-1),/tab was hidden/);assert(!toggle.checked);
  await act(async()=>{toggle.click();await tick();});
  await act(async()=>{ended();await tick();});assert.equal(stopped,3);assert.match(statuses.at(-1),/permission was revoked/);
  await act(async()=>{toggle.click();await tick();});
  await act(async()=>{render(false);await tick();});assert.equal(stopped,4);assert.match(statuses.at(-1),/lost the stage/);
  await act(async()=>{render();await tick();});await act(async()=>{toggle.click();await tick();});
  state={...state,voice_boot:'b'.repeat(32)};
  await act(async()=>{render();await tick();});assert.equal(stopped,5);assert.match(statuses.at(-1),/show changed/);
  const beforePrivacy=uploads.length;
  await act(async()=>{toggle.click();await tick();});await frames(21,0);await frames(10,.1);
  state={...state,privacy:true,live:false,epoch:state.epoch+1};
  await act(async()=>{render();await tick();});assert.equal(stopped,6);assert.equal(uploads.length,beforePrivacy,'privacy discards the clip in progress');
  assert.match(statuses.at(-1),/show changed/);
  state={...state,privacy:false,live:true};
  const display=[];
  await act(async()=>{root.render(React.createElement(window.OnAirHostMic,{compact:true,data:state,online:true,onReview(){},onDisplayStatus:value=>display.push(value),moreControls:React.createElement('label',null,React.createElement('input',{type:'checkbox'}),' Keyboard shortcuts')}));await tick();});
  const pill=document.querySelector('.onair-mic-pill'), more=document.querySelector('.onair-mic-more');
  assert(pill && more && !more.open);assert.equal(pill.getAttribute('aria-pressed'),'false');
  assert(more.textContent.includes('After recording') && more.textContent.includes('Hold to talk') && more.textContent.includes('Keyboard shortcuts'));
  await act(async()=>{pill.click();await tick();});assert.equal(pill.getAttribute('aria-pressed'),'true');assert(document.querySelector('[role=status]').textContent.includes('Mic open: listening'));
  await frames(21,0);const priorUploads=uploads.length;await frames(10,.1);await frames(17,0);
  assert.equal(uploads.length,priorUploads+1);assert.equal(uploads.at(-1).headers['X-OnAir-Capture'],'open');assert(display.some(x=>x.includes('You: synthetic') && x.includes('Queued')));
  uploadMode='refuse';await frames(10,.1);await frames(17,0);assert(display.some(x=>x.includes('You: synthetic') && x.includes('Refused: Stage refused the line.')));
  uploadMode='held';for(let i=0;i<4;i++){await frames(10,.1);await frames(17,0);}
  assert.match(display.at(-1),/oldest waiting utterance dropped/);
  await act(async()=>{releaseUpload(answer);await tick();});
  assert.match(display.at(-1),/You: synthetic.*Queued.*oldest waiting utterance dropped/,'latest transcript and dropped-queue notice remain together');
  await act(async()=>{pill.click();await tick();});assert.equal(pill.getAttribute('aria-pressed'),'false');assert.equal(stopped,7);assert(!document.body.textContent.includes('Mic open: listening'));
  await act(async()=>{root.unmount();await tick();});assert.equal(stopped,7);assert(!document.body.textContent.includes('Mic open: listening'));
  dom.window.close();global.setInterval=nativeInterval;global.clearInterval=nativeClear;
  console.log('open microphone VAD and barge-in passed');
})().catch(e=>{console.error(e);process.exitCode=1;root.unmount();dom.window.close()});
