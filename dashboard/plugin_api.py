"""Dashboard-authenticated bridge to the local On Air control server.

The dashboard owns authentication. Discovery and all blocking socket/file work run off
its event loop; no browser-supplied host, headers, or token are forwarded.
"""
from __future__ import annotations

import asyncio
import http.client
import json
import os
import re
import stat
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
_URL = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,512}\Z")
_MAX_BYTES = 1024 * 1024


def _read_file(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('not a regular file')
        with os.fdopen(fd, 'rb') as file:
            fd = -1
            data = file.read(limit + 1)
        if len(data) > limit:
            raise ValueError('file too large')
        return data
    finally:
        if fd != -1:
            os.close(fd)


def _discovery():
    home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')
    directory = home / 'plugin-data' / 'on-air'
    info = json.loads(_read_file(directory / 'control.json', 8192))
    if not isinstance(info, dict) or info.get('token_file') != 'desk.token':
        raise ValueError('invalid discovery')
    url = info.get('url')
    match = _URL.fullmatch(url) if isinstance(url, str) else None
    pid = info.get('pid')
    if not match or int(match.group(1)) > 65535 or type(pid) is not int or pid <= 0:
        raise ValueError('invalid discovery')
    os.kill(pid, 0)
    token = _read_file(directory / 'desk.token', 1024).decode('ascii').strip()
    if not _TOKEN.fullmatch(token):
        raise ValueError('invalid token')
    overlay = info.get('overlay_url') or ''
    if not isinstance(overlay, str) or len(overlay) > 200:
        overlay = ''
    return int(match.group(1)), token, overlay


def _redact(value, token):
    if isinstance(value, str):
        return value.replace(token, '[redacted]')
    if isinstance(value, list):
        return [_redact(item, token) for item in value]
    if isinstance(value, dict):
        return {_redact(key, token): _redact(item, token) for key, item in value.items()}
    return value


def _forward(method: str, path: str, query: str, body: bytes):
    try:
        port, token, overlay_url = _discovery()
        # The assistance connection test waits for the operator-configured provider (bounded at 10 s).
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=12 if path == '/assist/test' else 3)
        try:
            headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
            if method == 'POST':
                headers['Content-Type'] = 'application/json'
            conn.request(method, path + (('?' + query) if query else ''), body if method == 'POST' else None, headers)
            response = conn.getresponse()
            raw = response.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                raise ValueError('response too large')
            payload = _redact(json.loads(raw), token)
            if path == '/state' and isinstance(payload, dict):
                payload = {**payload, 'overlay_url': overlay_url}
            # Reject non-JSON numbers and invalid Unicode before returning to ASGI.
            json.dumps(payload, allow_nan=False, ensure_ascii=False).encode('utf-8')
            return response.status, payload
        finally:
            conn.close()
    except (OSError, ValueError, TypeError, UnicodeError, http.client.HTTPException, OverflowError):
        return 503, {'ok': False, 'error': 'On Air control unavailable'}


async def _proxy(request: Request, path: str):
    body = await request.body() if request.method == 'POST' else b''
    if len(body) > _MAX_BYTES:
        return JSONResponse({'ok': False, 'error': 'request too large'}, status_code=413)
    if path == '/cut-in':
        try:
            def unique_object(pairs):
                value = dict(pairs)
                if len(value) != len(pairs):
                    raise ValueError('duplicate fields')
                return value
            fields = json.loads(body, object_pairs_hook=unique_object)
            if (not isinstance(fields, dict) or set(fields) not in ({'epoch', 'boot'}, {'epoch', 'boot', 'only_if_airing'})
                    or ('only_if_airing' in fields and fields['only_if_airing'] is not True)):
                raise ValueError('invalid fields')
            if (type(fields['epoch']) is not int or not isinstance(fields['boot'], str)
                    or not re.fullmatch(r'[0-9a-f]{32}', fields['boot'])):
                raise ValueError('invalid fields')
        except (ValueError, TypeError, UnicodeDecodeError):
            return JSONResponse({'ok': False, 'error': 'body must carry an integer epoch and stage boot id'}, status_code=400)
    query = urlencode(list(request.query_params.multi_items()))
    status, payload = await asyncio.to_thread(_forward, request.method, path, query, body)
    return JSONResponse(payload, status_code=status)


