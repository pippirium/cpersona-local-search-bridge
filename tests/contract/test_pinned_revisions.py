"""B-01: the repository-level record of what was measured, so later milestones
cannot silently drift off the audited revisions."""

from .conftest import PINNED_REVISIONS


def test_pinned_revisions_are_full_sha():
    for name, sha in PINNED_REVISIONS.items():
        assert len(sha) == 40, name
        assert all(c in "0123456789abcdef" for c in sha), name


def test_audit_document_records_revisions():
    from pathlib import Path

    doc = (
        Path(__file__).resolve().parents[2] / "docs" / "10-contract-audit.md"
    ).read_text(encoding="utf-8")
    assert PINNED_REVISIONS["local-search"] in doc
    assert PINNED_REVISIONS["cpersona"] in doc
