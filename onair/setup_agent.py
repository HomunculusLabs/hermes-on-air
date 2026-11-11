"""The private-agent entry to quick setup: bounded tools, one slash command and a plugin skill.

Registered by the plugin in the user's OWN Hermes home only. In a stage home (the public co-host)
nothing here is registered, so public Twitch turns never see these schemas.

Authority is read from the host, not from the model:

- every handler asks the plugin context for the calling session's surface (``ctx.session_surface``)
  and refuses when it is the On Air platform, when no session can be established, or when this
  Hermes home is a stage home;
- the tools report status, plan and progress and offer two safety actions (cancel, stop the
  preview). Planning an explicitly requested model preview may make one short paid liveness request.
  No tool can apply a plan or start anything, and none reads an
  ``approved``/``caller`` argument (their schemas allow no extra properties);
- applying needs the human to type ``/onair-setup approve <code>`` in their own chat (a model cannot
  issue a user slash command) AND pick "Set up & preview" on the host's own choice card
  (``ctx.ask_choice``). If the surface cannot show that card, approval is refused and the dashboard
  is offered instead. There is no shell or file tool here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import setup_engine as se
from . import stage_runtime as sr

TOOLSET = "onair_setup"
_PUBLIC_SURFACES = {"on_air", "onair", "on-air"}
# Local clients the machine's own user drives (SDK session_surface: cli/tui/desktop). Messaging
# platforms and ACP editors are refused: the host exposes no sender-ownership signal to plugins.
# "local": the Hermes gateway's Local platform, i.e. this machine's own CLI/TUI/desktop clients attached over
# the owner-only local socket (core lists it in NON_MESSAGING_SESSION_SURFACES). On the supported SDK the
# classic CLI runs plugin slash commands there, so /onair-setup and /onair-broadcast arrive as "local".
_OWNER_SURFACES = {"cli", "tui", "desktop", "local"}

_OBJ_NONE = {"type": "object", "properties": {}, "additionalProperties": False}
from . import style as _style

SCHEMAS = {
    "onair_setup_schema": {
        "name": "onair_setup_schema",
        "description": ("Read-only. The On Air setup contract: which answers exist, the PUBLIC co-host style "
                        "fields you may set (persona line, tone, topics, avoid_topics, reply_length, "
                        "reply_pacing, output) with presets and natural-language examples, the actions and "
                        "the job/step states. Use it to turn what the user says into a style patch."),
        "parameters": _OBJ_NONE,
    },
    "onair_setup_status": {
        "name": "onair_setup_status",
        "description": ("Read-only. On Air quick-setup status for THIS user: saved stage, model connection "
                        "label (never keys), running setup job and private preview, and which answers are "
                        "still missing. Call this first when the user asks to set up On Air."),
        "parameters": _OBJ_NONE,
    },
    "onair_setup_plan": {
        "name": "onair_setup_plan",
        "description": ("Build the On Air setup plan and approval code. If the user chose the model, planning "
                        "may make one short paid connection test before approval; it creates no stage folder. "
                        "Ask only for answers listed in 'missing'. To run it the USER must type the returned "
                        "approve command themselves; you cannot approve it."),
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "channel": {"type": "string", "description": "Twitch channel name, without #"},
                "host_name": {"type": "string", "description": "The streamer's public name (default Host)"},
                "agent_name": {"type": "string", "description": "The public co-host's name (default Co-host)"},
                "use_model": {"type": "boolean",
                              "description": "Only the user's own answer: draft the test reply with their configured model connection"},
                "style": {**_style.SCHEMA, "properties": {**_style.SCHEMA["properties"],
                                                          "preset": {"type": "string", "enum": sorted(_style.PRESETS)}}},
            },
        },
    },
    "onair_setup_progress": {
        "name": "onair_setup_progress",
        "description": "Read-only. The current setup job's steps, actual results and any error with its next action.",
        "parameters": _OBJ_NONE,
    },
    "onair_setup_cancel": {
        "name": "onair_setup_cancel",
        "description": "Safety action: cancel a running setup (what it made is removed) and stop the private preview.",
        "parameters": _OBJ_NONE,
    },
    "onair_preview_safety": {
        "name": "onair_preview_safety",
        "description": ("Safety action on the PRIVATE preview only: 'pause' (privacy cut: nothing is shown) or "
                        "'stop'. Cannot approve, show a reply, unpause or start anything; the user does that."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["action"],
                       "properties": {"action": {"type": "string", "enum": ["pause", "stop"]}}},
    },
    "onair_stage_status": {
        "name": "onair_stage_status",
        "description": ("Read-only. The REAL stage (the public co-host this setup saved): its observed state "
                        "(stopped / starting / connected / exited, mode, pending count, desk and overlay URLs) and, "
                        "when it is not startable yet, exactly which prerequisite is missing and who does it."),
        "parameters": _OBJ_NONE,
    },
    "onair_stage_plan": {
        "name": "onair_stage_plan",
        "description": ("Read-only. Plan starting (or restarting) the REAL stage: checks its prerequisites and returns "
                        "the effects plus an approval code, or the exact missing prerequisites. Changes nothing. "
                        "The USER must type the returned approve command; you cannot approve it."),
        "parameters": _OBJ_NONE,
    },
    "onair_stage_control": {
        "name": "onair_stage_control",
        "description": "Read the real stage or make it safer. Cannot approve, arm, unmute or remove privacy.",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["action"],
                       "properties": {"action": {"type": "string", "enum": ["state", "pending", "privacy_on", "mute_on", "stop", "off_air", "cut_in"]}}},
    },
    "onair_stage_host_turn": {
        "name": "onair_stage_host_turn",
        "description": ("Hand the owner's own words to the running stage as the show host's turn. "
                        "Relays require approval mode by default and are subject to a settling window. "
                        "If the owner enables host_relay_autonomous, replies in autonomous mode air without owner approval. "
                        "Use only when the owner asks you to say or ask something to the co-host; "
                        "pass the owner's words faithfully, add nothing."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["text"],
                       "properties": {"text": {"type": "string"}}},
    },
    "onair_stage_safety": {
        "name": "onair_stage_safety",
        "description": ("Safety action on the REAL stage this setup started: 'off_air' (nothing more is shown) or "
                        "'stop' (off air, then end its process; ok only when it is observed gone). Cannot start, "
                        "arm, approve or show anything."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["action"],
                       "properties": {"action": {"type": "string", "enum": ["off_air", "stop"]}}},
    },
    "onair_show_plan": {
        "name": "onair_show_plan",
        "description": ("Plan a PRIVATE show (127.0.0.1 only) hosted by the saved co-host. Planning may make "
                        "one short paid model connection test before approval. It opens the "
                        "show and fills quiet time in its own words from a public show brief and topics, answers "
                        "selected private chat, and speaks/captions through Hermes's own model and voice. Returns "
                        "the exact effects and an approval code, or the missing prerequisite (no canned "
                        "stand-in). The USER approves the show once; you cannot approve it."),
        "parameters": {"type": "object", "additionalProperties": False, "properties": {
            "brief": {"type": "string", "maxLength": 240,
                      "description": "PUBLIC one-line show brief: what today's show is about (said on air)"},
            "policy": {"type": "string", "enum": ["auto", "review"],
                       "description": "auto: guarded lines air by themselves (default); review: each line waits for the user"},
            "quiet_seconds": {"type": "integer", "minimum": 10, "maximum": 600,
                              "description": "Quiet seconds before the co-host brings up the next talking point (default 30)"},
            "voice": {"type": "boolean",
                      "description": "false: captions only for this show. Default true: speak with the user's Hermes "
                                     "text-to-speech when it is available here (the plan's effects say which)"}}},
    },
    "onair_show_status": {
        "name": "onair_show_status",
        "description": ("Read-only. The private show's observed state: activity (thinking, speaking, listening, "
                        "waiting_for_you, paused, stopped), recent captions (with whether each was spoken), recent "
                        "private chat and whether it was answered, model/voice usage and any pending show plan."),
        "parameters": _OBJ_NONE,
    },
    "onair_show_chat": {
        "name": "onair_show_chat",
        "description": ("Pass ONE private chat message from the user to the running private show, as viewer chat. "
                        "The show's own chat gate and the co-host decide whether to answer it."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["text"],
                       "properties": {"text": {"type": "string", "maxLength": 300}}},
    },
    "onair_show_safety": {
        "name": "onair_show_safety",
        "description": ("Safety action on the private show: 'stop' (everything pauses at once; the USER resumes) or "
                        "'end' (close the show). Cannot start, resume or approve anything."),
        "parameters": {"type": "object", "additionalProperties": False, "required": ["action"],
                       "properties": {"action": {"type": "string", "enum": ["stop", "end"]}}},
    },
}


def owner_session(ctx: Any) -> tuple[bool, str]:
    """(allowed, reason) from the host's own session context. Fails closed."""
    if se.is_stage_home():
        return False, "This Hermes home is an On Air stage (the public co-host); setup is not available here."
    try:
        surface = str(ctx.session_surface() or "").strip().lower()
    except Exception:  # noqa: BLE001
        surface = ""
    if not surface:
        return False, "No owner session could be established for this call; nothing was read or changed."
    if surface in _PUBLIC_SURFACES:
        return False, "On Air setup is not available to the public stage."
    if surface not in _OWNER_SURFACES:
        # A messaging platform names the chat app, not who is typing: the host gives plugins no
        # owner signal there, so setup fails closed instead of trusting everyone admitted to the chat.
        return False, ("On Air setup runs only in your own local Hermes (CLI, TUI or desktop app) or in the "
                       "On Air dashboard tab; a messaging chat cannot prove you are the owner. Nothing was "
                       "read or changed.")
    return True, surface


