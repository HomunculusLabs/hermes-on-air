"""tools/verify_dropin.py compares actual permission bits, hashes and the exact member set.

Every case runs the real CLI in a subprocess over disposable files in a fresh temporary directory.
Set VERIFY_DROPIN_UNDER_TEST to point the same cases at another verifier (used for the red control).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERIFIER = Path(os.environ.get("VERIFY_DROPIN_UNDER_TEST") or ROOT / "tools" / "verify_dropin.py")
FILES = {"LICENSE": (b"licence text\n", 0o644), "onair/a.py": (b"x = 1\n", 0o644),
         "tools/run.sh": (b"#!/bin/sh\n", 0o755)}


class DropinVerifierTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="dropin-verify-")
        self.base = Path(self._tmp.name)
        self.stage = self.base / "stage"
        files = {}
        for rel, (data, mode) in FILES.items():
            p = self.stage / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            os.chmod(p, mode)
            files[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "mode": format(mode, "o")}
        self.manifest = self.base / "manifest.json"
        self.manifest.write_text(json.dumps({"files": files}), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self):
        r = subprocess.run([sys.executable, "-B", str(VERIFIER), str(self.manifest), str(self.stage)],
                           capture_output=True, text=True, timeout=30)
        return r.returncode, (json.loads(r.stdout) if r.stdout.strip() else {})

    def assertAccepted(self):
        rc, res = self.run_cli()
        self.assertEqual(rc, 0, res)
        self.assertIs(res.get("exact"), True)

    def assertRejected(self, key):
        rc, res = self.run_cli()
        self.assertEqual(rc, 1, res)
        self.assertIs(res.get("exact"), False)
        self.assertTrue(res.get(key), (key, res))
        return res

    def test_exact_0644_and_0755_closure_is_accepted(self):
        self.assertAccepted()

    def test_every_non_matching_mode_against_0644_is_rejected(self):
        for mode in (0o664, 0o666, 0o600, 0o640, 0o755):
            with self.subTest(mode=format(mode, "o")):
                os.chmod(self.stage / "LICENSE", mode)
                res = self.assertRejected("mode_mismatch")
                self.assertEqual(res["mode_mismatch"],
                                 [{"path": "LICENSE", "expected": "644", "actual": format(mode, "o")}])
        os.chmod(self.stage / "LICENSE", 0o644)
        self.assertAccepted()

    def test_executable_losing_its_bits_is_rejected(self):
        os.chmod(self.stage / "tools/run.sh", 0o644)
        self.assertRejected("mode_mismatch")

    def test_extra_missing_edited_and_link_members_are_rejected(self):
        extra = self.stage / "onair" / "stray.py"
        extra.write_bytes(b"")
        os.chmod(extra, 0o644)
        self.assertRejected("extra")
        extra.unlink()
        (self.stage / "onair/a.py").write_bytes(b"x = 2\n")
        self.assertRejected("hash_mismatch")
        (self.stage / "onair/a.py").write_bytes(b"x = 1\n")
        link = self.stage / "onair" / "link.py"
        link.symlink_to("a.py")
        self.assertRejected("links_or_special")
        link.unlink()
        moved = self.base / "LICENSE.away"
        (self.stage / "LICENSE").rename(moved)
        self.assertRejected("missing")
        moved.rename(self.stage / "LICENSE")
        self.assertAccepted()


if __name__ == "__main__":
    unittest.main()
