const assert=require('node:assert/strict');
const fs=require('node:fs'); const path=require('node:path'); const vm=require('node:vm'); const {createRequire}=require('node:module');
const local=createRequire(path.join(process.env.ONAIR_HERMES_SDK,'package.json'));
const {JSDOM}=local('jsdom'); const dom=new JSDOM('<div id="page"></div><div id="pane"></div>',{url:'about:blank'});
global.window=dom.window; global.document=dom.window.document; global.navigator=dom.window.navigator; global.IS_REACT_ACT_ENVIRONMENT=true;
const React=local('react'); const {createRoot}=local('react-dom/client'); const {act}=React;
(async()=>{
 const code=fs.readFileSync(path.join(__dirname,'../../desktop/plugin.js'),'utf8'); const context=vm.createContext({console,window:dom.window,setTimeout,clearTimeout});
 const mod=new vm.SourceTextModule(code,{context});await mod.link(async spec=>new vm.SyntheticModule(spec==='react'?['default']:['ROUTES_AREA','SIDEBAR_NAV_AREA'],function(){if(spec==='react')this.setExport('default',React);else{this.setExport('ROUTES_AREA','routes');this.setExport('SIDEBAR_NAV_AREA','nav')}},{context}));await mod.evaluate();
 let items=[],calls=[],allCalls=[], hostIds=new Set(), state={epoch:7,voice_boot:'a'.repeat(32),live:true,mode:'approval',privacy:false,muted:false,host_turns:{enabled:true}},pending=[{line_id:'a',epoch:7,text:'First line',source:'chat'}], denied=false, unavailable=false;
 const views={ok:true,connected:true,program_scene:'Other',boxes:{slots:{main:'scene',sub1:'slides',sub2:'window'},arrangement:'stack',inset_corner:'top-left',arrangement_source:'stored',available:{scene:{available:true},slides:{available:true},window:{available:true}}},window:{ready:true},layout:{presets:{presentation:{ready:true},window:{ready:true},window_pip:{ready:true},side_by_side:{ready:true}}}};
 const ctx={registerMany:x=>items.push(...x),rest:async(p,o)=>{const call={p,body:o&&o.body,method:o&&o.method||'GET'}; calls.push(call);allCalls.push(call);if(o&&o.method==='POST'){if(denied)return {ok:false,error:'Denied by server'};if(p==='/views/layout')return {ok:true,scene:'Preset scene'};if(p==='/views/boxes'&&o.body.op==='put')return {ok:true,move:{source:'window'},message:'Window moved from Sub 2 to Sub 1; Sub 2 is now empty.'};if(p==='/host/turn'){hostIds.add(o.body.id);return {ok:true,queued:true};}return {ok:true,queued:true};}if(unavailable&&p==='/state')throw Error('Unavailable');return p==='/state'?{...state}:p==='/pending'?{epoch:state.epoch,pending}:views}};
 mod.namespace.default.register(ctx);mod.namespace.default.register(ctx); assert.equal(items.length,3);
 const root=createRoot(document.getElementById('page')), pane=createRoot(document.getElementById('pane'));
 const flush=()=>new Promise(r=>setTimeout(r,15));
 await act(async()=>{root.render(items[0].render());pane.render(items[2].render());await flush()});
 assert.equal(calls.filter(x=>x.p==='/state').length,1);
 assert.match(document.getElementById('page').textContent,/Answers chat/);
 const button=(label,scope=document.getElementById('page'))=>Array.from(scope.querySelectorAll('button')).find(x=>x.textContent===label||x.getAttribute('aria-label')===label);
 async function click(label){let b=button(label);assert.ok(b,'missing '+label);assert.equal(b.disabled,false,label+' disabled');await act(async()=>{b.click();await flush()});}
 async function expectAction(label,p,body){calls=[];await click(label);let c=calls.find(x=>x.p===p&&x.method==='POST');assert.ok(c,label+' missing '+p);assert.deepEqual(JSON.parse(JSON.stringify(c.body)),body);}
 await expectAction('Approve','/approve',{line_id:'a',epoch:7}); assert.match(document.body.textContent,/Request accepted; state refreshed/); await expectAction('Skip','/reject',{line_id:'a',epoch:7});
 await expectAction('Privacy on (P)','/privacy',{on:true});await expectAction('Mute (M)','/mute',{on:true});await expectAction('Stop (Esc)','/stop',{});
 for(const mode of ['off_air','paused','approval']) { if(mode==='approval') continue; await expectAction(mode.replace('_',' ').replace(/^./,c=>c.toUpperCase()),'/mode',{mode}); }
 calls=[];await act(async()=>{window.dispatchEvent(new window.KeyboardEvent('keydown',{key:'p',bubbles:true}));await flush()});assert.equal(calls.filter(x=>x.p==='/privacy'&&x.method==='POST').length,1);assert.deepEqual(JSON.parse(JSON.stringify(calls.find(x=>x.p==='/privacy'&&x.method==='POST').body)),{on:true});
 let confirms=0;window.confirm=()=>{confirms++;return false};calls=[];await click('Autonomous');assert.equal(confirms,1);assert.equal(calls.filter(x=>x.method==='POST').length,0);
 window.confirm=()=>true;await expectAction('Autonomous','/mode',{mode:'autonomous'});
 state.privacy=true;state.muted=true;await click('Stop (Esc)');let safetyConfirms=0;window.confirm=()=>{safetyConfirms++;return false};await expectAction('Privacy off (P)','/privacy',{on:false});await expectAction('Unmute (M)','/mute',{on:false});await click('Stop (Esc)');assert.equal(safetyConfirms,0);state.privacy=false;state.muted=false;await click('Stop (Esc)');
 denied=true;await click('Mute (M)');assert.match(document.body.textContent,/Denied by server/);denied=false;
 await click('Views');
 assert.equal(button('Top left').getAttribute('aria-pressed'),'true');
 assert.ok(document.querySelector('#page .stage.top-left')); assert.match(document.getElementById('page').textContent,/remembered/i);
 for(const [label,corner] of [['Top left','top-left'],['Top right','top-right'],['Bottom left','bottom-left'],['Bottom right','bottom-right']]) await expectAction(label,'/views/boxes',{op:'arrangement',arrangement:'stack',inset_corner:corner});
 let select=document.querySelector('select[aria-label="Sub 1 source"]');calls=[];await act(async()=>{select.value='window';select.dispatchEvent(new window.Event('change',{bubbles:true}));await flush()});assert.deepEqual(JSON.parse(JSON.stringify(calls.find(x=>x.p==='/views/boxes'&&x.method==='POST').body)),{op:'put',slot:'sub1',source:'window'});assert.match(document.getElementById('page').textContent,/Window moved from Sub 2 to Sub 1/);
 await expectAction('Swap Main and Sub 1','/views/boxes',{op:'swap',a:'main',b:'sub1'});await expectAction('Swap Main and Sub 2','/views/boxes',{op:'swap',a:'main',b:'sub2'});await expectAction('Swap Sub 1 and Sub 2','/views/boxes',{op:'swap',a:'sub1',b:'sub2'});await expectAction('Side','/views/boxes',{op:'arrangement',arrangement:'side'});await expectAction('Stack','/views/boxes',{op:'arrangement',arrangement:'stack'});
 await expectAction('Show Boxes on stream','/views/scene',{scene:'On Air: Boxes'});
 views.program_scene='On Air: Boxes';await click('Find windows');assert.match(document.getElementById('page').textContent,/On stream now/);assert.ok(!button('Show Boxes on stream')||!button('Show Boxes on stream').classList.contains('primary'));views.program_scene='Other';
 for(const [label,preset] of [['Slides','presentation'],['Shared window','window'],['Window + slides inset','window_pip'],['Side by side','side_by_side']]) {calls=[];await click(label);assert.deepEqual(JSON.parse(JSON.stringify(calls.filter(x=>x.method==='POST').map(x=>[x.p,x.body]))),[['/views/layout',{preset}],['/views/scene',{scene:'Preset scene'}]]);}
 await expectAction('Find windows','/views/window',{action:'discover'});await expectAction('Show window','/views/window',{action:'show'});await expectAction('Hide window','/views/window',{action:'hide'});
 views.boxes.slots=null;await click('Find windows');await expectAction('Set up boxes with Scene','/views/boxes',{op:'set',main:'scene',sub1:'empty',sub2:'empty',arrangement:'stack'});views.boxes.slots={main:'scene',sub1:'slides',sub2:'window'};
 await click('Host');
 assert.match(document.querySelector('[data-onair-slot="host-voice"]').textContent,/dashboard.*On Air.*Talk/i);
 await expectAction('Cut in','/cut-in',{epoch:7,boot:'a'.repeat(32)});
 calls=[];await act(async()=>{button('Cut in',document.getElementById('pane')).click();await flush()});
 assert.deepEqual(JSON.parse(JSON.stringify(calls.find(x=>x.p==='/cut-in'&&x.method==='POST').body)),{epoch:7,boot:'a'.repeat(32)});
 denied=true;await click('Cut in');assert.match(document.getElementById('page').textContent,/Denied by server/);denied=false;
 let input=document.querySelector('textarea');await act(async()=>{const setter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;setter.call(input,'Host line');input.dispatchEvent(new window.Event('input',{bubbles:true}));await flush()});
 calls=[];await act(async()=>{button('Send to co-host').click();button('Send to co-host').click();await flush()});
 const sent=calls.filter(x=>x.p==='/host/turn'&&x.method==='POST');assert.equal(sent.length,1);assert.equal(hostIds.size,1);assert.match(sent[0].body.id,/^[a-f0-9]{32}$/);assert.equal(sent[0].body.text,'Host line');assert.equal(sent[0].body.epoch,7);assert.equal(sent[0].body.boot,'a'.repeat(32));
 assert.ok(document.querySelector('[data-onair-slot="host-voice"]'));
 await act(async()=>{const setter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;setter.call(input,'Retry line');input.dispatchEvent(new window.Event('input',{bubbles:true}));await flush()});
 denied=true;calls=[];await click('Send to co-host');const retryId=calls.find(x=>x.p==='/host/turn'&&x.method==='POST').body.id;assert.notEqual(retryId,sent[0].body.id);
 denied=false;calls=[];await click('Send to co-host');assert.equal(calls.find(x=>x.p==='/host/turn'&&x.method==='POST').body.id,retryId);
 await act(async()=>{const setter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;setter.call(input,'Stale draft');input.dispatchEvent(new window.Event('input',{bubbles:true}));await flush()});
 state.host_turns.enabled=false;await click('Mute (M)');assert.equal(button('Send to co-host').disabled,true);state.host_turns.enabled=true;await click('Mute (M)');assert.equal(button('Send to co-host').disabled,false);
 state.voice_boot='b'.repeat(32);await click('Mute (M)');assert.equal(input.value,'');assert.match(document.body.textContent,/The show changed since you started typing/);
 state.privacy=true;await click('Mute (M)');assert.equal(button('Cut in').disabled,true);assert.equal(button('Cut in',document.getElementById('pane')).disabled,true);state.privacy=false;await click('Mute (M)');
 await act(async()=>{const setter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;setter.call(input,'Stale draft');input.dispatchEvent(new window.Event('input',{bubbles:true}));await flush()});
 state.epoch=8;pending=[{line_id:'a',epoch:8,text:'First line'}];await act(async()=>{await new Promise(r=>setTimeout(r,2100))});assert.equal(input.value,'');assert.match(document.body.textContent,/The show changed since you started typing/);calls=[];button('Send to co-host').click();assert.equal(calls.some(x=>x.p==='/host/turn'&&x.method==='POST'),false);
 await act(async()=>{root.unmount();await flush()});calls=[];await act(async()=>{window.dispatchEvent(new window.KeyboardEvent('keydown',{key:'p',bubbles:true}));await flush()});assert.equal(calls.filter(x=>x.p==='/privacy'&&x.method==='POST').length,1);
 unavailable=true;await act(async()=>{await new Promise(r=>setTimeout(r,2100))});assert.match(document.getElementById('pane').textContent,/Rundown: Unknown/);assert.match(document.getElementById('pane').textContent,/Connection: Unavailable/);assert.match(document.getElementById('pane').textContent,/Desk unreachable/);assert.notEqual(document.querySelector('#pane h1').textContent,'On Air');unavailable=false;
 await act(async()=>{pane.unmount();await flush()});
 for(let i=0;i<20;i++){const r=createRoot(document.getElementById('page'));await act(async()=>{r.render(items[0].render());await flush()});await act(async()=>{r.unmount();await flush()});}
 if(process.env.ONAIR_DESKTOP_CALLS) fs.writeFileSync(process.env.ONAIR_DESKTOP_CALLS,JSON.stringify(allCalls));
 dom.window.close();console.log('desktop UI tests passed');
})().catch(e=>{console.error(e);process.exit(1)});
