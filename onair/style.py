"""The co-host's PUBLIC style: one bounded, documented customization schema.

The owner (through their private agent or the dashboard) chooses the public co-host's persona line,
tone, topics, subjects to avoid, reply length and reply pacing. Every choice is validated here,
saved into the stage home through the plugin's own settings (``plugins.entries.on-air.settings``)
and the stage's public ``SOUL.md``, and read back from there by every consumer:

- the real stage (``adapter._settings`` -> ``StageConfig.select`` and the never-say guard list);
- the private setup preview (same settings, same SOUL.md, reply length as its token cap);
- the dashboard and the agent's status (both show :func:`read_style` of the saved stage).

Unknown fields and unsafe values are refused with an explicit per-field error; nothing is ever
silently dropped. Output stays captions-only here: voice needs its own listening test after the
first reply, and the approval policy is chosen at the desk, never by setup.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

TONES = {
    "friendly": "Warm and friendly.",
    "relaxed": "Relaxed and unhurried; low-key, never hyped.",
    "upbeat": "Upbeat and energetic, but never shouting.",
    "dry": "Dry and understated, with gentle wit.",
}
LENGTHS = {"short": ("Keep every reply to one short spoken sentence.", 60),
           "medium": ("Keep replies to one to three short spoken sentences.", 120)}
# reply_pacing -> (max worthy chat replies per turn, seconds between routine replies) for chat.select
PACING = {"normal": (2, 120.0), "less_often": (1, 300.0), "rare": (1, 600.0)}
PACING_TEXT = {"normal": "", "less_often": "Reply less often: answer only the most worthwhile messages.",
               "rare": "Reply rarely: answer only direct questions worth answering."}
DEFAULTS = {"persona": "", "tone": "friendly", "topics": [], "avoid_topics": [], "reply_length": "medium",
            "reply_pacing": "normal", "output": "captions"}
_TERM = re.compile(r"[\w][\w '&+.-]{0,39}\Z", re.UNICODE)
_LINK = re.compile(r"https?://|www\.|\S+@\S+\.\w", re.I)
START, END = "<!-- on-air:style:start -->", "<!-- on-air:style:end -->"

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "description": ("Public co-host style. Send only the fields the user wants to change (a patch); the "
                    "rest keep their saved value. Everything here is public and shown to viewers' co-host."),
    "properties": {
        "persona": {"type": "string", "maxLength": 240,
                    "description": "One or two PUBLIC sentences about the co-host's personality. No private notes, "
                                   "memories, contacts or links."},
        "tone": {"type": "string", "enum": sorted(TONES)},
        "topics": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 40},
                   "description": "Public topics the co-host enjoys."},
        "avoid_topics": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 40},
                         "description": "Subjects never to bring up. Each is ALSO added to the deterministic never-say "
                                        "guard, so a line containing it is held back."},
        "reply_length": {"type": "string", "enum": sorted(LENGTHS)},
        "reply_pacing": {"type": "string", "enum": sorted(PACING),
                         "description": "How often chat gets a reply (less_often/rare answer fewer messages)."},
        "output": {"type": "string", "enum": ["captions"],
                   "description": "Captions only during setup. Voice is offered after the first reply and needs "
                                  "its own listening test."},
    },
}
PRESETS = {
    "relaxed": {"tone": "relaxed"},
    "reply_less_often": {"reply_pacing": "less_often"},
    "quiet": {"reply_pacing": "rare", "reply_length": "short"},
    "chatty": {"tone": "upbeat", "reply_pacing": "normal", "reply_length": "medium"},
}
EXAMPLES = [
    {"user_says": "keep it relaxed and reply less often", "style": {"tone": "relaxed", "reply_pacing": "less_often"}},
    {"user_says": "short answers, and never talk about politics", "style": {"reply_length": "short",
                                                                            "avoid_topics": ["politics"]}},
]
_REFUSED = {
    "voice": "Voice is not set during setup: it is offered after your first reply and needs its own listening test.",
    "approval": "The approval policy is chosen at the desk, not by setup; setup previews always wait for you.",
    "mode": "The approval policy is chosen at the desk, not by setup; setup previews always wait for you.",
    "autonomous": "The approval policy is chosen at the desk, not by setup; setup previews always wait for you.",
    "system_prompt": "Only the public persona line is customizable; there is no raw prompt field.",
}


def describe() -> dict:
    """The discoverable customization contract (schema, presets, examples, defaults, how each is enforced)."""
    return {"schema": SCHEMA, "presets": PRESETS, "examples": EXAMPLES, "defaults": DEFAULTS,
            "enforcement": ENFORCEMENT, "not_customizable_here": _REFUSED}


# Hard = deterministic code enforces it; soft = an instruction in the stage's SOUL.md that the model is
# asked to follow (genuine model behaviour is not tested here).
ENFORCEMENT = {
    "reply_pacing": "hard in the real stage and the preview: chat selection limits (max_worthy_per_turn, routine_gap_s)",
    "avoid_topics": "hard in the real stage and the preview: added to never_say (deterministic guard); also a SOUL.md instruction",
    "reply_length": "soft in the real stage (SOUL.md instruction); hard only in the private preview (60/120 token cap)",
    "tone": "soft (SOUL.md instruction)", "persona": "soft (SOUL.md instruction)", "topics": "soft (SOUL.md instruction)",
    "output": "captions only; voice needs its own listening test after the first reply",
}


def validate_patch(patch: Any) -> tuple[dict, dict]:
    """(clean patch, errors). Unknown fields and unsafe values are explicit errors."""
    if patch is None:
        return {}, {}
    if not isinstance(patch, dict):
        return {}, {"style": "style must be an object of fields to change"}
    clean, errors = {}, {}
    for key, value in patch.items():
        if key == "preset":
            names = value if isinstance(value, list) else [value]
            for name in names:
                if name not in PRESETS:
                    errors["preset"] = f"Unknown preset {name!r}; choose from {', '.join(sorted(PRESETS))}."
                else:
                    clean.update(PRESETS[name])
            continue
        if key not in SCHEMA["properties"]:
            errors[key] = _REFUSED.get(key, f"{key!r} is not a supported style setting; supported: "
                                       + ", ".join(sorted(SCHEMA["properties"])) + ".")
            continue
        spec = SCHEMA["properties"][key]
        if "enum" in spec:
            if value not in spec["enum"]:
                errors[key] = (_REFUSED["voice"] if key == "output" and value != "captions"
                               else f"Choose one of: {', '.join(spec['enum'])}.")
            else:
                clean[key] = value
        elif key == "persona":
            text = " ".join(str(value).split()) if isinstance(value, str) else None
            if text is None or len(text) > 240 or any(ord(ch) < 32 for ch in str(value).replace("\n", " ")):
                errors[key] = "The persona must be text, 240 characters or fewer."
            elif _LINK.search(text) or "#" in text[:1] or "<" in text or "`" in text:
                errors[key] = "Keep the persona to plain public words: no links, addresses, markup or headings."
            else:
                clean[key] = text
        else:  # topic lists
            if not isinstance(value, list) or len(value) > 8:
                errors[key] = "Give up to 8 short subjects as a list."
                continue
            terms = [" ".join(str(v).split()) for v in value if isinstance(v, str)]
            bad = [t for t in terms if not _TERM.fullmatch(t) or _LINK.search(t)]
            if len(terms) != len(value) or bad:
                errors[key] = "Each subject must be 1 to 40 plain characters (letters, digits, spaces)."
            else:
                clean[key] = list(dict.fromkeys(terms))
    return clean, errors


def merged(current: dict | None, patch: dict) -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (current or {}).items() if k in DEFAULTS})
    out.update(patch)
    return out


def changes(current: dict | None, patch: dict) -> list[str]:
    before, after = merged(current, {}), merged(current, patch)
    lines = []
    for key in SCHEMA["properties"]:
        if before[key] != after[key]:
            show = (lambda v: ", ".join(v) if isinstance(v, list) else (v or "(none)"))
            lines.append(f"{key.replace('_', ' ')}: {show(before[key])} -> {show(after[key])}")
    return lines


# ---- reading and writing the saved stage --------------------------------------------------------
def _settings(home: Path) -> dict:
    from .yamlio import safe_load
    config = safe_load((Path(home) / "config.yaml").read_text(encoding="utf-8")) or {}
    return (((config.get("plugins") or {}).get("entries") or {}).get("on-air") or {}).get("settings") or {}


def read_style(home: Path) -> dict:
    """The style the stage will actually use, re-read from its own config (defaults where unset)."""
    settings = _settings(home)
    saved = settings.get("public_style") if isinstance(settings.get("public_style"), dict) else {}
    style = merged(saved, {})
    pacing = settings.get("reply_pacing")
    style["reply_pacing"] = pacing if pacing in PACING else style["reply_pacing"]
    return style


def select_config(pacing: Any):
    """chat.select configuration for a reply_pacing value (unknown -> normal)."""
    from .chat.select import SelectConfig
    worthy, gap = PACING.get(pacing if isinstance(pacing, str) else "normal", PACING["normal"])
    return SelectConfig(max_worthy_per_turn=worthy, routine_gap_s=gap)


def soul_section(style: dict) -> str:
    lines = [START, "## Style chosen by the owner (On Air setup; public)", "", "- Tone: " + TONES[style["tone"]]]
    if style["persona"]:
        lines.append("- About you: " + style["persona"])
    if style["topics"]:
        lines.append("- Topics you enjoy: " + ", ".join(style["topics"]) + ".")
    if style["avoid_topics"]:
        lines.append("- Never bring up: " + ", ".join(style["avoid_topics"]) + ".")
    lines.append("- " + LENGTHS[style["reply_length"]][0])
    if PACING_TEXT[style["reply_pacing"]]:
        lines.append("- " + PACING_TEXT[style["reply_pacing"]])
    return "\n".join(lines + [END]) + "\n"


def render_files(home: Path, style: dict, previous: dict | None) -> dict[str, str]:
    """New text for config.yaml and SOUL.md with ``style`` applied; targeted, other edits kept."""
    home = Path(home)
    config = (home / "config.yaml").read_text(encoding="utf-8")
    settings = _settings(home)
    base = [t for t in (settings.get("never_say") or []) if t not in set((previous or {}).get("avoid_topics") or [])]
    never = list(dict.fromkeys([*base, *style["avoid_topics"]]))
    q = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731 - JSON is valid YAML flow
    public = {k: style[k] for k in DEFAULTS if k != "reply_pacing"}
    new, n = re.subn(r"(?m)^([ \t]+)never_say: .*$", lambda m: f"{m.group(1)}never_say: [{', '.join(q(t) for t in never)}]",
                     config, count=1)
    if n != 1:
        raise ValueError("the stage config has no never_say setting to update")
    found = re.search(r"(?m)^([ \t]+)never_say: ", new)
    indent = found.group(1) if found else "        "
    for key, value in (("reply_pacing", style["reply_pacing"]), ("public_style", q(public))):
        line = f"{indent}{key}: {value}"
        new, n = re.subn(rf"(?m)^{indent}{key}: .*$", lambda _m, line=line: line, new, count=1)
        if n == 0:
            new = re.sub(r"(?m)^([ \t]+never_say: .*)$", lambda m, line=line: m.group(1) + "\n" + line, new, count=1)
    soul = (home / "SOUL.md").read_text(encoding="utf-8")
    if START in soul and END in soul:
        head, rest = soul.split(START, 1)
        soul = head + soul_section(style) + rest.split(END, 1)[1].lstrip("\n")
    else:
        soul = soul.rstrip("\n") + "\n\n" + soul_section(style)
    return {"config.yaml": new, "SOUL.md": soul}


def write_files(home: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = Path(home) / name
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
