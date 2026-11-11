import { moodForLine } from './avatar_logic.js';

// Decode locally; the overlay is the only page that plays sound.
export function decodeVoice(buffer) {
  try {
    const v = new DataView(buffer), n = v.byteLength;
    if (n < 12 || v.getUint32(0) !== 0x52494646 || v.getUint32(8) !== 0x57415645) return null;
    const end = v.getUint32(4, true) + 8;
    if (end > n || end < 12) return null;
    let fmt = 0, fmtSize = 0, data = 0, size = 0;
    for (let p = 12; p + 8 <= end;) {
      const id = v.getUint32(p), len = v.getUint32(p + 4, true), next = p + 8 + len + (len & 1);
      if (p + 8 + len > end || next > end) return null;
      if (id === 0x666d7420 && !fmt) { fmt = p + 8; fmtSize = len; }
      if (id === 0x64617461 && !data) { data = p + 8; size = len; }
      p = next;
    }
    if (!fmt || fmtSize < 16 || !data || !size) return null;
    let format = v.getUint16(fmt, true);
    const channels = v.getUint16(fmt + 2, true), rate = v.getUint32(fmt + 4, true);
    const align = v.getUint16(fmt + 12, true), bits = v.getUint16(fmt + 14, true);
    if (format === 0xfffe) {
      if (fmtSize < 40 || v.getUint16(fmt + 16, true) < 22 ||
          v.getUint32(fmt + 28, true) !== 0x00100000 || v.getUint32(fmt + 32) !== 0x800000aa ||
          v.getUint32(fmt + 36) !== 0x00389b71) return null;
      const validBits = v.getUint16(fmt + 18, true);
      format = v.getUint32(fmt + 24, true);
      // Extensible integer samples are left-aligned in their container.
      if (!validBits || validBits > bits || (format === 3 && validBits !== 32)) return null;
    }
    if (![1, 2].includes(channels) || rate < 1000 || rate > 192000 ||
        !((format === 1 && [8, 16, 24, 32].includes(bits)) || (format === 3 && bits === 32)) ||
        align !== channels * bits / 8 || size % align || size / align > rate * 120) return null;
    const frames = size / align, step = Math.max(1, Math.round(rate * .02));
    const levels = new Float32Array(Math.ceil(frames / step));
    const width = bits / 8;
    let peak = 0;
    for (let w = 0; w < levels.length; w++) {
      let sum = 0, count = 0;
      const limit = Math.min(frames, (w + 1) * step);
      for (let i = w * step; i < limit; i++) for (let c = 0; c < channels; c++) {
        const p = data + i * align + c * width;
        let x;
        if (format === 3) x = v.getFloat32(p, true);
        else if (bits === 8) x = (v.getUint8(p) - 128) / 128;
        else if (bits === 16) x = v.getInt16(p, true) / 32768;
        else if (bits === 24) x = ((v.getUint8(p) | v.getUint8(p + 1) << 8 | v.getInt8(p + 2) << 16)) / 8388608;
        else x = v.getInt32(p, true) / 2147483648;
        if (!Number.isFinite(x)) return null;
        x = Math.max(-1, Math.min(1, x)); sum += x * x; count++;
      }
      levels[w] = Math.sqrt(sum / count); peak = Math.max(peak, levels[w]);
    }
    // Robust speech levels: ignore silence, and don't let a single loud window
    // set the gain for the entire utterance. Constant-level clips remain usable.
    const voiced = Array.from(levels).filter(x => x > Math.max(.003, peak * .025)).sort((a, b) => a - b);
    const percentile = p => voiced[Math.floor((voiced.length - 1) * p)] || 0;
    const floor = Math.max(.003, Math.min(percentile(.15) * .65, peak * .08));
    const high = Math.max(floor + .001, percentile(.90) * .90);
    const range = high - floor;
    let smoothed = 0;
    const attack = 1 - Math.exp(-step / rate / .035), release = 1 - Math.exp(-step / rate / .08);
    for (let i = 0; i < levels.length; i++) {
      const normalized = Math.max(0, Math.min(1, (levels[i] - floor) / range));
      const target = Math.pow(normalized, .7);
      smoothed += (target - smoothed) * (target > smoothed ? attack : release);
      // Fade to exact closure in short word gaps instead of holding a tiny smile.
      levels[i] = Math.max(0, (smoothed - .16) / .84);
    }
    return {levels, windowMs: step * 1000 / rate, durationMs: frames * 1000 / rate};
  } catch { return null; }
}

