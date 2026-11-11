"""One setup engine for both entries: the dashboard's quick-setup form and the private agent.

Both entries call the same four operations and see the same persisted state:

- ``status()``  sanitized facts only (names and states; never a key, token, URL credential or file
  content from the user's own Hermes home);
- ``plan(answers)``  validates answers, tests a requested model if untested, and returns the exact
  effects, the owned target and a digest over both. It never creates the stage folder;
- ``apply(digest, consent)``  runs the plan in one background job: create the private stage
  folder, optionally test the configured model connection by drafting ONE reply, start a private
  preview and read everything back. ``consent`` must be an :class:`OwnerConsent` for that exact
  digest. Only the owner entries mint one: the dashboard route (a click in the authenticated
  dashboard) and the ``/onair-setup approve`` slash command (typed by the human and confirmed on
  the host's own choice card). Tools never mint consent, and no argument such as
  ``approved=true`` or ``caller='private'`` is read by anything here;
- ``cancel()`` / ``stop_preview()``  safety actions, always allowed.

The private preview is the real stage controller, chat gate, output guard, overlay and desk
control API on 127.0.0.1 with ephemeral ports, fed one private test message. It never writes the
stage's ``control.json``, never uses the stage's overlay port and never reads Twitch, so it cannot
appear on an OBS browser source pointed at the real overlay. Lines still wait for a human.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

SCHEMA = 1
CONSENT_TTL_S = 600.0
MODEL_TIMEOUT_S = 20.0
MODEL_PROBE_TIMEOUT_S = 5.0
logger = logging.getLogger(__name__)
TEST_USER = "you_setup_test"
TEST_MESSAGE = "hi! is this thing on?"
_LABEL = re.compile(r"[A-Za-z0-9._:/@+ -]{1,80}\Z")
STEPS = (
    ("check", "Check your answers and the new folder"),
    ("create", "Create the private stage folder"),
    ("style", "Save the co-host's public style"),
    ("model", "Draft a reply with your model connection"),
    ("preview", "Start the private preview"),
    ("readback", "Read everything back"),
)
_ACTIVE = ("running", "cancelling")


class SetupError(Exception):
    pass


# The provider answered with HTTP 429 (for example a model the subscription does not include, or a usage
# limit). Stable status code only: the exception text/body can carry keys, URLs or account details.
MODEL_REJECTED_429 = ("The model provider rejected the request (HTTP 429). Check model access or limits in "
                      "Hermes; no preview reply was generated.")
MODEL_FAILED = "The model call failed. No preview reply was generated."
MODEL_UNAVAILABLE = ("This session cannot reach your model. Start a new Hermes session (or restart the gateway "
                     "that loaded the plugin) and ask again.")

# Readiness is process-local and attached to the actual host facade, not a persisted plan.  The
# routing wrapper is recreated on every lookup, so key it by its inner host facade instead.
# The context is part of the route: Hermes resolves runtime and profile via ContextVars.
_PROBES: list[dict] = []
_PROBES_LOCK = threading.Lock()


def _client_identity(client: Any) -> Any:
    from .adapter import _ShowModel
    return client._inner if isinstance(client, _ShowModel) else client


def _probe_home() -> str:
    try:
        from hermes_constants import get_hermes_home
        return os.fspath(get_hermes_home().resolve())
    except ImportError:
        return os.fspath(owner_home().resolve())


def _find_probe(client: Any, context: contextvars.Context) -> dict | None:
    identity, home = _client_identity(client), _probe_home()
    return next((r for r in _PROBES if r["client"] is identity and r["home"] == home
                 and r["context"] == context), None)


def _model_state(client: Any) -> str:
    if client is None:
        return "untested"
    context = contextvars.copy_context()
    with _PROBES_LOCK:
        record = _find_probe(client, context)
        return record["state"] if record else "untested"


def _log_model_failure(step: str, exc: BaseException, started: float) -> None:
    code = _status_code(exc)
    logger.debug("model failure step=%s category=%s status=%s elapsed_ms=%d", step,
                 type(exc).__name__, code if code is not None and 100 <= code <= 599 else "none",
                 int((time.monotonic() - started) * 1000))


def _invalidate_client(client: Any) -> None:
    identity = _client_identity(client)
    with _PROBES_LOCK:
        for record in _PROBES:
            if record["client"] is identity:
                record["state"], record["error"] = "unavailable", MODEL_UNAVAILABLE


def _probe_model(client: Any) -> str:
    """One bounded readiness request; a timed-out worker is never duplicated while in flight."""
    if client is None:
        logger.debug("model failure step=plan category=ClientUnavailable status=none elapsed_ms=0")
        return MODEL_UNAVAILABLE
    context = contextvars.copy_context()
    with _PROBES_LOCK:
        record = _find_probe(client, context)
        if record and record["state"] == "ready":
            return ""
        if not record or record["done"].is_set():
            # Do not accumulate unbounded client references; never evict an in-flight probe.
            if record:
                _PROBES.remove(record)
            while len(_PROBES) >= 64:
                old = next((r for r in _PROBES if r["done"].is_set()), None)
                if old is None:
                    logger.debug("model failure step=plan category=ProbeCapacity status=none elapsed_ms=0")
                    return MODEL_UNAVAILABLE
                _PROBES.remove(old)
            record = {"client": _client_identity(client), "home": _probe_home(), "context": context.copy(),
                      "state": "untested", "done": threading.Event(), "error": ""}
            _PROBES.append(record)

            def run() -> None:
                started = time.monotonic()
                try:
                    result = client.complete(messages=[{"role": "user", "content": "Reply OK."}],
                                             max_tokens=4, timeout=MODEL_PROBE_TIMEOUT_S,
                                             purpose="onair.setup-preview")
                    if not (getattr(result, "text", "") or "").strip():
                        raise _EmptyModelResponse()
                except Exception as exc:  # noqa: BLE001 - never include provider exception text
                    _log_model_failure("plan", exc, started)
                    error = MODEL_REJECTED_429 if _status_code(exc) == 429 else MODEL_UNAVAILABLE
                else:
                    error = ""
                with _PROBES_LOCK:
                    if record["state"] != "unavailable":  # a timeout is not reversed by a late answer
                        record["state"] = "unavailable" if error else "ready"
                        record["error"] = error
                    record["done"].set()

            threading.Thread(target=context.run, args=(run,), name="onair-model-probe", daemon=True).start()
    if not record["done"].wait(MODEL_PROBE_TIMEOUT_S):
        with _PROBES_LOCK:
            if not record["done"].is_set():
                record["state"], record["error"] = "unavailable", MODEL_UNAVAILABLE
                logger.debug("model failure step=plan category=TimeoutError status=none elapsed_ms=%d",
                             int(MODEL_PROBE_TIMEOUT_S * 1000))
    return record["error"]


class _EmptyModelResponse(Exception):
    pass


def _status_code(exc: BaseException) -> int | None:
    try:
        code = getattr(exc, "status_code", None)
    except Exception:  # noqa: BLE001 - a broken attribute is not a classification
        return None
    return code if isinstance(code, int) and not isinstance(code, bool) else None


class _ModelDraftError(SetupError):
    """The preview's model draft failed: the MODEL step failed (not only the preview runtime)."""

    def __init__(self, message: str, *, rejected_429: bool = False):
        super().__init__(message)
        self.rejected_429 = rejected_429


class OwnerConsent:
    """A human's approval of one exact plan digest. Minted only by :func:`owner_consent`."""

    __slots__ = ("digest", "source", "issued_at")

    def __init__(self, key: object, digest: str, source: str):
        if key is not _MINT:
            raise SetupError("approval can only come from the owner's dashboard or slash command")
        self.digest, self.source, self.issued_at = digest, source, time.monotonic()


_MINT = object()


def owner_consent(digest: str, source: str) -> OwnerConsent:
    """Called ONLY by owner entry points after a human action (dashboard click, approval card)."""
    if source not in ("dashboard", "slash-command"):
        raise SetupError("unknown approval source")
    return OwnerConsent(_MINT, str(digest), source)


def owner_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def is_stage_home(home: Path | None = None) -> bool:
    from .onboarding import MARKER
    return ((home or owner_home()) / MARKER).is_file()


