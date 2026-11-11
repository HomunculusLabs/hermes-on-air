"""macOS OBS startup packet: make OBS initialize its browser engine on the main thread BEFORE any WebSocket call.

Why (observed on OBS 33 / obs-browser 2.27.4 / CEF 150 on macOS): when the FIRST Browser Source of an OBS process
is created through obs-websocket, CEF initializes on that obs-websocket worker thread; the worker returns to its
pool, CEF's UI tasks never run, and every Browser Source stays blank (no renderer, no page request) for the life of
that process. Repeating CreateInput cannot fix it. Starting a FRESH OBS process whose scene collection already holds
one Browser Source makes CEF initialize on the main thread; Browser Sources created later over WebSocket render.

``prepare_startup`` writes, into an owned plugin-data namespace (never the OBS config root):

- ``OnAirStartup.json``: a scene collection whose only Browser Source is the real presentation source (same owned
  input name, the runtime's presentation URL and size, muted, no audio to OBS). No overlay/voice page: the voice
  overlay is created later by ``configure``, so speech is never doubled.
- ``required-settings.json``: the owned prestart settings (user.ini ``[General] FirstRun=true`` written BEFORE the
  first start of a fresh owned config; a value OBS writes after a run proves nothing), the collection selection, the
  start order and the loopback target that must resolve ON THE OBS HOST.
- ``MANIFEST.json``: sha256/size of both, written last.

The packet is PREPARED, not applied: nothing here starts, restarts or edits OBS. It contains no OBS password, stream
service/key, profile output settings, provider data or credentials.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PACKET = "onair-obs-startup"
VERSION = 1
COLLECTION_NAME = "On Air startup"
COLLECTION_FILE = "OnAirStartup.json"
SETTINGS_FILE = "required-settings.json"
MANIFEST_FILE = "MANIFEST.json"
FILES = (COLLECTION_FILE, SETTINGS_FILE, MANIFEST_FILE)
_NS = uuid.UUID("6f1d2c4e-0a2b-4c3d-9e8f-0a1b2c3d4e5f")
# Saved-source fields copied from the owned R8/R9 native OBS 33 collection that rendered (case B).
_SOURCE_COMMON = {"prev_ver": 553648128, "mixers": 0, "sync": 0, "flags": 0, "volume": 0.0, "balance": 0.5,
                  "enabled": True, "muted": True, "push-to-mute": False, "push-to-mute-delay": 0, "push-to-talk": False,
                  "push-to-talk-delay": 0, "hotkeys": {}, "deinterlace_mode": 0, "deinterlace_field_order": 0,
                  "monitoring_type": 0, "monitoring_enabled": False, "private_settings": {}}
_ITEM_COMMON = {"visible": True, "locked": False, "rot": 0.0, "align": 5, "bounds_type": 0, "bounds_align": 0,
                "bounds_crop": False, "crop_left": 0, "crop_top": 0, "crop_right": 0, "crop_bottom": 0,
                "group_item_backup": False, "pos": {"x": 0.0, "y": 0.0}, "scale": {"x": 1.0, "y": 1.0},
                "bounds": {"x": 0.0, "y": 0.0}, "scale_filter": "disable", "blend_method": "default",
                "blend_type": "normal", "show_transition": {"duration": 0}, "hide_transition": {"duration": 0},
                "private_settings": {}}


def default_dir() -> Path:
    """Derive (never create) ``<Hermes home>/plugin-data/on-air/obs-startup``.

    The host's ``plugin_data_dir`` creates its directory, so it is not used here: registration, status and refused
    calls must create nothing. ``hermes_constants.get_hermes_home`` only resolves the active home.
    """
    try:
        from hermes_constants import get_hermes_home  # read-only resolution of the active profile home
        home = Path(get_hermes_home())
    except Exception:  # noqa: BLE001 - standalone/tests
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home / "plugin-data" / "on-air" / "obs-startup"


def _uid(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "\x1f".join(parts)))


def _scene(name: str, items: list[tuple[str, str]]) -> dict:
    return {"name": name, "uuid": _uid("scene", name), "id": "scene", "versioned_id": "scene",
            "settings": {"id_counter": len(items), "custom_size": False,
                         "items": [{"name": n, "source_uuid": u, "id": i + 1, **_ITEM_COMMON}
                                   for i, (n, u) in enumerate(items)]},
            "mixers": 0, "sync": 0, "flags": 0, "volume": 0.0, "enabled": True, "muted": True, "monitoring_type": 0,
            "hotkeys": {}}


def build(cfg: Any, presentation_url: str) -> dict[str, bytes]:
    """Deterministic packet bytes for this runtime (same config + URL -> identical bytes)."""
    src_uuid = _uid("input", cfg.presentation_input)
    source = {"name": cfg.presentation_input, "uuid": src_uuid, "id": "browser_source",
              "versioned_id": "browser_source",
              "settings": {"is_local_file": False, "url": presentation_url, "width": int(cfg.width),
                           "height": int(cfg.height), "fps": 30, "shutdown": False, "reroute_audio": False},
              **_SOURCE_COMMON}
    coll: dict[str, Any] = {
        "name": COLLECTION_NAME, "current_scene": cfg.presentation_scene,
        "current_program_scene": cfg.presentation_scene,
        "scene_order": [{"name": cfg.presentation_scene}, {"name": cfg.window_scene}],
        "sources": [_scene(cfg.presentation_scene, [(cfg.presentation_input, src_uuid)]),
                    _scene(cfg.window_scene, []), source],
        "groups": [], "transitions": [], "current_transition": "Cut", "transition_duration": 0,
        "quick_transitions": [], "saved_projectors": [], "modules": {}, "preview_locked": True,
        "scaling_enabled": False}
    for k in ("DesktopAudioDevice1", "DesktopAudioDevice2", "AuxAudioDevice1", "AuxAudioDevice2", "AuxAudioDevice3",
              "AuxAudioDevice4"):
        coll[k] = None   # no global desktop/mic audio in the startup collection
    collection = (json.dumps(coll, indent=1, sort_keys=True) + "\n").encode()
    settings = {
        "packet": PACKET, "version": VERSION, "platform": "macos", "state": "prepared_not_applied",
        "collection": {"file": COLLECTION_FILE, "name": COLLECTION_NAME,
                       "install_as": f"<owned OBS config>/basic/scenes/{COLLECTION_FILE}"},
        "user_ini": {"when": "before_first_start", "fresh_owned_config_only": True,
                     "General": {"FirstRun": "true"},
                     "Basic": {"SceneCollection": COLLECTION_NAME, "SceneCollectionFile": COLLECTION_FILE[:-5]},
                     "note": ("Write these into a FRESH owned user.ini before OBS first starts. FirstRun=true found "
                              "after a run was written by OBS itself and proves nothing.")},
        "loopback_target": {"url": presentation_url, "resolves_on": "OBS_HOST",
                            "requirement": ("This is a loopback URL: it must be served (or port-forwarded) ON THE "
                                            "MACHINE RUNNING OBS. A URL served by another machine's localhost is "
                                            "not reachable from OBS unless forwarded to the same port there.")},
        "start_order": [
            {"step": "write_owned_prestart_settings", "detail": "user_ini above, in the owned OBS config only"},
            {"step": "install_collection", "detail": "copy the EXACT collection bytes (sha256 in MANIFEST.json)"},
            {"step": "serve_presentation_on_obs_host", "detail": "loopback_target reachable from OBS"},
            {"step": "start_fresh_obs_with_collection",
             "detail": "a NEW OBS process loads this collection at start; do not reuse a running OBS"},
            {"step": "onair_obs_setup_configure", "detail": "only now create/update the owned sources over WebSocket"},
            {"step": "verify_render", "detail": ("onair_present_status pages_connected > 0 and the OBS preview; "
                                                 "settings/ACK/source-active are not render proof")}],
        "not_included": ["OBS WebSocket password", "stream service/server/key", "profile output settings",
                         "provider/model data", "credentials"]}
    required = (json.dumps(settings, indent=1, sort_keys=True) + "\n").encode()
    files = {COLLECTION_FILE: collection, SETTINGS_FILE: required}
    manifest = {"packet": PACKET, "version": VERSION, "state": "prepared_not_applied",
                "files": {n: {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)} for n, b in files.items()}}
    files[MANIFEST_FILE] = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode()
    return files


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


START_ORDER = ("write_owned_prestart_settings", "install_collection", "serve_presentation_on_obs_host",
               "start_fresh_obs_with_collection", "onair_obs_setup_configure", "verify_render")
USER_INI_GENERAL = {"FirstRun": "true"}
USER_INI_BASIC = {"SceneCollection": COLLECTION_NAME, "SceneCollectionFile": COLLECTION_FILE[:-5]}
MAX_FILE_BYTES = 1 << 20
_HEX = set("0123456789abcdef")
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")


class PacketError(ValueError):
    """A packet that must not be reported as prepared or applied. ``code`` is stable; ``str()`` is actionable."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def _is_sha(v: Any) -> bool:
    return isinstance(v, str) and len(v) == 64 and set(v) <= _HEX


