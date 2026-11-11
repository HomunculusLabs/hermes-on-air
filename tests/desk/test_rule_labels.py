"""The desk label catalog must cover the rule IDs actually emitted by the guards.

Use AST rather than searching strings: detector descriptions and test payloads are
not rule IDs. Unknown/dynamic constructor expressions fail closed so an emitter
refactor cannot silently make this completeness check vacuous.
"""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "dashboard/dist/rule-labels.json"
SOURCES = (
    *sorted((ROOT / "onair/guard").glob("*.py")),
    ROOT / "onair/chat/gate.py",
    ROOT / "onair/chat/shapes.py",
    *sorted((ROOT / "onair/wordfilter").glob("*.py")),
    ROOT / "onair/types.py",  # shared fail-closed constructors
)
# Sanity anchors catch accidental loss of source files or constructor discovery.
ANCHORS = {"allow", "fail_closed", "never_say", "slur", "profanity",
           "decoded_name", "malformed", "decode_request", "chatter"}


def emitted_rules():
    result = set()
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else None
            )
            if name not in {"Verdict", "GateVerdict"}:
                continue
            position = 1 if name == "Verdict" else 2
            keyword = next((k.value for k in node.keywords if k.arg == "rule"), None)
            value = keyword if keyword is not None else (
                node.args[position] if len(node.args) > position else None
            )
            assert isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value, (
                f"Review dynamic {name} rule at {path.relative_to(ROOT)}:{node.lineno}"
            )
            result.add(value.value)
    return result


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, f"Duplicate rule label: {key!r}"
        result[key] = value
    return result


def test_all_emitted_rules_have_nonempty_human_labels():
    rules = emitted_rules()
    assert ANCHORS <= rules, f"Rule discovery lost anchors: {sorted(ANCHORS - rules)}"
    labels = json.loads(CATALOG.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    assert isinstance(labels, dict) and labels, "Expected a nonempty rule-to-label JSON object"
    missing = rules - labels.keys()
    assert not missing, f"Missing rule labels: {sorted(missing)}"
    invalid = [key for key, value in labels.items() if not isinstance(value, str) or not value.strip()]
    assert not invalid, f"Labels must be nonempty strings: {sorted(invalid)}"


def test_browser_embedded_catalog_matches_json():
    # Plugin loader only guarantees the entry script and CSS, not a JSON asset URL.
    source = (ROOT / "dashboard/dist/index.js").read_text(encoding="utf-8")
    marker = "  const RULE_LABELS = "
    assert source.count(marker) == 1
    embedded = source.split(marker, 1)[1].split(";\n", 1)[0]
    assert json.loads(embedded, object_pairs_hook=unique_object) == json.loads(
        CATALOG.read_text(encoding="utf-8"), object_pairs_hook=unique_object
    )