# ---- model connection: names only, through Hermes's public config reader -------------------------
def model_connection(model_client: Any) -> dict:
    """The configured model connection as a LABEL (provider · model) and whether it is usable here.

    Reads only ``model.provider`` and the model name with ``hermes_cli.config.load_config_readonly``;
    keys stay with Hermes (``ctx.llm`` uses host-owned credentials and never shows them)."""
    provider = name = ""
    try:
        from hermes_cli.config import load_config_readonly
        model = (load_config_readonly() or {}).get("model")
        if isinstance(model, dict):
            provider = model.get("provider") or ""
            name = model.get("default") or model.get("model") or ""
        elif isinstance(model, str):
            name = model
    except Exception:  # noqa: BLE001 - no config reader: report not configured
        pass
    parts = [p for p in (provider, name) if isinstance(p, str) and _LABEL.fullmatch(p.strip())]
    label = " · ".join(p.strip() for p in parts)
    if not label:
        return {"configured": False, "label": "", "usable_here": False, "state": "untested",
                "next_action": ("No model connection is set up in Hermes yet. Set one up with Hermes's own "
                                "model setup (hermes model); it asks for any key itself. On Air never asks "
                                "for keys. Until then the preview uses a canned stand-in reply.")}
    state = _model_state(model_client)
    untested = ("Not tested from this page. Ask your Hermes agent to plan setup: it tests the connection "
                "before you approve." if model_client is None else
                "The model connection is untested here. Ask your Hermes agent to plan setup and test it.")
    return {"configured": True, "label": label, "usable_here": state == "ready", "state": state,
            "next_action": "" if state == "ready" else (MODEL_UNAVAILABLE if state == "unavailable" else untested)}