// No audio playback: the overlay owns sound. Fail closed when state or SSE is lost.
export function connectEvents(character, config = {}, stage = {}) {
  let live = false, muted = true, speaking = false, captionId = null, generation = 0, deadline = 0, timer = 0;
  let curtain = false, closed = false, lastGlance = -Infinity, lastAlert = -Infinity, previousPresence = null;
  function presence(value) {
    document.body.dataset.curtain = String(curtain);
    document.body.dataset.presence = value;
    document.body.dataset.tally = value === 'live' ? 'on' : 'off';
    if (value === previousPresence) return;
    previousPresence = value;
    if (typeof stage.setPresence === 'function') stage.setPresence(value);
    if (typeof character.setPresence === 'function') character.setPresence(value);
  }
  function cut() {
    live = false; muted = true; curtain = true;
    // CSS hides the entire canvas synchronously, before any optional callbacks.
    document.body.dataset.curtain = 'true';
    neutral(); presence('privacy');
  }
  const requestedOffset = Number(config.voiceOffsetMs);
  const offset = Number.isFinite(requestedOffset) ? Math.max(-2000, Math.min(2000, requestedOffset)) : 0;
  function neutral() {
    generation++; speaking = false; captionId = null; deadline = 0;
    clearTimeout(timer); character.setSpeaking(false);
    if (typeof character.setMood === 'function') character.setMood(moodForLine(null));
    document.body.dataset.speaking = 'false';
  }
  function schedule(until) {
    deadline = Math.min(until, Date.now() + 120000);
    clearTimeout(timer); timer = setTimeout(neutral, Math.max(0, deadline - Date.now()));
  }
  function speak(ev) {
    generation++; captionId = ev.id; speaking = true;
    if (character.setVoice) character.setVoice(null, 0, 0);
    if (typeof character.setMood === 'function') character.setMood(moodForLine(ev.text));
    character.setSpeaking(true); document.body.dataset.speaking = 'true';
    schedule(Date.now() + Math.max(0, Number(ev.ttl_ms) || 0));
  }
  async function voice(ev) {
    if (!speaking || muted || ev.caption_id !== captionId ||
        typeof ev.url !== 'string' || !/^\/audio\/[A-Za-z0-9_-]{1,64}\.wav$/.test(ev.url)) return;
    const epoch = ++generation, started = performance.now();
    try {
      const response = await fetch(ev.url);
      const size = Number(response.headers.get('content-length'));
      if (!response.ok || !size || size > 16 * 1024 * 1024) return;
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength !== size) return;
      const envelope = decodeVoice(bytes), now = performance.now();
      if (!envelope || !live || muted || !speaking || generation !== epoch || ev.caption_id !== captionId) return;
      const start = started + Math.min(500, now - started) + offset;
      character.setVoice(envelope.levels, start, envelope.windowMs, envelope.durationMs);
      schedule(Math.max(deadline, Date.now() + Math.max(0, start + envelope.durationMs - now)));
    } catch { /* Missing or unsupported clips retain caption-timed procedural motion. */ }
  }
  cut();
  const events = new EventSource('/events');
  events.onmessage = ({data}) => {
    if (closed) return;
    let ev; try { ev = JSON.parse(data); } catch { cut(); return; }
    if (!ev || typeof ev !== 'object') { cut(); return; }
    if (ev.type === 'state') {
      if (typeof ev.live !== 'boolean' || typeof ev.privacy !== 'boolean' ||
          typeof ev.muted !== 'boolean' || !['off_air', 'paused', 'approval', 'autonomous'].includes(ev.mode)) { cut(); return; }
      if (ev.privacy) { cut(); return; }
      if (ev.live !== ['approval', 'autonomous'].includes(ev.mode)) { cut(); return; }
      live = ev.live === true && ['approval', 'autonomous'].includes(ev.mode);
      muted = !!ev.muted;
      curtain = false;
      if (curtain) live = false;
      if (!live || muted) neutral();
      presence(curtain ? 'privacy' : live ? 'live' : 'offair');
    } else if (ev.type === 'caption_clear') neutral();
    else if (live && !muted && ev.type === 'caption') speak(ev);
    else if (live && !muted && ev.type === 'audio') void voice(ev);
    else if (live && ev.type === 'chat') {
      const now = performance.now();
      if (now - lastGlance >= 4000) {
        lastGlance = now;
        if (typeof character.glance === 'function') character.glance(0.8, 0, 900);
      }
    } else if (live && ev.type === 'alert') {
      const now = performance.now();
      if (now - lastAlert >= 4000) {
        lastAlert = now;
        if (typeof stage.alert === 'function') stage.alert();
        if (typeof character.react === 'function') character.react('alert');
      }
    }
  };
  events.onerror = () => { if (!closed) cut(); };
  const close = events.close.bind(events);
  events.close = () => { if (!closed) { cut(); closed = true; close(); } };
  return events;
}
