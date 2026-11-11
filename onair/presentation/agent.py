"""Private-agent tools for the presentation/output slice, plus the human-typed go-live command.

Contract for the integrator (who owns the single real ``register`` call and the plugin.yaml merge):

- ``onair.presentation.register(ctx, config=None) -> list[str]`` registers the tools in ``SCHEMAS`` (toolset
  ``onair_present``) and the slash command ``/onair-broadcast``. It returns what it registered, and registers
  NOTHING in an On Air stage home (the public co-host), so public turns never see these schemas.
- Authority comes from the host, never from the model: every handler calls ``ctx.session_surface()`` and refuses
  unless it is a local owner client (cli/tui/desktop). No schema accepts a caller/owner/approved argument
  (``additionalProperties: false``) and extra keys are refused, not read.
- Broadcast start is a human release: the tool only returns a one-time code; the HUMAN types
  ``/onair-broadcast start <code>`` and confirms on the host's own choice card (``ctx.ask_choice``). Stop needs no
  approval. Nothing starts on load: registering serves nothing and contacts no OBS.
- ``onair.presentation.teardown()`` stops the surface and its loop thread (the integrator calls it on plugin
  shutdown). It does not stop a running broadcast: an operator's stream is never ended silently.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
from typing import Any

from . import layout, obs_bootstrap
from .deck import LIMITS, SAMPLE_DECK, Program, _text, validate_deck
from .obs_control import ObsConfig, ObsControl
from .surface import PresentationSurface

TOOLSET = "onair_present"
_PUBLIC_SURFACES = {"on_air", "onair", "on-air"}
# "local": the Hermes gateway's Local platform, i.e. this machine's own CLI/TUI/desktop clients attached over
# the owner-only local socket (core lists it in NON_MESSAGING_SESSION_SURFACES). On the supported SDK the
# classic CLI runs plugin slash commands there, so /onair-setup and /onair-broadcast arrive as "local".
_OWNER_SURFACES = {"cli", "tui", "desktop", "local"}
_NONE = {"type": "object", "properties": {}, "additionalProperties": False}
_CODE_TTL_S = 120

_SLIDE = {"type": "object", "additionalProperties": False, "required": ["kind"], "properties": {
    "kind": {"type": "string", "enum": ["title", "bullets", "text", "code"]},
    "heading": {"type": "string", "maxLength": LIMITS["heading"]},
    "subtitle": {"type": "string", "maxLength": LIMITS["subtitle"]},
    "bullets": {"type": "array", "maxItems": LIMITS["bullets"], "items": {"type": "string", "maxLength": LIMITS["bullet"]}},
    "body": {"type": "string", "maxLength": LIMITS["body"]},
    "code": {"type": "string", "maxLength": LIMITS["code"]},
    "language": {"type": "string", "maxLength": LIMITS["language"]}}}

SCHEMAS: dict[str, dict] = {
    "onair_work_share": {"name": "onair_work_share", "description": (
        "Publish a bounded PUBLIC work view on the existing slides Browser Source, never screen capture. "
        "Only explicitly supplied text is published. Refuses leak shapes. Work is blank under privacy, off air "
        "or when the configured overlay cannot prove safety. Actions: show, update, append, status, step, clear, read."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["action"], "properties": {
            "action": {"type": "string", "enum": ["show", "update", "append", "status", "step", "clear", "read"]},
            "title": {"type": "string", "maxLength": 80},
            "panes": {"type": "array", "minItems": 1, "maxItems": 2, "items": {
                "type": "object", "additionalProperties": False, "required": ["kind", "label", "content"], "properties": {
                    "kind": {"type": "string", "enum": ["code", "terminal", "text", "diff"]},
                    "label": {"type": "string", "maxLength": 40}, "language": {"type": "string", "maxLength": 24},
                    "content": {"type": "string", "maxLength": 6000}}}},
            "pane": {"type": "integer", "minimum": 0, "maximum": 1},
            "content": {"type": "string", "maxLength": 6000}, "text": {"type": "string", "maxLength": 6000},
            "status": {"type": "string", "maxLength": 80},
            "done": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 60}},
            "current": {"type": "string", "maxLength": 60},
            "next": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 60}}}}},
    "onair_present_status": {"name": "onair_present_status", "description": (
        "Read-only. What is actually on the presentation surface (serving, URL, mode, slide, revision, connected "
        "pages) and, when include_obs is true, what OBS reports: program scene, owned sources, speech-audio check, "
        "shared-window readiness and the stream output. Unreachable OBS is reported as not connected."),
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": {"include_obs": {"type": "boolean", "default": True}}}},
    "onair_present_publish": {"name": "onair_present_publish", "description": (
        "Publish (replace) the PUBLIC deck shown on stream through the presentation Browser Source: plain text "
        f"only, up to {LIMITS['slides']} slides of kind title, bullets, text or code. Starts serving the surface if "
        "needed and shows slide 'start' (default 0). Everything here is visible to viewers. Use sample=true for a "
        "built-in neutral sample deck."),
        "parameters": {"type": "object", "additionalProperties": False, "properties": {
            "deck": {"type": "object", "additionalProperties": False, "required": ["title", "slides"], "properties": {
                "title": {"type": "string", "maxLength": LIMITS["deck_title"]},
                "slides": {"type": "array", "minItems": 1, "maxItems": LIMITS["slides"], "items": _SLIDE}}},
            "sample": {"type": "boolean"},
            "start": {"type": "integer", "minimum": 0}}}},
    "onair_present_control": {"name": "onair_present_control", "description": (
        "Drive the presentation surface: next, prev, goto (index), standby (optional title/note card), resume "
        "(back to the current slide), serve (start serving without content) or stop_serving. Returns the new state."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["action"], "properties": {
            "action": {"type": "string", "enum": ["next", "prev", "goto", "standby", "resume", "serve", "stop_serving"]},
            "index": {"type": "integer", "minimum": 0},
            "title": {"type": "string", "maxLength": LIMITS["heading"]},
            "note": {"type": "string", "maxLength": LIMITS["note"]}}}},
    "onair_obs_setup": {"name": "onair_obs_setup", "description": (
        "OBS (WebSocket v5) setup for the owned show sources. 'inspect' is read-only. 'configure' creates or updates "
        "ONLY the owned scenes and the two owned Browser Sources (presentation page with no audio; caption/voice "
        "overlay as the single audio source) and reads them back. It never switches the program scene or starts "
        "any output. On macOS with no Browser Source yet it refuses with bootstrap_required before changing "
        "anything: use 'prepare_startup', which writes (does not apply) an owned startup packet: a scene collection "
        "holding the presentation Browser Source plus the prestart settings, for a FRESH OBS start before configure."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["action"], "properties": {
            "action": {"type": "string", "enum": ["inspect", "configure", "prepare_startup"]}}}},
    "onair_obs_scene": {"name": "onair_obs_scene", "description": (
        "Switch the OBS program scene to one of the approved scenes (owned presentation and shared-window scenes, "
        "plus any the operator approved in settings). ok only when OBS reports that scene on program. The "
        "shared-window scene is refused until the shared window is ready, exactly as onair_window_share show."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["scene"], "properties": {
            "scene": {"type": "string", "maxLength": 200}}}},
    "onair_window_share": {"name": "onair_window_share", "description": (
        "Share ONE OS window that the owner chose in OBS (OS capture, distinct from the presentation Browser "
        "Source). discover: platform, creatable kinds, selection/readiness and next_human_action; prepare: create "
        "the owned capture input (nothing is chosen or shown); open_chooser: ask OBS to show the system share "
        "dialog (PipeWire inputs; revokes any confirmation); show: put the shared-window scene on program (only when "
        "ready); hide: return to the presentation scene. Never picks or names a window. On Wayland the only "
        "creatable kind is combined screen/window capture: it is shown only after the OWNER types "
        "/onair-broadcast confirm-window and confirms on the card, and is reported as operator_confirmed with "
        "capture_type_verified false (OBS cannot tell a window from a monitor). You cannot confirm it yourself."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["action"], "properties": {
            "action": {"type": "string", "enum": ["discover", "prepare", "open_chooser", "show", "hide"]}}}},
    "onair_broadcast": {"name": "onair_broadcast", "description": (
        "The OBS stream output. status: what OBS reports (active/inactive/unknown). request_start: returns a one-time "
        "code; going live needs the USER to type the returned /onair-broadcast command and confirm, you cannot start "
        "it. stop: stop the stream; ok only when OBS reports the output inactive."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["action"], "properties": {
            "action": {"type": "string", "enum": ["status", "request_start", "stop"]}}}},
    "onair_layout": {"name": "onair_layout", "description": (
        "Arrange On Air's OWN scene items with a named preset. presets: the presets, the real sources each one "
        "needs and recipes (standby, conversation); no OBS contact. inspect: read-only, what OBS reports now (canvas, "
        "owned items, which preset each owned scene matches) and which presets are ready or what is missing. apply: "
        "set the preset in its owned scene, fitted to the OBS base canvas with aspect ratio kept, then read it back; "
        "applied only when the readback matches. Presets: presentation (slides full screen), window (the "
        "owner-selected window full screen), window_pip (window full screen, slides as a corner inset; corner "
        "optional), side_by_side. It never picks or captures a window (a window preset needs onair_window_share "
        "ready, on Wayland the owner's confirmation), never switches the program scene, never starts or stops the "
        "stream and never touches other scenes or sources. If the scene is already on program, viewers see the "
        "change at once. For three boxes use action 'set' with main/sub1/sub2 (scene, slides, window or empty; "
        "main cannot be empty) and arrangement stack or side. Use 'put' with slot/source, 'swap' with a/b, "
        "'arrangement' to change geometry, or 'boxes' to inspect readback. A source moves rather than duplicates; "
        "window still needs the owner's selection and confirmation. Switch to On Air: Boxes separately."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["action"], "properties": {
            "action": {"type": "string", "enum": ["presets", "inspect", "apply", "boxes", "set", "put", "swap", "arrangement"]},
            "preset": {"type": "string", "enum": list(layout.PRESET_NAMES)},
            "corner": {"type": "string", "enum": list(layout.CORNERS)},
            "main": {"type": "string", "enum": ["scene", "slides", "window", "empty"], "description": "Main can never be empty."},
            "sub1": {"type": "string", "enum": ["scene", "slides", "window", "empty"]},
            "sub2": {"type": "string", "enum": ["scene", "slides", "window", "empty"]},
            "slot": {"type": "string", "enum": ["main", "sub1", "sub2"]},
            "source": {"type": "string", "enum": ["scene", "slides", "window", "empty"]},
            "a": {"type": "string", "enum": ["main", "sub1", "sub2"]},
            "b": {"type": "string", "enum": ["main", "sub1", "sub2"]},
            "inset_corner": {"type": "string", "enum": ["bottom-right", "top-right", "top-left", "bottom-left"]},
            "arrangement": {"type": "string", "enum": ["stack", "side"]}}}},
}
TOOL_NAMES = tuple(SCHEMAS)


def _stage_home() -> bool:
    try:
        from ..onboarding import MARKER
    except Exception:  # noqa: BLE001 - cannot tell: treat as stage (fail closed)
        return True
    home = os.environ.get("HERMES_HOME") or os.path.join(os.path.expanduser("~"), ".hermes")
    return os.path.isfile(os.path.join(home, MARKER))


def owner_session(ctx: Any) -> tuple[bool, str]:
    """(allowed, reason) from the host's own session context; same policy as On Air setup. Fails closed."""
    if _stage_home():
        return False, "This Hermes home is an On Air stage (the public co-host); presentation control is not available here."
    try:
        surface = str(ctx.session_surface() or "").strip().lower()
    except Exception:  # noqa: BLE001
        surface = ""
    if not surface:
        return False, "No owner session could be established for this call; nothing was changed."
    if surface in _PUBLIC_SURFACES:
        return False, "Presentation control is not available to the public stage."
    if surface not in _OWNER_SURFACES:
        return False, ("Presentation and broadcast control run only in your own local Hermes (CLI, TUI or desktop "
                       "app); a messaging chat cannot prove you are the owner. Nothing was changed.")
    return True, surface


