"""CLI phrase checks that do not depend on terminal width.

Rich wraps to the console width. A narrow CI terminal turns
``nothing to re-approve`` into ``nothing to \\nre-approve``, so a raw
substring check fails even though the words are all there.
"""

from __future__ import annotations


def flat(text: str) -> str:
    """Collapse whitespace, including newlines Rich inserted while wrapping."""
    return " ".join(text.split())
