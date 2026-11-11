"""Private OBS identities created by the plugin, never inferred from names."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path


def _path(scene: str) -> Path:
    home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes').expanduser()
    key = hashlib.sha256(scene.encode('utf-8')).hexdigest()
    return home / 'plugin-data' / 'on-air' / 'obs-owned' / (key + '.json')


def read(scene: str) -> dict | None:
    try:
        value = json.loads(_path(scene).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def write(scene: str, record: dict) -> None:
    path = _path(scene)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.parent / ('.' + secrets.token_hex(16))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(record, stream, sort_keys=True)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


async def scene_uuid(c, scene: str) -> str | None:
    scenes = (await c.request('GetSceneList')).get('scenes', [])
    return next((s.get('sceneUuid') for s in scenes if s.get('sceneName') == scene), None)