def _refuse(reason: str) -> str:
    return json.dumps({"ok": False, "refused": True, "error": reason})


def make_handlers(ctx: Any, get_engine=se.engine) -> dict:
    def guarded(fn):
        def handler(args: Any = None, **_kw: Any) -> str:
            allowed, reason = owner_session(ctx)
            if not allowed:
                return _refuse(reason)
            return json.dumps(fn(args if isinstance(args, dict) else {}), default=str)
        return handler

    def describe(_args):
        return se.schema()

    def status(_args):
        return get_engine().status()

    def plan(args):
        extra = sorted(set(args) - {"channel", "host_name", "agent_name", "use_model", "style"})
        if extra:  # e.g. approved/caller/target: refused explicitly, never read
            return {"ok": False, "errors": {k: f"{k!r} is not accepted here; only the user approves, in their own "
                                               "chat or the dashboard." for k in extra}}
        answers = {k: args[k] for k in ("channel", "host_name", "agent_name", "use_model", "style") if k in args}
        result = get_engine().plan(answers)
        if result.get("ok"):
            result["tell_user"] = ("Show the user these effects. If they agree, THEY type: "
                                   + result["approve_with"]["chat"] + " (or press Set up & preview in the "
                                   "On Air dashboard tab). You cannot approve it for them.")
        return result

    def progress(_args):
        eng = get_engine()
        return {"ok": True, "job": se._public_job(eng.job()), "preview": eng.preview_facts()}

    def cancel(_args):
        return get_engine().cancel_all()  # ok only when the job is cancelled AND the preview stop is complete

    def safety(args):
        action = args.get("action")
        eng = get_engine()
        if action == "stop":
            stop = eng.stop_preview()  # ok only when the stop is confirmed complete (nothing still running)
            return {"ok": bool(stop.get("ok")), "preview": stop, **({"error": stop["error"]} if stop.get("error") else {})}
        if action == "pause":
            code, payload = se.preview_call(eng.dir, "POST", "/privacy", b'{"on": true}')
            return {"ok": code == 200, "status": code, "result": payload, "preview": eng.preview_facts()}
        return {"ok": False, "error": "action must be pause or stop"}

    def stage_status(_args):
        eng = get_engine()
        out = sr.status(eng)
        if out.get("state") in ("stop_unconfirmed", "identity_unknown"):
            out["startable"] = False
            out["tell_user"] = "The previous stage is NOT confirmed stopped; use onair_stage_safety stop again first."
        elif not out.get("running"):
            ready = sr.readiness(eng)  # read-only: status never issues or replaces an approval code
            out["startable"] = None if ready.get("unknown") else bool(ready.get("ok"))
            if ready.get("unknown"):
                out["readiness"] = "unknown"
                out["tell_user"] = ready["error"]
            for key in ("missing", "next_action", "prerequisites"):
                if ready.get(key):
                    out[key] = ready[key]
            if ready.get("ok"):
                out["tell_user"] = "Ready to start. Use onair_stage_plan; the user approves the start themselves."
        return out

    def stage_plan(_args):
        result = sr.plan(get_engine())
        if result.get("ok"):
            result["tell_user"] = ("Show the user these effects. If they agree, THEY type: "
                                   + result["approve_with"]["chat"] + ". You cannot approve it for them.")
        return result

    def stage_control(args):
        if set(args) != {"action"} or args["action"] not in ("state", "pending", "privacy_on", "mute_on", "stop", "off_air"):
            return {"ok": False, "error": "That action is not available to the agent."}
        return sr.safe_control(get_engine(), args["action"])

    def stage_host_turn(args):
        if set(args) != {"text"} or not isinstance(args["text"], str):
            return {"ok": False, "reason": "invalid", "message": "Provide only the owner words as text."}
        return sr.relay_host_turn(get_engine(), args["text"])

    def stage_safety(args):
        action = args.get("action")
        if action == "stop":
            return sr.stop(get_engine())
        if action == "off_air":
            return sr.off_air(get_engine())
        return {"ok": False, "error": "action must be off_air or stop"}

    def show_plan(args):
        from . import show_runtime
        result = show_runtime.plan(get_engine(), args)
        if result.get("ok"):
            result["tell_user"] = ("Show the user these effects. If they agree, THEY type: "
                                   + result["approve_with"]["chat"] + ". You cannot approve it for them.")
        return result

    def show_status(_args):
        from . import show_runtime
        return show_runtime.status(get_engine())

    def show_chat(args):
        from . import show_runtime
        return show_runtime.control(get_engine(), "say", {"text": args.get("text")})

    def show_safety(args):
        from . import show_runtime
        action = args.get("action")
        if action not in ("stop", "end"):
            return {"ok": False, "error": "action must be stop or end"}
        return show_runtime.control(get_engine(), action)

    return {"onair_show_plan": guarded(show_plan), "onair_show_status": guarded(show_status),
            "onair_show_chat": guarded(show_chat), "onair_show_safety": guarded(show_safety),
            "onair_setup_schema": guarded(describe), "onair_setup_status": guarded(status),
            "onair_setup_plan": guarded(plan), "onair_setup_progress": guarded(progress),
            "onair_setup_cancel": guarded(cancel), "onair_preview_safety": guarded(safety),
            "onair_stage_status": guarded(stage_status), "onair_stage_plan": guarded(stage_plan),
            "onair_stage_control": guarded(stage_control), "onair_stage_host_turn": guarded(stage_host_turn),
            "onair_stage_safety": guarded(stage_safety)}


