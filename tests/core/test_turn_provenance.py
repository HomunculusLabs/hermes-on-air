"""The agent hook proof must bind one exact successful response to one stage turn."""
import pytest

from onair.turn_provenance import TurnProof, current_proof, observe_end, observe_output, observe_start

IDENTITY = {"platform": "on_air", "session_id": "session", "task_id": "task", "turn_id": "agent-turn"}
GOOD = {"completed": True, "failed": False, "interrupted": False,
        "turn_exit_reason": "text_response(finish_reason=stop)"}


@pytest.fixture
def proof():
    proof = TurnProof("stage-turn", "a unique prompt")
    token = current_proof.set(proof)
    yield proof
    current_proof.reset(token)


def start(**changes):
    observe_start(**{**IDENTITY, **changes}, user_message="a unique prompt")


def output(text="A real answer.", **changes):
    observe_output(**{**IDENTITY, **changes}, assistant_response=text, conversation_history=[{"role": "user", "content": "prompt"}, {"role": "assistant", "content": text}])


def end(**changes):
    observe_end(**{**IDENTITY, **GOOD, **changes})


def test_requires_same_successful_turn_and_exact_text(proof):
    start()
    output()
    end()
    assert proof.matches("stage-turn", "A real answer.")
    assert not proof.matches("another-turn", "A real answer.")
    assert not proof.matches("stage-turn", "A real answer.\nExtra notice.")


@pytest.mark.parametrize("bad", [
    {"completed": False}, {"failed": True}, {"interrupted": True},
    {"completed": None}, {"failed": None}, {"interrupted": None},
    {"turn_exit_reason": "empty_response_exhausted"},
    {"turn_id": "other-turn"}, {"session_id": "other-session"},
])
def test_failed_interrupted_or_wrong_turn_never_proves_reply(proof, bad):
    start()
    output()
    end(**bad)
    assert not proof.matches("stage-turn", "A real answer.")


def test_missing_or_out_of_order_hooks_fail_closed(proof):
    output()
    end()
    start()
    assert not proof.matches("stage-turn", "A real answer.")


def test_duplicate_end_and_conflicting_identity_fail_closed(proof):
    start()
    output()
    end()
    end()
    assert not proof.matches("stage-turn", "A real answer.")


def test_conflicting_start_and_subagent_do_not_prove(proof):
    start()
    start(turn_id="different")
    output()
    end()
    assert not proof.matches("stage-turn", "A real answer.")


def test_finalizer_appended_text_is_not_certified(proof):
    start()
    observe_output(**IDENTITY, assistant_response="A real answer.\nA verifier notice.",
                   conversation_history=[{"role": "user", "content": "prompt"}, {"role": "assistant", "content": "A real answer."}])
    end()
    assert not proof.matches("stage-turn", "A real answer.\nA verifier notice.")
