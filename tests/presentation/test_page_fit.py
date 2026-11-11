"""The presentation page fits itself: the stylesheet and the script must agree on how.

The real Chromium check of the geometry is opt-in (test_page_browser.py). This runs everywhere and pins the
contract between the two files, so renaming one side cannot silently leave slides cropped or over the captions.
"""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "onair" / "presentation" / "web"
CSS = (WEB / "present.css").read_text(encoding="utf-8")
JS = (WEB / "app.js").read_text(encoding="utf-8")


def rule(selector: str) -> str:
    found = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert found, f"no rule for {selector}"
    return found.group(1)


def test_the_stage_scales_to_the_window_that_shows_it():
    assert "scale(var(--window, 1))" in rule("#stage")
    assert "setProperty('--window'" in JS and "addEventListener('resize'" in JS
    # The page itself must not pin the window to the design size, or a smaller window crops the slide.
    assert "1920px" not in rule("html, body")


def test_slide_text_shrinks_through_the_shared_property():
    assert "setProperty('--fit'" in JS
    for selector in ("h1", "#subtitle", "#bullets", "#body", "#code-wrap pre"):
        assert "var(--fit, 1)" in rule(selector), selector


def test_the_caption_area_is_kept_clear_of_slide_text():
    padding = re.search(r"padding:\s*(\d+)px\s+(\d+)px\s+(\d+)px", rule("#stage"))
    assert padding and int(padding.group(3)) >= 250
    footer = rule("#footer")
    assert "top:" in footer and "bottom:" not in footer
    assert "overflow: hidden" in rule("#slide")
