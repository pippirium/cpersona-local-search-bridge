"""B-13: opt-in contract probes against a REAL local-search instance.

Skipped unless BRIDGE_LIVE_BASE_URL is set (a loopback http://127.0.0.1:<port>
URL the operator started explicitly). These tests NEVER start or stop the
provider, NEVER write to its folders, and touch only synthetic test data in
a dedicated test folder the operator registered beforehand (folder id via
BRIDGE_LIVE_FOLDER_ID). They are the three-process integration's provider
half; the CPersona half is a manual operation record (docs/14).

Evidence they produce is what the B-13 PR's 実行できなかったテスト section
turns INTO executed tests when a live provider is available.
"""

from __future__ import annotations

import io
import os
import struct
import tempfile
import time
from pathlib import Path

import pytest

live = pytest.mark.skipif(
    not os.environ.get("BRIDGE_LIVE_BASE_URL"),
    reason="live provider opt-in: set BRIDGE_LIVE_BASE_URL (and "
           "BRIDGE_LIVE_FOLDER_ID) to run",
)


def _config():
    from bridge.config import validate_config

    base = os.environ["BRIDGE_LIVE_BASE_URL"]
    folder = os.environ.get("BRIDGE_LIVE_FOLDER_ID")
    tmp = Path(tempfile.mkdtemp(prefix="bridge-live-"))
    return validate_config(
        data_dir=tmp / "bdata",
        provider_base_url=base,
        managed_folder_id=int(folder) if folder else None,
        managed_root=(tmp / "managed") if folder else None,
    )


@live
def test_status_and_folders_contract():
    """GET /api/status + GET /api/folders answer the audited shapes (docs/10)."""
    from bridge.client import LocalSearchClient

    cfg = _config()
    client = LocalSearchClient(cfg)
    try:
        st = client.status()
        assert "index" in st and "model" in st
        folders = client.folders()
        assert isinstance(folders, list)
        ids = [f["id"] for f in folders]
        wanted = int(os.environ["BRIDGE_LIVE_FOLDER_ID"])
        assert wanted in ids  # operator registered the test folder
        entry = next(f for f in folders if f["id"] == wanted)
        assert entry.get("watch") is True  # premise for managed resolution
    finally:
        client.close()


@live
def test_search_shape_and_never_retried():
    """POST /api/search returns the audited envelope; exactly one request is
    made for a 200 (search is never retried: docs/12 §6)."""
    from bridge.client import LocalSearchClient

    cfg = _config()
    client = LocalSearchClient(cfg)
    try:
        data = client.search("bridge-integration-probe", page_size=20)
        for key in ("search_id", "generation", "items", "took_ms"):
            assert key in data
    finally:
        client.close()


@live
def test_folder_entries_recent_shape():
    """GET /api/files/recent: array of ResultItems; folder_entries filters
    client-side by folder_id (the route has no server-side filter)."""
    from bridge.client import LocalSearchClient

    cfg = _config()
    client = LocalSearchClient(cfg)
    try:
        fid = int(os.environ["BRIDGE_LIVE_FOLDER_ID"])
        entries = client.folder_entries(fid, limit=50)
        assert all(e["folder_id"] == fid for e in entries.items)
        for e in entries.items[:1]:
            for key in ("file_id", "kind", "name", "size", "mtime"):
                assert key in e
    finally:
        client.close()


@live
def test_managed_pending_resolution_full_loop():
    """The B-11 managed loop against the REAL watcher: register a linked
    file -> managed copy lands under the managed root -> the same bytes
    appear in the WATCHED folder (as an operator's sync would deliver
    them) -> the provider indexes them -> resolve_pending_assets binds the
    copy's NEW id from DB state alone. Test folder only; nothing in the
    provider is written except through the filesystem the watcher
    observes."""
    import subprocess

    import anyio

    from bridge.client import LocalSearchClient
    from bridge.config import validate_config
    from bridge.repository import Repository, open_repository
    from bridge.server import BridgeContext, create_server
    from tests.unit.test_server_health import connected_client

    base = os.environ["BRIDGE_LIVE_BASE_URL"]
    folder = int(os.environ["BRIDGE_LIVE_FOLDER_ID"])
    watched = Path(os.environ["BRIDGE_LIVE_WATCHED_DIR"])
    work = Path(tempfile.mkdtemp(prefix="bridge-live-loop-"))
    managed = work / "managed"
    managed.mkdir(mode=0o700)
    cfg = validate_config(data_dir=work / "bdata",
                          provider_base_url=base,
                          managed_folder_id=folder,
                          managed_root=managed)
    repo = Repository(open_repository(work / "bdata"))
    client = LocalSearchClient(cfg)
    # discovery via the INVENTORY route: a semantic query for a filename is
    # not a similarity match (fake embedder scores everything below
    # threshold; even the real model need not rank a filename query).
    # SELF-SUFFICIENT fixture: a unique synthetic file dropped into the
    # watched dir (the test's own file, removed in cleanup) — independent
    # of what else the folder holds, so the size-based resolution join is
    # unambiguous by construction.
    import secrets
    import zlib

    def _png(w: int, h: int, rgb: tuple[int, int, int],
             nonce: bytes) -> bytes:
        """A real decodable PNG built from stdlib only (no PIL needed in
        the Bridge venv; synthetic media per AGENTS.md)."""
        raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
        def chunk(tag: bytes, data: bytes) -> bytes:
            c = struct.pack(">I", len(data)) + tag + data
            return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw))
                + chunk(b"tEXt", b"nonce\x00" + nonce)
                + chunk(b"IEND", b""))

    seed = secrets.token_hex(8)
    fixture_name = f"bridge-itg-{seed}.png"
    fixture_bytes = _png(64, 64, (int(seed[:2], 16) or 9,
                                  int(seed[2:4], 16) or 99,
                                  int(seed[4:6], 16) or 199),
                         seed.encode())
    drop_src = watched / fixture_name
    drop_src.write_bytes(fixture_bytes)
    deadline = time.time() + 30
    provider_file_id = None
    while time.time() < deadline:
        entries = client.folder_entries(folder, limit=100)
        hit = [e for e in entries.items if e["name"] == fixture_name]
        if hit:
            provider_file_id = hit[0]["file_id"]
            break
        time.sleep(1)
    assert provider_file_id, f"{fixture_name} was not indexed in 30s"

    async def go():
        ctx = BridgeContext(config=cfg, provider=client, db=repo)
        async with connected_client(create_server(ctx)) as session:
            res = await session.call_tool(
                "register_asset",
                {"agent_id": "itg", "memory_ref": "mem:9001",
                 "provider_file_id": provider_file_id})
            sc = res.structured_content
            assert sc["ok"] is True, sc
            copies = [p for p in managed.rglob("*") if p.is_file()]
            assert copies, "no managed copy after register"
            # drop the copy's bytes into the WATCHED folder (what an
            # operator's own folder sync would do), then poll resolve.
            drop = watched / f"bridge-loop-{provider_file_id}.png"
            subprocess.run(["cp", str(copies[0]), str(drop)], check=True)
            deadline = time.time() + 30
            resolved = None
            try:
                while time.time() < deadline:
                    res = await session.call_tool(
                        "resolve_pending_assets", {})
                    resolved = res.structured_content
                    if resolved["bound"]:
                        break
                    await anyio.sleep(2)
            finally:
                drop.unlink(missing_ok=True)
            assert resolved and resolved["bound"], resolved
            assert resolved["ok"] is True

    try:
        anyio.run(go)
    finally:
        drop_src.unlink(missing_ok=True)
        repo.close()
        client.close()
