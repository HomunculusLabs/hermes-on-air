"""Bounded layout presets for the OWNED OBS scenes: presentation, shared window, picture-in-picture, side by side.

``onair_layout`` arranges only On Air's own scene items: the presentation Browser Source, the owner-selected
window capture and the one captions+voice overlay, inside On Air's own two scenes. Its input is a preset name
(and, for picture-in-picture, a corner); it takes no source names, URLs, HTML or CSS.

What ``apply`` does, in this order, after checking every prerequisite with reads only:

1. reads the OBS BASE canvas (GetVideoSettings baseWidth/baseHeight, never the output scaling);
2. hides owned items the preset does not use, sets each used item's bounds (fit inside, aspect ratio kept,
   centred: ``OBS_BOUNDS_SCALE_INNER``) so every box, inset and the full-canvas overlay lies inside the canvas,
   keeps the overlay above the other owned items, then shows the used items;
3. reads every transform, the enabled state and the stacking order back. ``applied`` is true only when all of
   them match; a refusal or a mismatch is reported as not applied, with what was already changed.

It never selects or captures a window, never switches the program scene, never starts or stops an output,
and never touches foreign scenes, foreign items, global audio or the overlay's audio. A window item is shown
only when the existing window-share readiness says ready (on Wayland: the owner's host-card confirmation).
Layout status is always derived from OBS readback, never from a remembered label, so a reset made elsewhere
(the owner in OBS, a re-prepare, a canvas change) shows up as "no longer matches".
"""
from __future__ import annotations

import time
from typing import Any

from .obs_control import (ALIGN_CENTER, ALIGN_TOP_LEFT, FIT_BOUNDS_TYPE, REQUEST_CODES, ObsControl, _canvas_dims,
                          _same_value)
from .obs_protocol import ObsRequestError

PRESET_NAMES = ("presentation", "window", "window_pip", "side_by_side")
CORNERS = ("top_right", "top_left", "bottom_right", "bottom_left")
DEFAULT_CORNER = "top_right"
MARGIN = 0.025        # of the canvas width: the gap between a box and the canvas edge (48 px at 1920)
INSET = 0.30          # picture-in-picture inset width, of the canvas width (576 px at 1920)

ROLES = ("window", "presentation", "overlay")      # bottom -> top stacking order of the owned items

REQUIREMENTS = {
    "presentation_page": ("The agent's presentation page: publish a deck (onair_present_publish) so the page is "
                          "served, then onair_obs_setup configure. It is a web page in an OBS Browser Source with "
                          "its audio off, not a screen capture."),
    "voice_overlay": ("Exactly one captions+voice overlay Browser Source, the configured one with OBS audio on, in "
                      "the preset's scene (onair_obs_setup configure adds it to both owned scenes)."),
    "shared_window": ("One OS window the OWNER chose in OBS: onair_window_share prepare creates the owned capture "
                      "input, the owner picks the window; on Wayland the owner also types /onair-broadcast "
                      "confirm-window and confirms the card. The agent cannot pick or confirm it."),
    "canvas": "The OBS base canvas size, read from OBS (GetVideoSettings).",
}

PRESETS: dict[str, dict] = {
    "presentation": {"label": "Slides full screen", "scene": "presentation",
                     "requires": ["presentation_page", "voice_overlay", "canvas"],
                     "shows": "The presentation page fills the canvas; captions and voice come from the overlay."},
    "window": {"label": "Shared window full screen", "scene": "window",
               "requires": ["shared_window", "voice_overlay", "canvas"],
               "shows": ("The owner-selected window fills the canvas (aspect ratio kept); the slides are hidden in "
                         "this scene; captions and voice come from the overlay.")},
    "window_pip": {"label": "Shared window with the slides as a picture-in-picture inset", "scene": "window",
                   "requires": ["shared_window", "presentation_page", "voice_overlay", "canvas"],
                   "options": {"corner": list(CORNERS), "default_corner": DEFAULT_CORNER},
                   "shows": ("The owner-selected window fills the canvas and the presentation page sits in a corner "
                             "inset (30% of the canvas width). The overlay draws above both: captions at the "
                             "bottom centre, chat on the right and the LIVE marker top left by default, so pick a "
                             "corner that suits what the overlay shows.")},
    "side_by_side": {"label": "Shared window and slides side by side", "scene": "window",
                     "requires": ["shared_window", "presentation_page", "voice_overlay", "canvas"],
                     "shows": ("The owner-selected window on the left half and the presentation page on the right "
                               "half, each fitted with its aspect ratio kept; captions and voice from the overlay.")},
}

