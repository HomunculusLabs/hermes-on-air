import pytest

from onair.guard import check_line
from onair.system_text import system_text_category
from onair.types import GuardContext


@pytest.mark.parametrize(("text", "category"), [
    ("Billing or credits exhausted: HTTP 429: quota reached", "rate_limited"),
    ("Billing or credits exhausted: quota reached", "billing"),
    ("Example reported that billing, credits, or account entitlement is exhausted for model.", "billing"),
    ("HTTP 503: unavailable", "provider_error"),
    ("The service rejected the request.\n\nProvider said: unavailable", "provider_error"),
    ("The service is rate-limiting requests right now", "rate_limited"),
    ("Your request was not processed. Send it again if you still want me to carry it out.", "notice"),
    ("This turn did not complete. Some actions may already have run", "notice"),
    ("⚠️ Processing completed but no response was generated.", "empty"),
    ("This conversation is too long for the model", "notice"),
    ("Sorry, I encountered an error.", "notice"),
    ("(No response generated)", "empty"),
])
def test_known_system_text_is_withheld(text, category):
    assert system_text_category(text) == category
    verdict = check_line(text, GuardContext())
    assert not verdict.release and verdict.rule == "system_text"


def test_empty_and_normal_reply():
    assert system_text_category("   ") == "empty"
    assert system_text_category("Welcome to the show!") is None
    assert check_line("Welcome to the show!", GuardContext()).release
