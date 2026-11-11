from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_voice_guide_is_linked_and_describes_current_behavior():
    guide = (ROOT / 'docs/voice.md').read_text(encoding='utf-8')
    for phrase in ('hermes onair voice-test', '{input_path}', '{output_path}', 'tts:',
                   'text_to_speech', 'voice_ahead', 'not supported', 'OBS', '24 hours'):
        assert phrase in guide
    for name in ('index.md', 'quickstart.md', 'talk.md'):
        assert 'voice.md' in (ROOT / 'docs' / name).read_text(encoding='utf-8')
    skill = (ROOT / 'onair/skills/first-stream/SKILL.md').read_text(encoding='utf-8')
    assert 'docs/voice.md' in skill
    assert 'voice-test' in skill


def test_manifest_declares_the_existing_voice_switch():
    from onair.yamlio import safe_load
    manifest = safe_load((ROOT / 'plugin.yaml').read_text(encoding='utf-8'))
    assert manifest['config_schema']['voice']['type'] == 'bool'
    assert manifest['config_schema']['voice']['default'] is True
    assert 'voice_ahead' not in manifest['config_schema']
