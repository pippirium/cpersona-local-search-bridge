"""Fail-closed per-task runner for the optional CP-02 EmbeddingGemma 2 benchmark.

The benchmark is external to Bridge. No network requests or user data access
are performed here: the caller supplies isolated paths and pinned snapshots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

MODEL_REVISION = "914f7f89142e33e77833254d9c9b90c3cef7303b"
DATASET_REVISION = "f9d4294be4a24b8c16d6bfb59d56a6fec4bd95c0"
TASK_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def commit(path: Path) -> str:
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def fingerprint(work: Path, bench: Path, arm: Path, launcher: Path, device: str,
                window: int, cache: Path) -> dict:
    """Fingerprint real input bytes and benchmark settings, excluding local private paths."""
    if not work.is_dir() or not bench.is_dir() or not arm.is_dir():
        raise ValueError("WORKDIR and both CPersona worktrees must exist")
    manifest = work / "lmeb" / "manifest.json"
    entries = read_json(manifest)
    if not isinstance(entries, list) or not entries:
        raise ValueError("LMEB file manifest missing or empty")
    root = work / "lmeb" / "eval_data"
    seen = set()
    dataset_digest = hashlib.sha256()
    for entry in entries:
        relative = entry.get("path") if isinstance(entry, dict) else None
        size = entry.get("size") if isinstance(entry, dict) else None
        if not isinstance(relative, str) or not isinstance(size, int) or size < 0:
            raise ValueError("invalid LMEB manifest entry")
        pp = Path(relative)
        if pp.is_absolute() or ".." in pp.parts or relative in seen:
            raise ValueError("unsafe or duplicate LMEB manifest path")
        seen.add(relative)
        target = root / pp
        if not target.is_file() or not target.resolve().is_relative_to(root.resolve()) or target.stat().st_size != size:
            raise ValueError("missing or mismatched LMEB file")
        dataset_digest.update(relative.encode("utf-8"))
        dataset_digest.update(sha256(target).encode("ascii"))
    model_dir = (work / "hf-cache" / "models--google--embeddinggemma-2" / "snapshots" / MODEL_REVISION)
    model = model_dir / "model.safetensors"
    if not model.is_file() or not model.resolve().is_relative_to(work.resolve()) or model.stat().st_size == 0:
        raise ValueError("pinned Gemma snapshot is missing")
    if not cache.is_dir():
        raise ValueError("embedding cache directory missing")
    if device not in ("cpu", "mps", "cuda"):
        raise ValueError("device must be cpu/mps/cuda")
    if window < 1 or window > 8192:
        raise ValueError("window must be in [1, 8192]")
    return {
        "schema_version": 1,
        "model": "google/embeddinggemma-2",
        "model_revision": MODEL_REVISION,
        "model_sha256": sha256(model),
        "dataset": "KaLM-Embedding/LMEB",
        "dataset_revision": DATASET_REVISION,
        "dataset_manifest_sha256": sha256(manifest),
        "dataset_content_sha256": dataset_digest.hexdigest(),
        "dataset_file_count": len(entries),
        "bench_commit": commit(bench),
        "arm_commit": commit(arm),
        "runner_sha256": sha256(Path(__file__)),
        "launcher_sha256": sha256(launcher),
        "device": device,
        "dtype": "float32" if device == "cpu" else "bfloat16",
        "max_seq_len": window,
        "recall_mode": "rrf",
        "auto_calibrate": True,
        "accel_backend": "numpy",
        "fast": True,
    }


def valid_result(path: Path, task: str) -> bool:
    """Reject truncated, empty, wrong-task and non-finite cached results."""
    try:
        result = read_json(path)
        if not isinstance(result, dict) or result.get("task") != task:
            return False
        subtasks = result.get("subtasks")
        if not isinstance(subtasks, dict) or not subtasks:
            return False
        if result.get("num_subtasks") != len(subtasks):
            return False
        values = [result.get("mean_ndcg_at_10"), result.get("time_s"), *subtasks.values()]
        if any(not isinstance(value, (int, float)) or isinstance(value, bool)
               or not math.isfinite(value) for value in values):
            return False
        if not 0 <= result["mean_ndcg_at_10"] <= 100 or result["time_s"] < 0:
            return False
        if any(not 0 <= value <= 100 for value in subtasks.values()):
            return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def write_json_atomic(path: Path, payload: dict) -> None:
    temp = path.with_name(path.name + ".tmp")
    with temp.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp, path)


def terminate_group(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=10)


def run_task(task: str, script: Path, output: Path, logs: Path,
             stamp: dict, env: dict, stall_seconds: int, poll_seconds: int) -> bool:
    result = output / f"{task}.json"
    marker = output / f"{task}.completed.json"
    if result.exists() or marker.exists():
        if result.is_file() and marker.is_file() and valid_result(result, task):
            previous = read_json(marker)
            if previous == {"run": stamp, "result_sha256": sha256(result)}:
                print(f"SKIP {task}: validated complete and matching fingerprint", flush=True)
                return True
        raise ValueError(f"{task}: existing unverified output; move it aside manually before retry")
    logfile = logs / f"task_{task}.log"
    with logfile.open("x", encoding="utf-8") as handle:
        child = subprocess.Popen(["bash", str(script), "--tasks", task],
                                 env=env, stdout=handle, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        try:
            last_size = -1
            last_progress = time.monotonic()
            while child.poll() is None:
                size = logfile.stat().st_size
                if size != last_size:
                    last_size = size
                    last_progress = time.monotonic()
                if time.monotonic() - last_progress >= stall_seconds:
                    print(f"STALL {task}: terminating only child process group", file=sys.stderr)
                    terminate_group(child)
                    return False
                time.sleep(poll_seconds)
        finally:
            terminate_group(child)
    if child.returncode != 0 or not valid_result(result, task):
        print(f"FAILED {task}: exit={child.returncode}; result not validated", file=sys.stderr)
        return False
    write_json_atomic(marker, {"run": stamp, "result_sha256": sha256(result)})
    print(f"DONE {task}: valid result and fingerprint saved", flush=True)
    return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tasks", default=os.getenv("CP02_TASKS"), help="comma/space-separated task names")
    p.add_argument("--device", choices=("cpu", "mps", "cuda"), default=os.getenv("CP02_DEVICE", "cpu"))
    p.add_argument("--window", type=int, default=int(os.getenv("CP02_MAX_SEQ_LEN", "1024")))
    p.add_argument("--stall-seconds", type=int, default=1200)
    p.add_argument("--poll-seconds", type=int, default=5)
    args = p.parse_args(argv)
    if args.stall_seconds < 1 or args.poll_seconds < 1:
        p.error("timeouts must be positive")
    tasks = re.split(r"[,\s]+", (args.tasks or "").strip())
    if not tasks or not all(TASK_NAME.fullmatch(t) for t in tasks) or len(set(tasks)) != len(tasks):
        p.error("provide unique CPersona task identifiers")
    required = ("WORKDIR", "OUTPUT_DIR", "EMB_CACHE_DIR", "CP_BENCH_WT", "CP_ARM_WT", "VENV")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        p.error("missing environment: " + ", ".join(missing))
    work, output, cache = (Path(os.environ[k]).expanduser().resolve()
                           for k in ("WORKDIR", "OUTPUT_DIR", "EMB_CACHE_DIR"))
    bench = Path(os.environ["CP_BENCH_WT"]).expanduser().resolve()
    arm = Path(os.environ["CP_ARM_WT"]).expanduser().resolve()
    venv = Path(os.environ["VENV"]).expanduser().resolve()
    script = Path(__file__).with_name("run_gemma_trackb.sh")
    if not (venv / "bin" / "python").is_file():
        p.error("VENV/bin/python unavailable")
    if output == work or work == cache or output == cache:
        p.error("output/cache/work roots must be distinct")
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    logs = work / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    try:
        stamp = fingerprint(work, bench, arm, script, args.device, args.window, cache)
        context_file = output / "run-context.json"
        if context_file.exists():
            if read_json(context_file) != stamp:
                raise ValueError("output directory was used with different model/data/run settings")
        else:
            if any(output.iterdir()):
                raise ValueError("output directory has unverified files but no run-context.json")
            write_json_atomic(context_file, stamp)
        env = dict(os.environ)
        env["DEVICE"] = args.device
        env["CP02_MAX_SEQ_LEN"] = str(args.window)
        env["HF_HUB_OFFLINE"] = "1"
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
        for task in tasks:
            if not run_task(task, script, output, logs, stamp, env,
                            args.stall_seconds, args.poll_seconds):
                return 1
        print("ALL REQUESTED TASKS VERIFIED", flush=True)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"CP-02 runner refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