RECIPES = {
    "standby": {
        "how": ["onair_present_control action 'standby' (optional title/note) puts a holding card on the "
                "presentation page",
                "to make it what viewers see, the presentation scene must be on program: onair_window_share 'hide' "
                "or onair_obs_scene with the presentation scene"],
        "not": ("Standby is a card on the presentation page, NOT an audio privacy cut: the co-host's voice overlay, "
                "microphones and any other OBS audio stay as they are, and in a window layout the shared window "
                "stays visible until the presentation scene is on program. To silence the co-host use the show's own "
                "safety (onair_show_safety stop, or the stage's off_air), and check OBS's own mixer.")},
    "conversation": {
        "how": ["apply 'presentation' and publish a title or bullets slide naming today's topic "
                "(onair_present_publish); the co-host's captions and voice play through the one overlay"],
        "not": ("The overlay has no built-in avatar; On Air provides no camera, guest or host video. "
                "An optional owner 3D scene can be added separately in OBS; see docs/scene.md. "
                "onair_layout never creates, moves or shows sources other than its own three.")},
}

NOT_OFFERED = ("Arbitrary sources, names, URLs, HTML or CSS; avatar, camera, guest or host video; choosing or "
               "capturing a window; switching the program scene; starting or stopping the stream; overlay theme or "
               "scale (those are URL options and OBS Custom CSS, see docs/overlay.md).")