DEFAULT_PRESENT_PORT = 18795


def _config(ctx: Any, config: dict | None) -> tuple[ObsConfig, int]:
    cfg: dict = dict(config or {})
    if not cfg and callable(getattr(ctx, "get_config", None)):
        try:
            got = ctx.get_config("presentation")
            cfg = dict(got) if isinstance(got, dict) else {}
        except Exception:  # noqa: BLE001
            cfg = {}
    env = os.environ
    overlay_port = int(cfg.get("overlay_port") or env.get("ONAIR_OVERLAY_PORT") or 18790)
    known = {k: cfg[k] for k in ("presentation_scene", "window_scene", "presentation_input", "overlay_input",
                                 "window_input", "width", "height", "start_deadline_s", "stop_deadline_s", "poll_s", "scene_view")
             if k in cfg}
    from ..secrets import get
    obs = ObsConfig(url=str(cfg.get("obs_url") or env.get("ONAIR_OBS_URL") or "ws://127.0.0.1:4455"),
                    password=lambda: get("ONAIR_OBS_PASSWORD"),
                    extra_scenes=tuple(str(s) for s in cfg.get("approved_scenes") or ()),
                    overlay_url=str(cfg.get("overlay_url") or f"http://127.0.0.1:{overlay_port}/overlay"), **known)
    port = int(cfg["present_port"] if "present_port" in cfg else env.get("ONAIR_PRESENT_PORT") or DEFAULT_PRESENT_PORT)
    return obs, port


