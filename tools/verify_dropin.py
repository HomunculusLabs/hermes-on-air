"""Verify a staged On Air drop-in against its refresh manifest, read-only, stdlib only (Python >= 3.9).

Exact closure: the set of regular files equals the manifest, every sha256 matches, and every file's
ACTUAL permission bits (``stat.S_IMODE``) equal the manifest's octal ``mode``. A 0664 or 0600 file
against a ``644`` entry is a mismatch, not "non-executable, close enough". Symlinks and special
files (devices, FIFOs, sockets) anywhere under the root are refused. Directories are walked, not
compared.

Extract the archive with an explicit umask in a subshell so the operator's own umask is untouched:

    (umask 0022; tar -C "$STAGING" -xf "$ARCHIVE")
    python3 -B tools/verify_dropin.py MANIFEST.json "$STAGING"

rc 0 = exact closure, 1 = mismatch (JSON report on stdout), 2 = usage or unreadable manifest.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path


def _expected_mode(entry: object) -> "int | None":
    mode = entry.get("mode") if isinstance(entry, dict) else None
    if not isinstance(mode, str) or not mode or len(mode) > 4 or any(c not in "01234567" for c in mode):
        return None
    return int(mode, 8)


def verify(manifest: dict, root: Path) -> dict:
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or not files:
        raise ValueError("manifest has no 'files' table")
    got = {}
    special = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            st = os.lstat(path)
            if stat.S_ISREG(st.st_mode):
                got[rel] = (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(st.st_mode))
            elif not stat.S_ISDIR(st.st_mode):
                special.append(rel)  # symlink, device, FIFO or socket
    want, have = set(files), set(got)
    both = sorted(want & have)
    invalid = sorted(k for k in want if _expected_mode(files[k]) is None
                     or not isinstance(files[k].get("sha256"), str))
    mode_mismatch = [{"path": k, "expected": files[k].get("mode"), "actual": format(got[k][1], "o")}
                     for k in both
                     if _expected_mode(files[k]) is not None and got[k][1] != _expected_mode(files[k])]
    res = {
        "expected": len(files),
        "found": len(got),
        "missing": sorted(want - have),
        "extra": sorted(have - want),
        "hash_mismatch": [k for k in both if got[k][0] != files[k].get("sha256")],
        "mode_mismatch": mode_mismatch,
        "links_or_special": sorted(special),
        "invalid_manifest_entries": invalid,
    }
    res["exact"] = not any(res[k] for k in ("missing", "extra", "hash_mismatch", "mode_mismatch",
                                            "links_or_special", "invalid_manifest_entries"))
    return res


def main(argv: list) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(argv[1])
    try:
        manifest = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        if root.is_symlink() or not root.is_dir():
            raise ValueError("staging root must be an existing, non-symlink directory")
        res = verify(manifest, root)
    except (OSError, ValueError) as exc:
        print(json.dumps({"exact": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(res, indent=1))
    return 0 if res["exact"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
