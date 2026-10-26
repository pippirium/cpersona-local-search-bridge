"""Metric definitions per docs/07 §metric rules — pure functions, unit-tested.

Conventions (declared once, used by every task):
- relevance is the max grade in qrels for an id; grade 0/absent = not relevant;
- gains = 2**grade - 1 (docs/07); ideal DCG from the qrels' own ranking;
- Recall@10 = (relevant found in top-10) / (all relevant) — a task with no
  qrels is EXCLUDED (missing_qrels), never scored 0;
- Success@10 = 1 if ANY relevant result is in top-10 — deliberately distinct
  from Recall@10 (docs/07 forbids conflating them);
- MRR@10 = 1/rank of first relevant, 0 if none in top-10;
- latency percentiles: nearest-rank on sorted samples (stated, not scipy).
"""

from __future__ import annotations

K = 10


def dcg(grades: list[int], k: int = K) -> float:
    import math

    return sum((2 ** g - 1) / math.log2(i + 2)
               for i, g in enumerate(grades[:k]))


def ndcg_at_10(ranks_grades: list[int], qrels_grades: list[int]) -> float:
    ideal = sorted(qrels_grades, reverse=True)
    idcg = dcg(ideal)
    if idcg == 0.0:
        raise ValueError("no graded qrels")
    return dcg(ranks_grades) / idcg


def recall_at_10(relevant_ids_top10: int, relevant_ids_total: int) -> float:
    if relevant_ids_total == 0:
        raise ValueError("no qrels")
    return relevant_ids_top10 / relevant_ids_total


def success_at_10(relevant_in_top10: bool) -> float:
    return 1.0 if relevant_in_top10 else 0.0


def mrr_at_10(first_relevant_rank: int | None) -> float:
    if first_relevant_rank is None or first_relevant_rank > K:
        return 0.0
    return 1.0 / first_relevant_rank


def percentiles(samples: list[float]) -> tuple[float | None, float | None]:
    """(p50, p95), nearest-rank. None on an empty sample."""
    if not samples:
        return None, None
    s = sorted(samples)
    import math

    def nrank(p: float) -> float:
        idx = max(1, math.ceil(p * len(s)))
        return s[min(len(s) - 1, idx - 1)]

    return nrank(0.50), nrank(0.95)