# ---- pure geometry ------------------------------------------------------------------------------------------
def boxes(preset: str, width: int, height: int, corner: str = DEFAULT_CORNER) -> dict[str, dict]:
    """Bounds box per owned role for this preset on a WIDTHxHEIGHT canvas. Integer pixels, always inside the canvas.

    Roles absent from the result are hidden (or simply not part of that scene)."""
    w, h = int(width), int(height)
    full = {"x": 0, "y": 0, "w": w, "h": h}
    m = max(1, int(w * MARGIN))
    if preset == "presentation":
        return {"presentation": dict(full), "overlay": dict(full)}
    if preset == "window":
        return {"window": dict(full), "overlay": dict(full)}
    if preset == "window_pip":
        if corner not in CORNERS:
            raise ValueError("corner")
        iw = max(1, int(w * INSET))
        ih = max(1, min(h - 2 * m, int(iw * h / w)))
        x = m if corner.endswith("left") else w - m - iw
        y = m if corner.startswith("top") else h - m - ih
        return {"window": dict(full), "presentation": {"x": x, "y": y, "w": iw, "h": ih}, "overlay": dict(full)}
    if preset == "side_by_side":
        bw = max(1, (w - 3 * m) // 2)
        bh = max(1, min(h - 2 * m, int(bw * h / w)))
        y = (h - bh) // 2
        return {"window": {"x": m, "y": y, "w": bw, "h": bh},
                "presentation": {"x": 2 * m + bw, "y": y, "w": bw, "h": bh}, "overlay": dict(full)}
    raise ValueError("preset")


def transform(box: dict) -> dict:
    """The SetSceneItemTransform fields for one box (the same v5 fields onair_obs_setup's canvas fit sends)."""
    return {"positionX": float(box["x"]), "positionY": float(box["y"]), "rotation": 0.0, "alignment": ALIGN_TOP_LEFT,
            "boundsType": FIT_BOUNDS_TYPE, "boundsAlignment": ALIGN_CENTER,
            "boundsWidth": float(box["w"]), "boundsHeight": float(box["h"])}


def fitted_rect(box: dict, source_w: float, source_h: float) -> dict | None:
    """Where OBS draws a source of SOURCE_WxSOURCE_H inside BOX with fit-inside bounds, centred (aspect kept)."""
    if not source_w or not source_h or source_w <= 0 or source_h <= 0:
        return None
    s = min(box["w"] / source_w, box["h"] / source_h)
    dw, dh = source_w * s, source_h * s
    return {"x": box["x"] + (box["w"] - dw) / 2, "y": box["y"] + (box["h"] - dh) / 2, "w": dw, "h": dh, "scale": s}


def describe(cfg: Any = None) -> dict:
    """The static ``presets`` answer: no OBS contact. ``scene_name`` is the owned OBS scene each preset arranges."""
    names = {"presentation": getattr(cfg, "presentation_scene", None), "window": getattr(cfg, "window_scene", None)}
    return {"ok": True, "presets": {name: dict(p, name=name, scene_name=names[p["scene"]])
                                    for name, p in PRESETS.items()},
            "requirements": REQUIREMENTS, "recipes": RECIPES, "not_offered": NOT_OFFERED,
            "apply_with": {"action": "apply", "preset": "<one of presets>",
                           "corner": "window_pip only: " + " | ".join(CORNERS)},
            "note": ("apply arranges On Air's own scene items only and never changes the program scene or the "
                     "stream; use inspect to see which presets are ready and what OBS currently shows.")}


# ---- OBS reads ----------------------------------------------------------------------------------------------
def _scene_names(ctl: ObsControl) -> dict[str, str]:
    return {"presentation": ctl.cfg.presentation_scene, "window": ctl.cfg.window_scene}


def _role_sources(ctl: ObsControl) -> dict[str, str]:
    return {"window": ctl.cfg.window_input, "presentation": ctl.cfg.presentation_input,
            "overlay": ctl.cfg.overlay_input}


async def _scene_items(ctl: ObsControl, c, scene: str) -> list[dict] | None:
    try:
        return (await c.request("GetSceneItemList", {"sceneName": scene})).get("sceneItems", [])
    except ObsRequestError:
        return None


async def _gather(ctl: ObsControl, c, need_window: bool) -> dict:
    """Everything the prerequisites and the readback need, by reads only."""
    facts: dict[str, Any] = {"canvas": None, "canvas_error": None}
    try:
        facts["canvas"] = _canvas_dims(await c.request("GetVideoSettings"))
        if facts["canvas"] is None:
            facts["canvas_error"] = "OBS reported no valid canvas (base) resolution."
    except ObsRequestError as exc:
        facts["canvas_error"] = f"OBS did not report its canvas size (code {exc.code})."
    inputs = await ctl._inputs(c)
    facts["sources"] = await ctl._sources(c, inputs)
    facts["program_scene"] = await ctl._program(c)
    facts["presentation_url"] = ctl._presentation_url()
    sources = _role_sources(ctl)
    facts["scenes"] = {}
    for role, scene in _scene_names(ctl).items():
        items = await _scene_items(ctl, c, scene)
        rec: dict[str, Any] = {"name": scene, "exists": items is not None, "items": {}}
        for index, it in enumerate(items or []):
            for r, src in sources.items():
                if it.get("sourceName") == src and isinstance(it.get("sceneItemId"), int) and r not in rec["items"]:
                    pos = it.get("sceneItemIndex", index)
                    rec["items"][r] = {"id": it["sceneItemId"], "enabled": it.get("sceneItemEnabled") is True,
                                       "index": pos if isinstance(pos, int) else index}
        for r, item in rec["items"].items():
            try:
                tr = (await c.request("GetSceneItemTransform", {"sceneName": scene, "sceneItemId": item["id"]})
                      ).get("sceneItemTransform") or {}
            except ObsRequestError as exc:
                tr, item["transform_error"] = {}, f"code {exc.code}"
            item["transform"] = tr
        facts["scenes"][role] = rec
    facts["window"] = await ctl._window_state(c) if need_window else None
    return facts


def _missing(preset: str, facts: dict) -> list[dict]:
    """Every unmet prerequisite of PRESET, each with an actionable next step. Empty means ready."""
    out: list[dict] = []
    spec = PRESETS[preset]
    scene = facts["scenes"][spec["scene"]]
    src = facts["sources"]
    pres = src.get("presentation") or {}
    if facts["canvas"] is None:
        out.append({"need": "canvas", "why": facts["canvas_error"] or "unknown canvas",
                    "next_action": "Check OBS Settings > Video (Base (Canvas) Resolution), then inspect again."})
    if not scene["exists"]:
        out.append({"need": "scene", "why": f"The owned scene {scene['name']!r} does not exist in OBS.",
                    "next_action": "onair_obs_setup configure" if spec["scene"] == "presentation"
                    else "onair_window_share prepare (after onair_obs_setup configure)"})
    if "presentation_page" in spec["requires"]:
        if not facts["presentation_url"]:
            out.append({"need": "presentation_page", "why": "The presentation page is not being served.",
                        "next_action": "onair_present_publish (a deck or sample=true), then onair_obs_setup configure"})
        elif not pres.get("url_matches"):
            out.append({"need": "presentation_page", "why": "OBS has no owned presentation Browser Source showing "
                        "this page.", "next_action": "onair_obs_setup configure"})
        elif pres.get("audio_via_obs") is not False:
            out.append({"need": "presentation_page", "why": "The presentation page's audio is on in OBS; only the "
                        "overlay may speak.", "next_action": "onair_obs_setup configure (turns it off)"})
        if spec["scene"] == "presentation" and scene["exists"] and "presentation" not in scene["items"]:
            out.append({"need": "presentation_page", "why": "The presentation page is not in the presentation scene.",
                        "next_action": "onair_obs_setup configure"})
    elif pres.get("exists") and pres.get("audio_via_obs") is True:
        # Shared invariant: a preset that does not need the slides still refuses an existing page that speaks.
        out.append({"need": "voice_overlay", "why": "The presentation page's audio is on in OBS; only the overlay may "
                    "speak (or turn off Control audio via OBS in that source's properties).",
                    "next_action": ("onair_obs_setup configure (turns it off)" if facts["presentation_url"] else
                                    "onair_present_publish (a deck or sample=true), then onair_obs_setup configure "
                                    "(turns it off)")})
    voice = src.get("speech_audio") or {}
    if not voice.get("ok"):
        out.append({"need": "voice_overlay", "why": voice.get("note") or "No speaking overlay.",
                    "next_action": ("remove the extra overlay Browser Sources in OBS: "
                                    + ", ".join(voice.get("extra_overlay_clients") or [])
                                    if voice.get("extra_overlay_clients") else "onair_obs_setup configure")})
    if scene["exists"] and "overlay" not in scene["items"]:
        out.append({"need": "voice_overlay", "why": f"The overlay is not in the scene {scene['name']!r}.",
                    "next_action": "onair_obs_setup configure"})
    if "shared_window" in spec["requires"]:
        w = facts["window"] or {}
        if not w.get("ready"):
            pre = list(w.get("prerequisites") or [])
            out.append({"need": "shared_window", "why": w.get("error") or "The shared window is not ready.",
                        "next_action": w.get("next_human_action") or (pre[-1] if pre else
                                                                      "onair_window_share discover"),
                        "window": {k: w.get(k) for k in ("exists", "kind", "selection", "selected", "producing_frames",
                                                         "operator_confirmed", "capture_type_verified")},
                        "human_only": bool(w.get("exists"))})
        elif scene["exists"] and "window" not in scene["items"]:
            out.append({"need": "shared_window", "why": "The window input is not in the shared-window scene.",
                        "next_action": "onair_window_share prepare"})
    return out


def _expected(preset: str, corner: str, canvas: tuple[int, int]) -> dict[str, dict]:
    """role -> {"enabled": bool, "transform": dict|None} for the owned items of the preset's scene."""
    used = boxes(preset, canvas[0], canvas[1], corner)
    roles = ("presentation", "overlay") if PRESETS[preset]["scene"] == "presentation" else ROLES
    return {r: ({"enabled": True, "transform": transform(used[r])} if r in used else {"enabled": False,
                                                                                    "transform": None})
            for r in roles}


def _compare(preset: str, corner: str, facts: dict) -> dict:
    """Readback check: every used item enabled with the expected transform, unused owned items hidden or absent,
    the overlay above every other owned item and, in picture-in-picture, the inset above the window."""
    canvas = facts["canvas"]
    if canvas is None:
        return {"matches": False, "problems": ["canvas unknown"]}
    scene = facts["scenes"][PRESETS[preset]["scene"]]
    problems: list[str] = []
    items = scene["items"]
    for role, want in _expected(preset, corner, canvas).items():
        got = items.get(role)
        if not want["enabled"]:
            if got is not None and got["enabled"]:
                problems.append(f"{role}: shown but this preset hides it")
            continue
        if got is None:
            problems.append(f"{role}: missing from the scene")
            continue
        if not got["enabled"]:
            problems.append(f"{role}: hidden")
        tr = got.get("transform") or {}
        bad = [k for k, v in want["transform"].items() if not _same_value(tr.get(k), v)]
        if bad:
            problems.append(f"{role}: transform differs ({', '.join(bad)})")
    shown = {r: it for r, it in items.items() if it["enabled"]}
    if "overlay" in shown and any(it["index"] > shown["overlay"]["index"] for r, it in shown.items() if r != "overlay"):
        problems.append("overlay: not above the other owned items")
    if preset == "window_pip" and {"window", "presentation"} <= set(shown) \
            and shown["presentation"]["index"] < shown["window"]["index"]:
        problems.append("presentation: inset is below the window")
    return {"matches": not problems, "problems": problems}


def _current(facts: dict) -> dict:
    """Which preset each owned scene matches right now, from readback only (None: none of them)."""
    out = {}
    for role in ("presentation", "window"):
        found = None
        for name, spec in PRESETS.items():
            if spec["scene"] != role:
                continue
            for corner in (CORNERS if name == "window_pip" else (DEFAULT_CORNER,)):
                if _compare(name, corner, facts)["matches"]:
                    found = {"preset": name, **({"corner": corner} if name == "window_pip" else {})}
                    break
            if found:
                break
        out[role] = found
    return out


def _public_scenes(facts: dict) -> dict:
    out = {}
    for role, rec in facts["scenes"].items():
        out[role] = {"name": rec["name"], "exists": rec["exists"],
                     "items": {r: {"enabled": it["enabled"], "stack_index": it["index"],
                                   **({k: it["transform"].get(k) for k in ("positionX", "positionY", "boundsType",
                                                                           "boundsWidth", "boundsHeight",
                                                                           "sourceWidth", "sourceHeight")}
                                      if it.get("transform") else {}),
                                   **({"transform_error": it["transform_error"]} if it.get("transform_error") else {})}
                               for r, it in rec["items"].items()}}
    return out


def _canvas_out(facts: dict) -> dict | None:
    c = facts["canvas"]
    return {"base_width": c[0], "base_height": c[1]} if c else None


def _window_out(facts: dict) -> dict | None:
    w = facts.get("window")
    if not w:
        return None
    return {k: w.get(k) for k in ("share", "label", "kind", "selection", "capture_type_verified", "operator_confirmed",
                                  "ready", "next_human_action")}


def boxes_geometry(arrangement: str, width: int, height: int, slots: dict) -> dict:
    from .boxes import geometry
    return geometry(arrangement, width, height, slots)


async def boxes_inspect(ctl: ObsControl) -> dict:
    from .boxes import inspect
    return await inspect(ctl)


async def boxes_apply(ctl: ObsControl, action: str, args: dict) -> dict:
    from .boxes import apply
    return await apply(ctl, action, args)


# ---- tool actions -------------------------------------------------------------------------------------------
async def inspect(ctl: ObsControl) -> dict:
    """Read-only: canvas, owned items as OBS reports them, which preset each scene matches, readiness per preset."""
    async def op(c):
        facts = await _gather(ctl, c, need_window=True)
        ready = {}
        for name in PRESETS:
            miss = _missing(name, facts)
            ready[name] = {"ready": not miss, "missing": miss}
        last = ctl.layout_last
        last_out = None
        if last:
            check = _compare(last["preset"], last.get("corner") or DEFAULT_CORNER, facts)
            last_out = {**last, "still_matches": check["matches"], "problems": check["problems"]}
        return {"ok": True, "canvas": _canvas_out(facts), "program_scene": facts["program_scene"],
                "current": _current(facts), "last_applied": last_out, "presets": ready,
                "scenes": _public_scenes(facts), "window": _window_out(facts),
                "speech_audio": facts["sources"].get("speech_audio"),
                "note": ("Read from OBS now. Geometry and enabled-state readback are not pixels or sound: check the "
                         "OBS preview and listen before going live.")}
    return await ctl._run(op)


async def apply(ctl: ObsControl, preset: str, corner: str = DEFAULT_CORNER) -> dict:
    if preset not in PRESETS:
        return {"ok": False, "applied": False, "error": "Unknown preset.", "presets": list(PRESETS)}
    if corner not in CORNERS:
        return {"ok": False, "applied": False, "error": "Unknown corner.", "corners": list(CORNERS)}
    spec = PRESETS[preset]

    async def op(c):
        facts = await _gather(ctl, c, need_window="shared_window" in spec["requires"])
        miss = _missing(preset, facts)
        scene = facts["scenes"][spec["scene"]]
        base = {"preset": preset, **({"corner": corner} if preset == "window_pip" else {}), "scene": scene["name"],
                "canvas": _canvas_out(facts), "program_scene": facts["program_scene"],
                **({"window": _window_out(facts)} if facts.get("window") else {})}
        if miss:
            return {"ok": False, "applied": False, "changed": False, **base, "missing": miss,
                    "error": "Not applied: a prerequisite is missing (see missing[*].next_action). Nothing was changed."}
        want = _expected(preset, corner, facts["canvas"])
        items = scene["items"]
        sent: list[str] = []
        error = None
        try:
            # 1. a picture-in-picture/side-by-side scene needs the owned presentation item, created hidden
            if want.get("presentation", {}).get("enabled") and "presentation" not in items:
                await c.request("CreateSceneItem", {"sceneName": scene["name"], "sourceName": ctl.cfg.presentation_input,
                                                    "sceneItemEnabled": False})
                sent.append("created the presentation item (hidden)")
                found = await ctl._item(c, scene["name"], ctl.cfg.presentation_input)
                if found is None or not isinstance(found.get("sceneItemId"), int):
                    raise ObsRequestError(600)
                items = dict(items, presentation={"id": found["sceneItemId"],
                                                  "enabled": found.get("sceneItemEnabled") is True, "index": 0})
            ref = lambda r: {"sceneName": scene["name"], "sceneItemId": items[r]["id"]}  # noqa: E731
            # 2. hide what this preset does not use, before anything moves
            for role, w in want.items():
                if not w["enabled"] and role in items and items[role]["enabled"]:
                    await c.request("SetSceneItemEnabled", {**ref(role), "sceneItemEnabled": False})
                    sent.append(f"hid {role}")
            # 3. bounds for every used item
            for role, w in want.items():
                if w["enabled"]:
                    await c.request("SetSceneItemTransform", {**ref(role), "sceneItemTransform": w["transform"]})
                    sent.append(f"set {role} bounds")
            # 4. stacking: inset above the window, overlay above every owned item
            if want.get("presentation", {}).get("enabled") and "window" in items:
                order = {it["sceneItemId"]: n for n, it in enumerate(await _scene_items(ctl, c, scene["name"]) or [])}
                pi, wi = order.get(items["presentation"]["id"]), order.get(items["window"]["id"])
                if pi is not None and wi is not None and pi < wi:
                    await c.request("SetSceneItemIndex", {**ref("presentation"), "sceneItemIndex": wi})
                    sent.append("raised the presentation above the window")
            listed = await _scene_items(ctl, c, scene["name"]) or []
            await c.request("SetSceneItemIndex", {**ref("overlay"), "sceneItemIndex": max(len(listed) - 1, 0)})
            sent.append("kept the overlay on top")
            # 5. show the used items last (the window only got here because readiness said ready)
            for role, w in want.items():
                if w["enabled"]:
                    await c.request("SetSceneItemEnabled", {**ref(role), "sceneItemEnabled": True})
                    sent.append(f"showed {role}")
        except ObsRequestError as exc:
            error = (f"OBS refused a layout request ({REQUEST_CODES.get(exc.code, 'failed')}, code {exc.code}); "
                     "the layout is NOT applied.")
        after = await _gather(ctl, c, need_window=False)
        check = _compare(preset, corner, after)
        applied = error is None and check["matches"]
        out = {"ok": applied, "applied": applied, "changed": bool(sent), "requests_accepted": sent, **base,
               "program_scene": after["program_scene"], "on_program": after["program_scene"] == scene["name"],
               "readback": {"matches": check["matches"], "problems": check["problems"],
                            "scene": _public_scenes(after)[spec["scene"]]},
               "speech_audio": after["sources"].get("speech_audio")}
        if applied:
            ctl.layout_last = {"preset": preset, **({"corner": corner} if preset == "window_pip" else {}),
                               "scene": scene["name"], "canvas": list(after["canvas"]), "at": round(time.time(), 3)}
            out["note"] = ("Applied and read back in the owned scene. The program scene and outputs were not changed."
                           + (" This scene is on program, so viewers see the new layout now." if out["on_program"]
                              else " To show it: " + (
                               "onair_window_share show." if spec["scene"] == "window" else
                               "onair_window_share hide, or onair_obs_scene with this scene.")))
        else:
            out["error"] = error or ("OBS read back a different layout; it is NOT applied: "
                                     + "; ".join(check["problems"]))
            if sent:
                out["note"] = "Some requests were accepted before this failed; inspect shows the scene as it is now."
        return out
    return await ctl._run(op)