# Host mic is local to this authenticated dashboard, never a generic proxy.
@router.get('/host/mic/mode')
async def host_mic_mode_get():
    try:
        return JSONResponse(await asyncio.to_thread(_onair('host_mic').mode_state, _ViewsConfig()))
    except Exception:
        return JSONResponse({'ok':False, 'error':'Microphone mode is unavailable.'}, status_code=503)


@router.post('/host/mic/mode')
async def host_mic_mode_post(request: Request):
    try:
        if len(request.headers.getlist('content-type')) != 1 or request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise ValueError('invalid content type')
        body = bytearray()
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                if len(body) + len(chunk) > 256:
                    raise ValueError('body too large')
                body.extend(chunk)
        def unique(pairs):
            value = dict(pairs)
            if len(value) != len(pairs): raise ValueError('duplicate fields')
            return value
        data = json.loads(body, object_pairs_hook=unique)
        if not isinstance(data, dict) or set(data) != {'mode'} or data['mode'] not in ('review', 'send'):
            raise ValueError('invalid fields')
    except (ValueError, TypeError, UnicodeDecodeError, TimeoutError):
        return JSONResponse({'ok':False, 'error':'Expected one valid mode.'}, status_code=400)
    try:
        result = await asyncio.to_thread(_onair('host_mic').set_mode, _ViewsConfig(), data['mode'])
        if result is None:
            return JSONResponse({'ok':False, 'error':'Review is required by the host_mic_review setting.'}, status_code=403)
        return JSONResponse(result)
    except Exception:
        return JSONResponse({'ok':False, 'error':'Microphone mode could not be saved.'}, status_code=503)


@router.get('/host/mic')
async def host_mic_settings():
    return JSONResponse(await _onair('host_mic').settings(_ViewsConfig()))


@router.post('/host/mic')
async def host_mic_upload(request: Request):
    return await _onair('host_mic').upload(request, _ViewsConfig(), _forward)


# Fixed routes only: clients cannot turn the bearer-bearing bridge into a generic proxy.
@router.get('/state')
@router.get('/pending')
@router.get('/receipts')
@router.get('/alerts')
@router.get('/alerts/recent')
@router.get('/assist')
@router.post('/mode')
@router.post('/approve')
@router.post('/reject')
@router.post('/privacy')
@router.post('/mute')
@router.post('/stop')
@router.post('/cut-in')
@router.post('/runtime/clear')
@router.post('/host/turn')
@router.post('/alerts')
@router.post('/assist/test')
async def proxy(request: Request):
    # route.path is the local pattern even when mounted beneath a dashboard prefix.
    return await _proxy(request, request.scope['route'].path.removeprefix('/api/plugins/on-air'))


# ---- Presentation views: a separate dashboard process has no owner's confirmation memory ----
# The profile's settings and environment are read the same way as the owner tool. This is a
# separate ObsControl: it cannot confer or revoke a confirmation held in the owner process.
class _ViewsConfig:
    def get_config(self, key):
        from collections.abc import Mapping
        from hermes_cli.config import load_config_readonly
        entry = load_config_readonly()
        for part in ('plugins', 'entries', 'on-air'):
            entry = entry.get(part) if isinstance(entry, Mapping) else None
        entry = entry if isinstance(entry, Mapping) else {}
        for section in ('settings', 'config'):
            values = entry.get(section)
            if isinstance(values, dict) and key in values:
                return values[key]
        return None


def _views_stage_home():
    home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')
    return (home / '.onair-stage-home').is_file()


