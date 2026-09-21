"""Recall re-ranking with JEV.

Iris's default lane ranks a shortlist with a *hand-tuned* score
(`0.6·vector + 0.4·FTS`, then decay × importance). The eval lab showed that
score losing to plain cosine similarity on its own corpus, because a
0.6/0.4 fusion is a proxy for "is this relevant?" — a judgment a System One
model makes directly and in one request.

Pattern: https://docs.typesafe.ai/cookbooks/rerank_typesafe — retrieve a
cheap shortlist, then ask one grounded yes/no question per candidate and sort
by the answer. Iris batches every candidate into a *single* request (the
parallel-questions pattern from
https://docs.typesafe.ai/cookbooks/semantic_find, where one request scores
218 candidates), so the cost of a rerank is one round trip, not N.

Composition rule: JEV supplies the **relevance** term only. The deterministic
policy multipliers (recency decay, importance) stay in `MemoryIndex`, because
those encode Iris's product semantics — what she is allowed to forget — and
must never be outsourced to a model. See
https://docs.typesafe.ai/patterns/composite-scoring.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from iris.config import settings
from iris.jev.client import JevClient, noul

log = logging.getLogger("iris.jev.recall")

# One character budget for the whole rerank state, well inside Jev's 32k-token
# `state` limit (docs/models) even with 20 candidates.
_CANDIDATE_CHARS = 700


class JevReranker:
    """Re-ranks a recall shortlist by asking "would this answer the query?"."""

    def __init__(
        self,
        jev: JevClient | None,
        *,
        enabled: bool | None = None,
        max_candidates: int | None = None,
        blend: float | None = None,
    ) -> None:
        self.jev = jev
        self._enabled = enabled
        self.max_candidates = max_candidates if max_candidates is not None else settings.jev_rerank_candidates
        self.blend = blend if blend is not None else settings.jev_rerank_blend

    @property
    def enabled(self) -> bool:
        if self._enabled is not None:
            return bool(self._enabled) and self.jev is not None
        return bool(settings.jev_rerank_enabled and self.jev is not None and self.jev.enabled)

    async def relevance(self, query: str, candidates: Sequence[str]) -> list[float] | None:
        """A probability per candidate that it answers `query`.

        Returns one score per *scored* candidate — a prefix of `candidates`,
        capped at `max_candidates`. The caller leaves the shortlist tail on its
        deterministic score, which is safe because the cap always exceeds the
        number of hits any lane returns.

        Returns `None` when disabled, empty, or on any JEV failure — callers
        keep their deterministic ordering in that case.
        """
        if not self.enabled or not query.strip() or not candidates:
            return None
        head = list(candidates)[: max(1, self.max_candidates)]
        # One question per candidate, all in one request. Each question names its
        # own candidate by index: Jev resolves backticked paths into the state,
        # and questions are evaluated independently, so nothing leaks between them.
        questions = {
            f"c{i}": noul(
                f"Could `candidates[{i}].text` be a stored memory that answers `query`? "
                "Judge this candidate in isolation: does it state the information the query asks for?",
                true="The candidate states information the query is asking about.",
                false="The candidate is only on a related topic, or answers a different question.",
            )
            for i in range(len(head))
        }
        state = {
            "query": query,
            "candidates": [
                {"id": i, "text": (text or "")[:_CANDIDATE_CHARS]} for i, text in enumerate(head)
            ],
        }
        answers = await self.jev.ask(state, questions)  # type: ignore[union-attr] - guarded by `enabled`
        if answers is None:
            return None
        return [answers.noul(f"c{i}") for i in range(len(head))]

