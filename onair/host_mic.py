"""Bounded, private push-to-talk processing for the authenticated desk only."""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import unicodedata
import wave
from pathlib import Path

from .scene_paths import open_scene_file, scene_parent

from fastapi.responses import JSONResponse

MAX_BYTES = 2 * 1024 * 1024
MAX_SECONDS = 30
_STT_TIMEOUT = 20
_gate = threading.Lock()
_upload_gate = threading.Lock()
_mode_gate = threading.Lock()
_MODE_FILE = 'host-mic-mode.json'


def _home():
    return Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')


def _acquire_home_upload_lock():
    """Hold a stable private lock inode for all dashboard processes of this home."""
    with scene_parent(_home(), ('plugin-data', 'on-air')) as parent:
        path = parent / '.host-mic-upload.lock'
        if not hasattr(os, 'O_NOFOLLOW') and path.is_symlink():
            raise OSError('Unsafe microphone lock')
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError('Unsafe microphone lock')
            if hasattr(os, 'fchmod'):
                os.fchmod(fd, 0o600)
            if os.name == 'posix':
                import fcntl
                try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                    return None
            elif os.name == 'nt':
                import msvcrt
                if not info.st_size: os.write(fd, b'0')
                os.lseek(fd, 0, os.SEEK_SET)
                try: msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError:
                    os.close(fd)
                    return None
            else:
                raise OSError('Microphone locking unavailable')
            return os.fdopen(fd, 'rb')
        except BaseException:
            os.close(fd)
            raise


def _saved_mode():
    try:
        with open_scene_file(_home(), ('plugin-data', 'on-air', _MODE_FILE)) as stream:
            raw = stream.read(129)
        if len(raw) > 128:
            return 'review'
        def unique(pairs):
            result = dict(pairs)
            if len(result) != len(pairs): raise ValueError('duplicate fields')
            return result
        value = json.loads(raw, object_pairs_hook=unique)
        return value['mode'] if isinstance(value, dict) and set(value) == {'mode'} and value['mode'] in ('review', 'send') else 'review'
    except (OSError, ValueError, TypeError, KeyError):
        return 'review'


def _write_mode(mode):
    with scene_parent(_home(), ('plugin-data', 'on-air')) as parent:
        fd, temp = tempfile.mkstemp(prefix='.host-mic-mode-', dir=parent)
        try:
            if hasattr(os, 'fchmod'):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'mode': mode}, stream)
            os.replace(temp, parent / _MODE_FILE)
        finally:
            try: os.unlink(temp)
            except FileNotFoundError: pass


def mode_state(config):
    # Unknown configuration is a review lock, not permission to send.
    locked = config.get_config('host_mic_review') is not False
    return {'ok': True, 'mode': 'review' if locked else _saved_mode(), 'locked': locked,
            'reason': 'Review is required by the host_mic_review setting.' if locked else ''}


def set_mode(config, mode):
    with _mode_gate:
        state = mode_state(config)
        if state['locked']:
            return None
        _write_mode(mode)
        return mode_state(config)


def _refuse(reason, message, status=200):
    return JSONResponse({'ok': False, 'reason': reason, 'message': message}, status_code=status)


def _duration(path, content_type):
    if content_type == 'audio/wav':
        with wave.open(path, 'rb') as audio:
            if audio.getcomptype() != 'NONE' or audio.getframerate() < 1:
                raise ValueError('invalid audio')
            return audio.getnframes() / audio.getframerate()
    # Limit packets as well as time and input bytes; never decode media in the dashboard.
    run = subprocess.run(['ffprobe', '-v', 'error', '-read_intervals', '%+#5000',
                          '-show_entries', 'format=format_name:stream=codec_type:packet=pts_time,duration_time',
                          '-of', 'json', path], capture_output=True, timeout=5, check=True)
    info = json.loads(run.stdout)
    if 'webm' not in info.get('format', {}).get('format_name', '').split(','):
        raise ValueError('not webm')
    if [s.get('codec_type') for s in info.get('streams', [])] != ['audio']:
        raise ValueError('not audio only')
    packets = info.get('packets', [])
    if not packets or len(packets) >= 5000:
        raise ValueError('invalid packets')
    times = [(float(p['pts_time']), float(p.get('duration_time', 0))) for p in packets]
    if any(not math.isfinite(t) or not math.isfinite(d) or d < 0 for t, d in times):
        raise ValueError('invalid timing')
    return max(t + d for t, d in times) - min(t for t, _ in times)