def _views_controller():
    agent = _onair('presentation.agent')
    settings = _ViewsConfig().get_config('presentation')
    if settings is not None and not isinstance(settings, dict):
        raise ValueError('invalid presentation settings')
    config, _ = agent._config(None, settings)
    # The presentation URL is chosen by the owner runtime and may have fallen back to an
    # ephemeral port. Do not guess it or certify an arbitrary OBS source URL as that page.
    return _onair('presentation.obs_control').ObsControl(config, lambda: '')


def _views_sanitize(payload):
    secret = _onair('secrets').get('ONAIR_OBS_PASSWORD')
    return _redact(payload, secret) if secret else payload


_views_lock = asyncio.Lock()


async def _views_run(fn):
    async with _views_lock:
        try:
            ctl = await asyncio.to_thread(_views_controller)
            return _views_sanitize(await fn(ctl))
        except Exception:  # no exception text, credentials or local settings in responses or logs
            return {'ok': False, 'connected': False, 'error': 'Presentation settings or OBS are unavailable in this dashboard.'}


@router.get('/views/state')
async def views_state():
    async def run(ctl):
        obs = await ctl.inspect()
        if not obs.get('ok'):
            return obs
        layout = _onair('presentation.layout')
        arranged = await layout.inspect(ctl)
        boxes = await layout.boxes_inspect(ctl)
        window = await ctl.window_discover()
        for part in (arranged, boxes, window):
            if not part.get('ok'):
                return {'ok': False, 'connected': part.get('connected', True),
                        'error': part.get('error') or 'OBS readback was incomplete; refresh Views.'}
        w = window.get('window') or {}
        program = obs.get('program_scene')
        window_item = (((arranged.get('scenes') or {}).get('window') or {}).get('items') or {}).get('window') or {}
        box_state = boxes.get('boxes') or {}
        box_slots = box_state.get('slots') or {}
        if (program == ctl.cfg.window_scene and window_item.get('enabled') is True) or (
                program == 'On Air: Boxes' and 'window' in box_slots.values()):
            window_state = 'showing'
        elif not w.get('exists'):
            window_state = 'none'
        elif w.get('selection') == 'operator_confirmed':
            window_state = 'confirmed'
        elif w.get('selected') and w.get('selection') == 'operator_selected_unconfirmed':
            # Another process may hold the real owner confirmation; this process cannot observe it.
            window_state = 'chosen_confirmation_unknown'
        elif w.get('ready'):
            window_state = 'ready'
        else:
            window_state = 'prepared'
        return {'ok': True, 'connected': True, 'program_scene': obs.get('program_scene'),
                'approved_scenes': obs.get('approved_scenes'), 'scenes_present': obs.get('scenes_present'),
                'stream': obs.get('stream'), 'layout': arranged, 'boxes': boxes.get('boxes'),
                'window': w, 'window_state': window_state, 'owner_runtime_shared': False,
                'stage_profile': _views_stage_home(),
                'limitation': ('This dashboard has a separate presentation runtime. The owner confirmation and '
                               'presentation page address are not shared. Ask your owner agent to use '
                               'onair_window_share and onair_layout; confirm the window in the owner session.')}
    return JSONResponse(await _views_run(run))


