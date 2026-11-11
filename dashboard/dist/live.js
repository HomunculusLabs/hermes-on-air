/* Focused operator view. All writes use the existing authenticated desk routes. */
(function () {
  'use strict';
  const SDK=window.__HERMES_PLUGIN_SDK__, React=SDK.React, h=React.createElement;
  const {useState,useEffect,useLayoutEffect,useRef}=React;
  const BASE='/api/plugins/on-air/';
  const asset=document.currentScript && document.currentScript.src;
  if (asset && !document.querySelector('link[data-onair-live]')) {
    const css=document.createElement('link'); css.rel='stylesheet'; css.href=new URL('live.css',asset).href; css.dataset.onairLive=''; document.head.appendChild(css);
  }
  const group = queue => {
    if (!queue.length) return [];
    const first=queue[0];
    if (typeof first.turn_id !== 'string' || !first.turn_id) return [first];
    const result=[];
    for (const line of queue) {
      if (line.turn_id !== first.turn_id || line.epoch !== first.epoch) break;
      result.push(line);
    }
    return result;
  };
  function context(line, receipts) {
    if (!line) return '';
    if (line.source === 'beat') return 'Quiet moment';
    if (line.source === 'host') {
      let text=typeof line.host_text==='string' && line.host_text.trim() ? line.host_text : '';
      if (!text && typeof line.turn_id==='string' && line.turn_id && Number.isSafeInteger(line.epoch) && Array.isArray(receipts)) {
        const ids=[...new Set(receipts.filter(x=>x && x.kind==='host' && x.disposition==='dispatched' && x.turn_id===line.turn_id && typeof x.id==='string' && x.id).map(x=>x.id))];
        const queued=ids.length===1 && receipts.find(x=>x && x.kind==='host' && x.disposition==='queued' && x.id===ids[0] && x.epoch===line.epoch && typeof x.text==='string' && x.text.trim());
        if(queued) text=queued.text;
      }
      return text ? 'You asked: “'+text+'”' : 'Host turn';
    }
    if (line.source === 'chat') {
      const parts=[line.user || line.name, line.question || line.chat_text].filter(x=>typeof x==='string' && x);
      return parts.length ? 'Chat: '+parts.join(': ') : 'Chat turn';
    }
    return line.source === 'support' ? 'Support turn' : 'Next line';
  }
  function shortcut(e) {
    if (e.defaultPrevented || e.repeat || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return false;
    const el=e.target;
    if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT|BUTTON|A|SUMMARY)$/.test(el.tagName))) return false;
    return e.key === 'Enter' || e.key.toLowerCase() === 'a';
  }
  async function approveAll(lines, approve) {
    for (const line of lines) if (!await approve(line)) return false;
    return true;
  }
  window.OnAirLiveLogic={group,context,shortcut,approveAll};
  window.OnAirLive=function Live({HostLine,onMicStatus,coreStore}) {
    const cached=coreStore && coreStore.current, age=cached && Date.now()-cached.at;
    const fresh=cached && age>=0 && age<4000 ? cached : null;
    const [state,setState]=useState(()=>fresh?fresh.state:null), [queue,setQueue]=useState(()=>fresh?fresh.queue:[]), [online,setOnline]=useState(()=>!!fresh), [answered,setAnswered]=useState(()=>!!fresh);
    const [lastGood,setLastGood]=useState(()=>fresh?fresh.at:0);
    const [views,setViews]=useState(null), [busy,setBusy]=useState(false), [warning,setWarning]=useState(''), [aired,setAired]=useState(null), [withheld,setWithheld]=useState(0), [receipts,setReceipts]=useState([]);
    const [preparing,setPreparing]=useState(null), [fence,setFence]=useState(0), [suspendHost,setSuspendHost]=useState(false), [scrollHint,setScrollHint]=useState(false);
    const alive=useRef(true), generation=useRef(0), locked=useRef(false), polling=useRef(false), safety=useRef(0), cuts=useRef(0), current=useRef({}), talk=useRef(null), lineBox=useRef(null);
    current.current={state,queue,online,busy,preparing,lastGood};
    useEffect(()=>{const input=talk.current && talk.current.querySelector('.onair-host-line > label.onair-field input');if(input) input.placeholder='or type a line';});
    async function get(path) { return SDK.fetchJSON(BASE+path); }
    function deadline(promise) {
      return new Promise((resolve,reject)=>{const timer=window.setTimeout(()=>reject(Error('Show did not answer in time.')),4000);promise.then(value=>{window.clearTimeout(timer);resolve(value);},error=>{window.clearTimeout(timer);reject(error);});});
    }
    async function post(path,body) {
      try {
        const r=await SDK.fetchJSON(BASE+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        if (!r || r.ok !== true) throw Error(r && (r.error || r.message) || 'No confirmation from the show.');
        return r;
      } catch (e) {
        const detail=e && (e.body || e.data || e.detail);
        throw Error(detail && (detail.error || detail.message) || e && e.message || 'Could not reach the show.');
      }
    }
    async function refresh() {
      const gen=++generation.current;
      try {
        const receiptRequest=get('receipts?limit=60').catch(()=>null);
        const [s,p]=await Promise.all([deadline(get('state')),deadline(get('pending'))]);
        if (!alive.current || gen!==generation.current || safety.current) return;
        if (!s || !p || !Array.isArray(p.pending) || s.epoch!==p.epoch) throw Error('unavailable');
        const at=Date.now();
        if(coreStore) coreStore.current={state:s,queue:p.pending,at};
        setLastGood(at);setState(s);setQueue(p.pending);setOnline(true);setAnswered(true);
        setAired(old=>old && old.id===s.airing && old.boot===s.voice_boot ? old : null);
        setPreparing(old=>old && (s.epoch!==old.epoch || s.privacy || s.mode==='off_air' || s.mode==='paused' || (p.pending.length && p.pending[0].line_id!==old.id)) ? null : old);
        receiptRequest.then(r=>{
          if (!alive.current || gen!==generation.current || safety.current) return;
          const recent=r && Array.isArray(r.receipts) ? r.receipts : [];
          setReceipts(recent);
          setWithheld(recent.filter(x=>x && x.kind==='line' && x.disposition==='withheld').length);
          // State marks a line as airing even during synthesis. Only an aired receipt
          // confirms publication; match its id before claiming the words are on stream.
          const published=s.airing && recent.slice().reverse().find(x=>x.kind==='aired' && x.line_id===s.airing && typeof x.text==='string' && x.text);
          setAired(published ? {id:published.line_id,text:published.text,boot:s.voice_boot} : null);
          const active=current.current.preparing;
          const dropped=active && recent.find(x=>x.kind==='line_dropped' && x.line_id===active.id);
          if(dropped)setWarning('Line was not aired: '+(dropped.reason || 'The show dropped it.'));
          setPreparing(old=>old && (s.epoch!==old.epoch || s.privacy || s.mode==='off_air' || s.mode==='paused' || recent.some(x=>x.kind==='aired' && x.line_id===old.id) || (dropped && dropped.line_id===old.id) || (p.pending.length && p.pending[0].line_id!==old.id)) ? null : old);
        });
      } catch (_) {
        if (!alive.current || gen!==generation.current || safety.current) return;
        if(coreStore) coreStore.current=null;
        setOnline(false);setAnswered(true);setQueue([]);setPreparing(null);setFence(n=>n+1);
      }
    }
    async function refreshViews() {
      try {const r=await get('views/state'); if (alive.current) setViews(r && r.ok && r.connected && r.boxes && r.boxes.slots ? r : null);}
      catch (_) {if (alive.current) setViews(null);}
    }
    useEffect(()=>{alive.current=true;async function poll(){if(polling.current||locked.current||safety.current)return;polling.current=true;try{await refresh();}finally{polling.current=false;}}poll();refreshViews();const a=window.setInterval(poll,1000), b=window.setInterval(refreshViews,5000);return()=>{alive.current=false;generation.current++;window.clearInterval(a);window.clearInterval(b);};},[]);
    useEffect(()=>{if(!online || !lastGood)return;const age=Date.now()-lastGood, delay=age<0?0:Math.max(0,4000-age);const timer=window.setTimeout(()=>{if(coreStore)coreStore.current=null;setOnline(false);setAnswered(true);setQueue([]);setPreparing(null);setFence(n=>n+1);},delay);return()=>window.clearTimeout(timer);},[online,lastGood]);
    async function action(path,body,isSafety) {
      const age=Date.now()-current.current.lastGood;
      if (!current.current.online || age<0 || age>=4000 || (!isSafety && (locked.current || safety.current))) return false;
      if(coreStore)coreStore.current=null;
      if (isSafety) safety.current++; else {locked.current=true;setBusy(true);}
      generation.current++; setWarning('');
      const cutsHost=path==='stop' || path==='privacy' && body.on || path==='mode' && body.mode==='off_air';
      if (cutsHost) {cuts.current++;setFence(n=>n+1);setPreparing(null);setQueue([]);setSuspendHost(true);}
      let ok=false;
      try {await post(path,body);ok=true;}
      catch(e) {if(alive.current)setWarning(e.message);}
      finally {
        if(isSafety)safety.current--; else {locked.current=false;if(alive.current)setBusy(false);}
        if(alive.current&&!safety.current) await refresh();
        if(cutsHost) cuts.current--;
        if(alive.current && !cuts.current && !safety.current)setSuspendHost(false);
      }
      return ok;
    }
    async function decide(which,line) {
      const c=current.current;
      if(!line || !c.online || !c.state || c.state.privacy || c.state.mode==='off_air' || c.busy || line.epoch!==c.state.epoch || !c.queue.some(x=>x.line_id===line.line_id && x.epoch===line.epoch)) return;
      if(which==='approve' && c.state.mode==='autonomous' && !window.confirm('Approve this line in Autonomous mode?')) return;
      if(which==='approve') setPreparing({id:line.line_id,epoch:line.epoch});
      const ok=await action(which,{line_id:line.line_id,epoch:line.epoch},false);
      if (!ok && alive.current) setPreparing(null);
    }
    async function all() {
      const c=current.current, lines=group(c.queue);
      if(!c.online || Date.now()-c.lastGood<0 || Date.now()-c.lastGood>=4000 || c.busy || !c.state || c.state.privacy || lines.length<2 || lines.some(x=>x.epoch!==c.state.epoch)) return;
      if(coreStore)coreStore.current=null;
      if(c.state.mode==='autonomous' && !window.confirm('Approve these lines in Autonomous mode?')) return;
      if(locked.current || safety.current) return;
      locked.current=true;setBusy(true);setWarning('');const token=++generation.current;
      let last=null;
      const ok=await approveAll(lines,async line=>{
        if(safety.current || token!==generation.current || !alive.current) return false;
        try {await post('approve',{line_id:line.line_id,epoch:line.epoch});last=line;return true;}
        catch(e){if(alive.current)setWarning('Approval stopped: '+e.message);return false;}
      });
      if(last && alive.current && token===generation.current)setPreparing({id:last.line_id,epoch:last.epoch});
      if(!ok && token!==generation.current && alive.current)setWarning('Approval stopped because the show state changed.');
      locked.current=false;if(alive.current){setBusy(false);if(!safety.current)await refresh();}
      return ok;
    }
    function mode(next) {
      const c=current.current;
      if(!c.online || !c.state || c.state.mode===next) return;
      if(next==='autonomous' && (c.state.mode!=='approval' || !window.confirm('Autonomous mode releases guarded lines without desk approval. Continue?'))) return;
      action('mode',{mode:next},false);
    }
    function safe(name) {
      const c=current.current;if(!c.online || !c.state)return;
      if(name==='privacy') {if(c.state.privacy && !window.confirm('Turn privacy off? Viewers may see the show again.'))return;action('privacy',{on:!c.state.privacy},true);}
      else if(name==='mute') action('mute',{on:!c.state.muted},true);
      else if(window.confirm('Stop all? This cancels speech and pending lines and pauses the show.')) action('stop',{},true);
    }
    const latest=useRef(null);latest.current={decide};
    useEffect(()=>{function key(e){if(!shortcut(e))return;const c=current.current;if(c.online && !c.busy && c.queue.length && !c.preparing){e.preventDefault();latest.current.decide('approve',c.queue[0]);}}window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);},[]);
    const s=online?state:null, first=queue[0], lines=group(queue), others=queue.length-lines.length;
    const waiting=first && s && !s.privacy && s.mode!=='off_air' && !(preparing && preparing.id===first.line_id);
    const onStream=!!(s && !s.privacy && s.live && aired && s.airing===aired.id);
    useLayoutEffect(()=>{const el=lineBox.current;const measure=()=>setScrollHint(!!(waiting && el && el.scrollHeight>el.clientHeight+1));measure();window.addEventListener('resize',measure);return()=>window.removeEventListener('resize',measure);},[waiting && first.text]);
    const title=s ? (s.privacy ? 'PRIVACY: viewers see the cover' : ({off_air:'Off air',approval:'Approval',autonomous:'Autonomous',paused:'Paused'}[s.mode] || 'Unknown')) : answered?'Disconnected':'Connecting…';
    const subtitle=s ? [s.show,typeof s.beats_left==='number'?s.beats_left+' beats left':'',withheld?'withheld '+withheld:''].filter(Boolean).join(' · ') : answered?'Desk cannot reach the show':'Connecting to the show…';
    const boxes=views && views.boxes, slots=boxes && boxes.slots, available=boxes && boxes.available || {};
    const unavailableSource=slots && Object.values(slots).find(x=>x!=='empty' && !(available[x] && available[x].available));
    const swapReason=!online ? 'Desk cannot reach the show.' : views && views.stage_profile ? 'This is a stage profile, not the owner presentation runtime. Ask your owner agent to use onair_layout, onair_obs_scene or onair_window_share.' : unavailableSource ? (available[unavailableSource] && available[unavailableSource].reason || 'A selected source is unavailable to this dashboard.') : slots && slots.sub1==='empty' ? 'Main cannot be empty.' : '';
    async function swap(){if(!slots || swapReason || busy)return;setBusy(true);setWarning('');try{await post('views/boxes',{op:'swap',a:'main',b:'sub1'});}catch(e){setWarning(e.message);}finally{await refreshViews();if(alive.current)setBusy(false);}}
    const canShowBoxes=!!(slots && boxes.canvas && Number.isFinite(boxes.canvas.width) && boxes.canvas.width>0 && Number.isFinite(boxes.canvas.height) && boxes.canvas.height>0 && boxes.geometry && ['main','sub1','sub2'].filter(slot=>slots[slot]!=='empty').every(slot=>{const r=boxes.geometry[slot] && boxes.geometry[slot].bounds;return r && ['x','y','width','height'].every(k=>Number.isFinite(r[k])) && r.width>0 && r.height>0;}));
    const boxDescription=slots && ['main','sub1','sub2'].map((slot,i)=>['Main','Sub 1','Sub 2'][i]+': '+({scene:'Scene',slides:'Slides',window:'Window',empty:'Empty'}[slots[slot]]||slots[slot])).join('; ')+'. Box arrangement, not a live preview. '+(views.program_scene==='On Air: Boxes'?'Boxes on program.':views.program_scene?'Boxes are not on program.':'Program scene unknown.')+(swapReason?' '+swapReason:'');
    function pos(slot){
      const b=boxes.geometry[slot].bounds, c=boxes.canvas;
      return {left:(b.x/c.width*100)+'%',top:(b.y/c.height*100)+'%',width:(b.width/c.width*100)+'%',height:(b.height/c.height*100)+'%'};
    }
    return h('main',{className:'onair-live'},
      h('header',{className:'live-top'+(s&&s.privacy?' live-private':'')},
        h('div',{className:'live-status'},h('div',{className:'live-status-title'},h('span',{className:'live-dot'+(s&&s.live?' is-on':''),'aria-hidden':'true'}),h('strong',null,title),s&&s.muted&&h('span',{className:'live-muted'},'Muted')),h('div',{className:'live-subtitle',title:withheld?'Withheld lines in the latest 60 receipts':undefined},subtitle),h('div',{className:'live-mic-indicator'})),
        h('div',{className:'live-controls'},h('label',{className:'live-mode'},h('span',null,'Mode'),h('select',{'aria-label':'Show mode',value:s?s.mode:'',disabled:!s||busy,onChange:e=>mode(e.target.value)},
          !s&&h('option',{value:''},'Unavailable'),s&&s.mode==='paused'&&h('option',{value:'paused',disabled:true},'Paused'),[['off_air','Off air'],['approval','Approval'],['autonomous','Autonomous']].map(([value,label])=>h('option',{key:value,value,disabled:value==='autonomous'&&s&&s.mode!=='approval'&&s.mode!=='autonomous'},label)))),
          h('button',{type:'button',className:'onair-privacy-button',disabled:!s,'aria-pressed':!!s&&s.privacy,onClick:()=>safe('privacy')},'Privacy'),
          h('button',{type:'button',disabled:!s,'aria-pressed':!!s&&s.muted,onClick:()=>safe('mute')},'Mute voice'),
          h('button',{type:'button',className:'live-stop',disabled:!s,onClick:()=>safe('stop')},'Stop'))),
      h('section',{className:'live-next','aria-label':'Next line'},
        waiting&&onStream&&h('div',{className:'live-speaking',title:'Speaking now: '+aired.text},h('span',{className:'live-dot is-on','aria-hidden':'true'}),h('span',{className:'live-speaking-text'},'Speaking now: '+aired.text)),
        h('div',{className:'live-context',title:waiting?context(first,receipts):undefined,tabIndex:waiting?0:undefined},waiting?context(first,receipts):onStream?'● Speaking now':'\u00a0'),
        h('div',{className:'live-line',ref:lineBox,tabIndex:waiting||onStream?0:undefined},h('span',{key:waiting?'waiting':onStream?aired.id:'idle',className:'live-line-content'},waiting?first.text:onStream?aired.text:preparing?'Voice is being prepared…':!s?(answered?'Desk cannot reach the show':'Connecting to the show…'):s.privacy?'Privacy is on.':s.mode==='off_air'?'Off air. Choose Approval to start.':s.mode==='paused'?'Paused.':(s.in_flight || s.host_turns && s.host_turns.waiting>0)?'She is thinking…':'Nothing waiting.')),
        h('div',{className:'live-actions'},waiting&&h(React.Fragment,null,h('button',{type:'button',className:'live-approve',disabled:busy||!s||first.epoch!==s.epoch,onClick:()=>decide('approve',first)},'Approve ',h('kbd',null,'↵'),h('kbd',null,'A')),h('button',{type:'button',disabled:busy,onClick:()=>decide('reject',first)},'Skip'),lines.length>1&&h('button',{type:'button',disabled:busy,onClick:all},'Approve all '+lines.length))),
        h('div',{className:'live-count'},waiting?[(queue.length>1?'Next: 1 of '+queue.length:''),(scrollHint?'Scroll for more':''),(lines.length>1?(lines.length-1)+' more '+(lines.length===2?'line':'lines')+' in this reply':''),others?others+' other waiting':''].filter(Boolean).join(' · '):'\u00a0'),
        h('div',{className:'live-warning',role:'alert'},warning)),
      h('footer',{className:'live-bottom'},h('div',{className:'live-talk',ref:talk},h(HostLine,{compact:true,data:state,online:!!(online&&!suspendHost&&state&&state.mode!=='off_air'),fence,onMicStatus}),
        (!(state && state.host_turns && state.host_turns.enabled === true)) && h('div',{className:'live-composer-placeholder'},h('button',{type:'button',className:'onair-mic-pill',disabled:true,title:'Microphone unavailable'},'🎙 Mic off'),h('input',{disabled:true,placeholder:'or type a line','aria-label':'Host input unavailable'}),h('button',{type:'button',disabled:true},'Send'),h('button',{type:'button',disabled:true},'Cut in'))),
        canShowBoxes&&h('div',{className:'live-view'},h('div',{className:'live-picture',role:'img','aria-label':boxDescription,title:boxDescription,tabIndex:0},['main','sub1','sub2'].filter(slot=>slot==='main'||slots[slot]!=='empty').map(slot=>h('div',{key:slot,className:'live-box live-box-'+slot,style:pos(slot),'aria-hidden':'true'},slot==='main'&&h('span',null,({scene:'Scene',slides:'Slides',window:'Window',empty:'Empty'}[slots[slot]]||slots[slot]))))),
          h('button',{type:'button',disabled:!!swapReason||busy,title:swapReason||undefined,'aria-label':swapReason?'Swap: '+swapReason:'Swap Main and Sub 1',onClick:swap},'Swap'))));
  };
})();