def _link_in_chain(p: Path) -> bool:
    """True when ``p`` or any EXISTING ancestor is a symbolic link (nothing is followed or created)."""
    p = Path(os.path.abspath(p))
    for q in [p, *p.parents]:
        if q.is_symlink():
            return True
    return False


def _symlinked(d: Path) -> bool:
    if _link_in_chain(d):
        return True
    try:
        return d.is_dir() and any((d / n).is_symlink() for n in os.listdir(d))
    except OSError:
        return True


def _need(cond: bool, code: str, detail: str) -> None:
    if not cond:   # explicit raise: validation must survive python -O
        raise PacketError(code, detail)


def _json_obj(b: bytes, name: str) -> dict:
    try:
        v = json.loads(b)
    except ValueError as e:
        raise PacketError("malformed", f"{name} is not valid JSON ({e.__class__.__name__}).") from None
    _need(isinstance(v, dict), "malformed", f"{name} must be a JSON object.")
    return v


def _loopback_url(url: Any) -> bool:
    """Accept only the producer's exact HTTP loopback authorities and an explicit valid port."""
    if (not isinstance(url, str) or len(url) >= 512 or not url.startswith("http://") or
            any(c in url for c in "\"'<> \\") or
            any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in url)):
        return False
    try:
        parsed = urlsplit(url)
        # Check the entire authority, not a prefix: this excludes userinfo, malformed brackets and trailing junk.
        authority = re.fullmatch(r"(" + "|".join(map(re.escape, _LOOPBACK_HOSTS)) + r"):([0-9]+)", parsed.netloc)
        return bool(authority and parsed.scheme == "http" and
                    parsed.hostname in ("127.0.0.1", "localhost", "::1") and
                    parsed.port is not None and 1 <= parsed.port <= 65535)
    except ValueError:  # malformed IPv6 authority or port
        return False


