"""DIAGNOSTIC receipt only (synthetic, owned): the identity_unknown stop text must not claim an off-air
HTTP request was sent just because off_air() was invoked. Seams: _alive/_control/_remember/_identity/off_air
are replaced so the ACTUAL sr.stop caller reaches its identity_unknown branch; no process is signalled."""
from __future__ import annotations

import json
import os

import pytest

from onair import stage_runtime as sr

from test_quick_setup import FakeCtx, owner  # noqa: F401
from test_stage_runtime import _made_stage

REFUSED = {"ok": False, "error": "The stage record changed (another start or stop ran); nothing was sent."}
CONFIRMED = {"ok": True, "mode": "off_air"}


@pytest.mark.parametrize("case", ["refused_no_post", "confirmed", "skipped"])
def test_identity_unknown_receipt_reports_invocation_not_post(owner, tmp_path, monkeypatch, case):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    record = {"pid": 2 ** 22 - 7, "gen": "diag-gen", "home": str(home), "ticks": 1}
    eng._path(sr.RUNTIME_FILE).write_text(json.dumps(record))
    calls, signals = [], []
    monkeypatch.setattr(sr, "_alive", lambda r: case != "skipped")
    monkeypatch.setattr(sr, "_control", lambda h, pid: {"pid": pid})
    monkeypatch.setattr(sr, "_endpoints", lambda info: [])
    monkeypatch.setattr(sr, "_remember", lambda e, r, **kw: r)
    monkeypatch.setattr(sr, "_identity", lambda pid: ("unknown", None))
    monkeypatch.setattr(sr, "off_air", lambda e, expect=None: (calls.append(expect),
                        dict(REFUSED if case == "refused_no_post" else CONFIRMED))[1])
    monkeypatch.setattr(os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    out = sr.stop(eng)
    assert out["state"] == "identity_unknown" and out["ok"] is False and out["signalled"] is False
    assert signals == [] and "NOT confirmed" in out["error"] and "NO signal was sent" in out["error"]
    # named diagnostic assertion: invocation is never reported as a sent request
    assert "request was sent" not in out["error"], "receipt claims an off-air HTTP request from mere invocation"
    if case == "skipped":
        assert calls == [] and out["off_air_attempted"] is False and out["off_air_result"] is None
        assert "no off-air operation was attempted" in out["error"]
    else:
        assert len(calls) == 1 and out["off_air_attempted"] is True
        assert "off-air operation was attempted earlier" in out["error"]
        if case == "refused_no_post":
            assert out["off_air_result"] == REFUSED and "did NOT confirm off air" in out["error"]
            assert REFUSED["error"] in out["error"] and "reported mode off_air" not in out["error"]
        else:
            assert out["off_air_result"] == CONFIRMED and "reported mode off_air" in out["error"]
