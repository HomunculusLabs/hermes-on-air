"""Capture a coherent set of public web assets and its short content identity."""
from __future__ import annotations

import hashlib
from pathlib import Path


def capture(web: Path, names: tuple[str, ...]) -> tuple[str, dict[str, bytes]]:
    assets = {name: (web / name).read_bytes() for name in names}
    digest = hashlib.sha256()
    for name, data in sorted(assets.items()):
        digest.update(name.encode('utf-8') + b'\0' + len(data).to_bytes(8, 'big') + data)
    token = digest.hexdigest()[:12]
    html = assets['index.html']
    if b'<html ' not in html:
        raise ValueError('web page has no html element')
    assets['index.html'] = html.replace(b'<html ', b'<html data-build="' + token.encode('ascii') + b'" ', 1)
    return token, assets