def _check_settings(req: dict) -> str:
    _need(req.get("packet") == PACKET and req.get("version") == VERSION and type(req.get("version")) is int,
          "wrong_identity", "required-settings.json is not an On Air startup packet of this version.")
    _need(req.get("platform") == "macos" and req.get("state") == "prepared_not_applied", "wrong_identity",
          "required-settings.json has the wrong platform/state.")
    col = req.get("collection")
    _need(isinstance(col, dict) and col.get("file") == COLLECTION_FILE and col.get("name") == COLLECTION_NAME,
          "wrong_collection", "required-settings.json names a different scene collection file.")
    ui = req.get("user_ini")
    _need(isinstance(ui, dict) and ui.get("when") == "before_first_start" and ui.get("fresh_owned_config_only") is True,
          "prestart_unbound", "user_ini must be written before the first start of a fresh owned config.")
    _need(ui.get("General") == USER_INI_GENERAL, "prestart_unbound",
          "user_ini [General] must be exactly FirstRun=true (prestart); anything else is refused.")
    _need(ui.get("Basic") == USER_INI_BASIC, "wrong_collection",
          "user_ini [Basic] must select exactly the startup collection.")
    lt = req.get("loopback_target")
    url = lt.get("url") if isinstance(lt, dict) else None
    _need(isinstance(lt, dict) and lt.get("resolves_on") == "OBS_HOST" and _loopback_url(url),
          "not_loopback", "loopback_target must be an http loopback URL that resolves on the OBS host.")
    so = req.get("start_order")
    _need(isinstance(so, list) and all(isinstance(x, dict) for x in so) and
          tuple(x.get("step") for x in so) == START_ORDER, "wrong_start_order",
          "start_order must be the exact prestart -> install -> serve -> fresh start -> configure -> verify order.")
    return url


