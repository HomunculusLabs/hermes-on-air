"""Cancel completion truth (carry A, backend): an ACCEPTED cancel request is not a completed cancel.

``cancel_all`` (the registered ``onair_setup_cancel`` tool and the dashboard ``/setup/quick/cancel`` route) is
ok only when the setup job reached a terminal state AND the preview stop is complete. A job that is still
running/cancelling (here: owned by another live process, an inert owned sleeper that never reaches its next
step) gives ``ok:false, partial:true, job_pending:true`` with the job shown as 'cancelling', and never the
'Nothing is running.' note. Once that process is gone the job is recovered as interrupted (terminal) and the
same call is ok. The routed-owner-unanswered and draining cases are in test_correction / test_js_quick_stop*.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

from onair import setup_agent as sa
from onair import setup_engine as se

from test_quick_setup import FakeCtx, owner  # noqa: F401


def test_cancelling_job_is_not_a_completed_cancel(owner, monkeypatch):
    monkeypatch.setattr(se, "_ENGINES", {})
    eng = se.SetupEngine(owner)
    sleeper = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        eng.dir.mkdir(parents=True, exist_ok=True)
        eng._write("job.json", {"id": "j-other", "pid": sleeper.pid, "state": "running", "digest": "d",
                                "steps": [{"key": "stage", "label": "Make the stage", "state": "running"}],
                                "started_at": time.time(), "updated_at": time.time()})
        out = eng.cancel_all(settle_s=0.3)
        assert out["ok"] is False and out["partial"] is True and out["job_pending"] is True, out
        assert out["job"]["state"] == "cancelling" and out["preview"]["ok"] is True, out
        assert "NOT finished cancelling" in out["error"] and "Nothing is running" not in out["note"], out
        assert (eng.dir / "cancel-j-other").exists()  # the request itself was delivered to the job
        # the registered tool surface reports the same partial truth
        handlers = sa.make_handlers(FakeCtx(surface="cli"), get_engine=lambda: eng)
        tool = json.loads(handlers["onair_setup_cancel"]({}))
        assert tool["ok"] is False and tool["job_pending"] is True, tool
        sleeper.kill()
        sleeper.wait(5)
        done = eng.cancel_all(settle_s=0.3)  # the job's process is gone: recovered as interrupted (terminal)
        assert done["ok"] is True and "partial" not in done, done
        assert eng.job()["state"] == "interrupted"
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(5)