def approve_text(plan: dict) -> str:
    return "Set up On Air like this?\n" + "\n".join("- " + e for e in plan["effects"])


def make_command(ctx: Any, get_engine=se.engine):
    """``/onair-setup [status|approve <code>|cancel|stop]`` — typed by the human in their own chat."""
    def command(raw_args: str = "", **_kw: Any) -> str:
        allowed, reason = owner_session(ctx)
        if not allowed:
            return reason
        words = (raw_args or "").split()
        verb = words[0].lower() if words else "status"
        eng = get_engine()
        if verb == "stage":
            return _stage_command(ctx, eng, words[1:])
        if verb == "show":
            return _show_command(ctx, eng, words[1:])
        if verb == "approve":
            stored = eng._read("plan.json")
            code = words[1] if len(words) > 1 else ""
            if not stored or not code or stored.get("code") != code:
                return ("No current setup plan has that code. Ask your agent to plan the setup again, or "
                        "use the On Air dashboard tab.")
            fresh = eng.plan(stored.get("answers") or {}, remember=False)
            if not fresh.get("ok"):
                if fresh.get("next_action"):
                    return " ".join(dict.fromkeys(str(s) for s in (fresh.get("error"), fresh["next_action"]) if s))
                return "Something changed since that plan was made; ask your agent to plan it again. Nothing was changed."
            if fresh.get("digest") != stored.get("digest"):
                return "Something changed since that plan was made; ask your agent to plan it again. Nothing was changed."
            choice = ctx.ask_choice(approve_text(fresh), [
                {"value": "approve", "label": "Set up & preview"}, {"value": "cancel", "label": "Cancel"}])
            if choice is None:
                return ("This chat cannot show an approval card, so nothing was changed. Approve the same plan "
                        "in the On Air dashboard tab instead.")
            if choice != "approve":
                return "Cancelled. Nothing was changed."
            result = eng.apply(fresh["digest"], se.owner_consent(fresh["digest"], "slash-command"))
            return _job_text(result.get("job"), result.get("error"))
        if verb == "cancel" or verb == "stop":
            out = eng.cancel_all()  # same job-terminal AND preview-quiescent result as the tool and the dashboard
            if not out.get("ok"):
                return str(out.get("error") or "The stop could not be confirmed. Check status again.") \
                    + " Run /onair-setup stop again."
            return (out.get("note") or "Stopped.") + " The private preview is stopped."
        return _job_text(se._public_job(eng.job()), None)
    return command


