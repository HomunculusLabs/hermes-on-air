"""First-run wizard backend and guided practice, through the real dashboard router.

The wizard's apply calls the existing stage-home creator and re-reads the result; practice drives
the real stage controller, guard, desk control API and overlay without writing discovery files.
"""
import asyncio
import json
import os
import time
import urllib.request

import pytest

fastapi = pytest.importorskip("fastapi", reason="dashboard stack not installed")
httpx = pytest.importorskip("httpx", reason="dashboard test client not installed")

from dashboard import plugin_api  # noqa: E402

BASE = "/api/plugins/on-air"


def app():
    a = fastapi.FastAPI()
    a.include_router(plugin_api.router, prefix=BASE)
    return a


@pytest.fixture
def homes(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes-home"
    hermes.mkdir()
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    monkeypatch.setenv("HOME", str(user))
    for name in ("ONAIR_ALLOWED_USERS", "ONAIR_TWITCH_CHANNEL"):
        monkeypatch.delenv(name, raising=False)
    return hermes, user


def run(coro):
    return asyncio.run(coro)


async def client_do(fn):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url="http://t") as c:
        return await fn(c)


ANSWERS = {"channel": "my_channel", "host_name": "Sam", "agent_name": "Pixel",
           "topics": "retro games, lamp building", "voice": "captions"}


def test_unconfigured_status_and_inline_errors(homes):
    hermes, user = homes

    async def go(c):
        status = (await c.get(BASE + "/setup/status")).json()
        assert status["desk_connected"] is False and status["current_home_is_stage"] is False
        assert status["suggested_target"] == str(user / "onair-stage")
        bad = await c.post(BASE + "/setup/plan", json={"answers": {"channel": "#no spaces!", "host_name": "",
                                                                    "target": ""}})
        assert bad.status_code == 200 and bad.json()["ok"] is False
        errors = bad.json()["errors"]
        assert "Twitch channel" in errors["channel"] and "host_name" in errors and "target" in errors
    run(client_do(go))
    assert sorted(os.listdir(user)) == []


def test_plan_creates_nothing_then_confirmed_apply_reads_back(homes):
    hermes, user = homes
    target = str(user / "onair-stage")

    async def go(c):
        plan = (await c.post(BASE + "/setup/plan", json={"answers": {**ANSWERS, "target": target}})).json()
        assert plan["ok"] and plan["target"] == target and not os.path.exists(target)
        assert any("twitch.tv/my_channel" in s for s in plan["summary"])
        refused = await c.post(BASE + "/setup/apply", json={"answers": {**ANSWERS, "target": target}})
        assert refused.json()["ok"] is False and "confirm" in refused.json()["errors"] and not os.path.exists(target)
        applied = await c.post(BASE + "/setup/apply", json={"answers": {**ANSWERS, "target": target}, "confirm": True})
        assert applied.status_code == 200, applied.text
        return applied.json()
    result = run(client_do(go))
    rows = {r["key"]: r for r in result["readiness"]["rows"]}
    assert rows["settings"]["state"] == "done" and rows["channel"]["state"] == "done"
    assert rows["isolation"]["state"] == "done" and rows["context"]["state"] == "done"
    assert rows["seed"]["state"] == "done" and rows["voice"]["state"] == "done"
    steps = {s["key"]: s for s in result["readiness"]["steps"]}
    assert rows["plugin"]["state"] == "action" and steps["plugin"]["number"] == rows["plugin"]["step"]
    plugin_cmd = steps["plugin"]["command"] or "".join(steps["plugin"]["template"])
    assert "plugins install" in plugin_cmd and "<on-air source>" not in plugin_cmd
    assert rows["model"]["state"] == "action" and rows["runtime"]["state"] == "untested"
    assert "Not ready for a real show yet" in result["readiness"]["summary"]
    soul = (user / "onair-stage" / "SOUL.md").read_text()
    assert "You are Pixel" in soul and "retro games" in soul
    # nothing written into the current Hermes home
    assert sorted(os.listdir(hermes)) == []

    async def again(c):
        dup = await c.post(BASE + "/setup/apply", json={"answers": {**ANSWERS, "target": target}, "confirm": True})
        assert dup.json()["ok"] is False and "already exists" in dup.json()["errors"]["target"]
        inside = await c.post(BASE + "/setup/apply", json={"answers": {**ANSWERS, "target": str(hermes / "stage")},
                                                            "confirm": True})
        assert inside.json()["ok"] is False and "overlaps an existing Hermes home" in inside.json()["errors"]["target"]
        rb = await c.post(BASE + "/setup/readiness", json={"home": target})
        assert rb.json()["ok"] is True and rb.json()["rows"]
        foreign = await c.post(BASE + "/setup/readiness", json={"home": str(hermes)})
        assert foreign.json()["ok"] is False
    run(client_do(again))
    assert not (hermes / "stage").exists()


