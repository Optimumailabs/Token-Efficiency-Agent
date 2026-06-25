"""Word-level diff rendering for prompt iterations.

Given two versions of a prompt, produce a red/green diff:
- removed words (present in the old, gone in the new) are marked red,
- added words (new in the updated version) are marked green.

Two renderers, both from the standard library only:
- ``diff_html`` for the dashboard and any web view,
- ``diff_ansi`` for the terminal.

The tokeniser for diffing splits on whitespace boundaries but keeps the
whitespace, so reconstruction is exact and only the words differ.
"""

from __future__ import annotations

import html
import re
from difflib import SequenceMatcher


def _split_words(text: str) -> list[str]:
    """Split into words and the whitespace between them, keeping both so the
    pieces can be concatenated back into the original string."""
    return re.findall(r"\s+|\S+", text)


# Word-level diff is O(n*m); above this many pieces per side it gets slow
# enough to matter in a dashboard. Beyond the cap we fall back to a coarse
# whole-text replace, which still renders red (old) then green (new).
_MAX_DIFF_PIECES = 4000


def diff_ops(old: str, new: str):
    """Yield (tag, text) tuples where tag is 'equal', 'delete', or 'insert'.
    A 'replace' is emitted as a delete followed by an insert so each side keeps
    its own colour.

    For very large inputs the word-level matcher is skipped in favour of a
    coarse whole-text replace, to keep rendering fast. Identical large inputs
    still short-circuit to a single 'equal'."""
    a = _split_words(old)
    b = _split_words(new)
    if len(a) > _MAX_DIFF_PIECES or len(b) > _MAX_DIFF_PIECES:
        if old == new:
            yield ("equal", old)
        else:
            if old:
                yield ("delete", old)
            if new:
                yield ("insert", new)
        return
    sm = SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            yield ("equal", "".join(a[i1:i2]))
        elif tag == "delete":
            yield ("delete", "".join(a[i1:i2]))
        elif tag == "insert":
            yield ("insert", "".join(b[j1:j2]))
        elif tag == "replace":
            yield ("delete", "".join(a[i1:i2]))
            yield ("insert", "".join(b[j1:j2]))


def diff_html(old: str, new: str) -> str:
    """Render the diff as an HTML fragment. Removed text is red with a
    strikethrough; added text is green. Everything is HTML-escaped."""
    out = []
    for tag, text in diff_ops(old, new):
        esc = html.escape(text)
        if tag == "equal":
            out.append(f"<span class='eq'>{esc}</span>")
        elif tag == "delete":
            out.append(f"<span class='del'>{esc}</span>")
        elif tag == "insert":
            out.append(f"<span class='ins'>{esc}</span>")
    return "".join(out)


# ANSI colours for terminal rendering.
_RED = "\033[9;31m"     # strike + red
_GREEN = "\033[32m"     # green
_RESET = "\033[0m"


def diff_ansi(old: str, new: str) -> str:
    """Render the diff for a terminal: removed text red and struck through,
    added text green."""
    out = []
    for tag, text in diff_ops(old, new):
        if tag == "equal":
            out.append(text)
        elif tag == "delete":
            out.append(f"{_RED}{text}{_RESET}")
        elif tag == "insert":
            out.append(f"{_GREEN}{text}{_RESET}")
    return "".join(out)


def diff_stats(old: str, new: str) -> dict:
    """Count added and removed words (whitespace pieces are ignored)."""
    added = removed = 0
    for tag, text in diff_ops(old, new):
        words = [w for w in _split_words(text) if w.strip()]
        if tag == "insert":
            added += len(words)
        elif tag == "delete":
            removed += len(words)
    return {"words_added": added, "words_removed": removed}
