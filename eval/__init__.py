"""B-14: cross-retrieval evaluation CLI (docs/07).

Three tasks, no generation LLM, synthetic-or-openly-licensed fixtures only:

  text_to_image   query text        -> ranked assets via bridge.search_assets
  memory_to_image memory_ref        -> ranked assets via bridge.list_memory_assets
  image_to_image  fixture file bytes-> ranked assets via the provider's
                                       media template route (the audited
                                       image->image path, docs/10)

Metrics exactly as docs/07 §metric rules: NDCG@10 with gains 2^grade-1,
Recall@10 (fraction of relevant recovered — distinct from Success@10,
which is any-relevant-at-10), MRR@10, p50/p95 latency with stated warmup.
Outputs: per-query JSON (schema eval/schema.json) + Markdown report that
ALWAYS prints limitations, environment fingerprints, missing qrels and
never claims an unexecuted test.

Usage (run explicitly; nothing here runs in CI against a live provider):

  uv run python -m eval.run \\
      --manifest eval/manifest.example.json \\
      --provider http://127.0.0.1:18765 \\
      --managed-folder-id 7 \\
      --out reports/eval-$(date +%Y%m%d)

Fixtures are materialized into a throwaway watched folder the operator
registers beforehand; the CLI never registers folders and never touches a
non-managed path of the provider.
"""
