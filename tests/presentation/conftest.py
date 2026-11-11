import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fake_obs import FakeObs  # noqa: E402


class FakeCtx:
    """Stands in for the Hermes PluginContext: real register_* call shapes, a configurable session surface."""

    def __init__(self, surface="cli", choice="go"):
        self.surface = surface
        self.choice = choice
        self.tools, self.commands, self.questions = {}, {}, []

    def session_surface(self):
        return self.surface

    def register_tool(self, name, toolset, schema, handler, description="", emoji="", **_kw):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, "description": description}

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = handler

    def ask_choice(self, question, choices):
        self.questions.append((question, choices))
        return self.choice


@pytest.fixture(autouse=True)
def owner_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("ONAIR_OBS_PASSWORD", raising=False)
    yield tmp_path / "home"
    from onair.presentation import teardown
    teardown()


@pytest.fixture
def obs():
    fake = FakeObs().start()
    yield fake
    fake.stop()


def make(obs_url, surface="cli", choice="go", **cfg):
    import onair.presentation as pres
    ctx = FakeCtx(surface, choice)
    config = {"obs_url": obs_url, "present_port": 0, "start_deadline_s": 1.5, "stop_deadline_s": 1.5, "poll_s": 0.05}
    config.update(cfg)
    done = pres.register(ctx, config=config)
    return ctx, done


@pytest.fixture
def free_url():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"ws://127.0.0.1:{port}"


os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
