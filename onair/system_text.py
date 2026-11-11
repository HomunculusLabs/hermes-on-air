"""Known Hermes-authored non-reply text. This is a backstop, not a provenance proof."""
from __future__ import annotations

import re

# Sources: agent/conversation_loop.py::_billing_terminal_label and
# _billing_or_entitlement_message; agent/turn_failure_copy.py failed-turn notices and
# _SITE_COPY; gateway/run.py::_normalize_empty_agent_response; locales/en.yaml
# gateway.errors and gateway.notify. The symbol/directive row mirrors the existing
# onair/stage.py::SYSTEM_NOTICE. Search the whole delivery BEFORE splitting.
_PATTERNS = (
    ("empty", r"\(empty\)|\(No response generated\)|Processing completed but no response was generated|didn't produce a reply this time, even after retries"),
    ("rate_limited", r"\b(?:HTTP\s+429|error code:\s*429|rate-limited every|is rate-limiting requests right now)\b"),
    ("billing", r"\b(?:Billing or credits exhausted:|Provider reported usage/credit exhaustion|reported that billing, credits, or account entitlement is exhausted|reported that your .{0,80}subscription usage (?:may be |is )exhausted)"),
    ("provider_error", r"\b(?:HTTP\s+[45]\d\d|error code:\s*[45]\d\d|Hermes hit (?:repeated errors|an internal error)|The connection to .{1,80} kept dropping|sent back an empty or broken reply|The response was still streaming when agent\.stream_max_lifetime)\b|(?m:^\s*(?:Provider said|Details):)"),
    ("notice", r"(?:Your request was not processed\. Send it again|This turn did not complete\. Some actions|An automated delivery was interrupted|This conversation (?:has grown too long|\(including attachments\) has grown too large|is too long for)|The model(?:'s reply|'s action| server rejected)|Hermes was shutting down and stopped this turn|Sorry, I encountered an error|\(No response generated\)|\(empty\))"),
    ("notice", r"(?m)^\s*(?:[⚡⏳⏩💾⚠❌🔄🛑⏹✅ℹ🔔📎🔧⚙]|\[\[|MEDIA:)"),
)
PATTERNS = tuple((category, re.compile(pattern, re.IGNORECASE)) for category, pattern in _PATTERNS)


def system_text_category(text: str) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return "empty"
    for category, pattern in PATTERNS:
        if pattern.search(text):
            return category
    return None
