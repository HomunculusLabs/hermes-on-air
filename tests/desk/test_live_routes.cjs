/* Exercise actual Live and HostLine and record every SDK route/body. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/'});
global.window=dom.window;global.document=dom.window.document;Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});global.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'),{createRoot}=require('react-dom/client'),{act}=React;
let calls=[],s,pending;const state=()=>({mode:'approval',privacy:false,muted:false,epoch:7,live:true,airing:null,voice_boot:'a'.repeat(32),host_turns:{enabled:true}});
const line=id=>({line_id:id,epoch:7,turn_id:'t',source:'host',text:'Reply'});
const views={ok:true,connected:true,program_scene:'On Air: Boxes',boxes:{slots:{main:'scene',sub1:'slides',sub2:'empty'},arrangement:'stack',inset_corner:'top-left',canvas:{width:1920,height:1080},geometry:{main:{bounds:{x:0,y:0,width:1920,height:1080}},sub1:{bounds:{x:48,y:48,width:384,height:216}}},available:{scene:{available:true},slides:{available:true}}}};
window.confirm=()=>true;
window.__HERMES_PLUGIN_SDK__={React,fetchJSON:async(url,opts)=>{
 const route=url.replace('/api/plugins/on-air/',''),method=opts?'POST':'GET', body=opts&&JSON.parse(opts.body);calls.push({route,method,body});
 if(opts){if(route==='approve'||route==='reject')pending=pending.filter(x=>x.line_id!==body.line_id);if(route==='privacy'||route==='mute')s[route==='mute'?'muted':'privacy']=body.on;if(route==='mode')s.mode=body.mode;if(route==='host/turn')return {ok:false,message:'retry'};return {ok:true};}
 if(route==='state')return {...s};if(route==='pending')return {epoch:s.epoch,pending:[...pending]};if(route.startsWith('receipts'))return {receipts:[]};if(route==='views/state')return views;
 if(route==='host/mic')return {enabled:true};if(route==='host/mic/mode')return {ok:true,mode:'review',locked:false};if(route==='setup/status')return {ok:true,desk_connected:true};throw Error('Unexpected route: '+route);
}};
window.__HERMES_PLUGINS__={register(){}};
vm.runInThisContext(fs.readFileSync('dashboard/dist/host-mic.js','utf8'));
vm.runInThisContext(fs.readFileSync('dashboard/dist/index.js','utf8').replace('  function Views() {','  window.__HostLine=HostLine;\n  function Views() {'));
vm.runInThisContext(fs.readFileSync('dashboard/dist/live.js','utf8'));
const append=document.head.appendChild.bind(document.head);document.head.appendChild=element=>{const x=append(element);if(element.tagName==='SCRIPT')queueMicrotask(()=>element.onload&&element.onload());return x;};
const root=createRoot(document.getElementById('root')),tick=()=>new Promise(r=>setTimeout(r,40));
async function mount(){await act(async()=>{root.render(React.createElement(window.OnAirLive,{key:Math.random(),HostLine:window.__HostLine}));await tick();});await act(async()=>{await tick();});}
async function click(text){const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===text);assert(b&&!b.disabled,text);await act(async()=>{b.click();await tick();});}
(async()=>{
 s=state();pending=[line('1')];await mount();
 let input=document.querySelector('.onair-host-line > label input');assert(input);
 await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,'Hello');input.dispatchEvent(new window.Event('input',{bubbles:true}));});
 await click('Send to co-host');await click('Send to co-host');let sends=calls.filter(x=>x.route==='host/turn');assert.equal(sends.length,2);assert.equal(sends[0].body.id,sends[1].body.id);
 await click('Cut in');await click('Approve ↵A');
 pending=[line('2')];await mount();await click('Skip');
 const mode=document.querySelector('select[aria-label="Show mode"]');
 for(const value of ['autonomous','approval'])await act(async()=>{mode.value=value;mode.dispatchEvent(new window.Event('change',{bubbles:true}));await tick();});
 await click('Privacy');await click('Privacy');await click('Mute voice');await click('Stop');
 s=state();pending=[];await mount();
 const picture=document.querySelector('.live-picture');assert.match(picture.getAttribute('aria-label'),/Main.*Scene.*Sub 1.*Slides/);assert(picture.hasAttribute('tabindex'),'box description is keyboard accessible');
 await click('Swap');
 const expected=['state','pending','receipts?limit=60','views/state','host/mic','host/mic/mode','host/turn','cut-in','approve','reject','mode','privacy','mute','stop','views/boxes'];
 for(const route of expected)assert(calls.some(x=>x.route===route),'not exercised: '+route);
 if(process.env.ONAIR_LIVE_ROUTE_RECEIPT)fs.writeFileSync(process.env.ONAIR_LIVE_ROUTE_RECEIPT,JSON.stringify(calls));
 await act(async()=>root.unmount());dom.window.close();console.log('live route UI passed');
})().catch(e=>{console.error(e);root.unmount();dom.window.close();process.exitCode=1});