# ---- the engine ---------------------------------------------------------------------------------
class SetupEngine:
    def __init__(self, home: Path | None = None, *, model_client: Any = None,
                 step_hook: Callable[[str], None] | None = None):
        self.home = Path(home or owner_home())
        self.dir = self.home / "plugin-data" / "on-air" / "setup"
        self._model_client_factory: Callable[[], Any] = lambda: model_client
        self.tts_dispatch: Any = _TTS_DISPATCH  # ctx.dispatch_tool: Hermes's configured text_to_speech
        self.step_hook = step_hook  # tests: block/interrupt between steps
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self.preview: Any = None
        self._draining: list = []  # stopped shows whose model request is still finishing

    @property
    def model_client(self) -> Any:
        started = time.monotonic()
        try:
            return self._model_client_factory()
        except Exception as exc:  # noqa: BLE001 - host facade resolution is a model failure
            _log_model_failure("resolve", exc, started)
            return None

    @model_client.setter
    def model_client(self, client: Any) -> None:
        self._model_client_factory = lambda: client

    # -- persisted state
    def _path(self, name: str) -> Path:
        return self.dir / name

    def _read(self, name: str) -> dict | None:
        try:
            value = json.loads(self._path(name).read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def _write(self, name: str, value: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self._path(name + f".{os.getpid()}.{threading.get_ident()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, sort_keys=True)
        os.replace(tmp, self._path(name))

    @contextlib.contextmanager
    def _file_lock(self, *, blocking: bool = True):
        """Cross-process lock on this home's setup folder; re-entrant within one thread of one engine."""
        local = self.__dict__.setdefault("_flock_local", threading.local())
        if getattr(local, "depth", 0):
            local.depth += 1
            try:
                yield
            finally:
                local.depth -= 1
            return
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self._path("lock"), os.O_RDWR | os.O_CREAT, 0o600)
        locked = False
        try:
            if os.name == "nt":
                import msvcrt
                if not os.fstat(fd).st_size:
                    os.write(fd, b"\0")
                while True:
                    os.lseek(fd, 0, os.SEEK_SET)
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if not blocking:
                            raise BlockingIOError("Setup is busy") from exc
                        time.sleep(.05)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            locked = True
            local.depth = 1
            yield
        finally:
            local.depth = 0
            if locked:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    # -- status
    def job(self) -> dict | None:
        job = self._read("job.json")
        if job and job.get("state") in _ACTIVE and not self._job_alive(job):
            # Re-read after observing death: the worker may have finished between the first
            # read and the liveness check, publishing its terminal state in the meantime.
            job = self._read("job.json")
            if not job or job.get("state") not in _ACTIVE or self._job_alive(job):
                return job
            # The process that ran this job is gone (or it is ours but its thread died): recover honestly.
            job.update(state="interrupted", error="Setup was interrupted before it finished.",
                       next_action="Press Set up & preview again; the half-made folder is removed first.")
            for step in job.get("steps", []):
                if step.get("state") == "running":
                    step["state"] = "interrupted"
            with contextlib.suppress(OSError):
                self._write("job.json", job)
        return job

    def _job_alive(self, job: dict) -> bool:
        pid = job.get("pid")
        if pid == os.getpid():  # by thread name, so a second copy of this module in-process agrees
            return _thread_alive("onair-setup-" + str(job.get("id")))
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def status(self) -> dict:
        from .setup_wizard import suggested_target
        job = self.job()
        saved = self._read("saved.json")
        saved_ok = bool(saved and isinstance(saved.get("home"), str) and is_stage_home(Path(saved["home"])))
        preview = self.preview_facts()
        plan = self._read("plan.json")
        return {
            "ok": True,
            "model_connection": model_connection(self.model_client),
            "saved_stage": ({"home": saved["home"], "channel": saved.get("channel", ""),
                             "agent_name": saved.get("agent_name", ""), "style": _style_or_none(saved["home"])}
                            if saved_ok else None),
            "customization": "Public style fields are discoverable with schema(): persona, tone, topics, "
                             "avoid_topics, reply_length, reply_pacing, output.",
            "job": _public_job(job),
            "preview": preview,
            "pending_plan": ({k: plan[k] for k in ("digest", "code", "effects", "target", "kind", "answers")}
                             if plan and (not job or job.get("digest") != plan.get("digest")) else None),
            "suggested_target": suggested_target(),
            "missing": [] if saved_ok else ["channel"],
            "real_stage": _real_stage(saved["home"]) if saved_ok else None,
            "show": _show_summary(self) if saved_ok else None,
        }

    # -- plan
    def plan(self, answers: Any, *, remember: bool = True) -> dict:
        from .setup_wizard import NO_TARGET_ACTION, check_agent_target, options_from, suggested_target, fallback_target
        from .onboarding import StageHomeError, check_target, validate_options
        if not isinstance(answers, dict):
            return {"ok": False, "errors": {"form": "answers must be an object"}}
        if is_stage_home(self.home):
            return {"ok": False, "errors": {"form": "This Hermes home is an On Air stage; set up from your own "
                                                    "Hermes home instead."}}
        from . import style as st
        unknown = sorted(set(answers) - {"channel", "host_name", "agent_name", "use_model", "target", "stage_home", "style"})
        if unknown:
            return {"ok": False, "errors": {k: f"{k!r} is not a setup answer; supported: channel, host_name, "
                                               "agent_name, use_model, style." for k in unknown}}
        patch, style_errors = st.validate_patch(answers.get("style"))
        if style_errors:
            return {"ok": False, "errors": {"style." + k: v for k, v in style_errors.items()}}
        if "target" in answers and "stage_home" in answers:
            return {"ok": False, "errors": {"target": "Choose only one stage folder."}}
        clean = {k: answers.get(k) for k in ("channel", "host_name", "agent_name", "use_model", "target", "stage_home")
                 if answers.get(k) is not None}
        if patch:
            clean["style"] = patch
        use_model = clean.get("use_model")
        if use_model is not None and not isinstance(use_model, bool):
            return {"ok": False, "errors": {"use_model": "Choose yes or no for the model connection."}}
        saved = self._read("saved.json")
        resume = bool(not clean.get("channel") and saved and isinstance(saved.get("home"), str)
                      and is_stage_home(Path(saved["home"])))
        client = self.model_client
        connection = model_connection(client)
        missing = []
        before = None
        fallback_reason = None
        automatic_target = not (clean.get("target") or clean.get("stage_home"))
        if resume:
            if not automatic_target:
                return {"ok": False, "errors": {"target": "A saved stage cannot be moved by setup; provide a channel to create a new stage."}}
            kind, target = "resume", saved["home"]
            channel, agent = saved.get("channel", ""), saved.get("agent_name", "Co-host")
            # Identity of an existing stage is not changed by setup: refuse explicitly, never drop silently.
            renamed = [k for k in ("host_name", "agent_name") if clean.get(k) not in (None, "", saved.get(k))]
            if renamed:
                return {"ok": False, "errors": {k: ("This saved stage keeps its names (co-host "
                                                    f"{agent!r}); renaming is not supported by setup. Leave it "
                                                    "out, or set up a new stage.") for k in renamed}}
            before = _style_or_none(target) or {}
            if st.changes(before, patch):
                kind = "customize"
        else:
            kind = "create"
            fields = {"channel": clean.get("channel") or "", "host_name": clean.get("host_name") or "Host",
                      "agent_name": clean.get("agent_name") or "Co-host",
                      "target": clean.get("stage_home") or clean.get("target") or suggested_target()}
            if not fields["channel"]:
                missing.append("channel")
            try:
                target, options, errors = options_from(fields)
            except StageHomeError as exc:
                return {"ok": False, "errors": {"form": str(exc)}}
            if missing:
                errors.pop("channel", None)
            if not errors and not missing:
                try:
                    validate_options(options)
                except StageHomeError as exc:
                    errors["target"] = str(exc)
                try:
                    if not errors:
                        target = os.fspath(check_agent_target(target) if "stage_home" in clean else check_target(target))
                except StageHomeError as exc:
                    if automatic_target:
                        alternative = fallback_target(target)
                        if alternative:
                            fallback_reason = str(exc)
                            target = alternative
                        else:
                            errors["target"] = f"{exc} {NO_TARGET_ACTION}"
                    else:
                        errors["target"] = str(exc)
            if errors:
                return {"ok": False, "errors": errors, "missing": missing}
            channel, agent = options.channel, options.agent_name
            clean.update(channel=channel, host_name=options.host_name, agent_name=agent)
            if not automatic_target:
                clean["stage_home" if "stage_home" in clean else "target"] = target
        if use_model is None and connection["configured"] and client is not None:
            missing.append("use_model")
        if missing:
            return {"ok": False, "missing": missing, "errors": {}, "questions": [_QUESTIONS[m] for m in missing],
                    "model_connection": connection}
        if use_model:
            if not connection["configured"]:
                return {"ok": False, "error": connection["next_action"], "next_action": connection["next_action"],
                        "model_connection": connection}
            error = _probe_model(client)
            connection = model_connection(client)
            if error:
                return {"ok": False, "error": error, "next_action": error,
                        "model_connection": connection}
        use = bool(use_model)
        effects = []
        if kind == "create":
            if fallback_reason:
                effects.append(f"The default stage folder was not used: {fallback_reason} Chosen alternative: {target}.")
            effects.append(f"Create a new private stage folder {target} for a public co-host called {agent}. "
                           "It gets its own public persona: none of your own agent's memory, profile, files, "
                           "tools, model or keys are copied into it.")
            effects.append(f"Save twitch.tv/{channel} as the channel (read-only, no login). Setup does not "
                           "connect to Twitch.")
        elif kind == "resume":
            effects.append(f"Use your saved stage folder {target} (twitch.tv/{channel}); nothing in it is changed.")
        else:
            effects.append(f"Use your saved stage folder {target} (twitch.tv/{channel}).")
        style_lines = st.changes(before, patch)
        if style_lines:
            effects.append("Save the co-host's public style into that stage's own settings and public SOUL.md: "
                           + "; ".join(style_lines) + ". Subjects to avoid also join its never-say guard. "
                           "The private preview restarts with it.")
        if use:
            effects.append(f"Use your configured model connection ({connection['label']}) for ONE short request: "
                           "draft a reply to the private test message. Planning may already have made one short "
                           "connection test; your provider counts both as normal usage. "
                           "Hermes keeps the key; On Air never sees it.")
        else:
            effects.append("Draft the test reply with a canned stand-in (no model is used).")
        effects.append("Start a private preview on this computer (127.0.0.1) with one test message from you. "
                       "Nothing reaches Twitch, OBS, your live overlay or viewers, and the reply waits for you.")
        body = {"schema": SCHEMA, "kind": kind, "target": target, "channel": channel, "agent_name": agent,
                "host_name": clean.get("host_name", saved.get("host_name", "Host") if resume else "Host"),
                "use_model": use, "model_label": connection["label"] if use else "", "effects": effects,
                "style_patch": patch, "style_before": before}
        if "stage_home" in clean or fallback_reason:
            body["checked_agent_target"] = True
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        result = {"ok": True, "digest": digest, "code": digest[:8], "kind": kind, "target": target,
                  "effects": effects, "uses_model": use, "answers": clean, "model_connection": connection,
                  "approve_with": {"dashboard": "Set up & preview", "chat": f"/onair-setup approve {digest[:8]}"}}
        if remember:
            with self._lock:
                self._write("plan.json", {**result, "body": body, "planned_at": time.time()})
        return result

    # -- apply
    def apply(self, digest: Any, consent: Any) -> dict:
        if not isinstance(consent, OwnerConsent):
            return {"ok": False, "error": "Not approved by you. Approve this setup in the dashboard or with "
                                          "/onair-setup approve in your own chat; nothing was changed."}
        if not isinstance(digest, str) or consent.digest != digest:
            return {"ok": False, "error": "That approval is for a different plan; nothing was changed."}
        if time.monotonic() - consent.issued_at > CONSENT_TTL_S:
            return {"ok": False, "error": "That approval expired; review the plan again."}
        with self._lock, self._file_lock():
            job = self.job()
            # A repeated click while the job runs, or while the preview it started still runs, is the same
            # job. A finished job whose preview has since stopped is NOT: a new authorized resume restarts it.
            if job and job.get("digest") == digest and (
                    job.get("state") in _ACTIVE
                    or (job.get("state") == "done" and self.preview_facts().get("running"))):
                return {"ok": True, "duplicate": True, "job": _public_job(job)}
            if job and job.get("state") in _ACTIVE:
                return {"ok": False, "error": "Another setup is still running; wait for it or cancel it.",
                        "job": _public_job(job)}
            stored = self._read("plan.json")
            if not stored or stored.get("digest") != digest:
                return {"ok": False, "error": "This plan is no longer the current one; review it again."}
            fresh = self.plan(stored.get("answers") or {}, remember=False)
            if not fresh.get("ok") or fresh.get("digest") != digest:
                return {"ok": False, "stale": True, "error": "Something changed since you reviewed this plan "
                        "(for example the folder now exists or the model changed); nothing was changed. "
                        "Review the new plan.", "plan": fresh if fresh.get("ok") else None}
            if job and job.get("state") == "interrupted":
                self._rollback(job)
            if self._foreign_owner_alive() is not None:  # one preview at a time; never replace it unseen
                return {"ok": False, "error": "A private preview started elsewhere (dashboard or agent) is still "
                        "running. Stop it first (Stop, or the preview safety stop), then apply again; nothing "
                        "was changed.", "job": _public_job(job)}
            if (self.preview is not None and not _quiescent(self.preview)) or self._still_draining() \
                    or self._published_drain() is not None:  # never replace work that is still finishing
                return {"ok": False, "draining": True, "error": "A model request from the private preview (here, "
                        "in the dashboard or in the agent) is still finishing. Stop it and wait until status says "
                        "it is stopped (quiescent), then apply again; nothing was changed.", "job": _public_job(job)}
            if self.preview is not None:  # only this engine's own show here: we hold the shared file lock
                self._release_own(self.preview)
            self._cancel.clear()
            self._job_id = secrets.token_hex(8)
            job = {"id": self._job_id, "digest": digest, "state": "running", "pid": os.getpid(),
                   "approved_by": consent.source, "started_at": time.time(), "updated_at": time.time(),
                   "kind": stored["body"]["kind"], "error": "", "next_action": "", "home": "", "created_home": None,
                   "steps": [{"key": k, "label": label, "state": "waiting", "detail": ""} for k, label in STEPS]}
            published = threading.Event()
            ready = [False]
            def run_after_publish():
                published.wait()
                if ready[0]:
                    self._run(job, stored["body"])
            job_context = contextvars.copy_context()
            self._thread = threading.Thread(target=job_context.run, args=(run_after_publish,),
                                            name="onair-setup-" + self._job_id, daemon=True)
            self._thread.start()
            try:
                self._write("job.json", job)
                ready[0] = True
            finally:
                published.set()
        return {"ok": True, "duplicate": False, "job": _public_job(job)}

    def _cancelled(self, job: dict) -> bool:
        return self._cancel.is_set() or self._path("cancel-" + str(job.get("id"))).exists()

    def _step(self, job: dict, key: str, state: str, detail: str = "") -> None:
        if job.get("state") == "running" and self._cancelled(job):
            job["state"] = "cancelling"  # never report cancelled while a step is still executing
        for step in job["steps"]:
            if step["key"] == key:
                step.update(state=state, detail=detail)
        job["updated_at"] = time.time()
        self._write("job.json", job)

    def _run(self, job: dict, body: dict) -> None:
        from .onboarding import StageHomeError, StageOptions, check_target, create_stage_home
        key = "check"
        try:
            for key, _ in STEPS:
                if self.step_hook:
                    self.step_hook(key)
                if self._cancelled(job):
                    raise _Cancelled()
                self._step(job, key, "running")
                if key == "check":
                    if body["kind"] == "create":
                        if body.get("checked_agent_target"):
                            from .setup_wizard import check_agent_target
                            check_agent_target(body["target"])
                        else:
                            check_target(body["target"])
                    elif not is_stage_home(Path(body["target"])):
                        raise SetupError("The saved stage folder is gone. Set up a new one.")
                    self._step(job, key, "done", "Answers valid; the folder is free." if body["kind"] == "create"
                               else "Saved stage folder found.")
                elif key == "create":
                    if body["kind"] in ("resume", "customize"):
                        job["home"] = body["target"]
                        self._step(job, key, "skipped", "Already created earlier.")
                        continue
                    if body.get("checked_agent_target"):
                        from .setup_wizard import check_agent_target
                        check_agent_target(body["target"])
                    created = create_stage_home(body["target"], StageOptions(
                        channel=body["channel"], host_name=body["host_name"], agent_name=body["agent_name"]))
                    info = os.lstat(created["home"])
                    job["home"] = os.fspath(created["home"])
                    job["created_home"] = [info.st_dev, info.st_ino]
                    self._write("saved.json", {"home": job["home"], "channel": body["channel"],
                                               "agent_name": body["agent_name"], "host_name": body["host_name"]})
                    self._step(job, key, "done", f"Created {job['home']} ({len(created['files'])} files).")
                elif key == "style":
                    self._apply_style(job, body)
                elif key == "model":
                    if not body["use_model"]:
                        self._step(job, key, "skipped", "Not used: the preview drafts with a canned stand-in.")
                        continue
                    self._step(job, key, "running", "Waiting for the model's reply…")
                elif key == "preview":
                    self._start_preview(job, body)
                elif key == "readback":
                    self._readback(job)
            if self._cancelled(job):
                raise _Cancelled()
            job.update(state="done", next_action=job.get("real_stage_note", ""))
            with contextlib.suppress(OSError):
                self._path("style-backup-" + job["id"] + ".json").unlink()
        except _Cancelled:
            for step in job["steps"]:
                if step["state"] in ("running", "waiting"):
                    step["state"] = "cancelled"
            self._rollback(job)
            job.update(state="cancelled", error="", next_action="Nothing is left behind. Press Set up & preview "
                                                              "to start again.")
        except _ModelDraftError as exc:  # raised inside the preview step, but it is the model step that failed
            self._fail(job, "model", str(exc), next_action=(
                "Fix the model's access or limits in Hermes (for example with `hermes model`) before trying "
                "again; then press Set up & preview again. Your saved stage and style are reused."
                if exc.rejected_429 else "Press Set up & preview again; your saved folder is reused."))
        except (SetupError, StageHomeError) as exc:
            self._fail(job, key, str(exc))
        except Exception as exc:  # noqa: BLE001 - reported, never raised into the host
            self._fail(job, key, f"Unexpected error ({type(exc).__name__}).")
        job["updated_at"] = time.time()
        self._write("job.json", job)
        with contextlib.suppress(OSError):
            self._path("cancel-" + str(job.get("id"))).unlink()

    def _apply_style(self, job: dict, body: dict) -> None:
        from . import style as st
        patch = body.get("style_patch") or {}
        if not patch:
            self._step(job, "style", "skipped", "No style change: the saved style is used.")
            return
        home = Path(job["home"])
        before = st.read_style(home)
        files = st.render_files(home, st.merged(before, patch), before)
        if body["kind"] == "customize":  # the folder is not ours to remove: keep exact bytes to restore
            backup = {name: (home / name).read_text(encoding="utf-8") for name in files}
            self._write("style-backup-" + job["id"] + ".json", {"home": os.fspath(home), "files": backup})
            job["style_backup"] = True
        st.write_files(home, files)
        after = st.read_style(home)
        if after != st.merged(before, patch):
            raise SetupError("The style was written but did not read back the same; nothing else was started.")
        self._step(job, "style", "done", "Saved and re-read: " + "; ".join(st.changes(before, patch)) + ".")

    def _fail(self, job: dict, key: str, message: str, next_action: str | None = None) -> None:
        self._step(job, key, "failed", message)
        # A failed job leaves no step 'running': a later step that was already working stopped because of
        # this failure; an earlier one that was still working (the model step while the preview started) did
        # not finish. Neither is reported done.
        order = [k for k, _ in STEPS]
        for step in job["steps"]:
            if step["key"] != key and step.get("state") == "running":
                if order.index(step["key"]) > order.index(key):
                    # Neutral on purpose: the preview's teardown runs after this and may still be draining.
                    step.update(state="failed", detail=f"Not completed: the {key} step failed, so no suggested "
                                "reply is waiting. Check the private preview's status before trying again.")
                else:
                    step.update(state="interrupted", detail=f"Not finished: the {key} step failed first.")
        nxt = next_action or {"check": "Fix the answer above, then press Set up & preview again.",
                              "create": "Choose another folder, then press Set up & preview again.",
                              "model": "Retry, or turn the model connection off to preview with the canned stand-in.",
                              "preview": "Press Set up & preview again; your saved folder is reused.",
                              "readback": "Press Set up & preview again."}.get(key, "Press Set up & preview again.")
        job.update(state="failed", error=message, next_action=nxt)
        if key in ("model", "preview", "readback") and self.preview is not None:
            self._stop_own()
        if key == "style":
            self._restore_style(job)

    def _restore_style(self, job: dict) -> None:
        backup = self._read("style-backup-" + str(job.get("id")) + ".json")
        if backup and job.get("style_backup"):
            from . import style as st
            st.write_files(Path(backup["home"]), backup["files"])
        with contextlib.suppress(OSError):
            self._path("style-backup-" + str(job.get("id")) + ".json").unlink()

    def _rollback(self, job: dict) -> None:
        """Stop the preview and remove ONLY the folder this job created (same inode, still marked)."""
        from .onboarding import MARKER, _remove_owned
        self._stop_own()
        self._restore_style(job)
        home, owned = job.get("home"), job.get("created_home")
        if home and owned and os.path.lexists(home):
            try:
                info = os.lstat(home)
                if [info.st_dev, info.st_ino] == list(owned) and (Path(home) / MARKER).is_file():
                    _remove_owned(Path(home), (info.st_dev, info.st_ino))
            except OSError:
                pass
            saved = self._read("saved.json")
            if saved and saved.get("home") == home and not os.path.lexists(home):
                with contextlib.suppress(OSError):
                    self._path("saved.json").unlink()

    def cancel(self) -> dict:
        with self._lock:
            job = self.job()
            if not job or job.get("state") not in _ACTIVE:
                return {"ok": True, "job": _public_job(job), "note": "Nothing is running."}
            if job.get("pid") == os.getpid() and job.get("id") == getattr(self, "_job_id", None):
                self._cancel.set()
            # The running job (in this or another Hermes process) polls this flag between steps.
            self._write("cancel-" + str(job["id"]), {"at": time.time()})
            shown = {**(_public_job(job) or {}), "state": "cancelling"}
            return {"ok": True, "job": shown,
                    "note": "Cancelling: the current step finishes, then everything it made is removed."}

    def cancel_all(self, settle_s: float = 3.0) -> dict:
        """Cancel the setup job AND stop the private preview (every cancel surface uses this): ok only when
        both are complete; a preview stop that is not confirmed (draining, owner did not answer) makes the
        whole result an honest partial failure instead of a success. An accepted cancel request (the job still
        'cancelling' or 'running') is NOT a completed cancel: ok needs the job to have reached a terminal state."""
        out = self.cancel()
        deadline = time.monotonic() + settle_s
        job = self.job()
        while job and job.get("state") in _ACTIVE and time.monotonic() < deadline:
            time.sleep(0.1)  # let the job reach its next step boundary and clean up (bounded)
            job = self.job()
        stop = self.stop_preview()
        out["preview"] = stop
        problems = []
        if job and job.get("state") in _ACTIVE:
            out["job"] = {**(_public_job(job) or {}), "state": "cancelling"}
            problems.append("The setup job has NOT finished cancelling yet (it stops at its next step).")
        elif job:
            out["job"] = _public_job(job)
        if not stop.get("ok"):
            problems.append("The private preview is NOT confirmed stopped: "
                            + str(stop.get("error") or "the stop could not be confirmed."))
        if problems:
            out.update(ok=False, partial=True, draining=bool(stop.get("draining")),
                       job_pending=bool(job and job.get("state") in _ACTIVE),
                       error=" ".join(problems) + " Check status again.")
            out["note"] = out["error"]  # never 'Nothing is running.' beside an unconfirmed part
        return out

    def wait(self, timeout: float = 30.0) -> dict | None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return _public_job(self.job())

    # -- preview runtime
    def _start_preview(self, job: dict, body: dict) -> None:
        from .yamlio import safe_load
        home = Path(job["home"])
        config = safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}
        settings = (((config.get("plugins") or {}).get("entries") or {}).get("on-air") or {}).get("settings") or {}
        persona = (home / "SOUL.md").read_text(encoding="utf-8")
        from . import style as st
        client_started = time.monotonic()
        client = self.model_client if body["use_model"] else None
        if body["use_model"] and client is None:
            logger.debug("model failure step=preview category=ClientUnavailable status=none elapsed_ms=%d",
                         int((time.monotonic() - client_started) * 1000))
            raise _ModelDraftError(MODEL_UNAVAILABLE)
        show = PreviewShow(name="onair-setup-preview-" + job["id"], settings=settings, persona=persona,
                           style=st.read_style(home),
                           model_client=client,
                           facts_path=self._path("preview-facts.json"))
        show.start()
        self.preview = show
        token_path = self._path("preview.token")
        fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(show.control.token + "\n")
        self._write("preview.json", {"url": show.control.url, "pid": os.getpid(), "job": job["id"],
                                     "overlay_url": show.overlay.overlay_url,
                                     "stage_overlay_port": settings.get("overlay_port")})
        show._publish()
        # The owner lets go of its private ports as soon as its show is put off air through the control API
        # (a routed Stop from another engine copy or process), not on some later status read.
        threading.Thread(target=self._watch_release, args=(show,), name="onair-setup-release-" + job["id"],
                         daemon=True).start()
        started = time.monotonic()
        show.send_test_message()
        deadline = time.monotonic() + MODEL_TIMEOUT_S + 5
        while time.monotonic() < deadline and not self._cancelled(job):
            facts = show.facts()
            if facts["pending"] or facts["draft_error"]:
                break
            time.sleep(0.05)
        facts = show.facts()
        if self._cancelled(job):
            raise _Cancelled()
        if body["use_model"]:
            if facts["draft_error"]:
                raise _ModelDraftError(facts["draft_error"], rejected_429=facts["draft_error"] == MODEL_REJECTED_429)
            if not facts["pending"]:
                _log_model_failure("preview", TimeoutError(), started)
                _invalidate_client(client)
                raise _ModelDraftError("The model did not answer in time.")
            self._step(job, "model", "done", f"Your model connection ({body['model_label']}) drafted a reply.")
        if not facts["pending"]:
            raise SetupError(facts["draft_error"] or "The preview started but no reply was drafted.")
        self._step(job, "preview", "done", "Private preview running on 127.0.0.1; a suggested reply is waiting.")

    def _readback(self, job: dict) -> None:
        from .setup_wizard import readiness
        ready = readiness(job["home"])
        if not ready.get("ok"):
            raise SetupError(ready.get("error") or "The saved settings could not be read back.")
        # The private preview is a rehearsal of the saved settings, not the real show: carry the stage's own
        # re-read readiness (plugin install, model, preflight, gateway) into the finished job honestly.
        left = len(ready.get("steps") or [])
        job["real_stage_note"] = ("Private preview only. The real show is not set up yet: " + str(left) + " step"
                                  + ("s" if left != 1 else "") + " left (see the On Air desk setup steps)."
                                  if left else "")
        state, payload = preview_call(self.dir, "GET", "/state")
        if state != 200 or not isinstance(payload, dict) or payload.get("mode") != "approval":
            raise SetupError("The private preview did not answer its own control check.")
        from . import style as st
        saved = st.read_style(Path(job["home"]))
        live = self.preview.style_in_effect() if self.preview is not None else {}
        worthy, gap = st.PACING[saved["reply_pacing"]]
        if (live.get("tone") != saved["tone"] or live.get("max_worthy_per_turn") != worthy
                or live.get("routine_gap_s") != gap or not set(saved["avoid_topics"]) <= set(live.get("never_say") or ())
                or live.get("max_tokens") != st.LENGTHS[saved["reply_length"]][1]):
            raise SetupError("The preview is not using the saved style; it was stopped. Press Set up & preview again.")
        rows = {r["key"]: r["state"] for r in ready["rows"]}
        self._step(job, "readback", "done",
                   f"Settings re-read from disk (channel {rows.get('channel')}, private context "
                   f"{rows.get('isolation')}); the preview answers in approval mode and uses the saved style "
                   f"(tone {saved['tone']}, pacing {saved['reply_pacing']}, length {saved['reply_length']}).")

    # -- who owns the running preview -----------------------------------------------------------------
    def _owner_info(self) -> dict | None:
        """preview.json when its owner is alive: a live process, and (same process) the owner's show thread."""
        info = self._read("preview.json")
        if not info:
            return None
        try:
            os.kill(int(info.get("pid")), 0)
        except (OSError, TypeError, ValueError):
            return None
        if info.get("pid") == os.getpid() and not _thread_alive("onair-setup-preview-" + str(info.get("job"))):
            return None
        return info

    def _foreign_owner_alive(self) -> dict | None:
        """The live owner when it is NOT this engine's own show (another process, or another copy of this
        module in the same process, e.g. dashboard vs. installed plugin)."""
        info = self._owner_info()
        show = self.preview
        if info is None or (show is not None and _identity(show) == (str(info.get("job")), info.get("pid"))):
            return None
        return info

    def _names_show(self, show: Any) -> bool:
        """True only while the shared discovery files still name THIS show: job + pid + token."""
        info = self._read("preview.json") or {}
        try:
            token = self._path("preview.token").read_text(encoding="ascii").strip()
        except OSError:
            token = ""
        control = getattr(show, "control", None)
        return (_identity(show) == (str(info.get("job")), info.get("pid"))
                and bool(token) and token == getattr(control, "token", None))

    def _release_own(self, show: Any) -> None:
        """Close this engine's own show and compare-and-delete the shared discovery/token/facts: files that
        already name a newer preview (another copy resumed it meanwhile) are left alone."""
        with self._lock:
            if self.preview is show:
                self.preview = None
            if getattr(show, "_released", False):
                return
            show._released = True  # no further facts publishing from this show
            draining = not _quiescent(show)
            if draining:
                self._draining.append(show)
        if draining:  # published for every copy/process until this owner records that the request has ended
            with contextlib.suppress(Exception), self._file_lock():
                reg = self._read("preview-draining.json") or {}
                owners = reg.get("owners")
                owners = owners if isinstance(owners, dict) else {}
                owners[f"{show.name}:{os.getpid()}"] = {"owner_job": show.name, "owner_pid": os.getpid()}
                self._write("preview-draining.json", {"owners": owners})
            threading.Thread(target=self._quiescence_watch, args=(show,), daemon=True,
                             name="onair-setup-drain-" + show.name).start()
        named = None
        with contextlib.suppress(Exception), self._file_lock():
            named = self._names_show(show)
        show.close()
        loop_gone = not (show.thread is not None and show.thread.is_alive())
        with self._file_lock():
            if named and loop_gone and self._names_show(show):  # its servers are closed: let go of discovery
                for name in ("preview.json", "preview.token"):
                    with contextlib.suppress(OSError):
                        self._path(name).unlink()
            facts = self._read("preview-facts.json")
            if facts and (facts.get("owner_job"), facts.get("owner_pid")) == (show.name, os.getpid()):
                facts.update(running=not loop_gone, mode="off_air", pending=0, quiescent=_quiescent(show))
                with contextlib.suppress(OSError):
                    self._write("preview-facts.json", facts)

    def _still_draining(self) -> bool:
        with self._lock:
            self._draining = [d for d in self._draining if not _quiescent(d)]
            return bool(self._draining)

    def _quiescence_watch(self, show: Any) -> None:
        """After a stop, record when the last in-flight model request has finished (it can no longer show
        anything: the show is closed and its epoch fenced), so status can say so."""
        deadline = time.monotonic() + MODEL_TIMEOUT_S + 10
        while not _quiescent(show) and time.monotonic() < deadline:
            time.sleep(0.05)
        with self._file_lock():
            facts = self._read("preview-facts.json")
            if facts and (facts.get("owner_job"), facts.get("owner_pid")) == (show.name, os.getpid()):
                facts["quiescent"] = _quiescent(show)
                with contextlib.suppress(OSError):
                    self._write("preview-facts.json", facts)
            if _quiescent(show):  # only the owner itself retires its draining record, and only once it has ended
                reg = self._read("preview-draining.json") or {}
                owners = reg.get("owners")
                owners = owners if isinstance(owners, dict) else {}
                if owners.pop(f"{show.name}:{os.getpid()}", None) is not None:
                    with contextlib.suppress(OSError):
                        self._write("preview-draining.json", {"owners": owners})

    def _published_drain(self) -> dict | None:
        """A stopped preview whose LIVE owner (this or another copy/process) has published that its model
        request is still in flight. Read coherently under the shared file lock; never inferred from this
        engine's own memory, and never erased by a non-owner. A dead owner process cannot still be drafting."""
        with self._file_lock():
            reg = self._read("preview-draining.json") or {}
            facts = self._read("preview-facts.json") or {}
        listed = reg.get("owners")
        owners = list(listed.values()) if isinstance(listed, dict) else []
        if facts.get("quiescent") is False and facts.get("owner_job") and facts.get("running") is not True:
            owners.append({"owner_job": facts.get("owner_job"), "owner_pid": facts.get("owner_pid")})
        for entry in owners:
            try:
                os.kill(int(entry.get("owner_pid")), 0)
            except (OSError, TypeError, ValueError):
                continue
            return entry
        return None

    def _watch_release(self, show: Any) -> None:
        while not getattr(show, "_released", False):
            if not show.alive or _off_air(show):
                self._release_own(show)
                return
            time.sleep(0.05)

    def _routed_stop(self, owner: dict) -> dict:
        """Stop a preview owned elsewhere through its token-authenticated loopback control API, then wait
        for the owner itself to let go (its ports close and the discovery files stop naming it)."""
        s1, _ = preview_call(self.dir, "POST", "/stop", b"{}")
        s2, pending = preview_call(self.dir, "GET", "/pending")
        lines = (pending.get("pending") if isinstance(pending, dict) else pending) or []
        s3, state = preview_call(self.dir, "POST", "/mode", json.dumps({"mode": "off_air"}).encode())
        mode = (state.get("state") or state).get("mode") if isinstance(state, dict) else None
        released = False
        if s1 == s2 == s3 == 200 and mode == "off_air" and not lines:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                info = self._read("preview.json") or {}
                if (str(info.get("job")), info.get("pid")) != (str(owner.get("job")), owner.get("pid")) \
                        and not _port_open(owner.get("url", "")):
                    released = True
                    break
                time.sleep(0.05)
        if not released:
            return {"ok": False, "running": True, "routed_to_owner": True,
                    "error": "The preview's owner did not confirm the stop; it may still be running. Try Stop "
                             "again, or stop it where it was started."}
        with self._file_lock():
            facts = self._read("preview-facts.json") or {}
        if (facts.get("owner_job") == "onair-setup-preview-" + str(owner.get("job")) and facts.get("quiescent") is False) \
                or self._published_drain() is not None:
            return {**_draining_result(), "routed_to_owner": True}
        return {"ok": True, "running": False, "routed_to_owner": True, "pending": 0, "quiescent": True,
                "detail": "Stopped by the process that ran the preview: output off, nothing pending, its private "
                          "ports are closed."}

    def stop_preview(self) -> dict:
        """Stop the private preview, whoever runs it. Never forgets a preview that is still running, and
        reports ok only when the stop is complete: servers closed AND no model request still in flight."""
        show = self.preview
        draining = None
        if show is not None:
            self._release_own(show)
            if show.thread is not None and show.thread.is_alive():
                return {"ok": False, "running": True, "error": "The preview did not shut down in time; it may "
                        "still be running. Try Stop again."}
            if not _quiescent(show):
                draining = show
        owner = self._foreign_owner_alive()
        if owner is not None:  # another copy/process runs the CURRENT preview (it may have resumed meanwhile)
            return self._routed_stop(owner)
        self._clear_stale()
        # a stop is complete only when NO live owner (this copy, another copy or another process) still has a
        # model request in flight: a routed owner's published quiescent:false outlives its discovery files
        if draining is not None or self._still_draining() or self._published_drain() is not None:
            return _draining_result()
        return {"ok": True, "running": False, "pending": 0, "quiescent": True}

    def _stop_own(self) -> None:
        """This engine's own show only (no routing): used while the shared file lock may be held."""
        if self.preview is not None:
            self._release_own(self.preview)
        self._clear_stale()

    def _clear_stale(self) -> None:
        with self._file_lock():
            info = self._read("preview.json")
            if info and self._owner_info() is None:  # discovery left by an owner that is gone: compare-and-delete
                for name in ("preview.json", "preview.token"):
                    with contextlib.suppress(OSError):
                        self._path(name).unlink()
                facts = self._read("preview-facts.json")
                if facts and facts.get("owner_job") == "onair-setup-preview-" + str(info.get("job")):
                    facts.update(running=False)
                    with contextlib.suppress(OSError):
                        self._write("preview-facts.json", facts)

    def preview_facts(self) -> dict:
        show = self.preview
        if show is not None and (not show.alive or _off_air(show)):  # put off air through its control API
            self._release_own(show)
            show = None
        if show is not None and show.alive:  # this engine runs it: read it live, not the last published copy
            with contextlib.suppress(Exception):
                facts = show.facts()
                facts["input_kind"] = "private_test"
                facts["owner"] = "this_entry"
                return facts
        info = self._owner_info()
        facts = self._read("preview-facts.json") or {}
        if info is None:
            facts.update(running=False)
            if self._still_draining() or self._published_drain() is not None:
                facts.update(quiescent=False, draining=True)
            else:
                facts.pop("draining", None)
                if "quiescent" in facts:  # no live owner is drafting (a dead owner's request cannot be)
                    facts["quiescent"] = True
        else:
            # Another copy or process owns it: ask the owner itself, not a published copy that may be stale.
            status, state = preview_call(self.dir, "GET", "/state")
            live = (state.get("state") or state) if isinstance(state, dict) else {}
            ident = "onair-setup-preview-" + str(info.get("job"))
            if facts.get("owner_job") != ident:  # facts of an older preview: keep only the owner's live view
                facts = {"overlay_url": info.get("overlay_url", "")}
            facts.update(running=status == 200 and live.get("mode") not in (None, "off_air"),
                         mode=live.get("mode", "off_air"), owner="other_entry")
            if isinstance(live.get("pending"), (int, list)):
                facts["pending"] = len(live["pending"]) if isinstance(live["pending"], list) else live["pending"]
        facts["input_kind"] = "private_test"
        return facts


class _Cancelled(Exception):
    pass


def _style_or_none(home: Any) -> dict | None:
    from . import style as st
    try:
        return st.read_style(Path(home))
    except Exception:  # noqa: BLE001 - a missing/unreadable stage simply has no saved style
        return None


def _show_summary(eng: Any) -> dict | None:
    """The private agent-hosted show (onair/show_runtime.py): observed state and any pending show plan."""
    try:
        from . import show_runtime
        return show_runtime.status(eng)
    except Exception:  # noqa: BLE001 - status never fails because of the show
        return None


def _real_stage(home: str) -> dict:
    """The REAL show's readiness for the saved stage, re-read from disk: every remaining step itself (title,
    note, exact command or marked template) so an agent can read and relay them without the dashboard.
    The private preview never counts as any of these."""
    from .setup_wizard import readiness
    try:
        ready = readiness(home)
    except Exception:  # noqa: BLE001 - unreadable stage: say so rather than guess
        return {"set_up": False, "error": "The saved stage could not be checked.", "steps": [], "checks": []}
    if not ready.get("ok"):
        return {"set_up": False, "error": ready.get("error") or "The saved stage could not be checked.",
                "steps": [], "checks": []}
    steps = [{**{k: s.get(k) for k in ("key", "title", "note", "command", "template")},
              "agent_can_do": s.get("key") in _AGENT_STAGE_STEPS,
              **({"agent_action": _AGENT_STAGE_STEPS[s.get("key")]} if s.get("key") in _AGENT_STAGE_STEPS else
                 {"why_not_here": _NOT_HERE.get(s.get("key"), "Not a step this setup performs.")})}
             for s in ready.get("steps") or []]
    return {"set_up": not steps,
            "summary": ("The real show is set up." if not steps else f"The real show is not set up yet: {len(steps)} "
                        "step" + ("s" if len(steps) != 1 else "") + " left, listed in order below. The private "
                        "preview is a rehearsal and does not count."),
            "steps": steps,
            "checks": [{k: r.get(k) for k in ("key", "label", "state", "detail")} for r in ready.get("rows") or []]}


# Why each remaining real-show step is NOT done by the approved setup itself. Everything this setup can do in
# the stage home it created (settings, channel, isolation incl. the stage's own nonsecret .env with
# ONAIR_ALLOWED_USERS=onair-stage, public SOUL.md, seed bank, saved style) it already did; a stage started the
# way the gateway starts it (load_hermes_dotenv on that home) arms through the unmodified preflight.
_NOT_HERE = {
    "plugin": "Installing into another Hermes home is the Hermes plugin manager's job (hermes plugins install "
              "--enable, run with that home), and it may download Hermes's own dependencies. This setup does not "
              "run the plugin manager or downloads for you, and a copied folder is not an accepted install.",
    "model": "Needs the user's own provider choice and API key, entered in the stage's own `hermes model`. "
             "This setup never reads or copies keys from your personal agent or any credential store.",
    "voice": "Text-to-speech needs a provider and audio routing that someone has to hear to test.",
    "preflight": "Needs the plugin and model steps first.",
    "gateway": "Needs the plugin and model steps first.",
    "desk": "Opens the operator desk for that running stage (optional; it needs the stage running).",
}


# Steps the owner's agent CAN carry out once the stage is prepared (plugin present+enabled, model configured):
# the approved stage start (stage_runtime.start) runs exactly these public commands in the saved stage home.
_AGENT_STAGE_STEPS = {
    "preflight": "Part of the approved stage start: onair_stage_plan, then the user types /onair-setup stage "
                 "approve <code> (runs hermes onair preflight in the stage home; it must pass).",
    "gateway": "Part of the approved stage start: onair_stage_plan, then the user types /onair-setup stage "
               "approve <code> (runs hermes gateway run in the stage home, OFF AIR; stop with onair_stage_safety).",
}


def schema() -> dict:
    """Discoverable contract for agents: answers, the public style schema, actions and job states."""
    from . import style as st
    return {"ok": True, "schema_version": SCHEMA,
            "answers": {"channel": "Twitch channel name, without # (needed once)",
                        "host_name": "streamer's public name (default Host)",
                        "agent_name": "public co-host name (default Co-host)",
                        "use_model": "the USER's own yes/no: draft the test reply with their configured model "
                                     "connection (a short paid test at planning, then one request if approved)",
                        "style": "a patch of public style fields (see style.schema)"},
            "style": st.describe(),
            "actions": {"status": "read-only", "plan": "returns effects + approval code; may make one paid model test",
                        "apply": "owner approval only (dashboard click or /onair-setup approve <code> + host card)",
                        "progress": "read-only", "cancel": "safety, always allowed",
                        "preview_pause": "safety, always allowed", "preview_stop": "safety, always allowed",
                        "stage_status": "read-only (observed real-stage state, or the exact missing prerequisite)",
                        "stage_plan": "read-only; returns effects + approval code when the stage is prepared",
                        "stage_start": "owner approval only (/onair-setup stage approve <code> + host card)",
                        "stage_off_air": "safety, always allowed", "stage_stop": "safety, always allowed"},
            "job_states": ["running", "cancelling", "done", "failed", "cancelled", "interrupted"],
            "step_states": ["waiting", "running", "done", "skipped", "failed", "cancelled", "interrupted"]}


def _thread_alive(name: str) -> bool:
    return any(t.name == name and t.is_alive() for t in threading.enumerate())


def _quiescent(show: Any) -> bool:
    return not getattr(show, "drafting", 0)


def _draining_result() -> dict:
    return {"ok": False, "running": False, "off_air": True, "pending": 0, "quiescent": False, "draining": True,
            "error": "Output is off and nothing is pending, but a model request is still finishing; it can no "
                     "longer show anything. The stop is not confirmed complete yet: check status again."}


def _identity(show: Any) -> tuple[str, int]:
    """(show name, owning pid): the preview identity also named in preview.json and the published facts."""
    return (str(getattr(show, "name", "")).removeprefix("onair-setup-preview-"), os.getpid())


def _port_open(url: str) -> bool:
    import socket
    match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", url or "")
    if not match:
        return False
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", int(match.group(1)))) == 0


def _off_air(show: Any) -> bool:
    controller = getattr(show, "controller", None)
    return bool(controller is not None and getattr(controller.state, "mode", "") == "off_air")


_QUESTIONS = {
    "channel": {"key": "channel", "question": "Which Twitch channel should the co-host read? (name only, no #)"},
    "use_model": {"key": "use_model", "question": "Use your configured model connection to draft the test "
                  "reply (a short paid connection test during planning, then one request if approved), or a canned stand-in?",
                  "choices": [True, False]},
}


def _public_job(job: dict | None) -> dict | None:
    if not job:
        return None
    return {k: job.get(k) for k in ("id", "digest", "state", "steps", "error", "next_action", "home", "kind",
                                    "approved_by", "started_at", "updated_at")}


# ---- loopback calls to the preview's own desk control API ---------------------------------------
_PREVIEW_ROUTES = {("GET", "/state"), ("GET", "/pending"), ("GET", "/receipts"), ("POST", "/approve"),
                   ("POST", "/reject"), ("POST", "/mode"), ("POST", "/privacy"), ("POST", "/stop"),
                   ("POST", "/mute")}
_URL = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})\Z")


