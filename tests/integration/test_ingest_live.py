"""Opt-in isolated local-search watcher test; synthetic data and fake embedder."""
import base64
import io
import os
import socket
import subprocess
import time

import httpx
import pytest
from PIL import Image

from bridge.client import LocalSearchClient
from bridge.config import validate_config
from bridge.reconcile import resolve_pending
from bridge.repository import Repository, open_repository
from bridge.tools_ingest import ingest_image

LS_COMMAND = os.environ.get("BRIDGE_THREE_PROCESS_LOCAL_SEARCH")


@pytest.mark.skipif(not LS_COMMAND, reason="isolated local-search executable required")
def test_ingest_then_live_watcher_resolves(tmp_path):
    managed = tmp_path / "managed"
    managed.mkdir(mode=0o700)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {k: os.environ[k] for k in ("PATH", "LANG", "TMPDIR") if k in os.environ}
    env.update(LOCAL_SEARCH_EMBEDDER="fake", PYTHONDONTWRITEBYTECODE="1")
    with (tmp_path / "provider.log").open("w") as log:
        proc = subprocess.Popen([LS_COMMAND, "serve", "--port", str(port), "--data-dir", str(tmp_path / "provider")],
            cwd=tmp_path, env=env, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=base, timeout=3, trust_env=False) as http:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    assert proc.poll() is None
                    try:
                        if http.get("/api/status").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(.1)
                else:
                    pytest.fail("isolated provider did not start")
                response = http.post("/api/folders", json={"path": str(managed), "kinds": ["image"],
                    "exclude": [], "watch": True})
                assert response.status_code == 201
                folder_id = response.json()["id"]
                cfg = validate_config(data_dir=tmp_path / "bridge", provider_base_url=base,
                    managed_root=managed, managed_folder_id=folder_id)
                repo = Repository(open_repository(tmp_path / "bridge"))
                provider = LocalSearchClient(cfg)
                try:
                    buf = io.BytesIO()
                    Image.new("RGB", (8, 8), "red").save(buf, "PNG")
                    result = ingest_image(provider=provider, repo=repo, config=cfg,
                        agent_id="synthetic", image_base64=base64.b64encode(buf.getvalue()).decode())
                    assert result.ok and result.ingest_status == "pending"
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        report = resolve_pending(provider=provider, repo=repo, config=cfg)
                        if report.bound:
                            break
                        time.sleep(.1)
                    else:
                        pytest.fail("isolated managed image did not resolve")
                    assert repo.locator_status(result.asset_id, cfg.provider_instance_id) == "ready"
                finally:
                    provider.close()
                    repo.close()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
