# B-13 — Isolated three-process acceptance (2026-10-26)

The opt-in test in `tests/integration/test_three_process.py` passed against:

| Component | Source/version |
| --- | --- |
| CPersona fork | `pippirium/CPersona` archive at `7821840f114866673022edfc5fe4b21a9b08a5e5`, installed 2.6.13a2; MCP 1.30.0 |
| local-search | existing checkout at `31f31048fb8edeedf0f94d295bc0c9471000920a` / 0.1.1; documented fake embedder |
| Bridge | main `9d648e1` runtime plus the new integration test; MCP 2.3.0 |

CPersona and Bridge were **separate stdio child processes** and local-search was a third child process speaking guarded loopback HTTP. No in-process fake provider or memory fixture substituted for a service. The test used a generated 16×16 red PNG, a new temporary CPersona database, separate Bridge database, dedicated provider data/index directory and disposable watched/managed folders. CPersona embedding mode was `none`, vector search local, operating-context loading off. No generation LLM was used. Provider/CPersona source code, live databases and client configuration were not modified.

## Executed command and result

With the fork installed in a dedicated test environment, from Bridge root:

```sh
BRIDGE_THREE_PROCESS_CPERSONA_PYTHON=<test-env-python> \
BRIDGE_THREE_PROCESS_LOCAL_SEARCH=<pinned-provider-executable> \
uv run pytest tests/integration/test_three_process.py -q
```

Result: one test passed, exit 0. Default `uv run pytest`: **301 passed, 5 skipped** (four existing live-provider probes and this explicit opt-in test), exit 0. `git diff --check`: passed.

The fork was fetched via GitHub's archive endpoint for the full SHA above and installed from that archive, without using the user's running CPersona service. For reproduction, inspect the fork's AGENTS.md and install it into a separate environment (its MCP 1.x dependency must not replace Bridge's MCP 2.x environment).

## Assertions executed

- Real CPersona `store` creates a synthetic memory; its returned ID supplies the opaque `mem:` reference.
- Bridge link, list and text search return that linked asset in exact scope.
- Real CPersona recall and reconstruct return text content, with the synthetic query evidence present; neither returns an image block.
- Explicit Bridge get returns the exact generated PNG bytes; another agent's scope returns `asset_not_found`.
- Explicit register creates a pending copy; the actual watcher indexes it and resolver binds it. A repeated import for another synthetic memory preserves ready state; explicit managed get returns identical bytes.
- After terminating only the test provider, get returns `dependency_unavailable`; unlink still works without deleting or modifying the generated source image.
- Child-process cleanup runs in `finally`; test data belongs to pytest's temporary directory. No existing provider is stopped.

## Limits

This is one small deterministic integration case, not a release certification or semantic-quality benchmark. It does not measure real EmbeddingGemma quality, compare embedding models, prove every OS failure mode, or rehearse installation on a fresh Mac. The historical docs/15 audit remains a separate earlier fake-embedder run; this document supplies the previously missing real CPersona three-process evidence. CP-02 remains tracked by #19.