def test_voice_choice_is_needs_action_not_ready(homes):
    hermes, user = homes
    target = str(user / "voice-stage")

    async def go(c):
        r = await c.post(BASE + "/setup/apply", json={"answers": {**ANSWERS, "voice": "tts", "target": target}, "confirm": True})
        return r.json()
    rows = {r["key"]: r for r in run(client_do(go))["readiness"]["rows"]}
    assert rows["voice"]["state"] == "action" and "not tested" in rows["voice"]["detail"]


def _readiness_for(target):
    async def go(c):
        return (await c.post(BASE + "/setup/readiness", json={"home": target})).json()
    return run(client_do(go))


def _apply(target):
    async def go(c):
        return (await c.post(BASE + "/setup/apply", json={"answers": {**ANSWERS, "target": target},
                                                          "confirm": True})).json()
    return run(client_do(go))


def test_remaining_steps_are_exact_scoped_and_counted(homes, monkeypatch):
    from shlex import quote
    setup_wizard = plugin_api._onair("setup_wizard")  # the module object the router really uses
    hermes, user = homes
    target = str(user / "onair-stage")
    ready = _apply(target)["readiness"]
    prefix = "HERMES_HOME=" + quote(target) + " hermes "
    steps = ready["steps"]
    keys = [s["key"] for s in steps]
    assert keys == ["plugin", "model", "preflight", "gateway", "desk"]
    assert [s["number"] for s in steps] == list(range(1, len(steps) + 1))
    # one count everywhere: the summary counts exactly the listed steps
    assert (str(len(steps)) + " steps left") in ready["summary"]
    numbers = {s["number"] for s in steps}
    assert all(r["step"] is None or r["step"] in numbers for r in ready["rows"])
    by = {s["key"]: s for s in steps}
    assert by["gateway"]["command"] == prefix + "gateway run"
    assert by["preflight"]["command"] == prefix + "onair preflight"
    assert "does not connect" in by["preflight"]["note"]
    for s in steps:
        cmd = s["command"] if s["command"] else "".join(s["template"])
        assert cmd.startswith(prefix), cmd
        assert (s["command"] is None) != (s["template"] is None)
    rows = {r["key"]: r for r in ready["rows"]}
    assert rows["runtime"]["state"] == "untested" and rows["runtime"]["step"] == by["gateway"]["number"]
    assert "Not connected" in rows["channel"]["detail"] and rows["channel"]["label"].endswith("saved")
    assert "-p stage" in ready["command_note"] and "profile" in ready["command_note"]

    # Not a Git checkout: a marked template that says where the source comes from, never copy-ready.
    monkeypatch.setattr(setup_wizard, "_SOURCE_ROOT", user)
    plugin = {s["key"]: s for s in _readiness_for(target)["steps"]}["plugin"]
    assert plugin["command"] is None and plugin["template"][1] == "<git-url>"
    assert "Git address" in plugin["note"]
    # A Git checkout holding the manifest: one exact file:// command for that very source.
    src = user / "src"
    (src / ".git").mkdir(parents=True)
    (src / "plugin.yaml").write_text("name: on-air\n")
    monkeypatch.setattr(setup_wizard, "_SOURCE_ROOT", src)
    plugin = {s["key"]: s for s in _readiness_for(target)["steps"]}["plugin"]
    assert plugin["template"] is None
    assert plugin["command"] == prefix + "plugins install " + quote("file://" + str(src)) + " --enable"


