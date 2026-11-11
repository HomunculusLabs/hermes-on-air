"""Real Chromium render of the presentation page, driven by the real tools (one browser start, no recording).

Opt-in: needs ONAIR_PLAYWRIGHT (an existing playwright package path) and node on PATH; nothing is installed.
Run it alone under the memory scope described in docs/presentation.md. Screenshots go to ONAIR_PRESENT_SHOTS.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import make

HERE = Path(__file__).resolve().parent / "browser"
# True once the page has applied its window scale (set by the page script on load and on every resize).
SCALED = "getComputedStyle(document.getElementById('stage')).getPropertyValue('--window').trim() === '%s'"
pytestmark = pytest.mark.skipif(not os.environ.get("ONAIR_PLAYWRIGHT"), reason="set ONAIR_PLAYWRIGHT to an existing playwright")


def call(ctx, name, args=None):
    return json.loads(ctx.tools[name]["handler"](args or {}))


def test_page_renders_program_changes_as_text(obs, tmp_path):
    ctx, _ = make(obs.url)
    url = call(ctx, "onair_present_publish", {"sample": True})["surface"]["url"]
    out = Path(os.environ.get("ONAIR_PRESENT_SHOTS") or tmp_path / "shots")
    env = dict(os.environ, ONAIR_PRESENT_URL=url, ONAIR_OUT=str(out))
    node = subprocess.Popen(["node", "--max-old-space-size=1536", str(HERE / "page.cjs")], cwd=HERE, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    results = {}

    def step(name, expect, shot=True, viewport=None):
        node.stdin.write(json.dumps({"name": name, "expect": expect, "shot": shot, "viewport": viewport}) + "\n")
        node.stdin.flush()
        results[name] = json.loads(node.stdout.readline())
        # Any driver error (including a failed screenshot) fails the step; a lost capture is not a pass.
        assert not results[name].get("err"), results[name]
        return results[name]

    try:
        assert json.loads(node.stdout.readline()).get("ready")
        r = step("slide-title", "document.getElementById('heading').textContent === 'Show and tell'")
        assert r["ok"] and r["value"]["mode"] == "slides" and r["value"]["media"] == 0 and not r["errors"], r
        for viewport, right in [([1920, 1080], 1488.2), ([1280, 720], 992.2)]:
            r = step('title-safe-' + str(viewport[0]),
                     "['heading','subtitle'].every(id => document.getElementById(id).getBoundingClientRect().right <= " + str(right) + ")",
                     shot=False, viewport=viewport)
            assert r['ok'], r
        step('restore-size', 'window.innerWidth === 1920', shot=False, viewport=[1920, 1080])
        call(ctx, "onair_present_control", {"action": "goto", "index": 2})
        r = step("slide-code", "document.getElementById('code').textContent.includes('def greet(name):')")
        assert r["ok"] and 'print(greet("chat"))' in r["value"]["code"], r
        call(ctx, "onair_present_publish", {"deck": {"title": "Literal", "slides": [
            {"kind": "bullets", "heading": "<b>not bold</b>", "bullets": ["<script>alert(1)</script>", "plain"]}]}})
        r = step("literal-text", "document.getElementById('heading').textContent === '<b>not bold</b>'")
        assert r["ok"] and r["value"]["injected"] == 0, r
        # A slide at the size limits shrinks its text until it is whole, and stays out of the caption area
        # (the lowest 250 of 1080 pixels, where the overlay's two caption lines sit).
        long = ("A bullet that runs all the way to the two hundred character limit " * 4)[:200]
        call(ctx, "onair_present_publish", {"deck": {"title": "Long", "slides": [
            {"kind": "bullets", "heading": ("A long heading " * 9)[:120], "bullets": [long] * 8},
            {"kind": "code", "heading": "Sixty lines", "language": "python", "code": "\n".join(["value = compute(value)"] * 60)}]}})
        r = step("long-bullets", "document.getElementById('bullets').children.length === 8 "
                                 "&& document.getElementById('stage').dataset.fit")
        assert r["ok"] and r["value"]["fit"] == "whole" and r["value"]["lowest"] <= 830, r
        # Code that cannot fit at the smallest size is cut at its end, inside its box, never into the caption area.
        call(ctx, "onair_present_control", {"action": "next"})
        r = step("long-code", "document.getElementById('heading').textContent === 'Sixty lines' "
                              "&& document.getElementById('stage').dataset.fit")
        assert r["ok"] and r["value"]["fit"] == "cut" and r["value"]["lowest"] <= 830, r
        # A plain browser window smaller than the design size shows the whole stage, scaled, not its top-left part.
        r = step("small-window", "window.innerWidth === 1280 && " + SCALED % "0.6667", viewport=[1280, 720])
        assert r["ok"] and r["value"]["stage"] == [0, 0, 1280, 720], r
        r = step("tall-window", "window.innerWidth === 1000 && " + SCALED % "0.5208", shot=False, viewport=[1000, 900])
        left, top, right, bottom = r["value"]["stage"]
        assert r["ok"] and (left, right) == (0, 1000) and top >= 0 and bottom <= 900 and abs((top + bottom) - 900) <= 2, r
        assert step("design-window", "window.innerWidth === 1920 && " + SCALED % "1.0000", shot=False, viewport=[1920, 1080])["value"]["stage"] == [0, 0, 1920, 1080]
        call(ctx, "onair_present_control", {"action": "standby", "title": "Back in five"})
        r = step("standby", "document.getElementById('stage').dataset.mode === 'standby'")
        assert r["ok"] and r["value"]["standby"] == "Back in five", r
        call(ctx, "onair_present_control", {"action": "resume"})
        assert step("resumed", "document.getElementById('stage').dataset.mode === 'slides'", shot=False)["ok"]
        call(ctx, "onair_present_control", {"action": "stop_serving"})
        r = step("disconnected", "document.getElementById('stage').dataset.mode === 'disconnected'")
        assert r["ok"] and r["value"]["slideHidden"] is True and r["value"]["standbyHidden"] is False, r
        node.stdin.write(json.dumps({"name": "quit"}) + "\n")
        node.stdin.flush()
        closed = json.loads(node.stdout.readline())
        assert closed == {"closed": True, "browserStarts": 1, "recording": False}
    finally:
        (out / "report.json").parent.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps(results, indent=1))
        if node.poll() is None:
            node.kill()
        node.wait(10)
