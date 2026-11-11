import codecs
import subprocess
import sys

import pytest

from onair.guard import check_line, check_name
from onair.types import GuardContext


def canary(encoded):
    return codecs.decode(encoded, 'rot_13')


SLUR = canary('avttre')
PROFANITY = canary('shpx')


@pytest.mark.parametrize('mode', ['mask', 'block', 'allow'])
def test_slurs_always_blocked(mode):
    assert check_line(SLUR, GuardContext(), profanity=mode).rule == 'slur'
    assert check_line('.'.join(SLUR), GuardContext(), profanity=mode).rule == 'slur'


@pytest.mark.parametrize('mode', ['mask', 'block'])
def test_profanity_blocks_unmasked_line(mode):
    assert check_line(PROFANITY, GuardContext(), profanity=mode).rule == 'profanity'


def test_default_masks_by_blocking_and_allow_mode_permits_profanity():
    assert check_line(PROFANITY, GuardContext()).rule == 'profanity'
    assert check_line(PROFANITY, GuardContext(), profanity='allow').release
    assert check_line('hello everyone', GuardContext()).release


def test_slur_wins_over_profanity():
    assert check_line(PROFANITY + ' ' + SLUR, GuardContext()).rule == 'slur'


@pytest.mark.parametrize('name', [SLUR, PROFANITY], ids=['slur', 'profanity'])
def test_names_always_block_even_exact_protected_name(name):
    assert not check_name(name, GuardContext()).release
    assert not check_name(name, GuardContext(protected_names=(name,))).release


def test_invalid_profanity_policy_fails_closed():
    assert check_line('hello', GuardContext(), profanity='unexpected').rule == 'fail_closed'


def test_allow_does_not_bypass_other_guard_rules():
    assert check_line(PROFANITY, GuardContext(never_say=(PROFANITY,)), profanity='allow').rule == 'never_say'


def test_wordfilter_error_fails_closed(monkeypatch):
    import onair.wordfilter

    def fail(_text):
        raise RuntimeError('filter failure')

    monkeypatch.setattr(onair.wordfilter, 'hits', fail)
    assert check_line('hello', GuardContext(), profanity='allow').rule == 'fail_closed'
    assert check_name('viewer', GuardContext()).rule == 'fail_closed'


def test_wordfilter_import_first_avoids_circular_import():
    subprocess.run([sys.executable, '-c', 'import onair.wordfilter; import onair.guard'], check=True)
