const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm'), {JSDOM}=require('jsdom');
const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/'});global.window=dom.window;global.document=dom.window.document;global.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'),{act}=React,{createRoot}=require('react-dom/client');let Page,blocked=false,release=[];
const state={mode:'approval',live:true,privacy:false,muted:false,epoch:7,seq:1,voice_boot:'a'.repeat(32),host_turns:{enabled:false}};
window.__HERMES_PLUGIN_SDK__={React,fetchJSON:async url=>{const p=url.replace('/api/plugins/on-air/','');if(p==='state'){if(blocked)await new Promise(r=>release.push(r));return {...state};}if(p==='pending')return {epoch:7,pending:[]};if(p==='receipts?limit=60')return {receipts:[]};return {};}};
window.__HERMES_PLUGINS__={register:(_,p)=>Page=p};for(const f of ['live.js','index.js'])vm.runInThisContext(fs.readFileSync('dashboard/dist/'+f,'utf8'));
const root=createRoot(document.getElementById('root')),tick=(ms=50)=>new Promise(r=>setTimeout(r,ms));
async function navigate(name){await act(async()=>{document.querySelector('.onair-secondary summary').click();const b=[...document.querySelectorAll('.onair-secondary button')].find(e=>e.textContent===name);assert(b);b.click();await tick();});}
(async()=>{
 await act(async()=>{root.render(React.createElement(Page));await tick();});assert.match(document.querySelector('.live-status-title').textContent,/Approval/);
 blocked=true;await navigate('Full desk (advanced)');assert(!/connecting/i.test(document.querySelector('.onair-desk').textContent));assert([...document.querySelectorAll('.onair-safety-actions button')].every(b=>!b.disabled));
 await act(async()=>{await tick(4100);});assert([...document.querySelectorAll('.onair-safety-actions button')].every(b=>b.disabled),'expired core state must disable controls');
 blocked=false;await act(async()=>{for(const r of release.splice(0))r();await tick(1100);});assert([...document.querySelectorAll('.onair-safety-actions button')].every(b=>!b.disabled));
 blocked=true;await navigate('Live');assert(!/connecting/i.test(document.querySelector('.onair-live').textContent));assert([...document.querySelectorAll('.live-controls button')].every(b=>!b.disabled));
 await act(async()=>{await tick(4100);});assert([...document.querySelectorAll('.live-controls button')].every(b=>b.disabled),'expired Live state must disable controls');
 blocked=false;await act(async()=>{for(const r of release.splice(0))r();await tick(1100);});assert.match(document.querySelector('.live-status-title').textContent,/Approval/);
 await act(async()=>root.unmount());dom.window.close();console.log('shared core state passed');
})().catch(e=>{console.error(e);root.unmount();dom.window.close();process.exitCode=1});
