"""Unknown show state must not be reported as a stopped show or new start."""
from onair import setup_agent, show_runtime


def test_owner_command_reports_unknown(monkeypatch):
    monkeypatch.setattr(show_runtime, 'status', lambda eng: {
        'running': None, 'state': 'unknown', 'show': None, 'pending_show_plan': None,
        'start_next_action': "Show state cannot be verified. Use the owning agent's Stop or End."})
    text = setup_agent._show_command(None, None, ['status'])
    assert 'cannot be verified' in text
    assert 'None' not in text