def _check_collection(coll: dict, url: str) -> dict:
    _need(coll.get("name") == COLLECTION_NAME, "wrong_collection", "The collection has a different name.")
    srcs = coll.get("sources")
    _need(isinstance(srcs, list) and all(isinstance(x, dict) for x in srcs), "malformed", "Collection sources malformed.")
    browsers = [x for x in srcs if x.get("id") == "browser_source"]
    others = [x for x in srcs if x.get("id") not in ("browser_source", "scene")]
    _need(len(browsers) == 1 and not others, "wrong_collection",
          "The collection must hold exactly one Browser Source (the presentation page) and no other input kind.")
    b = browsers[0]
    st = b.get("settings")
    _need(isinstance(st, dict) and st.get("url") == url and st.get("is_local_file") is False and
          st.get("reroute_audio") is False and b.get("muted") is True and b.get("monitoring_type") == 0,
          "wrong_collection", "The Browser Source must be the muted presentation page at the loopback_target URL.")
    _need(all(coll.get(k) is None for k in ("DesktopAudioDevice1", "DesktopAudioDevice2", "AuxAudioDevice1",
                                           "AuxAudioDevice2", "AuxAudioDevice3", "AuxAudioDevice4")),
          "wrong_collection", "The startup collection must not enable global desktop or microphone audio.")
    return {"presentation_input": b.get("name"), "url": url}


def validate(directory: Path, expected_manifest_sha256: str | None = None) -> dict:
    """Strict, read-only packet validation shared by status, prepare (prior packet) and the consumer.

    Exact identity/version/state, exactly the three regular files (no links, nothing else), manifest membership of
    exactly the two payloads with matching types/sizes/hashes, FirstRun=true prestart semantics, the collection
    selection and a loopback target. ``expected_manifest_sha256`` (when given) binds the manifest bytes to the digest
    the producer reported. Raises PacketError; creates nothing.
    """
    d = Path(directory)
    _need(not _link_in_chain(d), "symlink", "The packet folder (or a parent) is a symbolic link; nothing followed.")
    _need(d.is_dir(), "missing", "No startup packet folder.")
    names = sorted(os.listdir(d))
    _need(not any((d / n).is_symlink() for n in names), "symlink", "The packet folder contains a symbolic link.")
    _need(names == sorted(FILES), "membership",
          f"The packet folder must contain exactly {', '.join(FILES)}; found {', '.join(names) or 'nothing'}.")
    blobs = {}
    for n in FILES:
        p = d / n
        _need(p.is_file() and p.stat().st_size <= MAX_FILE_BYTES, "malformed", f"{n} is not a small regular file.")
        blobs[n] = p.read_bytes()
    mb = blobs[MANIFEST_FILE]
    msha = _sha(mb)
    if expected_manifest_sha256 is not None:
        _need(_is_sha(expected_manifest_sha256), "unbound", "The expected manifest digest is missing or malformed.")
        _need(msha == expected_manifest_sha256, "unbound",
              "MANIFEST.json is not the manifest the producer reported (digest mismatch).")
    man = _json_obj(mb, MANIFEST_FILE)
    _need(set(man) == {"packet", "version", "state", "files"}, "malformed", "MANIFEST.json has unexpected keys.")
    _need(man["packet"] == PACKET and man["version"] == VERSION and type(man["version"]) is int and
          man["state"] == "prepared_not_applied", "wrong_identity", "MANIFEST.json is not this packet/version/state.")
    files = man["files"]
    _need(isinstance(files, dict) and set(files) == {COLLECTION_FILE, SETTINGS_FILE}, "membership",
          "MANIFEST.json must bind exactly the collection and required-settings files.")
    for n, meta in files.items():
        _need(isinstance(meta, dict) and set(meta) == {"sha256", "bytes"} and _is_sha(meta["sha256"]) and
              type(meta["bytes"]) is int and 0 < meta["bytes"] <= MAX_FILE_BYTES, "malformed",
              f"MANIFEST.json entry for {n} must be exactly sha256 + bytes.")
        _need(len(blobs[n]) == meta["bytes"], "size_mismatch", f"{n} size differs from MANIFEST.json.")
        _need(_sha(blobs[n]) == meta["sha256"], "hash_mismatch", f"{n} differs from MANIFEST.json (hash mismatch).")
    url = _check_settings(_json_obj(blobs[SETTINGS_FILE], SETTINGS_FILE))
    info = _check_collection(_json_obj(blobs[COLLECTION_FILE], COLLECTION_FILE), url)
    return {"manifest_sha256": msha, "files": {n: dict(files[n]) for n in sorted(files)}, **info, "_blobs": blobs}


