"""The declared minimum's manifest warning does not disable runtime middleware."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_manifest_warning_is_exact_and_does_not_change_minimum():
    doc = (ROOT / "docs/compatibility.md").read_text()
    manifest = (ROOT / "plugin.yaml").read_text()
    assert "Plugin on-air: unknown manifest field(s) ignored: provides_middleware (newer manifest schema or typo; plugin still loads)" in doc
    assert "ctx.register_middleware" in doc
    assert 'requires_hermes: \">=0.22.7\"' in manifest
    assert "provides_middleware: [llm_execution]" in manifest