def make_stage_command(ctx: Any, get_engine=se.engine):
    """Only the local owner can type this command; model tools cannot call it."""
    def command(raw_args: str = "", **_kw: Any) -> str:
        allowed, reason = owner_session(ctx)
        if not allowed:
            return reason
        verb, _, remainder = (raw_args or "").strip().partition(" ")
        verb, remainder = verb.lower(), remainder.strip()
        eng = get_engine()
        if verb == "cut" and not remainder:
            return sr.owner_command(eng, "cut").get("message", "The stage did not answer.")
        if verb == "say" and remainder:
            return sr.owner_command(eng, "say", remainder).get("message", "The stage did not answer.")
        if verb == "mode" and remainder in ("approval", "autonomous"):
            return sr.owner_command(eng, "mode", remainder).get("message", "The stage did not answer.")
        if verb in ("privacy", "mute") and remainder == "off":
            try:
                choice = ctx.ask_choice("Turn " + verb + " off? This loosens stage safety.",
                                        [{"value": "approve", "label": "Turn off"}, {"value": "cancel", "label": "Cancel"}])
            except Exception:
                choice = None
            if choice != "approve":
                return "Nothing was changed."
            return sr.owner_command(eng, verb + "_off").get("message", "The stage did not answer.")
        if verb == "approve":
            result = sr.owner_command(eng, "pending")
            pending = (result.get("result") or {}).get("pending", []) if result.get("ok") else []
            rows = [row for row in pending if isinstance(row, dict) and (not remainder or row.get("line_id") == remainder)]
            if not rows:
                return "No matching waiting line was found."
            line = rows[0]
            if not isinstance(line.get("text"), str) or not isinstance(line.get("line_id"), str) or type(line.get("epoch")) is not int:
                return "The waiting line could not be verified."
            source = line.get('source')
            source = source if source in ('host', 'chat', 'beat', 'support') else 'unknown'
            try:
                choice = ctx.ask_choice("Approve this line for the stage?\nSource: " + source + "\n" + line["text"],
                                        [{"value": "approve", "label": "Approve line"}, {"value": "cancel", "label": "Cancel"}])
            except Exception:
                choice = None
            if choice != "approve":
                return "Nothing was approved."
            out = sr.owner_command(eng, "approve", {"line_id": line["line_id"], "epoch": line["epoch"]})
            return out.get("message", "The stage did not answer.")
        return "Use /onair-stage cut, /onair-stage say TEXT, /onair-stage approve [id], /onair-stage mode approval|autonomous, /onair-stage privacy off, or /onair-stage mute off."
    return command


