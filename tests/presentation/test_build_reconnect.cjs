const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm'), {JSDOM}=require('jsdom');
const assets=[['present','onair/presentation/web/index.html','onair/presentation/web/app.js'],['overlay','onair/overlay/web/index.html','onair/overlay/web/overlay.js']];
for(const [name,html,script] of assets){
 let now=100000,reloads=0,store={};
 function load(token){
  const dom=new JSDOM(fs.readFileSync(html,'utf8'),{url:'http://localhost/'}), document=dom.window.document;
  if(token)document.documentElement.dataset.build=token;
  let source;
  const location={search:'',reload(){reloads++;}};
  const window={location,innerWidth:1920,innerHeight:1080,addEventListener(){},sessionStorage:{getItem(k){return store[k]||null},setItem(k,v){store[k]=v}}};
  class DateFake extends Date {static now(){return now;}}
  class EventSource {constructor(){source=this} close(){}}
  const ctx={window,document,location,EventSource,URLSearchParams,Date:DateFake,setTimeout(){return 1},clearTimeout(){},getComputedStyle:()=>({getPropertyValue:()=>1}),console};
  vm.runInNewContext(fs.readFileSync(script,'utf8'),ctx);
  return {send(build,mode){const data=name==='present'?{type:'program',revision:1,mode:mode||'standby',build}:{type:'state',seq:1,live:false,mode:'off_air',privacy:false,muted:false,build};source.onmessage({data:JSON.stringify(data)});},document,dom};
 }
 let page=load('aaaaaaaaaaaa'); page.send('aaaaaaaaaaaa'); assert.equal(reloads,0);page.send(undefined);assert.equal(reloads,0);
 page.send('bbbbbbbbbbbb');assert.equal(reloads,1,name+' changed build must reload');
 page=load('aaaaaaaaaaaa');page.send('bbbbbbbbbbbb');assert.equal(reloads,1,name+' reload must be throttled across page loads');
 now+=30001;page=load('aaaaaaaaaaaa');page.send('bbbbbbbbbbbb');assert.equal(reloads,2,name+' retry allowed after 30s');
 page=load(undefined);page.send('cccccccccccc');assert.equal(reloads,2,name+' absent loaded token must not reload');
 if(name==='present'){page.send(undefined,'unknown');assert.equal(page.document.getElementById('stage').dataset.mode,'standby');}
 page.dom.window.close();
}
console.log('build reconnect passed');
