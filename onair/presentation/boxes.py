"""Bounded three-slot composition in the plugin's own OBS scene.

Only known inputs are placed; geometry and enabled state are always read back.
A window selection remains governed by the existing process-local owner confirmation.
"""
from __future__ import annotations

import itertools
import math
from typing import Any

from .obs_control import BROWSER_KIND, ObsControl, ObsRequestError, _canvas_dims, _same_value, _scene_page, _overlay_collision_message
from .obs_protocol import ObsError
from .layout import transform
from . import ownership

SCENE = 'On Air: Boxes'
SLOTS = ('main', 'sub1', 'sub2')
SOURCES = ('scene', 'slides', 'window')
ARRANGEMENTS = ('stack', 'side')
CORNERS = ('bottom-right', 'top-right', 'top-left', 'bottom-left')


def geometry(arrangement: str, width: int, height: int, slots: dict, inset_corner: str = 'bottom-right') -> dict:
    if arrangement not in ARRANGEMENTS or inset_corner not in CORNERS or width < 8 or height < 8:
        raise ValueError('invalid arrangement, corner or canvas')
    result = {'main': {'x': 0, 'y': 0, 'w': width, 'h': height}}
    if slots['sub1'] == slots['sub2'] == 'empty':
        return result
    margin = max(1, int(width * .025))
    col = int(width * .27)
    if arrangement == 'stack':
        # Reserve the slide surface's bottom 250/1080 caption band. The insets
        # are 16:9 bounds, not full-height columns that can mask slide headings.
        col = max(1, int(width * .20))
        box_height = max(1, min(int(col * 9 / 16), (int(height * 830 / 1080) - 3 * margin) // 2))
        x = margin if inset_corner.endswith('left') else width - margin - col
        for index, slot in enumerate(('sub1', 'sub2')):
            if slots[slot] != 'empty':
                y = (margin + index * (box_height + margin) if inset_corner.startswith('top') else
                     int(height * 830 / 1080) - margin - box_height - index * (box_height + margin))
                result[slot] = {'x': x, 'y': y, 'w': col, 'h': box_height}
    else:
        result['main'] = {'x': margin, 'y': margin, 'w': width - col - 3 * margin, 'h': height - 2 * margin}
        box_height = (height - 3 * margin) // 2
        for index, slot in enumerate(('sub1', 'sub2')):
            if slots[slot] != 'empty':
                result[slot] = {'x': width - margin - col, 'y': margin + index * (box_height + margin),
                                'w': col, 'h': box_height}
    return result


def _names(ctl: ObsControl) -> dict:
    c = ctl.cfg
    return {'scene': c.scene_view_input, 'slides': c.presentation_input, 'window': c.window_input, 'overlay': c.overlay_input}


def _identity_error(ctl: ObsControl) -> str | None:
    names = _names(ctl)
    if len(set(names.values())) != len(names) or SCENE in (ctl.cfg.presentation_scene, ctl.cfg.window_scene, ctl.cfg.scene_view_scene):
        return 'Boxes names collide with another configured source or scene; nothing changed.'
    return None


def _validate(slots: dict, arrangement: Any) -> str | None:
    if set(slots) != set(SLOTS) or any(slots[s] not in (*SOURCES, 'empty') for s in SLOTS):
        return 'Use only scene, slides, window or empty in each box.'
    if slots['main'] == 'empty':
        return 'Main can never be empty.'
    used = [v for v in slots.values() if v != 'empty']
    if len(used) != len(set(used)):
        return 'A source can appear in only one box.'
    if arrangement not in ARRANGEMENTS:
        return 'Arrangement must be stack or side.'
    return None


async def _read(ctl: ObsControl, c) -> dict:
    names = _names(ctl)
    canvas = _canvas_dims(await c.request('GetVideoSettings'))
    scenes = await ctl._scenes(c)
    present = SCENE in scenes
    items = (await c.request('GetSceneItemList', {'sceneName': SCENE})).get('sceneItems', []) if present else []
    foreign = [i.get('sourceName') for i in items if i.get('sourceName') not in names.values()]
    duplicates = len({i.get('sourceName') for i in items}) != len(items)
    roles = {}
    for i in items:
        name = i.get('sourceName')
        role = next((r for r, n in names.items() if n == name), None)
        if role is None:
            continue
        ref = {'sceneName': SCENE, 'sceneItemId': i['sceneItemId']}
        tr = (await c.request('GetSceneItemTransform', ref)).get('sceneItemTransform') or {}
        roles[role] = {'id': i['sceneItemId'], 'enabled': i.get('sceneItemEnabled') is True,
                       'index': i.get('sceneItemIndex', 0), 'transform': tr}
    inputs = await ctl._inputs(c)
    record = ownership.read(SCENE) if present else None
    observed_items = {}
    source_identity_ok = True
    for i in items:
        name = i.get('sourceName')
        source_uuid = await ctl._input_uuid(c, name)
        source_identity_ok = source_identity_ok and bool(source_uuid and i.get('sourceUuid') == source_uuid)
        observed_items[name] = {'id': i.get('sceneItemId'), 'uuid': source_uuid}
    owned = bool(record and record.get('scene_uuid') and source_identity_ok and
                 record.get('scene_uuid') == await ownership.scene_uuid(c, SCENE) and
                 not duplicates and record.get('items') == observed_items and
                 all(v.get('uuid') and isinstance(v.get('id'), int) for v in observed_items.values()))
    src = await ctl._sources(c, inputs)
    window = await ctl._window_state(c)
    available = {
        'slides': {'available': bool(src['presentation'].get('url_matches') and src['presentation'].get('audio_via_obs') is False),
                   'reason': 'Publish slides and run onair_obs_setup configure.'},
        'scene': {'available': False, 'reason': 'Enable scene_view and run onair_obs_setup configure.'},
        'window': {'available': bool(window.get('ready')),
                   'reason': window.get('next_human_action') or ' '.join(window.get('prerequisites') or []) or 'Prepare and choose a window in OBS.'},
        'empty': {'available': True, 'reason': ''},
    }
    if ctl.cfg.scene_view is True and inputs.get(names['scene']) == BROWSER_KIND:
        settings = (await c.request('GetInputSettings', {'inputName': names['scene']})).get('inputSettings', {})
        url = _scene_page(ctl.cfg.overlay_url)
        available['scene']['available'] = bool(url and settings.get('url') == url and settings.get('reroute_audio') is False
                                                and await ctl._scene_view_safe(c))
    voice_ok = bool(src['speech_audio']['ok'])
    return {'canvas': canvas, 'present': present, 'owned': owned, 'record': record if owned else None, 'items': roles, 'foreign': foreign, 'duplicates': duplicates,
            'available': available, 'window': window, 'voice_ok': voice_ok,
            'overlay_conflicts': src['speech_audio']['extra_overlay_clients'], 'program_scene': await ctl._program(c)}


def _problems(facts: dict, slots: dict, arrangement: str, inset_corner: str = 'bottom-right') -> list[str]:
    problems = []
    if not facts['present']:
        return ['Boxes scene is missing.']
    if not facts['owned']:
        return ['Boxes scene or its items are not plugin-owned; rename or remove the scene in OBS.']
    if facts['foreign'] or facts['duplicates']:
        problems.append('Boxes scene has foreign or duplicate items.')
    canvas = facts['canvas']
    if not canvas:
        return problems + ['OBS canvas is unavailable.']
    expected = geometry(arrangement, *canvas, slots, inset_corner)
    items = facts['items']
    for slot in SLOTS:
        source = slots[slot]
        if source == 'empty':
            continue
        item = items.get(source)
        if not item or not item['enabled']:
            problems.append(f'{slot}: {source} missing or hidden')
            continue
        want = transform(expected[slot])
        if any(not _same_value(item['transform'].get(k), v) for k, v in want.items()):
            problems.append(f'{slot}: {source} transform differs')
    for source in SOURCES:
        if source not in slots.values() and items.get(source, {}).get('enabled'):
            problems.append(f'{source}: unexpectedly visible')
    main_item = items.get(slots['main'])
    if main_item:
        for slot in ('sub1', 'sub2'):
            sub = items.get(slots[slot])
            if sub and sub['enabled'] and sub['index'] < main_item['index']:
                problems.append(f'{slot}: below main')
    overlay = items.get('overlay')
    full = transform({'x': 0, 'y': 0, 'w': canvas[0], 'h': canvas[1]})
    if not overlay or not overlay['enabled'] or any(not _same_value(overlay['transform'].get(k), v) for k, v in full.items()):
        problems.append('overlay: missing, hidden or wrong transform')
    elif any(i['enabled'] and i['index'] > overlay['index'] for role, i in items.items() if role != 'overlay'):
        problems.append('overlay: not on top')
    return problems


def _observed_geometry(facts: dict, slots: dict | None) -> dict | None:
    if not slots:
        return None
    out = {}
    for slot, source in slots.items():
        item = facts['items'].get(source)
        row = {'source': source, 'enabled': bool(item and item['enabled']),
               'bounds': None, 'content': None, 'z_index': item['index'] if item else None}
        if item:
            tr = item['transform']
            keys = ('positionX', 'positionY', 'boundsWidth', 'boundsHeight')
            vals = [tr.get(k) for k in keys]
            if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vals):
                x, y, w, h = vals
                row['bounds'] = dict(zip(('x', 'y', 'width', 'height'), vals))
                sw, sh = tr.get('sourceWidth'), tr.get('sourceHeight')
                if (isinstance(sw, (int, float)) and isinstance(sh, (int, float)) and
                        math.isfinite(sw) and math.isfinite(sh) and sw > 0 and sh > 0 and w > 0 and h > 0 and
                        tr.get('boundsType') == 'OBS_BOUNDS_SCALE_INNER' and
                        tr.get('boundsAlignment') == 0 and tr.get('alignment') == 5 and
                        not tr.get('rotation') and not any(tr.get(k) for k in ('cropLeft', 'cropRight', 'cropTop', 'cropBottom'))):
                    scale = min(w / sw, h / sh)
                    cw, ch = sw * scale, sh * scale
                    row['content'] = {'x': round(x + (w - cw) / 2, 2), 'y': round(y + (h - ch) / 2, 2),
                                      'width': round(cw, 2), 'height': round(ch, 2)}
        out[slot] = row
    return out


def _state(facts: dict) -> dict:
    matches = []
    if facts['canvas'] and facts['present'] and not facts['foreign'] and not facts['duplicates']:
        for main, sub1, sub2 in itertools.product(SOURCES, (*SOURCES, 'empty'), (*SOURCES, 'empty')):
            slots = dict(main=main, sub1=sub1, sub2=sub2)
            if _validate(slots, 'stack'):
                continue
            for arrangement in ARRANGEMENTS:
                for corner in CORNERS:
                    if not _problems(facts, slots, arrangement, corner):
                        matches.append((slots, arrangement, corner))
    slots = matches[0][0] if matches else None
    arrangements = sorted({m[1] for m in matches})
    corners = sorted({m[2] for m in matches})
    stored = (facts.get('record') or {}).get('layout') or {}
    arrangement = arrangements[0] if len(arrangements) == 1 else None
    arrangement_source = 'readback' if arrangement else None
    if arrangement is None and stored.get('arrangement') in arrangements:
        arrangement, arrangement_source = stored['arrangement'], 'stored'
    corner = corners[0] if len(corners) == 1 else None
    corner_source = 'readback' if corner else None
    if corner is None and stored.get('inset_corner') in corners:
        corner, corner_source = stored['inset_corner'], 'stored'
    overlay = facts['items'].get('overlay')
    topmost = bool(overlay and overlay['enabled'] and not facts['foreign'] and not facts['duplicates'] and
                   all(not i['enabled'] or i['index'] < overlay['index'] for role, i in facts['items'].items() if role != 'overlay'))
    canvas = facts['canvas']
    return {'scene': SCENE, 'present': facts['present'], 'slots': slots,
            'arrangement': arrangement, 'arrangement_source': arrangement_source,
            'inset_corner': corner, 'inset_corner_source': corner_source,
            'arrangement_ambiguous': len(arrangements) > 1,
            'canvas': {'width': canvas[0], 'height': canvas[1]} if canvas else None,
            'geometry': _observed_geometry(facts, slots), 'overlay_topmost': topmost,
            'available': facts['available'], 'program_scene': facts['program_scene'],
            'window': {k: v for k, v in facts['window'].items() if not k.startswith('_')},
            'reason': 'Boxes readback does not match a supported arrangement.' if facts['present'] and not matches else ''}


async def inspect(ctl: ObsControl) -> dict:
    if error := _identity_error(ctl):
        return {'ok': False, 'error': error}
    async def op(c):
        return {'ok': True, 'boxes': _state(await _read(ctl, c))}
    return await ctl._run(op)


async def safe_to_show(ctl: ObsControl, c) -> dict:
    if error := _identity_error(ctl):
        return {'ok': False, 'error': error}
    facts = await _read(ctl, c)
    state = _state(facts)
    if facts['overlay_conflicts']:
        instruction = _overlay_collision_message(ctl.cfg.overlay_input, facts['overlay_conflicts'])
        return {'ok': False, 'error': instruction, 'next_human_action': instruction}
    if 'window' in facts['items'] and not facts['available']['window']['available']:
        reason = facts['available']['window']['reason']
        return {'ok': False, 'error': reason, 'next_human_action': reason}
    if not state['slots'] or not facts['voice_ok']:
        return {'ok': False, 'error': state['reason'] or 'Boxes are not configured with the single voice overlay.'}
    for source in state['slots'].values():
        if source != 'empty' and not facts['available'][source]['available']:
            return {'ok': False, 'error': facts['available'][source]['reason'],
                    'next_human_action': facts['available'][source]['reason']}
    return {'ok': True}


async def apply(ctl: ObsControl, action: str, args: dict) -> dict:
    if error := _identity_error(ctl):
        return {'ok': False, 'error': error}
    async def op(c):
        facts = await _read(ctl, c)
        state = _state(facts)
        if facts['overlay_conflicts']:
            instruction = _overlay_collision_message(ctl.cfg.overlay_input, facts['overlay_conflicts'])
            return {'ok': False, 'error': instruction, 'next_human_action': instruction}
        if facts['present'] and not facts['owned']:
            return {'ok': False, 'error': (f"OBS already has a scene named {SCENE!r} that this plugin has no record of creating (made by hand, by an "
                                          "earlier version, or under another Hermes home). In OBS, remove or rename that scene, then ask "
                                          "again: it is recreated and recorded. Nothing changed.")}
        if facts['foreign'] or facts['duplicates']:
            return {'ok': False, 'error': 'Boxes scene contains foreign or duplicate items; nothing changed.'}
        # A familiar name alone is not enough to mutate an item. Existing items must
        # still resolve to the expected owned input kind/page before even hiding them.
        for role in ('scene', 'slides'):
            if role in facts['items'] and not facts['available'][role]['available']:
                return {'ok': False, 'error': f'The existing {role} item cannot be verified as the owned source; nothing changed.'}
        w = facts['window']
        if 'window' in facts['items'] and not ctl._window_spec(w.get('platform'), w.get('kind')):
            return {'ok': False, 'error': 'The existing window item is not a supported owned capture; nothing changed.'}
        if not facts['canvas']:
            return {'ok': False, 'error': 'OBS canvas unavailable; nothing changed.'}
        move = None
        default_corner = 'bottom-right' if action == 'set' else state.get('inset_corner') or 'bottom-right'
        corner = args.get('inset_corner', default_corner)
        if corner not in CORNERS:
            return {'ok': False, 'error': 'Inset corner must be bottom-right, top-right, top-left or bottom-left.'}
        if action == 'set':
            slots = {s: args.get(s) for s in SLOTS}
            arrangement = args.get('arrangement')
        else:
            if not facts['present']:
                main = next((name for name in ('scene', 'slides') if facts['available'][name]['available']), None)
                if main is None:
                    return {'ok': False, 'error': 'No Scene or Slides source is ready; configure a source first.'}
                slots, arrangement = {'main': main, 'sub1': 'empty', 'sub2': 'empty'}, 'stack'
            else:
                if not state['slots']:
                    return {'ok': False, 'error': 'Set the boxes first; current readback is unknown.'}
                slots = dict(state['slots'])
                arrangement = state['arrangement'] or 'stack'
            if action == 'put':
                slot, source = args.get('slot'), args.get('source')
                if slot not in SLOTS or source not in (*SOURCES, 'empty'):
                    return {'ok': False, 'error': 'Unknown slot or source.'}
                for s in SLOTS:
                    if source != 'empty' and slots[s] == source and s != slot:
                        move = {'source': source, 'from': s, 'to': slot}
                        slots[s] = 'empty'
                slots[slot] = source
            elif action == 'swap':
                a, b = args.get('a'), args.get('b')
                if a not in SLOTS or b not in SLOTS or a == b:
                    return {'ok': False, 'error': 'Choose two different boxes.'}
                slots[a], slots[b] = slots[b], slots[a]
            elif action == 'arrangement':
                arrangement = args.get('arrangement')
            else:
                return {'ok': False, 'error': 'Unknown boxes action.'}
        error = _validate(slots, arrangement)
        if error:
            return {'ok': False, 'error': error}
        if not facts['voice_ok']:
            return {'ok': False, 'error': 'Configure exactly one captions and voice overlay first.'}
        for source in slots.values():
            if source != 'empty' and not facts['available'][source]['available']:
                reason = facts['available'][source]['reason']
                return {'ok': False, 'error': reason, 'next_human_action': reason}
        names = _names(ctl)
        sent = []
        error = None
        next_human_action = None
        try:
            record = ownership.read(SCENE)
            if not facts['present']:
                created = await c.request('CreateScene', {'sceneName': SCENE})
                scene_id = created.get('sceneUuid')
                if not scene_id or scene_id != await ownership.scene_uuid(c, SCENE):
                    return {'ok': False, 'error': 'OBS did not confirm the new Boxes scene identity; rename or remove it before retrying.'}
                record = {'scene_uuid': scene_id, 'items': {}}
                ownership.write(SCENE, record)
                sent.append('created Boxes scene')
            items = facts['items']
            for role in (*SOURCES, 'overlay'):
                if role not in items and (role == 'overlay' or role in slots.values()):
                    source_uuid = await ctl._input_uuid(c, names[role])
                    created = await c.request('CreateSceneItem', {'sceneName': SCENE, 'sourceName': names[role], 'sceneItemEnabled': False})
                    if not source_uuid or not isinstance(created.get('sceneItemId'), int):
                        return {'ok': False, 'error': 'OBS did not confirm the new item identity; remove the Boxes scene before retrying.'}
                    record['items'][names[role]] = {'id': created['sceneItemId'], 'uuid': source_uuid}
                    ownership.write(SCENE, record)
                    sent.append(f'created {role} item hidden')
            facts = await _read(ctl, c)
            if not facts['owned']:
                return {'ok': False, 'error': 'Boxes identities changed during setup; remove the Boxes scene before retrying.'}
            items = facts['items']
            # Keep a previously shown capture hidden while the layout is changing.
            # Recheck its owner confirmation immediately before showing it again.
            if items.get('window', {}).get('enabled'):
                await c.request('SetSceneItemEnabled', {'sceneName': SCENE, 'sceneItemId': items['window']['id'], 'sceneItemEnabled': False})
                sent.append('hid window during layout')
            for role in SOURCES:
                if role in items and role not in slots.values():
                    await c.request('SetSceneItemEnabled', {'sceneName': SCENE, 'sceneItemId': items[role]['id'], 'sceneItemEnabled': False})
                    sent.append(f'hid {role}')
            geo = geometry(arrangement, *facts['canvas'], slots, corner)
            # Expand an incoming main behind the old main, then shrink the old main.
            # OBS v5 requests here are sequential, not an atomic batch.
            if state['slots'] and state['slots']['main'] != slots['main']:
                await c.request('SetSceneItemIndex', {'sceneName': SCENE, 'sceneItemId': items[slots['main']]['id'], 'sceneItemIndex': 0})
                sent.append('placed incoming main below outgoing')
            for slot, box in geo.items():
                role = slots[slot]
                ref = {'sceneName': SCENE, 'sceneItemId': items[role]['id']}
                await c.request('SetSceneItemTransform', {**ref, 'sceneItemTransform': transform(box)})
                sent.append(f'set {slot} bounds')
            ref = {'sceneName': SCENE, 'sceneItemId': items['overlay']['id']}
            await c.request('SetSceneItemTransform', {**ref, 'sceneItemTransform': transform({'x': 0, 'y': 0, 'w': facts['canvas'][0], 'h': facts['canvas'][1]})})
            # Main is below both insets; the one overlay stays above everything.
            for slot in SLOTS:
                role = slots[slot]
                if role != 'empty':
                    await c.request('SetSceneItemIndex', {'sceneName': SCENE, 'sceneItemId': items[role]['id'], 'sceneItemIndex': len(items) - 1})
            await c.request('SetSceneItemIndex', {**ref, 'sceneItemIndex': len(items) - 1})
            await c.request('SetSceneItemEnabled', {**ref, 'sceneItemEnabled': True})
            sent.append('positioned overlay')
            if 'window' in slots.values():
                latest = await ctl._window_state(c)
                if not latest.get('ready'):
                    next_human_action = latest.get('next_human_action') or ' '.join(latest.get('prerequisites') or [])
                    error = 'Window readiness changed during the layout; the window was not shown.'
            if not error:
                for slot in SLOTS:
                    role = slots[slot]
                    if role != 'empty':
                        await c.request('SetSceneItemEnabled', {'sceneName': SCENE, 'sceneItemId': items[role]['id'], 'sceneItemEnabled': True})
                        sent.append(f'showed {slot}')
        except ObsRequestError as exc:
            error = f'OBS refused a boxes request (code {exc.code}); some changes may have been applied.'
        except (ObsError, OSError, ValueError):
            error = 'OBS disconnected during the boxes action; some changes may have been applied.'
        try:
            after = await _read(ctl, c)
        except (ObsError, OSError, ValueError) as exc:
            return {'ok': False, 'applied': False, 'connected': isinstance(exc, ObsRequestError),
                    'requested': {'slots': slots, 'arrangement': arrangement, 'inset_corner': corner},
                    'requests_accepted': sent,
                    'problems': [f'{slot}: readback unavailable' for slot in SLOTS] + ['overlay: readback unavailable'],
                    'error': error or 'OBS readback is unavailable; some changes may have been applied.'}
        if ('window' in slots.values() and
                (not after['available']['window']['available'] or
                 (after['window'].get('_fingerprint') is not None and
                  after['window']['_fingerprint'] != ctl._confirmed))):
            error = 'Window authorization or readiness changed during the layout; attempting to hide the window.'
            next_human_action = after['available']['window']['reason']
            item = after['items'].get('window')
            if item:
                try:
                    await c.request('SetSceneItemEnabled', {'sceneName': SCENE, 'sceneItemId': item['id'], 'sceneItemEnabled': False})
                    after = await _read(ctl, c)
                    if after['items'].get('window', {}).get('enabled'):
                        error += ' OBS did not confirm it hidden; hide it in OBS.'
                except (ObsError, OSError, ValueError):
                    error += ' OBS could not confirm it hidden; hide it in OBS.'
        problems = _problems(after, slots, arrangement, corner)
        ok = not error and not problems
        if ok:
            record = after['record']
            record['layout'] = {'arrangement': arrangement, 'inset_corner': corner}
            try:
                ownership.write(SCENE, record)
            except OSError:
                ok = False
                error = 'OBS layout matched, but the private layout choice could not be stored.'
            after['record'] = record if ok else None
        labels = {'main': 'Main', 'sub1': 'Sub 1', 'sub2': 'Sub 2'}
        message = (f"{move['source'].capitalize()} moved from {labels[move['from']]} to {labels[move['to']]}; "
                   f"{labels[move['from']]} is now empty.") if move and ok else None
        return {'ok': ok, 'applied': ok, 'boxes': _state(after),
                'requested': {'slots': slots, 'arrangement': arrangement, 'inset_corner': corner},
                'requests_accepted': sent, 'problems': problems,
                **({'error': error or 'OBS readback differs from the requested boxes.'} if not ok else {}),
                **({'next_human_action': next_human_action} if next_human_action else {}),
                **({'move': move, 'message': message} if message else {})}
    return await ctl._run(op)
