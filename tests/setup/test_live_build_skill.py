"""Executable contract for the live-build owner's documented tool calls."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from onair import setup_agent
from onair.presentation import agent, work

SKILL = Path(__file__).resolve().parents[2] / "onair/skills/live-build/SKILL.md"


def check_value(value, schema):
    """Check JSON types, enum, required and nested fields against registered schemas."""
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    kind = schema.get("type")
    if kind:
        assert type(value) is types[kind], (value, kind)
    if "enum" in schema:
        assert value in schema["enum"], value
    if isinstance(value, str) and "maxLength" in schema:
        assert len(value) <= schema["maxLength"]
    if isinstance(value, int) and not isinstance(value, bool):
        assert value >= schema.get("minimum", value)
        assert value <= schema.get("maximum", value)
    if isinstance(value, list):
        assert len(value) >= schema.get("minItems", 0)
        assert len(value) <= schema.get("maxItems", len(value))
        for item in value:
            check_value(item, schema.get("items", {}))
    if isinstance(value, dict):
        props = schema.get("properties", {})
        assert set(schema.get("required", ())) <= set(value)
        if schema.get("additionalProperties") is False:
            assert set(value) <= set(props), set(value) - set(props)
        for key, item in value.items():
            if key in props:
                check_value(item, props[key])


def example_calls(text):
    return [(name, json.loads(raw)) for name, raw in
            re.findall(r"^(onair_[a-z_]+) (\{.*\})$", text, re.M)]


def test_skill_tool_mentions_and_every_example_match_registered_schemas(owner):
    ctx, _ = owner
    schemas = {name: schema for name, (_toolset, schema) in ctx.tools.items()}
    text = SKILL.read_text()
    names = set(re.findall(r"\bonair_[a-z_]+\b", text))
    assert names == {"onair_work_share", "onair_layout", "onair_present_status",
                     "onair_stage_control", "onair_obs_setup", "onair_obs_scene",
                     "onair_stage_host_turn"}
    assert names <= set(schemas)
    calls = example_calls(text)
    assert len(calls) >= 20
    assert names - {"onair_obs_setup"} <= {name for name, _ in calls}
    for name, args in calls:
        check_value(args, schemas[name]["parameters"])
    # A typo in a nested pane field must not silently pass the contract.
    bad = {"action": "show", "title": "Build", "panes": [
        {"kind": "code", "label": "Code", "content": "", "invented": "x"}]}
    with pytest.raises(AssertionError):
        check_value(bad, schemas["onair_work_share"]["parameters"])
    with pytest.raises(AssertionError):
        check_value({"action": "set", "main": "not-a-source"}, schemas["onair_layout"]["parameters"])


@pytest.fixture
def owner(tmp_path, monkeypatch):
    # Same registration path as the other packaged skill contract tests.
    from test_produce_skill import Ctx
    home = tmp_path / "owner"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    ctx = Ctx()
    setup_agent.register(ctx)
    agent.register(ctx, config={"present_port": 0, "obs_url": "ws://127.0.0.1:9"})
    yield ctx, None
    agent.teardown()


def test_example_work_actions_pass_real_content_validator():
    state = None
    mutations = 0
    for name, args in example_calls(SKILL.read_text()):
        if name != "onair_work_share":
            continue
        if args["action"] == "clear":
            state = None
            continue
        state, errors = work.apply(state, args)
        assert not errors, (args, errors)
        assert state is not None
        mutations += 1
    assert mutations >= 9


def test_example_code_reproduces_real_failure_and_pass(tmp_path):
    calls = example_calls(SKILL.read_text())
    snippets = [args["content"] for name, args in calls
                if name == "onair_work_share" and args["action"] == "update"]
    outputs = [args["text"].strip() for name, args in calls
               if name == "onair_work_share" and args["action"] == "append"]
    assert len(snippets) == len(outputs) == 2
    (tmp_path / "test_synth.py").write_text(snippets[0] + "\n")
    (tmp_path / "synth.py").write_text("def tone(freq, rate, count):\n    return [0.0] * count\n")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    command = [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_synth.py"]
    failed = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert failed.returncode != 0 and outputs[0] in failed.stdout
    (tmp_path / "synth.py").write_text(snippets[1] + "\n")
    passed = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert passed.returncode == 0 and outputs[1] in passed.stdout


def test_skill_has_public_safety_and_owner_word_boundary():
    text = SKILL.read_text()
    assert not re.search(r"(?<![A-Za-z0-9_])/([A-Za-z0-9._-]+/)+|\b[A-Za-z]:\\|192\.168\.", text)
    for phrase in ("owner's own words", "does NOT authorize autonomous model-written milestone notes",
                   "never retry the same text", "at most once", "never touch OBS items",
                   "stop and clear immediately", "owner-named project folder"):
        assert phrase.lower() in text.lower()