def _transcribe(path, timeout=_STT_TIMEOUT):
    # A separate child bounds provider runtime and prevents provider logs leaking into the desk.
    # Its temporary files share our private directory, removed by the caller even on timeout.
    code = ("import json,logging,sys,tempfile,os; "
            "tempfile.tempdir=os.path.dirname(sys.argv[1]); logging.disable(logging.CRITICAL); "
            "from tools import transcription_tools as stt; "
            "enabled=stt.is_stt_enabled(); probe=getattr(stt,'_get_provider',None); load=getattr(stt,'_load_stt_config',None); "
            "available=enabled and (not callable(probe) or not callable(load) or probe(load()) != 'none'); "
            "r=stt.transcribe_audio(sys.argv[1],source='onair') if available else {}; "
            "print(json.dumps({'success':r.get('success') is True,'transcript':r.get('transcript',''),'no_provider':not available}))")
    env = {**os.environ, 'TMPDIR': os.path.dirname(path), 'TEMP': os.path.dirname(path),
           'TMP': os.path.dirname(path), 'HERMES_DISABLE_LAZY_INSTALLS': '1'}
    # Own a process group so a timed-out provider cannot leave its decoder writing
    # into the private directory after the dashboard has returned the refusal.
    proc = subprocess.Popen([sys.executable, '-c', code, path], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env, start_new_session=(os.name == 'posix'))
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            proc.kill()
        proc.communicate()
        raise
    if proc.returncode:
        raise ValueError('transcription unavailable')
    return json.loads(output.splitlines()[-1])


def _process(audio, content_type, epoch, ident, review, forward, boot=None, timeout=_STT_TIMEOUT):
    if not _gate.acquire(blocking=False):
        return {'ok': False, 'reason': 'busy', 'message': 'Another clip is being transcribed.'}
    try:
        with tempfile.TemporaryDirectory(prefix='onair-mic-') as directory:
            path = os.path.join(directory, 'clip.wav' if content_type == 'audio/wav' else 'clip.webm')
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as file:
                file.write(audio)
            try:
                duration = _duration(path, content_type)
                if not 0 < duration <= MAX_SECONDS:
                    return {'ok': False, 'reason': 'duration', 'message': 'Clip must be at most 30 seconds.'}
            except (ValueError, OSError, EOFError, wave.Error, subprocess.SubprocessError, KeyError, TypeError):
                return {'ok': False, 'reason': 'audio', 'message': 'Invalid or unsupported audio clip.'}
            try:
                result = _transcribe(path) if timeout == _STT_TIMEOUT else _transcribe(path, timeout)
            except subprocess.TimeoutExpired:
                return {'ok': False, 'reason': 'timeout', 'message': 'Transcription timed out. Check speech-to-text in the dashboard profile with hermes tools; first use of a local model can take longer.'}
            except Exception:
                return {'ok': False, 'reason': 'transcription', 'message': 'Speech-to-text is unavailable. Run hermes tools in the dashboard profile to configure speech-to-text.'}
            if isinstance(result, dict) and result.get('no_provider') is True:
                return {'ok': False, 'reason': 'no_provider', 'message': 'Speech-to-text is disabled or no provider is available in the dashboard profile. Run hermes tools in that profile to configure it.'}
            text = result.get('transcript') if isinstance(result, dict) else None
            if not isinstance(result, dict) or not result.get('success') or not isinstance(text, str) or len(text) > 2000:
                return {'ok': False, 'reason': 'transcription', 'message': 'No transcript available. Check speech-to-text with hermes tools.'}
            text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Cc')
            if not text.strip():
                return {'ok': False, 'reason': 'transcription', 'message': 'No transcript available. Check speech-to-text with hermes tools.'}
            if review:
                return {'ok': True, 'review': True, 'transcript': text}
            body = {'text': text, 'epoch': epoch, 'boot': boot}
            if ident:
                body['id'] = ident
            _, answer = forward('POST', '/host/turn', '', json.dumps(body).encode())
            if not isinstance(answer, dict):
                return {'ok': False, 'reason': 'unavailable', 'message': 'Show unavailable.'}
            return {**answer, 'transcript': text}
    finally:
        _gate.release()


async def settings(config):
    try:
        enabled = await asyncio.to_thread(config.get_config, 'host_mic')
        review = await asyncio.to_thread(mode_state, config)
        cut = await asyncio.to_thread(config.get_config, 'host_cut_in_on_talk')
        timeout = await asyncio.to_thread(config.get_config, 'host_mic_timeout_seconds')
        timeout = timeout if type(timeout) is int and 5 <= timeout <= 120 else _STT_TIMEOUT
        return {'ok': True, 'enabled': enabled is True, 'review': review['mode'] == 'review',
                'cut_in_on_talk': cut is not False, 'timeout_seconds': timeout}
    except Exception:
        return {'ok': True, 'enabled': False, 'review': True, 'cut_in_on_talk': True, 'timeout_seconds': _STT_TIMEOUT}


