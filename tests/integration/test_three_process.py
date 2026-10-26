"""Explicit opt-in: real fork/Bridge stdio children + isolated real HTTP provider.

BRIDGE_THREE_PROCESS_CPERSONA_PYTHON and BRIDGE_THREE_PROCESS_LOCAL_SEARCH
must point to installed executables. No user DB/config or existing provider
is used. The provider runs with its documented fake embedder: this proves
integration, not semantic quality. Source code is never patched by this test.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time
import zlib

import anyio
import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]
CP_PYTHON = os.environ.get("BRIDGE_THREE_PROCESS_CPERSONA_PYTHON")
LS_COMMAND = os.environ.get("BRIDGE_THREE_PROCESS_LOCAL_SEARCH")


def _png() -> bytes:
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    raw = (b"\x00" + bytes([240, 10, 10]) * 16) * 16
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _payload(result):
    assert not result.is_error
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(next(c.text for c in result.content if c.type == "text"))


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.mark.skipif(not (CP_PYTHON and LS_COMMAND), reason="three-process opt-in executables required")
def test_real_three_process_memory_asset_round_trip(tmp_path):
    # No inherited service credentials, endpoints or runtime configuration.
    env = {k: os.environ[k] for k in ("PATH", "LANG", "TMPDIR") if k in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    watched = tmp_path / "watched"
    managed = tmp_path / "managed"
    watched.mkdir(mode=0o700)
    managed.mkdir(mode=0o700)
    media = _png()
    source = watched / "synthetic-red.png"
    source.write_bytes(media)
    with (tmp_path / "children.log").open("w+") as logs:
        def start():
            return subprocess.Popen(
                [LS_COMMAND, "serve", "--port", str(port),
                 "--data-dir", str(tmp_path / "provider")],
                env={**env, "LOCAL_SEARCH_EMBEDDER": "fake"},
                cwd=tmp_path, stdout=logs, stderr=logs,
            )
        process = start()
        try:
            with httpx.Client(base_url=base, timeout=3, trust_env=False) as http:
                def wait_ready():
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        assert process.poll() is None, "provider exited before ready"
                        try:
                            response = http.get("/api/status")
                            if response.status_code == 200:
                                return
                        except httpx.TransportError:
                            pass
                        time.sleep(0.1)
                    pytest.fail("isolated provider readiness timeout")
                wait_ready()
                def folder(path):
                    result = http.post("/api/folders", json={
                        "path": str(path), "kinds": ["image"], "exclude": [], "watch": True})
                    assert result.status_code == 201
                    return result.json()["id"]
                source_folder = folder(watched)
                managed_folder = folder(managed)
                deadline = time.monotonic() + 30
                source_id = None
                while time.monotonic() < deadline:
                    entries = http.get("/api/files/recent", params={"limit": 100}).json()
                    hits = [e for e in entries if e["folder_id"] == source_folder
                            and e["name"] == source.name]
                    if hits:
                        source_id = hits[0]["file_id"]
                        break
                    time.sleep(0.1)
                assert source_id is not None, "synthetic source not indexed"

                cp = StdioServerParameters(command=CP_PYTHON, args=["-m", "cpersona"],
                    cwd=str(tmp_path), env={**env,
                        "CPERSONA_DB_PATH": str(tmp_path / "cpersona.db"),
                        "CPERSONA_EMBEDDING_MODE": "none",
                        "CPERSONA_VECTOR_SEARCH_MODE": "local",
                        "CPERSONA_OPERATING_CONTEXT": "off", "CPERSONA_TRANSPORT": "stdio"})
                bridge = StdioServerParameters(command=sys.executable,
                    args=["-m", "bridge.server"], cwd=str(ROOT), env={**env,
                        "PYTHONPATH": str(ROOT / "src"),
                        "BRIDGE_DATA_DIR": str(tmp_path / "bridge"),
                        "BRIDGE_PROVIDER_URL": base,
                        "BRIDGE_MANAGED_ROOT": str(managed),
                        "BRIDGE_MANAGED_FOLDER_ID": str(managed_folder)})

                async def run():
                    async with stdio_client(cp, errlog=logs) as (cr, cw):
                        async with ClientSession(cr, cw) as memory:
                            await memory.initialize()
                            async with stdio_client(bridge, errlog=logs) as (br, bw):
                                async with ClientSession(br, bw) as assets:
                                    await assets.initialize()
                                    scope = {"agent_id": "integration", "project_id": "synthetic", "channel": "test"}
                                    saved = _payload(await memory.call_tool("store", {
                                        **scope, "content": "合成テスト 赤い画像 integration probe"}))
                                    assert saved["result"] == "stored" and saved["ok"]
                                    ref = f"mem:{saved['id']}"
                                    link = _payload(await assets.call_tool("link_asset", {
                                        **scope, "memory_ref": ref, "provider_file_id": source_id}))
                                    assert link["ok"] and link["link_status"] == "ready"
                                    aid = link["asset_id"]
                                    listed = _payload(await assets.call_tool("list_memory_assets", {
                                        **scope, "memory_ref": ref}))
                                    assert [a["asset_id"] for a in listed["assets"]] == [aid]
                                    search = _payload(await assets.call_tool("search_assets", {
                                        **scope, "query": "赤"}))
                                    assert search["ok"] and aid in [a["asset_id"] for a in search["results"]]
                                    for tool in ("recall", "reconstruct"):
                                        text_result = await memory.call_tool(tool, {**scope, "query": "integration"})
                                        assert all(c.type == "text" for c in text_result.content)
                                        assert "integration" in json.dumps(_payload(text_result), ensure_ascii=False)
                                    image = await assets.call_tool("get_asset", {**scope, "asset_id": aid})
                                    assert [c.type for c in image.content] == ["image"]
                                    assert base64.b64decode(image.content[0].data) == media
                                    denied = _payload(await assets.call_tool("get_asset", {
                                        **scope, "agent_id": "other", "asset_id": aid}))
                                    assert denied["error"]["code"] == "asset_not_found"
                                    copied = _payload(await assets.call_tool("register_asset", {
                                        **scope, "memory_ref": ref, "provider_file_id": source_id}))
                                    assert copied["ok"] and copied["link_status"] == "pending"
                                    deadline = time.monotonic() + 30
                                    while time.monotonic() < deadline:
                                        resolved = _payload(await assets.call_tool("resolve_pending_assets", {}))
                                        if any(b["asset_id"] == copied["asset_id"] for b in resolved["bound"]):
                                            break
                                        await anyio.sleep(0.1)
                                    else:
                                        pytest.fail("managed copy did not resolve")
                                    again = _payload(await assets.call_tool("register_asset", {
                                        **scope, "memory_ref": "mem:9002", "provider_file_id": source_id}))
                                    assert again["asset_id"] == copied["asset_id"] and again["link_status"] == "ready"
                                    image = await assets.call_tool("get_asset", {**scope, "asset_id": copied["asset_id"]})
                                    assert base64.b64decode(image.content[0].data) == media
                                    _stop(process)
                                    outage = _payload(await assets.call_tool("get_asset", {**scope, "asset_id": aid}))
                                    assert outage["error"]["code"] == "dependency_unavailable"
                                    removed = _payload(await assets.call_tool("unlink_asset", {
                                        **scope, "memory_ref": ref, "asset_id": aid}))
                                    assert removed["unlinked"] and removed["remaining_links"] == 0
                                    assert source.read_bytes() == media
                anyio.run(run)
        finally:
            _stop(process)
