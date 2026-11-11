import codecs

import pytest

from onair.wordfilter import hits, mask


def word(value):
    return codecs.decode(value, "rot13")


def test_tiers_offsets_and_mask():
    text = "hi " + word("shpx") + " and " + word("avttre") + "!"
    found = hits(text)
    assert [(h.tier, text[h.start:h.end]) for h in found] == [
        ("profanity", word("shpx")), ("slur", word("avttre"))
    ]
    assert mask(text) == "hi [filtered] and " + word("avttre") + "!"


@pytest.mark.parametrize("encoded", ["SH PX", "shpx", "shpxf", "shpxrq", "shpxvat"])
def test_inflections(encoded):
    term = word(encoded.replace(" ", ""))
    assert hits(term)[0].tier == "profanity"


def test_variants_and_offsets():
    term = word("shpx")
    variants = [
        "Ｆ" + term[1:],
        term[0] + "υ" + term[2:],
        ".".join(term),
        " ".join(term),
        term[0] * 3 + term[1:],
        term[0] + "**" + term[-1],
    ]
    for variant in variants:
        text = "x " + variant + "!"
        found = hits(text)
        assert found, repr(variant)
        assert text[found[0].start:found[0].end] == variant
        assert mask(text) == "x [filtered]!"


@pytest.mark.parametrize("text", ["Scunthorpe", "classical", "assignment", "assistant", "cocktail", "passage", "hello world", "a classic lesson"])
def test_benign_controls(text):
    assert hits(text) == []
    assert mask(text) == text


def test_leet_and_expanding_fold_offsets():
    term = word("fuvg")
    variant = term[0] + "h1" + term[-1]
    text = "ﬃ " + variant + "!"
    h, = hits(text)
    assert (h.start, h.end, h.tier) == (2, 6, "profanity")


def test_mask_multiple_and_non_string():
    text = word("shpx") + ", " + word("fuvg")
    assert mask(text) == "[filtered], [filtered]"
    with pytest.raises(TypeError):
        hits(None)


def test_ambiguous_mask_not_guessed():
    assert hits("****") == []


def test_slur_obfuscation_is_reported_but_not_masked():
    variant = ".".join(word("avttre"))
    assert any(h.tier == "slur" for h in hits(variant))
    assert mask(variant) == variant


def test_combining_mark_included_in_original_span():
    text = word("shpx") + "\u0301"
    found = hits(text)
    assert found[0].end == len(text)
    assert mask(text) == "[filtered]"


def test_no_cross_word_substrings_or_newline_joining():
    assert hits(word("shpx") + "ingness") == []
    assert hits("\n".join(word("shpx"))) == []


def test_offset_with_normalized_expansion_inside():
    # A ligature expands before matching while offsets remain original indices.
    text = "x " + word("fuvg")[0] + "h" + "ｉ" + "t!"
    h, = hits(text)
    assert text[h.start:h.end] == text[2:-1]


def test_each_listed_root_and_inflection():
    from onair.wordfilter.lists import ENTRIES
    from onair.wordfilter.seed import SEED

    for tier, encoded in set(ENTRIES) | set(SEED):
        root = word(encoded)
        for text in (root, root + "s"):
            assert any(h.tier == tier and text[h.start:h.end] == text for h in hits(text))


def test_empty_and_unicode_input():
    assert hits("") == []
    assert mask("") == ""
    assert hits("☕ привет 你好") == []


def test_ambiguous_partly_masked_form_is_not_guessed():
    from collections import defaultdict
    from onair.wordfilter import _FORMS

    groups = defaultdict(set)
    for tier, form in _FORMS:
        if len(form) > 3:
            groups[(form[0], form[-1], len(form))].add((tier, form))
    first, last, size = next(key for key, values in groups.items() if len(values) > 1)
    assert hits(first + "*" * (size - 2) + last) == []


def test_guard_capital_i_reading():
    text = word("fuvg").replace("i", "I")
    assert any(h.tier == "profanity" for h in hits(text))


@pytest.mark.parametrize("text", ["spice", "spiced", "spices", "spicing", "dicker", "dickers"])
def test_common_inflection_collisions_are_not_flagged(text):
    assert hits(text) == []


def test_underscore_separated_slur():
    variant = "_".join(word("fcvp"))
    text = "before " + variant + " after"
    assert any(h.tier == "slur" and text[h.start:h.end] == variant for h in hits(text))


