"""Generate the encoded list from an externally stored curated TSV.

Usage: python onair/wordfilter/tools/generate.py /outside/git/curated.tsv > onair/wordfilter/lists.py
Each input line is tier<TAB>lowercase-ascii-word; no source text is written to git.
"""
import codecs
import re
import sys
from pathlib import Path


def main(path: str) -> None:
    entries = set()
    for row in Path(path).read_text(encoding="utf-8").splitlines():
        tier, word = row.split("\t")
        if tier not in {"slur", "profanity"} or not re.fullmatch(r"[a-z]{3,32}", word):
            raise ValueError("invalid curated entry")
        entries.add((tier, codecs.encode(word, "rot_13")))
    print('"""Generated ROT13 terms; attribution and licensing in README.md."""')
    print("ENTRIES = (")
    for tier, encoded in sorted(entries):
        print(f"    ({tier!r}, {encoded!r}),")
    print(")")


if __name__ == "__main__":
    main(sys.argv[1])
