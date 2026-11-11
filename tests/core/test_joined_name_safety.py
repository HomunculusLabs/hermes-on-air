"""Joined handles must not bypass tier filtering or censor ordinary names."""

import codecs

import pytest

from onair.chat.gate import speakable_name
from onair.guard import check_line, check_name
from onair.stage import StageController
from onair.types import GuardContext


def decoded(value):
    return codecs.decode(value, 'rot_13')


# Keep tier canaries encoded in source, including when embedded in a handle.
PROFANE_NAMES = [decoded('FuvgYbeq'), decoded('ShpxObv'), decoded('shpxobv99')]
SLUR = decoded('avttre')
SLUR_NAMES = [SLUR, f'Big{SLUR}Fan', f'xX{SLUR}Xx', f'{SLUR}hater', f'{SLUR}420']
UNSAFE_NAMES = [(name, 'profanity') for name in PROFANE_NAMES] + [
    (name, 'slur') for name in SLUR_NAMES
]
BENIGN_NAMES = [
    'Dickens_fan', 'ScunthorpeUnited', 'Cassandra', 'Hancock_99', 'Hancock1999',
    'RaccoonKing', 'SpicyTaco', 'viewer_42', 'PixelMoth',
    'xX_Gamer_Xx', 'NightOwl', 'bob_ross_fan',
]


@pytest.mark.parametrize('name,tier', UNSAFE_NAMES, ids=[f'unsafe-{i}' for i in range(len(UNSAFE_NAMES))])
@pytest.mark.parametrize('boundary', ['speakable', 'name_guard', 'alert'])
def test_unsafe_joined_names_are_hidden_across_name_boundaries(name, tier, boundary):
    if boundary == 'speakable':
        assert speakable_name(name) == 'someone in chat'
    elif boundary == 'name_guard':
        verdict = check_name(name, GuardContext())
        assert not verdict.release and verdict.rule == tier
    else:
        assert StageController()._alert_name(name) == 'someone'


@pytest.mark.parametrize('name,tier', UNSAFE_NAMES, ids=[f'unsafe-{i}' for i in range(len(UNSAFE_NAMES))])
@pytest.mark.parametrize('mode', ['mask', 'block', 'allow'])
def test_joined_name_in_spoken_line_respects_tier_and_policy(name, tier, mode):
    verdict = check_line(f'Thanks for being here, {name}!', GuardContext(), profanity=mode)
    if tier == 'profanity' and mode == 'allow':
        assert verdict.release
    else:
        assert not verdict.release and verdict.rule == tier


@pytest.mark.parametrize('name', BENIGN_NAMES)
@pytest.mark.parametrize('boundary', ['speakable', 'name_guard', 'alert', 'line'])
def test_benign_names_remain_speakable_and_releasable(name, boundary):
    expected = name.replace('_', ' ')
    if boundary == 'speakable':
        assert speakable_name(name) == expected
    elif boundary == 'name_guard':
        assert check_name(name, GuardContext()).release
    elif boundary == 'alert':
        assert StageController()._alert_name(name) == expected
    else:
        for mode in ('mask', 'block', 'allow'):
            assert check_line(f'Thanks for being here, {name}!', GuardContext(), profanity=mode).release


def test_priced_base64_shaped_handle_is_withheld_by_encoded_name_guard():
    """Parent ruling: a handle that happens to be base64-shaped (e.g. 'Hancock99') is withheld by
    the pre-existing encoded-name guard. That is the accepted price of the strict decoder, not a
    word-filter hit; similar handles with a separator or another digit count still release."""
    assert not check_name('Hancock99', GuardContext()).release
    assert StageController()._alert_name('Hancock99') == 'someone'
    for name in ('Hancock_99', 'Hancock1999', 'Spock99', 'Peacock99', 'Zelda99'):
        assert check_name(name, GuardContext()).release, name


@pytest.mark.parametrize('root', [decoded('juber'), decoded('fyhg')])
@pytest.mark.parametrize('shape', ['xX{}Xx', '{}hater', 'Big{}Fan'])
def test_sexual_insult_roots_are_scanned_inside_joined_handles(root, shape):
    """Parent addition: these two profanity roots get the inner scan too (raid/gift names)."""
    assert StageController()._alert_name(shape.format(root.capitalize() if shape.startswith('Big') else root)) == 'someone'


@pytest.mark.parametrize('name', ['Shoreline', 'SlothKing', 'Whitehorse'])
def test_inner_scan_neighbours_release(name):
    assert StageController()._alert_name(name) == name
