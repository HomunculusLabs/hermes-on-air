const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<!doctype html><title>Desk</title><div id="root"></div>', {url:'http://localhost/'});
global.window=dom.window; global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
Object.defineProperty(global,'crypto',{value:require('node:crypto').webcrypto,configurable:true});
global.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'), {act}=React, {createRoot}=require('react-dom/client');
let state={mode:'approval',live:true,privacy:false,muted:false,epoch:1,voice_boot:'a'.repeat(32),host_turns:{enabled:true}}, stopped=0, Page, holdAlerts=false, releaseAlerts;
const requests=[];
const track={stop(){stopped++;},addEventListener(){}};
navigator.mediaDevices={getUserMedia:async()=>({getTracks:()=>[track],getAudioTracks:()=>[track]})};
class Context {constructor(){this.sampleRate=48000;this.destination={};} createMediaStreamSource(){return {connect(){},disconnect(){}};} createAnalyser(){return {fftSize:2048,getFloatTimeDomainData(a){a.fill(0);}};} createScriptProcessor(){return {connect(){},disconnect(){}};} createGain(){return {gain:{value:1},connect(){},disconnect(){}};} close(){return Promise.resolve();}}
window.AudioContext=Context;
window.__HERMES_PLUGIN_SDK__={React,fetchJSON:async(url,opts)=>{
 const p=url.replace('/api/plugins/on-air/','');requests.push(p);
 if(p==='state')return {...state};if(p==='pending')return {epoch:state.epoch,pending:[]};
 if(p==='receipts?limit=60')return {receipts:[]};if(p==='host/mic')return {enabled:true,open_enabled:true,cut_in_on_talk:true};
 if(p==='host/mic/mode')return {ok:true,mode:'send',locked:false};
 if(p==='alerts'){if(holdAlerts)await new Promise(r=>{releaseAlerts=r});return {alerts:{}};}if(p==='alerts/recent')return {alerts:[]};if(p==='views/state')return {ok:false};
 if(['assist','setup/status'].includes(p))return {};throw Error('Unexpected route: '+p);
}};
window.__HERMES_PLUGINS__={register:(_,p)=>Page=p};
for(const f of ['host-mic.js','live.js','index.js'])vm.runInThisContext(fs.readFileSync('dashboard/dist/'+f,'utf8'));
const root=createRoot(document.getElementById('root'));
const tick=()=>new Promise(r=>setTimeout(r,40));
async function click(label){const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===label);assert(b,label);await act(async()=>{b.click();await tick();});}
(async()=>{
 await act(async()=>{root.render(React.createElement(Page));await tick();});
 assert(document.querySelector('.live-status-title').textContent.includes('Approval'));
 await act(async()=>{document.querySelector('script[src$="host-mic.js"]').onload();await tick();});
 await click('🎙 Mic off');assert(document.querySelector('.onair-live-mic-badge'));
 holdAlerts=true;await click('Details');assert.equal(stopped,1,'leaving Live must close the microphone');
 assert(document.body.textContent.includes('APPROVAL'),'core state must not wait for optional alerts');
 holdAlerts=false;await act(async()=>{releaseAlerts();await tick();});
 assert.match(document.body.textContent,/Open mic turned off.*desk closed/i,'the destination explains why the mic stopped');
 assert(!document.body.textContent.includes('CONNECTION LOST'),'healthy stage must not appear lost');
 state={...state,mode:'autonomous'};
 await click('Live');assert(document.querySelector('.live-status-title').textContent.includes('Autonomous'),'return must fetch current state');
 assert(!document.querySelector('.onair-live-mic-badge'),'microphone is not reopened');
 state={...state,mode:'off_air',live:false};await click('Details');await click('Live');
 const micOff=[...document.querySelectorAll('.live-talk button')].find(b=>b.textContent.includes('Mic off'));assert(micOff && micOff.disabled,'off air explicitly shows microphone off');
 assert(requests.includes('state')&&requests.includes('pending'));
 await act(async()=>root.unmount());dom.window.close();console.log('tab lifecycle passed');
})().catch(e=>{console.error(e);root.unmount();dom.window.close();process.exitCode=1});