@router.post('/views/scene')
@router.post('/views/layout')
@router.post('/views/window')
@router.post('/views/boxes')
async def views_action(request: Request):
    body = await _json_body(request)
    kind = request.scope['route'].path.rsplit('/', 1)[1]
    if body is None:
        return JSONResponse({'ok': False, 'error': 'body must be a JSON object'}, status_code=400)
    expected = {'scene': {'scene'}, 'layout': {'preset'}, 'window': {'action'}}
    if kind == 'boxes':
        fields = {'set': {'main', 'sub1', 'sub2', 'arrangement'}, 'put': {'slot', 'source'},
                  'swap': {'a', 'b'}, 'arrangement': {'arrangement'}}
        op = body.get('op')
        if not isinstance(op, str) or op not in fields:
            return JSONResponse({'ok': False, 'error': 'Unknown boxes action.'})
        keys = {'op'} | fields[op]
        optional = {'inset_corner'} if op in ('set', 'arrangement') else set()
    else:
        keys, optional = expected[kind], set()
    if not (keys <= set(body) <= keys | optional) or any(not isinstance(v, str) or len(v) > 200 for v in body.values()):
        return JSONResponse({'ok': False, 'error': 'Invalid fields.'}, status_code=400)
    if _views_stage_home():
        return JSONResponse({'ok': False, 'error': 'This is a stage profile, not the owner presentation runtime. Ask your owner agent to use onair_layout, onair_obs_scene or onair_window_share.'})
    if kind == 'window' and body['action'] in ('prepare', 'open_chooser'):
        return JSONResponse({'ok': False, 'error': 'This separate dashboard cannot revoke the owner runtime confirmation. Ask your owner agent to use onair_window_share with action ' + body['action'] + '.'})
    async def run(ctl):
        layout = _onair('presentation.layout')
        if kind == 'scene':
            return await ctl.set_scene(body['scene'])
        if kind == 'layout':
            return await layout.apply(ctl, body['preset'])
        if kind == 'boxes':
            return await layout.boxes_apply(ctl, body['op'], body)
        actions = {'discover': ctl.window_discover, 'show': ctl.window_show, 'hide': ctl.window_hide}
        if body['action'] not in actions:
            return {'ok': False, 'error': 'Unknown window action.'}
        return await actions[body['action']]()
    return JSONResponse(await _views_run(run))


# ---- First-run setup wizard and guided practice (no real show involved) ----------------------
_ROOT = Path(__file__).resolve().parents[1]
_PKG = '_onair_desk_pkg'


def _onair(sub: str):
    """Import an On Air module from this plugin's own source, without touching sys.path."""
    import importlib
    import importlib.util
    import sys

    if _PKG not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _PKG, _ROOT / 'onair' / '__init__.py', submodule_search_locations=[str(_ROOT / 'onair')])
        module = importlib.util.module_from_spec(spec)
        sys.modules[_PKG] = module
        spec.loader.exec_module(module)
    return importlib.import_module(_PKG + '.' + sub)


_practice_lock = None
_practice = None


def _lock():
    global _practice_lock
    if _practice_lock is None:
        import threading
        _practice_lock = threading.Lock()
    return _practice_lock


async def _json_body(request: Request):
    body = await request.body()
    if len(body) > 64 * 1024:
        return None
    try:
        value = json.loads(body or b'{}')
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _desk_connected() -> bool:
    try:
        _discovery()
        return True
    except (OSError, ValueError, TypeError, UnicodeError):
        return False


@router.get('/setup/status')
async def setup_status():
    def status():
        wizard = _onair('setup_wizard')
        home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')
        return {'ok': True, 'desk_connected': _desk_connected(),
                'current_home_is_stage': (home / '.onair-stage-home').is_file(),
                'suggested_target': wizard.suggested_target(),
                'practice_running': bool(_practice and _practice.alive)}
    return JSONResponse(await asyncio.to_thread(status))


@router.post('/setup/plan')
async def setup_plan(request: Request):
    body = await _json_body(request)
    if body is None:
        return JSONResponse({'ok': False, 'error': 'body must be a JSON object'}, status_code=400)
    result = await asyncio.to_thread(_onair('setup_wizard').plan, body.get('answers') or {})
    # A refusal is an answer (ok: false + per-field errors), not a transport failure.
    return JSONResponse(result)


@router.post('/setup/apply')
async def setup_apply(request: Request):
    body = await _json_body(request)
    if body is None:
        return JSONResponse({'ok': False, 'error': 'body must be a JSON object'}, status_code=400)
    result = await asyncio.to_thread(_onair('setup_wizard').apply, body.get('answers') or {}, body.get('confirm'))
    # A refusal is an answer (ok: false + per-field errors), not a transport failure.
    return JSONResponse(result)