def status(directory: Path | None = None, cfg: Any = None, presentation_url: str | None = None) -> dict:
    """Read-only: whether a VALID packet for the CURRENT runtime exists. Creates nothing.

    ``prepared`` is true only for an intact packet whose bytes equal what this runtime would prepare now (when the
    runtime's config/URL are given). An intact packet for an old URL/config is ``stale``; a malformed or edited one
    is ``drift`` with a reason. Neither is ever reported as ready to apply.
    """
    d = Path(directory or default_dir())
    out: dict = {"prepared": False, "dir": str(d)}
    try:
        if not _link_in_chain(d) and not d.exists():
            return out
        v = validate(d)
    except PacketError as e:
        out.update(drift=True, problem=e.code, error=str(e),
                   next_agent_action=("The startup packet is not valid and will not be applied or overwritten. "
                                      "Remove that folder deliberately, then run onair_obs_setup prepare_startup."))
        return out
    except OSError as e:
        out.update(drift=True, problem="unreadable", error=f"The startup packet could not be read ({e.__class__.__name__}).")
        return out
    out.update(intact=True, manifest_sha256=v["manifest_sha256"], presentation_url=v["url"])
    if cfg is not None:
        current = bool(presentation_url) and all(
            v["_blobs"][n] == b for n, b in build(cfg, presentation_url).items())
        if not current:
            out.update(stale=True, next_agent_action=(
                "The prepared packet is for an older presentation URL/config, not this runtime; do not apply it. "
                "Run onair_obs_setup prepare_startup again (it updates its own unmodified packet)."))
            return out
    out["prepared"] = True
    return out


def prepare(cfg: Any, presentation_url: str, secret: str = "", directory: Path | None = None) -> dict:
    d = Path(directory or default_dir())
    base = {"dir": str(d), "applied": False, "state": "prepared_not_applied"}
    files = build(cfg, presentation_url)
    try:
        _check_settings(_json_obj(files[SETTINGS_FILE], SETTINGS_FILE))
    except PacketError as e:
        return {"ok": False, "status": "refused", **base, "problem": e.code, "error": str(e)}
    if secret and any(secret.encode() in b for b in files.values()):
        return {"ok": False, "status": "refused_secret", **base, "error": "A credential would enter the packet; nothing was written."}
    if _symlinked(d):
        return {"ok": False, "status": "refused_symlink", **base,
                "error": "The owned startup folder (or a parent/entry in it) is a symbolic link; nothing was written or followed."}
    previous = None
    if d.exists():
        names = set(os.listdir(d)) if d.is_dir() else {"?"}
        if MANIFEST_FILE not in names:
            if names:
                return {"ok": False, "status": "collision", **base, "existing": sorted(names),
                        "error": "Files exist in the startup folder without its manifest; nothing was overwritten."}
        else:
            try:
                v = validate(d)
            except (PacketError, OSError) as e:
                return {"ok": False, "status": "drift", **base, "problem": getattr(e, "code", "unreadable"),
                        "error": (f"The prepared packet is not intact ({e}); it was NOT overwritten. "
                                  "Remove the folder deliberately to prepare a fresh packet.")}
            if all(v["_blobs"][n] == b for n, b in files.items()):
                return {"ok": True, "status": "unchanged", **base, "manifest_sha256": v["manifest_sha256"],
                        "files": _listing(d, files)}
            previous = v["manifest_sha256"]
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _symlinked(d):
        return {"ok": False, "status": "refused_symlink", **base, "error": "The owned startup folder became a link."}
    for n in (COLLECTION_FILE, SETTINGS_FILE, MANIFEST_FILE):   # manifest last
        tmp = d / f".{n}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(files[n])
        os.replace(tmp, d / n)
    out = {"ok": True, "status": "updated" if previous else "created", **base,
           "manifest_sha256": _sha(files[MANIFEST_FILE]), "files": _listing(d, files)}
    if previous:
        out["previous_manifest_sha256"] = previous
    return out


