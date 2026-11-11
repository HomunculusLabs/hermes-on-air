"""Browser-journey server: the REAL plugin_api router + real dashboard/dist assets. Only the host SDK shim
(React + the dashboard SDK globals, path in ONAIR_SDK_SHIM) is a disclosed test fixture."""
import os
import sys
from pathlib import Path

SRC = Path(sys.argv[1])
sys.path.insert(0, str(SRC))
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import HTMLResponse, Response  # noqa: E402
import uvicorn  # noqa: E402

from dashboard.plugin_api import router  # noqa: E402

SHIM = Path(os.environ["ONAIR_SDK_SHIM"])
app = FastAPI()

HELD = None
if os.environ.get("ONAIR_HELD_MODEL"):
    # DISCLOSED FIXTURE (opt-in, test server only): a held model client for the host-owned ctx.llm edge, bound
    # to the EXACT module copy the dashboard routes use (plugin_api._onair('setup_engine'), i.e.
    # ``_onair_desk_pkg.setup_engine``) BEFORE any setup engine exists. It blocks each request until released,
    # so a Stop during a draft is genuinely draining. GET /fixture/held shows calls/entered/bound; POST
    # /fixture/release lets the request finish. No network, no provider.
    import threading
    import time
    from types import SimpleNamespace

    from dashboard import plugin_api as _api

    _se = _api._onair("setup_engine")
    if _se._ENGINES:
        raise SystemExit("held model must be bound before any setup engine is created")

    class HeldModel:
        def __init__(self):
            self.calls, self.probes, self.entered, self.release = [], [], threading.Event(), threading.Event()

        def complete(self, messages=None, max_tokens=None, timeout=None, purpose=None, **_kw):
            if max_tokens == 4:
                self.probes.append({"purpose": purpose, "max_tokens": max_tokens})
                return SimpleNamespace(text="OK", provider="fixture-provider", model="held-model")
            self.calls.append({"t": time.time(), "purpose": purpose, "max_tokens": max_tokens,
                               "roles": [m.get("role") for m in messages or []]})
            self.entered.set()
            self.release.wait(float(os.environ.get("ONAIR_HELD_MODEL_MAX_S", "120")))
            return SimpleNamespace(text="Thanks for stopping by!", provider="fixture-provider", model="held-model")

    HELD = HeldModel()
    _se.set_model_client(HELD)

    @app.get("/fixture/held")
    def held_state():
        engines = list(_se._ENGINES.values())
        return {"module": _se.__name__, "engines": len(engines),
                "engines_bound": bool(engines) and all(e.model_client is HELD for e in engines),
                "calls": HELD.calls, "probes": HELD.probes, "entered": HELD.entered.is_set(), "released": HELD.release.is_set()}

    @app.post("/fixture/release")
    def held_release():
        HELD.release.set()
        return {"released": True}

if os.environ.get("ONAIR_SHOW_FIXTURE"):
    # DISCLOSED FIXTURES (opt-in, visual harness only) for the two external edges of the private show, bound to
    # the module copy the dashboard routes use: a model client answering from the rendered turn prompt (no
    # provider) and a text_to_speech dispatcher writing a short silent WAV (no voice synthesis). The real
    # show runtime, StageController, guard, HermesTTS and overlay consume them.
    import json as _json
    from types import SimpleNamespace as _NS

    from dashboard import plugin_api as _api

    _se = _api._onair("setup_engine")

    class ShowModel:
        def complete(self, messages=None, **_kw):
            user = str((messages or [{}])[-1].get("content", ""))
            if _kw.get("max_tokens") == 4:
                text = "OK"
            elif 'beat="opening"' in user:
                text = "Hey everyone, I'm Pixel, and today we're wiring a desk lamp that dims when chat goes quiet!"
            elif "<show_note" in user:
                text = "Soldering tip of the day: tin the tip first and the joint basically makes itself."
            elif "lamp" in user or "?" in user:
                text = "Great question! The lamp listens to chat and fades a little every quiet minute."
            else:
                text = ""
            return _NS(text=text, provider="fixture-provider", model="fixture-model")

    def tts_dispatch(name, args):
        from onair.tts import silent_wav
        Path(args["output_path"]).write_bytes(silent_wav(1.2))
        return _json.dumps({"success": True, "file_path": args["output_path"]})

    _se.set_model_client(ShowModel())
    _se.set_tts_dispatch(tts_dispatch)
    _sr = _api._onair("show_runtime")
    _sr.tts_connection = lambda dispatch: {"usable_here": True, "label": "fixture-voice", "next_action": ""}

app.include_router(router, prefix="/api/plugins/on-air")
HTML = ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        '<link rel="stylesheet" href="/style.css"><style>body{margin:0;font-family:Arial,sans-serif;background:#0f1a19;color:#ecefec}</style></head>'
        '<body><div id="root"></div><script src="/sdk.js"></script><script src="/index.js"></script></body></html>')


@app.get("/on-air")
def page():
    return HTMLResponse(HTML)


@app.get("/sdk.js")
def sdk():
    return Response(SHIM.read_bytes(), media_type="text/javascript")


@app.get("/index.js")
def js():
    return Response((SRC / "dashboard/dist/index.js").read_bytes(), media_type="text/javascript")


@app.get("/style.css")
def css():
    return Response((SRC / "dashboard/dist/style.css").read_bytes(), media_type="text/css")


uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