@router.post('/setup/readiness')
async def setup_readiness(request: Request):
    body = await _json_body(request)
    home = body.get('home') if body else None
    if not isinstance(home, str) or not home or len(home) > 4096:
        return JSONResponse({'ok': False, 'error': 'home must be a path'}, status_code=400)
    result = await asyncio.to_thread(_onair('setup_wizard').readiness, home)
    return JSONResponse(result)


# ---- Quick setup: the SAME engine the private agent's tools use (onair/setup_engine.py) --------
def _engine():
    return _onair('setup_engine').engine()


@router.get('/setup/quick/schema')
async def quick_schema():
    """The same discoverable contract the private agent's onair_setup_schema tool returns."""
    return JSONResponse(_onair('setup_engine').schema())


@router.get('/setup/quick/status')
async def quick_status():
    return JSONResponse(await asyncio.to_thread(lambda: _engine().status()))


@router.post('/setup/quick/plan')
async def quick_plan(request: Request):
    body = await _json_body(request)
    if body is None:
        return JSONResponse({'ok': False, 'error': 'body must be a JSON object'}, status_code=400)
    return JSONResponse(await asyncio.to_thread(lambda: _engine().plan(body.get('answers') or {})))


@router.post('/setup/quick/apply')
async def quick_apply(request: Request):
    """The dashboard is authenticated by the host; the click on Set up & preview is the owner's
    approval of exactly this digest. Anything else in the body is ignored."""
    body = await _json_body(request)
    if body is None:
        return JSONResponse({'ok': False, 'error': 'body must be a JSON object'}, status_code=400)
    digest = body.get('digest')
    if body.get('confirm') is not True or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        return JSONResponse({'ok': False, 'error': 'Press Set up & preview to approve this plan; nothing was changed.'})
    module = _onair('setup_engine')
    return JSONResponse(await asyncio.to_thread(
        lambda: module.engine().apply(digest, module.owner_consent(digest, 'dashboard'))))


@router.get('/setup/quick/job')
async def quick_job():
    def job():
        eng = _engine()
        return {'ok': True, 'job': _onair('setup_engine')._public_job(eng.job()), 'preview': eng.preview_facts()}
    return JSONResponse(await asyncio.to_thread(job))


@router.post('/setup/quick/cancel')
async def quick_cancel():
    def cancel():
        return _engine().cancel_all()  # ok only when the job is cancelled AND the preview stop is complete
    return JSONResponse(await asyncio.to_thread(cancel))


@router.get('/setup/quick/preview/state')
@router.get('/setup/quick/preview/pending')
@router.post('/setup/quick/preview/approve')
@router.post('/setup/quick/preview/reject')
@router.post('/setup/quick/preview/mode')
@router.post('/setup/quick/preview/privacy')
@router.post('/setup/quick/preview/stop')
async def quick_preview(request: Request):
    body = await request.body() if request.method == 'POST' else b''
    if len(body) > 64 * 1024:
        return JSONResponse({'ok': False, 'error': 'request too large'}, status_code=413)
    path = request.scope['route'].path.removeprefix('/api/plugins/on-air').removeprefix('/setup/quick/preview')
    module = _onair('setup_engine')
    status, payload = await asyncio.to_thread(module.preview_call, _engine().dir, request.method, path, body)
    return JSONResponse(payload, status_code=status)


# ---- The private agent-hosted show (onair/show_runtime.py): same engine, usually hosted by the agent process ----
@router.get('/setup/quick/show/status')
async def show_status():
    return JSONResponse(await asyncio.to_thread(lambda: _onair('show_runtime').status(_engine())))


@router.post('/setup/quick/show/plan')
async def show_plan(request: Request):
    body = await _json_body(request)
    if body is None:
        return JSONResponse({'ok': False, 'error': 'body must be a JSON object'}, status_code=400)
    return JSONResponse(await asyncio.to_thread(lambda: _onair('show_runtime').plan(_engine(), body.get('answers') or {})))


