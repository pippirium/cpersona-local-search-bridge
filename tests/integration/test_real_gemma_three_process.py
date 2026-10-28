"""Opt-in REAL Gemma smoke for CPersona + Bridge + local-search.

Requires isolated executables and a dedicated pinned model cache. CPersona
embeddings remain disabled: this proves protocol integration, not retrieval quality.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

import anyio
import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from eval.runner import make_png
from tests.integration.test_three_process import _payload, _stop

ROOT = Path(__file__).resolve().parents[2]
REV = "914f7f89142e33e77833254d9c9b90c3cef7303b"
MODEL_ROOT = "models--google--embeddinggemma-2"
CP_PYTHON = os.getenv("BRIDGE_THREE_PROCESS_CPERSONA_PYTHON")
LS_COMMAND = os.getenv("BRIDGE_THREE_PROCESS_LOCAL_SEARCH")
CACHE_DIR = os.getenv("BRIDGE_REAL_GEMMA_CACHE_DIR")


@pytest.mark.skipif(not (CP_PYTHON and LS_COMMAND and CACHE_DIR),
                    reason="real Gemma test requires three isolated input settings")
def test_real_gemma_three_process_isolated(tmp_path):
    cache = Path(CACHE_DIR).expanduser().resolve()
    src = cache / MODEL_ROOT
    snapshot = src / "snapshots" / REV
    required = (".gitattributes", "1_Pooling/config.json", "2_Normalize/config.json",
                "README.md", "chat_template.jinja", "config.json",
                "config_sentence_transformers.json", "model.safetensors",
                "modules.json", "preprocessor_config.json", "processor_config.json",
                "sentence_bert_config.json", "tokenizer.json",
                "tokenizer_config.json", "vocab.json")
    assert src.is_dir() and all((snapshot / name).is_file() and (snapshot / name).stat().st_size > 0
                                for name in required), (
        "dedicated pinned Gemma cache missing; do not use a production data dir")
    provider_dir = tmp_path / "provider"
    provider_dir.mkdir(mode=0o700)
    models_dir = provider_dir / "models"
    models_dir.mkdir(mode=0o700)
    # Follow HF blob symlinks into a fresh copy: the provider never changes
    # model cache bytes supplied by the operator.
    shutil.copytree(src, models_dir / MODEL_ROOT, symlinks=False)

    env = {k: os.environ[k] for k in ("PATH", "LANG", "TMPDIR") if k in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1",
               TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    assert "LOCAL_SEARCH_EMBEDDER" not in env
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    managed = tmp_path / "managed"
    managed.mkdir(mode=0o700)
    media = make_png(96, 96, (220, 25, 25), nonce="real-gemma-smoke")

    with (tmp_path / "provider-child.log").open("w+") as logs:
        proc = subprocess.Popen(
            [LS_COMMAND, "serve", "--port", str(port), "--data-dir", str(provider_dir)],
            cwd=tmp_path, env=env, stdout=logs, stderr=logs)
        try:
            with httpx.Client(base_url=base, timeout=10, trust_env=False) as http:
                deadline = time.monotonic() + 600
                status = None
                while time.monotonic() < deadline:
                    assert proc.poll() is None, "isolated provider exited before ready"
                    try:
                        response = http.get("/api/status")
                        if response.status_code == 200:
                            status = response.json()
                            model = status.get("model") or {}
                            assert model.get("id") == "google/embeddinggemma-2"
                            assert model.get("revision") == REV
                            assert model.get("config") == "text_image"
                            if model.get("state") == "ready":
                                break
                            assert model.get("state") != "error", "real model failed to load"
                    except httpx.TransportError:
                        pass
                    time.sleep(1)
                else:
                    pytest.fail("real Gemma model did not become ready in dedicated provider")
                assert status["model"]["state"] == "ready"
                response = http.post("/api/folders", json={
                    "path": str(managed), "kinds": ["image"], "exclude": [], "watch": True})
                assert response.status_code == 201
                folder_id = response.json()["id"]

                cp = StdioServerParameters(command=CP_PYTHON, args=["-m", "cpersona"],
                    cwd=str(tmp_path), env={**env,
                        "CPERSONA_DB_PATH": str(tmp_path / "cpersona.db"),
                        "CPERSONA_EMBEDDING_MODE": "none",
                        "CPERSONA_VECTOR_SEARCH_MODE": "local",
                        "CPERSONA_OPERATING_CONTEXT": "off", "CPERSONA_TRANSPORT": "stdio",
                        "CPERSONA_UPDATE_CHECK": "false"})
                bridge = StdioServerParameters(command=sys.executable,
                    args=["-m", "bridge.server"], cwd=str(ROOT), env={**env,
                        "PYTHONPATH": str(ROOT / "src"),
                        "BRIDGE_DATA_DIR": str(tmp_path / "bridge"),
                        "BRIDGE_PROVIDER_URL": base,
                        "BRIDGE_MANAGED_ROOT": str(managed),
                        "BRIDGE_MANAGED_FOLDER_ID": str(folder_id)})

                async def run():
                    async with stdio_client(cp, errlog=logs) as (cr, cw):
                        async with ClientSession(cr, cw) as memory:
                            await memory.initialize()
                            async with stdio_client(bridge, errlog=logs) as (br, bw):
                                async with ClientSession(br, bw) as assets:
                                    await assets.initialize()
                                    scope = {"agent_id": "real-gemma-smoke", "project_id": "isolated", "channel": "test"}
                                    health = _payload(await assets.call_tool("bridge_health", {}))
                                    assert health["managed_enabled"] and health["managed_watch"]
                                    saved = _payload(await memory.call_tool("store", {
                                        **scope, "content": "synthetic red image recall probe"}))
                                    assert saved["ok"] and saved["result"] == "stored"
                                    recalled = _payload(await memory.call_tool("recall", {
                                        **scope, "query": "synthetic red image"}))
                                    refs = [x.get("ref") for x in recalled.get("messages", []) if x.get("ref")]
                                    assert refs, "real CPersona must return a memory ref; do not synthesize IDs"
                                    ref = refs[0]
                                    received = _payload(await assets.call_tool("ingest_image", {
                                        **scope, "image_base64": base64.b64encode(media).decode("ascii")}))
                                    assert received["ok"] and received["ingest_status"] == "pending"
                                    asset_id = received["asset_id"]
                                    link = _payload(await assets.call_tool("link_registered_asset", {
                                        **scope, "memory_ref": ref, "asset_id": asset_id}))
                                    assert link["ok"] and link["link_status"] == "pending"
                                    deadline = time.monotonic() + 300
                                    while time.monotonic() < deadline:
                                        _payload(await assets.call_tool("resolve_pending_assets", {}))
                                        state = _payload(await assets.call_tool("get_ingest_status", {
                                            **scope, "asset_id": asset_id}))
                                        if state.get("status") == "ready":
                                            break
                                        await anyio.sleep(1)
                                    else:
                                        pytest.fail("real Gemma managed image never indexed")
                                    listed = _payload(await assets.call_tool("list_memory_assets", {
                                        **scope, "memory_ref": ref}))
                                    assert asset_id in [x["asset_id"] for x in listed["assets"]]
                                    query = await assets.call_tool("search_assets_by_image", {
                                        **scope, "query_asset_id": asset_id})
                                    assert all(c.type != "image" for c in query.content)
                                    result = _payload(query)
                                    assert result["ok"] and asset_id in [x["asset_id"] for x in result["results"]]
                                    text_only = await memory.call_tool("reconstruct", {
                                        **scope, "query": "synthetic red image"})
                                    assert all(c.type == "text" for c in text_only.content)
                                    raw = await assets.call_tool("get_asset", {**scope, "asset_id": asset_id})
                                    assert [c.type for c in raw.content] == ["image"]
                                    assert base64.b64decode(raw.content[0].data) == media
                                    denied = _payload(await assets.call_tool("get_asset", {
                                        **scope, "agent_id": "wrong", "asset_id": asset_id}))
                                    assert denied["error"]["code"] == "asset_not_found"
                                    unlinked = _payload(await assets.call_tool("unlink_asset", {
                                        **scope, "memory_ref": ref, "asset_id": asset_id}))
                                    assert unlinked["unlinked"]
                                    after = _payload(await assets.call_tool("list_memory_assets", {
                                        **scope, "memory_ref": ref}))
                                    assert asset_id not in [x["asset_id"] for x in after["assets"]]
                anyio.run(run)
        finally:
            _stop(proc)