def test_empty_or_stale_plugin_folder_is_not_installed(homes):
    hermes, user = homes
    target = user / "onair-stage"
    _apply(str(target))
    folder = target / "plugins" / "on-air"
    folder.mkdir(parents=True)
    for files in ([], ["plugin.yaml"]):
        for name in files:
            (folder / name).write_text("name: on-air\n")
        ready = _readiness_for(str(target))
        row = {r["key"]: r for r in ready["rows"]}["plugin"]
        assert row["state"] == "action", (files, row)
        assert "not a usable install" in row["detail"]
        plugin = {s["key"]: s for s in ready["steps"]}["plugin"]
        assert (plugin["command"] or "".join(plugin["template"])).endswith("--enable --force")
    # manifest + entry module + enabled in the stage config (the wizard enables it): done
    (folder / "__init__.py").write_text("")
    ready = _readiness_for(str(target))
    row = {r["key"]: r for r in ready["rows"]}["plugin"]
    assert row["state"] == "done" and "enabled" in row["detail"]
    assert "plugin" not in {s["key"] for s in ready["steps"]}
    # ... but explicitly disabled in the stage config: not done, and the exact enable command
    cfg = target / "config.yaml"
    import yaml
    data = yaml.safe_load(cfg.read_text())
    data.setdefault("plugins", {})["disabled"] = ["on-air"]
    cfg.write_text(yaml.safe_dump(data))
    ready = _readiness_for(str(target))
    row = {r["key"]: r for r in ready["rows"]}["plugin"]
    assert row["state"] == "action" and "not enabled" in row["detail"]
    plugin = {s["key"]: s for s in ready["steps"]}["plugin"]
    assert plugin["command"].endswith("hermes plugins enable on-air")


def _wait(c_get, predicate, timeout=6.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = c_get()
        if predicate(value):
            return value
        time.sleep(0.1)
    raise AssertionError("condition not reached: %r" % (value,))


def test_guided_practice_drives_real_stage_without_discovery(homes):
    hermes, user = homes
    show = None

    async def go(c):
        started = (await c.post(BASE + "/practice/start")).json()
        assert started["running"] is True
        nonlocal show
        show = plugin_api._practice

        async def facts():
            return (await c.get(BASE + "/practice/facts")).json()["facts"]

        async def until(pred):
            for _ in range(80):
                f = await facts()
                if pred(f):
                    return f
                await asyncio.sleep(0.1)
            raise AssertionError(await facts())

        await until(lambda f: f["pending"] == 1)
        pending = (await c.get(BASE + "/practice/pending")).json()
        line = pending["pending"][0]
        assert "approves a line" in line["text"]
        state = (await c.get(BASE + "/practice/state")).json()
        assert state["practice"] is True and state["mode"] == "approval" and state["voice"] is False
        r = await c.post(BASE + "/practice/approve", json={"line_id": line["line_id"], "epoch": line["epoch"]})
        assert r.json()["ok"] is True
        f = await until(lambda f: f["captions"] >= 1)
        assert f["approved"] == 1 and "approves a line" in f["last_caption"]
        # the caption reached the real overlay server's event stream
        overlay = f["overlay_url"]
        assert overlay.startswith("http://127.0.0.1:")
        html = urllib.request.urlopen(overlay, timeout=3).read()
        assert b"<" in html
        await c.post(BASE + "/practice/next")
        await until(lambda f: f["pending"] == 1)
        line = (await c.get(BASE + "/practice/pending")).json()["pending"][0]
        assert "3am" in line["text"]
        r = await c.post(BASE + "/practice/reject", json={"line_id": line["line_id"], "epoch": line["epoch"]})
        assert r.json()["ok"] is True
        await c.post(BASE + "/practice/privacy", json={"on": True})
        await c.post(BASE + "/practice/privacy", json={"on": False})
        await c.post(BASE + "/practice/stop", json={})
        f = await facts()
        assert f["rejected"] == 1 and f["privacy_on"] == 1 and f["privacy_off"] == 1
        assert f["stopped"] == 1 and f["mode"] == "paused" and f["resumed"] is False
        await c.post(BASE + "/practice/mode", json={"mode": "approval"})
        assert (await facts())["resumed"] is True
        # practice never wrote discovery files: the real desk still sees no show
        assert not (hermes / "plugin-data").exists()
        real = await c.get(BASE + "/state")
        assert real.status_code == 503
        # a route outside the practice allow-list does not exist
        assert (await c.post(BASE + "/practice/runtime/clear", json={})).status_code in (404, 405)
        await c.post(BASE + "/practice/end")
        assert (await c.get(BASE + "/practice/facts")).json()["running"] is False
        assert (await c.get(BASE + "/practice/state")).status_code == 503
    try:
        run(client_do(go))
    finally:
        if plugin_api._practice is not None:
            plugin_api._practice.close()
            plugin_api._practice = None
    assert show is not None and not show.thread.is_alive()
