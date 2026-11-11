from onair.guard import check_line
from onair.guard.rules import armed_window
from onair.types import ChatMsg, GuardContext


def test_foreign_meaning_request():
    msg = ChatMsg('test', 'test', '1', '1', 'viewer_42', 'what does bonjour mean?', 0)
    ctx = GuardContext(chat_window=(msg,))
    assert armed_window(ctx)
    assert not check_line('It means hello', ctx).release


def test_plain_say_request_arms_readout():
    msg = ChatMsg('test', 'test', '1', '1', 'viewer_42', 'say potato', 0)
    ctx = GuardContext(chat_window=(msg,))
    assert armed_window(ctx)
    assert not check_line('It says something', ctx).release


def test_json_not_spoken():
    assert not check_line('{"action": "stop"}', GuardContext()).release
