"""B-14 report writer + fixture builders (offline; the full runner runs
against a live provider only — CLI, never CI-mandatory)."""

import json
from pathlib import Path

from eval import runner as R
from eval.run import write_report


def test_png_fixture_is_decodable_and_deterministic():
    data = R.make_png(8, 4, (10, 20, 30), nonce="n1")
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    # IHDR declares declared dims
    import struct
    w, h = struct.unpack(">II", data[16:24])
    assert (w, h) == (8, 4)
    assert data == R.make_png(8, 4, (10, 20, 30), nonce="n1")
    assert data != R.make_png(8, 4, (10, 20, 30), nonce="n2")


def test_zip_fixture_valid():
    import io
    import zipfile

    data = R.make_zip("zz")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.namelist() == ["note.txt"]
        assert b"zz" in z.read("note.txt")


def test_report_always_has_limitations_and_unmeasured_dash(tmp_path):
    result = {
        "schema_version": 1,
        "env": {"bridge_commit": "x", "provider_version": None,
                "provider_generation": None, "embedder": "fake",
                "device": None, "executed_at": "now", "warmup": 0},
        "tasks": {"text_to_image": {
            "query_count": 1, "missing_qrels": 1, "errors": 0,
            "status": "unmeasured",
            "metrics": {"ndcg_at_10": None, "recall_at_10": None,
                        "success_at_10": None, "mrr_at_10": None,
                        "p50_ms": None, "p95_ms": None}}},
        "per_query": [],
        "limitations": ["nothing was measured"],
    }
    write_report(result, tmp_path / "out")
    md = (tmp_path / "out" / "report.md").read_text()
    assert "unmeasured" in md and "—" in md
    assert "nothing was measured" in md
    j = json.loads((tmp_path / "out" / "result.json").read_text())
    assert j["schema_version"] == 1


def test_rank_for_search_maps_only_known_ids():
    ids = {"fx-a": 11, "fx-b": 12}
    items = [{"file_id": 12}, {"file_id": 99}, {"file_id": 11}]
    ranks = R.rank_for_search(items, ids)
    assert ranks == [("fx-b", 1), ("unlabeled:99", 2), ("fx-a", 3)]


def test_materialize_generates_only_declared(tmp_path):
    manifest = {"fixtures": [
        {"id": "p", "generator": "png_solid", "width": 4, "height": 4,
         "rgb": [1, 2, 3]},
        {"id": "z", "generator": "zip_note"}]}
    paths = R.materialize(manifest, tmp_path)
    assert sorted(p.name for p in paths.values()) == ["p.png", "z.zip"]
    removed = R.sweep_generated(tmp_path, paths)
    assert sorted(removed) == ["p.png", "z.zip"]
    assert list(tmp_path.iterdir()) == []


def test_unknown_generator_rejected(tmp_path):
    manifest = {"fixtures": [{"id": "x", "generator": "ffmpeg"}]}
    try:
        R.materialize(manifest, tmp_path)
        raise AssertionError("must reject unknown generators")
    except ValueError:
        pass


def test_real_model_provenance_must_be_explicit_and_exact():
    import pytest
    from eval.run import verify_real_model

    expected = "914f7f89142e33e77833254d9c9b90c3cef7303b"
    verify_real_model({"model": {"id": "google/embeddinggemma-2",
                                 "revision": expected}}, expected)
    for status in (
        {},
        {"model": {"id": "google/embeddinggemma-2", "revision": "fake"}},
        {"model": {"id": "other-model", "revision": expected}},
        {"model": {"id": "google/embeddinggemma-2", "revision": expected[:8]}},
    ):
        with pytest.raises(ValueError, match="revision"):
            verify_real_model(status, expected)


def test_partial_status_and_model_revision_are_schema_valid(tmp_path):
    import jsonschema
    result = {
        "schema_version": 1,
        "env": {
            "bridge_commit": "x", "provider_version": "0.1.1",
            "provider_generation": 1, "embedder": "google/embeddinggemma-2",
            "model_revision": "914f7f89142e33e77833254d9c9b90c3cef7303b",
            "device": "cpu", "executed_at": "now", "warmup": 2,
        },
        "tasks": {"image_to_image": {
            "query_count": 2, "missing_qrels": 0, "errors": 1,
            "status": "partial",
            "metrics": {"ndcg_at_10": 1.0, "recall_at_10": 1.0,
                        "success_at_10": 1.0, "mrr_at_10": 1.0,
                        "p50_ms": 25.0, "p95_ms": 25.0},
        }},
        "per_query": [],
        "limitations": ["one query failed; only one successful query scored"],
    }
    schema = json.loads((Path(__file__).resolve().parents[2] / "eval" /
                         "schema.json").read_text())
    jsonschema.validate(result, schema)
    write_report(result, tmp_path / "out")
    report = (tmp_path / "out" / "report.md").read_text()
    assert "partial" in report and "one query failed" in report
