"""Fetch the CP-02 LMEB eval_data subset (23 tasks) from
KaLM-Embedding/LMEB at the pinned revision, size-verified against a manifest
listed in ./README.md. Sequential and resume-friendly: reruns skip files whose
size already matches.

Usage: WORKDIR=/durable/dir python3 download_lmeb.py
(never /tmp — the first run lost a completed 1.15 GB dataset to macOS cleanup)
"""
import json
import os
import sys
import time
import urllib.request

REVISION = "f9d4294be4a24b8c16d6bfb59d56a6fec4bd95c0"
BASE = ("https://huggingface.co/datasets/KaLM-Embedding/LMEB/resolve/"
        + REVISION + "/")
TASK_DIRS = ("Episodic", "Dialogue", "Semantic", "Procedural")

workdir = os.environ.get("WORKDIR")
if not workdir:
    sys.exit("set WORKDIR to a durable directory")
root = os.path.join(workdir, "lmeb", "eval_data")
manifest_path = os.path.join(workdir, "lmeb", "manifest.json")

if os.path.exists(manifest_path):
    manifest = json.load(open(manifest_path))
else:
    # Discover the file list from the Hub tree API and pin sizes as manifest.
    url = ("https://huggingface.co/api/datasets/KaLM-Embedding/LMEB/tree/"
           + REVISION + "?recursive=true")
    req = urllib.request.Request(url, headers={"User-Agent": "cp02-gemma-eval"})
    entries = json.load(urllib.request.urlopen(req, timeout=120))
    manifest = [{"path": e["path"], "size": e["size"]} for e in entries
                if e["type"] == "file" and e["path"].split("/")[0] in TASK_DIRS
                and e["path"].count("/") >= 2]
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    json.dump(manifest, open(manifest_path, "w"))

sizes = {m["path"]: m["size"] for m in manifest}
files = [p for p in sorted(sizes) if p.split("/")[0] in TASK_DIRS]
print(f"{len(files)} files", flush=True)
done = 0
t0 = time.time()
for p in files:
    dest = os.path.join(root, p)
    if os.path.exists(dest) and os.path.getsize(dest) == sizes.get(p, -1):
        done += 1
        continue
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    for attempt in range(3):
        try:
            req = urllib.request.Request(BASE + urllib.request.quote(p),
                                         headers={"User-Agent": "cp02-gemma-eval"})
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
            if os.path.getsize(tmp) != sizes[p]:
                raise OSError(f"size mismatch {p}")
            os.replace(tmp, dest)
            break
        except Exception as e:
            print(f"RETRY {p}: {e}", flush=True)
            time.sleep(2)
    else:
        print(f"FAILED {p}", flush=True)
        sys.exit(1)
    done += 1
    if done % 25 == 0:
        print(f"{done}/{len(files)} {time.time() - t0:.0f}s", flush=True)
print("ALL DONE", time.time() - t0, flush=True)
