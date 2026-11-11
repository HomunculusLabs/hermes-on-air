import pytest
from onair.guard.decoders import split_joined

@pytest.mark.parametrize('joiner', ['1', '②', '\u02bc', '\u200b', '\u0301', '_', '-'])
def test_unicode_joiners(joiner):
    assert split_joined('alpha' + joiner + 'beta') == ['alpha', 'beta']