def preview_call(directory: Path, method: str, path: str, body: bytes = b"") -> tuple[int, Any]:
    import http.client
    if (method, path) not in _PREVIEW_ROUTES:
        return 404, {"ok": False, "error": "not a preview route"}
    try:
        info = json.loads((directory / "preview.json").read_text(encoding="utf-8"))
        token = (directory / "preview.token").read_text(encoding="ascii").strip()
        match = _URL.fullmatch(info.get("url", ""))
        if not match:
            raise ValueError("bad url")
        conn = http.client.HTTPConnection("127.0.0.1", int(match.group(1)), timeout=3)
        try:
            headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
            if method == "POST":
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body if method == "POST" else None, headers)
            response = conn.getresponse()
            payload = json.loads(response.read(1024 * 1024 + 1))
            return response.status, _scrub(payload, token)
        finally:
            conn.close()
    except (OSError, ValueError, TypeError, KeyError):
        return 503, {"ok": False, "error": "The private preview is not running. Press Set up & preview "
                                           "(or Resume) to start it."}


def _scrub(value: Any, token: str) -> Any:
    if isinstance(value, str):
        return value.replace(token, "[redacted]")
    if isinstance(value, list):
        return [_scrub(v, token) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v, token) for k, v in value.items()}
    return value


# ---- the private preview show -------------------------------------------------------------------
from .practice import PracticeShow  # noqa: E402


