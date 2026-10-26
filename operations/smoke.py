#!/usr/bin/env python3
"""Manual smoke script (B-15 runbook §smoke).

Drives the real Bridge code paths (config validation, migrations, the MCP
server surface) in-process against an EXPLICITLY STARTED local-search you
point at. It creates one synthetic asset in a folder you name, verifies
the state machine answers, and cleans up ONLY its own rows/files.
Exit 0 = every check passed; each check prints PASS/FAIL with a machine
code — no paths or provider message text (D-19).

  uv run python operations/smoke.py --provider http://127.0.0.1:18765 \
      --data-dir /tmp/bridge-smoke --folder-id 3

--folder-id must be a watch-on folder you registered yourself (the same
premise register_asset needs; the script never registers folders).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_RESULTS: list[tuple[str, str]] = []


def check(name: str, ok: bool, code: str = "") -> bool:
    _RESULTS.append((name, "PASS" if ok else ("FAIL:" + (code or "assert"))))
    print(f"{name}: {'PASS' if ok else 'FAIL'} {code if not ok else ''}".rstrip())
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--folder-id", type=int, required=True)
    args = ap.parse_args(argv)

    _root = Path(__file__).resolve().parents[1]
    # src/ for the package (mirrors pytest pythonpath=["src"]); repo root
    # for bridge_policy.py (pytest gets it free via rootdir insertion)
    sys.path.insert(0, str(_root / "src"))
    sys.path.insert(0, str(_root))
    import anyio

    from bridge.client import LocalSearchClient
    from bridge.config import validate_config
    from bridge.repository import Repository, open_repository

    data_dir = Path(args.data_dir)
    cfg = validate_config(data_dir=data_dir / "bdata",
                          provider_base_url=args.provider)
    check("config_accepted", True)
    check("data_dir_is_isolated", "local-search" not in str(data_dir).lower(),
          "data_dir suspicious")  # coarse guard: don't point at provider dirs

    repo = Repository(open_repository(data_dir / "bdata"))
    client = LocalSearchClient(cfg)
    try:
        st = client.status()
        check("provider_reachable", bool(st.get("version")))
        folders = client.folders()
        entry = next((f for f in folders if f["id"] == args.folder_id), None)
        check("folder_registered", entry is not None, "folder_<missing>")
        check("folder_watch_on", bool(entry and entry.get("watch")),
              "folder_unwatched")

        # one synthetic PNG into the folder you registered, by copy — the
        # smoke needs an indexed file; you chose the folder, we don't touch
        # any other.
        from bridge.tools_import import register_asset  # product path

        # materialize directly via provider-visible filesystem: the folder
        # path is provider data — read it from the API entry only for the
        # write, never printed.
        import struct
        import zlib

        def png(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
            raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

            def chunk(tag: bytes, data: bytes) -> bytes:
                c = struct.pack(">I", len(data)) + tag + data
                return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            return (b"\x89PNG\r\n\x1a\n"
                    + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

        drop = Path(entry["path"]) / f"bridge-smoke-{int(time.time())}.png"
        drop.write_bytes(png(8, 8, (9, 99, 199)))
        deadline = time.time() + 30
        file_id = None
        while time.time() < deadline:
            for e in client.folder_entries(args.folder_id, limit=100).items:
                if e["name"] == drop.name:
                    file_id = e["file_id"]
                    break
            if file_id:
                break
            time.sleep(1)
        check("watcher_indexed_fixture", file_id is not None, "fixture_unindexed")

        if file_id is not None:
            # bridge_health + resolve + audit through the same repo/client
            # the stdio server would build (in-process; no stdio framing in
            # a shell script).
            from bridge.audit import audit
            from bridge.reconcile import resolve_pending
            from bridge.tools_link import link_asset, unlink_asset

            res = link_asset(provider=client, repo=repo,
                             provider_instance_id=cfg.provider_instance_id,
                             source_instance_id=cfg.source_instance_id or "smoke",
                             agent_id="smoke", project_id="smoke", channel="",
                             memory_ref="mem:1", provider_file_id=file_id)
            check("link_asset_ok", res.ok and res.link_status == "ready",
                  (res.error or {}).get("code", ""))
            rep = resolve_pending(provider=client, repo=repo, config=cfg)
            check("resolve_reports_premise", rep.ok,
                  rep.error.get("code", "") if rep.error else "")
            au = audit(provider=client, repo=repo, config=cfg)
            check("audit_dry_run_clean", au.ok and au.applied is False,
                  au.error.get("code", "") if au.error else "")
            # cleanup: our link + our file only
            if res.asset_id:
                unlink_asset(repo=repo,
                             source_instance_id=cfg.source_instance_id or "smoke",
                             agent_id="smoke", project_id="smoke", channel="",
                             memory_ref="mem:1", asset_id=res.asset_id)
        drop.unlink(missing_ok=True)
    finally:
        repo.close()
        client.close()

    failed = [r for r in _RESULTS if not r[1].startswith("PASS")]
    print(f"smoke: {len(_RESULTS) - len(failed)}/{len(_RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
