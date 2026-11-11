"""Validation for explicitly authored, public work snippets; never reads files."""
from __future__ import annotations

import re
from typing import Any

from .deck import _CONTROL, _LEAK_SHAPES

_ANSI = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[ -/]*[0-~]')
_SECRET = re.compile(r'\b[A-Za-z_][A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|BEARER|AUTH)[A-Za-z0-9_]*\s*=|\b(?:TOKEN|SECRET|PASSWORD|API_KEY|BEARER|AUTH)\s*=', re.I)
_HEADER = re.compile(r'\bauthorization\s*:', re.I)
_EMAIL = re.compile(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', re.I)
_RULES = (('secret assignment', _SECRET), ('authorization header', _HEADER), ('email address', _EMAIL), *_LEAK_SHAPES)
KINDS = ('code', 'terminal', 'text', 'diff')


def clean(value: Any, field: str, limit: int, errors: dict, *, multiline: bool = False,
          required: bool = False, lines: int | None = None) -> str:
    if value is None or value == '':
        if required:
            errors[field] = 'line 1: required'
        return ''
    if not isinstance(value, str):
        errors[field] = 'line 1: must be text'
        return ''
    # A generous hard input bound also prevents an oversized escape stream becoming work.
    if len(value) > max(limit * 4, 24000):
        errors[field] = 'line 1: input too large'
        return ''
    value = _ANSI.sub('', value).replace('\r\n', '\n').replace('\r', '\n')
    for number, line in enumerate(value.split('\n'), 1):
        if _CONTROL.search(line):
            errors[field] = f'line {number}: control characters are not allowed'
            return ''
        for label, pattern in _RULES:
            if pattern.search(line):
                errors[field] = f'line {number}: {label} is not allowed on the public surface'
                return ''
    if not multiline:
        if '\n' in value:
            errors[field] = 'line 2: must be a single line'
            return ''
        value = ' '.join(value.split())
    if len(value) > limit:
        errors[field] = f'line 1: at most {limit} characters'
    elif lines is not None and value.count('\n') + 1 > lines:
        errors[field] = f'line {lines + 1}: at most {lines} lines'
    return value


def apply(old: dict | None, args: dict) -> tuple[dict | None, dict]:
    """Validate an entire candidate before changing the program."""
    import copy
    action = args.get('action')
    fields = {'show': ({'title', 'panes'}, {'status'}), 'update': ({'pane', 'content'}, set()),
              'append': ({'pane', 'text'}, set()), 'status': ({'status'}, set()),
              'step': ({'done', 'current', 'next'}, set())}
    if action not in fields:
        return None, {'action': 'unknown action'}
    required, optional = fields[action]
    if not required <= set(args) - {'action'} or set(args) - {'action'} - required - optional:
        return None, {'action': 'give exactly the fields for this action'}
    errors: dict = {}
    if action == 'show':
        work: dict = {'title': clean(args.get('title'), 'title', 80, errors, required=True),
                      'status': clean(args.get('status'), 'status', 80, errors), 'panes': [], 'steps': None}
        panes = args.get('panes')
        if not isinstance(panes, list) or not 1 <= len(panes) <= 2:
            errors['panes'] = 'line 1: provide one or two panes'
            panes = []
        for i, pane in enumerate(panes):
            field = f'panes[{i}]'
            if not isinstance(pane, dict) or set(pane) - {'kind', 'label', 'language', 'content'} or not {'kind', 'label', 'content'} <= set(pane):
                errors[field] = 'line 1: expected kind, label, content and optional language only'
                continue
            kind = pane['kind']
            if kind not in KINDS:
                errors[field + '.kind'] = 'line 1: expected code, terminal, text or diff'
            label = clean(pane['label'], field + '.label', 40, errors, required=True)
            lang = clean(pane.get('language'), field + '.language', 24, errors)
            content = clean(pane['content'], field + '.content', 6000, errors, multiline=True, lines=120)
            work['panes'].append({'kind': kind, 'label': label, 'language': lang, 'content': content})
    else:
        if old is None:
            return None, {'action': 'show a work view first'}
        work = copy.deepcopy(old)
        if action in ('update', 'append'):
            idx = args.get('pane')
            if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(work['panes']):
                return None, {'pane': 'line 1: no such pane'}
            pane = work['panes'][idx]
            if action == 'append' and pane['kind'] not in ('text', 'terminal'):
                return None, {'pane': 'line 1: append requires a text or terminal pane'}
            key = 'text' if action == 'append' else 'content'
            value = clean(args.get(key), key, 6000, errors, multiline=True, lines=120 if action == 'update' else None)
            if action == 'append':
                value = '\n'.join((pane['content'] + value).split('\n')[-120:])
                # Revalidate combined text: secrets may cross an append boundary.
                value = clean(value, key, 6000, errors, multiline=True, lines=120)
            pane['content'] = value
        elif action == 'status':
            work['status'] = clean(args.get('status'), 'status', 80, errors)
        else:
            done, nxt = args.get('done'), args.get('next')
            if not isinstance(done, list) or not isinstance(nxt, list) or len(done) + len(nxt) + 1 > 6:
                return None, {'step': 'line 1: at most six total items'}
            work['steps'] = {'done': [clean(v, f'done[{i}]', 60, errors, required=True) for i, v in enumerate(done)],
                             'current': clean(args.get('current'), 'current', 60, errors, required=True),
                             'next': [clean(v, f'next[{i}]', 60, errors, required=True) for i, v in enumerate(nxt)]}
    return (None, errors) if errors else (work, {})
