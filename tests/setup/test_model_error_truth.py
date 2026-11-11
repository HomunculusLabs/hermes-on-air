"""Failure labels cannot diagnose provider outages or recommend bypassing admission."""
from types import SimpleNamespace

from onair.show_runtime import AgentShow
from onair.setup_engine import PreviewShow


class Client:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def complete(self, **kwargs):
        if self.error:
            raise self.error
        return self.result


def test_show_failure_and_success_are_distinct(tmp_path):
    body = {"agent_name": "Fixture", "host_name": "Host", "style": {}}
    show = AgentShow(body=body, persona="fixture", model_client=Client(error=RuntimeError("local cap")),
                     tts_dispatch=None, data_dir=tmp_path)
    assert show._complete("hello") == ""
    assert show.last_error == "The model call failed. The show keeps listening."
    assert show.model_errors == 1
    show.model_client = Client(result=SimpleNamespace(text="Hello", provider="test", model="model"))
    assert show._complete("hello") == "Hello"


def test_preview_failure_and_empty_are_not_provider_diagnosis():
    preview = object.__new__(PreviewShow)
    preview.persona, preview.max_tokens = "fixture", 10
    preview.draft_error = preview.model_attribution = ""
    preview.model_client = Client(error=RuntimeError("local cap"))
    assert preview._draft_once("hi") == ""
    assert preview.draft_error == "The model call failed. No preview reply was generated."
    preview.model_client = Client(result=SimpleNamespace(text="", provider="test", model="model"))
    assert preview._draft_once("hi") == ""
    assert preview.draft_error == "The model call returned no preview reply."
    preview.model_client = Client(result=SimpleNamespace(text="hello", provider="test", model="model"))
    assert preview._draft_once("hi") == "hello"
