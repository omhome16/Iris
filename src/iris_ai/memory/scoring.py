"""Recall scoring — the storage-independent half of retrieval.

Fusion, decay and diversity are *policy*: they decide which remembered things
win, and they must not change when the store behind them does. So they live
here, as pure functions over `MemoryHit`s, and every memory backend calls the
same code. A backend's job is only to produce candidates and their numbers
(a vector score, a keyword score, where they came from) — not to decide what
"relevant" means.

`recency_weight` stays in `memory.index` because it is imported by name from
there (`tests/test_memory_units.py`, `memory/forgetting.py`); moving it would
change import identity for no gain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # `index` imports this module, so a runtime import here is a cycle
    from iris_ai.memory.index import MemoryHit


def normalize(vector: np.ndarray) -> np.ndarray:
    """Unit-normalize a vector; a zero vector passes through as zeros.

    Embeddings from different providers are not guaranteed unit-length, and a
    dot product is only a cosine when both operands are normalized.
    """
    return vector / (np.linalg.norm(vector) or 1.0)


def mmr_select(
    pairs: list[tuple[MemoryHit, np.ndarray]],
    *,
    top_k: int,
    lam: float = 0.7,
) -> list[MemoryHit]:
    """Maximal Marginal Relevance: relevance minus redundancy.

    Deterministic, local, no model calls. Greedily takes the candidate that
    maximizes ``lam * relevance - (1 - lam) * max_similarity(already_selected)``
    so near-duplicates do not crowd out distinct memories.

    When every vector is zero (an FTS-only store with no embedder), redundancy
    is always 0 and this degenerates to a stable top-k by score — which is the
    correct answer when there is no geometry to be diverse in.
    """
    if not pairs:
        return []
    pool = [(hit, normalize(emb)) for hit, emb in pairs]
    selected: list[tuple[MemoryHit, np.ndarray]] = []
    while pool and len(selected) < top_k:
        best: tuple[MemoryHit, np.ndarray] | None = None
        best_val = -1.0
        best_idx = -1
        for i, (hit, emb) in enumerate(pool):
            if selected:
                redundancy = max(float(np.dot(emb, s[1])) for s in selected)
            else:
                redundancy = 0.0
            value = lam * hit.score - (1 - lam) * redundancy
            if value > best_val:
                best_val, best_idx, best = value, i, (hit, emb)
        if best is None:
            break
        selected.append(best)
        pool.pop(best_idx)
    return [hit for hit, _ in selected]