def _listing(d: Path, files: dict[str, bytes]) -> dict:
    return {n: {"path": str(d / n), "sha256": _sha(b), "bytes": len(b)} for n, b in files.items()}


# ---- consumer: install the packet into a FRESH owned OBS config root ------------------------------------
def normal_obs_roots(home: Path | None = None) -> list[Path]:
    h = Path(home or Path.home())
    return [h / "Library" / "Application Support" / "obs-studio", h / ".config" / "obs-studio"]


def _under(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


def user_ini_text() -> str:
    return ("[General]\n" + "".join(f"{k}={v}\n" for k, v in USER_INI_GENERAL.items()) +
            "[Basic]\n" + "".join(f"{k}={v}\n" for k, v in USER_INI_BASIC.items()))


def target_refusal(target: Path, prohibited: list[Path]) -> str | None:
    """Why ``target`` is not a safe NEW owned OBS config root, or None. Read-only; follows nothing."""
    raw = str(target)
    if not os.path.isabs(raw) or any(part == ".." for part in Path(raw).parts):
        return "The target must be an absolute path without '..'."
    t = Path(os.path.normpath(raw))
    if os.path.lexists(t):
        return "The target already exists; only a NEW owned OBS config root is accepted."
    if _link_in_chain(t.parent):
        return "A parent of the target is a symbolic link; nothing was followed or created."
    if not t.parent.is_dir():
        return "The target's parent folder must already exist (it is not created)."
    real = Path(os.path.realpath(t.parent)) / t.name
    for r in prohibited:
        for form in {Path(os.path.normpath(os.path.abspath(r))), Path(os.path.realpath(r))}:
            if _under(t, form) or _under(real, form) or _under(form, t):
                return "The target is (inside) a normal OBS config root; the normal profile is never touched."
    return None


def apply(packet_dir: Path, target_root: Path, expected_manifest_sha256: str,
          prohibited_roots: list[Path] | None = None) -> dict:
    """Install the EXACT validated packet into a NEW owned OBS config root. Never starts OBS.

    Refuses BEFORE any target write when the packet is not strictly valid, when its manifest is not the one the
    producer reported (``expected_manifest_sha256``), or when the target exists, has a symbolic-link ancestor, or is a
    normal OBS root or inside one. Writes user.ini (FirstRun=true + collection selection) and the collection bytes,
    then reads both back.
    """
    prohibited = list(normal_obs_roots()) + [Path(p) for p in (prohibited_roots or [])]
    try:
        _need(expected_manifest_sha256 is not None, "unbound", "The producer's manifest digest is required to apply.")
        v = validate(Path(packet_dir), expected_manifest_sha256)
    except PacketError as e:
        return {"ok": False, "applied": False, "status": "refused", "problem": e.code, "error": str(e)}
    except OSError as e:
        return {"ok": False, "applied": False, "status": "refused", "problem": "unreadable", "error": str(e)}
    why = target_refusal(Path(target_root), prohibited)
    if why:
        return {"ok": False, "applied": False, "status": "refused", "problem": "unsafe_target", "error": why}
    t = Path(os.path.normpath(str(target_root)))
    ini = user_ini_text().encode()
    coll = v["_blobs"][COLLECTION_FILE]
    os.mkdir(t, 0o700)
    os.mkdir(t / "basic", 0o700)
    os.mkdir(t / "basic" / "scenes", 0o700)
    dest = {t / "user.ini": ini, t / "basic" / "scenes" / COLLECTION_FILE: coll}
    for p, b in dest.items():
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(b)
    back = {str(p): _sha(p.read_bytes()) for p in dest}
    ok = back == {str(p): _sha(b) for p, b in dest.items()} and b"FirstRun=true\n" in (t / "user.ini").read_bytes()
    return {"ok": ok, "applied": "files_written_obs_not_started" if ok else False, "status": "applied" if ok else "readback_failed",
            "manifest_sha256": v["manifest_sha256"], "root": str(t), "readback": back,
            "collection_sha256": _sha(coll), "user_ini_sha256": _sha(ini), "presentation_url": v["url"],
            "start_order": list(START_ORDER)}
