"""Source-bound checks for the owner skills; stage and messaging isolation are separate."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
import yaml

from onair import cli, setup_agent as sa
from onair.onboarding import MARKER
from onair.presentation import agent as presentation
from onair.presentation.obs_control import ObsConfig

ROOT = Path(__file__).resolve().parents[2]
NEW = ("first-stream", "views", "host", "show-day", "troubleshoot", "seed-bank", "live-build")
OLD = ("setup", "produce", "scene", "scene-build", "avatar")
PACK = ROOT / "onair" / "skills"
_SPEC = importlib.util.spec_from_file_location("produce_skill_checks", ROOT / "tests/setup/test_produce_skill.py")
assert _SPEC is not None and _SPEC.loader is not None
_produce = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_produce)


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "owner"))
    (tmp_path / "owner").mkdir()
    ctx = _produce.Ctx()
    done = sa.register(ctx)
    presentation.register(ctx, config={"present_port": 0, "obs_url": "ws://127.0.0.1:9"})
    yield ctx, done
    presentation.teardown()


def stable(text):
    # Only the unshipped microphone section is excluded from live contract checks.
    return re.sub(r"\n## Not yet shipped[^\n]*\n.*?(?=\n## |\Z)", "\n", text, flags=re.S)


def test_host_stable_procedure_covers_shipped_controls():
    text = stable((PACK / "host" / "SKILL.md").read_text())
    procedure = text.split("## Hard rules", 1)[0]
    for item in ("onair_stage_host_turn", "/onair-stage privacy off", "/onair-stage mute off",
                 "host_relay_autonomous", "settling", "approval"):
        assert item in procedure
    assert "not present" not in procedure


@pytest.mark.parametrize("name", NEW)
def test_new_skill_metadata_and_owner_discovery(owner, name):
    ctx, done = owner
    path = PACK / name / "SKILL.md"
    text = path.read_text()
    meta = yaml.safe_load(text.split("---", 2)[1])
    assert meta["name"] == name
    description = meta["description"]
    assert len(description) <= 60 and re.fullmatch(r"Use when .+\. .+\.", description)
    assert ctx.skills[name] == (path, description)
    assert "skill:" + name in done
    assert re.search(r"(?m)^1\. ", text)
    assert "## Hard rules" in text and "## Tell the owner" in text


@pytest.mark.parametrize("name", OLD + NEW)
def test_all_skill_descriptions_fit_owner_discovery(owner, name):
    ctx, _ = owner
    path = PACK / name / "SKILL.md"
    meta = yaml.safe_load(path.read_text().split("---", 2)[1])
    assert len(meta["description"]) <= 60
    assert ctx.skills[name] == (path, meta["description"])


def test_separation_and_seed_bank_docs_are_accurate():
    dashboard = (ROOT / "dashboard/dist/index.js").read_text()
    assert "can never see your personal agent" not in dashboard
    assert "not an operating-system sandbox" in dashboard
    doc = (ROOT / "docs/seed-bank.md").read_text()
    assert "controller must call" not in doc
    assert "rundown state is shown on the desk" in doc
    assert "first repeat after an upgrade" in doc


def test_existing_metadata_report_only():
    # Existing owners can fix these independently; do not silently change their metadata.
    report = {}
    for name in OLD:
        text = (PACK / name / "SKILL.md").read_text()
        meta = yaml.safe_load(text.split("---", 2)[1])
        description = meta["description"]
        report[name] = {"length": len(description), "format": bool(re.fullmatch(r"Use when .+\. .+\.", description)),
                        "schema_problems": _produce._problems(text, {**sa.SCHEMAS, **presentation.SCHEMAS},
                                                             {"onair_setup", "onair_present"},
                                                             {"tools": 0, "actions": 0, "examples": 0})}
    print("Existing skill metadata:", json.dumps(report, sort_keys=True))
    assert set(report) == set(OLD)
    assert all(item["format"] and item["length"] <= 60 and not item["schema_problems"]
               for item in report.values())


def test_registered_skills_not_in_stage_but_messaging_tools_refuse(tmp_path, monkeypatch):
    home = tmp_path / "stage"
    home.mkdir()
    (home / MARKER).write_text("stage\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    ctx = _produce.Ctx()
    assert sa.register(ctx) == [] and ctx.skills == {} and ctx.tools == {}
    owner_home = tmp_path / "owner"
    owner_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(owner_home))
    class Messaging(_produce.Ctx):
        def session_surface(self):
            return "telegram"
    msg = Messaging()
    sa.register(msg)
    # Registration is home-scoped, not surface-scoped. Calls are guarded per session.
    assert set(NEW) <= set(msg.skills)
    refused = json.loads(sa.make_handlers(msg)["onair_setup_schema"]({}))
    assert refused["refused"] is True and "messaging chat" in refused["error"]


def test_installed_loader_resolves_and_views_each_new_skill(tmp_path, monkeypatch):
    # Reuse the real isolated Hermes loader harness, extending its probe without editing it.
    spec = importlib.util.spec_from_file_location("join_harness", ROOT / "tests/test_integration_join.py")
    assert spec is not None and spec.loader is not None
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    if not harness.PY314.is_file() or not (harness.SDK / "hermes_cli").is_dir():
        pytest.skip("the prepared Hermes runtime is unavailable")
    extra = '''\nfrom tools.skills_tool import skill_view\nout["pack_views"] = {}\nfor name in ("first-stream", "views", "host", "show-day", "troubleshoot", "seed-bank", "live-build"):\n    path = mgr.find_plugin_skill("on-air:" + name)\n    result = json.loads(skill_view("on-air:" + name)) if path else {}\n    out["pack_views"][name] = {"found": bool(path), "success": bool(result.get("success")),\n                                "content": result.get("content", "")}\n'''
    monkeypatch.setattr(harness, "PROBE", harness.PROBE.replace('out["skills"] = mgr.list_plugin_skills("on-air")',
                                                                 'out["skills"] = mgr.list_plugin_skills("on-air")' + extra))
    for kind in ("owner", "stage"):
        data = harness._run(tmp_path, kind)
        for name in NEW:
            result = data["pack_views"][name]
            if kind == "owner":
                assert result["found"] and result["success"]
                assert result["content"].endswith((PACK / name / "SKILL.md").read_text())
            else:
                assert not result["found"] and not result["success"]


def test_readme_inventory_matches_registered_tools(owner):
    ctx, _ = owner
    text = (ROOT / "README.md").read_text()
    section = text.split("## What your agent gets", 1)[1].split("## Skills pack", 1)[0]
    rows = re.findall(r"^\| (?:Setup|Private preview and show|Real stage|Presentation|OBS, layout, window and broadcast) \| (.+) \|$", section, re.M)
    names = [name for row in rows for name in re.findall(r"`(onair_[a-z_]+)`", row)]
    assert len(names) == len(set(names)) and set(names) == set(ctx.tools)
    counts = {toolset: sum(value[0] == toolset for value in ctx.tools.values())
              for toolset in ("onair_setup", "onair_present")}
    assert f"The first {counts['onair_setup']} tools belong to `onair_setup`; the other {counts['onair_present']}" in section
    assert f"{len(ctx.tools)} tools in total" in section


def test_stable_skill_calls_against_registered_contracts(owner):
    ctx, _ = owner
    schemas = _produce._schemas(ctx)
    toolsets = {toolset for toolset, _ in ctx.tools.values()}
    checked = {"tools": 0, "actions": 0, "examples": 0}
    parser = argparse.ArgumentParser()
    cli.setup_parser(parser)
    choices = next(a.choices for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    action_words = {word for schema in schemas.values() for word in
                    schema.get("parameters", {}).get("properties", {}).get("action", {}).get("enum", [])}
    config_keys = yaml.safe_load((ROOT / "plugin.yaml").read_text())["config_schema"]
    presentation_keys = ObsConfig.__dataclass_fields__
    commands_seen, cli_seen = 0, 0
    for name in NEW:
        text = stable((PACK / name / "SKILL.md").read_text())
        assert not _produce._problems(text, schemas, toolsets, checked), name
        for command, rest in re.findall(r"`/(onair-[a-z]+)(?:\s+([^`]*))?`", text):
            assert command in ctx.commands, (name, command)
            # Command words before placeholders or user text must be in the registered hint.
            for word in rest.split()[:2]:
                if word.startswith("<") or word in {"TEXT", "ID"}:
                    break
                assert word in ctx.commands[command], (name, command, word)
            commands_seen += 1
        for sub in re.findall(r"`hermes onair ([a-z-]+)", text):
            assert sub in choices, (name, sub)
            cli_seen += 1
        # Every standalone identifier is a real action, configuration key or response field.
        # Tool/action pairs and JSON examples are checked against the specific tool above.
        for word in re.findall(r"`([a-z][a-z_]+)`", text):
            if word.startswith("onair_"):
                continue
            assert word in action_words or word in config_keys or word in presentation_keys or word == "next_human_action", (name, word)
        for key in re.findall(r"`([a-z][a-z_]+)`(?= (?:enabled|in the stage settings))", text):
            assert key in config_keys or key in presentation_keys, (name, key)
    assert checked["tools"] >= 30 and checked["actions"] >= 10 and checked["examples"] >= 5
    assert commands_seen >= 6 and cli_seen >= 2
    wrong = '`onair_not_real`\n`onair_layout` `undo`\nonair_layout {"action":"apply","preset":"bogus"}'
    assert len(_produce._problems(wrong, schemas, toolsets, {"tools": 0, "actions": 0, "examples": 0})) == 3


@pytest.mark.parametrize("name", OLD + NEW)
def test_all_skills_reserve_owner_decisions(name):
    text = (PACK / name / "SKILL.md").read_text().lower()
    assert "never go live, arm, approve a line, lift privacy or unmute on your own" in text


@pytest.mark.parametrize("name", NEW)
def test_owner_only_actions_are_prohibited(name):
    text = stable((PACK / name / "SKILL.md").read_text()).lower()
    rules = text.split("## hard rules", 1)[1].split("## ", 1)[0]
    for phrase in ("never go live", "arm", "approve a line", "lift privacy", "unmute", "stream key", "never claim"):
        assert phrase in rules
    for pattern in (r"\b(?:you|agent) (?:should |must |can )?(?:arm the stage|approve (?:the |a )?line|unmute|go live|lift privacy)",
                    r"\b(?:call|invoke) [`']?onair_stage_control[`']? [`']?(?:approve|unmute|arm)"):
        assert not re.search(pattern, text), (name, pattern)


def github_anchors(text):
    result = set(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)', text))
    counts = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        heading = re.sub(r"<[^>]+>", "", heading).lower()
        heading = re.sub(r"[^\w\-\s]", "", heading).strip().replace(" ", "-")
        n = counts.get(heading, 0)
        counts[heading] = n + 1
        result.add(heading if n == 0 else f"{heading}-{n}")
    return result


def link_errors(paths):
    errors = []
    for source in paths:
        text = source.read_text()
        # Ignore fenced examples, including example markdown links.
        text = re.sub(r"(?ms)^```.*?^```\s*$", "", text)
        for raw in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text):
            dest = raw.split()[0].strip("<>")
            parts = urlsplit(dest)
            if parts.scheme or parts.netloc or dest.startswith("//"):
                continue
            target = (source.parent / unquote(parts.path)).resolve() if parts.path else source
            if not target.is_file():
                errors.append((str(source.relative_to(ROOT)), dest, "missing file"))
            elif parts.fragment and target.suffix.lower() in {".md", ".markdown"}:
                if unquote(parts.fragment) not in github_anchors(target.read_text()):
                    errors.append((str(source.relative_to(ROOT)), dest, "missing anchor"))
    return errors


def test_all_readme_and_doc_relative_links_and_images():
    paths = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    assert not link_errors(paths)


def test_public_safe_changed_text():
    words = ["salty" + "sloane", "celes" + "tine", "/data/" + "sda", "home" + r"\.arpa",
             r"192\." + r"168\.", r"\b" + "M" + "ai" + r"\b", r"\b" + "Bi" + "ll" + r"\b",
             "Mi" + "so", "Ar" + "thur", "Sisy" + "phus", "git" + "ea", "hack" + "athon", "kan" + "ban"]
    forbidden = re.compile("|".join(words), re.I)
    files = [ROOT / "README.md", Path(__file__), *(PACK / n / "SKILL.md" for n in NEW),
             *(ROOT / "docs" / n for n in ("index.md", "quickstart.md", "streaming-setup.md", "troubleshooting.md", "faq.md", "safety.md"))]
    for path in files:
        assert not forbidden.search(path.read_text()), str(path.relative_to(ROOT))
