"""The semantic-assist eval corpus is test data: it lives under tests/fixtures, unchanged, and the
eval CLI still reads it by default.

The corpus holds adversarial chat lines on purpose (prompt-injection and echo bait). It is never
loaded at runtime by the plugin, so it is kept with the test fixtures rather than with the shipped
tools. These cases pin its bytes and case identities and run the real ``tools/assist_eval.py``
``main()`` with its default ``--corpus``.

FIXTURE: the external Laya classification service is replaced by an in-process fake transport
(substituted for ``http_transport``). No socket is opened and no model or classifier is called;
the fake answers ``/health`` and ``/check`` with clean verdicts so the consumer runs end to end.
"""
from __future__ import annotations

import collections
import hashlib
import http.client
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "assist_eval_corpus.json"
OLD_PATH = ROOT / "tools" / "assist_eval_corpus.json"
EVAL = ROOT / "tools" / "assist_eval.py"
DOC = ROOT / "docs" / "semantic-assist-eval.md"

# Identity of the frozen corpus (onair-assist-eval-v1), unchanged by the move.
CORPUS_SHA256 = "27ea7d368c8e057acc90b51589498f24bd9b8526c33962de12a3b85897ebdeb5"
CORPUS_NAME = "onair-assist-eval-v1"
# sha256 of the case ids joined with "\n", in corpus order.
CASE_IDS_SHA256 = "c3cfd226ac6ae1dee6a9fb6decd719d8957c5117d742c13c5c6ad6011c2058ad"
CATEGORIES = {"safe_answer": 15, "unsafe_echo": 12, "prompt_injection": 12, "ambiguity": 8,
              "benign_lookalike": 12}


def load_eval_module():
    spec = importlib.util.spec_from_file_location("onair_assist_eval_under_test", EVAL)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {EVAL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeLaya:
    """Labelled stand-in for the local Laya service: records every call, answers clean."""

    def __init__(self):
        self.calls = []

    def factory(self, host, port, **_kw):
        self.calls.append(("connect", host, port))
        return self.call

    def call(self, method, path, body, timeout):
        self.calls.append((method, path, body))
        if method == "GET" and path.endswith("/health"):
            return 200, {"ok": True, "model": "FIXTURE-fake-laya", "flag_keys": ["injection"],
                         "threshold_default": 0.5, "max_chars": 100000, "modes": ["pre", "post"]}
        if method == "POST" and path.endswith("/check"):
            return 200, {"flagged": False, "scores": {"injection": 0.01, "exfil": 0.0}, "notes": [],
                         "confidence": 0.99, "latency_ms": 1.0}
        return 404, None


def _no_network(*_a, **_kw):
    raise AssertionError("the eval test must not open a real connection")


class AssistEvalFixtureTest(unittest.TestCase):
    def test_corpus_lives_under_tests_fixtures_with_unchanged_bytes_and_cases(self):
        self.assertTrue(FIXTURE.is_file(), f"missing {FIXTURE.relative_to(ROOT)}")
        self.assertFalse(OLD_PATH.exists(), "the adversarial corpus must not ship under tools/")
        data = FIXTURE.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), CORPUS_SHA256)
        corpus = json.loads(data)
        self.assertEqual(corpus["name"], CORPUS_NAME)
        ids = [case["id"] for case in corpus["cases"]]
        self.assertEqual(len(ids), 59)
        self.assertEqual(len(set(ids)), 59)
        self.assertEqual(hashlib.sha256("\n".join(ids).encode()).hexdigest(), CASE_IDS_SHA256)
        self.assertEqual(dict(collections.Counter(c["cat"] for c in corpus["cases"])), CATEGORIES)

    def test_eval_cli_default_corpus_is_the_fixture_and_every_case_runs(self):
        module = load_eval_module()
        corpus = json.loads(FIXTURE.read_bytes())
        fake = FakeLaya()
        with tempfile.TemporaryDirectory(prefix="assist-eval-") as tmp:
            out_path = Path(tmp) / "results.json"
            argv = [str(EVAL), "--out", str(out_path)]  # no --corpus: the default is under test
            with mock.patch.object(module, "http_transport", fake.factory), \
                    mock.patch.object(http.client.HTTPConnection, "connect", _no_network), \
                    mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
                rc = module.main()
            self.assertEqual(rc, 0)
            out = json.loads(out_path.read_text(encoding="utf-8"))

        self.assertEqual(out["corpus"], CORPUS_NAME)
        self.assertEqual(out["corpus_sha256"], CORPUS_SHA256)
        self.assertEqual([r["id"] for r in out["rows"]], [c["id"] for c in corpus["cases"]])
        self.assertEqual(out["summary"]["cases"], 59)
        self.assertEqual(out["summary"]["non_ok_statuses"], {})
        self.assertIs(out["health"].get("ok"), True)
        self.assertEqual(out["health"]["info"]["model"], "FIXTURE-fake-laya")

        posts = [c[2] for c in fake.calls if c[0] == "POST"]
        drafts = [b["text"] for b in posts if b["mode"] == "post"]
        self.assertEqual(drafts, [module.expand(c["draft"]) for c in corpus["cases"]])
        self.assertFalse(any("{FAKE_" in b["text"] for b in posts), "placeholders must be expanded")
        self.assertEqual(out["requests_total"], 1 + len(posts))
        self.assertEqual(fake.calls[0], ("connect", "127.0.0.1", 8390))

    def test_eval_doc_names_the_fixture_path(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("tests/fixtures/assist_eval_corpus.json", text)
        self.assertNotIn("tools/assist_eval_corpus.json", text)


if __name__ == "__main__":
    unittest.main()
