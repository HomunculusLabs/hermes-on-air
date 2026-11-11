const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<div id="root"></div>', {url:'http://localhost/'});
global.window=dom.window; global.document=dom.window.document; global.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'); const {act}=React; const {createRoot}=require('react-dom/client');
const calls=[], queued=new Set();
let loseResponse=false;
window.__HERMES_PLUGIN_SDK__={React,fetchJSON:async (url,opts)=>{ const body=JSON.parse(opts.body); calls.push(body); if (url.endsWith("cut-in")) return {ok:true}; if (queued.has(body.id)) return {ok:false,reason:"duplicate"}; queued.add(body.id); if (loseResponse) {loseResponse=false; throw Error('connection lost');} return {ok:true,queued:true,position:1}; }};
window.__HERMES_PLUGINS__={register:()=>{}};
const code=fs.readFileSync('dashboard/dist/index.js','utf8').replace('  function Views() {','  window.__HostLine = HostLine;\n  function Views() {');
vm.runInThisContext(code);
const root=createRoot(document.querySelector('#root'));
const setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
const valid={host_turns:{enabled:true},epoch:7,voice_boot:'a'.repeat(32),live:true,mode:'approval',privacy:false};
async function render(data=valid,online=true,fence=0){ await act(async()=>root.render(React.createElement(window.__HostLine,{data,online,fence}))); }
async function type(text){const input=document.querySelector('input');await act(async()=>{setter.call(input,text);input.dispatchEvent(new window.Event('input',{bubbles:true}));});}
async function click(){await act(async()=>document.querySelector('button').click());}
(async()=>{
  await render();
  const detailsLine=document.querySelector('.onair-host-line');
  assert.deepEqual([...detailsLine.children].map(e=>e.tagName),['H2','LABEL','BUTTON','BUTTON','P','LABEL','P']);
  assert.equal(detailsLine.querySelector('.onair-field').textContent,'Your line');
  assert.equal(detailsLine.querySelector('.onair-cut-button').textContent,'Cut in');
  assert.match(detailsLine.querySelector('.onair-check').textContent,/Keyboard shortcuts/);
  assert(!detailsLine.querySelector('.onair-mic-more'),'Details retains the full Talk block');
  await type('before cut');
  await render({...valid,privacy:true},true,1); await render({...valid,epoch:8},true,1);
  assert.equal(document.querySelector('input').value,''); assert.match(document.body.textContent,/re-enter/i);
  await click(); assert.equal(calls.length,0);
  await type('fresh');
  await act(async()=>{const b=document.querySelector('button');b.click();b.click();});
  assert.equal(calls.length,2); assert.equal(calls[0].text,'fresh'); assert.equal(calls[0].epoch,8);
  assert.equal(calls[0].boot,'a'.repeat(32));
  assert.match(calls[0].id,/^[0-9a-f]{32}$/); assert.equal(calls[0].id,calls[1].id); assert.equal(queued.size,1);
  await type('another'); await click(); assert.notEqual(calls[2].id,calls[0].id); assert.equal(queued.size,2);
  await type('retry'); loseResponse=true; await click(); await click();
  assert.equal(calls[3].id,calls[4].id); assert.equal(queued.size,3);
  await type('discarded by cut');
  await act(async()=>document.querySelector('.onair-cut-button').click());
  assert.deepEqual(calls.at(-1), {epoch:8,boot:'a'.repeat(32)});
  assert.equal(document.querySelector('input').value, '');
  await act(async()=>window.dispatchEvent(new window.KeyboardEvent('keydown',{key:'c',bubbles:true})));
  assert.equal(calls.length,6,'C outside Talk must not cut by default');
  const shortcuts=[...document.querySelectorAll('label')].find(x=>x.textContent.includes('Keyboard shortcuts')).querySelector('input');
  assert.equal(shortcuts.checked,false);
  await act(async()=>shortcuts.click());
  await act(async()=>window.dispatchEvent(new window.KeyboardEvent('keydown',{key:'c',bubbles:true})));
  assert.deepEqual(calls.at(-1), {epoch:8,boot:'a'.repeat(32)});
  assert.equal(calls.length, 7);
  await act(async()=>shortcuts.click());
  const talk=document.querySelector('.onair-host-line');
  await act(async()=>talk.dispatchEvent(new window.KeyboardEvent('keydown',{key:'c',bubbles:true})));
  assert.equal(calls.length,8,'C works when Talk has focus');
  for(const change of [
    {data:{...valid,epoch:9}}, {data:{...valid,voice_boot:'b'.repeat(32)}}, {data:{...valid,mode:'paused',live:false}},
    {data:{...valid,mode:'off_air',live:false}}, {data:valid,online:false}, {data:valid,fence:2}
  ]) {
    await render(); await type('old'); await render(change.data,change.online ?? true,change.fence ?? 0);
    await render(valid,true,change.fence ?? 0);
    assert.equal(document.querySelector('input').value,'');
    assert.match(document.body.textContent,/re-enter/i);
  }
  await render(valid); await type('old live draft');
  const modelPause={...valid,mode:'paused',live:false,model_failure_paused:true};
  await render(modelPause);
  assert.equal(document.querySelector('input').value,'');
  assert.equal(document.querySelector('input').disabled,false);
  assert.equal(document.querySelector('.onair-cut-button').disabled,true);
  await type('retry after failure'); await click();
  assert.equal(calls.at(-1).text,'retry after failure');
  await type('discard at resume'); await render(valid);
  assert.equal(document.querySelector('input').value,'');
  await act(async()=>root.unmount()); dom.window.close(); console.log('host draft passed');
})().catch(e=>{console.error(e);process.exitCode=1;root.unmount();dom.window.close()});
