const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
(async () => {
 const code = fs.readFileSync(path.join(__dirname, '../../desktop/plugin.js'), 'utf8');
 const React = {createElement:(type, props, ...children)=>({type,props:props||{},children}), useState:()=>{}, useEffect:()=>{}, useRef:()=>{}};
 let timers=new Map(), nextTimer=0;
 const sandbox = vm.createContext({console, setTimeout:fn=>{timers.set(++nextTimer,fn);return nextTimer}, clearTimeout:id=>timers.delete(id), window:{confirm:()=>true}});
 const mod = new vm.SourceTextModule(code, {context:sandbox});
 await mod.link(async spec => new vm.SyntheticModule(spec==='react'?['default']:['ROUTES_AREA','SIDEBAR_NAV_AREA'], function() { if(spec==='react') this.setExport('default',React); else {this.setExport('ROUTES_AREA','routes');this.setExport('SIDEBAR_NAV_AREA','sidebar.nav');} }, {context:sandbox}));
 await mod.evaluate();
 const {default:plugin, createOwner} = mod.namespace;
 assert.equal(plugin.id,'on-air'); assert.equal(plugin.name,'On Air'); assert.equal(plugin.defaultEnabled,false);
 let items=[], calls=[], refusal=false;
 const ctx={registerMany:xs=>items.push(...xs),rest:async(p,o)=>{calls.push([p,o]); if(o&&o.method==='POST')return refusal?{ok:false,error:'Denied by show'}:{ok:true}; return p==='/state'?{epoch:1,mode:'approval'}:p==='/pending'?{epoch:1,pending:[]}:{ok:true,connected:false}}};
 plugin.register(ctx); plugin.register(ctx); assert.equal(items.length,3); assert.equal(items[0].area,'routes'); assert.equal(items[2].area,'panes');
 const tick=()=>new Promise(r=>setImmediate(r));
 const owner=createOwner(ctx); let notifications=0; const off1=owner.subscribe(()=>notifications++), off2=owner.subscribe(()=>notifications++);
 await tick(); assert.equal(owner.snapshot().online,true); assert.equal(calls.filter(([p])=>p==='/state').length,1); assert.equal(timers.size,1);
 await owner.action('/approve',{line_id:'line',epoch:1}); assert.equal(calls.at(-1)[0],'/approve'); assert.equal(calls.at(-1)[1].body.epoch,1);
 refusal=true; await owner.action('/mute',{on:true},true); assert.match(owner.snapshot().notice,/Denied by show/);
 off1(); assert.equal(owner.active(),true); off2(); assert.equal(owner.active(),false); assert.equal(timers.size,0);
 for (let i=0;i<20;i++) {
   let resolves=[]; const slow=createOwner({...ctx,rest:()=>new Promise(r=>{resolves.push(r)})}); let changes=0;
   const off=slow.subscribe(()=>changes++); off(); resolves[0]({epoch:2}); resolves[1]({epoch:2,pending:[]}); resolves[2]({ok:true}); await tick(); assert.equal(changes,0); assert.equal(slow.snapshot().online,false); assert.equal(slow.active(),false); assert.equal(timers.size,0);
 }
 let inconsistent=createOwner({...ctx,rest:async p=>p==='/state'?{epoch:1}:p==='/pending'?{epoch:2,pending:[{line_id:'stale',epoch:2}]}:{ok:true}});
 let stop=inconsistent.subscribe(()=>{}); await tick(); assert.equal(inconsistent.snapshot().online,false); assert.equal(inconsistent.snapshot().queue.length,0); assert.equal(timers.size,1); stop(); assert.equal(timers.size,0);
 let release; const actionCtx={...ctx,rest:(p,o)=>o&&o.method==='POST'?new Promise(r=>{release=r}):ctx.rest(p,o)};
 const late=createOwner(actionCtx); let un=late.subscribe(()=>{}); await tick(); const pending=late.action('/stop',{},true); un(); un=late.subscribe(()=>{}); await tick(); release({ok:true,note:'stale action'}); await pending; assert.notEqual(late.snapshot().notice,'stale action'); un(); assert.equal(timers.size,0);
 assert.doesNotMatch(code,/https?:\/\/|\b(?:token|port)\b/i);
 console.log('desktop owner tests passed');
})().catch(e=>{console.error(e);process.exitCode=1});