def _stage_command(ctx: Any, eng: Any, words: list) -> str:
    """``/onair-setup stage [status|approve <code>|off|stop]`` — the REAL stage, typed by the human."""
    verb = words[0].lower() if words else "status"
    if verb == "approve":
        stored = eng._read(sr.PLAN_FILE) or {}
        code = words[1] if len(words) > 1 else ""
        if not stored.get("code") or not code or stored.get("code") != code:
            reason = "No current stage plan has that code. Ask your agent to plan the stage start again."
            sr.record_refusal(eng, {"error": reason, "next_action": "Ask the agent for a new stage plan."})
            return reason
        fresh = sr.readiness(eng)  # read-only re-check: the shown code stays the one to approve
        if not fresh.get("ok"):
            if not fresh.get("unknown"):
                sr.record_refusal(eng, fresh)
            reasons = "; ".join(str(m.get("detail")) for m in fresh.get("missing", []) if m.get("detail"))
            return " ".join(part for part in (str(fresh.get("error") or ""), reasons, fresh.get("next_action") or "") if part)
        if fresh.get("digest") != stored.get("digest"):
            reason = "The stage changed since that plan was made; ask your agent to plan it again. Nothing was started."
            sr.record_refusal(eng, {"error": reason, "next_action": "Review the changes and plan the stage start again."})
            return reason
        choice = ctx.ask_choice("Start the real On Air stage?\n" + "\n".join("- " + e for e in fresh["effects"]), [
            {"value": "start", "label": "Start the stage (off air)"}, {"value": "cancel", "label": "Cancel"}])
        if choice is None:
            reason = "This chat cannot show an approval card, so nothing was started."
            sr.record_refusal(eng, {"error": reason, "next_action": "Use an owner surface that can show the approval card."})
            return reason
        if choice != "start":
            return "Cancelled. Nothing was started."
        # start() compares and consumes exactly this shown code under the setup lock (after the card returned)
        result = sr.start(eng, fresh["digest"], se.owner_consent(fresh["digest"], "slash-command"), code)
        stage = result.get("stage") or {}
        if not result.get("ok"):
            return "The stage did NOT start correctly: " + str(result.get("error")) + (
                " " + result["next_action"] if result.get("next_action") else "") + (
                " Preflight said: " + " | ".join(result["preflight"]) if isinstance(result.get("preflight"), list) else "")
        if result.get("already_running"):
            return "The stage was already running, so nothing new was started. " + sr.describe(stage)
        return sr.describe(stage) + (" " + str(stage.get("next_action")) if stage.get("next_action") else "")
    if verb in ("off", "off_air"):
        out = sr.off_air(eng)
        if out.get("ok") and out.get("mode") == "off_air":
            return "The stage is off air (its desk confirmed it)."
        if out.get("ok") and out.get("stopped"):
            return str(out.get("note"))  # positively nothing running: a no-op, not a mode acknowledgement
        return "NOT confirmed off air: " + str(out.get("error") or out.get("note"))
    if verb == "stop":
        out = sr.stop(eng)
        return str(out.get("note")) if out.get("ok") else "The stage is NOT confirmed stopped: " + str(out.get("error"))
    stage = sr.status(eng)
    text = sr.describe(stage)
    if not stage.get("running") and stage.get("state") not in ("stop_unconfirmed", "identity_unknown"):
        ready = sr.readiness(eng)
        if not ready.get("ok"):
            reasons = "; ".join(str(m.get("detail")) for m in ready.get("missing", []) if m.get("detail"))
            text += (" Readiness unknown. " if ready.get("unknown") else " Not startable. ") + reasons + " " + str(ready.get("next_action") or ready.get("error") or "")
    refusal = stage.get("last_refusal")
    if refusal:
        from datetime import datetime, timezone
        when = datetime.fromtimestamp(refusal["time"], timezone.utc).isoformat(timespec="seconds")
        text += f" Last refused start ({when}): {refusal['reason']} Remedy: {refusal['remedy']}"
    return text


