const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const dom = new JSDOM(fs.readFileSync('onair/overlay/web/index.html', 'utf8'), {url:'http://localhost/overlay', runScripts:'outside-only'});
const w = dom.window;
let source;
w.EventSource = class { constructor() { source = this; } close() {} };
w.eval(fs.readFileSync('onair/overlay/web/overlay.js', 'utf8'));
let seq = 0;
const send = ev => source.onmessage({data:JSON.stringify({...ev,seq:++seq})});
const state = {type:'state',live:true,mode:'approval',privacy:false,muted:false};
const caption = () => send({type:'caption',id:'c',speaker:'host',text:'visible'});
const covered = () => assert.equal(w.document.querySelector('#cover').hidden,false);
const cleared = () => assert.equal(w.document.querySelector('#caption').hidden,true);
for (const invalid of [
  {privacy:undefined}, {privacy:'false'}, {live:1}, {muted:null}, {mode:'unknown'}, {live:false}
]) {
  send(state); caption(); assert.equal(w.document.querySelector('#caption').hidden,false);
  send({...state,...invalid}); covered(); cleared(); caption(); cleared();
}
send(state); caption(); source.onmessage({data:JSON.stringify({...state,seq:undefined})}); covered(); cleared();
send(state); caption(); source.onmessage({data:JSON.stringify({...state,seq:'bad'})}); covered(); cleared();
send(state); caption(); source.onmessage({data:'{'}); covered(); cleared(); caption(); cleared();
send(state); caption(); send({...state,privacy:true}); covered(); cleared();
send(state); caption(); assert.equal(w.document.querySelector('#caption').hidden,false);
source.onerror(); covered(); cleared();
w.close();
console.log('overlay state passed');