async def upload(request, config, forward):
    options = await settings(config)
    if not options['enabled']:
        return _refuse('disabled', 'Host microphone is disabled.')
    # Reject ambiguity rather than choosing a first value for security bindings.
    for header in ('content-type', 'content-length', 'x-onair-epoch', 'x-onair-boot', 'x-onair-id', 'x-onair-mode'):
        if len(request.headers.getlist(header)) > 1:
            return _refuse('fields', 'Duplicate microphone headers are not accepted.', 400)
    requested_mode = request.headers.get('x-onair-mode', 'review')
    if requested_mode not in ('review', 'send'):
        return _refuse('fields', 'Invalid microphone release mode.')
    content_type = request.headers.get('content-type', '').lower()
    if content_type not in ('audio/wav', 'audio/webm'):
        return _refuse('type', 'Use audio/wav or audio/webm.')
    epoch = request.headers.get('x-onair-epoch', '')
    ident = request.headers.get('x-onair-id', '')
    boot = request.headers.get('x-onair-boot', '')
    if not re.fullmatch(r'[0-9a-f]{32}', boot) or not re.fullmatch(r'(0|[1-9][0-9]{0,15})', epoch) or (ident and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ident)):
        return _refuse('fields', 'A valid press-time epoch and stage boot and optional id are required.')
    length = request.headers.get('content-length')
    if length is not None and (not re.fullmatch(r'(0|[1-9][0-9]{0,9})', length) or int(length) > MAX_BYTES):
        return _refuse('size', 'Clip is too large.', 413)
    if not _upload_gate.acquire(blocking=False):
        return _refuse('busy', 'Another clip is being processed.')
    home_lock = None
    try:
        try:
            home_lock = _acquire_home_upload_lock()
        except OSError:
            return _refuse('unavailable', 'Microphone upload lock is unavailable.')
        if home_lock is None:
            return _refuse('busy', 'Another clip is being processed.')
        audio = bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    if len(audio) + len(chunk) > MAX_BYTES:
                        return _refuse('size', 'Clip is too large.', 413)
                    audio.extend(chunk)
        except TimeoutError:
            return _refuse('timeout', 'Clip upload timed out.')
        if not audio:
            return _refuse('audio', 'Clip is empty.')
        async def current_binding():
            code, state = await asyncio.to_thread(forward, 'GET', '/state', '', b'')
            return (code == 200 and isinstance(state, dict) and state.get('live') is True
                    and state.get('mode') in ('approval', 'autonomous') and state.get('privacy') is False
                    and type(state.get('epoch')) is int and state['epoch'] == int(epoch)
                    and state.get('voice_boot') == boot)
        if not await current_binding():
            return _refuse('stale_state', 'Show changed or is unavailable; record a new line.')
        abandoned = threading.Event()
        def guarded_forward(method, path, query, body):
            if abandoned.is_set():
                return 409, {'ok':False, 'reason':'disconnected', 'message':'Recording was discarded.'}
            try:
                if mode_state(config)['mode'] != 'send':
                    return 409, {'ok':False, 'reason':'review_required', 'message':'Review is now required; send the transcript yourself.'}
            except Exception:
                return 409, {'ok':False, 'reason':'review_required', 'message':'Review is required; send the transcript yourself.'}
            return forward(method, path, query, body)
        worker = asyncio.create_task(asyncio.to_thread(_process, audio, content_type, int(epoch), ident,
                                                       options['review'] or requested_mode == 'review', guarded_forward, boot,
                                                       options['timeout_seconds']))
        async def watch_disconnect():
            while not worker.done():
                if await request.is_disconnected():
                    abandoned.set()
                    return
                await asyncio.sleep(.05)
        watcher = asyncio.create_task(watch_disconnect())
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError:
            abandoned.set()
            # The worker owns the private file; wait for its bounded cleanup before releasing the gate.
            await asyncio.shield(worker)
            raise
        finally:
            watcher.cancel()
            try: await watcher
            except asyncio.CancelledError: pass
        if abandoned.is_set():
            return _refuse('disconnected', 'Recording was discarded.')
        if result.get('ok') and result.get('review') and not await current_binding():
            return _refuse('stale_state', 'Show changed during transcription; record a new line.')
        return JSONResponse(result)
    finally:
        if home_lock is not None:
            home_lock.close()
        _upload_gate.release()