class PreviewShow(PracticeShow):
    """The practice runtime, configured from the saved stage and fed one private test message."""

    def __init__(self, *, name: str, settings: dict, persona: str, model_client: Any, facts_path: Path,
                 style: dict | None = None):
        from . import style as st
        super().__init__(overlay_port=0)  # ephemeral: never the stage's OBS overlay port
        self.name = name
        self.style = st.merged(style, {})
        self.max_tokens = st.LENGTHS[self.style["reply_length"]][1]
        self.select = st.select_config(settings.get("reply_pacing"))
        self.settings, self.persona, self.model_client = settings, persona, model_client
        self.facts_path = facts_path
        self.draft_error = ""
        self.reply_source = "model" if model_client is not None else "canned"
        self.drafting = 0  # model requests in flight (quiescence after a stop)
        self._draft_lock = threading.Lock()
        self.model_attribution = ""

    def start(self) -> None:  # no scripted practice message
        import threading as _t
        context = contextvars.copy_context()
        self.thread = _t.Thread(target=context.run, args=(self._thread_main,), name=self.name, daemon=True)
        self.thread.start()
        if not self._ready.wait(10) or self._error is not None:
            self.close()
            raise SetupError("The private preview could not start.")
        self.started_at = time.time()
        self._publish()

    async def _boot(self) -> None:
        from .control import ControlServer
        from .overlay.server import OverlayServer
        from .rehearsal import _make_bank
        from .stage import StageConfig, StageController, Turn
        from .tts import CaptionsOnly

        names = self.settings.get("agent_names") or ["Co-host"]
        agent = str(self.settings.get("speaker_name") or names[0])
        holder: dict = {}

        async def dispatch(turn: Turn) -> None:
            controller = holder["c"]
            text = turn.chat[0].text if turn.chat else ""
            if self.model_client is None:
                reply = f"Hello from {agent}, and this is a canned setup-test reply rather than the model"
            else:
                context = contextvars.copy_context()
                reply = await asyncio.get_running_loop().run_in_executor(None, context.run, self._draft, text)
            if reply:
                controller.deliver_reply(turn.turn_id, reply)
            controller.turn_done(turn.turn_id, "setup_preview")
            self._publish()

        cfg = StageConfig(host_name=str(self.settings.get("host_name") or "Host"), speaker_name=agent,
                          agent_names=tuple(str(n) for n in names),
                          never_say=tuple(str(t) for t in self.settings.get("never_say") or ()),
                          dead_air_seconds=3600.0, turn_gap_s=0.1, tick_s=0.1, air_pace=0.02, select=self.select)
        controller = StageController(cfg, data_dir=None, guard=None, tts=CaptionsOnly(), dispatch=dispatch,
                                     seed_bank=_make_bank())
        holder["c"] = controller

        def on_event(event: dict) -> None:
            if event.get("type") == "caption" and isinstance(event.get("text"), str):
                self.captions.append(event["text"])
                del self.captions[:-20]
                self._publish()

        controller.hub.add_listener(on_event)
        self.overlay = OverlayServer(controller.hub, controller.clip, port=0)
        await self.overlay.start()
        self.control = ControlServer(controller, None, overlay_url=self.overlay.overlay_url)
        await self.control.start()
        await controller.start()
        controller.set_mode("approval")
        self.controller = controller

    def _draft(self, text: str) -> str:
        """ONE bounded completion through the host-owned connection, public persona only."""
        with self._draft_lock:
            self.drafting += 1
        try:
            return self._draft_once(text)
        finally:
            with self._draft_lock:
                self.drafting -= 1

    def _draft_once(self, text: str) -> str:
        started = time.monotonic()
        try:
            result = self.model_client.complete(
                messages=[{"role": "system", "content": self.persona},
                          {"role": "user", "content": "<live_chat>\n" + TEST_USER + ": " + text + "\n</live_chat>"}],
                max_tokens=self.max_tokens, timeout=MODEL_TIMEOUT_S, purpose="onair.setup-preview")
            reply = (getattr(result, "text", "") or "").strip()
            self.model_attribution = " · ".join(str(x) for x in (getattr(result, "provider", ""),
                                                                 getattr(result, "model", "")) if x)
        except Exception as exc:  # noqa: BLE001 - SDK failure does not establish provider outage
            # Never the exception text (keys, URLs, response bodies); never a fallback reply or a retry.
            _log_model_failure("preview", exc, started)
            _invalidate_client(self.model_client)
            self.draft_error = MODEL_REJECTED_429 if _status_code(exc) == 429 else MODEL_FAILED
            return ""
        if not reply:
            _log_model_failure("preview", _EmptyModelResponse(), started)
            _invalidate_client(self.model_client)
        self.draft_error = "" if reply else "The model call returned no preview reply."
        return reply

    def send_test_message(self) -> dict:
        def ingest():
            from .types import ChatMsg
            msg = ChatMsg("private-test", "setup", "setup-test-1", "setup-u1", TEST_USER, TEST_MESSAGE,
                          time.time(), first_msg=True)
            return self.controller.ingest(msg).kind
        verdict = self._run(ingest)
        self.next_index = 1
        self._publish()
        return {"user": TEST_USER, "text": TEST_MESSAGE, "verdict": verdict}

    def _run(self, fn, timeout: float = 5.0):
        """On the show's own loop thread (listeners, dispatch) call directly: waiting would deadlock."""
        try:
            on_loop = asyncio.get_running_loop() is self.loop
        except RuntimeError:
            on_loop = False
        return fn() if on_loop and self.controller is not None else super()._run(fn, timeout)

    def facts(self) -> dict:
        def read():
            snap = self.controller.snapshot()
            return {"mode": snap["mode"], "privacy": snap["privacy"], "muted": snap["muted"],
                    "pending": snap["pending"]}
        out = self._run(read) if self.alive else {"mode": "off_air", "privacy": False, "muted": False, "pending": 0}
        out.update(running=self.alive and not _off_air(self), message={"user": TEST_USER, "text": TEST_MESSAGE} if self.next_index else None,
                   reply_source=self.reply_source, model_attribution=self.model_attribution,
                   draft_error=self.draft_error, captions=len(self.captions),
                   last_caption=self.captions[-1] if self.captions else "",
                   overlay_url=self.overlay.overlay_url if self.overlay else "",
                   style_in_effect=self.style_in_effect(),
                   owner_job=self.name, owner_pid=os.getpid(), quiescent=not self.drafting)
        return out

    def style_in_effect(self) -> dict:
        """What the running preview actually uses, read from its own controller and request settings."""
        controller = getattr(self, "controller", None)
        cfg = getattr(controller, "cfg", None)
        select = getattr(cfg, "select", self.select)
        from .style import PACING
        # reply pacing is read back from the controller's actual selection limits, not from the request
        pacing = next((k for k, (w, g) in PACING.items()
                       if (w, g) == (select.max_worthy_per_turn, select.routine_gap_s)), "custom")
        return {"tone": self.style["tone"], "reply_length": self.style["reply_length"], "max_tokens": self.max_tokens,
                "reply_pacing": pacing,
                "max_worthy_per_turn": select.max_worthy_per_turn, "routine_gap_s": select.routine_gap_s,
                "never_say": list(getattr(cfg, "never_say", ()) or ()),
                "persona_sha256": hashlib.sha256(self.persona.encode()).hexdigest()[:16]}

    def _publish(self) -> None:
        if getattr(self, "_released", False):  # a stopped show never overwrites a newer preview's facts
            return
        try:
            facts = self.facts()
        except Exception:  # noqa: BLE001
            return
        tmp = self.facts_path.with_name(self.facts_path.name + f".{threading.get_ident()}.tmp")
        with contextlib.suppress(OSError):
            self.facts_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(facts, fh)
            os.replace(tmp, self.facts_path)


