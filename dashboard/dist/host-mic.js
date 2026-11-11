/* Push-to-talk desk component. No microphone is kept between clips. */
(function () {
  'use strict';
  const SDK = window.__HERMES_PLUGIN_SDK__, React = SDK.React, h = React.createElement;
  const {useState, useEffect, useRef} = React;
  const BASE = '/api/plugins/on-air/';

  window.OnAirHostMic = function HostMic({data, online, onReview, onStart, draftPresent}) {
    const [enabled, setEnabled] = useState(false);
    const [cutOnTalk, setCutOnTalk] = useState(true);
    const [releaseMode, setReleaseMode] = useState(null);
    const [modeSaving, setModeSaving] = useState(false);
    const modeName = React.useId();
    const modeLock = useRef(false);
    const [phase, setPhase] = useState('idle');
    const [message, setMessage] = useState('');
    const [seconds, setSeconds] = useState(0);
    const [toggle, setToggle] = useState(false);
    const current = useRef({data, online}); current.current = {data, online};
    const active = useRef(null), status = useRef('idle'), generation = useRef(0), alive = useRef(true), gesture = useRef(null);
    const change = value => {status.current = value; if (alive.current) setPhase(value);};
    const say = value => {if (alive.current) setMessage(value);};
    useEffect(() => {
      let live = true;
      SDK.fetchJSON(BASE + 'host/mic').then(r => {if (live) {setEnabled(r && r.enabled === true); setCutOnTalk(!r || r.cut_in_on_talk !== false);}}).catch(() => {});
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
      if (!enabled || !releaseMode || modeLock.current || status.current !== 'idle') return;
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
            body:JSON.stringify({epoch:item.epoch, boot:item.boot})});
          if (token !== generation.current) return;
          if (!result || result.ok !== true || !result.state || result.state.epoch !== item.epoch + 1 ||
              result.state.voice_boot !== item.boot || result.state.privacy !== false || result.state.live !== true ||
              !['approval','autonomous'].includes(result.state.mode)) {
            discard(result && (result.message || result.error) || 'Cut was refused; recording not started.'); return;
          }
          // This epoch advance is safe only because this cut was requested by this press.
          item.epoch = result.state.epoch; item.preCutData = snapshot.data; item.cutRequested = true;
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
      function down(e) {
        if (e.key.toLowerCase() !== 'v' || e.repeat || e.ctrlKey || e.metaKey || e.altKey ||
            /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable) return;
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
        if ((e.key === 'Escape' || e.key.toLowerCase() === 'c' || (current.current.data && !current.current.data.privacy)) &&
            (status.current === 'listening' || status.current === 'requesting')) discard(e.key.toLowerCase() === 'p' ? 'Privacy requested; clip discarded.' : 'Safety control requested; clip discarded.');
      }
      window.addEventListener('keydown', down); window.addEventListener('keyup', up); window.addEventListener('blur', blur);
      document.addEventListener('visibilitychange', visibility);
      window.addEventListener('click', safetyClick, true); window.addEventListener('keydown', safetyKey, true);
      return () => {window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); window.removeEventListener('blur', blur);
        document.removeEventListener('visibilitychange', visibility);
        window.removeEventListener('click', safetyClick, true); window.removeEventListener('keydown', safetyKey, true);};
    }, [enabled, cutOnTalk, releaseMode]);
    useEffect(() => () => {alive.current = false; discard();}, []);
    if (!enabled) return null;
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
    return h('div', {className:'onair-host-mic'},
      h('fieldset', {disabled:!releaseMode || releaseMode.locked || modeSaving || phase !== 'idle'},
        h('legend', null, 'After recording'),
        h('label', null, h('input', {type:'radio', name:modeName, checked:!releaseMode || releaseMode.mode === 'review', onChange:()=>chooseMode('review')}),
          ' Review before sending', releaseMode && releaseMode.locked ? ' — required' : '', h('span', {className:'onair-help'}, ' — Check and edit the transcript before you send it.')),
        h('label', null, h('input', {type:'radio', name:modeName, checked:!!releaseMode && releaseMode.mode === 'send', onChange:()=>chooseMode('send')}),
          ' Send as soon as I release', releaseMode && releaseMode.locked ? ' — unavailable' : '', h('span', {className:'onair-help'}, ' — The transcript goes to the co-host without your review.'))),
      releaseMode && releaseMode.locked && h('p', {className:'onair-help'}, releaseMode.reason || 'Review is required by settings.'),
      h('p', {className:'onair-help'}, 'Push-to-talk only: this is not hands-free or always-listening.'),
      h('button', {type:'button', onPointerDown:pointerDown, onPointerUp:pointerUp,
        onPointerCancel:()=>discard('Recording cancelled; clip discarded.'),
        onClick:e=>{if (e.detail === 0) {if (status.current === 'listening' || status.current === 'requesting') finish(); else begin(true);}},
        disabled:phase === 'transcribing' || modeSaving || !releaseMode || !online || !data || data.privacy !== false,
        'aria-pressed':phase === 'listening',
        style:phase === 'listening' ? {background:'#b91c1c', color:'white'} : {}},
        phase === 'listening' ? '● Listening ' + seconds + 's — ' + (toggle ? 'click again to transcribe' : 'release to transcribe') : phase === 'requesting' ? 'Requesting microphone…' : phase === 'transcribing' ? 'Transcribing…' : 'Hold to talk'),
      h('p', null, 'Hold this button or V while not typing, then release to transcribe. Or click once to start and again to transcribe.'),
      h('p', {role:'status'}, message));
  };
})();
