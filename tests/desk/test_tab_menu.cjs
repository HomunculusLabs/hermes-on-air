const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {JSDOM}=require('jsdom');const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/'});
global.window=dom.window;global.document=dom.window.document;global.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'),{act}=React,{createRoot}=require('react-dom/client');let Page;
window.OnAirLive=()=>React.createElement('div',null,'Live test view');
window.__HERMES_PLUGIN_SDK__={React,fetchJSON:async url=>{const p=url.replace('/api/plugins/on-air/','');if(p==='state')return {mode:'approval',live:true,epoch:1,seq:1};if(p==='pending')return {epoch:1,pending:[]};if(p==='receipts?limit=60')return {receipts:[]};return {};}};
window.__HERMES_PLUGINS__={register:(_,p)=>Page=p};vm.runInThisContext(fs.readFileSync('dashboard/dist/index.js','utf8'));
const root=createRoot(document.getElementById('root')),tick=()=>new Promise(r=>setTimeout(r,35));
async function render(key){await act(async()=>{root.render(React.createElement(Page,{key}));await tick();});}
async function openMenu(){const s=document.querySelector('.onair-secondary summary');assert(s,'Setup & help disclosure');await act(async()=>{s.click();await tick();});assert(s.parentElement.open);}
async function select(label){const b=[...document.querySelectorAll('.onair-secondary button')].find(x=>x.textContent===label);assert(b,label);await act(async()=>{b.click();await tick();});}
(async()=>{
 await render('initial');assert.deepEqual([...document.querySelectorAll('.onair-tabs > button')].map(x=>x.textContent),['Live','Details']);
 for(const [label,stored] of [['Start here','setup'],['Practice','practice'],['Help','help']]){await openMenu();await select(label);assert.equal(JSON.parse(window.localStorage.getItem('onair.tab.v1')).tab,stored);assert(!document.querySelector('.onair-secondary').open);}
 for(const [stored,expected] of [['setup','Start On Air'],['practice','Practice'],['help','Help'],['desk','OPERATOR DESK'],['live','Live test view']]){
  window.localStorage.setItem('onair.tab.v1',JSON.stringify({tab:stored}));await render(stored);assert(document.body.textContent.includes(expected),'stored '+stored+' resolves to '+expected);
 }
 await act(async()=>root.unmount());dom.window.close();console.log('secondary navigation passed');
})().catch(e=>{console.error(e);root.unmount();dom.window.close();process.exitCode=1});
