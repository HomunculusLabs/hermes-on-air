const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/'});
global.window=dom.window; global.document=dom.window.document; global.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'), {act}=React, {createRoot}=require('react-dom/client');
let mode={ok:true,mode:'review',locked:false,reason:''}, writes=[];
window.__HERMES_PLUGIN_SDK__={React,fetchJSON:async(url,opts)=>{
  if(url.endsWith('/host/mic/mode')) {
    if(opts){writes.push(JSON.parse(opts.body));mode={...mode,mode:writes.at(-1).mode};}
    return mode;
  }
  if(url.endsWith('/host/mic')) return {ok:true,enabled:true,cut_in_on_talk:true};
  throw Error(url);
}};
vm.runInThisContext(fs.readFileSync('dashboard/dist/host-mic.js','utf8'));
const root=createRoot(document.getElementById('root')), data={epoch:7,voice_boot:'a'.repeat(32),privacy:false};
const tick=()=>new Promise(r=>setTimeout(r,20));
const render=()=>root.render(React.createElement(window.OnAirHostMic,{online:true,data,onReview(){}}));
(async()=>{
 await act(async()=>{render();await tick()});
 assert.match(document.body.textContent,/Review before sending/); assert.match(document.body.textContent,/Send as soon as I release/);
 assert.match(document.body.textContent,/not hands-free or always-listening/i);
 const radios=[...document.querySelectorAll('input[type=radio]')];
 assert.equal(radios.length,2); assert.equal(radios[0].checked,true);
 await act(async()=>{radios[1].click();await tick()});
 assert.deepEqual(writes,[{mode:'send'}]); assert.equal(radios[1].checked,true);
 await act(async()=>{root.render(null);await tick()});
 mode={ok:true,mode:'review',locked:true,reason:'Review is required by the host_mic_review setting.'};
 await act(async()=>{render();await tick()});
 assert([...document.querySelectorAll('input[type=radio]')].every(r=>r.matches(':disabled')));
 assert.match(document.body.textContent,/Review is required/);
 assert.match(document.body.textContent,/Review before sending — required/);
 assert.match(document.body.textContent,/Send as soon as I release — unavailable/);
 await act(async()=>root.unmount());dom.window.close();console.log('mic release mode passed');
})().catch(e=>{console.error(e);process.exitCode=1;root.unmount();dom.window.close()});
