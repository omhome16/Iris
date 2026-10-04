"""temporal-rag beats the default on the shipped stale-fact suite."""

from iris_ai.eval.score import list_suites, run_suite


def test_temporal_rag_is_less_stale_than_the_default():
    path = next(path for path in list_suites("context") if path.stem == "temporal-recall")
    default = run_suite(path, component="default")
    better = run_suite(path, component="temporal-rag")
    assert default["stale_as_current"] == 1.0
    assert default["evidence_recall"] == 1.0
    assert better["stale_as_current"] == 0.0
    assert better["evidence_recall"] == 1.0
    assert better["context_chars"] > 0