def _show_command(ctx: Any, eng: Any, words: list) -> str:
    """``/onair-setup show [status|approve <code>|stop|resume|end]`` — the private show, typed by the human."""
    from . import show_runtime
    verb = words[0].lower() if words else "status"
    if verb == "approve":
        stored = eng._read(show_runtime.PLAN_FILE) or {}
        code = words[1] if len(words) > 1 else ""
        if not stored.get("code") or stored.get("code") != code:
            return "No current show plan has that code. Ask your agent to plan the show again."
        fresh = show_runtime.plan(eng, stored.get("answers") or {}, remember=False)
        if not fresh.get("ok"):
            return str(fresh.get("error")) + (" " + str(fresh["next_action"]) if fresh.get("next_action") else "")
        if fresh.get("digest") != stored.get("digest"):
            return "Something changed since that show plan was made; ask your agent to plan it again. Nothing was started."
        choice = ctx.ask_choice("Start this private show?\n" + "\n".join("- " + e for e in fresh["effects"]), [
            {"value": "start", "label": "Start the show"}, {"value": "cancel", "label": "Cancel"}])
        if choice is None:
            return "This chat cannot show an approval card, so nothing was started."
        if choice != "start":
            return "Cancelled. Nothing was started."
        result = show_runtime.start(eng, fresh["digest"], se.owner_consent(fresh["digest"], "slash-command"), code)
        if not result.get("ok"):
            return "The show did NOT start: " + str(result.get("error"))
        show = result.get("show") or {}
        return ("The private show is running: " + str(show.get("activity_text")) + " Watch and chat on the On Air "
                "tab; captions: " + str(show.get("overlay_url")) + ". Stop any time: /onair-setup show stop.")
    if verb in ("stop", "resume", "end"):
        out = show_runtime.control(eng, verb)
        if not out.get("ok"):
            return "NOT done: " + str(out.get("error") or "the show did not answer.")
        return {"stop": "Stopped: the show is paused and nothing is shown or said. /onair-setup show resume continues.",
                "resume": "Resumed: the show is live again (privately).",
                "end": str(out.get("note") or "The show ended.")}[verb]
    state = show_runtime.status(eng)
    if state.get("state") == "unknown":
        return str(state.get("start_next_action") or "Show state cannot be verified.")
    show = state.get("show") or {}
    return ("Private show: " + str(show.get("activity_text"))
            + (" Pending show plan: /onair-setup show approve " + state["pending_show_plan"]["code"]
               if state.get("pending_show_plan") and not state.get("running") else ""))


