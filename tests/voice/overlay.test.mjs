import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Exercise the shipped overlay handler with a fake player, no media device.
test('caption clear pauses the active program player and hides its caption', () => {
  const elements = new Map();
  function element() { return {hidden:false, textContent:'', style:{}, dataset:{}, offsetWidth:0, children:[],
    classList:{add(){}, remove(){}, toggle(){}}, addEventListener(){}, appendChild(){}, querySelector(){return null}}; }
  const document = {getElementById(id){if(!elements.has(id)) elements.set(id,element()); return elements.get(id);},
    body:element(), documentElement:{style:{setProperty(){}}}, createElement:element};
  let source, player;
  class EventSource {constructor(){source=this} close(){}}
  class Audio {constructor(url){this.url=url; this.pauses=0; player=this} addEventListener(){} play(){return Promise.resolve()} pause(){this.pauses++} removeAttribute(){} load(){}}
  const context = {document, EventSource, Audio, URLSearchParams, location:{search:''},
    window:{addEventListener(){}}, setTimeout:()=>1, clearTimeout(){}, getComputedStyle:()=>({fontSize:'40',lineHeight:'40'})};
  vm.runInNewContext(fs.readFileSync(new URL('../../onair/overlay/web/overlay.js', import.meta.url),'utf8'),context);
  const send = event => source.onmessage({data:JSON.stringify(event)});
  send({type:'state',seq:1,live:true,mode:'approval',privacy:false,muted:false});
  send({type:'caption',seq:2,id:'c1',speaker:'Co-host',text:'A safe reply',ttl_ms:30000});
  send({type:'audio',seq:3,id:'a1',url:'/audio/a1.wav',caption_id:'c1'});
  assert.equal(player.url,'/audio/a1.wav'); assert.equal(elements.get('caption').hidden,false);
  send({type:'caption_clear',seq:4});
  assert.equal(player.pauses,1); assert.equal(elements.get('caption').hidden,true);
  assert.equal(elements.get('caption-text').textContent,'');
});