def _port_is_default(ctx: Any, config: dict | None) -> bool:
    """True when nobody chose the presentation port (no present_port setting, no ONAIR_PRESENT_PORT)."""
    cfg: dict = dict(config or {})
    if not cfg and callable(getattr(ctx, "get_config", None)):
        try:
            got = ctx.get_config("presentation")
            cfg = dict(got) if isinstance(got, dict) else {}
        except Exception:  # noqa: BLE001
            cfg = {}
    return "present_port" not in cfg and not os.environ.get("ONAIR_PRESENT_PORT")


class Runtime:
    """One per process: the program, its loopback surface (on a private loop thread) and the OBS controller."""

    def __init__(self, obs: ObsConfig, port: int = DEFAULT_PRESENT_PORT, client: Any = None, fallback_port: bool = False):
        self.program = Program()
        self.port = port
        # Only when nobody chose the port: a busy default falls back to a free loopback port (reported as port_note).
        self.fallback_port = fallback_port
        self.port_note = ""
        self.surface: PresentationSurface | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._work_last_push = 0.0
        self._work_push_handle = None
        kwargs = {"client": client} if client is not None else {}
        self.obs = ObsControl(obs, lambda: self.surface.url if self.surface else "", **kwargs)
        self._start_code: tuple[str, float] | None = None
        self.startup_dir = obs_bootstrap.default_dir()

    # ---- macOS OBS startup packet (prepared, never applied here) ---------------------------------------
    def prepare_startup(self) -> dict:
        s = self.surface
        if s is None or not s.serving:
            return {"ok": False, "error": "The presentation surface is not serving; publish a deck or use action "
                    "'serve' first, so the packet carries the real presentation URL. Nothing was written."}
        cfg = self.obs.cfg
        out = obs_bootstrap.prepare(cfg, s.url, secret=cfg.password(), directory=self.startup_dir)
        out["startup_packet"] = {"collection_name": obs_bootstrap.COLLECTION_NAME, "presentation_url": s.url,
                                 "presentation_input": cfg.presentation_input, "scene": cfg.presentation_scene}
        if out.get("ok"):
            out["expected_manifest_sha256"] = out["manifest_sha256"]
            out["next_agent_action"] = (
                "Prepared, NOT applied. On the machine running OBS, within your own access there: write the "
                "required-settings.json user.ini values into a FRESH owned OBS config before OBS first starts, install "
                "the collection with its exact bytes (the consumer must be given expected_manifest_sha256 from this "
                "result and refuses any other manifest), make the presentation URL reachable "
                "on that machine (it is a loopback URL), start a NEW OBS process with that collection, then run "
                "onair_obs_setup configure. Do not restart or edit an OBS the owner is using; if you have no approved "
                "way to do this on the OBS host, report that one prerequisite to the owner.")
        return out

    # ---- loop thread -----------------------------------------------------------------------------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or not self._loop.is_running():
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def run():
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                loop.run_forever()
            self._thread = threading.Thread(target=run, name="onair-presentation", daemon=True)
            self._thread.start()
            ready.wait(5)
            self._loop = loop
        return self._loop

    def call(self, coro, timeout: float = 30.0):
        return asyncio.run_coroutine_threadsafe(coro, self._ensure_loop()).result(timeout)

    # ---- surface -------------------------------------------------------------------------------------
    def serve(self) -> dict:
        with self._lock:
            if self.surface is None or not self.surface.serving:
                surface = PresentationSurface(self.program, port=self.port, overlay_url=lambda: self.obs.cfg.overlay_url)
                self.port_note = ""
                try:
                    self.call(surface.start(), 10)
                except OSError:
                    if not self.fallback_port:
                        return {"ok": False, "error": (
                            f"The presentation port {self.port} is in use. Nothing was started. It was chosen in the "
                            "On Air settings (presentation.present_port) or ONAIR_PRESENT_PORT: the owner can set a "
                            "free port there, or remove it so On Air picks a free loopback port by itself.")}
                    surface = PresentationSurface(self.program, port=0, overlay_url=lambda: self.obs.cfg.overlay_url)
                    try:
                        self.call(surface.start(), 10)
                    except OSError:
                        return {"ok": False, "error": "No free loopback port for the presentation page. Nothing was started."}
                    self.port_note = (f"The default presentation port {self.port} was in use, so the page is on a free "
                                      f"loopback port for this session: {surface.url}. Use this URL (OBS sources are "
                                      "configured from it by onair_obs_setup configure).")
                self.surface = surface
        return {"ok": True, **self.state()}

    def _push(self) -> None:
        s = self.surface
        if s is not None and s.serving and s.loop is not None:
            s.loop.call_soon_threadsafe(s.broadcast)

    def _push_work(self, urgent: bool = False) -> None:
        """Coalesce routine work frames to four per second; clearing is immediate."""
        s = self.surface
        if not s or not s.serving or not s.loop:
            return
        def send():
            self._work_push_handle = None
            self._work_last_push = time.monotonic()
            if self.surface is s and s.serving:
                s.broadcast(urgent=urgent)
        def schedule():
            if urgent:
                if self._work_push_handle:
                    self._work_push_handle.cancel()
                send()
                return
            delay = max(0.0, .25 - (time.monotonic() - self._work_last_push))
            if self._work_push_handle is None:
                self._work_push_handle = s.loop.call_later(delay, send)
        s.loop.call_soon_threadsafe(schedule)

    def public(self) -> dict:
        s = self.surface
        return s.snapshot() if s and s.serving else self.program.public()

    def state(self) -> dict:
        s = self.surface
        if s and s.serving:
            s.enforce_source()
        surface = {"serving": bool(s and s.serving), "url": s.url if s else "", "pages_connected": s.clients if s else 0,
                   "label": "Browser Source (web page, not screen capture)"}
        if self.port_note and s is not None and s.serving:
            surface["port_note"] = self.port_note
        return {"surface": surface, "program": self.program.private()}

    def stop_serving(self) -> dict:
        with self._lock:
            s, self.surface = self.surface, None
            if s is not None and s.serving:
                self.call(s.stop(), 10)
        return {"ok": True, **self.state()}

    def teardown(self) -> None:
        self.stop_serving()
        loop, self._loop = self._loop, None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
            if self._thread is not None:
                self._thread.join(5)
            loop.close()
        self._start_code = None

    # ---- go-live code (human release) -----------------------------------------------------------------
    def issue_start_code(self) -> str:
        code = secrets.token_hex(3)
        self._start_code = (code, time.monotonic() + _CODE_TTL_S)
        return code

    def take_start_code(self, code: str) -> bool:
        held, self._start_code = self._start_code, None
        return bool(held and code and secrets.compare_digest(held[0], code) and time.monotonic() < held[1])


