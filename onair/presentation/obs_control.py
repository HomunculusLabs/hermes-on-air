"""OBS WebSocket v5 control for the presentation slice: owned sources, approved scenes, window share, broadcast.

Every operation opens its own short authenticated connection (``obs_protocol.Client``), acts, then READS BACK
what OBS reports. A request that OBS admitted is never reported as an observed effect: scene switches are
confirmed with ``GetCurrentProgramScene``, the broadcast with ``GetStreamStatus.outputActive``. If OBS cannot
be reached the result is ``connected: false`` and ``ok: false`` (unknown is not success).

Two different things are shared, and they are labelled differently everywhere:

- ``browser_source``: the agent's presentation page, loaded by an OBS Browser Source. It is a web page; it is
  NOT screen or window capture.
- ``os_window``: an OS window chosen by the HUMAN in OBS (or the system's chooser dialog) for one owned
  capture input. The agent can prepare that input, open the chooser and show/hide it, but it never picks a
  window and never reads window titles. Window-only kinds are shown once the human's selection produces frames.
  On Wayland, current OBS offers only the COMBINED ``pipewire-screen-capture-source`` whose system dialog can
  hand OBS a monitor OR a window and OBS does not say which. That kind is shown only as an
  ``operator_confirmed`` selection (``capture_type_verified: false``): the owner picks ONE intended window, then
  types ``/onair-broadcast confirm-window`` and confirms on the host card. The confirmation is bound to the
  input's UUID, kind and current settings and is revoked when this code prepares the input or reopens the
  chooser. The software cannot attest that a window, not a display, was picked.

Out of scope here: microphone/input mute automation (the earlier lane's unresolved policy), stream
destination/key settings (never read or written), recording, and creating anything outside the owned names.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit

from .obs_protocol import Client, ObsAuthError, ObsError, ObsRequestError
from . import ownership

BROWSER_KIND = "browser_source"
LINUX_CAPTURE_KINDS = {"pipewire-screen-capture-source", "pipewire-window-capture-source",
                       "pipewire-desktop-capture-source", "xcomposite_input", "xshm_input",
                       "xshm_input_v2", "v4l2_input", "pulse_input_capture"}


def _normalise_platform(reported: Any, kinds: set) -> str:
    if reported == "windows":
        return "windows"
    if reported in ("macos", "osx", "darwin"):
        return "macos"
    if isinstance(reported, str) and reported and LINUX_CAPTURE_KINDS.intersection(kinds):
        return "linux"
    return "unknown"


async def _obs_platform(c, version: dict | None = None) -> tuple:
    version = version if version is not None else await c.request("GetVersion")
    reported = version.get("platform")
    try:
        kinds = set((await c.request("GetInputKindList", {"unversioned": True})).get("inputKinds", []))
    except ObsRequestError:
        kinds = set()
    return _normalise_platform(reported, kinds), reported, kinds

# Window-only capture kinds by OBS platform, verified against obs-studio 7a25c3fd8b5eb87c2d2e0606cae36e32014bc6fa
# (plugins/win-capture/window-capture.c, plugins/mac-capture/mac-sck-video-capture.m,
#  plugins/linux-capture/xcomposite-input.c, plugins/linux-pipewire/screencast-portal.c).
WINDOW_KINDS: dict[str, list[dict]] = {
    "windows": [{"kind": "window_capture", "selector": "window", "chooser": "obs_properties"}],
    "macos": [{"kind": "screen_capture", "selector": "window", "chooser": "obs_properties",
               "type_key": "type", "window_type": 1, "create_settings": {"type": 1}},
              {"kind": "window_capture", "selector": "window", "chooser": "obs_properties"}],
    # X11 sessions only: plugins/linux-capture/linux-capture.c registers xcomposite/xshm only when the platform
    # is OBS_NIX_PLATFORM_X11_EGL, so an OBS running on Wayland never offers this kind.
    "linux": [{"kind": "xcomposite_input", "selector": "capture_window", "chooser": "obs_properties"}],
}
# Window-only kinds that OBS still LOADS from an older scene collection but no longer lets anyone CREATE:
# obs-studio 7a25c3fd registers them with OBS_SOURCE_CAP_OBSOLETE (== OBS_SOURCE_CAP_DISABLED, libobs/obs-source.h);
# obs-websocket e28f8497 leaves DISABLED kinds out of GetInputKindList and CreateInput refuses them (605).
# They are accepted only as an owned input that already exists in OBS, never created, never "available".
LEGACY_WINDOW_KINDS: dict[str, list[dict]] = {
    "linux": [{"kind": "pipewire-window-capture-source", "selector": "RestoreToken", "chooser": "portal",
               "button": "Reload", "legacy": True}],
}
# Kinds that can (or always do) capture a whole screen. Never used as a "window share", except the combined
# PipeWire kind on Linux, which _window_spec maps to COMBINED_SPEC (owner-confirmed, capture_type_verified False).
SCREEN_KINDS = {"monitor_capture", "display_capture", "xshm_input", "xshm_input_v2", "pipewire-desktop-capture-source",
                "pipewire-screen-capture-source", "game_capture", "dshow_input", "av_capture_input", "v4l2_input"}
# The one PipeWire kind current OBS lets a client create (screencast-portal.c, not OBSOLETE). Its system dialog
# offers monitors AND windows and OBS does not report which was picked. It is therefore used ONLY as an
# operator-selected source: shown after the owner's host-card confirmation, always labelled
# capture_type_verified: false. The agent never calls it a verified window-only source.
COMBINED_PIPEWIRE_KIND = "pipewire-screen-capture-source"
COMBINED_SPEC = {"kind": COMBINED_PIPEWIRE_KIND, "selector": "RestoreToken", "chooser": "portal", "button": "Reload",
                 "combined": True}
CONFIRM_COMMAND = "/onair-broadcast confirm-window"
NO_WINDOW_ONLY_KIND_WAYLAND = (
    "This OBS (Linux, Wayland/PipeWire) offers no window-only capture kind (the old window-only PipeWire kind is "
    f"obsolete and cannot be created). It offers the combined '{COMBINED_PIPEWIRE_KIND}', whose system dialog can "
    "hand OBS a whole monitor OR one window; OBS cannot tell the agent which. Action 'prepare' creates it as an "
    "operator-selected source (nothing is shown): you pick ONE dedicated window in the system dialog, check the OBS "
    f"preview, then type {CONFIRM_COMMAND} in your own Hermes and confirm on the card. Until then it is never put "
    "on program. Alternatives: the agent's Browser Source page (onair_present_publish), or OBS in an X11 session "
    "(window-only 'xcomposite_input').")
NO_WINDOW_KIND_AT_ALL = (
    "This OBS (Linux) lists no window capture kind a client can create (neither window-only 'xcomposite_input' "
    f"nor the combined '{COMBINED_PIPEWIRE_KIND}'), so the agent cannot prepare one. Put the work on the agent's "
    "Browser Source page (onair_present_publish) instead, or enable OBS's PipeWire/X11 capture plugin.")
MAX_DEADLINE_S = 25.0   # keeps a tool call (30 s) and the slash command (60 s) from timing out while OBS is polled

# macOS: the first Browser Source of an OBS process must not be created over WebSocket (see obs_bootstrap).
BOOTSTRAP_WHY = (
    "On macOS, OBS starts its browser engine (CEF) when the FIRST Browser Source of the process is created. If that "
    "happens through a WebSocket request, CEF starts on an obs-websocket worker thread and every Browser Source in "
    "that OBS process stays blank; creating it again over WebSocket cannot fix it. OBS must be a fresh process that "
    "loads a scene collection already holding the presentation Browser Source. Nothing in OBS was changed.")
RENDER_NOTE = ("Configuration was read back from OBS (kind, URL, audio). That is NOT render proof: an accepted request, "
               "matching settings or an active source do not show the page rendered. Render evidence is the page "
               "connecting back (onair_present_status surface.pages_connected > 0) plus the OBS preview.")

PREREQUISITES = {
    "obs_properties": ("In OBS, open the properties of the input '{input}' and pick ONE dedicated window to share "
                       "(for a first test, a demo window you opened for the show). The agent cannot pick it for you."),
    "portal": ("OBS asks the system's share dialog (xdg-desktop-portal) to choose. Pick ONE dedicated "
               "window, not a screen. If no dialog appeared, use action 'open_chooser' (it presses the input's "
               "Reload button) or press Reload in the input's properties in OBS."),
    "macos": ("macOS: OBS needs Screen Recording permission (System Settings > Privacy & Security > Screen & System "
              "Audio Recording) and the input's Method must be Window Capture."),
}
REQUEST_CODES = {204: "unknown request type", 207: "not ready", 300: "missing field", 400: "invalid field",
                 401: "invalid field type", 402: "field out of range",
                 500: "output already running", 501: "output not running", 504: "output disabled",
                 600: "not found", 601: "already exists", 602: "wrong resource type", 605: "invalid input kind",
                 700: "creation failed", 701: "action failed", 702: "processing failed", 703: "cannot act"}

# Fit the OWNED Browser Sources to the canvas. Request and readback fields as obs-websocket e28f8497 defines them
# (src/requesthandler/RequestHandler_Config.cpp GetVideoSettings; RequestHandler_SceneItems.cpp
# SetSceneItemTransform; src/utils/Obs_ObjectHelper.cpp GetSceneItemTransform). Every field sent exists since
# obs-websocket 5.0.0; cropToBounds (5.5+) is never sent. The canvas is the BASE resolution, never the output
# scaling. OBS_BOUNDS_SCALE_INNER scales the whole page to fit inside the bounds and keeps its aspect ratio.
FIT_BOUNDS_TYPE = "OBS_BOUNDS_SCALE_INNER"
ALIGN_TOP_LEFT = 5          # OBS_ALIGN_TOP | OBS_ALIGN_LEFT: the item's position is its top-left corner
ALIGN_CENTER = 0            # the fitted page is centred inside the bounds (letterbox/pillarbox if aspect differs)
MAX_CANVAS_PX = 16384
FIT_NOTE = ("The owned Browser Sources are fitted inside the OBS canvas (base resolution) with their aspect ratio kept; "
            "configure fits them again each time. Foreign items and owned sources in other scenes are not touched.")


def _canvas_dims(v: Any) -> tuple[int, int] | None:
    """Base (canvas) width/height from GetVideoSettings, or None when OBS did not report a usable size."""
    if not isinstance(v, dict):
        return None
    dims = []
    for key in ("baseWidth", "baseHeight"):
        x = v.get(key)
        if isinstance(x, bool) or not isinstance(x, (int, float)) or x != int(x) or not 1 <= x <= MAX_CANVAS_PX:
            return None
        dims.append(int(x))
    return dims[0], dims[1]


def _same_value(got: Any, want: Any) -> bool:
    if isinstance(want, str):
        return got == want
    return isinstance(got, (int, float)) and not isinstance(got, bool) and abs(float(got) - float(want)) <= 0.01


@dataclass
class ObsConfig:
    url: str = "ws://127.0.0.1:4455"
    password: Callable[[], str] = field(default=lambda: "", repr=False)   # read at call time, never returned
    presentation_scene: str = "On Air: Presentation"
    window_scene: str = "On Air: Shared window"
    extra_scenes: tuple[str, ...] = ()                                      # operator-approved existing scenes
    presentation_input: str = "On Air presentation (browser)"
    overlay_input: str = "On Air captions & voice (browser)"
    window_input: str = "On Air shared window (OS capture)"
    overlay_url: str = "http://127.0.0.1:18790/overlay"
    scene_view: bool = False
    scene_view_scene: str = field(default="On Air: 3D scene", init=False)
    scene_view_input: str = field(default="On Air 3D scene (browser)", init=False)
    width: int = 1920
    height: int = 1080
    start_deadline_s: float = 15.0
    stop_deadline_s: float = 15.0
    poll_s: float = 0.25

    def __post_init__(self) -> None:
        self.start_deadline_s = min(max(float(self.start_deadline_s), 0.1), MAX_DEADLINE_S)
        self.stop_deadline_s = min(max(float(self.stop_deadline_s), 0.1), MAX_DEADLINE_S)
        self.poll_s = min(max(float(self.poll_s), 0.02), 2.0)

    @property
    def approved_scenes(self) -> list[str]:
        return list(dict.fromkeys([self.presentation_scene, self.window_scene, "On Air: Boxes"] +
                                  ([self.scene_view_scene] if self.scene_view is True else []) +
                                  [s for s in self.extra_scenes if s]))

    @property
    def owned_scenes(self) -> list[str]:
        return [self.presentation_scene, self.window_scene] + ([self.scene_view_scene] if self.scene_view is True else [])


def _scene_page(overlay_url: str) -> str | None:
    """Derive the fixed page from a credential-free literal loopback origin."""
    try:
        u = urlsplit(overlay_url)
        loopback = u.hostname == "localhost" or ipaddress.ip_address(u.hostname or "").is_loopback
        if u.scheme not in ("http", "https") or not loopback or u.username or u.password:
            return None
        port = u.port  # validate port even when it is omitted
        if port is not None and not 1 <= port <= 65535:
            return None
        return f"{u.scheme}://{u.netloc}/scene/"
    except ValueError:
        return None


def _err(exc: BaseException) -> dict:
    if isinstance(exc, ObsAuthError):
        return {"connected": False, "error": "OBS rejected the password (check ONAIR_OBS_PASSWORD)."}
    if isinstance(exc, ObsRequestError):
        return {"connected": True, "error": f"OBS refused the request: {REQUEST_CODES.get(exc.code, 'failed')}"
                f" (code {exc.code})", "code": exc.code}
    if isinstance(exc, ValueError):
        return {"connected": False, "error": "The OBS address must be a loopback ws:// URL."}
    return {"connected": False, "error": "OBS is not reachable at the configured address (is OBS running with "
            "its WebSocket server enabled?)."}


_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _page_key(url: Any) -> tuple | None:
    try:
        u = urlsplit(str(url or ""))
        port = u.port or _DEFAULT_PORTS.get(u.scheme)
    except ValueError:
        return None
    host = "loopback" if u.hostname in _LOOPBACK_NAMES or (u.hostname or "").startswith("127.") else u.hostname
    return (u.scheme, host, port, u.path.rstrip("/"))


def _same_page(url: Any, want: str) -> bool:
    """Same page, treating localhost / 127.x / ::1 as one host and the scheme's default port as explicit."""
    a, b = _page_key(url), _page_key(want)
    return a is not None and a == b


