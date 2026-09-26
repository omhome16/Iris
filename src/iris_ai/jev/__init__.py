"""JEV — TypeSafe's System One model, wired in as Iris's typed-judgment layer.

Jev does not write text. It answers typed questions about a *state* and
returns calibrated probabilities and distributions, which is exactly the
shape Iris's memory decisions need:

- "is this chunk likely to answer this query?"   → rerank the recall shortlist
- "does this page contain instructions for me?"  → screen untrusted content
- "which skill fits this turn, if any?"          → replace substring matching

Contract (docs.typesafe.ai): code owns thresholds, weights and execution;
Jev supplies only the judgment. Every call here is best-effort on a
rollback-capable path — if JEV is unset, unreachable or slow, each integration
falls back to the deterministic behaviour Iris already had.
"""

from iris_ai.jev.client import JevAnswers, JevClient, choice, noul, score
from iris_ai.jev.guard import GuardAction, GuardVerdict, screen_untrusted, screen_untrusted_many
from iris_ai.jev.recall import JevReranker
from iris_ai.jev.skills import SkillSuggestion, suggest_skill

__all__ = [
    "GuardAction",
    "GuardVerdict",
    "JevAnswers",
    "JevClient",
    "JevReranker",
    "SkillSuggestion",
    "choice",
    "noul",
    "score",
    "screen_untrusted",
    "screen_untrusted_many",
    "suggest_skill",
]