def _refuse(reason: str) -> str:
    return json.dumps({"ok": False, "refused": True, "error": reason})


def make_handlers(ctx: Any, rt: Runtime) -> dict:
    def guarded(name, fn):
        allowed_keys = set(SCHEMAS[name]["parameters"]["properties"])

        def handler(args: Any = None, **_kw: Any) -> str:
            ok, reason = owner_session(ctx)
            if not ok:
                return _refuse(reason)
            args = args if isinstance(args, dict) else {}
            extra = sorted(set(args) - allowed_keys)
            if extra:
                if name == "onair_work_share":
                    return json.dumps({"ok": False, "refused": True, "error": "Unexpected field: only the documented fields are allowed."})
                return json.dumps({"ok": False, "error": "Not accepted here: " + ", ".join(extra)})
            try:
                return json.dumps(fn(args), default=str)
            except Exception as exc:  # noqa: BLE001 - report, never crash the agent turn
                return json.dumps({"ok": False, "error": f"{type(exc).__name__}: presentation action failed"})
        return handler

    def work_share(a):
        action = a.get("action")
        if action == "read":
            if set(a) != {"action"}:
                return {"ok": False, "error": "Give only action for read."}
            snap = rt.public()
            return {"ok": True, "revision": snap["revision"], "summary": "Current public program.", "program": snap}
        if action == "show":
            from .work import apply
            _, errors = apply(None, a)
            if errors:
                return {"ok": False, "refused": True, "errors": errors, "note": "Nothing was published."}
            served = rt.serve()
            if not served.get("ok"):
                return served
        ok, result = rt.program.work_action(a)
        if not ok:
            return {"ok": False, "refused": True, "errors": result, "note": "Nothing was published."}
        rt._push_work(urgent=action == "clear")
        snap = rt.public()
        return {"ok": True, "revision": snap["revision"], "summary": "Work cleared." if action == "clear" else "Work updated.",
                "program": snap, "surface": rt.state()["surface"]}

    def status(a):
        out = {"ok": True, **rt.state()}
        if a.get("include_obs", True):
            obs = rt.call(rt.obs.inspect())
            win = rt.call(rt.obs.window_discover()) if obs.get("connected") else {}
            out["obs"] = obs
            out["window"] = win.get("window")
        return out

    def publish(a):
        if a.get("sample"):
            deck, errors = validate_deck(SAMPLE_DECK)
        else:
            deck, errors = validate_deck(a.get("deck"))
        if deck is None:
            return {"ok": False, "errors": errors, "note": "Nothing was published."}
        start = a.get("start", 0)
        if not isinstance(start, int) or isinstance(start, bool) or not 0 <= start < len(deck["slides"]):
            return {"ok": False, "errors": {"start": f"0 to {len(deck['slides']) - 1}"}}
        served = rt.serve()
        if not served.get("ok"):
            return served
        rt.program.publish(deck, start)
        rt._push()
        return {"ok": True, **rt.state()}

    def control(a):
        action = a.get("action")
        if action == "serve":
            return rt.serve()
        if action == "stop_serving":
            return rt.stop_serving()
        if action in ("next", "prev", "goto"):
            ok, why = rt.program.move(action, a.get("index"))
            if not ok:
                return {"ok": False, "error": why, **rt.state()}
        elif action == "standby":
            errors: dict = {}
            title = _text(a.get("title"), "title", LIMITS["heading"], errors) if "title" in a else None
            note = _text(a.get("note"), "note", LIMITS["note"], errors) if "note" in a else None
            if errors:
                return {"ok": False, "errors": errors}
            rt.program.standby(title, note)
        elif action == "resume":
            ok, why = rt.program.resume()
            if not ok:
                return {"ok": False, "error": why, **rt.state()}
        else:
            return {"ok": False, "error": "unknown action"}
        rt._push()
        out = {"ok": True, **rt.state()}
        if not out["surface"]["serving"]:
            out["note"] = "Changed the program, but the surface is not serving; use action 'serve'."
        return out

    def obs_setup(a):
        action = a.get("action")
        if action == "prepare_startup":
            return rt.prepare_startup()
        if action == "configure":
            out = rt.call(rt.obs.configure())
            if out.get("bootstrap_required"):
                s = rt.surface
                url = s.url if s is not None and s.serving else None
                try:   # read-only: never writes on a refusal; a malformed packet never reads as prepared
                    pkt = obs_bootstrap.status(rt.startup_dir, rt.obs.cfg, url)
                except Exception as e:  # noqa: BLE001 - keep the bootstrap guidance whatever the folder holds
                    pkt = {"prepared": False, "drift": True, "problem": "unreadable",
                           "error": f"The startup packet could not be checked ({e.__class__.__name__})."}
                out["startup_packet"] = pkt
                if pkt.get("prepared"):
                    out["next_agent_action"] = ("The startup packet is prepared for this runtime but OBS is not running "
                                                "from it: apply it on the OBS host with its manifest_sha256 (see "
                                                "required-settings.json start_order), start a FRESH OBS with that "
                                                "collection, then configure again.")
                elif pkt.get("stale") or pkt.get("drift"):
                    out["next_agent_action"] = pkt.get("next_agent_action") or (
                        "The startup packet is not usable; run onair_obs_setup prepare_startup again.")
            elif out.get("configured"):
                out["render"] = {**out.get("render", {}), "presentation_pages_connected": rt.state()["surface"]["pages_connected"]}
            return out
        if action == "inspect":
            return rt.call(rt.obs.inspect())
        return {"ok": False, "error": "unknown action"}

    def obs_scene(a):
        return rt.call(rt.obs.set_scene(str(a.get("scene") or "")))

    def window(a):
        fn = {"discover": rt.obs.window_discover, "prepare": rt.obs.window_prepare,
              "open_chooser": rt.obs.window_open_chooser, "show": rt.obs.window_show, "hide": rt.obs.window_hide}
        if a.get("action") not in fn:
            return {"ok": False, "error": "unknown action"}
        return rt.call(fn[a["action"]]())

    def broadcast(a):
        action = a.get("action")
        if action == "status":
            return rt.call(rt.obs.stream_status())
        if action == "stop":
            return rt.call(rt.obs.stop_stream())
        if action == "request_start":
            code = rt.issue_start_code()
            return {"ok": True, "started": False, "approve_with": f"/onair-broadcast start {code}",
                    "expires_in_s": _CODE_TTL_S,
                    "tell_user": ("Going live is your call. If you want to start the OBS stream to the destination "
                                  f"configured in OBS, type: /onair-broadcast start {code}  (you cannot be started by "
                                  "the agent; the code expires in two minutes).")}
        return {"ok": False, "error": "unknown action"}

    def arrange(a):
        action = a.get("action")
        box_keys = {"boxes": set(), "set": {"main", "sub1", "sub2", "arrangement"},
                    "put": {"slot", "source"}, "swap": {"a", "b"}, "arrangement": {"arrangement"}}
        if action in box_keys:
            optional = {"inset_corner"} if action in ("set", "arrangement") else set()
            if not ({"action"} | box_keys[action] <= set(a) <= {"action"} | box_keys[action] | optional):
                return {"ok": False, "error": "Give exactly the fields required for this boxes action."}
            if action == "boxes":
                return rt.call(layout.boxes_inspect(rt.obs))
            return rt.call(layout.boxes_apply(rt.obs, action, a))
        extra_box_keys = set(a) - {"action", "preset", "corner"}
        if extra_box_keys:
            return {"ok": False, "error": "Not accepted here: " + ", ".join(sorted(extra_box_keys))}
        if action != "apply" and ("preset" in a or "corner" in a):
            return {"ok": False, "error": "preset and corner are used only with action 'apply'."}
        if action == "presets":
            return layout.describe(rt.obs.cfg)
        if action == "inspect":
            return rt.call(layout.inspect(rt.obs))
        if action == "apply":
            preset = a.get("preset")
            if preset not in layout.PRESETS:
                return {"ok": False, "applied": False, "error": "Give a preset: " + ", ".join(layout.PRESET_NAMES)}
            if "corner" in a and preset != "window_pip":
                return {"ok": False, "applied": False, "error": "corner applies only to the window_pip preset."}
            corner = a.get("corner", layout.DEFAULT_CORNER)
            if corner not in layout.CORNERS:
                return {"ok": False, "applied": False, "error": "corner: " + ", ".join(layout.CORNERS)}
            out = rt.call(layout.apply(rt.obs, preset, corner))
            out.setdefault("applied", False)   # an unreachable OBS is never an applied layout
            return out
        return {"ok": False, "error": "unknown action"}

    fns = {"onair_work_share": work_share, "onair_present_status": status, "onair_present_publish": publish, "onair_present_control": control,
           "onair_obs_setup": obs_setup, "onair_obs_scene": obs_scene, "onair_window_share": window,
           "onair_broadcast": broadcast, "onair_layout": arrange}
    return {name: guarded(name, fn) for name, fn in fns.items()}


