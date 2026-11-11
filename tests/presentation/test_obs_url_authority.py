"""Focused startup-packet authority regression (no OBS, network, or credentials)."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


module_path = os.environ.get("URL_AUTHORITY_MODULE")
if module_path:
    spec = importlib.util.spec_from_file_location("obs_bootstrap_tested", module_path)
    ob = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ob)
else:
    from onair.presentation import obs_bootstrap as ob

CLI = os.environ.get("URL_AUTHORITY_CLI")
CFG = SimpleNamespace(presentation_input="Presentation", presentation_scene="Present",
                      window_scene="Window", width=1920, height=1080)
GOOD = ("http://127.0.0.1:1/present", "http://localhost:65535/present",
        "http://[::1]:54321/present")
BAD = (
    "http://127.0.0.1:1@192.0.2.7:80/present",
    "http://localhost:1@192.0.2.7:80/present",
    "http://[::1]:1@192.0.2.7:80/present",
    "http://user@localhost:80/present", "http://localhost:/present",
    "http://localhost:0/present", "http://localhost:65536/present",
    "http://localhost:abc/present", "http://localhost:１２/present",
    "http://localhost:80:90/present", "http://[::1]junk:80/present",
    "http://[::1]:80junk/present", "http://[::1:80/present",
    "http://[::2]:80/present", "http://127.0.0.2:80/present",
    "http://localhost:80\\@192.0.2.7/present",
    "http://localhost:80/\x00present", "http://localhost:80/\x7fpresent",
    "http://localhost:80/\npresent", "http://localhost:80/ space",
    "https://localhost:80/present", "http://localhost:80/" + "x" * 512,
)


class StartupAuthorityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def packet_from_build(self, url, name):
        # A malicious, hash-consistent packet, not an authorized producer result.
        directory = self.root / name
        directory.mkdir()
        files = ob.build(CFG, url)
        for key, value in files.items():
            (directory / key).write_bytes(value)
        return directory, hashlib.sha256(files[ob.MANIFEST_FILE]).hexdigest()

    def cli(self, packet, target, digest, apply=True):
        if not CLI or not module_path:
            self.skipTest("isolated shipped CLI fixture not configured")
        args = [sys.executable, "-B"]
        if not __debug__:
            args.append("-O")
        args += [CLI, module_path, str(packet), str(target), "--expect", digest]
        if apply:
            args.append("--apply")
        result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
        return result.returncode, json.loads(result.stdout)

    def test_valid_producer_validate_apply_and_status(self):
        for i, url in enumerate(GOOD):
            with self.subTest(url=url):
                packet = self.root / ("packet" + str(i))
                result = ob.prepare(CFG, url, directory=packet)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["status"], "created")
                data = ob.validate(packet, result["manifest_sha256"])
                self.assertEqual(data["url"], url)
                self.assertTrue(ob.status(packet, CFG, url)["prepared"])
                stale = ob.status(packet, CFG, "http://localhost:1234/present")
                self.assertTrue(stale["intact"])
                self.assertTrue(stale["stale"])
                self.assertFalse(stale["prepared"])
                target = self.root / ("target" + str(i))
                applied = ob.apply(packet, target, result["manifest_sha256"])
                self.assertTrue(applied["ok"], applied)
                self.assertEqual((target / "basic/scenes" / ob.COLLECTION_FILE).read_bytes(),
                                 (packet / ob.COLLECTION_FILE).read_bytes())
                self.assertEqual((target / "user.ini").read_bytes(), ob.user_ini_text().encode())
                self.assertIn(b"FirstRun=true\n", (target / "user.ini").read_bytes())

    def test_invalid_authorities_refused_by_producer_before_write(self):
        for i, url in enumerate(BAD):
            with self.subTest(url=repr(url)):
                packet = self.root / ("producer" + str(i))
                result = ob.prepare(CFG, url, directory=packet)
                self.assertFalse(result["ok"], result)
                self.assertFalse(packet.exists())

    def test_invalid_authorities_refused_by_validator_and_apply_before_write(self):
        for i, url in enumerate(BAD):
            with self.subTest(url=repr(url)):
                packet, digest = self.packet_from_build(url, "bad" + str(i))
                with self.assertRaises(ob.PacketError) as caught:
                    ob.validate(packet, digest)
                self.assertEqual(caught.exception.code, "not_loopback")
                target = self.root / ("badtarget" + str(i))
                result = ob.apply(packet, target, digest)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["problem"], "not_loopback")
                self.assertFalse(target.exists())
                self.assertFalse(ob.status(packet)["prepared"])

    def test_invalid_update_does_not_modify_existing_packet(self):
        packet = self.root / "existing-packet"
        self.assertTrue(ob.prepare(CFG, GOOD[0], directory=packet)["ok"])
        before = {name: (packet / name).read_bytes() for name in ob.FILES}
        result = ob.prepare(CFG, BAD[0], directory=packet)
        self.assertFalse(result["ok"], result)
        self.assertEqual(before, {name: (packet / name).read_bytes() for name in ob.FILES})
        self.assertTrue(ob.status(packet, CFG, GOOD[0])["prepared"])
        updated = ob.prepare(CFG, GOOD[1], directory=packet)
        self.assertEqual(updated["status"], "updated")
        self.assertTrue(ob.status(packet, CFG, GOOD[1])["prepared"])

    def test_digest_and_target_guards(self):
        packet = self.root / "good"
        digest = ob.prepare(CFG, GOOD[0], directory=packet)["manifest_sha256"]
        target = self.root / "wrong-digest"
        result = ob.apply(packet, target, "0" * 64)
        self.assertEqual(result["problem"], "unbound")
        self.assertFalse(target.exists())
        existing = self.root / "existing"
        existing.mkdir()
        sentinel = existing / "sentinel"
        sentinel.write_bytes(b"untouched")
        result = ob.apply(packet, existing, digest)
        self.assertEqual(result["problem"], "unsafe_target")
        self.assertEqual(sentinel.read_bytes(), b"untouched")
        missing_parent = self.root / "missing" / "target"
        self.assertEqual(ob.apply(packet, missing_parent, digest)["problem"], "unsafe_target")
        self.assertFalse(missing_parent.parent.exists())

    def test_prestart_and_symlink_refusals(self):
        packet, _ = self.packet_from_build(GOOD[0], "prestart")
        settings_path = packet / ob.SETTINGS_FILE
        settings = json.loads(settings_path.read_bytes())
        settings["user_ini"]["General"]["FirstRun"] = "false"
        contents = (json.dumps(settings, indent=1, sort_keys=True) + "\n").encode()
        settings_path.write_bytes(contents)
        manifest_path = packet / ob.MANIFEST_FILE
        manifest = json.loads(manifest_path.read_bytes())
        manifest["files"][ob.SETTINGS_FILE] = {"sha256": hashlib.sha256(contents).hexdigest(), "bytes": len(contents)}
        manifest_bytes = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode()
        manifest_path.write_bytes(manifest_bytes)
        target = self.root / "prestart-target"
        result = ob.apply(packet, target, hashlib.sha256(manifest_bytes).hexdigest())
        self.assertEqual(result["problem"], "prestart_unbound")
        self.assertFalse(target.exists())
        valid = self.root / "valid-for-link"
        digest = ob.prepare(CFG, GOOD[0], directory=valid)["manifest_sha256"]
        real_parent = self.root / "real-parent"
        real_parent.mkdir()
        link = self.root / "link"
        link.symlink_to(real_parent, target_is_directory=True)
        result = ob.apply(valid, link / "target", digest)
        self.assertEqual(result["problem"], "unsafe_target")
        self.assertFalse((real_parent / "target").exists())
        blocked = self.root / "prohibited"
        blocked.mkdir()
        result = ob.apply(valid, blocked / "target", digest, prohibited_roots=[blocked])
        self.assertEqual(result["problem"], "unsafe_target")
        self.assertFalse((blocked / "target").exists())

    def test_shipped_cli_good_bad_and_digest(self):
        if not CLI or not module_path:
            self.skipTest("isolated shipped CLI fixture not configured")
        packet = self.root / "cli-good"
        digest = ob.prepare(CFG, GOOD[0], directory=packet)["manifest_sha256"]
        for i, url in enumerate(GOOD[1:], 1):
            extra_packet = self.root / ("cli-packet" + str(i))
            extra_digest = ob.prepare(CFG, url, directory=extra_packet)["manifest_sha256"]
            extra_target = self.root / ("cli-target" + str(i))
            rc, out = self.cli(extra_packet, extra_target, extra_digest)
            self.assertEqual((rc, out["status"]), (0, "applied"), out)
            self.assertEqual((extra_target / "basic/scenes" / ob.COLLECTION_FILE).read_bytes(),
                             (extra_packet / ob.COLLECTION_FILE).read_bytes())
        target = self.root / "cli-target"
        rc, out = self.cli(packet, target, digest)
        self.assertEqual((rc, out["status"]), (0, "applied"), out)
        self.assertEqual((target / "basic/scenes" / ob.COLLECTION_FILE).read_bytes(),
                         (packet / ob.COLLECTION_FILE).read_bytes())
        self.assertEqual((target / "user.ini").read_bytes(), ob.user_ini_text().encode())
        target = self.root / "cli-wrong"
        rc, out = self.cli(packet, target, "0" * 64)
        self.assertEqual((rc, out["problem"]), (3, "unbound"), out)
        self.assertFalse(target.exists())
        bad, bad_digest = self.packet_from_build(BAD[0], "cli-bad")
        target = self.root / "cli-bad-target"
        rc, out = self.cli(bad, target, bad_digest)
        self.assertEqual((rc, out.get("problem")), (3, "not_loopback"), out)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
