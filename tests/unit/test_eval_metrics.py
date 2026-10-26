"""B-14 metric formulas (docs/07 §metric rules) as unit tests."""

from eval.metrics import (K, dcg, mrr_at_10, ndcg_at_10, percentiles,
                          recall_at_10, success_at_10)


def test_dcg_gain_formula():
    # gains 2^grade-1, discount log2(i+2)
    assert dcg([3]) == 7.0
    assert dcg([0]) == 0.0
    # gains=1 each; discounts log2(2)=1.0 and log2(3)=1.585
    assert abs(dcg([1, 1]) - (1.0 + 1.0 / 1.5849625007211563)) < 1e-9


def test_ndcg_perfect_and_zero():
    assert ndcg_at_10([3, 1], [3, 1]) == 1.0
    assert ndcg_at_10([0, 0], [3]) == 0.0
    # out-of-ideal-order is penalized
    assert ndcg_at_10([1, 3], [3, 1]) < 1.0


def test_ndcg_requires_qrels():
    try:
        ndcg_at_10([0], [0])
    except ValueError:
        return
    raise AssertionError("empty qrels must raise, not score 0")


def test_recall_and_success_are_distinct():
    assert recall_at_10(1, 4) == 0.25
    assert success_at_10(True) == 1.0
    assert success_at_10(False) == 0.0
    try:
        recall_at_10(0, 0)
        raise AssertionError("no-qrels must not be scored 0")
    except ValueError:
        pass


def test_mrr():
    assert mrr_at_10(1) == 1.0
    assert mrr_at_10(4) == 0.25
    assert mrr_at_10(None) == 0.0
    assert mrr_at_10(K + 1) == 0.0  # beyond top-10 counts as miss


def test_percentiles_nearest_rank():
    assert percentiles([]) == (None, None)
    s = [float(i) for i in range(1, 101)]  # 1..100
    p50, p95 = percentiles(s)
    assert p50 == 50.0 and p95 == 95.0