def _job_text(job: dict | None, error: str | None) -> str:
    if error:
        return error
    if not job:
        return "No setup has run yet. Ask your agent to set up On Air, or open the On Air dashboard tab."
    lines = [f"Setup {job['state']}."] + [f"- {s['label']}: {s['state']}" + (f" ({s['detail']})" if s["detail"] else "")
                                          for s in job.get("steps") or []]
    if job.get("next_action"):
        lines.append("Next: " + job["next_action"])
    return "\n".join(lines)


SKILL = Path(__file__).resolve().parent / "skills" / "setup" / "SKILL.md"
# The producer skill (on-air:produce): brief -> missing questions -> supported layout -> real tools -> private
# preview/readback -> controls. Owner homes only, like every entry registered here.
PRODUCE_SKILL = Path(__file__).resolve().parent / "skills" / "produce" / "SKILL.md"
SCENE_SKILL = Path(__file__).resolve().parent / "skills" / "scene" / "SKILL.md"
SCENE_BUILD_SKILL = Path(__file__).resolve().parent / "skills" / "scene-build" / "SKILL.md"
AVATAR_SKILL = Path(__file__).resolve().parent / "skills" / "avatar" / "SKILL.md"
SKILLS = (("setup", SKILL, "Use when setting up On Air. Prepare a safe preview."),
          ("produce", PRODUCE_SKILL, "Use when producing a show. Prepare and rehearse it."),
          ("scene", SCENE_SKILL, "Use when setting up a 3D scene. Preview it safely."),
          ("scene-build", SCENE_BUILD_SKILL, "Use when building a 3D room. Design and verify it."),
          ("avatar", AVATAR_SKILL, "Use when adding a VRM avatar. Verify it locally."))
SKILLS += tuple((name, Path(__file__).resolve().parent / "skills" / name / "SKILL.md", description)
                for name, description in (
                    ("first-stream", "Use when starting a first stream. Rehearse off air."),
                    ("views", "Use when arranging stream views. Check OBS readback."),
                    ("host", "Use when talking to the co-host. Send a host line."),
                    ("show-day", "Use when running a show day. Check each handoff."),
                    ("troubleshoot", "Use when On Air fails. Find the next safe check."),
                    ("seed-bank", "Use when planning a rundown. Validate its beats.")))


def register(ctx: Any) -> list:
    """Register the private-agent setup entry. Returns what was registered (empty in a stage home)."""
    if se.is_stage_home():
        return []
    se.set_model_client_factory(lambda: getattr(ctx, "llm", None))
    dispatch = getattr(ctx, "dispatch_tool", None)
    se.set_tts_dispatch(dispatch if callable(dispatch) else None)
    done = []
    handlers = make_handlers(ctx)
    if callable(getattr(ctx, "register_tool", None)):
        for name, schema in SCHEMAS.items():
            ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handlers[name],
                              description=schema["description"], emoji="🔴")
            done.append("tool:" + name)
        from .preflight import note_owner_toolset
        note_owner_toolset(TOOLSET)  # preflight refuses to run the stage from a home that has these
    if callable(getattr(ctx, "register_command", None)):
        ctx.register_command("onair-setup", make_command(ctx), description="On Air quick setup, the private show and the real stage: status, approve, cancel, show, stage",
                             args_hint="[status|approve <code>|cancel|stage [status|approve <code>|off|stop]|show [status|approve <code>|stop|resume|end]]")
        done.append("command:onair-setup")
        ctx.register_command("onair-stage", make_stage_command(ctx), description="Owner stage controls and host lines",
                             args_hint="say TEXT | cut | approve [id] | mode approval|autonomous | privacy off | mute off")
        done.append("command:onair-stage")
    if callable(getattr(ctx, "register_skill", None)):
        for name, path, description in SKILLS:
            if path.is_file():
                ctx.register_skill(name, path, description=description)
                done.append("skill:" + name)
    return done
