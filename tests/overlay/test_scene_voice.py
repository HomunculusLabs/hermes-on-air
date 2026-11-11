import json
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest


def test_pcm_envelope_generated_formats(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    source = Path(__file__).parents[2] / 'onair/scene_starter/events.js'
    (tmp_path / 'events.mjs').write_text(source.read_text()
                                         .replace("from './avatar_logic.js'", "from './avatar_logic.mjs'"))
    (tmp_path / 'avatar_logic.mjs').write_text(
        (Path(__file__).parents[2] / 'onair/scene_starter/avatar_logic.js').read_text())
    rate = 8000
    for bits, channels, floating, extensible in [(16, 1, False, False), (16, 2, False, False),
                                                  (8, 1, False, False), (24, 1, False, False),
                                                  (32, 1, False, False), (32, 1, True, False),
                                                  (16, 2, False, True), (32, 1, True, True),
                                                  (32, 1, False, True)]:
        samples = []
        for i in range(rate):
            value = .7 * math.sin(2 * math.pi * 440 * i / rate) if i < 2400 or i >= 5600 else 0
            for channel in range(channels):
                v = value * (1 if channel == 0 else .4)
                if floating:
                    sample = struct.pack('<f', v)
                elif bits == 8:
                    sample = bytes([round(128 + v * 127)])
                else:
                    sample = round(v * (2 ** (bits - 1) - 1)).to_bytes(bits // 8, 'little', signed=True)
                samples.append(sample)
        data = b''.join(samples)
        tag = 3 if floating else 1
        fmt = struct.pack('<HHIIHH', 0xfffe if extensible else tag, channels, rate, rate * channels * bits // 8, channels * bits // 8, bits)
        if extensible:
            fmt += struct.pack('<HHI', 22, bits, 0) + struct.pack('<IHH8s', tag, 0, 0x10, bytes.fromhex('800000aa00389b71'))
        chunks = b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(data)) + data
        (tmp_path / f'{bits}-{channels}-{int(floating)}-{int(extensible)}.wav').write_bytes(b'RIFF' + struct.pack('<I', len(chunks) + 4) + b'WAVE' + chunks)
    padded_pcm = bytearray((tmp_path / '32-1-0-1.wav').read_bytes())
    struct.pack_into('<H', padded_pcm, 38, 24)  # 24 valid bits in a 32-bit container
    (tmp_path / '24-in-32-extensible.wav').write_bytes(padded_pcm)
    script = r'''
import {decodeVoice, connectEvents} from './events.mjs';
import fs from 'node:fs';
for (const name of fs.readdirSync('.').filter(x => x.endsWith('.wav'))) {
  const b = fs.readFileSync(name), v = decodeVoice(b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength));
  if (!v || v.levels.length !== 50 || v.levels[5] < .6 || v.levels[23] > .05 || v.levels[42] < .6) throw Error(name + ' bad envelope ' + JSON.stringify(v && [...v.levels]));
  if (decodeVoice(b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength-10))) throw Error('truncated accepted');
}
if (decodeVoice(new Uint8Array([1,2,3]).buffer)) throw Error('garbage accepted');
globalThis.document = {body:{dataset:{}}};
globalThis.EventSource = class { close() {} };
let clock = 100;
globalThis.performance = {now: () => clock};
const character = {voice: null, setSpeaking(v) { this.speaking = v; }, setVoice(...v) { this.voice = v; }};
const events = connectEvents(character, {voiceOffsetMs: 9000});
const emit = ev => events.onmessage({data: JSON.stringify(ev)});
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const response = bytes => ({ok: true, headers: {get: () => String(bytes.byteLength)}, arrayBuffer: async () => bytes});
const wav = fs.readFileSync('16-1-0-0.wav');
const valid = wav.buffer.slice(wav.byteOffset, wav.byteOffset + wav.byteLength);
const bad = new Uint8Array([1,2,3]).buffer;
emit({type:'state', live:true, mode:'approval', privacy:false, muted:false});
emit({type:'caption', id:'bad', ttl_ms:5000});
globalThis.fetch = async () => response(bad);
emit({type:'audio', caption_id:'bad', url:'/audio/a.wav'});
await settle();
if (!character.speaking || character.voice[0] !== null) throw Error('invalid WAV did not retain fallback');
emit({type:'caption', id:'valid', ttl_ms:5000});
globalThis.fetch = async () => { clock = 900; return response(valid); };
emit({type:'audio', caption_id:'valid', url:'/audio/a.wav'});
await settle();
if (!character.voice[0] || character.voice[1] !== 2600) throw Error('offset or fetch cap incorrect');
let release;
globalThis.fetch = () => new Promise(resolve => { release = () => resolve(response(valid)); });
emit({type:'caption', id:'pending', ttl_ms:5000});
emit({type:'audio', caption_id:'pending', url:'/audio/a.wav'});
emit({type:'state', live:false, mode:'off_air', privacy:false, muted:false});
release(); await settle();
if (character.speaking || character.voice[0] !== null) throw Error('stale fetch reopened mouth');
events.close();
'''
    result = subprocess.run([node, '--input-type=module', '-e', script], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_speech_envelope_uses_voiced_dynamics_and_closes(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    root = Path(__file__).parents[2] / 'onair/scene_starter'
    (tmp_path / 'events.mjs').write_text((root / 'events.js').read_text().replace("from './avatar_logic.js'", "from './avatar_logic.mjs'"))
    (tmp_path / 'avatar_logic.mjs').write_text((root / 'avatar_logic.js').read_text())
    rate = 8000
    values = []
    # Syllables have a low RMS compared with a brief isolated peak.
    for i in range(rate * 3):
        t = i / rate
        amp = ([.035, .065, .10, .15][int(t * 4) % 4]
               if t % 1 < .65 else 0)
        value = amp * math.sin(2 * math.pi * 190 * t)
        if i == rate // 10:
            value = .95
        values.append(struct.pack('<h', round(value * 32767)))
    with wave.open(str(tmp_path / 'speech.wav'), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(b''.join(values))
    script = r'''
import {decodeVoice} from './events.mjs'; import fs from 'node:fs';
const b=fs.readFileSync('speech.wav');
const v=decodeVoice(b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength));
const voiced=[...v.levels].filter((x,i)=>i%50<30);
const pauses=[...v.levels].filter((x,i)=>i%50>=43);
if (Math.max(...voiced)<.8 || Math.min(...voiced)>.15 || Math.max(...pauses)>.08)
  throw Error('speech dynamics or closure failed: '+JSON.stringify({voiced,pauses}));
'''
    result = subprocess.run([node, '--input-type=module', '-e', script], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