def make_present_command(ctx: Any, rt: Runtime):
    def command(raw_args: str = "", **_kw: Any) -> str:
        ok, reason = owner_session(ctx)
        if not ok:
            return reason
        words = (raw_args or "").split()
        if words == ["work", "clear"]:
            ok, result = rt.program.work_action({"action": "clear"})
            if ok:
                rt._push_work(urgent=True)
                return f"Work cleared; revision {result['revision']}."
            return "Work could not be cleared."
        if words == ["work", "status"]:
            snap = rt.public()
            return f"Presentation mode: {snap['mode']}; revision {snap['revision']}."
        return "Use /onair-present work clear or /onair-present work status."
    return command


def _describe_stream(out: dict) -> str:
    s = out.get("stream") or {}
    if not out.get("connected", True) or not s:
        return "OBS stream state unknown: " + str(out.get("error") or "not reachable")
    return f"OBS reports the stream {s.get('state')}" + (f" ({s.get('timecode')})" if s.get("active") else "") + "."


def make_command(ctx: Any, rt: Runtime):
    """``/onair-broadcast [status|start <code>|stop]`` typed by the human in their own chat."""
    def command(raw_args: str = "", **_kw: Any) -> str:
        ok, reason = owner_session(ctx)
        if not ok:
            return reason
        words = (raw_args or "").split()
        verb = words[0].lower() if words else "status"
        if verb == "stop":
            try:
                out = rt.call(rt.obs.stop_stream(), 60)
            except TimeoutError:
                return "NOT confirmed stopped: OBS did not answer in time. Check OBS's Start/Stop Streaming button."
            return ("Stopped: " if out.get("ok") else "NOT confirmed stopped: ") + _describe_stream(out) + \
                ("" if out.get("ok") else " " + str(out.get("error") or ""))
        if verb == "revoke-window":
            try:
                out = rt.call(rt.obs.revoke_window("revoked by the owner with /onair-broadcast revoke-window"), 30)
            except TimeoutError:
                return "Window confirmation revoked, but OBS did not confirm the capture hidden. Hide it in OBS."
            if not out.get("ok"):
                return "Window confirmation revoked, but OBS did not confirm the capture hidden. Hide it in OBS."
            return ("Window confirmation revoked; the agent cannot show the operator-selected capture until you confirm "
                    "again." if out.get("revoked") else "There was no window confirmation to revoke.")
        if verb == "confirm-window":
            try:
                target = rt.call(rt.obs.window_confirmation_target(), 30)
            except TimeoutError:
                return "Nothing confirmed: OBS did not answer in time."
            if not target.get("ok"):
                return "Nothing confirmed: " + str(target.get("error") or "OBS is not reachable.")
            choice = ctx.ask_choice(
                f"Does the OBS input '{target['input']}' show only ONE window you chose for the show, not a whole "
                "screen or a private window? OBS cannot tell the agent what you picked; your answer is the only "
                "check. Check the OBS preview first.",
                [{"value": "confirm", "label": "Yes: one intended window"}, {"value": "cancel", "label": "No / cancel"}]) \
                if callable(getattr(ctx, "ask_choice", None)) else None
            if choice is None:
                return "This chat cannot show a confirmation card, so nothing was confirmed."
            if choice != "confirm":
                return "Cancelled. Nothing was confirmed; the capture stays off program."
            try:
                out = rt.call(rt.obs.confirm_window(target["fingerprint"]), 30)
            except TimeoutError:
                return "Nothing confirmed: OBS did not answer in time."
            if not out.get("ok"):
                return "Nothing confirmed: " + str(out.get("error") or "")
            return ("Confirmed: operator-selected capture (capture type NOT verified by software). The agent may now "
                    "show it; the confirmation ends if the input changes, is re-prepared or the chooser is reopened.")
        if verb == "start":
            if not rt.take_start_code(words[1] if len(words) > 1 else ""):
                return "No current go-live request has that code. Ask your agent for a new one. Nothing was started."
            choice = ctx.ask_choice("Start streaming from OBS to the destination configured in OBS? Viewers will see "
                                    "the current program.", [{"value": "go", "label": "Go live"},
                                                             {"value": "cancel", "label": "Cancel"}]) \
                if callable(getattr(ctx, "ask_choice", None)) else None
            if choice is None:
                return "This chat cannot show a confirmation card, so nothing was started. Start the stream in OBS yourself."
            if choice != "go":
                return "Cancelled. Nothing was started."
            try:
                out = rt.call(rt.obs.start_stream(), 60)
            except TimeoutError:  # bounded deadlines make this rare; an unanswered start is never "live"
                return "NOT confirmed live: OBS did not answer in time. Check OBS's Start/Stop Streaming button."
            return ("Live: " if out.get("ok") else "NOT live: ") + _describe_stream(out) + \
                ("" if out.get("ok") else " " + str(out.get("error") or ""))
        return _describe_stream(rt.call(rt.obs.stream_status()))
    return command