def test_separator_bounds_and_digit_boundaries():
    term = word("avttre")
    assert any(h.tier == "slur" for h in hits("___".join(term)))
    assert hits("____".join(term)) == []
    assert hits("1" + term) == []
    assert hits(term + "1") == []


def test_ambiguous_wildcard_may_hide_a_slur():
    # Both a profanity and a slur match this pattern; neither is returned.
    assert hits("s*i*") == []


@pytest.mark.parametrize("encoded,tier", [
    ("ergneq", "slur"), ("ergneqrq", "slur"), ("puvax", "slur"),
    ("tbbx", "slur"), ("pbba", "slur"), ("qlxr", "slur"),
    ("fyhg", "profanity"), ("encr", "slur"), ("encvat", "slur"),
    ("xlf", "slur"),
])
def test_curated_seed_roots_and_policy(encoded, tier):
    text = word(encoded)
    assert any(h.tier == tier and (h.start, h.end) == (0, len(text)) for h in hits(text))
    assert mask(text) == ("[filtered]" if tier == "profanity" else text)


def test_curated_seed_does_not_match_benign_substrings():
    for text in ("raccoon", "Scunthorpe"):
        assert hits(text) == []
        assert mask(text) == text


def test_curated_seed_separated_spelling_is_reported():
    text = ".".join(word("puvax"))
    assert any(h.tier == "slur" and (h.start, h.end) == (0, len(text)) for h in hits(text))


def test_seeded_dictionary_sample_excludes_listed_forms_and_has_no_hits():
    import random
    from onair.guard.lexicon import dictionary
    from onair.wordfilter import _EXCLUDED_FORMS, _WORDS, _forms

    all_forms = set().union(*(_forms(root) for _, root in _WORDS))
    dictionary_words = set(dictionary())
    removed = dictionary_words & all_forms
    assert removed  # This is not a vacuous exclusion.
    # Forms deliberately suppressed for benign collisions are documented separately.
    for term in removed - _EXCLUDED_FORMS:
        assert hits(term), word(term)
    pool = sorted(dictionary_words - all_forms)
    rng = random.Random(20261104)
    for _ in range(2000):
        line = " ".join(rng.choice(pool) + rng.choice(["", "", ",", ".", "!", ";"])
                        for _ in range(rng.randint(1, 12)))
        assert not hits(line), word(line)


def test_shipped_word_list_holds_no_listed_form_in_plain_text():
    """Dictionary words the filter lists ship ROT13-encoded; the dictionary itself is unchanged."""
    import codecs
    from onair.guard import wordlist_en
    from onair.guard.lexicon import dictionary
    from onair.wordfilter import _EXCLUDED_FORMS, _WORDS, _forms

    listed = set().union(*(_forms(root) for _, root in _WORDS)) - set(_EXCLUDED_FORMS)
    plain = set(wordlist_en.WORDS.split())
    concealed = set(codecs.decode(wordlist_en.CONCEALED_ROT13, "rot_13").split())
    assert not plain & listed
    assert concealed and concealed <= listed
    assert not plain & concealed
    assert dictionary() == plain | concealed
    assert len(dictionary()) == wordlist_en.WORD_COUNT
    # the benign forms the filter leaves alone are ordinary dictionary words and stay readable
    assert set(_EXCLUDED_FORMS) & plain


def test_no_tracked_text_file_spells_a_listed_root_in_plain_text():
    """Public-repo hygiene: listed roots appear only ROT13-encoded anywhere in the plugin tree."""
    import re
    from pathlib import Path
    from onair.wordfilter import _WORDS

    root = Path(__file__).resolve().parents[2]
    roots = sorted({w for _, w in _WORDS}, key=len, reverse=True)
    pattern = re.compile(r"(?<![A-Za-z])(?:" + "|".join(map(re.escape, roots)) + r")(?![A-Za-z])", re.IGNORECASE)
    suffixes = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".js", ".cjs", ".css", ".html", ".toml", ".tsv"}
    skip = {".git", "__pycache__", ".pytest_cache", "node_modules"}
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes or skip & set(path.relative_to(root).parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.split("\n"), 1):
            if pattern.search(line):
                found.append(f"{path.relative_to(root)}:{number}")
    assert not found, found[:20]
