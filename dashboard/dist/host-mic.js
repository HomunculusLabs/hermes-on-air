/* Push-to-talk desk component. No microphone is kept between clips. */
(function () {
  'use strict';
  const SDK = window.__HERMES_PLUGIN_SDK__, React = SDK.React, h = React.createElement;
  const {useState, useEffect, useRef} = React;
  const BASE = '/api/plugins/on-air/';

  function OpenMic({data, online, enabled, onStart, onActive, onStatus, onDisplayStatus, compact, cutOnTalk}) {
    const [running, setRunning] = useState(false), [message, setMessage] = useState(''), [dropped, setDropped] = useState('');
    const current = useRef({data, online}); current.current = {data, online};
    const session = useRef(null), alive = useRef(true), serial = useRef(0), pillMeter = useRef(null);
    useEffect(()=>{if (compact && onDisplayStatus) onDisplayStatus([message, dropped].filter(Boolean).join(' · '));},[compact, message, dropped, onDisplayStatus]);
    const valid = s => s && s.live === true && s.privacy === false &&
      ['approval', 'autonomous'].includes(s.mode) && Number.isSafeInteger(s.epoch) && /^[0-9a-f]{32}$/.test(s.voice_boot);
    function close(why) {
      serial.current++;
      const s = session.current; session.current = null;
      if (s) {
        s.closed = true; s.queue.length = 0;
        if (s.clip) {clearTimeout(s.clip.watchdog); s.clip.parts.length=0; s.clip=null;}
        s.ring.length=0; s.ringSize=0;
        clearInterval(s.timer); clearTimeout(s.hiddenTimer);
        if (s.abort) s.abort.abort();
        if (s.processor) s.processor.disconnect();
        if (s.source) s.source.disconnect();
        if (s.gain) s.gain.disconnect();
        if (s.stream) s.stream.getTracks().forEach(t => t.stop());
        if (s.context) s.context.close().catch(() => {});
        if (s.badge) s.badge.remove();
        document.title = document.title.replace(/^🔴 MIC OPEN —\s*/, '');
      }
      if (s && why && onStatus) onStatus(why);
      if (alive.current) {setRunning(false); onActive(false); if (why) setMessage(why);}
    }
    function wav(samples) {
      const n = samples.reduce((sum, part) => sum + part.length, 0);
      const buffer = new ArrayBuffer(44 + n * 2), view = new DataView(buffer);
      const text = (at, v) => {for (let i=0;i<v.length;i++) view.setUint8(at+i,v.charCodeAt(i));};
      text(0,'RIFF'); view.setUint32(4,36+n*2,true); text(8,'WAVE'); text(12,'fmt ');
      view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,1,true);
      view.setUint32(24,16000,true); view.setUint32(28,32000,true); view.setUint16(32,2,true);
      view.setUint16(34,16,true); text(36,'data'); view.setUint32(40,n*2,true);
      let offset=44;
      for (const part of samples) for (const v of part) {view.setInt16(offset, Math.max(-32768,Math.min(32767,Math.round(v*32767))),true); offset+=2;}
      return new Blob([buffer],{type:'audio/wav'});
    }
    async function drain(s) {
      if (s.busy || s.closed) return;
      s.busy = true;
      while (!s.closed && s.queue.length) {
        const clip = s.queue.shift();
        s.abort = new AbortController();
        try {
          const result = await SDK.fetchJSON(BASE+'host/mic', {method:'POST', signal:s.abort.signal,
            headers:{'Content-Type':'audio/wav','X-OnAir-Epoch':String(clip.epoch),'X-OnAir-Boot':clip.boot,
              'X-OnAir-Id':clip.id,'X-OnAir-Mode':'send','X-OnAir-Capture':'open'}, body:clip.audio});
          if (s.closed) break;
          if (clip.epoch !== s.epoch) {s.abort=null; continue;}
          const text = result && result.transcript ? 'You: '+result.transcript+' · ' : '';
          if (result && result.ok && result.queued) {s.refused=0; setMessage(text+'Queued at position '+result.position+'.');}
          else {s.refused++; setMessage(text+'Refused: '+(result && (result.message || result.reason || result.error) || 'Clip was not accepted.'));}
        } catch (error) {if (!s.closed && clip.epoch === s.epoch) {s.refused++; setMessage('Refused: '+(error && error.message || 'transcription or connection unavailable.'));}}
        s.abort = null;
        if (s.refused >= 3) {close('Open mic turned off after three refused uploads.'); break;}
      }
      s.busy = false;
    }
    function enqueue(s, clip) {
      if (s.closed || clip.voiced < 400 || !clip.parts.length) return;
      const audio=wav(clip.parts);
      if (audio.size > 2*1024*1024) {setMessage('Clip too large; discarded.'); return;}
      if (s.queue.length >= 2) {s.queue.shift(); setDropped('Upload queue full: oldest waiting utterance dropped.');}
      s.queue.push({audio, epoch:clip.epoch, boot:clip.boot,
        id:Array.from(crypto.getRandomValues(new Uint8Array(16)), b=>b.toString(16).padStart(2,'0')).join('')});
      drain(s);
    }
    async function inspectClip(s, clip) {
      clip.pending = true;
      clip.watchdog = setTimeout(()=>{if (!s.closed && s.clip === clip && (clip.pending || clip.cutting)) close('Open mic turned off: show state or cut timed out.');},5000);
      try {
        const fresh=await SDK.fetchJSON(BASE+'state');
        if (s.closed || s.clip!==clip) return;
        if (!valid(fresh) || fresh.epoch!==clip.epoch || fresh.voice_boot!==clip.boot ||
            !(fresh.airing === null || (typeof fresh.airing === 'string' && fresh.airing.length))) throw Error('Show changed; open mic turned off.');
        clip.pending=false;
        if (cutOnTalk && fresh.airing !== null) clip.needsCut=true;
        else {clip.ready=true; clearTimeout(clip.watchdog);}
      } catch (error) {if (!s.closed && s.clip===clip) close(error && error.message || 'Show state unavailable; open mic turned off.');}
    }
    async function cutForClip(s, clip) {
      if (clip.cutting || s.closed || s.clip!==clip) return;
      clip.cutting=true; s.cutting=true;
      try {
        const result=await SDK.fetchJSON(BASE+'cut-in',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({epoch:clip.epoch,boot:clip.boot,only_if_airing:true})});
        if (s.closed || s.clip!==clip) return;
        const next=result && result.state;
        if (!result || result.ok!==true || typeof result.cut!=='boolean' || !valid(next) || next.voice_boot!==clip.boot ||
            next.epoch !== clip.epoch+(result.cut ? 1 : 0)) throw Error(result && (result.message || result.error) || 'Cut was refused.');
        if (result.cut) {
          s.previousEpoch=s.epoch; s.epoch=next.epoch; clip.epoch=next.epoch;
          s.queue.length=0;
          if (s.abort) s.abort.abort();
          setMessage('Cut requested; listening.');
        }
        clip.ready=true; clip.cutting=false; clip.needsCut=false; s.cutting=false; clearTimeout(clip.watchdog);
      } catch (error) {if (!s.closed && s.clip===clip) close((error && error.message || 'Cut unavailable.')+' Open mic turned off.');}
    }
    async function open() {
      if (session.current || !enabled) return;
      const before=current.current;
      if (!before.online || !valid(before.data)) {setMessage('Show unavailable, stopped or private.'); return;}
      const token=++serial.current, binding={epoch:before.data.epoch, boot:before.data.voice_boot};
      const s={queue:[], closed:false, refused:0, clip:null, ring:[], ringSize:0, phase:0}; session.current=s;
      onActive(true); setRunning(true); setDropped(''); if (onStatus) onStatus(''); setMessage('Requesting microphone permission…');
      try {
        const fresh=await SDK.fetchJSON(BASE+'state');
        if (token!==serial.current) return;
        if (!valid(fresh) || fresh.epoch!==binding.epoch || fresh.voice_boot!==binding.boot) throw Error('Show changed; open mic not started.');
        const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
        s.stream=stream;
        if (token!==serial.current) {stream.getTracks().forEach(t=>t.stop()); return;}
        if (!current.current.online || !valid(current.current.data) || current.current.data.epoch!==binding.epoch || current.current.data.voice_boot!==binding.boot) throw Error('Show changed; open mic not started.');
        s.epoch=binding.epoch; s.boot=binding.boot;
        const badge=document.createElement('aside'); badge.setAttribute('role','status');
        badge.style.cssText='position:fixed;z-index:2147483647;top:8px;right:8px;max-width:calc(100vw - 16px);box-sizing:border-box;background:#a00000;color:white;padding:12px;font-size:20px;font-weight:bold;border:3px solid white;box-shadow:0 1px 8px #000';
        if (compact) {badge.className='onair-live-mic-badge'; badge.style.cssText='';}
        const label=document.createElement('span'); label.textContent='● Mic open: listening '; badge.appendChild(label);
        const meterLabel=document.createElement('span'); meterLabel.textContent='Level '; meterLabel.style.cssText='font-size:14px;margin-left:8px;margin-right:4px'; badge.appendChild(meterLabel);
        const meter=document.createElement('meter'); meter.min=0; meter.max=1; meter.value=0; meter.setAttribute('aria-label','Microphone level'); badge.appendChild(meter);
        const off=document.createElement('button'); off.type='button'; off.textContent='Turn off'; off.style.cssText='margin-left:10px;font-size:16px;min-height:44px'; off.onclick=()=>close('Open mic turned off.'); badge.appendChild(off);
        (compact && document.querySelector('.live-mic-indicator') || document.body).appendChild(badge); s.badge=badge; document.title='🔴 MIC OPEN — '+document.title;
        setRunning(true); setMessage('Open mic is listening.'); if (onStart) onStart();
        stream.getAudioTracks().forEach(track=>track.addEventListener('ended',()=>{if (!s.closed) close('Microphone permission was revoked or the device ended.');}));
        const AudioCtx=window.AudioContext || window.webkitAudioContext;
        const ctx=new AudioCtx(); s.context=ctx;
        const source=ctx.createMediaStreamSource(stream), analyser=ctx.createAnalyser(), processor=ctx.createScriptProcessor(2048,1,1), gain=ctx.createGain();
        s.source=source; s.processor=processor; s.gain=gain; gain.gain.value=0;
        analyser.fftSize=2048; source.connect(analyser); source.connect(processor); processor.connect(gain); gain.connect(ctx.destination);
        const ratio=ctx.sampleRate/16000;
        processor.onaudioprocess=e=>{
          if (s.closed) return;
          const input=e.inputBuffer.getChannelData(0), out=[];
          for (let i=0;i<input.length;i++) {s.phase+=1; while (s.phase>=ratio) {s.phase-=ratio; out.push(input[i]);}}
          const part=Float32Array.from(out);
          if (s.clip) s.clip.parts.push(part);
          s.ring.push(part); s.ringSize+=part.length;
          // Keep 300 ms before the 150 ms speech-confirmation window.
          while (s.ring.length && s.ringSize-s.ring[0].length>=7200) s.ringSize-=s.ring.shift().length;
        };
        const dataArray=new Float32Array(analyser.fftSize); let start=performance.now(), last=start, floor=0, count=0, above=0, silence=0;
        s.timer=setInterval(()=>{
          if (s.closed) return;
          if (!document.title.startsWith('🔴 MIC OPEN —')) document.title='🔴 MIC OPEN — '+document.title;
          const now=performance.now(), dt=Math.min(150,now-last); last=now;
          analyser.getFloatTimeDomainData(dataArray); let sum=0; for (const value of dataArray) sum+=value*value;
          const level=Math.sqrt(sum/dataArray.length); meter.value=Math.min(1,level*8); if (pillMeter.current) pillMeter.current.value=meter.value;
          if (now-start<1000) {floor+=(level-floor)/(++count); return;}
          const threshold=Math.max(.012, floor*3);
          const speaking=level>threshold;
          if (!speaking && !s.clip) floor=floor*.995+level*.005;
          if (speaking) {above+=dt; silence=0;} else {above=0; silence+=dt;}
          if (!s.clip && above>=150) {
            s.clip={parts:s.ring.slice(), epoch:s.epoch, boot:s.boot, started:now-above, voiced:above};
            inspectClip(s,s.clip);
          } else if (s.clip && speaking) s.clip.voiced+=dt;
          if (s.clip && s.clip.needsCut && above>=400 && !s.clip.cutting) cutForClip(s,s.clip);
          if (s.clip && (silence>=800 || now-s.clip.started>=28000) && !s.clip.cutting) {
            const clip=s.clip; s.clip=null; above=0; silence=0; clearTimeout(clip.watchdog);
            if (clip.ready) enqueue(s,clip);
          }
        },50);
        if (document.hidden) {s.hiddenAt=Date.now(); s.hiddenTimer=setTimeout(()=>close('Open mic turned off because the tab was hidden.'),60000);}
      } catch (error) {if (token===serial.current) close(error && error.message || 'Microphone permission or capture is unavailable.');}
    }
    useEffect(()=>{
      const s=session.current;
      if (!s) return;
      const ownCutAdvance = s.cutting && data && data.epoch === s.epoch+1;
      const priorToOwnCut = s.previousEpoch !== undefined && data && data.epoch === s.previousEpoch;
      if (data && data.epoch === s.epoch) s.previousEpoch=undefined;
      if (!online || !valid(data) || (s.boot && (data.voice_boot!==s.boot ||
          (data.epoch!==s.epoch && !ownCutAdvance && !priorToOwnCut))))
        close(!online ? 'Open mic turned off: desk lost the stage.' : 'Open mic turned off: show changed, stopped or privacy is on.');
    },[online,data && data.live,data && data.mode,data && data.privacy,data && data.epoch,data && data.voice_boot]);
    useEffect(()=>{
      function visibility() {
        const s=session.current; if (!s) return;
        clearTimeout(s.hiddenTimer);
        if (document.hidden) {s.hiddenAt=Date.now(); s.hiddenTimer=setTimeout(()=>close('Open mic turned off because the tab was hidden.'),60000);}
        else if (s.hiddenAt && Date.now()-s.hiddenAt >= 60000) close('Open mic turned off because the tab was hidden.');
        else s.hiddenAt=null;
      }
      function safetyClick(e) {
        if (!session.current || !e.target.closest) return;
        if (e.target.closest('.onair-stop, .live-stop')) close('Open mic turned off: Stop was requested.');
        else if (e.target.closest('.onair-privacy-button') && current.current.data && !current.current.data.privacy) close('Open mic turned off: privacy was requested.');
      }
      function safetyKey(e) {
        if (!session.current || e.repeat || e.ctrlKey || e.metaKey || e.altKey || !e.target || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable) return;
        if (e.key === 'Escape') close('Open mic turned off: Stop was requested.');
        else if (e.key.toLowerCase() === 'p' && current.current.data && !current.current.data.privacy) close('Open mic turned off: privacy was requested.');
      }
      document.addEventListener('visibilitychange',visibility);
      window.addEventListener('click',safetyClick,true); window.addEventListener('keydown',safetyKey,true);
      return ()=>{document.removeEventListener('visibilitychange',visibility); window.removeEventListener('click',safetyClick,true); window.removeEventListener('keydown',safetyKey,true);};
    },[]);
    useEffect(()=>()=>{alive.current=false; if (session.current) close('Open mic turned off: desk closed or lost the stage.');},[]);
    if (!enabled) return null;
    if (compact) return h('button',{type:'button',className:'onair-mic-pill'+(running?' is-open':''), 'aria-pressed':running, title:running?'Click to turn off microphone':'click to talk hands-free', onClick:()=>running?close('Open mic turned off.'):open(), disabled:!enabled||!online}, running ? '● Mic open' : '🎙 Mic off', running && h('meter',{ref:pillMeter,min:0,max:1,defaultValue:0,'aria-label':'Microphone level'}));
    return h('div',{className:'onair-open-mic'},h('label',null,
      h('input',{type:'checkbox',checked:running,onChange:e=>e.target.checked?open():close('Open mic turned off.')}),' Open mic'),
      h('p',{className:'onair-help'},'Open mic sends each detected transcript directly. Review below applies to hold-to-talk; replies still follow the guard and show mode.'),
      h('p',{role:'status'},message), dropped && h('p',{role:'status'},dropped));
  }

  window.OnAirHostMic = function HostMic({data, online, onReview, onStart, onOpenStatus, onDisplayStatus, compact = false, moreControls, draftPresent, shortcutsEnabled = false}) {
    const [enabled, setEnabled] = useState(false);
    const [openEnabled, setOpenEnabled] = useState(false), [openActive, setOpenActive] = useState(false);
    const [cutOnTalk, setCutOnTalk] = useState(true);
    const [releaseMode, setReleaseMode] = useState(null);
    const [modeSaving, setModeSaving] = useState(false);
    const modeName = React.useId();
    const modeLock = useRef(false);
    const [phase, setPhase] = useState('idle');
    const [message, setMessage] = useState('');
    useEffect(()=>{if (compact && message && onDisplayStatus) onDisplayStatus(message);},[compact,message,onDisplayStatus]);
    function sizeMore() {
      const more=document.querySelector('.onair-mic-more[open]'), panel=more && more.querySelector('.onair-mic-popover'), approve=document.querySelector('.live-approve');
      if (panel) panel.style.maxHeight=approve ? Math.max(0, more.getBoundingClientRect().top-approve.getBoundingClientRect().bottom-12)+'px' : '';
    }
    useEffect(()=>{
      if (!compact) return;
      window.addEventListener('resize',sizeMore);
      const observer=typeof ResizeObserver==='function' ? new ResizeObserver(sizeMore) : null;
      if (observer) {const next=document.querySelector('.live-next'), bottom=document.querySelector('.live-bottom'); if(next)observer.observe(next); if(bottom)observer.observe(bottom);}
      return ()=>{window.removeEventListener('resize',sizeMore);if(observer)observer.disconnect();};
    },[compact]);
    const [seconds, setSeconds] = useState(0);
    const [toggle, setToggle] = useState(false);
    const current = useRef({data, online}); current.current = {data, online};
    const active = useRef(null), status = useRef('idle'), generation = useRef(0), alive = useRef(true), gesture = useRef(null);
    const change = value => {status.current = value; if (alive.current) setPhase(value);};
    const say = value => {if (alive.current) setMessage(value);};
    useEffect(() => {
      let live = true;
      SDK.fetchJSON(BASE + 'host/mic').then(r => {if (live) {setEnabled(r && r.enabled === true); setOpenEnabled(r && r.open_enabled === true); setCutOnTalk(!r || r.cut_in_on_talk !== false);}}).catch(() => {});
      SDK.fetchJSON(BASE + 'host/mic/mode').then(r => {
        if (!live) return;
        if (r && r.ok === true && ['review','send'].includes(r.mode) && typeof r.locked === 'boolean' && (!r.locked || r.mode === 'review')) setReleaseMode(r);
        else say('Microphone mode is unavailable; recording cannot start.');
      }).catch(() => {if (live) say('Microphone mode is unavailable; recording cannot start.');});
      return () => {live = false;};
    }, []);
    useEffect(() => {
      if (draftPresent === false) setMessage(m => m === 'Transcript ready in Host line. Review it before sending.' ? '' : m);
    }, [draftPresent]);
    function stopTracks(item) {
      if (item && item.stream && !item.stopped) {
        item.stopped = true; item.stream.getTracks().forEach(track => track.stop());
      }
    }
    function discard(why) {
      generation.current++;
      const item = active.current; active.current = null;
      if (item) {
        item.discard = true; item.chunks = []; clearTimeout(item.cap); clearTimeout(item.stopWatchdog); clearInterval(item.timer);
        stopTracks(item);
        try {if (item.recorder && item.recorder.state !== 'inactive') item.recorder.stop();} catch (_) {}
      }
      if (alive.current) setToggle(false);
      change('idle'); if (why) say(why);
    }
    async function chooseMode(value) {
      if (!releaseMode || releaseMode.locked || modeLock.current || status.current !== 'idle') return;
      modeLock.current = true; setModeSaving(true);
      try {
        const result = await SDK.fetchJSON(BASE + 'host/mic/mode', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode:value})});
        if (!result || result.ok !== true || result.mode !== value || typeof result.locked !== 'boolean') throw Error('Mode was not saved.');
        if (alive.current) {setReleaseMode(result); say('Microphone mode saved.');}
      } catch (error) {
        if (alive.current) {setReleaseMode(null); say(error && error.message || 'Mode could not be confirmed; recording is unavailable.');}
      } finally {modeLock.current = false; if (alive.current) setModeSaving(false);}
    }
    async function begin(clickMode = false) {
      if (!enabled || openActive || !releaseMode || modeLock.current || status.current !== 'idle') return;
      const snapshot = current.current;
      if (!snapshot.online || !snapshot.data || snapshot.data.privacy !== false || !Number.isSafeInteger(snapshot.data.epoch) || !/^[0-9a-f]{32}$/.test(snapshot.data.voice_boot)) {
        say('Show state is unavailable or privacy is on.'); return;
      }
      if (onStart) onStart();
      setToggle(clickMode);
      const item = {epoch:snapshot.data.epoch, boot:snapshot.data.voice_boot, mode:releaseMode.mode, chunks:[], discard:false, started:Date.now()};
      const token = ++generation.current; active.current = item; change('requesting'); say('Checking show state…');
      let requestingDevice = false;
      try {
        let latest = await SDK.fetchJSON(BASE + 'state');
        const valid = value => value && value.live === true && ['approval','autonomous'].includes(value.mode) &&
          value.privacy === false && value.voice_boot === item.boot && value.epoch === item.epoch &&
          (value.airing === null || (typeof value.airing === 'string' && value.airing.length > 0));
        if (token !== generation.current) return;
        if (!valid(latest)) {discard('Show changed or its airing state is unavailable; recording not started.'); return;}
        if (cutOnTalk && latest.airing !== null) {
          const result = await SDK.fetchJSON(BASE + 'cut-in', {method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({epoch:item.epoch, boot:item.boot, only_if_airing:true})});
          if (token !== generation.current) return;
          if (!result || result.ok !== true || typeof result.cut !== 'boolean' || !result.state || result.state.epoch !== item.epoch + (result.cut ? 1 : 0) ||
              result.state.voice_boot !== item.boot || result.state.privacy !== false || result.state.live !== true ||
              !['approval','autonomous'].includes(result.state.mode)) {
            discard(result && (result.message || result.error) || 'Cut was refused; recording not started.'); return;
          }
          // This epoch advance is safe only because this cut was requested by this press.
          item.epoch = result.state.epoch; item.preCutData = snapshot.data; item.cutRequested = result.cut;
          latest = await SDK.fetchJSON(BASE + 'state');
          if (token !== generation.current) return;
          if (!valid(latest)) {discard('Show changed after the cut; recording not started.'); return;}
        }
        requestingDevice = true; say('Requesting microphone permission…');
        item.stream = await navigator.mediaDevices.getUserMedia({audio:true, video:false});
        const now = current.current;
        if (token !== generation.current || !now.online || !now.data || now.data.privacy !== false ||
            (now.data.epoch !== item.epoch && now.data !== item.preCutData) || now.data.voice_boot !== item.boot) {
          stopTracks(item); if (token === generation.current) discard('Show changed; recording discarded.'); return;
        }
        item.recorder = new MediaRecorder(item.stream, {mimeType:'audio/webm'});
        item.recorder.ondataavailable = e => {
          if (item.discard || !e.data || !e.data.size) return;
          item.size = (item.size || 0) + e.data.size;
          if (item.size > 2 * 1024 * 1024) {discard('Clip too large; recording discarded.'); return;}
          item.chunks.push(e.data);
        };
        item.recorder.onerror = () => discard('Recording failed; clip discarded.');
        item.recorder.onstop = async () => {
          clearTimeout(item.stopWatchdog);
          if (item.discard || token !== generation.current) return;
          change('transcribing'); say('Transcribing…');
          try {
            // Validate the old binding before uploading; never replace it with this epoch.
            const latest = await SDK.fetchJSON(BASE + 'state');
            if (token !== generation.current) return;
            if (!latest || latest.privacy !== false || latest.epoch !== item.epoch || latest.voice_boot !== item.boot) {
              discard('Show changed or privacy turned on; clip discarded.'); return;
            }
            const result = await SDK.fetchJSON(BASE + 'host/mic', {method:'POST',
              headers:{'Content-Type':'audio/webm', 'X-OnAir-Epoch':String(item.epoch), 'X-OnAir-Boot':item.boot, 'X-OnAir-Mode':item.mode},
              body:new Blob(item.chunks, {type:'audio/webm'})});
            item.chunks = [];
            if (token !== generation.current) return;
            if (result && result.ok && result.review) {
              onReview(result.transcript, item.epoch, item.boot); say('Transcript ready in Host line. Review it before sending.');
            } else if (result && result.ok && result.queued) {
              say('Transcript: ' + result.transcript + ' · Queued at position ' + result.position + '.');
            } else {
              say((result && result.transcript ? 'Transcript: ' + result.transcript + ' · ' : '') + 'Refused: ' +
                  (result && (result.message || result.error) || 'Clip was not accepted.') +
                  (result && result.reason === 'stale_epoch' ? ' Record a new line.' : ''));
            }
          } catch (_) {if (token === generation.current) say('Transcription could not be completed.');}
          finally {if (token === generation.current) {active.current = null; change('idle');}}
        };
        item.recorder.start(1000); item.started = Date.now(); change('listening'); setSeconds(0); say(item.cutRequested ? 'Cut requested; listening.' : 'Listening');
        item.timer = setInterval(() => setSeconds(Math.floor((Date.now() - item.started) / 1000)), 250);
        item.cap = setTimeout(() => finish(), 29500);
      } catch (error) {stopTracks(item); if (token === generation.current) {active.current = null; change('idle');
        say(requestingDevice ? 'Microphone permission or recording is unavailable.' : (error && error.message || 'Show or cut is unavailable; recording not started.'));}}
    }
    function finish() {
      const item = active.current;
      if (!item) return;
      if (status.current === 'requesting') {discard('Recording cancelled before permission was granted.'); return;}
      if (status.current !== 'listening') return;
      clearTimeout(item.cap); clearInterval(item.timer); setToggle(false); change('transcribing'); say('Transcribing…');
      item.stopWatchdog = setTimeout(() => discard('Recording stop timed out; clip discarded.'), 5000);
      try {item.recorder.stop();} catch (_) {discard('Recording failed; clip discarded.');} finally {stopTracks(item);}
    }
    useEffect(() => {
      if ((!online || !data || data.privacy !== false || (active.current && (data.epoch !== active.current.epoch || data.voice_boot !== active.current.boot))) &&
          (status.current === 'listening' || status.current === 'requesting')) discard('Show changed or privacy turned on; clip discarded.');
    }, [online, data && data.privacy, data && data.epoch, data && data.voice_boot]);
    useEffect(() => {
      const shortcutAllowed = e => shortcutsEnabled || !!(e.target && e.target.closest && e.target.closest('.onair-host-line, .onair-talk-disclosure'));
      function down(e) {
        if (e.key.toLowerCase() !== 'v' || e.repeat || e.ctrlKey || e.metaKey || e.altKey ||
            /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable || !shortcutAllowed(e)) return;
        if (!enabled) return;
        e.preventDefault(); begin();
      }
      function up(e) {if (e.key.toLowerCase() === 'v' && (status.current === 'listening' || status.current === 'requesting')) {e.preventDefault(); finish();}}
      function blur() {if (status.current === 'listening' || status.current === 'requesting') discard('Desk lost focus; clip discarded.');}
      function visibility() {if (document.hidden) blur();}
      function safetyClick(e) {
        if (e.target.closest && (e.target.closest('.onair-cut-button') ||
            (e.target.closest('.onair-privacy-button') && current.current.data && !current.current.data.privacy)) &&
            (status.current === 'listening' || status.current === 'requesting')) discard(e.target.closest('.onair-privacy-button') ? 'Privacy requested; clip discarded.' : 'Cut requested; clip discarded.');
      }
      function safetyKey(e) {
        if ((e.key.toLowerCase() !== 'p' && e.key.toLowerCase() !== 'c' && e.key !== 'Escape') || e.repeat || e.ctrlKey || e.metaKey || e.altKey ||
            /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable) return;
        if (e.key.toLowerCase() === 'c' && !shortcutAllowed(e)) return;
        if ((e.key === 'Escape' || e.key.toLowerCase() === 'c' || (current.current.data && !current.current.data.privacy)) &&
            (status.current === 'listening' || status.current === 'requesting')) discard(e.key.toLowerCase() === 'p' ? 'Privacy requested; clip discarded.' : 'Safety control requested; clip discarded.');
      }
      window.addEventListener('keydown', down); window.addEventListener('keyup', up); window.addEventListener('blur', blur);
      document.addEventListener('visibilitychange', visibility);
      window.addEventListener('click', safetyClick, true); window.addEventListener('keydown', safetyKey, true);
      return () => {window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); window.removeEventListener('blur', blur);
        document.removeEventListener('visibilitychange', visibility);
        window.removeEventListener('click', safetyClick, true); window.removeEventListener('keydown', safetyKey, true);};
    }, [enabled, cutOnTalk, releaseMode, openActive, shortcutsEnabled]);
    useEffect(() => () => {alive.current = false; discard();}, []);
    if (!enabled) return compact ? h('div',{className:'onair-host-mic compact-mic'},h('button',{type:'button',className:'onair-mic-pill',disabled:true,title:'Microphone unavailable'},'🎙 Mic off')) : null;
    function pointerDown(e) {
      if (e.button !== 0) return;
      e.preventDefault();
      if (status.current === 'listening' || status.current === 'requesting') {gesture.current = 'end'; finish(); return;}
      gesture.current = Date.now(); begin();
      if (e.currentTarget.setPointerCapture) e.currentTarget.setPointerCapture(e.pointerId);
    }
    function pointerUp() {
      if (typeof gesture.current === 'number') {
        if (Date.now() - gesture.current >= 250) finish();
        else if (status.current === 'listening' || status.current === 'requesting') setToggle(true);
      }
      gesture.current = null;
    }
    const openControl = openEnabled && h(OpenMic, {data, online, enabled:phase === 'idle' || openActive, onStart, onActive:setOpenActive, onStatus:onOpenStatus, onDisplayStatus, compact, cutOnTalk});
    const modeControl = h('fieldset', {disabled:openActive || !releaseMode || releaseMode.locked || modeSaving || phase !== 'idle'},
        h('legend', null, 'After recording'),
        h('label', null, h('input', {type:'radio', name:modeName, checked:!releaseMode || releaseMode.mode === 'review', onChange:()=>chooseMode('review')}),
          ' Review before sending', releaseMode && releaseMode.locked ? ' — required' : '', h('span', {className:'onair-help'}, ' — Check and edit the transcript before you send it.')),
        h('label', null, h('input', {type:'radio', name:modeName, checked:!!releaseMode && releaseMode.mode === 'send', onChange:()=>chooseMode('send')}),
          ' Send as soon as I release', releaseMode && releaseMode.locked ? ' — unavailable' : '', h('span', {className:'onair-help'}, ' — The transcript goes to the co-host without your review.')));
    const modeHelp = h(React.Fragment, null, releaseMode && releaseMode.locked && h('p', {className:'onair-help'}, releaseMode.reason || 'Review is required by settings.'),
      h('p', {className:'onair-help'}, openEnabled ? 'Hold to talk records a separate clip.' : 'Push-to-talk only: this is not hands-free or always-listening.'));
    const holdControl = h('button', {type:'button', onPointerDown:pointerDown, onPointerUp:pointerUp,
        onPointerCancel:()=>discard('Recording cancelled; clip discarded.'),
        onClick:e=>{if (e.detail === 0) {if (status.current === 'listening' || status.current === 'requesting') finish(); else begin(true);}},
        disabled:openActive || phase === 'transcribing' || modeSaving || !releaseMode || !online || !data || data.privacy !== false,
        'aria-pressed':phase === 'listening',
        style:phase === 'listening' ? {background:'#b91c1c', color:'white'} : {}},
        phase === 'listening' ? '● Listening ' + seconds + 's — ' + (toggle ? 'click again to transcribe' : 'release to transcribe') : phase === 'requesting' ? 'Requesting microphone…' : phase === 'transcribing' ? 'Transcribing…' : 'Hold to talk');
    const holdHelp = h('p', null, 'Hold this button or use V with Talk focused or keyboard shortcuts enabled, then release to transcribe. Or click once to start and again to transcribe.');
    if (compact) return h('div', {className:'onair-host-mic compact-mic'},
      openControl || holdControl,
      h('details',{className:'onair-mic-more',onToggle:sizeMore},h('summary',{'aria-label':'More microphone options'},'⋯'),
        h('div',{className:'onair-mic-popover'},modeControl, openEnabled && holdControl, moreControls, openEnabled && h('p',{className:'onair-help'},'Open mic sends each detected transcript directly. Review below applies to hold-to-talk; replies still follow the guard and show mode.'), modeHelp, holdHelp)));
    return h('div', {className:'onair-host-mic'}, openControl, modeControl, modeHelp, holdControl, holdHelp, h('p', {role:'status'}, message));
  };
})();
