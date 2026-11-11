const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<div id="root"></div>', {url:'http://localhost/'});
global.window=dom.window; global.document=dom.window.document;
Object.defineProperty(global, 'navigator', {value:dom.window.navigator, configurable:true});
global.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'), {act}=React, {createRoot}=require('react-dom/client');
const boot='a'.repeat(32); let captures=0, cuts=[], uploads=[], resolveCut, cutMode='accept', cutEnabled=true;
let state={epoch:7,voice_boot:boot,privacy:false,live:true,mode:'approval',airing:'line'};
navigator.mediaDevices={getUserMedia:async()=>{captures++; return {getTracks:()=>[{stop(){}}]};}};
global.MediaRecorder=class {constructor(){this.state='inactive'} start(){this.state='recording'} stop(){this.state='inactive';this.ondataavailable({data:new Blob(['x'])});queueMicrotask(()=>this.onstop())}};
window.__HERMES_PLUGIN_SDK__={React,fetchJSON:async (url,opts)=>{
  if(url.endsWith('/host/mic/mode')) return {ok:true,mode:'review',locked:true,reason:'Review is required.'};
  if(url.endsWith('/host/mic') && !opts) return {enabled:true,cut_in_on_talk:cutEnabled};
  if(url.endsWith('/state')) return state;
  if(url.endsWith('/cut-in')) {
    cuts.push(JSON.parse(opts.body));
    if(cutMode==='refuse') return {ok:false,message:'Stage refused the cut.'};
    if(cutMode==='wait') return new Promise(resolve=>{resolveCut=resolve});
    state={...state,epoch:state.epoch+1,airing:null}; return {ok:true,state};
  }
  if(url.endsWith('/host/mic')) {uploads.push(opts);return {ok:true,review:true,transcript:'hello'}}
  throw Error(url);
}};
vm.runInThisContext(fs.readFileSync('dashboard/dist/host-mic.js','utf8'));
const root=createRoot(document.getElementById('root'));
const render=()=>root.render(React.createElement(window.OnAirHostMic,{data:state,online:true,onReview(){}}));
const key=type=>window.dispatchEvent(new window.KeyboardEvent(type,{key:'v',bubbles:true}));
const tick=()=>new Promise(r=>setTimeout(r,25));
(async()=>{
  await act(async()=>{render();await tick()});
  await act(async()=>{key('keydown');await tick()});
  assert.equal(cuts.length,1); assert.deepEqual(cuts[0],{epoch:7,boot});
  assert.equal(captures,1); assert.match(document.body.textContent,/Cut requested; listening/i);
  await act(async()=>{render();key('keyup');await tick()});
  assert.equal(uploads[0].headers['X-OnAir-Epoch'],'8');
  await act(async()=>{key('keydown');await tick()});
  assert.equal(cuts.length,1); assert.equal(captures,2);
  await act(async()=>{key('keyup');await tick()});
  state={...state,airing:'next'}; cutMode='refuse';
  await act(async()=>{render();key('keydown');await tick()});
  assert.equal(cuts.length,2); assert.equal(captures,2); assert.match(document.body.textContent,/Stage refused the cut/);
  cutMode='wait';
  await act(async()=>{key('keydown');await tick()});
  await act(async()=>{key('keyup');resolveCut({ok:true,state:{...state,epoch:9,airing:null}});await tick()});
  assert.equal(captures,2, 'releasing before the cut returns must not start capture');
  await act(async()=>{key('keydown');await tick()});
  await act(async()=>{window.dispatchEvent(new window.KeyboardEvent('keydown',{key:'p',bubbles:true}));resolveCut({ok:true,state:{...state,epoch:9,airing:null}});await tick()});
  assert.equal(captures,2, 'privacy while cut is pending must not start capture');
  await act(async()=>{root.render(null);await tick()});
  cutEnabled=false; cutMode='accept';
  await act(async()=>{render();await tick()});
  const previousCuts=cuts.length;
  await act(async()=>{key('keydown');await tick()});
  assert.equal(cuts.length,previousCuts); assert.equal(captures,3, 'the owner can disable cut on talk');
  await act(async()=>{key('keyup');await tick()});
  await act(async()=>root.unmount()); dom.window.close(); console.log('cut on talk passed');
})().catch(e=>{console.error(e);process.exitCode=1;root.unmount();dom.window.close()});
