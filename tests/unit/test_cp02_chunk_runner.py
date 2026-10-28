"""Offline tests for the optional CP-02 runner; no model, network or user DB."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "eval" / "cp02" / "chunk_runner.py"
spec = importlib.util.spec_from_file_location("cp02_chunk_runner", SCRIPT)
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


def result(task="Example", n=1, value=75.2):
    return {"task": task, "mean_ndcg_at_10": value, "subtasks": {"one": value},
            "num_subtasks": n, "time_s": 3.2}


@pytest.mark.parametrize("bad", [
    {"task": "Other"}, {"num_subtasks": 0}, {"mean_ndcg_at_10": 101},
    {"mean_ndcg_at_10": float("nan")}, {"subtasks": {}}, {"time_s": -1},
    {"subtasks": {"one": 80, "two": 70}},
])
def test_invalid_results_never_count_as_done(tmp_path, bad):
    path = tmp_path / "Example.json"
    path.write_text(json.dumps(result() | bad))
    assert not runner.valid_result(path, "Example")


def test_valid_result(tmp_path):
    path = tmp_path / "Example.json"
    path.write_text(json.dumps(result()))
    assert runner.valid_result(path, "Example")


def test_existing_file_without_marker_fails_closed(tmp_path):
    output = tmp_path / "output"
    logs = tmp_path / "logs"
    output.mkdir(); logs.mkdir()
    (output / "Example.json").write_text(json.dumps(result()))
    with pytest.raises(ValueError, match="unverified"):
        runner.run_task("Example", tmp_path / "unused.sh", output, logs,
                        {"model": "demo"}, {}, 3, 1)


def test_validated_resume_detects_tampering(tmp_path):
    output = tmp_path / "output"
    logs = tmp_path / "logs"
    output.mkdir(); logs.mkdir()
    target = output / "Example.json"
    target.write_text(json.dumps(result()))
    ctx = {"model": "demo"}
    (output / "Example.completed.json").write_text(json.dumps({
        "run": ctx, "result_sha256": runner.sha256(target)}))
    assert runner.run_task("Example", tmp_path / "unused.sh", output, logs, ctx, {}, 3, 1)
    target.write_text(json.dumps(result(value=55)))
    with pytest.raises(ValueError, match="unverified"):
        runner.run_task("Example", tmp_path / "unused.sh", output, logs, ctx, {}, 3, 1)


def test_exit_nonzero_does_not_stamp_success(tmp_path):
    output = tmp_path / "out"; output.mkdir()
    logs = tmp_path / "logs"; logs.mkdir()
    script = tmp_path / "fail.sh"
    script.write_text("#!/bin/bash\nexit 13\n")
    assert runner.run_task("Example", script, output, logs, {}, {}, 5, 1) is False
    assert not (output / "Example.completed.json").exists()


def test_fingerprint_rejects_path_traversal(tmp_path):
    work = tmp_path / "work"; work.mkdir()
    (work / "lmeb").mkdir()
    (work / "lmeb" / "manifest.json").write_text(json.dumps([
        {"path": "../secret", "size": 2}]))
    bench = tmp_path / "bench"; bench.mkdir()
    arm = tmp_path / "arm"; arm.mkdir()
    cache = tmp_path / "cache"; cache.mkdir()
    with pytest.raises(ValueError, match="unsafe"):
        runner.fingerprint(work, bench, arm, SCRIPT, "cpu", 1024, cache)
