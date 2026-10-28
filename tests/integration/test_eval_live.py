"""Opt-in B-14 CLI rehearsal against an isolated fake-embedder provider."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
LS_COMMAND = os.environ.get("BRIDGE_THREE_PROCESS_LOCAL_SEARCH")


@pytest.mark.skipif(not LS_COMMAND, reason="isolated local-search executable required")
def test_existing_evaluation_cli_with_synthetic_fixtures(tmp_path):
    watched = tmp_path / "watched"
    watched.mkdir(mode=0o700)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR") if key in os.environ}
    env.update(LOCAL_SEARCH_EMBEDDER="fake", PYTHONDONTWRITEBYTECODE="1")
    with (tmp_path / "provider.log").open("w") as log:
        process = subprocess.Popen([LS_COMMAND, "serve", "--port", str(port),
            "--data-dir", str(tmp_path / "provider")], cwd=tmp_path, env=env, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=base, timeout=3, trust_env=False) as http:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    assert process.poll() is None
                    try:
                        if http.get("/api/status").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("isolated provider did not start")
                folder = http.post("/api/folders", json={"path": str(watched), "kinds": ["image"],
                    "exclude": [], "watch": True})
                assert folder.status_code == 201
                output = tmp_path / "report"
                # The example ZIP fixture is intentionally not indexed by an
                # image-only folder; omit it to avoid the evaluator's 60s wait.
                manifest = json.loads((ROOT / "eval/manifest.example.json").read_text())
                manifest["fixtures"] = [item for item in manifest["fixtures"] if item["generator"] == "png_solid"]
                manifest_path = tmp_path / "image-only-manifest.json"
                manifest_path.write_text(json.dumps(manifest))
                command = [sys.executable, "-m", "eval.run", "--manifest", str(manifest_path),
                           "--provider", base, "--watched-dir", str(watched),
                           "--managed-folder-id", str(folder.json()["id"]),
                           "--data-dir", str(tmp_path / "bridge"), "--out", str(output), "--warmup", "0"]
                run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=45)
                assert run.returncode == 0, "isolated evaluation CLI failed"
                report = json.loads((output / "result.json").read_text())
                assert report["env"]["embedder"] == "fake"
                assert set(report["tasks"]) == {"text_to_image", "memory_to_image", "image_to_image"}
                assert any("NOT semantic" in item for item in report["limitations"])
                assert not any(item.get("errors") for item in report["per_query"])
                assert not list(watched.iterdir())  # CLI cleans up only its generated fixtures
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
