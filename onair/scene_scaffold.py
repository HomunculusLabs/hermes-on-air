"""Create an owner-controlled scene from pinned, verified upstream packages."""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

PACKAGES = (
    {'url': 'https://registry.npmjs.org/three/-/three-0.187.0.tgz',
     'sha256': 'ff540832ded0cfe919764d24a3d88f3e2a05f23303f876423e4f893841e90728',
     'files': {
         'package/LICENSE': 'three/LICENSE.txt',
         'package/build/three.module.js': 'three/build/three.module.js',
         'package/build/three.core.js': 'three/build/three.core.js',
         'package/examples/jsm/loaders/GLTFLoader.js': 'three/examples/jsm/loaders/GLTFLoader.js',
         'package/examples/jsm/utils/BufferGeometryUtils.js': 'three/examples/jsm/utils/BufferGeometryUtils.js',
         'package/examples/jsm/utils/SkeletonUtils.js': 'three/examples/jsm/utils/SkeletonUtils.js',
     }},
    {'url': 'https://registry.npmjs.org/@pixiv/three-vrm/-/three-vrm-3.5.5.tgz',
     'sha256': '6f0102f987bc8abc9b9e78ef5b3259ea9f0dc51e30bf51d32aea6218394ea755',
     'files': {'package/lib/three-vrm.module.js': 'three-vrm.module.js',
               'package/LICENSE': 'three-vrm-LICENSE.txt'}},
)


class SceneError(Exception):
    pass


def _download(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read(12 * 1024 * 1024 + 1)
        if len(data) > 12 * 1024 * 1024:
            raise SceneError('Download too large; no scene was changed.')
        return data
    except (OSError, urllib.error.URLError) as exc:
        raise SceneError('Scene download failed. Check your network and retry; no scene was changed.') from exc


def scaffold(dest: Path, *, force: bool = False, home: Path | None = None) -> None:
    from .scene_paths import check_components, scene_parent
    dest = Path(os.path.abspath(dest))
    anchor = Path(os.path.abspath(home)) if home is not None else Path(dest.anchor)
    try:
        parts = dest.relative_to(anchor).parts
        check_components(anchor, parts, missing=True)
    except (OSError, ValueError) as exc:
        raise SceneError('Scene path contains a symlink or is outside the home; no scene was changed.') from exc
    if dest.is_symlink() or (dest.exists() and (not force or not dest.is_dir())):
        raise SceneError('Scene already exists; use --force to replace it after backing up your files.')
    # Verify every archive before creating or changing the owner's scene.
    archives = []
    for spec in PACKAGES:
        data = _download(spec['url'])
        if hashlib.sha256(data).hexdigest() != spec['sha256']:
            raise SceneError('Scene dependency digest mismatch; no scene was changed.')
        archives.append(data)
    try:
        with scene_parent(anchor, parts[:-1]) as parent:
            dest = parent / parts[-1]
            with tempfile.TemporaryDirectory(prefix='.onair-scene-', dir=parent) as tmp:
                try:
                    check_components(anchor, parts, missing=True)
                except OSError as exc:
                    raise SceneError('Scene path contains a symlink; no scene was changed.') from exc
                root = Path(tmp) / 'scene'
                shutil.copytree(Path(__file__).parent / 'scene_starter', root)
                try:
                    for spec, data in zip(PACKAGES, archives):
                        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz', encoding='utf-8') as tar:
                            for source, target in spec['files'].items():
                                member = tar.getmember(source)
                                if not member.isfile() or member.size > 10 * 1024 * 1024:
                                    raise SceneError('Invalid scene dependency archive.')
                                stream = tar.extractfile(member)
                                if stream is None:
                                    raise SceneError('Invalid scene dependency archive.')
                                content = stream.read().decode('utf-8')
                                if target.startswith('three/examples/'):
                                    content = content.replace("from 'three'", "from '../../../build/three.module.js'")
                                elif target == 'three-vrm.module.js':
                                    content = content.replace("from 'three'", "from './three/build/three.module.js'")
                                    content = content.replace('from "three"', 'from "./three/build/three.module.js"')
                                path = root / 'vendor' / target
                                path.parent.mkdir(parents=True, exist_ok=True)
                                path.write_text(content, encoding='utf-8')
                except (tarfile.TarError, KeyError, UnicodeError, OSError) as exc:
                    raise SceneError('Invalid scene dependency archive; no scene was changed.') from exc
                try:
                    check_components(anchor, parts, missing=True)
                except OSError as exc:
                    raise SceneError('Scene path contains a symlink; no scene was changed.') from exc
                backup = Path(tmp) / 'previous'
                if dest.exists():
                    if not force or dest.is_symlink():
                        raise SceneError('Scene already exists; no scene was changed.')
                    dest.rename(backup)
                try:
                    root.rename(dest)
                except BaseException:
                    if backup.exists():
                        backup.rename(dest)
                    raise
    except OSError as exc:
        raise SceneError('Scene path is unavailable or contains a symlink; no scene was changed.') from exc