_RUNTIME: Runtime | None = None


def runtime() -> Runtime | None:
    return _RUNTIME


def register(ctx: Any, config: dict | None = None, client: Any = None) -> list:
    """Register the presentation tools and the go-live command. Serves nothing and contacts no OBS."""
    global _RUNTIME
    if _stage_home():
        return []
    obs, port = _config(ctx, config)
    if _RUNTIME is not None:
        _RUNTIME.teardown()
    rt = _RUNTIME = Runtime(obs, port, client, fallback_port=_port_is_default(ctx, config))
    done = []
    handlers = make_handlers(ctx, rt)
    if callable(getattr(ctx, "register_tool", None)):
        for name, schema in SCHEMAS.items():
            ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handlers[name],
                              description=schema["description"], emoji="🎬")
            done.append("tool:" + name)
        from ..preflight import note_owner_toolset
        note_owner_toolset(TOOLSET)  # preflight refuses to run the stage from a home that has these
    if callable(getattr(ctx, "register_command", None)):
        ctx.register_command("onair-broadcast", make_command(ctx, rt),
                             description="On Air broadcast: status, start <code> (you confirm), stop, "
                                         "confirm-window / revoke-window (your chosen OBS capture)",
                             args_hint="[status|start <code>|stop|confirm-window|revoke-window]")
        done.append("command:onair-broadcast")
        ctx.register_command("onair-present", make_present_command(ctx, rt),
                             description="Work view: clear or report status", args_hint="work [clear|status]")
        done.append("command:onair-present")
    return done


def teardown() -> None:
    global _RUNTIME
    rt, _RUNTIME = _RUNTIME, None
    if rt is not None:
        rt.teardown()
