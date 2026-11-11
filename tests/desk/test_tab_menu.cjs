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
 await render('initial');assert.equal(document.querySelectorAll('.onair-tabs > button').length,0);assert(document.body.textContent.includes('Live test view'));
 await openMenu();await act(async()=>{document.body.dispatchEvent(new window.MouseEvent('click',{bubbles:true}));await tick();});assert(!document.querySelector('.onair-secondary').open,'outside click closes menu');
 await openMenu();await act(async()=>{document.querySelector('.onair-secondary summary').dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await tick();});assert(!document.querySelector('.onair-secondary').open,'Escape closes menu');assert.equal(document.activeElement,document.querySelector('.onair-secondary summary'));
 for(const [label,stored] of [['Full desk (advanced)','advanced'],['Start here','setup'],['Practice','practice'],['Help','help'],['Live','live']]){await openMenu();await select(label);assert.equal(JSON.parse(window.localStorage.getItem('onair.tab.v1')).tab,stored);assert(!document.querySelector('.onair-secondary').open);}
 for(const [stored,expected] of [['setup','Start On Air'],['practice','Practice'],['help','Help'],['desk','Live test view'],['details','Live test view'],['advanced','OPERATOR DESK'],['live','Live test view']]){
  window.localStorage.setItem('onair.tab.v1',JSON.stringify({tab:stored}));await render(stored);assert(document.body.textContent.includes(expected),'stored '+stored+' resolves to '+expected);
 }
 await render('advanced-check');await openMenu();await select('Full desk (advanced)');
 const safety=[...document.querySelectorAll('.onair-safety-actions button')];assert.equal(safety.length,3);for(const b of safety){assert(b.title);assert(document.getElementById(b.getAttribute('aria-describedby')).textContent.length>10);}
 await act(async()=>root.unmount());dom.window.close();console.log('secondary navigation passed');
})().catch(e=>{console.error(e);root.unmount();dom.window.close();process.exitCode=1});
