"""The on-air:produce skill: registered for the OWNER's agent only, source-bound, and consistent with the real schemas.

FakeCtx stands in for the host PluginContext registrars (labelled fixture). The real Hermes loader check of the same
skill is in tests/test_integration_join.py. The doc checks read the shipped Markdown and compare every tool, action,
example payload, preset and slash command it names with the ACTUAL registered schemas and commands, so the skill
cannot promise an operation the plugin does not have.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from onair import setup_agent as sa
from onair.onboarding import MARKER
from onair.presentation import agent as present
from onair.presentation import layout

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "onair" / "skills" / "produce" / "SKILL.md"
DOCS = [SKILL, ROOT / "docs" / "production.md"]


class Ctx:
    """FIXTURE registrar: records tools, commands (with args_hint) and skills; owner CLI session."""

    def __init__(self):
        self.tools, self.commands, self.skills = {}, {}, {}

    def session_surface(self):
        return "cli"

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (toolset, schema)

    def register_command(self, name, handler, description="", args_hint="", **kw):
        self.commands[name] = args_hint

    def register_skill(self, name, path, description="", **kw):
        self.skills[name] = (Path(path), description)


@pytest.fixture
def owner(tmp_path, monkeypatch):
    home = tmp_path / "owner"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    ctx = Ctx()
    done = sa.register(ctx)
    present.register(ctx, config={"present_port": 0, "obs_url": "ws://127.0.0.1:9"})
    yield ctx, done
    present.teardown()


def test_produce_skill_is_registered_for_the_owner_and_bound_to_its_source(owner):
    ctx, done = owner
    assert "skill:produce" in done and "skill:setup" in done
    path, description = ctx.skills["produce"]
    assert path.resolve() == SKILL.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(SKILL.read_bytes()).hexdigest()
    front = SKILL.read_text().split("---")[1]
    assert re.search(r"^name: produce$", front, re.M)
    assert re.search(r"^description: (.+)$", front, re.M).group(1) == description
    assert description.startswith("Use when") and len(description) <= 80


@pytest.mark.parametrize('name', ['scene', 'scene-build', 'avatar'])
def test_scene_skills_registered_for_owner(owner, name):
    ctx, done = owner
    path, description = ctx.skills[name]
    assert 'skill:' + name in done
    assert path == ROOT / 'onair' / 'skills' / name / 'SKILL.md'
    assert description.startswith('Use when ') and len(description) <= 60
    assert f'description: {description}' in path.read_text()


def test_stage_home_registers_no_skill(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / MARKER).write_text("stage\n")
    monkeypatch.setenv("HERMES_HOME", str(stage))
    ctx = Ctx()
    assert sa.register(ctx) == [] and ctx.skills == {} and ctx.tools == {}
    assert present.register(ctx) == [] and "onair_layout" not in ctx.tools


def _schemas(ctx):
    return {name: schema for name, (_toolset, schema) in ctx.tools.items()}


def _texts():
    return {p.name: p.read_text() for p in DOCS}


def _problems(text, schemas, toolsets, checked):
    """Every tool, `tool` `action` pair and `tool {json}` example in TEXT checked against the real schemas."""
    bad = []
    for tok in re.findall(r"`(onair_[a-z_]+)`", text):
        checked["tools"] += 1
        if tok not in schemas and tok not in toolsets:
            bad.append(("unknown tool", tok))
    for tool, word in re.findall(r"`(onair_[a-z_]+)` `([a-z_]+)`", text):
        checked["actions"] += 1
        props = schemas.get(tool, {}).get("parameters", {}).get("properties", {})
        if word not in props.get("action", {}).get("enum", []) and word not in props:
            bad.append(("unknown action", tool, word))
    for tool, raw in re.findall(r"^(onair_[a-z_]+) (\{.*\})$", text, re.M):
        checked["examples"] += 1
        args, params = json.loads(raw), schemas.get(tool, {}).get("parameters")
        if params is None:
            bad.append(("example for unknown tool", tool))
            continue
        if not set(args) <= set(params["properties"]) or not set(params.get("required", [])) <= set(args):
            bad.append(("example keys", tool, sorted(args)))
            continue
        for key, value in args.items():
            spec = params["properties"][key]
            kind = {"string": str, "boolean": bool, "integer": int, "object": dict}.get(spec.get("type"))
            if ("enum" in spec and value not in spec["enum"]) or (kind and not isinstance(value, kind)):
                bad.append(("example value", tool, key, value))
    return bad


def test_every_named_tool_action_and_example_exists_in_the_real_schemas(owner):
    ctx, _ = owner
    schemas = _schemas(ctx)
    toolsets = {toolset for toolset, _ in ctx.tools.values()}
    assert "onair_layout" in schemas and {"onair_setup", "onair_present"} <= toolsets
    checked = {"tools": 0, "actions": 0, "examples": 0}
    for name, text in _texts().items():
        assert _problems(text, schemas, toolsets, checked) == [], name
    print(json.dumps(checked))
    assert checked["tools"] >= 20 and checked["actions"] >= 10 and checked["examples"] >= 4, checked
    # negative control: the same checker rejects invented tools, actions, presets, keys and types
    wrong = ("`onair_layout` `undo`\n`onair_avatar`\n"
             'onair_layout {"action": "apply", "preset": "avatar"}\n'
             'onair_layout {"action": "apply", "camera": "Camera"}\n'
             'onair_layout {"preset": "window"}\n'
             'onair_present_publish {"sample": "yes"}\n')
    found = _problems(wrong, schemas, toolsets, {"tools": 0, "actions": 0, "examples": 0})
    assert [b[0] for b in found] == ["unknown tool", "unknown action", "example value", "example keys",
                                     "example keys", "example value"], found


def test_slash_commands_named_in_the_docs_are_real(owner):
    ctx, _ = owner
    seen = 0
    for name, text in _texts().items():
        for cmd, rest in re.findall(r"`/(onair-[a-z]+) ([^`]+)`", text):
            assert cmd in ctx.commands, (name, cmd)
            hint = ctx.commands[cmd]
            for word in rest.split()[:2]:
                if not word.startswith("<"):
                    assert word in hint, (name, cmd, word, hint)
            seen += 1
    assert seen >= 3


def test_presets_and_corners_in_the_docs_match_the_tool(owner):
    ctx, _ = owner
    enum = _schemas(ctx)["onair_layout"]["parameters"]["properties"]
    for name, text in _texts().items():
        rows = re.findall(r"^\| `([a-z_]+)` \|", text, re.M)
        assert rows == list(enum["preset"]["enum"]) == list(layout.PRESET_NAMES), (name, rows)
        for corner in enum["corner"]["enum"]:
            assert f"`{corner}`" in text, (name, corner)
    text = SKILL.read_text()
    assert "The overlay has no built-in avatar; never promise camera, guest or host video." in text
    assert "An optional owner 3D scene" in text and "docs/scene.md" in text
    for word in ("avatar", "camera", "guest"):
        for line in text.splitlines():
            if word in line.lower():
                assert "no built-in avatar" in line.lower() and "never promise" in line.lower(), line


def test_docs_are_public_safe_and_honest_about_standby(owner):
    for name, text in _texts().items():
        assert not re.search(r"/home/|/Users/|192\.168\.|\b10\.\d+\.\d+\.\d+|sk-[A-Za-z0-9]{8}", text), name
        assert "not an audio privacy cut" in text.lower() or "not a privacy cut" in text.lower(), name
        assert "confirm-window" in text and "applied: true" in text, name
