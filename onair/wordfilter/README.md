# Word filter

`hits(text)` returns a sorted list of immutable `Hit(tier, start, end)` values; offsets are Python string indices into the original text, with an exclusive end. Tiers are `slur` and `profanity`. Multiple overlapping hits are possible. `mask(text)` replaces overlapping **profanity** spans with `[filtered]`; slur hits are not masked by this function and need separate caller policy. Neither function performs I/O.

## Data and attribution

The encoded list is a deliberately small, manually selected English-only subset of the English list in [LDNOOBW / List of Dirty, Naughty, Obscene and Otherwise Bad Words](https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words), originally provided by Shutterstock and contributors. That list is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); the full license is included in `LICENSE-CC-BY-4.0.txt`. Our changes are selection, tier classification, lowercasing and ROT13 encoding; inflections are generated at runtime. ROT13 is only an exposure-reduction measure, not encryption.

`seed.py` adds a separate parent-curated ROT13 seed; the runtime unions it with
the licensed subset without replacing it. The seed's `profanity` entry follows
the configurable profanity policy; its other entries use the always-on `slur`
tier, including harassment terms. Some entries have ordinary non-abusive uses;
the filter does not use context to distinguish those senses.
Nine seed entries also occur in the MIT-licensed
[better_profanity word list](https://github.com/snguyenthanh/better_profanity/blob/master/better_profanity/profanity_wordlist.txt);
its license is included as `LICENSE-better-profanity.txt`. The remaining
abbreviation (`xlf`, ROT13) is an additional parent-curated harassment term.

`tools/generate.py` regenerates `lists.py` from a separately curated plaintext TSV **stored outside the repository**. Each line is `tier<TAB>word`, where tier is `slur` or `profanity`, and the word consists of 3–32 lowercase ASCII letters. For example:

```
python onair/wordfilter/tools/generate.py /outside/repository/curated.tsv > onair/wordfilter/lists.py
```

The TSV contains sensitive terms; do not add it to git. Obtain upstream English source and curate and classify entries locally; the generator does not fetch data. Do not treat the tier labels as an objective social taxonomy.

## Bounds

This is a small lexical backstop, **not** a comprehensive or context-sensitive safety classifier. It misses unlisted terms, languages, paraphrases, arbitrary suffixes, multiword terms, and obfuscations outside the supported patterns. Separators (including underscores) are limited to three per letter gap; runs are limited to eight per letter. Adjacent letters or digits prevent a whole-word match, so adding digits to either end can evade detection. Masked forms use `*` or `?` for one character each, require two visible characters, and are accepted only if exactly one listed form matches. Such ambiguity is evaluated against this small list, not every possible English word; even an ambiguous candidate containing a slur is not reported. Completely masked forms are not reported either.

Generated forms that collide with the ordinary words `spices`, `spiced`, `spicing`, `dicker`, and `dickers` are intentionally excluded from generated forms, including their obfuscated spellings: consequently the corresponding suffixes used as slurs or profanity can escape. Other patterns may still match parts of separated spellings. Whole-word matching avoids substring false positives such as Scunthorpe, but legitimate uses of listed words (including regional meanings of slurs) and letter sequences spanning separate words may still match. Folding is per character to preserve offsets, so some context-dependent Unicode normalization may be missed. Tests demonstrate examples, not a measured production false-positive rate.
