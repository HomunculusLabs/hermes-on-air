import base64
import codecs
import random

import pytest

from onair.guard import check_line
from onair.guard.lexicon import dictionary
from onair.guard.rules import encoded_carrier
from onair.types import GuardContext


LINES = [
    'you all make it easy — this room is genuinely nice to host.',
    'and you all make it easy — thank you.',
    'roughly: cozy, unhurried, comfortable. This stream, basically.',
    'basically', 'you all make it easy',
    'Welcome in, everyone! It is lovely to see you.',
    'Thank you for spending your evening here.',
    'Your kindness makes this little room feel like home.',
    'I appreciate all the thoughtful questions today.',
    'Take your time and settle in comfortably.',
    'We can make this together, one step at a time.',
    'Let us start with a clean sheet of paper.',
    'First, draw a small circle near the center.',
    'The next step is to add a gentle curve.',
    'Notice how the colors change in the light.',
    'We can always undo a mistake and try again.',
    'There is no rush; learning takes practice.',
    'Try holding the brush a little more loosely.',
    'Now let the paint dry before the next layer.',
    'A warm light can make the scene feel softer.',
    'I like how this part is coming together.',
    'Thanks for sharing your ideas with us.',
    'That is a really helpful observation.',
    'We will take a short break after this section.',
    'Feel free to follow along at your own pace.',
    'The first version does not have to be perfect.',
    'Let us compare these two shapes side by side.',
    'A little contrast helps the edges stand out.',
    'I am glad you are here with us tonight.',
    'This is a good place to pause and check our work.',
    'We can add more detail once the base is ready.',
    'Start small, then adjust until it feels right.',
    'Thank you for making this such a welcoming space.',
    'Every attempt teaches us something useful.',
    'I love the way this shade sits beside the blue.',
    'Let us turn the page and begin another sketch.',
    'Keep the edges soft and the middle bright.',
    'Together we can figure out the next step.',
    'That little change makes a big difference.',
    'You are doing great, so keep going!',
    'A slow and steady rhythm works well here.',
    'We have made some lovely progress today.',
    'Thanks again for your patience and company.',
]


@pytest.mark.parametrize('line', LINES)
def test_plain_host_lines_release(line):
    assert check_line(line, GuardContext()).release


@pytest.mark.parametrize('field,term', [('never_say', 'swordfish'), ('protected_names', 'cohost')])
@pytest.mark.parametrize('encoding', ['base64', 'hex', 'rot13'])
def test_contextual_encoded_terms_withheld(field, term, encoding):
    token = (base64.b64encode(term.encode()).decode() if encoding == 'base64' else
             term.encode().hex() if encoding == 'hex' else codecs.encode(term, 'rot_13'))
    result = check_line(f'Thanks for being here, {token}!', GuardContext(**{field: (term,)}))
    assert not result.release and result.rule == 'encoded_carrier'


def test_seeded_dictionary_lines_are_not_carriers():
    rng = random.Random(20261104)
    words = sorted(dictionary())
    ctx = GuardContext()
    for _ in range(2000):
        line = ' '.join(rng.choice(words) + rng.choice(['', '', ',', '.', '!', ';'])
                        for _ in range(rng.randint(1, 12)))
        assert not encoded_carrier(line, ctx), line
