/* On Air operator desk — plain readable IIFE, no build step.
 *
 * All state comes from the dashboard-authenticated proxy at /api/plugins/on-air/
 * (see dashboard/plugin_api.py). Every string renders through React text nodes; no raw
 * HTML is ever assigned from data.
 *
 * Keyboard: A approve · R reject · ArrowUp/ArrowDown (or J/K) select ·
 * P privacy cut · M mute · Esc stop all. Safety keys always fire: they never
 * wait on the model, the queue or a refresh.
 *
 * A failed preflight (from /state) shows under the banner with its failing checks, a fix per known
 * check and the recovery path. When the runtime gate has latched (reason runtime_refused:<rule>)
 * the rule is named and explained; off air, a "Clear runtime latch" button posts runtime/clear.
 * Arming afterwards still runs preflight; nothing here arms or goes autonomous by itself.
 *
 * Until a show answers, the desk shows how to reach one with existing commands. When a connected
 * show stops answering, the last confirmed state is shown as stale and every show control is
 * disabled (local Copy buttons and the setup disclosure keep working; they send nothing).
 * An action counts as confirmed only on an explicit {ok: true} reply from the show.
 */
(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  const React = SDK.React;
  const h = React.createElement;
  const { useState, useEffect, useLayoutEffect, useRef } = React;

  const BASE = "/api/plugins/on-air/";
  // Resolve the companion asset relative to this profile's loaded plugin entry.
  const MIC_ASSET = document.currentScript && document.currentScript.src
    ? new URL("host-mic.js", document.currentScript.src).href : "/dashboard-plugins/on-air/dist/host-mic.js";
  const POLL_MS = 1000; // live updates by fast polling
  const CHAT_QUIET_MS = 15000; // no events for this long -> "chat: quiet"

  // Embedded to avoid asset fetches; kept in sync with rule-labels.json by tests.
  const RULE_LABELS = {
  "allow": "Allow",
  "announce": "Announce",
  "armed_readout": "Read-out of a decoded message",
  "armed_refusal": "Armed refusal",
  "attention_ping": "Attention ping",
  "bot_command": "Bot command",
  "caps": "Caps",
  "chatter": "Chatter",
  "copypasta": "Copypasta",
  "decline_ask": "Decline ask",
  "decode_request": "Decode request",
  "decoded_echo": "Decoded echo",
  "decoded_name": "Decoded name",
  "direct_ask": "Direct ask",
  "duplicate": "Repeat of their earlier message",
  "empty": "Empty",
  "encoded_carrier": "Encoded carrier",
  "encoded_payload": "Encoded payload",
  "fail_closed": "Safety check failed closed",
  "fake_authority": "Fake authority",
  "forged_authority": "Forged authority",
  "forged_identity": "Forged identity",
  "injection": "Injection",
  "link": "Link",
  "malformed": "Malformed",
  "meta": "Meta",
  "never_say": "Never-say phrase",
  "newcomer": "Newcomer",
  "privacy_fishing": "Privacy fishing",
  "profanity": "Profanity detected",
  "promo": "Promo",
  "protected_name": "Protected name",
  "question": "Question",
  "rate_limited": "Rate limited",
  "repeat": "Repeat",
  "repeat_on_request": "Repeat on request",
  "repetitive": "Repetitive",
  "rudeness": "Rudeness",
  "say_request": "Say request",
  "slur": "Slur detected",
  "system_text": "System or provider notice",
  "symbols": "Symbols",
  "translation_ask": "Translation ask",
  "wall": "Wall",
  "no_current_turn": "No current turn",
  "privacy": "Privacy cut active",
  "off_air": "Show is off air",
  "approval_queue_full": "Approval queue full"
};

  const MODE_LABEL = { off_air: "OFF AIR", paused: "PAUSED", approval: "APPROVAL", autonomous: "AUTONOMOUS" };
  const MODE_NAME = { off_air: "Off air", paused: "Paused", approval: "Approval", autonomous: "Autonomous" };

  /* The new-operator path, using only existing Hermes and On Air commands. "stage" is an example
   * profile name. Nothing here reads, shows or copies a credential. */
  const SETUP_STEPS = [
    { title: "Use a dedicated stage profile", commands: ["hermes profile create stage"],
      help: "Keep private memory, personal SOUL.md text and other LLM middleware plugins out of it. \u201cstage\u201d is an example name: use yours in every command." },
    // Where On Air is installed from varies, so that command is a template with no Copy button.
    { title: "Install On Air in that profile", commands: [{ template: ["hermes -p stage plugins install ", "<git-url>", " --enable"] }, "hermes -p stage plugins enable on-air"],
      help: "Plugins are per profile, so a new profile starts without On Air. Replace `<git-url>` with the Git URL you installed On Air from (a local clone works as a `file://` URL). If On Air is already installed in this profile but disabled, the second command turns it on." },
    { title: "Print the settings for that profile", commands: ["hermes -p stage onair setup"],
      help: "Prints the `config.yaml` and `.env` lines to merge into the stage profile, then runs preflight. It writes nothing itself." },
    { title: "Pass preflight", commands: ["hermes -p stage onair preflight"],
      help: "Every check must pass; warnings are advice. The show runs preflight again each time you arm." },
    { title: "Start the stage gateway", commands: ["hermes -p stage gateway run"],
      help: "Runs in the foreground: keep that terminal open. Ctrl+C there stops the gateway and the show." },
    { title: "Open this desk from the same profile", commands: ["hermes -p stage dashboard --isolated", "hermes -p stage onair status"],
      help: "The desk only finds a show started from the same profile as the dashboard serving it. `--isolated` gives the stage profile its own dashboard (add `--port` if the default port is taken); open its On Air tab. The status command reads the same state from a terminal." },
  ];
  const OUTPUT_HELP = [
    ["Captions only", "A complete show with no voice. Set `voice: false` in the On Air plugin settings; preflight then reports captions only."],
    ["Voice (optional)", "Speaks lines through your Hermes TTS provider, after the guard and after approval. Preflight cannot verify audio routing, so it warns: test voice before going live. Mute (M) silences it at any time."],
    ["OBS (optional)", "Once connected, Show state lists the loopback overlay address to add as an OBS browser source. Without OBS you can still rehearse from this desk."],
  ];
  const MODE_HELP = [
    ["Off air", "Nothing airs. To arm, choose Approval: preflight runs first."],
    ["Paused", "Armed but holding: no new turns start and alerts wait. Stop all lands here."],
    ["Approval", "Every line waits in the queue for you to approve or reject."],
    ["Autonomous", "Guarded lines air without waiting for you. Only from Approval, after a confirmation."],
  ];
  // A fix for each known preflight check (names from onair/preflight.py). Unknown checks get none.
  const CHECK_FIX = {
    enabled: "Set `gateway.platforms.on_air.enabled` to `true` in the stage profile's `config.yaml`.",
    trusted_inbound: "Remove trusted_inbound from the On Air platform settings.",
    toolsets: "Set `platform_toolsets.on_air` to exactly `[no_mcp]`.",
    agent_runtime: "Remove the codex_app_server runtime from the stage profile's model settings.",
    context_engine: "Set `context.engine` to `compressor`.",
    allowed_users: "Set `ONAIR_ALLOWED_USERS=onair-stage` in the stage profile's `.env`, then restart the stage gateway.",
    memory: "Turn off `memory.memory_enabled` and `memory.user_profile_enabled`, and remove `memory.provider`.",
    auto_tts: "Set `voice.auto_tts: false` in the stage profile.",
    seed_bank: "Point seed_bank at a readable YAML seed bank with at least one beat.",
    never_say: "List never_say as non-empty phrases.",
    profanity: "Set profanity to mask, block or allow.",
    channel: "Set twitch_channel to your channel name.",
    overlay_port: "Free the overlay port (18790 by default) or set overlay_port to a free port.",
    tts: "Set voice to true or false (false means captions only).",
    runtime_gate: "Enable the On Air plugin in this profile (`hermes -p stage plugins enable on-air`), then restart the stage gateway.",
    runtime_tools: "Follows the runtime_gate check: fix that one first.",
    preflight: "Run `hermes -p stage onair preflight` in a terminal to see each check.",
  };
  // What each latching runtime rule means (onair/runtime_guard.py LATCHING_RULES) and what to fix.
  const RUNTIME_HELP = {
    tool_surface: { what: "A provider request for the stage carried tool schemas, so the gate refused it before the provider was called.",
      fix: "Keep `platform_toolsets.on_air` exactly `[no_mcp]` and remove any plugin that adds tools to the stage profile." },
    private_context: { what: "A provider request carried a Hermes memory, user-profile or memory-provider block, so the gate refused it before the provider was called.",
      fix: "Turn off built-in memory and the user profile in the stage profile and remove its memory provider." },
    unsupported_payload: { what: "The gate could not inspect part of a provider request, so it refused the request rather than guess.",
      fix: "Use a chat-completions, Anthropic, Bedrock or Responses provider, with no other LLM middleware plugins in the stage profile." },
    unsupported_api_mode: { what: "The stage model uses an API mode the gate cannot inspect, so the request was refused before the provider was called.",
      fix: "Switch the stage profile to a chat-completions, Anthropic, Bedrock or Responses provider." },
    gate_not_observed: { what: "A reply arrived that the gate never saw pass (for example from a runtime that bypasses the agent loop), so it was withheld.",
      fix: "Remove any codex_app_server runtime and other LLM middleware plugins from the stage profile." },
    internal_error: { what: "The gate failed closed on an internal error.",
      fix: "Check the stage gateway's log for the error and fix its cause." },
  };

  const get = (path) =>
    SDK.fetchJSON(BASE + path).catch(() => {
      throw new Error("unreachable");
    });
  const post = (path, body) =>
    SDK.fetchJSON(BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((result) => {
      // Only an explicit {ok: true} from the show is a confirmation.
      if (!result || typeof result !== "object" || result.ok !== true)
        throw new Error(result && typeof result.error === "string" && result.error ? result.error : "no confirmation from the show");
      return result;
    });

  const LIVE_API = { get, post };
  /* The practice show answers the same desk routes under practice/ (in-memory, never discoverable). */
  const PRACTICE_API = {
    get: (path) => get("practice/" + path),
    post: (path, body) => post("practice/" + path, body),
  };


  /* One terminal command, shared by the desk and the setup wizard. An exact command gets a Copy
   * button; a template has a part the user must fill in, so it is marked and never offered for copying. */
  function commandView(value, copied, onCopy) {
    return value && value.template
      ? h("div", { className: "onair-command onair-command-template", key: value.template.join("") },
        h("kbd", { className: "onair-template" }, value.template[0], h("var", null, value.template[1]), value.template[2]),
        h("span", { className: "onair-fill" }, "Fill in"))
      : h("div", { className: "onair-command", key: value },
        h("kbd", { className: "onair-cmd" }, value),
        h("button", { type: "button", onClick: (event) => onCopy(value, event), className: "onair-copy",
          "aria-label": (copied === value ? "Copied command: " : "Copy command: ") + value }, copied === value ? "Copied" : "Copy"));
  }

  /* One plain-language status and ONE next action. Never claims a broadcast or working voice. */
  function plainStatus(d, online, practice, waiting, blocked, lost) {
    const source = practice
      ? { label: "Chat source", value: "PRACTICE (scripted, not Twitch)", tone: "info" }
      : online ? { label: "Chat source", value: "Stage connected: the desk controls reach it. This desk cannot see whether Twitch chat is being read.", tone: "info" }
        : { label: "Chat source", value: lost ? "CONNECTION LOST" : "NOT CONNECTED", tone: "warn" };
    if (!online || !d) {
      return { facts: [source, { label: "Showing", value: "Nothing from this desk", tone: "muted" }],
        next: practice ? "Press Start practice show above." : lost
          ? "Cut at the source if needed (Ctrl+C in the stage terminal or hide the OBS source); this desk retries every second."
          : "Open Start here to set up a stage, or Practice to try the desk safely." };
    }
    const modeText = { off_air: "Off air (nothing is read or shown)", paused: "Paused (nothing new is shown)",
      approval: waiting ? "Waiting for your approval" : "Approval: every line waits for you", autonomous: "Autonomous (lines air without you)" }[d.mode] || String(d.mode);
    const facts = [source,
      { label: "Show", value: modeText, tone: d.mode === "approval" ? "ok" : d.mode === "autonomous" ? "warn" : "muted" },
      { label: "Overlay", value: d.privacy ? "Privacy cover up ('Be right back')" : d.mode === "off_air" ? "Nothing showing" : d.mode === "paused" ? "Paused: nothing new is shown" : d.mode === "autonomous" ? "Guarded captions can appear without approval" : "Captions show after you approve", tone: d.privacy ? "warn" : "info" },
      { label: "Voice", value: d.muted ? "Muted" : d.voice ? "On (not verified by this desk)" : "Off (captions only)", tone: "muted" }];
    let next;
    if (blocked) next = "Fix the failing check shown below, then choose Approval.";
    else if (d.privacy) next = "The privacy cover is up. Press Uncover (P) when you are ready to continue.";
    else if (d.mode === "off_air") next = "Choose Approval under Show state to start. Nothing airs until you approve it.";
    else if (d.mode === "paused") next = "Paused. Choose Approval under Show state to resume.";
    else if (d.mode === "autonomous") next = "Guarded lines air without approval. Choose Approval to review each line.";
    else if (waiting) next = "Read the draft in the Approval queue, then Approve or Reject it.";
    else next = "Nothing to review. Drafts appear in the Approval queue when chat asks something.";
    return { facts, next };
  }

  /* Only a plain loopback overlay URL may become a link. */
  function overlayLink(value) {
    try {
      const url = new URL(value);
      return url.protocol === "http:" &&
        ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) &&
        url.pathname === "/overlay" && !url.username && !url.password
        ? url.href
        : null;
    } catch (_) {
      return null;
    }
  }

  function humanLabel(value) {
    if (typeof value !== "string" || !value) return "Unavailable";
    return Object.prototype.hasOwnProperty.call(RULE_LABELS, value) ? RULE_LABELS[value] :
      value.replace(/[_-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  }

  /* One readable line per receipt. The guard at work is the demo: show what was
   * withheld or skipped, and why, from the receipt's own rule. */
  function receiptLine(entry) {
    if (!entry || typeof entry !== "object") return null;
    if (entry.kind === "line") {
      if (entry.disposition === "withheld")
        return { tag: "withheld", text: "Withheld line — " + humanLabel(entry.rule) + (entry.text ? ": " + entry.text : "") };
      if (entry.disposition === "released") return { tag: "aired", text: "Aired line: " + (entry.text || "") };
      return { tag: entry.disposition || "line", text: (entry.disposition || "line") + (entry.text ? ": " + entry.text : "") };
    }
    if (entry.kind === "chat") {
      const passed = entry.verdict === "worthy" || entry.verdict === "routine";
      return passed
        ? { tag: "passed", text: "Chat passed (" + humanLabel(entry.rule) + "): " + (entry.user || "viewer") + ": " + (entry.text || "") }
        : { tag: "skipped", text: "Skipped chat (" + humanLabel(entry.rule) + "): " + (entry.user || "viewer") + ": " + (entry.text || "") };
    }
    if (entry.kind === "alert")
      return { tag: "alert", text: "Alert: " + alertLine({ kind: entry.alert_kind, name: entry.name, amount: entry.amount, months: entry.months }) };
    if (entry.kind === "support")
      return { tag: "support", text: "Support " + humanLabel(entry.support_kind || "event") + " · " + humanLabel(entry.disposition) + (entry.reason ? " (" + humanLabel(entry.reason) + ")" : "") };
    if (entry.kind === "runtime_refused")
      return { tag: "runtime", text: "Runtime gate refused a provider request (" + humanLabel(entry.rule) + ") · show cut to OFF AIR" };
    if (entry.kind === "runtime_cleared")
      return { tag: "runtime", text: "Runtime latch cleared (" + humanLabel(entry.rule) + ")" };
    return null;
  }

  /* Preflight as /state reports it: the latch reason (runtime_refused:<rule>) and the failing
   * checks. Anything malformed reads as nothing to show; the server still refuses to arm. */
  function preflightView(data) {
    const pf = data && data.preflight && typeof data.preflight === "object" ? data.preflight : null;
    if (!pf) return { reason: "", failing: [], latched: false };
    const reason = typeof pf.reason === "string" ? pf.reason : "";
    const failing = Array.isArray(pf.checks)
      ? pf.checks.filter((check) => check && typeof check === "object" && check.status === "fail")
      : [];
    return { reason, failing, latched: reason.startsWith("runtime_refused:") };
  }

  /* One wording for privacy and audio, shared by the banner, chips, state line and controls. */
  const PRIVACY_BANNER = "PRIVACY CUT ON · viewers see 'Be right back' · audio blocked · pending cleared";
  function privacyLine(d) { return d.privacy ? "Privacy cut ON" : "Privacy cut off"; }
  function audioLine(d) {
    if (d.muted) return "Muted";
    if (d.privacy) return "Audio blocked by privacy";
    // voice is false when it is configured off or TTS is unavailable: both mean no voice.
    return d.voice === false ? "No voice (captions only)" : "Sound on";
  }
  function stateParts(d, practice) {
    const parts = [MODE_LABEL[d.mode] || "Unknown mode", practice ? "PRACTICE · NOT BROADCAST" : d.live ? "LIVE" : "NOT LIVE", privacyLine(d), audioLine(d)];
    if (typeof d.pending === "number") parts.push(d.pending + " pending");
    return parts;
  }

  /* Fixed help strings only: `backticked` parts are literal names that must not break. */
  function richText(text) {
    return text.split("`").map((part, i) => (i % 2 ? h("span", { key: i, className: "onair-literal" }, part) : part));
  }

  /* A readable name for each desk action, so a notice says what was (not) confirmed. */
  function actionLabel(path, body, mode) {
    if (path === "privacy") return body && body.on ? "Privacy cut" : "Privacy off";
    if (path === "mute") return body && body.on ? "Mute" : "Unmute";
    if (path === "mode") return mode === "off_air" && body.mode === "approval" ? "Arm into Approval" : "Switch to " + (MODE_NAME[body.mode] || "mode");
    return { approve: "Approve", reject: "Reject", stop: "Stop all", "runtime/clear": "Clear runtime latch", alerts: "Alert settings", "assist/test": "Test assistance connection" }[path] || "Action";
  }

  function secondsAgo(ms) {
    const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000));
    return seconds < 1 ? "just now" : seconds < 120 ? seconds + " s ago" : Math.round(seconds / 60) + " min ago";
  }

  function relativeTime(ts) {
    if (typeof ts !== "number" || !Number.isFinite(ts) || ts <= 0) return "Time unavailable";
    const seconds = Math.floor((Date.now() - ts * 1000) / 1000);
    if (seconds < 0) return "Time in future";
    if (seconds < 60) return "Just now";
    if (seconds < 3600) return Math.floor(seconds / 60) + " min ago";
    if (seconds < 86400) return Math.floor(seconds / 3600) + " hr ago";
    return Math.floor(seconds / 86400) + " days ago";
  }

  function guardEntry(entry, i) {
    const output = entry.kind === "line";
    const rule = typeof entry.rule === "string" && entry.rule ? entry.rule : "unavailable";
    const label = Object.prototype.hasOwnProperty.call(RULE_LABELS, rule) ? RULE_LABELS[rule] : "Unknown rule";
    return h("li", { key: entry.n || i, className: "onair-guard-entry " + (output ? "onair-withheld" : "onair-skipped") },
      h("div", { className: "onair-guard-meta" },
        h("span", { className: "onair-tag" }, output ? "Line withheld" : "Chat skipped"),
        h("strong", null, label), h("code", null, rule),
        h("time", null, relativeTime(entry.ts))),
      !output && h("p", { className: "onair-guard-name" }, entry.user || "viewer"),
      h("p", { className: "onair-guard-text" }, typeof entry.text === "string" ? entry.text : ""));
  }


  /* Optional semantic assistance: SHADOW advice next to a queued line. It is display only;
   * no recommendation reaches approve/reject, and anything unknown reads as "review". */
  const ASSIST_RECS = { approve: "✓ suggests approve", withhold: "✕ suggests withhold", review: "? suggests review" };
  const ASSIST_PROVIDERS = { off: "Off", laya: "Laya (local guardrail service)", jev: "Jev (TypeSafe, needs a key)", demo: "Demo (toy rules, not a classifier)" };
  const ASSIST_STATES = { off: "Off", shadow: "Shadow", misconfigured: "Misconfigured", key_absent: "Key absent", unavailable: "Unavailable" };
  function assistChip(a) {
    if (!a || typeof a !== "object") return null;
    if (a.pending) return h("p", { className: "onair-assist-chip onair-assist-pending" }, h("span", { className: "onair-tag" }, "Shadow"), " Checking… (advice only; approve or reject as usual)");
    const known = Object.prototype.hasOwnProperty.call(ASSIST_RECS, a.recommendation);
    const rec = known ? a.recommendation : "review";
    // A confidence is shown only for a known label and only inside [0, 1].
    const conf = known && typeof a.confidence === "number" && Number.isFinite(a.confidence) && a.confidence >= 0 && a.confidence <= 1
      ? " · " + Math.round(a.confidence * 100) + "%" : "";
    const status = typeof a.status === "string" && a.status !== "ok" ? " · " + humanLabel(a.status) : "";
    const ms = typeof a.latency_ms === "number" && Number.isFinite(a.latency_ms) ? " · " + Math.round(a.latency_ms) + " ms" : "";
    const who = typeof a.provider === "string" && a.provider ? a.provider : "assist";
    return h("p", { className: "onair-assist-chip onair-assist-" + rec },
      h("span", { className: "onair-tag" }, "Shadow · " + who),
      " " + ASSIST_RECS[rec] + conf + status + ms,
      typeof a.basis === "string" && a.basis ? h("span", { className: "onair-assist-basis" }, a.basis) : null);
  }
  function assistTestLine(t) {
    if (!t || typeof t !== "object") return "Not tested yet.";
    if (t.ok) {
      const info = t.info && typeof t.info === "object" ? t.info : {};
      return "Last test passed" + (typeof t.latency_ms === "number" ? " in " + Math.round(t.latency_ms) + " ms" : "") +
        (info.model ? " · " + info.model : "") + (info.network ? " · " + info.network : "") + ".";
    }
    return "Last test failed: " + (typeof t.detail === "string" ? t.detail : "unavailable");
  }

  function alertLine(entry) {
    if (!entry || typeof entry !== "object") return "";
    const name = entry.name || "Someone";
    const amount = entry.amount;
    const months = entry.months;
    const unit = (n, singular, plural) => n + " " + (n === 1 ? singular : plural);
    switch (entry.kind) {
      case "raid": return name + " raided · " + unit(amount || 0, "viewer", "viewers");
      case "giftbomb": return name + " gifted " + unit(amount || 0, "gift sub", "gift subs");
      case "bits": return name + " cheered · " + unit(amount || 0, "bit", "bits");
      case "sub": case "resub": return name + (entry.kind === "sub" ? " subscribed" : " resubscribed") + (months > 0 ? " · " + unit(months, "month", "months") : "");
      case "subgift": return name + " gifted a sub";
      default: return name + " · " + humanLabel(entry.kind || "support");
    }
  }

  function HostLine({ data, online, fence }) {
    const newDraftId = () => Array.from(window.crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, "0")).join("");
    const [draft, setDraft] = useState({text:"", epoch:null, boot:null, fence:null});
    const [message, setMessage] = useState("");
    const [busy, setBusy] = useState(false);
    const cutLock = useRef(false);
    const ready = !!(online && data && data.host_turns && data.host_turns.enabled === true &&
      data.live === true && data.privacy === false && ["approval", "autonomous"].includes(data.mode) && Number.isSafeInteger(data.epoch) && /^[0-9a-f]{32}$/.test(data.voice_boot));
    const retry = !!(online && data && data.host_turns && data.host_turns.enabled === true &&
      data.mode === "paused" && data.model_failure_paused === true && data.privacy === false &&
      Number.isSafeInteger(data.epoch) && /^[0-9a-f]{32}$/.test(data.voice_boot));
    const canType = ready || retry;
    const stale = !!draft.text && (!canType || draft.retry !== retry || draft.epoch !== data.epoch || draft.boot !== data.voice_boot || draft.fence !== fence);
    useLayoutEffect(() => {
      if (stale) { setDraft({text:"", epoch:null, boot:null, fence:null}); setMessage("Draft cleared because the show state changed or was lost. Re-enter your line."); }
    }, [stale]);
    const [Mic, setMic] = useState(null);
    useEffect(() => {
      if (!online || !data || !data.host_turns || data.host_turns.enabled !== true) return;
      let live = true;
      const script = document.createElement("script");
      script.src = MIC_ASSET;
      script.onload = () => { if (live && window.OnAirHostMic) setMic(() => window.OnAirHostMic); };
      document.head.appendChild(script);
      return () => { live = false; script.remove(); };
    }, [online, !!(data && data.host_turns && data.host_turns.enabled)]);
    async function cut() {
      if (!ready || busy || cutLock.current) return;
      cutLock.current = true;
      const binding = {epoch:data.epoch, boot:data.voice_boot};
      setDraft({text:"", epoch:null, boot:null, fence:null});
      setBusy(true);
      try {
        const result = await SDK.fetchJSON(BASE + "cut-in", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(binding)});
        setMessage(result && result.ok === true ? "Cut requested; old work was discarded." : "Cut was refused; refresh the show state.");
      } catch (_) { setMessage("Cut could not be confirmed; refresh the show state."); }
      finally { cutLock.current = false; setBusy(false); }
    }
    useEffect(() => {
      function key(e) {
        if (e.key.toLowerCase() !== "c" || e.repeat || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented ||
            (e.target && (e.target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)))) return;
        if (ready && !busy) {e.preventDefault(); cut();}
      }
      window.addEventListener("keydown", key);
      return () => window.removeEventListener("keydown", key);
    }, [ready, busy, data && data.epoch, data && data.voice_boot]);
    if (!online || !data || !data.host_turns || data.host_turns.enabled !== true) return null;
    async function send() {
      if (busy || !canType || stale || !draft.text.trim()) return;
      setBusy(true);
      try {
        const result = await SDK.fetchJSON(BASE + "host/turn", {method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({text:draft.text, epoch:draft.epoch, boot:draft.boot, id:draft.id})});
        setMessage(result.message || (result.ok && result.queued ? "Host line: Queued at position " + result.position + " at send time." : "Not queued."));
        if (result.ok && result.queued) setDraft(d => d.id === draft.id ? {text:"", epoch:null, boot:null, fence:null} : d);
      } catch (error) { setMessage(error && error.message || "Could not reach the show."); }
      finally { setBusy(false); }
    }
    return h("section", {className:"onair-panel onair-host-line", "aria-label":"Host line"},
      h("h2", null, "Host line"),
      h("label", {className:"onair-field"}, "Your line", h("input", {value:stale ? "" : draft.text, disabled:!canType,
        onChange:e=>{ const text = e.target.value; setDraft(d => ({text, epoch:d.text ? d.epoch : data.epoch, boot:d.text ? d.boot : data.voice_boot, fence:d.text ? d.fence : fence, retry:d.text ? d.retry : retry,
          id:d.text ? d.id : newDraftId()})); }, maxLength:2000})),
      h("button", {type:"button", disabled:busy || !canType || stale || !draft.text.trim(), onClick:send}, "Send to co-host"),
      h("button", {type:"button", className:"onair-cut-button", disabled:busy || !ready, onClick:cut}, "Cut in"),
      h("p", null, "Cut in stops the current line and discards waiting work. Shortcut: C when not typing."),
      h("p", {role:"status"}, message),
      Mic && h(Mic, {data, online, draftPresent:!!draft.text && !stale, onStart:()=>setMessage(""), onReview:(value, epoch, boot)=>setDraft({text:value, epoch, boot, fence, retry:false, id:newDraftId()})}));
  }

  function Views() {
    const [state, setState] = useState(null);
    const [message, setMessage] = useState("");
    const [busy, setBusy] = useState(false);
    const generation = useRef(0);
    const alive = useRef(true);
    const inFlight = useRef(false);
    async function refresh(force) {
      if (inFlight.current && !force) return;
      const seq = ++generation.current;
      try {
        const result = await get("views/state");
        if (alive.current && generation.current === seq) setState(result);
      } catch (_) {
        if (alive.current && generation.current === seq) setState({ok:false, connected:false, error:"Views are unavailable from this dashboard."});
      }
    }
    useEffect(() => { alive.current=true; refresh(); const timer=window.setInterval(refresh, 5000);
      return () => { alive.current=false; generation.current++; window.clearInterval(timer); }; }, []);
    function refusal(result, fallback) {
      return result && [result.error || result.message, result.next_human_action].filter(Boolean).filter((s, i, a) => a.indexOf(s) === i).join(" ") || fallback;
    }
    async function request(path, body, after) {
      if (inFlight.current) return;
      inFlight.current = true; setBusy(true); setMessage(""); generation.current++;
      try {
        const result=await SDK.fetchJSON(BASE + "views/" + path, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
        if (!result || result.ok !== true) setMessage(refusal(result, "No confirmation from OBS."));
        else if (after) {
          const switched=await SDK.fetchJSON(BASE + "views/scene", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({scene:result.scene})});
          setMessage(switched && switched.ok ? "OBS confirmed the look." : refusal(switched, "Scene switch not confirmed."));
        } else setMessage(result.note || "OBS confirmed the action.");
      } catch (error) { setMessage(refusal(error && (error.body || error.data || error.detail), error && error.message || "Views action could not be confirmed.")); }
      finally { await refresh(true); inFlight.current = false; if (alive.current) setBusy(false); }
    }
    const connected=!!(state && state.ok && state.connected);
    const boxes=connected && state.boxes || {};
    const slots=boxes.slots || null;
    const available=boxes.available || {};
    const stage=!!(state && state.stage_profile);
    const disabled=!connected || busy || stage;
    const boxUnavailable=!!(slots && Object.values(slots).some(source => source!=="empty" && !(available[source] && available[source].available)));
    const boxesDisabled=disabled || boxUnavailable;
    const labels={scene:"Scene",slides:"Slides",window:"Window",empty:"Empty"};
    const labelSlots={main:"Main",sub1:"Sub 1",sub2:"Sub 2"};
    const windowState=connected && state.window || {};
    const windowInstruction=windowState.ready ? "OBS reports a selection with frames. Check the OBS preview before showing it." :
      windowState.next_human_action || (windowState.prerequisites || []).join(" ") || "Choose a window in OBS and check its preview.";
    const current = state && state.layout && state.layout.current || {};
    const scenes = state && state.layout && state.layout.scenes || {};
    const currentPreset=connected && (scenes.presentation && state.program_scene === scenes.presentation.name ? current.presentation :
      scenes.window && state.program_scene === scenes.window.name ? current.window : null);
    const looks=[["presentation","Slides"],["window","Shared window"],["window_pip","Window + slides inset"],["side_by_side","Side by side"]];
    const presets=state && state.layout && state.layout.presets || {};
    return h("section", {className:"onair-panel onair-views", "aria-labelledby":"onair-views", "aria-busy":busy},
      h("h2", {id:"onair-views"}, "Views"),
      h("p", {className:"onair-help"}, "Three boxes: choose each source once. Captions and voice stay on top, outside the boxes. Changes to the live scene are immediately visible."),
      !connected && h("p", {role:"status"}, state && state.error || "Checking OBS…"),
      connected && h(React.Fragment, null,
        h("p", null, "Program scene: ", h("strong", null, state.program_scene || "Unknown")),
        h("p", null, "Stream: ", state.stream && state.stream.active === true ? "live" : state.stream && state.stream.active === false ? "not live" : "unknown", " (OBS)."),
        !stage && state.limitation && h("p", {className:"onair-help"}, state.limitation),
        stage && h("p", {className:"onair-help onair-stage-reason"}, "This stage profile cannot change views from the desk. Say to your owner agent: “Please change the view.”")),
      boxUnavailable && !stage && h("p", {className:"onair-help"}, "Box controls are disabled because a selected source is unavailable to this dashboard. Ask your owner agent to use onair_layout to change the boxes."),
      slots && h("p", {className:"onair-help"}, "Arrangement: ", boxes.arrangement === "side" ? "side by side" : "stacked", ". Main: ", labels[slots.main] || slots.main, "; Sub 1: ", labels[slots.sub1] || slots.sub1, "; Sub 2: ", labels[slots.sub2] || slots.sub2, "."),
      h("div", {className:"onair-box-diagram onair-box-" + (boxes.arrangement || "stack"), role:"group", "aria-label":"Box arrangement"},
        ["main","sub1","sub2"].map(slot=>h("label", {className:"onair-box onair-box-"+slot,key:slot},
          h("strong", null,labelSlots[slot]),
          h("select", {"aria-label":labelSlots[slot]+" source",value:slots ? slots[slot] : "",disabled:boxesDisabled || !slots,
            onChange:e=>request("boxes",{op:"put",slot,source:e.target.value})},
            !slots && h("option", {value:""}, "Unavailable"),
            ["scene","slides","window","empty"].map(source=>h("option", {key:source,value:source,
              disabled:(source==="empty" && slot==="main") || (source!=="empty" && !(available[source] && available[source].available))},labels[source])))))),
      h("div", {className:"onair-view-actions"},
        [["main","sub1"],["main","sub2"],["sub1","sub2"]].map(([a,b])=>h("button",{type:"button",key:a+b,disabled:boxesDisabled || !slots || ((a==="main" || b==="main") && slots[b]==="empty"),onClick:()=>request("boxes",{op:"swap",a,b})},"Swap "+labelSlots[a]+" and "+labelSlots[b])),
        ["stack","side"].map(arrangement=>h("button",{type:"button",key:arrangement,disabled:boxesDisabled || !slots,"aria-pressed":boxes.arrangement===arrangement,onClick:()=>request("boxes",{op:"arrangement",arrangement})},arrangement==="stack"?"Stack":"Side")),
        h("button",{type:"button",disabled:boxesDisabled || !slots,onClick:()=>request("scene",{scene:"On Air: Boxes"})},"Show Boxes on stream"),
        !slots && h("button",{type:"button",disabled:disabled || !(available.scene && available.scene.available),onClick:()=>request("boxes",{op:"set",main:"scene",sub1:"empty",sub2:"empty",arrangement:"stack"})},"Set up boxes with Scene")),
      connected && Object.entries(available).filter(([name,value])=>name!=="empty" && !value.available).map(([name,value])=>h("p",{key:name,className:"onair-help"},labels[name]+" unavailable: "+value.reason)),
      h("h3", null, "Simple looks"), h("p", {className:"onair-help"}, "Fixed presets, not free source assignments."),
      h("div",{className:"onair-view-actions"},looks.map(([preset,label])=>h("button",{type:"button",key:preset,
        disabled:disabled || !(presets[preset] && presets[preset].ready),"aria-pressed":!!(currentPreset && currentPreset.preset===preset),
        onClick:()=>request("layout",{preset},true)},label)),
        connected && state.scenes_present && state.scenes_present["On Air: 3D scene"] && h("button",{type:"button",disabled:disabled,"aria-pressed":state.program_scene==="On Air: 3D scene",onClick:()=>request("scene",{scene:"On Air: 3D scene"})},"3D scene")),
      h("h3", null, "Window sharing"),
      h("p", null, !connected ? "State unavailable." : state.window_state === "showing" ? "Window item is enabled on OBS program (render not verified)." :
        state.window_state === "confirmed" ? "Confirmed in this runtime." : state.window_state === "chosen_confirmation_unknown" ? "Chosen; the owner session's confirmation is unknown here." :
        !windowState.exists ? "No window prepared." : windowState.ready ? "Ready in this dashboard." : windowState.selected ? "Chosen; owner confirmation may be needed." : "Prepared; choose a window."),
      connected && h("p", {className:"onair-help"}, windowInstruction),
      !stage && h("p", {className:"onair-help"}, "Prepare and chooser must run in your owner agent to preserve its confirmation: ask for onair_window_share prepare or open_chooser. On Wayland check the OBS preview, type /onair-broadcast confirm-window and confirm the card."),
      h("div",{className:"onair-view-actions"},["discover","prepare","open_chooser","show","hide"].map(action=>h("button",{type:"button",key:action,disabled:disabled || action==="prepare" || action==="open_chooser" || (action==="show" && !windowState.ready),onClick:()=>request("window",{action})},({discover:"Discover",prepare:"Prepare",open_chooser:"Choose window",show:"Show",hide:"Hide"})[action]))),
      h("p",{role:"status"},busy ? "Applying change · Checking OBS…" : message));
  }

  function Desk(props) {
    const { get, post } = (props && props.api) || LIVE_API;
    const practice = !!(props && props.practice);
    const [data, setData] = useState(null);
    const [queue, setQueue] = useState([]);
    const [receipts, setReceipts] = useState([]);
    const [guardLimit, setGuardLimit] = useState({ signature: null, visible: 0 });
    const [guardSize, setGuardSize] = useState(0);
    const [queueMore, setQueueMore] = useState(0);
    const [showAllGuard, setShowAllGuard] = useState(false);
    const guardList = useRef(null);
    const queueList = useRef(null);
    const scrollQueueSelection = useRef(false);
    const [receiptsAvailable, setReceiptsAvailable] = useState(false);
    const [alerts, setAlerts] = useState(null);
    // null means unavailable (no answer yet, or the show stopped answering); [] means none.
    const [recent, setRecent] = useState(null);
    // Optional semantic assistance status (shadow only); null = unavailable, never drives a control.
    const [assist, setAssist] = useState(null);
    const [showMoreAlerts, setShowMoreAlerts] = useState(false);
    const [online, setOnline] = useState(false);
    const [hostFence, setHostFence] = useState(0);
    // phase: connecting (no answer yet) · setup (never connected) · online · lost (was connected).
    const [conn, setConn] = useState({ phase: "connecting", lastOk: 0, reason: "", checks: 0 });
    const [notice, setNotice] = useState("");
    const [busy, setBusy] = useState(false);
    const [selected, setSelected] = useState(0);
    const [alertDraft, setAlertDraft] = useState(null);
    const [copied, setCopied] = useState("");
    const [copyStatus, setCopyStatus] = useState("");
    const alive = useRef(true);
    const actionLock = useRef(false);
    const safetyPending = useRef(0);
    const generation = useRef(0);
    const pollInFlight = useRef(false);
    const lastSeqMove = useRef({ seq: null, at: 0 });
    const current = useRef({});
    // When the current notice was set, so an old result reads as history ("Last action: … · 12 s ago").
    const noticeAt = useRef({ text: "", at: 0 });
    if (noticeAt.current.text !== notice) noticeAt.current = { text: notice, at: Date.now() };
    current.current = { data, queue, online, busy, selected };

    async function refresh() {
      const gen = ++generation.current;
      const results = await Promise.allSettled([
        get("state"), get("pending"), get("receipts?limit=60"), get("alerts"), get("alerts/recent"), get("assist"),
      ]);
      if (!alive.current || gen !== generation.current || safetyPending.current) return undefined;
      const [s, p, r, a, ar, asr] = results;
      const state = s.status === "fulfilled" ? s.value : null;
      const pend = p.status === "fulfilled" ? p.value : null;
      const answered = state && typeof state === "object" && pend && Array.isArray(pend.pending);
      if (!answered || state.epoch !== pend.epoch) {
        // Fail closed: nothing stale stays actionable. The last state is kept only as a labeled
        // "last confirmed" summary; it never drives a control, chip or banner.
        setOnline(false);
        setQueue([]);
        setReceiptsAvailable(false);
        setReceipts([]);
        setAlerts(null);
        setRecent(null);
        setAssist(null);
        setAlertDraft(null); // an unsaved edit cannot be confirmed against a state we cannot see
        setShowMoreAlerts(false);
        setConn((c) => ({ phase: c.lastOk ? "lost" : "setup", lastOk: c.lastOk,
          reason: answered ? "inconsistent" : "unreachable", checks: c.checks + 1 }));
        return null;
      }
      setData(state);
      setQueue(pend.pending);
      setOnline(true);
      setConn({ phase: "online", lastOk: Date.now(), reason: "", checks: 0 });
      setSelected((n) => Math.min(n, Math.max(0, pend.pending.length - 1)));
      const receiptList = r.status === "fulfilled" && Array.isArray(r.value && r.value.receipts) ? r.value.receipts.slice(-60) : null;
      setReceiptsAvailable(!!receiptList);
      setReceipts(receiptList || []);
      if (a.status === "fulfilled" && a.value && typeof a.value === "object") setAlerts(a.value.alerts || a.value);
      else { setAlerts(null); setAlertDraft(null); }
      if (ar.status === "fulfilled" && ar.value) {
        const list = Array.isArray(ar.value) ? ar.value : ar.value.alerts || ar.value.recent || [];
        setRecent(Array.isArray(list) ? list : null);
      } else setRecent(null);
      setAssist(asr.status === "fulfilled" && asr.value && asr.value.assist && typeof asr.value.assist === "object" ? asr.value.assist : null);
      if (typeof state.seq === "number" && lastSeqMove.current.seq !== state.seq) {
        lastSeqMove.current = { seq: state.seq, at: Date.now() };
      }
      return state;
    }

    useEffect(() => {
      alive.current = true;
      async function poll() {
        if (pollInFlight.current || actionLock.current || safetyPending.current) return;
        pollInFlight.current = true;
        try {
          await refresh();
        } finally {
          pollInFlight.current = false;
        }
      }
      poll();
      const timer = window.setInterval(poll, POLL_MS);
      return () => {
        alive.current = false;
        generation.current++;
        window.clearInterval(timer);
      };
    }, []);

    const guardRows = receipts.filter((entry) => entry && (entry.kind === "line" && entry.disposition === "withheld" ||
      entry.kind === "chat" && entry.verdict !== "worthy" && entry.verdict !== "routine")).slice().reverse();
    const guardSignature = JSON.stringify([guardRows, guardSize]);

    // Measure all candidates before paint, then retain only whole rows.
    useLayoutEffect(() => {
      const list = guardList.current;
      if (!list || guardLimit.signature === guardSignature) return;
      const rows = Array.from(list.children);
      const rootSize = parseFloat(window.getComputedStyle(document.documentElement).fontSize) || 16;
      const budget = (parseFloat(window.getComputedStyle(list).getPropertyValue("--onair-guard-budget")) || 23) * rootSize;
      const top = list.getBoundingClientRect().top;
      let visible = 0;
      rows.forEach((row, i) => {
        const bottom = row.getBoundingClientRect().bottom - top;
        if (i === visible && (bottom <= budget || i === 0)) visible++;
      });
      setGuardLimit({ signature: guardSignature, visible });
    }, [guardSignature, showAllGuard, guardLimit.signature]);

    useEffect(() => {
      const resize = () => setGuardSize((n) => n + 1);
      window.addEventListener("resize", resize);
      return () => window.removeEventListener("resize", resize);
    }, []);

    useEffect(() => {
      const list = guardList.current;
      if (!list || typeof ResizeObserver === "undefined") return;
      let width = list.getBoundingClientRect().width;
      const observer = new ResizeObserver(() => {
        const next = list.getBoundingClientRect().width;
        if (Math.abs(next - width) > 0.5) { width = next; setGuardSize((n) => n + 1); }
      });
      observer.observe(list);
      return () => observer.disconnect();
    }, [receiptsAvailable, guardRows.length > 0]);

    useLayoutEffect(() => {
      const list = queueList.current;
      if (!list || queue.length <= 5) { setQueueMore(0); return; }
      const measure = () => {
        const bottom = list.getBoundingClientRect().bottom;
        setQueueMore(Array.from(list.children).filter((row) => row.getBoundingClientRect().bottom > bottom + 0.5).length);
      };
      measure();
      list.addEventListener("scroll", measure);
      window.addEventListener("resize", measure);
      const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
      if (observer) { observer.observe(list); Array.from(list.children).forEach((row) => observer.observe(row)); }
      return () => { list.removeEventListener("scroll", measure); window.removeEventListener("resize", measure); if (observer) observer.disconnect(); };
    }, [queue, selected]);

    useEffect(() => {
      if (!scrollQueueSelection.current) return;
      scrollQueueSelection.current = false;
      const row = queueList.current && queueList.current.children[selected];
      if (row && row.scrollIntoView) row.scrollIntoView({ block: "nearest", inline: "nearest" });
    }, [selected]);

    /* Fire-and-refresh for every desk action. Safety actions (privacy, mute,
     * stop) bypass the action lock and clear the queue optimistically: they must
     * never wait on the model, the queue or a poll. */
    async function action(path, body, safety) {
      if (!current.current.online || (!safety && (actionLock.current || safetyPending.current))) return;
      const label = actionLabel(path, body, current.current.data && current.current.data.mode);
      if (path === "stop" || (path === "privacy" && body && body.on === true) ||
          (path === "mode" && body && body.mode === "off_air")) setHostFence(n => n + 1);
      if (!safety) {
        actionLock.current = true;
        setBusy(true);
      } else {
        safetyPending.current++;
        setQueue([]);
      }
      generation.current++;
      let message = "";
      let confirmed = false;
      let fresh;
      try {
        await post(path, body);
        confirmed = true;
        message = label + " — " + path + " confirmed by the show.";
      } catch (error) {
        message = label + " — " + path + " NOT confirmed: " + (error && error.message ? error.message : "failed");
      } finally {
        if (alive.current) setNotice(message);
        if (safety) safetyPending.current--;
        if (alive.current && !safetyPending.current) fresh = await refresh();
        if (!safety) {
          actionLock.current = false;
          if (alive.current) setBusy(false);
        }
      }
      if (!alive.current || !confirmed) return;
      // A confirmation describes the request; only the refreshed state describes the show.
      if (fresh === null) setNotice(message + " Refresh failed: current state unknown.");
      else if (path === "runtime/clear" && fresh)
        setNotice(preflightView(fresh).latched
          ? message + " The refreshed state still reports the runtime latch."
          : message + " The show stays OFF AIR: choose Approval to arm (preflight runs again).");
    }

    function decide(which, line) {
      const c = current.current;
      if (!line || !c.online || c.busy || !c.data) return;
      if (line.epoch !== c.data.epoch) return;
      if (!c.queue.some((item) => item.line_id === line.line_id && item.epoch === line.epoch)) return;
      action(which, { line_id: line.line_id, epoch: line.epoch }, false);
    }

    function safetyKey(name) {
      const c = current.current;
      if (!c.online || !c.data) return;
      if (name === "privacy") action("privacy", { on: !c.data.privacy }, true);
      if (name === "mute") action("mute", { on: !c.data.muted }, true);
      if (name === "stop") action("stop", {}, true);
    }

    function requestMode(mode) {
      const c = current.current;
      if (!c.online || !c.data || c.data.mode === mode) return;
      if (mode === "autonomous") {
        if (c.data.mode !== "approval") return; // reachable only from approval
        if (!window.confirm("Autonomous mode releases guarded lines without desk approval. Continue?")) return;
      }
      action("mode", { mode }, false);
    }

    useEffect(() => {
      function key(event) {
        const el = event.target;
        if (
          event.defaultPrevented || event.repeat || event.ctrlKey || event.metaKey || event.altKey ||
          (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)))
        )
          return;
        const k = event.key.toLowerCase();
        if (k === "p") { event.preventDefault(); safetyKey("privacy"); return; }
        if (k === "m") { event.preventDefault(); safetyKey("mute"); return; }
        if (k === "escape") { event.preventDefault(); safetyKey("stop"); return; }
        const c = current.current;
        if (!c.online || c.busy || !c.queue.length) return;
        if (k === "arrowdown" || k === "j") {
          event.preventDefault();
          scrollQueueSelection.current = c.selected < c.queue.length - 1;
          setSelected((n) => Math.max(0, Math.min(c.queue.length - 1, n + 1)));
          return;
        }
        if (k === "arrowup" || k === "k") {
          event.preventDefault();
          scrollQueueSelection.current = c.selected > 0;
          setSelected((n) => Math.max(0, Math.min(c.queue.length - 1, n - 1)));
          return;
        }
        if (k === "a" || k === "r") {
          event.preventDefault();
          decide(k === "a" ? "approve" : "reject", c.queue[c.selected]);
        }
      }
      window.addEventListener("keydown", key);
      return () => window.removeEventListener("keydown", key);
    }, []);

    async function copyText(value, event) {
      // Capture the row now: React clears currentTarget once the handler returns.
      const row = event && event.currentTarget ? event.currentTarget.parentNode : null;
      try {
        if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") throw new Error("no clipboard");
        await navigator.clipboard.writeText(value);
        if (!alive.current) return;
        setCopied(value);
        setCopyStatus("Copied to the clipboard: " + value);
        window.setTimeout(() => { if (alive.current) setCopied((v) => (v === value ? "" : v)); }, 2000);
      } catch (_) {
        if (!alive.current) return;
        setCopyStatus("");
        setNotice("Copy failed — the text is selected; copy it manually.");
        try {
          const target = row && row.querySelector(".onair-cmd, a");
          if (target) window.getSelection().selectAllChildren(target);
        } catch (_) { /* selecting is a convenience only */ }
      }
    }

    // Only a state confirmed by the latest refresh drives chips, banners and controls.
    const shown = online && data ? data : null;
    // The show answered but its state and queue disagreed: not lost, but nothing is confirmed.
    const unconfirmed = !online && conn.phase !== "connecting" && conn.reason === "inconsistent";
    const lost = !online && (conn.phase === "lost" || unconfirmed);
    const disabled = !online || busy;
    const overlay = shown ? overlayLink(shown.overlay_url) : null;
    const chatLive = !!shown && shown.mode !== "off_air" && Date.now() - lastSeqMove.current.at < CHAT_QUIET_MS;
    const showAlerts = online && alerts ? alertDraft || alerts : null;
    const recentList = online && Array.isArray(recent) ? recent : null;
    const alertFields = ["enabled", "thank_you", "include_messages"];
    const rows = receipts.map(receiptLine).filter(Boolean);
    const guardMore = guardLimit.signature === guardSignature ? guardRows.length - guardLimit.visible : 0;
    const withheldCount = guardRows.filter((entry) => entry.kind === "line").length;
    const skippedCount = guardRows.length - withheldCount;
    const button = (text, fn, options) => h("button", Object.assign({ type: "button", onClick: fn }, options || {}), text);
    const command = (value) => commandView(value, copied, copyText);
    function modeButton(mode, label) {
      const selectedMode = !!shown && shown.mode === mode;
      const reason = !shown ? "Show is not connected." : busy ? "Another action is in progress." :
        selectedMode ? "This is the current mode." : shown.mode === "off_air" && mode !== "approval"
          ? "Enter approval mode first." : mode === "autonomous" && shown.mode !== "approval"
          ? "Enter approval mode before switching to autonomous." : "";
      return h("span", { key: mode, className: "onair-mode-option", title: reason || undefined,
        tabIndex: reason ? 0 : undefined, "aria-label": reason ? label + ": " + reason : undefined },
        button(label, () => requestMode(mode), { disabled: !!reason, "aria-pressed": selectedMode }));
    }

    const preflight = preflightView(shown);
    const preflightBlocked = !!preflight.reason || preflight.failing.length > 0;
    // The clear exists only for the runtime latch, and the controller accepts it only off air.
    const canClearLatch = preflight.latched && shown.mode === "off_air";
    const latchRule = preflight.latched ? preflight.reason.slice("runtime_refused:".length) : "";
    const latchHelp = Object.prototype.hasOwnProperty.call(RUNTIME_HELP, latchRule) ? RUNTIME_HELP[latchRule] : null;
    const preflightPanel = preflightBlocked && h("section", { className: "onair-preflight", role: "alert", "aria-labelledby": "onair-preflight" },
      h("h2", { id: "onair-preflight" }, preflight.latched ? "Runtime gate latched · arming refused" : "Preflight failed · arming refused"),
      preflight.reason && h("p", null, "Reason: ", h("code", null, preflight.reason)),
      preflight.latched && h("p", { className: "onair-latch-rule" },
        h("strong", null, "What happened (" + humanLabel(latchRule) + "): "),
        latchHelp ? latchHelp.what + " The show was cut to OFF AIR." : "An unrecognised refusal rule. The show was cut to OFF AIR; check the stage gateway's log."),
      preflight.failing.length > 0 && h("ul", { className: "onair-preflight-checks", "aria-label": "Failing preflight checks" },
        preflight.failing.map((check, i) => {
          const name = typeof check.name === "string" && check.name ? check.name : "check";
          const fix = Object.prototype.hasOwnProperty.call(CHECK_FIX, name) ? CHECK_FIX[name] : "";
          return h("li", { key: i },
            h("code", null, name), " " + (typeof check.detail === "string" ? check.detail : ""),
            fix && h("span", { className: "onair-check-fix" }, "Fix: ", richText(fix)));
        })),
      h("h3", { className: "onair-recovery-title" }, "Next steps"),
      preflight.latched
        ? h("ol", { className: "onair-steps" },
          h("li", null, "Fix the cause: ", richText(latchHelp ? latchHelp.fix : "find the refusal in the stage gateway's log.")),
          h("li", null, shown.mode === "off_air"
            ? "Clear the latch below. The clear is recorded in receipts and does not arm the show."
            : "Go off air (Show state, Off air): the clear works only off air."),
          h("li", null, "Choose Approval to arm again. Preflight runs again and refuses if anything still fails."))
        : h("ol", { className: "onair-steps" },
          h("li", null, "Fix each failing check in the stage profile (config.yaml or .env)."),
          h("li", null, "Re-check from a terminal:", command("hermes -p stage onair preflight")),
          h("li", null, "Restart the stage gateway so it loads the change (Ctrl+C in its terminal, then):", command("hermes -p stage gateway run")),
          h("li", null, "Choose Approval to arm. The show runs preflight again and refuses if anything still fails.")),
      preflight.latched && (canClearLatch
        ? h("div", { className: "onair-preflight-actions" },
          button("Clear runtime latch", () => action("runtime/clear", {}, false), { disabled: disabled, className: "onair-clear-latch" }),
          h("p", { className: "onair-help" }, "Fix the cause first. Clearing lets the show arm again; arming still runs preflight."))
        : h("p", { className: "onair-help" }, "Go off air to clear the runtime latch.")));

    const bannerChips = [];
    if (shown) {
      bannerChips.push(practice
        ? h("span", { key: "live", className: "onair-chip onair-practice-chip", title: "A practice show on this computer. Nothing is broadcast." }, "PRACTICE · NOT BROADCAST")
        : h("span", { key: "live", className: "onair-chip " + (shown.live ? "onair-live" : ""), title: "The stage is armed (approval or autonomous). This is not proof of a Twitch broadcast; your broadcast runs in OBS or Twitch." }, shown.live ? "LIVE" : "NOT LIVE"));
      bannerChips.push(h("span", { key: "mode", className: "onair-chip" }, MODE_LABEL[shown.mode] || shown.mode));
      if (preflightBlocked) bannerChips.push(h("span", { key: "preflight", className: "onair-chip onair-warn" + (preflight.latched ? " onair-latch-badge" : "") }, preflight.latched ? "RUNTIME LATCHED" : "PREFLIGHT FAILED"));
      if (shown.privacy) bannerChips.push(h("span", { key: "privacy", className: "onair-chip onair-warn" }, "PRIVACY CUT ON"));
      if (shown.muted) bannerChips.push(h("span", { key: "muted", className: "onair-chip onair-warn" }, "MUTED"));
      if (shown.rundown) bannerChips.push(h("span", { key: "rundown", className: "onair-chip" }, "RUNDOWN: " + shown.rundown));
      if (shown.model_failure_paused) bannerChips.push(h("span", { key: "model-paused", className: "onair-chip onair-warn", role: "alert" }, "Paused: the model is not answering"));
      if (shown.model_status) bannerChips.push(h("span", { key: "model-status", className: "onair-chip onair-warn", role: "status" }, shown.model_status));
      if (shown.rundown_warning) bannerChips.push(h("span", { key: "rundown-warning", className: "onair-chip onair-warn", role: "alert" }, shown.rundown_warning));
      bannerChips.push(h("span", { key: "chat", className: "onair-chip", title: "Any show event in the last 15 seconds. Not proof of chat or of a broadcast." },
        chatLive ? "activity: observed" : "activity: quiet"));
    } else {
      bannerChips.push(h("span", { key: "off", className: "onair-chip onair-warn" },
        unconfirmed ? "STATE UNCONFIRMED" : lost ? "CONNECTION LOST" : conn.phase === "connecting" ? "CONNECTING" : "NOT CONNECTED"));
      if (lost && !unconfirmed) bannerChips.push(h("span", { key: "unknown", className: "onair-chip" }, "STATE UNKNOWN"));
    }

    const setupSteps = h("ol", { className: "onair-steps onair-setup-steps" }, SETUP_STEPS.map((step) =>
      h("li", { key: step.title }, h("span", { className: "onair-step-title" }, step.title),
        step.commands.map(command), h("p", { className: "onair-help" }, richText(step.help)))));
    const outputs = h("div", { className: "onair-outputs" }, h("h3", null, "Captions only, voice or OBS?"),
      h("dl", null, OUTPUT_HELP.map(([term, text]) => h(React.Fragment, { key: term }, h("dt", null, term), h("dd", null, richText(text))))));
    const connectPanel = !practice && !online && conn.phase !== "connecting" && h("section", { className: "onair-panel onair-connect", "aria-labelledby": "onair-connect" },
      h("h2", { id: "onair-connect" }, unconfirmed ? "Show state unconfirmed" : lost ? "Connection to the show lost" : "Connect this desk to a show"),
      lost
        ? h(React.Fragment, null,
          h("p", { className: "onair-measure" }, conn.reason === "inconsistent"
            ? "The show answered, but its state and queue disagreed (it changed mid-refresh). "
            : "The show stopped answering this dashboard. ",
            "All show controls are disabled because nothing they send could be confirmed; Copy and the setup steps below still work. The desk checks again every second."),
          data && h("p", { className: "onair-stale" },
            h("strong", null, "Last confirmed " + secondsAgo(conn.lastOk) + " ", h("span", { className: "onair-nowrap" }, "(may have changed):"), " "),
            stateParts(data, practice).map((part, i) => h(React.Fragment, { key: i }, i ? " · " : "", h("span", { className: "onair-nowrap" }, part)))),
          h("p", { className: "onair-measure" }, "If the show may still be on air, cut it at the source: Ctrl+C in the stage gateway's terminal stops the show, or hide the overlay source in OBS. To check whether the stage is still running:"),
          command("hermes -p stage onair status"),
          h("details", { className: "onair-setup-details" }, h("summary", null, "Setup steps and outputs"),
            h("div", { className: "onair-connect-body" }, setupSteps, outputs)))
        : h(React.Fragment, null,
          h("p", { className: "onair-measure" }, "No stage is connected, so nothing is being read or shown. New here? Set up a stage with the guided wizard, or try a practice show first (no account needed). This desk checks again every second; nothing is sent until a show answers."),
          (props && (props.onSetup || props.onPractice)) && h("div", { className: "onair-connect-actions" },
            props.onSetup && button("Open the setup wizard", props.onSetup, { className: "onair-primary" }),
            props.onPractice && button("Try a practice show", props.onPractice)),
          h("details", { className: "onair-setup-details" }, h("summary", null, "Already have a stage? Manual commands and outputs"),
            h("div", { className: "onair-connect-body" }, setupSteps, outputs))));

    const defaultNotice = online ? "Review every line before approval. Privacy, mute and stop always fire instantly (P · M · Esc)."
      : conn.phase === "connecting" ? "Connecting to the desk…"
        : unconfirmed ? "Show state unconfirmed. All show controls are disabled until the show answers consistently."
        : lost ? "Connection lost. All show controls are disabled until the show answers again."
          : "Not connected to a show yet. Follow the steps below.";

    const plain = plainStatus(shown, online, practice, queue.length, preflightBlocked, lost);
    return h("main", { className: "onair-desk" + (practice ? " onair-practice-desk" : "") },
      shown && shown.privacy && h("div", { className: "onair-privacy-banner", role: "status" }, PRIVACY_BANNER),
      h("header", { className: "onair-header" },
        h("div", null, h("p", { className: "onair-eyebrow" }, practice ? "PRACTICE DESK · NOTHING LEAVES THIS COMPUTER" : "OPERATOR DESK"), h("h1", null, practice ? "Practice show" : "On Air")),
        h("div", { className: "onair-banner", role: "status" }, bannerChips),
        preflightPanel),
      h("section", { className: "onair-plain", "aria-label": "What is happening now" },
        h("ul", { className: "onair-plain-facts" }, plain.facts.map((fact) => h("li", { key: fact.label, className: "onair-fact onair-fact-" + fact.tone },
          h("span", { className: "onair-fact-label" }, fact.label + ": "), h("strong", null, fact.value)))),
        h("p", { className: "onair-next" }, h("span", { className: "onair-next-label" }, "Next: "), plain.next)),
      // One grid area for the notice and the setup/lost guide, so a connected desk keeps its layout.
      h("div", { className: "onair-notice-area" },
        // The age ticks every poll, so it stays outside the live region (no repeated announcements).
        h("p", { className: "onair-notice" },
          h("span", { role: "status", "aria-live": "polite" }, notice ? "Last action: " + notice : defaultNotice),
          notice && h("span", { className: "onair-notice-age", "aria-hidden": "true" }, " (" + secondsAgo(noticeAt.current.at) + ")")),
        connectPanel),

      h("section", { className: "onair-panel onair-safety", "aria-labelledby": "onair-safety" },
        h("h2", { id: "onair-safety" }, "Safety controls"),
        !online && h("p", { className: "onair-help" }, lost
          ? (unconfirmed ? "Disabled: the show's state could not be confirmed" : "Disabled: the show is not answering") +
            ", so a cut from here could not be confirmed. Cut at the source instead (see above)."
          : "Available once a show is connected. Privacy, mute and stop then fire instantly, even while the model is busy."),
        h("div", { className: "onair-safety-actions" },
          h("div", null, button(shown && shown.privacy ? "Uncover (P)" : "Privacy cut (P)", () => safetyKey("privacy"),
            { disabled: !online, className: "onair-privacy-button", "aria-pressed": !!(shown && shown.privacy) }),
            h("p", { className: "onair-help" }, shown && shown.privacy
              ? "Privacy is active: viewers see 'Be right back', audio is blocked and pending work is cleared. Press P to uncover."
              : "Covers the show ('Be right back'), blocks audio and clears pending work. Press P again to uncover.")),
          h("div", null, button(shown && shown.muted ? "Unmute (M)" : "Mute all (M)", () => safetyKey("mute"),
            { disabled: !online, "aria-pressed": !!(shown && shown.muted) }),
            h("p", { className: "onair-help" }, "Mutes audio; captions continue. Press M again to unmute.")),
          h("div", null, button("Stop all (Esc)", () => safetyKey("stop"), { disabled: !online, className: "onair-stop" }),
            h("p", { className: "onair-help" }, "Cancels speech and pending lines and pauses a live show. Return to approval to resume.")))),

      !practice && h("details", {className:"onair-views-disclosure"},
        h("summary", null, "Views and window sharing"), h(Views)),
      !practice && data && data.host_turns && data.host_turns.enabled === true && h("details", {className:"onair-talk-disclosure"},
        h("summary", null, "Talk to the co-host"), h(HostLine, {data, online, fence:hostFence})),
      h("div", { className: "onair-grid" },
        h("section", { className: "onair-panel", "aria-labelledby": "onair-mode" },
          h("h2", { id: "onair-mode" }, "Show state"),
          h("p", null, "Mode: ", h("strong", null, shown ? MODE_LABEL[shown.mode] || shown.mode : "Unknown")),
          h("p", null, shown
            ? privacyLine(shown) + " · " + audioLine(shown) + (typeof shown.pending === "number" ? " · " + shown.pending + " pending" : "")
            : "Status unavailable — not connected."),
          h("div", { className: "onair-modes", role: "group", "aria-label": "Show mode" },
            modeButton("off_air", "Off air"), modeButton("paused", "Paused"),
            modeButton("approval", "Approval"), modeButton("autonomous", "Autonomous")),
          h("dl", { className: "onair-mode-help" }, MODE_HELP.map(([term, text]) =>
            h(React.Fragment, { key: term }, h("dt", null, term), h("dd", null, text)))),
          overlay
            ? h("p", { className: "onair-overlay" }, "OBS overlay: ", h("a", { href: overlay, target: "_blank", rel: "noreferrer noopener" }, overlay),
              " ", button(copied === overlay ? "Copied" : "Copy", (event) => copyText(overlay, event),
                { className: "onair-copy", "aria-label": (copied === overlay ? "Copied" : "Copy") + " overlay address" }))
            : h("p", { className: "onair-help" }, shown
              ? "The show did not report an overlay address."
              : "The OBS overlay address appears here once a show is connected. OBS is optional.")),

        h("section", { className: "onair-panel", "aria-labelledby": "onair-queue" },
          h("h2", { id: "onair-queue" }, "Approval queue ", h("span", { className: "onair-count" }, online ? queue.length + " waiting" : "—")),
          h("p", { className: "onair-help onair-approve-explain" }, h("strong", null, "Approve"), " = put this exact line on the overlay (spoken only if voice is on). ", h("strong", null, "Reject"), " = drop it; nothing is shown."),
          h("p", { className: "onair-help" }, "Keys: A approve · R reject · ArrowUp/ArrowDown (or J/K) select."),
          !online
            ? h("p", null, "Queue unavailable — not connected. Nothing can be approved until a show answers.")
            : !queue.length
              ? h("p", null, "No lines waiting.")
              : h(React.Fragment, null,
                queue.length > 5 && queueMore > 0 && h("p", { className: "onair-help onair-scroll-hint" }, queueMore + " more waiting — scroll to review ↓"),
                h("ol", { ref: queueList, className: "onair-list" + (queue.length > 5 ? " onair-queue-scroll" : ""), tabIndex: queue.length > 5 ? 0 : undefined, "aria-label": "Approval queue" }, queue.map((line, i) =>
                h("li", { key: line.line_id, className: "onair-line" + (selected === i ? " onair-selected" : ""), onClick: () => setSelected(i) },
                  selected === i && h("p", { className: "onair-selection-hint" }, "Selected · A approve · R reject"),
                  h("p", {className:"onair-help onair-line-source"}, ["host","chat","beat","support"].includes(line.source) ? ({host:"Host turn",chat:"Chat turn",beat:"Beat turn",support:"Support turn"})[line.source] : "Source unknown"),
                  h("p", { className: "onair-line-text" }, line.text),
                  assistChip(line.assist),
                  h("div", { className: "onair-actions" },
                    button("Approve", () => decide("approve", line), { className: "onair-approve", disabled: disabled || line.epoch !== shown.epoch, "aria-label": "Approve line " + (i + 1) }),
                    button("Reject", () => decide("reject", line), { disabled: disabled || line.epoch !== shown.epoch, "aria-label": "Reject line " + (i + 1) }))))))),

        h("section", { className: "onair-panel", "aria-labelledby": "onair-guard" },
          h("h2", { id: "onair-guard" }, "The guard at work"),
          receiptsAvailable && h("p", { className: "onair-help" }, "In the latest 60 receipts: " + withheldCount + (withheldCount === 1 ? " line" : " lines") + " withheld · " + skippedCount + " chat skipped."),
          !receiptsAvailable ? h("p", null, "Receipts unavailable.") : guardRows.length
            ? h(React.Fragment, null,
              h("ul", { ref: guardList, className: "onair-events onair-guard-list", "aria-label": "Guard receipts" }, (showAllGuard || guardLimit.signature !== guardSignature ? guardRows : guardRows.slice(0, guardLimit.visible)).map(guardEntry)),
              guardMore > 0 && button(showAllGuard ? "Show fewer" : "Show all (" + guardMore + " more)", () => setShowAllGuard(!showAllGuard), { className: "onair-guard-more", "aria-expanded": showAllGuard }))
            : h("p", null, "No withheld output or skipped input in this window.")),


      h("details", { className: "onair-advanced" },
        h("summary", null, "Advanced: support alerts, receipts and optional assistance"),
      h("div", { className: "onair-grid" },
        h("section", { className: "onair-panel onair-assist", "aria-labelledby": "onair-assist" },
          h("h2", { id: "onair-assist" }, "Semantic assistance ", h("span", { className: "onair-count" },
            assist ? (ASSIST_STATES[assist.state] || humanLabel(assist.state)) : "—")),
          h("p", { className: "onair-help onair-assist-note" }, "Shadow only: recommendations never approve, reject or air anything. Only your Approve sends a line."),
          !assist
            ? h("p", null, online ? "Assistance status unavailable." : "Assistance status unavailable — not connected.")
            : h(React.Fragment, null,
              h("p", null, h("strong", null, "Provider: "), ASSIST_PROVIDERS[assist.provider] || humanLabel(assist.provider)),
              typeof assist.endpoint === "string" && assist.endpoint && h("p", null, h("strong", null, "Endpoint: "), h("code", null, assist.endpoint)),
              typeof assist.detail === "string" && assist.detail && h("p", { className: "onair-help" }, assist.detail),
              assist.provider !== "off" && h("p", null, assistTestLine(assist.last_test)),
              assist.counters && typeof assist.counters.requested === "number" && h("p", { className: "onair-help" },
                "This show: " + assist.counters.requested + " checked · " + (assist.counters.attached || 0) + " shown · " +
                (assist.counters.unavailable || 0) + " unavailable · " + (assist.counters.stale_discarded || 0) + " discarded as stale."),
              assist.provider !== "off" && button("Test connection", () => action("assist/test", {}, false), { disabled: disabled, className: "onair-assist-test" }),
              assist.provider === "off" && h("p", { className: "onair-help" }, richText("To try it: set `semantic_assist` in the On Air plugin settings to provider `laya` with a loopback `endpoint`, or `jev` with `ONAIR_JEV_API_KEY` in Hermes secrets, then restart the show.")))),

        h("section", { className: "onair-panel", "aria-labelledby": "onair-alerts" },
          h("h2", { id: "onair-alerts" }, "Support alerts"),
          !showAlerts
            ? h("p", null, online ? "Alert settings unavailable." : "Alert settings unavailable — not connected.")
            : h(React.Fragment, null,
              h("p", { className: "onair-help" }, "Alert cards show immediately (names are filtered); thank-you lines still need your approval."),
              alertFields.filter((name) => typeof showAlerts[name] === "boolean").map((name) =>
                h("label", { className: "onair-check", key: name, title: name === "include_messages"
                  ? "Warning: viewer-written text from support events would reach the agent's turn. Off by default."
                  : undefined },
                  h("input", {
                    type: "checkbox", checked: showAlerts[name], disabled: disabled,
                    onChange: (event) => setAlertDraft(Object.assign({}, showAlerts, { [name]: event.target.checked })),
                  }),
                  h("span", null, ({ enabled: "Alerts on", thank_you: "Thank-you lines", include_messages: "Include viewer messages" })[name],
                    name === "include_messages" && h("span", { className: "onair-inline-warning" }, " Warning: viewer-written text reaches the agent; off by default.")))),
              "min_bits" in showAlerts && h("label", { className: "onair-field" }, "Minimum bits",
                h("input", {
                  type: "number", min: 1, step: 1, value: showAlerts.min_bits, disabled: disabled,
                  onChange: (event) => setAlertDraft(Object.assign({}, showAlerts, { min_bits: event.target.value === "" ? "" : Number(event.target.value) })),
                })),
              alertDraft && button("Save alert settings", () => {
                const payload = {};
                ["enabled", "thank_you", "include_messages", "min_bits", "during_privacy"].forEach((key) => {
                  if (key in alertDraft) payload[key] = alertDraft[key];
                });
                setAlertDraft(null);
                action("alerts", payload, false);
              }, { disabled: disabled || !Number.isSafeInteger(alertDraft.min_bits) || alertDraft.min_bits < 1 })),
          h("h3", null, "Recent alerts"),
          !recentList
            ? h("p", null, online ? "Recent alerts unavailable." : "Recent alerts unavailable — not connected.")
            : recentList.length
            ? h(React.Fragment, null,
              h("ul", { className: "onair-events" }, recentList.slice().reverse().slice(0, showMoreAlerts ? recentList.length : 3).map((entry, i) => h("li", { key: i, className: "onair-recent-alert" }, alertLine(entry)))),
              recentList.length > 3 && button(showMoreAlerts ? "Show fewer" : "Show more (" + (recentList.length - 3) + ")", () => setShowMoreAlerts(!showMoreAlerts), { className: "onair-alerts-more", "aria-expanded": showMoreAlerts }))
            : h("p", null, "No recent alerts.")),

        h("section", { className: "onair-panel", "aria-labelledby": "onair-receipts" },
          h("h2", { id: "onair-receipts" }, "Receipts"),
          !receiptsAvailable ? h("p", null, "Receipts unavailable.") : rows.length
            ? h("ul", { className: "onair-events" }, rows.slice(0, 25).map((row, i) =>
              h("li", { key: i }, h("span", { className: "onair-tag" }, row.tag), " " + row.text)))
            : h("p", null, "No receipts yet."))))),
      h("p", { className: "onair-sr onair-copy-status", role: "status", "aria-live": "polite" }, copyStatus));
  }

  /* ---------------- First-run wizard, guided practice and help ---------------- */
  const SETUP_KEY = "onair.setup.v1";
  const TOUR_KEY = "onair.tour.v1";
  const TAB_KEY = "onair.tab.v1";
  function load(key, fallback) {
    try { const v = JSON.parse(window.localStorage.getItem(key)); return v && typeof v === "object" ? v : fallback; } catch (e) { return fallback; }
  }
  function save(key, value) { try { window.localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ } }
  // Wizard/setup calls report a structured refusal (422) as data, not as an exception.
  function ask(path, body) {
    return SDK.fetchJSON(BASE + path, body === undefined ? undefined : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).catch((error) => {
      const detail = error && (error.body || error.data || error.detail);
      if (detail && typeof detail === "object") return detail;
      try { const parsed = JSON.parse(String(error && error.message || "").replace(/^[^{]*/, "")); if (parsed && typeof parsed === "object") return parsed; } catch (e) { /* not JSON */ }
      return { ok: false, error: "The dashboard could not reach On Air's setup service." };
    });
  }

  const WIZ_STEPS = ["Welcome", "Stage folder", "Channel and co-host", "Brain and output", "Review", "Check and finish"];
  const STATE_LABEL = { done: "Done", action: "Needs action", untested: "Not tested" };

  /* One wizard command, rendered by the desk's own commandView (exact → Copy; template → marked
   * "Fill in", never copyable). Commands wrap at spaces (style.css), never mid-word. */
  function CommandLine(props) {
    const [copied, setCopied] = useState("");
    const [failed, setFailed] = useState(false);
    const alive = useRef(true);
    useEffect(() => () => { alive.current = false; }, []);
    async function copy(value, event) {
      const row = event && event.currentTarget ? event.currentTarget.parentNode : null;
      try {
        if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") throw new Error("no clipboard");
        await navigator.clipboard.writeText(value);
        if (!alive.current) return;
        setFailed(false); setCopied(value);
        window.setTimeout(() => { if (alive.current) setCopied((v) => (v === value ? "" : v)); }, 2000);
      } catch (_) {
        if (!alive.current) return;
        setFailed(true);
        try { const k = row && row.querySelector(".onair-cmd"); if (k) window.getSelection().selectAllChildren(k); } catch (e) { /* selecting is a convenience only */ }
      }
    }
    return h(React.Fragment, null, commandView(props.template ? { template: props.template } : props.value, copied, copy),
      failed && h("p", { className: "onair-help onair-copy-failed", role: "status" }, "Copy failed: the command is selected, copy it manually."));
  }

  function Field(props) {
    const id = "onair-wiz-" + props.name;
    const err = props.errors && props.errors[props.name];
    return h("div", { className: "onair-wiz-field" },
      h("label", { htmlFor: id }, props.label),
      props.hint && h("p", { className: "onair-help", id: id + "-hint" }, props.hint),
      h(props.multiline ? "textarea" : "input", {
        id, value: props.value || "", placeholder: props.placeholder || "", maxLength: props.max || 200,
        rows: props.multiline ? 2 : undefined, "aria-invalid": err ? "true" : "false",
        "aria-describedby": (props.hint ? id + "-hint " : "") + (err ? id + "-err" : ""),
        onChange: (event) => props.onChange(props.name, event.target.value),
      }),
      err && h("p", { className: "onair-field-error", id: id + "-err", role: "alert" }, err));
  }

  function Wizard(props) {
    const saved = load(SETUP_KEY, {});
    const [step, setStep] = useState(Number.isInteger(saved.step) ? Math.min(saved.step, 4) : 0);
    const [answers, setAnswers] = useState(Object.assign({ target: "", channel: "", host_name: "", agent_name: "", topics: "", voice: "captions" }, saved.answers || {}));
    const [errors, setErrors] = useState({});
    const [busy, setBusy] = useState(false);
    const [planned, setPlanned] = useState(null);
    const [confirm, setConfirm] = useState(false);
    const [result, setResult] = useState(saved.home ? { home: saved.home } : null);
    const [message, setMessage] = useState("");
    const heading = useRef(null);
    useEffect(() => { if (!answers.target && props.status && props.status.suggested_target) setAnswers((a) => Object.assign({}, a, { target: a.target || props.status.suggested_target })); }, [props.status]);
    useEffect(() => { save(SETUP_KEY, { step, answers, home: result && result.home }); }, [step, answers, result]);
    useEffect(() => { if (heading.current) heading.current.focus(); }, [step]);
    useEffect(() => {
      if (step === 5 && result && result.home && !result.readiness) recheck();
    }, [step]);
    const set = (name, value) => { setAnswers((a) => Object.assign({}, a, { [name]: value })); setErrors((e) => Object.assign({}, e, { [name]: undefined })); setPlanned(null); setConfirm(false); };
    const fieldsFor = { 1: ["target"], 2: ["channel", "host_name", "agent_name", "topics"], 3: ["voice"], 4: [] };
    async function next() {
      if (step >= 1 && step <= 3) {
        setBusy(true);
        const r = await ask("setup/plan", { answers });
        setBusy(false);
        const errs = (r && r.errors) || {};
        const mine = {};
        fieldsFor[step].forEach((k) => { if (errs[k]) mine[k] = errs[k]; });
        if (!r || (r.ok !== true && !r.errors)) { setMessage((r && r.error) || "Could not check your answers."); return; }
        if (Object.keys(mine).length) { setErrors(mine); setMessage("Fix the highlighted answer to continue."); return; }
        setErrors({}); setMessage("");
        if (step === 3) {
          if (r.ok !== true) { setErrors(errs); setMessage("Some answers need fixing: use Back to edit them."); }
          setPlanned(r.ok ? r : null);
        }
      }
      setStep(step + 1);
    }
    async function apply() {
      setBusy(true); setMessage("Creating the stage folder…");
      const r = await ask("setup/apply", { answers, confirm: confirm === true });
      setBusy(false);
      if (r && r.ok === true) { setResult(r); setMessage("Settings saved and re-read from disk."); setStep(5); return; }
      setErrors((r && r.errors) || {}); setMessage((r && r.errors && (r.errors.target || r.errors.confirm || r.errors.form)) || (r && r.error) || "Nothing was changed.");
    }
    async function recheck() {
      if (!result || !result.home) return;
      setBusy(true);
      const r = await ask("setup/readiness", { home: result.home });
      setBusy(false);
      if (r && r.ok === true) { setResult(Object.assign({}, result, { readiness: r })); setMessage("Re-checked just now."); }
      else setMessage((r && r.error) || "Could not re-check the stage folder.");
    }
    async function reviewPlan() {
      setBusy(true);
      const r = await ask("setup/plan", { answers });
      setBusy(false);
      if (r && r.ok === true) { setPlanned(r); setErrors({}); } else { setPlanned(null); setErrors((r && r.errors) || {}); setMessage("Some answers need fixing: use Back to edit them."); }
    }
    useEffect(() => { if (step === 4 && !planned) reviewPlan(); }, [step]);

    const nav = h("div", { className: "onair-wiz-nav" },
      step > 0 && step < 5 && h("button", { type: "button", onClick: () => setStep(step - 1), disabled: busy }, "Back"),
      step >= 1 && step <= 3 && h("button", { type: "button", className: "onair-primary", onClick: next, disabled: busy }, "Next"),
      step === 4 && h("button", { type: "button", className: "onair-primary", onClick: apply, disabled: busy || !planned || !confirm }, "Create stage folder"));
    let body;
    if (step === 0) {
      body = h(React.Fragment, null,
        h("p", { className: "onair-measure" }, "On Air lets an AI co-host answer your Twitch chat on screen. You stay in charge: nothing appears until you approve it."),
        h("div", { className: "onair-choice-grid" },
          h("div", { className: "onair-choice" },
            h("h3", null, "Try a practice show"),
            h("p", null, "Scripted chat and ready-made replies on this computer. No account, API key, model, Twitch, voice or broadcast. Takes about 2 minutes."),
            h("button", { type: "button", className: "onair-primary", onClick: props.onPractice }, "Try a practice show")),
          h("div", { className: "onair-choice" },
            h("h3", null, "Set up my Twitch co-host"),
            h("p", null, "A few questions, then On Air creates a separate stage folder for your show. Nothing is started or broadcast."),
            h("button", { type: "button", onClick: () => setStep(1) }, "Set up my Twitch co-host"))));
    } else if (step === 1) {
      body = h(React.Fragment, null,
        h("p", { className: "onair-measure" }, "The stage runs in a separate Hermes home with its own settings. This is separation, not an operating-system sandbox."),
        h(Field, { name: "target", label: "New stage folder", value: answers.target, errors, onChange: set, max: 400,
          hint: "A folder that does not exist yet, outside your Hermes folder. On Air creates it; it never overwrites or reuses an existing folder." }),
        h("p", { className: "onair-help" }, "Nothing is created until you confirm on the Review step."));
    } else if (step === 2) {
      body = h(React.Fragment, null,
        h(Field, { name: "channel", label: "Twitch channel to read", value: answers.channel, placeholder: "my_channel", errors, onChange: set, max: 30,
          hint: "Just the name from twitch.tv/your_name. Chat is read anonymously and read-only: no Twitch login needed." }),
        h(Field, { name: "host_name", label: "Your name (the human streamer)", value: answers.host_name, placeholder: "Sam", errors, onChange: set, max: 60,
          hint: "Protected host name." }),
        h(Field, { name: "agent_name", label: "Co-host name (the AI)", value: answers.agent_name, placeholder: "Pixel", errors, onChange: set, max: 60 }),
        h(Field, { name: "topics", label: "Show topics (optional, public)", value: answers.topics, placeholder: "retro games, building a desk lamp", errors, onChange: set, multiline: true,
          hint: "Goes into the co-host's public persona. Never put private notes here." }));
    } else if (step === 3) {
      body = h(React.Fragment, null,
        h("div", { className: "onair-wiz-status" }, h("span", { className: "onair-state onair-state-action" }, "Needs action later"),
          h("p", null, h("strong", null, "Co-host brain (model): not chosen yet. "), "The new stage gets its own model after this wizard; it never borrows your personal agent's model or keys. Practice needs no model.")),
        h("fieldset", { className: "onair-wiz-radio" }, h("legend", null, "What should the co-host output?"),
          h("label", null, h("input", { type: "radio", name: "onair-voice", checked: answers.voice !== "tts", onChange: () => set("voice", "captions") }),
            h("span", null, h("strong", null, "Captions only (recommended)"), " · text on your overlay, nothing spoken.")),
          h("label", null, h("input", { type: "radio", name: "onair-voice", checked: answers.voice === "tts", onChange: () => set("voice", "tts") }),
            h("span", null, h("strong", null, "Captions and voice"), " · needs text-to-speech set up and tested for the stage; marked as needing action until then."))),
        h("p", { className: "onair-help" }, h("strong", null, "Approval: always on for your first show. "), "Every line waits for you to press Approve."),
        h("details", { className: "onair-wiz-advanced" }, h("summary", null, "Advanced (optional)"),
          h("p", { className: "onair-help" }, "Semantic assistance (Laya or Jev) stays OFF. It can only add advice beside a draft and never approves anything. You can turn it on later in the stage's plugin settings.")));
    } else if (step === 4) {
      body = !planned
        ? h("p", null, busy ? "Checking your answers…" : (Object.values(errors).filter(Boolean)[0] || "Checking…"))
        : h(React.Fragment, null,
          h("p", null, "On Air will create exactly this folder:"), h("p", null, h("code", { className: "onair-path" }, planned.target)),
          h("ul", { className: "onair-review-list" }, planned.summary.map((line) => h("li", { key: line }, line))),
          h("p", { className: "onair-help" }, planned.untouched.join(" ")),
          h("details", null, h("summary", null, "Files and generated settings"),
            h("ul", null, planned.creates.map((f) => h("li", { key: f }, h("code", { className: "onair-path" }, f)))),
            h("pre", { className: "onair-config-preview" }, planned.config_preview)),
          h("label", { className: "onair-check onair-confirm" }, h("input", { type: "checkbox", checked: confirm, onChange: (e) => setConfirm(e.target.checked) }),
            h("span", null, "Create this new folder with these settings. Nothing is started, installed or broadcast.")),
          errors.target && h("p", { className: "onair-field-error", role: "alert" }, errors.target));
    } else {
      const ready = result && result.readiness;
      const left = ready && Array.isArray(ready.steps) ? ready.steps : [];
      body = !ready ? h("p", null, busy ? "Reading the stage folder back…" : "No stage folder created yet.")
        : h(React.Fragment, null,
          h("p", { className: "onair-ready-summary" }, ready.summary),
          h("p", null, "Stage folder: ", h("code", { className: "onair-path" }, ready.home)),
          h("h3", { id: "onair-wiz-checks" }, "What is set up"),
          h("ul", { className: "onair-ready-rows", "aria-labelledby": "onair-wiz-checks" }, ready.rows.map((row) =>
            h("li", { key: row.key, className: "onair-ready-row" },
              h("span", { className: "onair-state onair-state-" + row.state }, STATE_LABEL[row.state] || row.state),
              h("div", null, h("strong", null, row.label), h("p", { className: "onair-help" }, row.detail),
                Number.isInteger(row.step) && h("p", { className: "onair-ready-action" }, "To do: step " + row.step + " below."))))),
          left.length > 0 && h("h3", { id: "onair-wiz-left" }, "What is left, in this order"),
          left.length > 0 && h("p", { className: "onair-help onair-measure" }, "Run each command in a terminal on this computer. Practice works now, without any of them. " + (ready.command_note || "")),
          left.length > 0 && h("ol", { className: "onair-steps onair-ready-steps", "aria-labelledby": "onair-wiz-left" }, left.map((s) =>
            h("li", { key: s.key, className: "onair-ready-step" },
              h("span", { className: "onair-step-title" }, s.title),
              s.template ? h(CommandLine, { template: s.template }) : h(CommandLine, { value: s.command }),
              h("p", { className: "onair-help" }, s.note)))),
          h("div", { className: "onair-wiz-nav" },
            h("button", { type: "button", className: "onair-primary", onClick: props.onPractice }, "Practise now"),
            h("button", { type: "button", onClick: recheck, disabled: busy }, "Re-check"),
            h("button", { type: "button", onClick: props.onDesk }, "Go to the desk"),
            h("button", { type: "button", onClick: () => { setResult(null); setPlanned(null); setConfirm(false); setStep(1); } }, "Set up another stage")));
    }
    return h("section", { className: "onair-wizard onair-panel", "aria-labelledby": "onair-wiz-title" },
      h("ol", { className: "onair-wiz-progress", "aria-label": "Setup steps" }, WIZ_STEPS.map((name, i) =>
        h("li", { key: name, className: i === step ? "onair-current" : i < step ? "onair-done" : "", "aria-current": i === step ? "step" : undefined }, h("span", null, i + 1), " " + name))),
      h("h2", { id: "onair-wiz-title", tabIndex: -1, ref: heading }, "Step " + (step + 1) + " of " + WIZ_STEPS.length + ": " + WIZ_STEPS[step]),
      body,
      message && h("p", { className: "onair-wiz-message", role: "status" }, message),
      nav);
  }

  const TOUR = [
    { title: "Start the practice show", text: "Press Start practice show. A pretend viewer asks a question and the co-host writes a draft for you to review.", done: (f) => f && (f.pending >= 1 || f.approved >= 1 || f.rejected >= 1 || f.captions >= 1) },
    { title: "Approve the draft", text: "Find the draft under Approval queue and press Approve. Approve = put this exact line on the overlay (it would be spoken only if voice were on; practice is captions only).", done: (f) => f && f.captions >= 1 },
    { title: "See it on the overlay", text: "Open the real practice overlay to see its current output. The last caption below is history and may have cleared.", done: (f) => f && f.captions >= 1 },
    { title: "Reject a draft that goes too far", text: "Press Bring in the next message. The co-host drafts a promise you never made. Press Reject: the line is dropped and nothing is shown.", done: (f) => f && f.rejected >= 1, next: true },
    { title: "Privacy cover", text: "Press Privacy cut (P). Viewers would see 'Be right back', audio is blocked and pending drafts are cleared. Then press Uncover (P).", done: (f) => f && f.privacy_on >= 1 && f.privacy_off >= 1 && !f.privacy },
    { title: "Stop all, then resume", text: "Press Stop all (Esc): it cancels everything pending and pauses the show. To resume, choose Approval under Show state.", done: (f) => f && f.stopped >= 1 && f.resumed },
    { title: "You did it", text: "You approved, rejected, covered and stopped a show, all in practice. Nothing left this computer.", done: () => true },
  ];

  function Practice(props) {
    const [facts, setFacts] = useState(null);
    const [running, setRunning] = useState(false);
    const [run, setRun] = useState(0);
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState("");
    const [tour, setTour] = useState(load(TOUR_KEY, { step: 0, skipped: false }));
    useEffect(() => { save(TOUR_KEY, tour); setNote(""); }, [tour.step, tour.skipped]);
    useEffect(() => {
      let live = true;
      async function poll() {
        const r = await ask("practice/facts");
        if (!live) return;
        setRunning(!!(r && r.running)); setFacts(r && r.running ? r.facts : null);
      }
      poll();
      const timer = setInterval(poll, 1000);
      return () => { live = false; clearInterval(timer); };
    }, [run]);
    async function start() {
      setBusy(true); setNote("Starting the practice show…");
      const r = await ask("practice/start", {});
      setBusy(false);
      if (r && r.ok === true) { setRun(run + 1); setNote("Practice show started. A practice viewer just asked a question."); }
      else setNote((r && r.error) || "The practice show could not start.");
    }
    async function end() {
      setBusy(true);
      await ask("practice/end", {});
      setBusy(false); setRun(run + 1); setNote("Practice show ended.");
    }
    async function bringNext() {
      setBusy(true);
      const r = await ask("practice/next", {});
      setBusy(false);
      setNote(r && r.ok === true ? "Practice viewer " + r.message.user + " asked: " + r.message.text : (r && r.error) || "No more practice messages.");
    }
    const step = TOUR[Math.min(tour.step, TOUR.length - 1)];
    const stepDone = step.done(facts);
    const lastCaption = facts && facts.last_caption ? facts.last_caption : "";
    return h("div", { className: "onair-practice" },
      h("section", { className: "onair-practice-banner", role: "note" },
        h("strong", null, "PRACTICE"), " · scripted chat and ready-made replies. No Twitch, no model, no voice, nothing is broadcast."),
      !tour.skipped && h("section", { className: "onair-tour onair-panel", "aria-labelledby": "onair-tour-title" },
        h("p", { className: "onair-eyebrow" }, "Tutorial · step " + (Math.min(tour.step, TOUR.length - 1) + 1) + " of " + TOUR.length),
        h("h2", { id: "onair-tour-title" }, step.title),
        h("p", { className: "onair-measure" }, step.text),
        h("p", { className: "onair-tour-status", role: "status", "aria-live": "polite" },
          tour.step >= TOUR.length - 1 ? "" : stepDone ? "✓ Done. Press Next." : "Waiting for you to do this step…"),
        h("div", { className: "onair-wiz-nav" },
          tour.step === 0 && !stepDone && running && h("button", { type: "button", className: "onair-primary", onClick: start, disabled: busy }, "Start a fresh practice show"),
          tour.step < TOUR.length - 1 && h("button", { type: "button", className: stepDone ? "onair-primary" : "", disabled: !stepDone, onClick: () => setTour({ step: tour.step + 1, skipped: false }) }, "Next"),
          tour.step >= TOUR.length - 1 && h(React.Fragment, null,
            h("button", { type: "button", className: "onair-primary", onClick: props.onSetup }, "Set up my Twitch co-host"),
            h("button", { type: "button", onClick: props.onDesk }, "Go to the real desk")),
          h("button", { type: "button", onClick: () => setTour({ step: tour.step, skipped: true }) }, "Skip tutorial"))),
      tour.skipped && h("p", { className: "onair-help" }, "Tutorial hidden. ", h("button", { type: "button", className: "onair-link", onClick: () => setTour({ step: 0, skipped: false }) }, "Replay tutorial")),
      h("div", { className: "onair-practice-tools" },
        !running ? h("button", { type: "button", className: "onair-primary", onClick: start, disabled: busy }, "Start practice show")
          : h(React.Fragment, null,
            h("button", { type: "button", onClick: bringNext, disabled: busy || !facts || facts.messages_sent >= facts.messages_total }, "Bring in the next message"),
            h("button", { type: "button", onClick: start, disabled: busy }, "Restart practice"),
            h("button", { type: "button", onClick: end, disabled: busy }, "End practice")),
        note && h("span", { className: "onair-help", role: "status" }, note)),
      running && h("section", { className: "onair-panel onair-overlay-preview", "aria-labelledby": "onair-preview-title" },
        h("h2", { id: "onair-preview-title" }, "Practice output history"),
        h("p", { className: "onair-help" }, facts && facts.privacy ? "Current overlay: privacy cover ('Be right back')." : facts && (facts.mode === "paused" || facts.mode === "off_air") ? "Current overlay: cleared." : "Current caption: open the practice overlay to check."),
        h("p", { className: "onair-help" }, "The last caption is history, not the current overlay. Open the practice overlay for its current output."),
        facts && facts.last_message && h("p", { className: "onair-help" }, "Last practice chat · ", h("strong", null, facts.last_message.user + ": "), facts.last_message.text),
        h("p", { className: "onair-help" }, "Last emitted caption:"),
        h("div", { className: "onair-preview-screen" },
          lastCaption ? h("p", { className: "onair-preview-caption" }, lastCaption) : h("p", { className: "onair-preview-empty" }, "No captions in practice history yet.")),
        facts && facts.overlay_url && h("p", { className: "onair-help" }, "Real practice overlay on this computer: ", h("a", { href: facts.overlay_url, target: "_blank", rel: "noreferrer noopener" }, "open overlay preview"))),
      running && h(Desk, { key: run, api: PRACTICE_API, practice: true }));
  }

  function Help(props) {
    const rows = [
      ["Approve (A)", "Puts this exact line on the overlay as a caption. It is spoken only if voice is set up and on."],
      ["Reject (R)", "Drops the draft. Nothing is shown or spoken."],
      ["Privacy cut (P)", "Covers the overlay with 'Be right back', blocks audio and clears pending drafts. Press again to uncover."],
      ["Mute all (M)", "Stops all audio. Captions continue. Press again to unmute."],
      ["Stop all (Esc)", "Cancels everything pending and pauses the show. Choose Approval under Show state to resume."],
      ["Approval / Paused / Off air", "Approval: the co-host drafts, you decide. Paused: nothing new is shown. Off air: chat is not read."],
    ];
    return h("section", { className: "onair-panel onair-help-tab", "aria-labelledby": "onair-help-title" },
      h("h2", { id: "onair-help-title" }, "What each control does"),
      h("dl", { className: "onair-help-list" }, rows.map(([term, text]) => h(React.Fragment, { key: term }, h("dt", null, term), h("dd", null, text)))),
      h("p", { className: "onair-measure" }, "You are the streamer; the co-host is the AI that drafts replies. On Air never starts streaming for you: your broadcast stays in OBS or Twitch."),
      h("div", { className: "onair-wiz-nav" },
        h("button", { type: "button", className: "onair-primary", onClick: props.onReplay }, "Replay tutorial"),
        h("button", { type: "button", onClick: props.onSetup }, "Open setup")));
  }


  /* ---------------- Quick start: the SAME setup engine the private agent's tools use ----------------
   * Primary entry is the user's own Hermes agent ("set up On Air"); this form is the optional
   * oversight/preference view. Both plan with setup/quick/plan and approve the exact digest.
   */
  const QS_TONES = [["friendly", "Friendly"], ["relaxed", "Relaxed"], ["upbeat", "Upbeat"], ["dry", "Dry"]];
  const QS_PACING = [["normal", "Normal"], ["less_often", "Less often"], ["rare", "Rarely"]];
  const QS_STEP = { waiting: "Waiting", running: "Working…", done: "Done", skipped: "Skipped", failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted" };
  function QuickStart(props) {
    const [st, setSt] = useState(null);
    const [channel, setChannel] = useState("");
    const [useModel, setUseModel] = useState(false);
    const [tone, setTone] = useState("");
    const [pacing, setPacing] = useState("");
    const [plan, setPlan] = useState(null);
    const [errors, setErrors] = useState({});
    const [job, setJob] = useState(null);
    const [preview, setPreview] = useState(null);
    const [pending, setPending] = useState([]);
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState("");
    const [stopUnconfirmed, setStopUnconfirmed] = useState(null);
    const alive = useRef(true);
    useEffect(() => () => { alive.current = false; }, []);
    async function refresh() {
      const r = await ask("setup/quick/status");
      if (!alive.current || !r || r.ok !== true) return;
      setSt(r); setJob(r.job); setPreview(r.preview);
      // The agent's exact persisted plan (name, topics, style, effects): render it and approve THAT digest.
      if (r.pending_plan && r.pending_plan.digest) setPlan((p) => p || { ...r.pending_plan, from_agent: true, approve_with: { chat: "/onair-setup approve " + r.pending_plan.code } });
      const style = r.saved_stage && r.saved_stage.style;
      if (style) { setTone((t) => t || style.tone); setPacing((p) => p || style.reply_pacing); }
    }
    async function poll() { const r = await ask("setup/quick/job"); if (alive.current && r && r.ok) { setJob(r.job); setPreview(r.preview); } }
    async function loadPending() { const r = await ask("setup/quick/preview/pending"); if (alive.current) setPending(r && Array.isArray(r.pending) ? r.pending : []); }
    useEffect(() => { refresh(); }, []);
    useEffect(() => {
      if (!job || (job.state !== "running" && job.state !== "cancelling")) return undefined;
      const t = window.setTimeout(poll, 400);
      return () => window.clearTimeout(t);
    }, [job]);
    useEffect(() => { if (preview && preview.running) loadPending(); else setPending([]); }, [preview && preview.running, preview && preview.pending, preview && preview.captions]);
    const saved = (st && st.saved_stage) || null;
    const savedStyle = (saved && saved.style) || {};
    const conn = (st && st.model_connection) || {};
    function answers() {
      const a = {};
      if (!saved && channel.trim()) a.channel = channel.trim();
      if (conn.usable_here) a.use_model = useModel;
      const style = {};
      if (tone && tone !== (savedStyle.tone || "friendly")) style.tone = tone;
      if (pacing && pacing !== (savedStyle.reply_pacing || "normal")) style.reply_pacing = pacing;
      if (Object.keys(style).length) a.style = style;
      return a;
    }
    async function review(event) {
      if (event) event.preventDefault();
      setBusy(true); setNote("");
      const r = await ask("setup/quick/plan", { answers: answers() });
      setBusy(false);
      if (r && r.ok === true) { setPlan(r); setErrors({}); return; }
      setPlan(null); setErrors((r && r.errors) || {});
      setNote(r && r.missing && r.missing.length ? "Still needed: " + r.missing.join(", ") + "." : (r && (r.error || (r.errors && Object.values(r.errors)[0]))) || "Fix the highlighted answer.");
    }
    async function approve() {
      setBusy(true);
      const r = await ask("setup/quick/apply", { digest: plan.digest, confirm: true });
      setBusy(false);
      if (r && r.job) setJob(r.job);
      if (!r || r.ok !== true) { setNote((r && r.error) || "Nothing was changed."); setPlan(r && r.plan ? r.plan : null); return; }
      setPlan(null); setNote("");
    }
    async function decide(kind, line) {
      setBusy(true);
      const r = await ask("setup/quick/preview/" + kind, { line_id: line.line_id, epoch: line.epoch });
      setBusy(false);
      setNote(r && r.ok !== false ? (kind === "approve" ? "Shown on the private viewer preview only." : "Skipped. Nothing was shown.") : (r && r.error) || "That did not work.");
      await poll(); loadPending();
    }
    async function pause() { setBusy(true); await ask("setup/quick/preview/privacy", { on: !(preview && preview.privacy) }); setBusy(false); poll(); }
    async function stop() { setBusy(true); const r = await ask("setup/quick/cancel", {}); setBusy(false); if (r && r.job) setJob(r.job); setStopUnconfirmed(r && r.ok === true ? null : stopNote(r)); await refresh(); setNote(stopNote(r)); }
    // The Stop result comes from the backend (cancel_all): "nothing is running" only when it confirmed a
    // complete stop; a draining/partial/unanswered stop says so, in the backend's own words, and asks to retry.
    function stopNote(r) {
      if (r && r.ok === true) return "Stopped. Nothing is running.";
      const why = (r && ((r.preview && r.preview.error) || r.error)) || "The page got no answer from the setup backend.";
      return (r && r.draining ? "Not stopped yet: still finishing. " : "Not confirmed stopped. ") + why + " Press Stop again.";
    }
    const running = job && (job.state === "running" || job.state === "cancelling");
    const live = preview && preview.running;
    const steps = job && Array.isArray(job.steps) ? job.steps : [];
    const failed = steps.find((s) => s.state === "failed");
    const style = (preview && preview.style_in_effect) || null;

    const agentNote = h("p", { className: "onair-help" }, "Easiest: ask your own Hermes agent to \u201cset up On Air\u201d. It uses this same setup, asks only what is missing, and you approve it. This page shows the same plan and state.");
    const flow = h("p", { className: "onair-measure onair-qs-flow" }, "How it works: a Twitch chat message \u2192 the co-host suggests a reply \u2192 you choose Show this reply or Skip \u2192 only then viewers see it.");

    // A stop the backend reports as still finishing (running:false, draining:true, quiescent:false), or ANY
    // Stop answer that was not a confirmed complete stop (partial, cancelling job, unanswered owner, no answer),
    // is NOT a stopped preview: keep saying so, in the backend's own words, with a working retry that asks again.
    // The private show is its own top-level section, always FIRST (same position in every branch, so it keeps
    // its state): its Stop show is the first stop control on the page, never inside the setup preview.
    const showFirst = saved ? h(ShowPanel, null) : null;
    if (!live && ((preview && (preview.draining || preview.quiescent === false)) || stopUnconfirmed)) {
      return h(React.Fragment, null, showFirst, h("section", { className: "onair-panel onair-qs", "aria-labelledby": "onair-qs-title" },
        h("h2", { id: "onair-qs-title", tabIndex: -1 }, "Stopping your private preview\u2026"),
        h("div", { className: "onair-qs-progress", role: "status", "aria-live": "polite" },
          h("h3", null, "Not confirmed stopped yet"),
          h("p", null, (preview && preview.error) || stopUnconfirmed || "The setup backend has not confirmed the stop yet."),
          h("button", { type: "button", className: "onair-danger", onClick: stop, disabled: busy }, "Stop again")),
        note && h("p", { className: "onair-help", role: "status" }, note)));
    }

    if (live) {
      const line = pending[0];
      return h(React.Fragment, null, showFirst, h("section", { className: "onair-panel onair-qs onair-qs-preview", "aria-labelledby": "onair-qs-title" },
        h("h2", { id: "onair-qs-title" }, "Your private preview"),
        h("p", { className: "onair-help" }, "This is the setup test, not the show. Its Pause and Stop control only this preview; a running show has its own Stop show above."),
        h("div", { className: "onair-qs-safety", role: "group", "aria-label": "Setup preview controls (not the show)" },
          h("span", { className: "onair-help" }, "Setup preview only:"),
          h("button", { type: "button", onClick: pause, disabled: busy }, preview.privacy ? "Unpause" : "Pause"),
          h("button", { type: "button", className: "onair-danger", onClick: stop, disabled: busy }, "Stop")),
        h("ol", { className: "onair-qs-causal" },
          h("li", null, h("h3", null, "Message"),
            h("span", { className: "onair-tag" }, "Private setup test \u2014 not Twitch"),
            preview.message ? h("p", null, h("strong", null, preview.message.user + ": "), preview.message.text) : h("p", null, "No message yet.")),
          h("li", null, h("h3", null, "Suggested reply"),
            h("span", { className: "onair-tag" }, preview.reply_source === "model" ? "Drafted by your model connection" : "Canned stand-in, not the model"),
            line ? h(React.Fragment, null, h("p", { className: "onair-qs-reply" }, line.text),
              h("div", { className: "onair-wiz-nav" },
                h("button", { type: "button", className: "onair-primary", onClick: () => decide("approve", line), disabled: busy || preview.privacy }, "Show this reply"),
                h("button", { type: "button", onClick: () => decide("reject", line), disabled: busy }, "Skip")))
              : h("p", null, preview.captions ? "Nothing waiting." : (preview.draft_error || "No suggested reply waiting."))),
          h("li", null, h("h3", null, "Viewer preview"),
            h("span", { className: "onair-tag" }, "Private: not your OBS or live overlay"),
            h("div", { className: "onair-preview-screen" + (preview.privacy ? " onair-preview-brb" : "") },
              preview.last_caption ? h("p", { className: "onair-preview-caption" }, preview.last_caption) : h("p", { className: "onair-preview-empty" }, "Nothing shown yet.")))),
        style && h("p", { className: "onair-help" }, "Style in use: tone " + style.tone + " \u00b7 reply pacing " + String(style.reply_pacing || "unknown").replace("_", " ") + " \u00b7 " + style.reply_length + " replies."),
        note && h("p", { className: "onair-help", role: "status" }, note),
        job && job.state === "done" && job.next_action && h("p", { className: "onair-help onair-qs-real-stage" }, job.next_action),
        h("p", { className: "onair-help" }, "Later, optional: voice (needs its own listening test) and OBS (copy the overlay URL into a browser source).")));
    }

    return h(React.Fragment, null, showFirst, h("section", { className: "onair-panel onair-qs", "aria-labelledby": "onair-qs-title" },
      h("h2", { id: "onair-qs-title", tabIndex: -1 }, saved ? "Welcome back" : "Start On Air"),
      agentNote, flow,
      (running || (job && job.state !== "done")) && steps.length > 0 && h("div", { className: "onair-qs-progress", role: "status", "aria-live": "polite" },
        h("h3", null, running ? (job.state === "cancelling" ? "Cancelling\u2026 the current step finishes first" : "Setting up\u2026") : "Last setup: " + job.state),
        h("ol", null, steps.map((s) => h("li", { key: s.key, className: "onair-qs-step onair-qs-" + s.state }, h("strong", null, QS_STEP[s.state] || s.state), " \u00b7 " + s.label + (s.detail ? " \u2014 " + s.detail : "")))),
        failed && h("p", { className: "onair-field-error", role: "alert" }, failed.detail + " Next: " + (job.next_action || "")),
        !failed && job.next_action && h("p", { className: "onair-help" }, job.next_action),
        running && h("button", { type: "button", onClick: stop, disabled: job.state === "cancelling" }, "Cancel")),
      !running && !plan && h("form", { className: "onair-qs-form", onSubmit: review },
        saved ? h("p", null, "Saved: twitch.tv/" + saved.channel + " \u00b7 co-host " + saved.agent_name)
          : h("div", { className: "onair-wiz-field" },
            h("label", { htmlFor: "onair-qs-channel" }, "Your Twitch channel"),
            h("input", { id: "onair-qs-channel", value: channel, placeholder: "my_channel", maxLength: 25, autoComplete: "off", "aria-invalid": errors.channel ? "true" : "false", onChange: (e) => { setChannel(e.target.value); setErrors({}); } }),
            errors.channel && h("p", { className: "onair-field-error", role: "alert" }, errors.channel)),
        conn.usable_here ? h("label", { className: "onair-qs-check" }, h("input", { type: "checkbox", checked: useModel, onChange: (e) => setUseModel(e.target.checked) }), " Draft the test reply with my model connection (" + conn.label + "; one short request)")
          : h("p", { className: "onair-help" }, conn.configured ? "Model connection " + conn.label + ": " + conn.next_action : conn.next_action || ""),
        h("div", { className: "onair-qs-prefs" },
          h("label", null, "Tone ", h("select", { value: tone || savedStyle.tone || "friendly", onChange: (e) => setTone(e.target.value) }, QS_TONES.map(([v, l]) => h("option", { key: v, value: v }, l)))),
          h("label", null, "Replies ", h("select", { value: pacing || savedStyle.reply_pacing || "normal", onChange: (e) => setPacing(e.target.value) }, QS_PACING.map(([v, l]) => h("option", { key: v, value: v }, l))))),
        h("div", { className: "onair-wiz-nav" },
          h("button", { type: "submit", className: "onair-primary", disabled: busy }, saved ? "Resume preview" : "Set up & preview"),
          props.onAdvanced && h("button", { type: "button", onClick: props.onAdvanced }, "Step-by-step setup instead"))),
      plan && h("div", { className: "onair-qs-plan" },
        h("h3", null, plan.from_agent ? "A setup plan is waiting for your approval (for example from your agent). It will:" : "This will:"),
        plan.answers && h("p", { className: "onair-help" }, ["Co-host " + (plan.answers.agent_name || (saved && saved.agent_name) || "Co-host"),
          plan.answers.channel ? "twitch.tv/" + plan.answers.channel : "",
          plan.answers.style && plan.answers.style.tone ? "tone " + plan.answers.style.tone : "",
          plan.answers.style && Array.isArray(plan.answers.style.topics) && plan.answers.style.topics.length ? "topics " + plan.answers.style.topics.join(", ") : ""].filter(Boolean).join(" \u00b7 ")),
        h("ul", null, plan.effects.map((e, i) => h("li", { key: i }, e))),
        plan.answers && plan.answers.use_model && !conn.usable_here && h("p", { className: "onair-field-error", role: "status" }, "This plan uses your model connection, which this page cannot reach. Approve it in your agent chat: " + plan.approve_with.chat),
        h("div", { className: "onair-wiz-nav" },
          h("button", { type: "button", className: "onair-primary", onClick: approve, disabled: busy }, "Approve: set up & preview"),
          h("button", { type: "button", onClick: () => setPlan(null), disabled: busy }, "Change answers")),
        h("p", { className: "onair-help" }, "Or approve from your own chat: " + plan.approve_with.chat)),
      note && h("p", { className: "onair-help", role: "status" }, note)));
  }

  /* The private agent-hosted show: model-written opening and quiet-time talk, selected private chat answers,
   * captions (and voice), truthful activity, one show approval, Stop/Resume/End. */
  const SHOW_ACT = { thinking: "Thinking\u2026", speaking: "Speaking", listening: "Listening", waiting_for_you: "Waiting for you", paused: "Paused", stopped: "Not running" };
  function ShowPanel() {
    const [st, setSt] = useState(null);
    const [text, setText] = useState("");
    const [brief, setBrief] = useState("");
    const [plan, setPlan] = useState(null);
    const [note, setNote] = useState("");
    const [busy, setBusy] = useState(false);
    const alive = useRef(true);
    useEffect(() => () => { alive.current = false; }, []);
    async function refresh() { const r = await ask("setup/quick/show/status"); if (alive.current && r && r.ok) { setSt(r); // Follow the persisted pending show plan: a used/replaced code is never kept as the primary action. A plan made
      // on this page is also persisted, so it is kept only while it still matches; a running show clears it.
      const pp = r.pending_show_plan || null; setPlan((p) => (r.running || !pp) ? null : ((p && p.digest === pp.digest && p.code === pp.code) ? p : pp)); } }
    useEffect(() => { refresh(); const t = window.setInterval(refresh, 1000); return () => window.clearInterval(t); }, []);
    async function act(name, body) { setBusy(true); const r = await ask("setup/quick/show/" + name, body || {}); setBusy(false); setNote(r && r.ok !== false ? (r.note || "") : ((r && r.error) || "That did not work.")); refresh(); return r; }
    async function planShow(e) { if (e) e.preventDefault(); const r = await act("plan", { answers: brief.trim() ? { brief: brief.trim() } : {} }); if (r && r.ok) setPlan(r); else if (r) setNote((r.error || "") + (r.next_action ? " " + r.next_action : "")); }
    async function startShow() { const r = await act("start", { digest: plan.digest, confirm: true }); if (r && r.ok) setPlan(null); }
    async function say(e) { e.preventDefault(); if (!text.trim()) return; const r = await act("say", { text: text.trim() }); if (r && r.ok) setText(""); }
    const show = (st && st.show) || {};
    const running = st && st.running;
    if (st && st.state === "unknown") {
      return h("section", { className: "onair-panel onair-qs", "aria-labelledby": "onair-show-title" },
        h("h2", { id: "onair-show-title" }, "Private show status unknown"),
        h("p", { role: "alert", className: "onair-field-error" }, st.start_next_action));
    }
    if (running) {
      const pend = Array.isArray(show.pending) ? show.pending[0] : null;
      return h("section", { className: "onair-panel onair-qs", "aria-labelledby": "onair-show-title" },
        h("h2", { id: "onair-show-title" }, "Private show: " + (show.agent_name || "co-host")),
        h("p", { className: "onair-qs-real-stage", role: "status", "aria-live": "polite" }, h("strong", null, SHOW_ACT[show.activity] || show.activity), " \u2014 " + String(show.activity_text || "").replace(/^[^:]*:\s*/, "")),
        h("div", { className: "onair-qs-safety", role: "group", "aria-label": "Show controls" },
          show.activity === "paused" ? h("button", { type: "button", className: "onair-primary", onClick: () => act("resume"), disabled: busy }, "Resume show")
            : h("button", { type: "button", className: "onair-danger", onClick: () => act("stop"), disabled: busy }, "Stop show"),
          h("button", { type: "button", onClick: () => act("end"), disabled: busy }, "End show")),
        h("p", { className: "onair-help" }, "Model: " + ((show.model && (show.model.answered_by || show.model.label)) || "?") + " \u00b7 " + ((show.model && show.model.requests) || 0) + "/" + ((show.model && show.model.max_requests) || "?") + " requests \u00b7 Voice: " + (show.voice && show.voice.planned ? (show.voice.label || "on") + (show.voice.failures ? " (" + show.voice.failures + " failed)" : "") : "captions only")),
        pend && h("div", { className: "onair-wiz-nav" }, h("p", { className: "onair-qs-reply" }, pend.text),
          h("button", { type: "button", className: "onair-primary", onClick: () => act("approve", { line_id: pend.line_id, epoch: pend.epoch }), disabled: busy }, "Show"),
          h("button", { type: "button", onClick: () => act("reject", { line_id: pend.line_id, epoch: pend.epoch }), disabled: busy }, "Skip")),
        h("h3", null, "On air (private)"),
        h("ol", { className: "onair-measure" }, (show.lines || []).slice().reverse().map((l) => h("li", { key: l.caption_id }, (l.audio ? "\ud83d\udd0a " : "") + l.text))),
        h("form", { className: "onair-qs-form", onSubmit: say },
          h("div", { className: "onair-wiz-field" },
            h("label", { htmlFor: "onair-show-chat" }, "Private chat (as a viewer)"),
            h("input", { id: "onair-show-chat", value: text, maxLength: 300, autoComplete: "off", placeholder: "Say something to " + (show.agent_name || "the co-host") + "\u2026", onChange: (e) => setText(e.target.value) })),
          h("button", { type: "submit", disabled: busy || !text.trim() }, "Send")),
        h("ul", { className: "onair-help" }, (show.chat || []).slice().reverse().map((c) => h("li", { key: c.msg_id }, c.text + " \u2014 " + c.answer))),
        show.last_error && h("p", { className: "onair-field-error", role: "status" }, show.last_error),
        show.overlay_url && h("p", { className: "onair-help" }, "Captions + voice page (private): " + show.overlay_url),
        note && h("p", { className: "onair-help", role: "status" }, note));
    }
    // Only a process with your model connection can start a show (no canned stand-in). The normal dashboard has
    // none, so there the primary action is the agent-chat approval, never a Start that would be refused.
    const startHere = !!(st && st.start_here);
    const chatCmd = plan && plan.approve_with && plan.approve_with.chat;
    return h("section", { className: "onair-panel onair-qs", "aria-labelledby": "onair-show-title" },
      h("h2", { id: "onair-show-title" }, "Run a private show"),
      h("p", { className: "onair-help" }, "Your co-host opens the show, talks when chat is quiet and answers the chat worth answering, using your own model and voice. Easiest: ask your agent to \u201cstart a private On Air show\u201d."),
      st && !startHere && !plan && h("p", { className: "onair-qs-real-stage onair-show-chat-start", role: "status" },
        h("strong", null, "Shows start from your agent chat here. "), "This page cannot reach your model connection, and a show never uses a canned stand-in. " + (st.start_next_action || "")),
      startHere && !plan && h("form", { className: "onair-qs-form", onSubmit: planShow },
        h("div", { className: "onair-wiz-field" },
          h("label", { htmlFor: "onair-show-brief" }, "What is today's show about? (public: it is said on air)"),
          h("input", { id: "onair-show-brief", value: brief, maxLength: 240, placeholder: "building a desk lamp that dims when chat goes quiet", onChange: (e) => setBrief(e.target.value) })),
        h("button", { type: "submit", className: "onair-primary", disabled: busy }, "Review the show")),
      plan && h("div", { className: "onair-qs-plan" }, h("h3", null, "This show will:"),
        h("ul", null, (plan.effects || []).map((e, i) => h("li", { key: i }, e))),
        startHere ? h(React.Fragment, null,
          h("div", { className: "onair-wiz-nav" },
            h("button", { type: "button", className: "onair-primary", onClick: startShow, disabled: busy }, "Start the show"),
            h("button", { type: "button", onClick: () => setPlan(null), disabled: busy }, "Change")),
          chatCmd && h("p", { className: "onair-help" }, "Or approve from your own agent chat: " + chatCmd))
          : h("p", { className: "onair-qs-real-stage onair-show-chat-start", role: "status" },
            h("strong", null, "To start it, type this in your Hermes agent chat: "), h("code", null, chatCmd || "/onair-setup show approve CODE"),
            " This page cannot reach your model connection, so it cannot start the show itself; once it runs you watch, chat and Stop it here.")),
      note && h("p", { className: "onair-help", role: "status" }, note));
  }

  function SetupTab(props) {
    // Someone part-way through the step-by-step wizard continues there; everyone else gets quick start.
    const [advanced, setAdvanced] = useState(() => { const v = load(SETUP_KEY, {}); return Number.isInteger(v.step) && v.step > 0; });
    if (advanced) return h(React.Fragment, null,
      h("p", null, h("button", { type: "button", onClick: () => setAdvanced(false) }, "\u2190 Back to quick start")),
      h(Wizard, props));
    return h(QuickStart, { onAdvanced: () => setAdvanced(true) });
  }

  const TABS = [["setup", "Start here"], ["practice", "Practice"], ["desk", "Desk"], ["help", "Help"]];
  function App() {
    const stored = load(TAB_KEY, {});
    const [tab, setTab] = useState(typeof stored.tab === "string" ? stored.tab : "desk");
    const [status, setStatus] = useState(null);
    useEffect(() => {
      let live = true;
      ask("setup/status").then((r) => {
        if (!live || !r || r.ok !== true) return;
        setStatus(r);
        if (typeof stored.tab !== "string" && !r.desk_connected) setTab("setup");
      });
      return () => { live = false; };
    }, []);
    const go = (name) => { setTab(name); save(TAB_KEY, { tab: name }); };
    const replay = () => { save(TOUR_KEY, { step: 0, skipped: false }); go("practice"); };
    return h("div", { className: "onair-app" },
      h("nav", { className: "onair-tabs", "aria-label": "On Air sections" }, TABS.map(([name, label]) =>
        h("button", { key: name, type: "button", className: "onair-tab" + (tab === name ? " onair-tab-current" : ""), "aria-current": tab === name ? "page" : undefined, onClick: () => go(name) }, label))),
      tab === "setup" ? h(SetupTab, { status, onPractice: () => go("practice"), onDesk: () => go("desk") })
        : tab === "practice" ? h(Practice, { onSetup: () => go("setup"), onDesk: () => go("desk") })
          : tab === "help" ? h(Help, { onReplay: replay, onSetup: () => go("setup") })
            : h(Desk, { onSetup: () => go("setup"), onPractice: () => go("practice") }));
  }

  window.__HERMES_PLUGINS__.register("on-air", App);
})();
