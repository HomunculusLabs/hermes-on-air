"""Scene paths: reject links, including links above the scene directory."""
from __future__ import annotations

import contextlib
import os
import stat
from pathlib import Path


def check_components(anchor: Path, parts: tuple[str, ...], *, missing: bool = False) -> Path:
    """Portable no-symlink check, anchored at an existing directory."""
    anchor = Path(os.path.abspath(anchor))
    current = anchor
    root = anchor.resolve(strict=True)
    if stat.S_ISLNK(anchor.lstat().st_mode) or not anchor.is_dir():
        raise OSError('Scene path contains a symlink or is not a directory.')
    for name in parts:
        if name in ('', '.', '..') or '/' in name or '\\' in name:
            raise OSError('Invalid scene path.')
        current = current / name
        try:
            info = current.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise
        if stat.S_ISLNK(info.st_mode) or not current.resolve().is_relative_to(root):
            raise OSError('Scene path contains a symlink.')
    return current


def _can_stage_under(path: Path) -> bool:
    """True only where a directory can really be created beneath a descriptor path.

    Some systems list the descriptor as a directory yet refuse to create anything under it;
    those use the portable branch instead."""
    probe = path / ('.onair-probe-' + os.urandom(6).hex())
    try:
        probe.mkdir(mode=0o700)
        probe.rmdir()
        return True
    except OSError:
        return False


@contextlib.contextmanager
def scene_parent(anchor: Path, parts: tuple[str, ...]):
    """Create/open a scene parent without following links; keep its descriptor alive."""
    use_fd = (hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY')
              and os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd)
    if use_fd:
        fd = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name in parts:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            descriptor_path = Path('/dev/fd') / str(fd)
            if _can_stage_under(descriptor_path):
                yield descriptor_path
                return
        finally:
            os.close(fd)
    parent = check_components(anchor, parts, missing=True)
    parent.mkdir(parents=True, exist_ok=True)
    check_components(anchor, parts)
    yield parent


def open_scene_file(anchor: Path, parts: tuple[str, ...]):
    """Open a regular file below anchor, using descriptor-relative no-follow when available."""
    if hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY') and os.open in os.supports_dir_fd:
        fd = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name in parts[:-1]:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0), dir_fd=fd)
            if not stat.S_ISREG(os.fstat(child).st_mode):
                os.close(child)
                raise OSError('Not a regular file.')
            return os.fdopen(child, 'rb')
        finally:
            os.close(fd)
    path = check_components(anchor, parts)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise OSError('Not a regular file.')
    stream = path.open(mode='rb')
    try:
        after = os.fstat(stream.fileno())
        check_components(anchor, parts)
        if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise OSError('Scene file changed.')
        return stream
    except BaseException:
        stream.close()
        raise
