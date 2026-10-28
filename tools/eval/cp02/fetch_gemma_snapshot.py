"""Fetch pinned google/embeddinggemma-2 @ 914f7f89... into a throwaway cache
via huggingface_hub.snapshot_download, with the file list pinned by
local-search's REQUIRED_FILES so the result is byte-compatible with
local-search's own cache layout (a plain HF cache dir — local-search can adopt
it later via cache_folder; nothing in a production ~/.local-search is read or
written).

Usage: WORKDIR=/durable/dir python3 fetch_gemma_snapshot.py
The 1,488,915,288-byte model.safetensors was sha256-verified after download
on 2026-10-26 (blob hash 47a68c5b…; note the filename-hash entry in blobs/ is
a symlink — read through it when hashing).
"""
import os

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

CACHE = os.environ.get("WORKDIR")
if not CACHE:
    raise SystemExit("set WORKDIR to a durable directory")
CACHE = os.path.join(CACHE, "hf-cache")

MODEL_ID = "google/embeddinggemma-2"
REVISION = "914f7f89142e33e77833254d9c9b90c3cef7303b"
# Mirrors local-search embedder/download.py REQUIRED_FILES (documented contract,
# not copied code).
REQUIRED_FILES = (
    ".gitattributes",
    "1_Pooling/config.json",
    "2_Normalize/config.json",
    "README.md",
    "chat_template.jinja",
    "config.json",
    "config_sentence_transformers.json",
    "model.safetensors",
    "modules.json",
    "preprocessor_config.json",
    "processor_config.json",
    "sentence_bert_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
)

from huggingface_hub import snapshot_download  # noqa: E402

path = snapshot_download(MODEL_ID, revision=REVISION, cache_dir=CACHE,
                        allow_patterns=list(REQUIRED_FILES))
print("snapshot:", path)