# ---- one engine per Hermes home per process -----------------------------------------------------
_ENGINES: dict[str, SetupEngine] = {}
_ENGINES_LOCK = threading.Lock()
_MODEL_CLIENT_FACTORIES: dict[str, Callable[[], Any]] = {}
_TTS_DISPATCH: Any = None


def set_tts_dispatch(dispatch: Any) -> None:
    """The plugin's ``ctx.dispatch_tool`` (Hermes's own configured TTS); None where no plugin context exists."""
    global _TTS_DISPATCH
    _TTS_DISPATCH = dispatch
    for engine in _ENGINES.values():
        engine.tts_dispatch = dispatch


def set_model_client_factory(factory: Callable[[], Any]) -> None:
    """Resolve this profile's host facade when a job or show needs it."""
    key = os.fspath(owner_home().resolve())
    with _ENGINES_LOCK:
        _MODEL_CLIENT_FACTORIES[key] = factory
        if key in _ENGINES:
            _ENGINES[key]._model_client_factory = factory


def set_model_client(client: Any) -> None:
    """Install a fixed client for contexts without a live plugin facade (including tests)."""
    set_model_client_factory(lambda: client)


def engine(home: Path | None = None) -> SetupEngine:
    key = os.fspath(Path(home or owner_home()).resolve())
    with _ENGINES_LOCK:
        if key not in _ENGINES:
            item = SetupEngine(Path(key))
            item._model_client_factory = _MODEL_CLIENT_FACTORIES.get(key, lambda: None)
            _ENGINES[key] = item
        return _ENGINES[key]