@router.post('/setup/quick/show/start')
async def show_start(request: Request):
    """The click on Start the show is the owner's approval of exactly this show digest (dashboard-authenticated)."""
    body = await _json_body(request)
    digest = body.get('digest') if body else None
    if not body or body.get('confirm') is not True or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        return JSONResponse({'ok': False, 'error': 'Press Start the show to approve this show plan; nothing was started.'})
    module, show = _onair('setup_engine'), _onair('show_runtime')
    return JSONResponse(await asyncio.to_thread(
        lambda: show.start(module.engine(), digest, module.owner_consent(digest, 'dashboard'))))


@router.post('/setup/quick/show/say')
@router.post('/setup/quick/show/stop')
@router.post('/setup/quick/show/resume')
@router.post('/setup/quick/show/end')
@router.post('/setup/quick/show/approve')
@router.post('/setup/quick/show/reject')
async def show_control(request: Request):
    body = await _json_body(request)
    if body is None:
        return JSONResponse({'ok': False, 'error': 'body must be a JSON object'}, status_code=400)
    action = request.scope['route'].path.rsplit('/', 1)[1]
    return JSONResponse(await asyncio.to_thread(lambda: _onair('show_runtime').control(_engine(), action, body)))


def _practice_facts():
    if _practice is None or not _practice.alive:
        return {'ok': True, 'running': False}
    return {'ok': True, 'running': True, 'facts': _practice.facts()}


@router.post('/practice/start')
async def practice_start():
    def start():
        global _practice
        with _lock():
            if _practice is not None:
                _practice.close()
                _practice = None
            show = _onair('practice').PracticeShow()
            show.start()
            _practice = show
        return _practice_facts()
    try:
        return JSONResponse(await asyncio.to_thread(start))
    except Exception:
        return JSONResponse({'ok': False, 'error': 'The practice show could not start.'}, status_code=503)


@router.post('/practice/end')
async def practice_end():
    def end():
        global _practice
        with _lock():
            if _practice is not None:
                _practice.close()
                _practice = None
        return {'ok': True, 'running': False}
    return JSONResponse(await asyncio.to_thread(end))


@router.post('/practice/next')
async def practice_next():
    def nxt():
        if _practice is None or not _practice.alive:
            return 409, {'ok': False, 'error': 'Start a practice show first.'}
        try:
            return 200, {'ok': True, 'message': _practice.next_message(), **_practice_facts()}
        except Exception as exc:  # PracticeError carries a safe message
            return 409, {'ok': False, 'error': str(exc) or 'no more practice messages'}
    status, payload = await asyncio.to_thread(nxt)
    return JSONResponse(payload, status_code=status)


@router.get('/practice/facts')
async def practice_facts():
    try:
        return JSONResponse(await asyncio.to_thread(_practice_facts))
    except Exception:
        return JSONResponse({'ok': False, 'error': 'practice show unavailable'}, status_code=503)


@router.get('/practice/state')
@router.get('/practice/pending')
@router.get('/practice/receipts')
@router.get('/practice/alerts')
@router.get('/practice/alerts/recent')
@router.get('/practice/assist')
@router.post('/practice/mode')
@router.post('/practice/approve')
@router.post('/practice/reject')
@router.post('/practice/privacy')
@router.post('/practice/mute')
@router.post('/practice/stop')
@router.post('/practice/alerts')
async def practice_desk(request: Request):
    body = await request.body() if request.method == 'POST' else b''
    if len(body) > _MAX_BYTES:
        return JSONResponse({'ok': False, 'error': 'request too large'}, status_code=413)
    path = request.scope['route'].path.removeprefix('/api/plugins/on-air').removeprefix('/practice')
    query = urlencode(list(request.query_params.multi_items()))

    def call():
        show = _practice
        if show is None or not show.alive:
            return 503, {'ok': False, 'error': 'No practice show is running. Press Start practice.'}
        try:
            return show.call(request.method, path, query, body)
        except Exception:
            return 503, {'ok': False, 'error': 'practice show unavailable'}
    status, payload = await asyncio.to_thread(call)
    return JSONResponse(payload, status_code=status)
