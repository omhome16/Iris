from iris.memory.chunking import chunk_text, estimate_tokens
from iris.memory.index import recency_weight


def test_estimate_tokens_counts_words():
    assert estimate_tokens("") == 0
    assert estimate_tokens("one two three") == 3


def test_chunk_small_text_stays_whole():
    chunks = chunk_text("hello world", chunk_tokens=400, overlap_tokens=80)
    assert chunks == ["hello world"]


def test_chunk_long_text_with_overlap():
    text = " ".join(f"word{i}" for i in range(1000))
    chunks = chunk_text(text, chunk_tokens=100, overlap_tokens=20)
    assert len(chunks) > 10
    assert " ".join(chunks[0].split()) == " ".join(chunks[0].split())
    first_tokens = chunks[0].split()
    second_tokens = chunks[1].split()
    assert second_tokens[0] == first_tokens[-20]


def test_recency_decay_half_life():
    from datetime import date, timedelta

    today = date(2026, 8, 15)
    fresh = recency_weight(today, today=today)
    assert fresh == 1.0
    one_hl = recency_weight(today - timedelta(days=30), today=today)
    assert abs(one_hl - 0.5) < 1e-9
    two_hl = recency_weight(today - timedelta(days=60), today=today)
    assert abs(two_hl - 0.25) < 1e-9
    future = recency_weight(today + timedelta(days=10), today=today)
    assert future == 1.0


def test_provenance_promotable_only_trusted():
    from iris.memory.provenance import Origin

    assert Origin.OWNER.promotable
    assert Origin.AGENT.promotable
    assert not Origin.UNTRUSTED.promotable
    assert not Origin.SYSTEM.promotable