def _unowned_overlay_message(name: str) -> str:
    return (f"OBS already has an input named {name!r} that this plugin has no record of creating "
            "(made by hand, by an earlier version, or under another Hermes home). In OBS, remove or rename "
            f"the input {name!r}, then run OBS setup again: it is recreated and recorded. Nothing was changed.")


def _overlay_collision_message(owned: str, others: list[str]) -> str:
    names = ', '.join(repr(name) for name in others)
    return (f"The captions overlay input {owned!r} conflicts with {names}. Remove the other input(s) in OBS, "
            "or rename them and change their URL away from the overlay, then run setup again. "
            "The plugin never deletes a source it did not create; renaming alone does not stop duplicate audio.")


def _public(state: dict) -> dict:
    """Drop internal keys (the confirmation fingerprint) from anything returned to a tool."""
    return {k: v for k, v in state.items() if not k.startswith("_")}


class ObsControl:
    def __init__(self, cfg: ObsConfig, presentation_url: Callable[[], str], client: Callable[..., Any] = Client):
        self.cfg = cfg
        self._presentation_url = presentation_url
        self._client = client
        self.last_stream: dict = {"state": "unknown", "observed_at": None}
        # Owner confirmation of the operator-selected (combined) input: a fingerprint of the input's UUID, kind and
        # settings, set ONLY by the host command after the owner's card (agent.make_command), never by a tool.
        self._confirmed: str | None = None
        self._revoked_because: str | None = None
        # The last layout onair_layout applied AND read back (layout.py). Never reported as current by itself:
        # inspect compares it with OBS readback every time.
        self.layout_last: dict | None = None
        # Serialize reads and writes using this controller, including confirmation checks.
        self._operation_lock = asyncio.Lock()

    # ---- plumbing -------------------------------------------------------------------------------------
    async def _run(self, op: Callable[[Any], Any]) -> dict:
        async with self._operation_lock:
            try:
                async with self._client(self.cfg.url, self.cfg.password()) as c:
                    out = await op(c)
                    out.setdefault("connected", True)
                    return out
            except (ObsError, ValueError, OSError) as exc:
                return {"ok": False, **_err(exc)}

    async def _program(self, c) -> str:
        return (await c.request("GetCurrentProgramScene")).get("currentProgramSceneName", "")

    async def _scenes(self, c) -> list[str]:
        return [s.get("sceneName") for s in (await c.request("GetSceneList")).get("scenes", [])]

    async def _inputs(self, c) -> dict[str, str]:
        return {i.get("inputName"): i.get("inputKind") for i in (await c.request("GetInputList")).get("inputs", [])}

    async def _input_uuid(self, c, name: str) -> str | None:
        inputs = (await c.request("GetInputList")).get("inputs", [])
        return next((i.get("inputUuid") for i in inputs if i.get("inputName") == name), None)

    async def _item(self, c, scene: str, source: str) -> dict | None:
        try:
            items = (await c.request("GetSceneItemList", {"sceneName": scene})).get("sceneItems", [])
        except ObsRequestError:
            return None
        return next((i for i in items if i.get("sourceName") == source), None)

    async def _ensure_item(self, c, scene: str, source: str, *, top: bool = False) -> None:
        item = await self._item(c, scene, source)
        if item is None:
            await c.request("CreateSceneItem", {"sceneName": scene, "sourceName": source, "sceneItemEnabled": True})
            item = await self._item(c, scene, source)
        if top and item is not None:
            count = len((await c.request("GetSceneItemList", {"sceneName": scene})).get("sceneItems", []))
            await c.request("SetSceneItemIndex", {"sceneName": scene, "sceneItemId": item["sceneItemId"],
                                                  "sceneItemIndex": count - 1})

    async def _stream(self, c) -> dict:
        s = await c.request("GetStreamStatus")
        out = {"state": "active" if s.get("outputActive") is True else "inactive" if s.get("outputActive") is False
               else "unknown", "active": s.get("outputActive"), "reconnecting": s.get("outputReconnecting"),
               "timecode": s.get("outputTimecode"), "bytes": s.get("outputBytes"),
               "skipped_frames": s.get("outputSkippedFrames"), "total_frames": s.get("outputTotalFrames"),
               "observed_at": round(time.time(), 3)}
        self.last_stream = out
        return out

    async def _fit_owned(self, c) -> dict:
        """Fit every owned Browser Source item in the owned scenes to the OBS base canvas, then read each back.

        ``fitted`` is True only when OBS reported a valid canvas AND every expected owned item exists AND each one
        read back the requested transform. Anything else is reported as not fitted, never as configured."""
        cfg = self.cfg
        out: dict[str, Any] = {"fitted": False, "canvas": None, "items": [], "missing": [],
                               "mode": "fit inside the canvas, aspect ratio kept, centred", "note": FIT_NOTE}
        try:
            video = await c.request("GetVideoSettings")
        except ObsRequestError as exc:
            out["error"] = (f"OBS did not report its canvas size ({REQUEST_CODES.get(exc.code, 'failed')}, code "
                            f"{exc.code}); the owned sources were NOT fitted.")
            return out
        dims = _canvas_dims(video)
        if dims is None:
            out["error"] = "OBS reported no valid canvas (base) resolution; the owned sources were NOT fitted."
            return out
        bw, bh = dims
        out["canvas"] = {"base_width": bw, "base_height": bh,
                         "from": "GetVideoSettings baseWidth/baseHeight (the canvas, not the output scaling)"}
        want = {"positionX": 0.0, "positionY": 0.0, "rotation": 0.0, "alignment": ALIGN_TOP_LEFT,
                "boundsType": FIT_BOUNDS_TYPE, "boundsAlignment": ALIGN_CENTER,
                "boundsWidth": float(bw), "boundsHeight": float(bh)}
        owned = (cfg.presentation_input, cfg.overlay_input) + ((cfg.scene_view_input,) if cfg.scene_view is True else ())
        expected = [(cfg.presentation_scene, cfg.presentation_input)] + [(s, cfg.overlay_input) for s in cfg.owned_scenes]
        if cfg.scene_view is True:
            expected.append((cfg.scene_view_scene, cfg.scene_view_input))
        for scene in cfg.owned_scenes:
            try:
                items = (await c.request("GetSceneItemList", {"sceneName": scene})).get("sceneItems", [])
            except ObsRequestError as exc:
                out["items"].append({"scene": scene, "source": None, "fitted": False,
                                     "error": f"scene items unreadable (code {exc.code})"})
                continue
            for it in items:
                name = it.get("sourceName")
                if name not in owned or not isinstance(it.get("sceneItemId"), int):
                    continue                                   # foreign item: never transformed
                if scene == cfg.window_scene and name == cfg.presentation_input:
                    continue                                   # a picture-in-picture inset: arranged by onair_layout
                if name == cfg.scene_view_input and scene != cfg.scene_view_scene:
                    continue
                if scene == cfg.scene_view_scene and name == cfg.presentation_input:
                    continue
                rec: dict[str, Any] = {"scene": scene, "source": name, "scene_item_id": it["sceneItemId"],
                                       "fitted": False}
                ref = {"sceneName": scene, "sceneItemId": it["sceneItemId"]}
                try:
                    await c.request("SetSceneItemTransform", {**ref, "sceneItemTransform": want})
                except ObsRequestError as exc:
                    rec["error"] = f"OBS refused the fit ({REQUEST_CODES.get(exc.code, 'failed')}, code {exc.code})"
                    out["items"].append(rec)
                    continue
                try:
                    tr = (await c.request("GetSceneItemTransform", ref)).get("sceneItemTransform") or {}
                except ObsRequestError as exc:
                    rec["error"] = f"the fit could not be read back (code {exc.code})"
                    out["items"].append(rec)
                    continue
                rec["readback"] = {k: tr.get(k) for k in (*want, "sourceWidth", "sourceHeight")}
                mismatch = [k for k, v in want.items() if not _same_value(tr.get(k), v)]
                if mismatch:
                    rec["mismatch"] = mismatch
                    rec["error"] = "OBS read back a different transform"
                else:
                    rec["fitted"] = True
                out["items"].append(rec)
        done = {(r["scene"], r["source"]) for r in out["items"] if r["fitted"]}
        out["missing"] = [[s, n] for s, n in expected if (s, n) not in {(r["scene"], r["source"]) for r in out["items"]}]
        out["fitted"] = bool(out["items"]) and all(r["fitted"] for r in out["items"]) and not out["missing"] \
            and all(e in done for e in expected)
        if not out["fitted"]:
            out["error"] = ("Not every owned item was confirmed fitted to the canvas" +
                            (f" (missing: {', '.join(n + ' in ' + s for s, n in out['missing'])})" if out["missing"]
                             else "") + "; the program may show a cropped page.")
        return out

    async def _sources(self, c, inputs: dict[str, str]) -> dict:
        """Owned browser sources plus every browser source that points at the voice overlay."""
        cfg, pres_url = self.cfg, self._presentation_url()
        overlay_clients, report = [], {}
        for name, kind in inputs.items():
            if kind != BROWSER_KIND:
                continue
            settings = (await c.request("GetInputSettings", {"inputName": name})).get("inputSettings", {})
            audio = settings.get("reroute_audio") is True
            if _same_page(settings.get("url"), cfg.overlay_url):
                overlay_clients.append({"input": name, "audio_via_obs": audio})
            if name == cfg.presentation_input:
                report["presentation"] = {"exists": True, "kind": kind, "label": "Browser Source (web page, not screen capture)",
                                          "url_matches": bool(pres_url) and _same_page(settings.get("url"), pres_url),
                                          "audio_via_obs": audio}
            if name == cfg.overlay_input:
                report["overlay"] = {"exists": True, "kind": kind, "url_matches": _same_page(settings.get("url"), cfg.overlay_url),
                                     "audio_via_obs": audio}
        for key, name in (("presentation", cfg.presentation_input), ("overlay", cfg.overlay_input)):
            if key not in report:
                report[key] = {"exists": name in inputs, "kind": inputs.get(name),
                               **({"error": "an input with this name exists but is not a Browser Source"} if name in inputs else {})}
        # One speaking overlay only: every overlay page plays the voice, so a second client doubles speech.
        others = [o["input"] for o in overlay_clients if o["input"] != cfg.overlay_input]
        voice_ok = bool(report["overlay"].get("url_matches") and report["overlay"].get("audio_via_obs")) and not others
        report["speech_audio"] = {"ok": voice_ok, "overlay_clients": len(overlay_clients), "extra_overlay_clients": others,
                                  "presentation_audio_off": report["presentation"].get("audio_via_obs") is False,
                                  "note": ("Exactly one overlay Browser Source carries the voice." if voice_ok else
                                           "Voice would be missing or doubled: keep ONE overlay Browser Source (the "
                                           "configured one, with 'Control audio via OBS' on) and remove the others.")}
        return report

    # ---- read ----------------------------------------------------------------------------------------
    async def inspect(self) -> dict:
        async def op(c):
            v = await c.request("GetVersion")
            platform, reported, _ = await _obs_platform(c, v)
            inputs = await self._inputs(c)
            scenes = await self._scenes(c)
            src = await self._sources(c, inputs)
            out = {"ok": True, "obs": {"obs_version": v.get("obsVersion"), "websocket_version": v.get("obsWebSocketVersion"),
                                       "platform": platform, "platform_reported": reported},
                   "program_scene": await self._program(c), "approved_scenes": self.cfg.approved_scenes,
                   "scenes_present": {s: s in scenes for s in self.cfg.approved_scenes},
                   "sources": src, "stream": await self._stream(c)}
            out["startup_bootstrap_required"] = platform == "macos" and BROWSER_KIND not in inputs.values()
            out["presentation_ready"] = bool(all(out["scenes_present"][s] for s in self.cfg.owned_scenes[:1])
                                             and src["presentation"].get("url_matches") and src["speech_audio"]["ok"]
                                             and src["speech_audio"]["presentation_audio_off"])
            return out
        return await self._run(op)

    # ---- configure owned sources (never switches program, never starts output) -------------------------
    async def configure(self) -> dict:
        cfg, pres_url = self.cfg, self._presentation_url()
        if not pres_url:
            return {"ok": False, "error": "The presentation surface is not serving; publish a deck or use action 'serve' first."}
        scene_url = _scene_page(cfg.overlay_url) if cfg.scene_view is True else None
        if cfg.scene_view is True and not scene_url:
            return {"ok": False, "configured": False, "error": "The 3D scene requires a credential-free loopback overlay URL; nothing was changed."}
        if cfg.scene_view is True and (cfg.scene_view_scene in (cfg.presentation_scene, cfg.window_scene) or
                                       cfg.scene_view_input in (cfg.presentation_input, cfg.overlay_input, cfg.window_input)):
            return {"ok": False, "configured": False, "error": "The 3D scene names collide with another configured scene or input; nothing was changed."}

        async def op(c):
            platform, reported, _ = await _obs_platform(c)
            inputs = await self._inputs(c)
            existing_sources = await self._sources(c, inputs)
            conflicts = existing_sources['speech_audio']['extra_overlay_clients']
            if conflicts:
                instruction = _overlay_collision_message(cfg.overlay_input, conflicts)
                return {'ok': False, 'configured': False, 'error': instruction,
                        'next_human_action': instruction, 'sources': existing_sources}
            for name in (cfg.presentation_input, cfg.overlay_input):
                if name in inputs and inputs[name] != BROWSER_KIND:
                    return {"ok": False, "error": f"An existing input named {name!r} is not a Browser Source; "
                            "rename it in OBS or configure a different owned name. Nothing was changed."}
            if cfg.scene_view is True and cfg.overlay_input in inputs:
                current_uuid = await self._input_uuid(c, cfg.overlay_input)
                if not current_uuid or ownership.read('input:' + cfg.overlay_input) != {'uuid': current_uuid}:
                    return {'ok': False, 'configured': False, 'error': _unowned_overlay_message(cfg.overlay_input),
                            'next_human_action': _unowned_overlay_message(cfg.overlay_input)}
            if platform == "macos" and BROWSER_KIND not in inputs.values():
                return {"ok": False, "configured": False, "bootstrap_required": True, "platform": platform,
                        "platform_reported": reported,
                        "why": BOOTSTRAP_WHY, "next_agent_action": "onair_obs_setup action 'prepare_startup'",
                        "error": "OBS needs a startup collection first (macOS); nothing was created."}
            scenes = await self._scenes(c)
            if cfg.scene_view is True:
                scene_exists = cfg.scene_view_scene in scenes
                input_exists = cfg.scene_view_input in inputs
                if scene_exists != input_exists:
                    return {"ok": False, "configured": False, "error": "A 3D scene or input name is already in use; nothing was changed."}
                if scene_exists and not await self._scene_view_safe(c):
                    return {"ok": False, "configured": False, "error": "The 3D scene is not the verified plugin-owned view; rename or remove it in OBS. Nothing was changed."}
            new_scene_uuid = None
            for scene in cfg.owned_scenes:
                if scene not in scenes:
                    created = await c.request("CreateScene", {"sceneName": scene})
                    if scene == cfg.scene_view_scene and cfg.scene_view is True:
                        new_scene_uuid = created.get('sceneUuid')
            base = {"width": cfg.width, "height": cfg.height}
            wanted = ((cfg.presentation_input, {**base, "url": pres_url, "reroute_audio": False}),
                      (cfg.overlay_input, {**base, "url": cfg.overlay_url, "reroute_audio": True}))
            for name, settings in wanted:
                if name in inputs:
                    await c.request("SetInputSettings", {"inputName": name, "inputSettings": settings, "overlay": True})
                else:
                    created_input = await c.request("CreateInput", {"sceneName": cfg.presentation_scene, "inputName": name,
                                                    "inputKind": BROWSER_KIND, "inputSettings": settings,
                                                    "sceneItemEnabled": True})
                    if name == cfg.overlay_input:
                        input_uuid = created_input.get('inputUuid')
                        if not input_uuid or input_uuid != await self._input_uuid(c, name):
                            return {'ok': False, 'configured': False, 'error': 'OBS did not confirm the new overlay identity; rename or remove it before retrying.'}
                        ownership.write('input:' + name, {'uuid': input_uuid})
            await self._ensure_item(c, cfg.presentation_scene, cfg.presentation_input)
            new_input = None
            if cfg.scene_view is True and cfg.scene_view_input not in inputs:
                new_input = await c.request("CreateInput", {"sceneName": cfg.scene_view_scene, "inputName": cfg.scene_view_input,
                                                "inputKind": BROWSER_KIND,
                                                "inputSettings": {**base, "url": scene_url, "reroute_audio": False},
                                                "sceneItemEnabled": True})
            new_overlay = None
            for scene in cfg.owned_scenes:
                if scene == cfg.scene_view_scene and new_input is not None:
                    new_overlay = await c.request('CreateSceneItem', {'sceneName': scene, 'sourceName': cfg.overlay_input,
                                                                       'sceneItemEnabled': True})
                else:
                    await self._ensure_item(c, scene, cfg.overlay_input, top=True)
            if new_input is not None:
                overlay_uuid = await self._input_uuid(c, cfg.overlay_input)
                record = {'scene_uuid': new_scene_uuid, 'items': [
                    {'name': cfg.scene_view_input, 'uuid': new_input.get('inputUuid'), 'item': new_input.get('sceneItemId')},
                    {'name': cfg.overlay_input, 'uuid': overlay_uuid, 'item': (new_overlay or {}).get('sceneItemId')}]}
                observed = await self._scene_view_record(c)
                if observed:
                    for intended, actual in zip(record['items'], observed['items']):
                        intended['settings'] = actual['settings']
                if not new_scene_uuid or observed is None or observed != record:
                    return {'ok': False, 'configured': False, 'error': 'OBS did not confirm the newly created 3D identities; rename or remove the scene and input before retrying.'}
                ownership.write(cfg.scene_view_scene, record)
            fit = await self._fit_owned(c)
            src = await self._sources(c, await self._inputs(c))
            sources_ok = bool(src["presentation"].get("url_matches") and src["overlay"].get("url_matches")
                              and src["presentation"].get("audio_via_obs") is False)
            if cfg.scene_view is True:
                scene_settings = (await c.request("GetInputSettings", {"inputName": cfg.scene_view_input})).get("inputSettings", {})
                scene_items = (await c.request("GetSceneItemList", {"sceneName": cfg.scene_view_scene})).get("sceneItems", [])
                names = [item.get("sourceName") for item in scene_items if item.get("sceneItemEnabled") is True]
                scene_ok = (scene_settings.get("url") == scene_url and scene_settings.get("reroute_audio") is False
                            and len(names) == 2 and names == [cfg.scene_view_input, cfg.overlay_input])
                sources_ok = sources_ok and scene_ok
            ok = sources_ok and fit["fitted"] is True
            out = {"ok": ok, "configured": ok, "sources_configured": sources_ok, "platform": platform,
                   "platform_reported": reported, "sources": src,
                   "canvas": fit["canvas"], "fit": fit, "program_scene": await self._program(c),
                   "render": {"verified": False, "note": RENDER_NOTE},
                   "note": "Configured the owned Browser Sources; the program scene and outputs were not changed."}
            if not sources_ok and cfg.scene_view is True:
                out["error"] = "OBS did not confirm the requested 3D scene sources, enabled state and order."
            if sources_ok and not fit["fitted"]:
                out["error"] = ("The owned Browser Sources are set but NOT confirmed fitted to the OBS canvas: "
                                + fit.get("error", "unconfirmed") + " Nothing else was changed.")
            return out
        return await self._run(op)

    async def _scene_view_record(self, c) -> dict | None:
        cfg = self.cfg
        url = _scene_page(cfg.overlay_url)
        if not url:
            return None
        scene_id = await ownership.scene_uuid(c, cfg.scene_view_scene)
        if not isinstance(scene_id, str) or not scene_id:
            return None
        items = (await c.request('GetSceneItemList', {'sceneName': cfg.scene_view_scene})).get('sceneItems', [])
        names = [cfg.scene_view_input, cfg.overlay_input]
        if (len(items) != 2 or [i.get('sourceName') for i in items] != names or
                any(i.get('sceneItemEnabled') is not True or not isinstance(i.get('sceneItemId'), int) for i in items)):
            return None
        inputs = (await c.request('GetInputList')).get('inputs', [])
        identities = []
        for item, name, expected_url, audio in zip(items, names, (url, cfg.overlay_url), (False, True)):
            entry = next((i for i in inputs if i.get('inputName') == name), None)
            if not entry or entry.get('inputKind') != BROWSER_KIND or not entry.get('inputUuid'):
                return None
            if item.get('sourceUuid') != entry['inputUuid']:
                return None
            if name == cfg.overlay_input and ownership.read('input:' + name) != {'uuid': entry['inputUuid']}:
                return None
            settings = (await c.request('GetInputSettings', {'inputName': name})).get('inputSettings', {})
            if (settings.get('url') != expected_url or settings.get('reroute_audio') is not audio or
                    settings.get('width') != cfg.width or settings.get('height') != cfg.height):
                return None
            identities.append({'name': name, 'uuid': entry['inputUuid'], 'item': item['sceneItemId'], 'settings': settings})
        return {'scene_uuid': scene_id, 'items': identities}

    async def _scene_view_safe(self, c) -> bool:
        try:
            observed = await self._scene_view_record(c)
            sources = await self._sources(c, await self._inputs(c))
            return (observed is not None and observed == ownership.read(self.cfg.scene_view_scene)
                    and sources['speech_audio']['ok'] is True)
        except ObsRequestError:
            return False

    async def _safe_scene_contents(self, c, scene: str) -> dict:
        """Reject unverified captures and nested/unknown sources even in approved scenes."""
        items = (await c.request('GetSceneItemList', {'sceneName': scene})).get('sceneItems')
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            return {'ok': False, 'error': 'OBS did not report the scene contents; nothing was switched.'}
        inputs = await self._inputs(c)
        names = [item.get('sourceName') for item in items]
        if scene in (self.cfg.presentation_scene, self.cfg.window_scene):
            allowed = ({self.cfg.presentation_input, self.cfg.overlay_input} if scene == self.cfg.presentation_scene
                       else {self.cfg.presentation_input, self.cfg.overlay_input, self.cfg.window_input})
            if len(set(names)) != len(names) or any(name not in allowed for name in names):
                return {'ok': False, 'error': 'The scene has foreign or duplicate items; nothing was switched.'}
            for name in names:
                if name in (self.cfg.presentation_input, self.cfg.overlay_input):
                    expected = self._presentation_url() if name == self.cfg.presentation_input else self.cfg.overlay_url
                    settings = (await c.request('GetInputSettings', {'inputName': name})).get('inputSettings', {})
                    audio = name == self.cfg.overlay_input
                    if inputs.get(name) != BROWSER_KIND or not expected or settings.get('url') != expected or settings.get('reroute_audio') is not audio:
                        return {'ok': False, 'error': 'A scene browser source no longer matches the configured page; nothing was switched.'}
        window = None
        for item in items:
            name = item.get('sourceName')
            kind = inputs.get(name)
            input_uuid = await self._input_uuid(c, name)
            if not input_uuid or item.get('sourceUuid') != input_uuid:
                return {'ok': False, 'error': 'A scene item source identity cannot be verified; nothing was switched.'}
            if name == self.cfg.window_input:
                if scene not in (self.cfg.window_scene, 'On Air: Boxes'):
                    return {'ok': False, 'error': 'A window capture is present in an unverified scene; nothing was switched.'}
                if window is None:
                    window = await self._window_state(c)
                if not window.get('ready'):
                    return {'ok': False, 'error': 'The scene contains an unconfirmed window capture.',
                            'next_human_action': window.get('next_human_action')}
            elif kind not in (BROWSER_KIND, 'image_source', 'color_source', 'color_source_v3', 'text_gdiplus_v2', 'text_ft2_source_v2'):
                return {'ok': False, 'error': 'The scene contains a capture, nested scene, or unverified source; nothing was switched.'}
        return {'ok': True}

    async def _switch_guard(self, c, scene: str) -> dict:
        if scene == self.cfg.scene_view_scene:
            if self.cfg.scene_view is not True or not await self._scene_view_safe(c):
                return {'ok': False, 'error': 'The 3D scene ownership, sources, settings or order cannot be verified. Rename or remove the conflicting scene and configure it again.'}
        if scene == 'On Air: Boxes':
            from .boxes import safe_to_show
            guard = await safe_to_show(self, c)
            if not guard.get('ok'):
                return guard
        if scene == self.cfg.window_scene:
            state = await self._window_state(c)
            if not state['ready']:
                return {'ok': False, 'window': _public(state), 'next_human_action': state.get('next_human_action'),
                        'error': 'The shared-window scene shows a capture that is not ready.'}
        return await self._safe_scene_contents(c, scene)

    async def _rollback_switch(self, c, scene: str, previous: str, detail: str = '') -> dict:
        # OBS has no atomic switch-and-check. Restore only a guarded, stable scene.
        try:
            for fallback in dict.fromkeys((previous, self.cfg.presentation_scene)):
                if fallback == scene or fallback not in self.cfg.approved_scenes:
                    continue
                identity = await ownership.scene_uuid(c, fallback)
                if not identity or not (await self._switch_guard(c, fallback)).get('ok'):
                    continue
                await c.request('SetCurrentProgramScene', {'sceneName': fallback})
                observed = await self._program(c)
                if (observed == fallback and (await self._switch_guard(c, fallback)).get('ok') and
                        identity == await ownership.scene_uuid(c, fallback)):
                    return {'ok': False, 'program_scene': observed, 'requested': scene,
                            'error': 'Scene contents or identity changed during the switch; a verified safe scene was restored.' + detail}
            observed = await self._program(c)
            return {'ok': False, 'program_scene': observed, 'requested': scene,
                    'error': 'Scene contents or identity changed during the switch; no safe rollback was confirmed. Check OBS immediately.' + detail}
        except (ObsError, OSError, ValueError):
            return {'ok': False, 'connected': False, 'program_scene': None, 'requested': scene,
                    'error': 'Scene contents or identity changed during the switch; OBS could not confirm rollback or the current program scene. Check OBS immediately.' + detail}

    # ---- program scene -------------------------------------------------------------------------------
    async def set_scene(self, scene: str) -> dict:
        if scene not in self.cfg.approved_scenes:
            return {"ok": False, "error": "Not an approved scene.", "approved_scenes": self.cfg.approved_scenes}

        async def op(c):
            previous = await self._program(c)
            scene_identity = await ownership.scene_uuid(c, scene)
            if not scene_identity:
                return {'ok': False, 'program_scene': previous, 'requested': scene,
                        'error': 'OBS did not confirm the scene identity; nothing was switched.'}
            guard = await self._switch_guard(c, scene)
            if not guard.get('ok'):
                return {**guard, 'program_scene': previous, 'requested': scene}
            await c.request("SetCurrentProgramScene", {"sceneName": scene})
            now = await self._program(c)
            window_failure = ''
            capture_item = await self._item(c, scene, self.cfg.window_input) if scene in (self.cfg.window_scene, 'On Air: Boxes') else None
            if capture_item:
                latest = await self._window_state(c)
                if (not latest.get('ready') or
                        (latest.get('_fingerprint') is not None and latest['_fingerprint'] != self._confirmed)):
                    warning = ''
                    try:
                        item = await self._item(c, scene, self.cfg.window_input)
                        if item:
                            await c.request('SetSceneItemEnabled', {'sceneName': scene, 'sceneItemId': item['sceneItemId'],
                                                                   'sceneItemEnabled': False})
                            item = await self._item(c, scene, self.cfg.window_input)
                        if not item or item.get('sceneItemEnabled') is not False:
                            warning = ' OBS did not confirm it hidden; hide it in OBS.'
                    except (ObsError, OSError, ValueError):
                        warning = ' OBS did not confirm it hidden; hide it in OBS.'
                    window_failure = ' Window authorization or readiness changed during the switch.' + warning
            try:
                verified = await self._switch_guard(c, scene) if not window_failure else {'ok': False}
                if scene_identity != await ownership.scene_uuid(c, scene):
                    verified = {'ok': False}
            except (ObsError, OSError, ValueError):
                verified = {'ok': False}
            if not verified.get('ok'):
                return await self._rollback_switch(c, scene, previous, window_failure)
            return {"ok": now == scene, "program_scene": now, "requested": scene,
                    **({} if now == scene else {"error": "OBS did not report the requested program scene."})}
        return await self._run(op)

    # ---- selected OS window --------------------------------------------------------------------------
    def _window_spec(self, platform: str, kind: str | None) -> dict | None:
        if platform == "linux" and kind == COMBINED_PIPEWIRE_KIND:
            return COMBINED_SPEC
        return next((k for k in WINDOW_KINDS.get(platform, []) + LEGACY_WINDOW_KINDS.get(platform, [])
                     if k["kind"] == kind), None)

    @staticmethod
    def _no_kind_reason(platform: str, kinds: set) -> str:
        if platform == "linux":
            return NO_WINDOW_KIND_AT_ALL
        return ("OBS reports none of the window-only capture kinds for this platform "
                f"({', '.join(k['kind'] for k in WINDOW_KINDS[platform])}); the agent cannot create a window share "
                "here. Put the work on the agent's Browser Source page (onair_present_publish) instead.")

    # ---- owner confirmation of an operator-selected (combined) input ---------------------------------
    @staticmethod
    def _fingerprint(uuid: Any, kind: Any, settings: dict) -> str:
        blob = json.dumps({"uuid": uuid, "kind": kind, "settings": settings}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def revoke_window_confirmation(self, why: str) -> bool:
        had, self._confirmed = self._confirmed is not None, None
        if had:
            self._revoked_because = why
        return had

    async def revoke_window(self, why: str) -> dict:
        """Invalidate first, then serialize hiding with other controller operations."""
        had = self.revoke_window_confirmation(why)
        async def op(c):
            problems = []
            scenes = await self._scenes(c)
            for scene in (self.cfg.window_scene, "On Air: Boxes"):
                if scene not in scenes:
                    continue
                try:
                    items = (await c.request('GetSceneItemList', {'sceneName': scene})).get('sceneItems', [])
                    matches = [item for item in items if item.get('sourceName') == self.cfg.window_input]
                    if len(matches) > 1:
                        problems.append(scene)
                        continue
                    item = matches[0] if matches else None
                    if item and scene == 'On Air: Boxes':
                        from .boxes import _read
                        if not (await _read(self, c))['owned']:
                            problems.append(scene)
                            continue
                    if item and item.get("sceneItemEnabled") is True:
                        await c.request("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item["sceneItemId"],
                                                                "sceneItemEnabled": False})
                        observed = (await c.request('GetSceneItemList', {'sceneName': scene})).get('sceneItems', [])
                        hidden = next((i for i in observed if i.get('sceneItemId') == item['sceneItemId']), None)
                        if not hidden or hidden.get('sceneItemEnabled') is not False:
                            problems.append(scene)
                    elif item and item.get('sceneItemEnabled') is not False:
                        problems.append(scene)
                except (ObsError, OSError, ValueError):
                    problems.append(scene)
            return {"ok": not problems, "revoked": had, "unconfirmed_hidden": problems}
        return await self._run(op)

    async def _window_state(self, c) -> dict:
        cfg = self.cfg
        platform, reported, kinds = await _obs_platform(c)
        # Only kinds OBS both lists and lets a client create count as available for 'prepare'.
        available = [k["kind"] for k in WINDOW_KINDS.get(platform, []) if k["kind"] in kinds]
        combined_creatable = platform == "linux" and COMBINED_PIPEWIRE_KIND in kinds and not available
        creatable = available[0] if available else (COMBINED_PIPEWIRE_KIND if combined_creatable else None)
        inputs = await self._inputs(c)
        kind = inputs.get(cfg.window_input)
        out: dict[str, Any] = {"label": "Shared window (OS window capture, chosen by you in OBS)", "share": "os_window",
                               "platform": platform, "platform_reported": reported, "window_kinds_available": available,
                               "can_create_input": creatable is not None, "creatable_kind": creatable,
                               "input": cfg.window_input, "exists": kind is not None, "kind": kind,
                               "legacy_input": False, "selection": None, "capture_type_verified": None,
                               "operator_confirmed": False, "selected": False, "producing_frames": None,
                               "ready": False, "next_human_action": None, "prerequisites": []}
        if not WINDOW_KINDS.get(platform):
            out["prerequisites"].append(
                "No capture kind is supported for this OBS platform. The supported-platform kinds are "
                "window_capture, screen_capture, xcomposite_input and pipewire-screen-capture-source; "
                "use a supported Windows, macOS or Linux OBS setup with its capture plugin, or use the "
                "Browser Source page (onair_present_publish) instead.")
            return out
        if kind is None:
            if available:
                out["prerequisites"].append("Use action 'prepare' to create the owned window-capture input.")
            elif combined_creatable:
                out["prerequisites"].append(NO_WINDOW_ONLY_KIND_WAYLAND)
                out["next_human_action"] = ("Ask the agent to prepare the window share, then select one window in the "
                                            "system dialog, check the OBS preview, and confirm with "
                                            f"{CONFIRM_COMMAND} in your own Hermes.")
            else:
                out["prerequisites"].append(self._no_kind_reason(platform, kinds))
            return out
        spec = self._window_spec(platform, kind)
        if spec is None:
            out["can_create_input"] = False
            out["error"] = ("The owned input is not a window-only capture kind" +
                            (" (it can capture a whole screen)" if kind in SCREEN_KINDS else "") + "; it will not be shown.")
            out["prerequisites"].append(f"Delete or rename '{cfg.window_input}' in OBS" +
                                        (", then use action 'prepare'." if creatable else "."))
            if not creatable:
                out["prerequisites"].append(self._no_kind_reason(platform, kinds))
            return out
        combined = bool(spec.get("combined"))
        out["selection"] = "operator_selected_unconfirmed" if combined else "window_only_kind"
        out["capture_type_verified"] = not combined   # window-only by OBS kind; combined: OBS cannot tell
        if spec.get("legacy"):
            out["legacy_input"] = True
            out["label"] = ("Shared window (OS window capture through an older window-only PipeWire input that "
                            "already existed in OBS; chosen by you in the system dialog)")
        if combined:
            out["label"] = ("Operator-selected capture (combined PipeWire screen/window source: OBS cannot tell the "
                            "agent whether a window or a whole monitor was picked; shown only after you confirm)")
        settings = (await c.request("GetInputSettings", {"inputName": cfg.window_input})).get("inputSettings", {})
        value = settings.get(spec["selector"])
        selected = bool(value) and value not in (0, "0")
        if spec.get("type_key") and settings.get(spec["type_key"]) != spec["window_type"]:
            selected = False
            out["prerequisites"].append("Set the input's Method to Window Capture in OBS.")
        item = await self._item(c, cfg.window_scene, cfg.window_input)
        if item is not None:
            tr = (await c.request("GetSceneItemTransform", {"sceneName": cfg.window_scene,
                                                            "sceneItemId": item["sceneItemId"]})).get("sceneItemTransform", {})
            out["producing_frames"] = bool((tr.get("sourceWidth") or 0) > 0 and (tr.get("sourceHeight") or 0) > 0)
        else:
            out["prerequisites"].append("The owned window input is not in the shared-window scene; use action 'prepare'.")
        if spec["chooser"] == "portal" and out["producing_frames"]:
            selected = True  # the portal hands OBS a stream only after the human picked something
        out["selected"] = selected
        out["chooser"] = spec["chooser"]
        if not selected or not out["producing_frames"]:
            out["prerequisites"].append(PREREQUISITES[spec["chooser"]].format(input=cfg.window_input))
            if platform == "macos":
                out["prerequisites"].append(PREREQUISITES["macos"])
        out["ready"] = bool(selected and out["producing_frames"] and item is not None)
        if combined:
            fp = self._fingerprint(await self._input_uuid(c, cfg.window_input), kind, settings)
            confirmed = self._confirmed is not None and self._confirmed == fp
            if self._confirmed is not None and not confirmed:
                self.revoke_window_confirmation("the input's identity or settings changed in OBS")
            out["operator_confirmed"] = confirmed
            out["selection"] = "operator_confirmed" if confirmed else "operator_selected_unconfirmed"
            out["_fingerprint"] = fp
            if self._revoked_because and not confirmed:
                out["confirmation_revoked_because"] = self._revoked_because
            if not confirmed:
                out["ready"] = False
                out["next_human_action"] = (
                    ("Pick ONE dedicated window (not a screen) in the system share dialog on the OBS machine. " if
                     not out["producing_frames"] else "") +
                    f"Check in the OBS preview that '{cfg.window_input}' shows only that one window, then type "
                    f"{CONFIRM_COMMAND} in your own Hermes and confirm on the card. The agent cannot verify a "
                    "window was picked rather than a monitor, and will not show it before you confirm.")
                out["prerequisites"].append(out["next_human_action"])
        elif not out["ready"] and out["prerequisites"]:
            out["next_human_action"] = out["prerequisites"][-1]
        return out

    async def window_discover(self) -> dict:
        async def op(c):
            return {"ok": True, "window": _public(await self._window_state(c))}
        return await self._run(op)

    async def window_prepare(self) -> dict:
        cfg = self.cfg

        async def op(c):
            state = await self._window_state(c)
            if state.get("error"):
                return {"ok": False, "window": _public(state), "error": state["error"]}
            revoked = self.revoke_window_confirmation("action 'prepare' reconfigured the input")
            if not state["exists"]:
                if not state["creatable_kind"]:
                    return {"ok": False, "window": _public(state), "error": "No window capture kind can be created in "
                            "this OBS; nothing was created. See prerequisites."}
                kind = state["creatable_kind"]
                spec = self._window_spec(state["platform"], kind) or {}
                if cfg.window_scene not in await self._scenes(c):
                    await c.request("CreateScene", {"sceneName": cfg.window_scene})
                await c.request("CreateInput", {"sceneName": cfg.window_scene, "inputName": cfg.window_input,
                                                "inputKind": kind, "inputSettings": dict(spec.get("create_settings", {})),
                                                "sceneItemEnabled": True})
            else:
                await self._ensure_item(c, cfg.window_scene, cfg.window_input)
            if cfg.overlay_input in await self._inputs(c):
                await self._ensure_item(c, cfg.window_scene, cfg.overlay_input, top=True)
            state = await self._window_state(c)
            return {"ok": state["exists"] and not state.get("error"), "window": _public(state),
                    "confirmation_revoked": revoked,
                    "note": "The input exists; a human still chooses the window. Nothing is on program yet." +
                            (f" This source can capture a monitor or a window; after choosing, the owner must type "
                             f"{CONFIRM_COMMAND}." if state.get("selection", "").startswith("operator") else "")}
        return await self._run(op)

    async def window_open_chooser(self) -> dict:
        async def op(c):
            state = await self._window_state(c)
            spec = self._window_spec(state["platform"], state["kind"])
            if not spec or spec["chooser"] != "portal":
                return {"ok": False, "window": _public(state), "error": "This input has no system chooser; pick the "
                        "window in the input's properties in OBS."}
            revoked = self.revoke_window_confirmation("the system chooser was reopened")
            await c.request("PressInputPropertiesButton", {"inputName": self.cfg.window_input, "propertyName": spec["button"]})
            state = await self._window_state(c)
            return {"ok": True, "window": _public(state), "confirmation_revoked": revoked,
                    "note": "Asked OBS to open the system chooser on the OBS machine; a human must pick ONE window "
                            "there. Opening it authorizes nothing" +
                            (f"; the owner confirms afterwards with {CONFIRM_COMMAND}." if spec.get("combined") else ".")}
        return await self._run(op)

    async def window_show(self) -> dict:
        cfg = self.cfg

        async def op(c):
            state = await self._window_state(c)
            if not state["ready"]:
                return {"ok": False, "window": _public(state), "next_human_action": state.get("next_human_action"),
                        "error": "The shared window is not ready; see next_human_action/prerequisites. "
                                 "Nothing was put on program."}
            previous = await self._program(c)
            scene_identity = await ownership.scene_uuid(c, cfg.window_scene)
            if not scene_identity:
                return {'ok': False, 'program_scene': previous, 'error': 'OBS did not confirm the scene identity; nothing was switched.'}
            guard = await self._safe_scene_contents(c, cfg.window_scene)
            if not guard.get('ok'):
                return guard
            item = await self._item(c, cfg.window_scene, cfg.window_input)
            if item is None:
                return {"ok": False, "window": _public(state), "error": "The shared-window item disappeared; use "
                        "action 'prepare'."}
            await c.request("SetSceneItemEnabled", {"sceneName": cfg.window_scene, "sceneItemId": item["sceneItemId"],
                                                    "sceneItemEnabled": True})
            async def changed():
                latest = await self._window_state(c)
                return (not latest.get('ready') or
                        (latest.get('_fingerprint') is not None and latest['_fingerprint'] != self._confirmed))
            async def fail_changed():
                warning = ''
                try:
                    await c.request('SetSceneItemEnabled', {'sceneName': cfg.window_scene, 'sceneItemId': item['sceneItemId'],
                                                            'sceneItemEnabled': False})
                    observed = await self._item(c, cfg.window_scene, cfg.window_input)
                    if not observed or observed.get('sceneItemEnabled') is not False:
                        warning = ' OBS did not confirm it hidden; hide it in OBS.'
                except (ObsError, OSError, ValueError):
                    warning = ' OBS did not confirm it hidden; hide it in OBS.'
                return {'ok': False, 'error': 'Window authorization or readiness changed during the operation.' + warning}
            if await changed():
                return await fail_changed()
            await c.request("SetCurrentProgramScene", {"sceneName": cfg.window_scene})
            now = await self._program(c)
            item = await self._item(c, cfg.window_scene, cfg.window_input)
            if item and await changed():
                failure = await fail_changed()
                return await self._rollback_switch(c, cfg.window_scene, previous, ' ' + failure['error'])
            try:
                guard = await self._switch_guard(c, cfg.window_scene)
                same_scene = scene_identity == await ownership.scene_uuid(c, cfg.window_scene)
            except (ObsError, OSError, ValueError):
                guard, same_scene = {'ok': False}, False
            if not guard.get('ok') or not same_scene:
                return await self._rollback_switch(c, cfg.window_scene, previous)
            ok = now == cfg.window_scene and bool(item and item.get("sceneItemEnabled"))
            return {"ok": ok, "program_scene": now, "share": "os_window", "label": state["label"],
                    "selection": state["selection"], "capture_type_verified": state["capture_type_verified"],
                    "operator_confirmed": state["operator_confirmed"],
                    **({} if ok else {"error": "OBS did not report the shared-window scene on program."})}
        return await self._run(op)

    # ---- host-command side of the owner confirmation (NOT exposed as an agent tool) -----------------
    async def window_confirmation_target(self) -> dict:
        """What the owner is asked to confirm: the current combined input, which must exist and produce frames."""
        async def op(c):
            state = await self._window_state(c)
            if not state["exists"] or state.get("error"):
                return {"ok": False, "error": state.get("error") or "There is no owned capture input; ask the agent "
                        "to use onair_window_share action 'prepare' first."}
            if state["selection"] not in ("operator_selected_unconfirmed", "operator_confirmed"):
                return {"ok": False, "error": "This input is a window-only kind; it needs no confirmation."}
            if not state["producing_frames"] or not state["selected"]:
                return {"ok": False, "error": "OBS reports no frames from the input yet: pick ONE window in the system "
                        "share dialog first. Nothing was confirmed."}
            return {"ok": True, "input": state["input"], "kind": state["kind"], "fingerprint": state["_fingerprint"]}
        return await self._run(op)

    async def confirm_window(self, fingerprint: str) -> dict:
        """Bind the owner's card answer to the input exactly as it was when the card was shown."""
        async def op(c):
            state = await self._window_state(c)
            if state.get("_fingerprint") != fingerprint or not state["producing_frames"]:
                return {"ok": False, "error": "The input changed while you were deciding (identity, settings or frames); "
                        "nothing was confirmed. Check OBS and run the command again."}
            self._confirmed, self._revoked_because = fingerprint, None
            return {"ok": True, "window": _public(await self._window_state(c))}
        return await self._run(op)

    async def window_hide(self) -> dict:
        out = await self.set_scene(self.cfg.presentation_scene)
        out["share"] = "browser_source" if out.get("ok") else "unknown"
        return out

    # ---- broadcast -----------------------------------------------------------------------------------
    async def stream_status(self) -> dict:
        async def op(c):
            return {"ok": True, "stream": await self._stream(c)}
        return await self._run(op)

    async def _await_stream(self, c, want: bool, deadline_s: float) -> dict:
        end = time.monotonic() + deadline_s
        while True:
            s = await self._stream(c)
            if s["active"] is want or time.monotonic() >= end:
                return s
            await asyncio.sleep(self.cfg.poll_s)

    async def start_stream(self) -> dict:
        async def op(c):
            before = await self._stream(c)
            if before["active"] is True:
                return {"ok": True, "stream": before, "note": "The stream was already observed live; nothing was sent."}
            await c.request("StartStream")
            s = await self._await_stream(c, True, self.cfg.start_deadline_s)
            ok = s["active"] is True
            return {"ok": ok, "stream": s, **({"note": "OBS reports the stream output active."} if ok else {
                "error": "OBS accepted the start request but the stream output was not observed active within "
                         f"{self.cfg.start_deadline_s:g}s. Check the destination in OBS Settings > Stream."})}
        return await self._run(op)

    async def stop_stream(self) -> dict:
        async def op(c):
            try:
                await c.request("StopStream")
            except ObsRequestError as exc:
                if exc.code != 501:  # 501 = not running: fine, confirm by observation below
                    raise
            s = await self._await_stream(c, False, self.cfg.stop_deadline_s)
            ok = s["active"] is False
            return {"ok": ok, "stream": s, **({} if ok else {"error": "The stream output is still observed active."})}
        return await self._run(op)
