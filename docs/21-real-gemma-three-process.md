# IMG-09 extension — real EmbeddingGemma 2 three-process acceptance (Issue #77)

**Status: implemented as an opt-in test, not run on a real-model machine.**
The existing `tests/integration/test_three_process.py` forces a fake embedder
and proves only protocol and permission handling. It has not been replaced.
A passing fake test must never be cited as real-embedding evidence.

## Setup on the separate PC

Use only fresh evaluation checkouts, a dedicated model cache, and a disposable
folder hierarchy. Do **not** reuse CPersona's live memory database, local-search's
current index/data directory, user image collections or client credentials.

Use the pinned revisions originally audited for the Bridge:
- CPersona fork: `7821840f114866673022edfc5fe4b21a9b08a5e5`
- local-search: `31f31048fb8edeedf0f94d295bc0c9471000920a`
- Real model: `google/embeddinggemma-2@914f7f89142e33e77833254d9c9b90c3cef7303b`

Verify the exact commits on both separate checkouts. Do not replace the
dependency environments of one project with another; run CPersona and
local-search from their own installed environments. The local-search executable
must accept `serve --port N --data-dir DIR`.

The test copies only the Hugging Face model cache subtree from an **explicitly
selected, dedicated** cache root into its own disposable local-search data
folder before startup. The dedicated cache root has this layout:

```text
<DEDICATED-HF-CACHE>/
  models--google--embeddinggemma-2/
    snapshots/914f7f89142e33e77833254d9c9b90c3cef7303b/
      model.safetensors
      config.json
      ... remaining REQUIRED_FILES (verified by the test)
    blobs/...
```

It is safe to prefetch the pinned model outside the test with
`huggingface_hub.snapshot_download(..., revision=<full-SHA>,
cache_dir=<DEDICATED-HF-CACHE>)` in a separately reviewed preparation step.
The test does **not** download any model and sets Hugging Face/Transformers to
offline mode for its child processes. Expect space and memory overhead from
copying the model into an isolated data directory.

The pinned local-search implementation supports CPU (fp32) and macOS MPS
(bf16); it **does not provide a CUDA inference path**. On a Linux PC with
an NVIDIA GPU this test runs local-search on CPU, unless a separately approved
local-search implementation adds CUDA support. This distinction does not
prevent running the separate CPersona Track B harness on CUDA, which is
tracked by #75; never combine device-specific performance claims.

## Command

Run from the Bridge repository root after `uv sync`, with the paths
replaced by independently verified **test installations**:

```sh
export BRIDGE_THREE_PROCESS_CPERSONA_PYTHON=<CPERSONA-TEST-VENV>/bin/python
export BRIDGE_THREE_PROCESS_LOCAL_SEARCH=<ISOLATED-LOCAL-SEARCH-EXECUTABLE>
export BRIDGE_REAL_GEMMA_CACHE_DIR=<DEDICATED-HF-CACHE>
uv run pytest tests/integration/test_real_gemma_three_process.py -o addopts='' -q
```

With any of the three environment variables absent, pytest should **skip**
the test. Do not put these variables in the default shell profile or CI.
With them present, a failure is a failure, not a reason to silently switch to
`LOCAL_SEARCH_EMBEDDER=fake`. The test creates a new loopback port, separate
CPersona memory DB, Bridge SQLite, local-search SQLite/LanceDB and synthetic
PNG under pytest `tmp_path`. It terminates only the local-search child
it launched and uses stdio MCP child management for CPersona and Bridge.

## Positive and negative acceptance

- Verify `/api/status` model ID, **full revision**, `text_image` config and
  **ready state**. HTTP 200 with `loading` is not evidence of readiness.
- Store one synthetic event with real CPersona; obtain an actual
  `memory_ref` from its response; ingest a generated PNG into Bridge managed
  storage; require `pending` and later a successful watcher index/resolve.
- Explicitly link this asset to the memory, list the reference, use the
  real local-search image query via the Bridge's scoped MCP tool and fetch the
  **correct original bytes** only through explicit `get_asset`.
- Prove that ordinary reconstruct returns text only; another scope cannot
  fetch the asset; unlink removes the memory association. Search tool response
  itself must contain metadata only, not inline image blocks.

The test **does not** show retrieval superiority, general image accuracy or
CPersona's embedded semantic recall: CPersona runs with
`CPERSONA_EMBEDDING_MODE=none` to isolate the image-bridge flow.
The single exact-image query only checks real-encoder integration. Evaluating
text/image ranking against graded human qrels belongs to #76. Actual end-user
MCP-host attachment forwarding, crash/rebuild under load, long pending
latency distributions, cold-install experience and a non-CPU Linux inference
path remain **unverified**.

## Results record

When someone runs this on the separate PC, attach the following to #77:
the OS/CPU/GPU/RAM and available capacity; Python/torch/transformers versions;
Git commits and model full revision; the test command, date, elapsed time and
passed/failed/skipped counts; model device/dtype reported by `/api/status`;
and a brief list of unverified gates. Do not upload raw child logs, local
absolute paths, tokens, PNG bytes, private memory or downloaded model weights.
A test failure means the corresponding acceptance item remains open.
