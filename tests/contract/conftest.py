"""Contract fixtures for the B-01 audit.

The files under fixtures/ are synthetic reproductions of provider response shapes
verified against the pinned revisions recorded in docs/10-contract-audit.md:

- local-search pippirium/local-search@31f31048fb8edeedf0f94d295bc0c9471000920a
- CPersona fork pippirium/CPersona@7821840f114866673022edfc5fe4b21a9b08a5e5 (2.6.13a2)

They contain no real user data, no absolute machine paths of the developer's
machine, and no personal file names. Contract tests validate the *Bridge-facing
model* of the providers against these fixtures; they never call a live provider.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "contracts"

# Provider revisions measured on 2026-10-26 (see docs/10-contract-audit.md).
PINNED_REVISIONS = {
    "local-search": "31f31048fb8edeedf0f94d295bc0c9471000920a",
    "cpersona": "7821840f114866673022edfc5fe4b21a9b08a5e5",
}


def load_contract(name: str) -> dict:
    """Load one contract fixture by path relative to fixtures/contracts."""
    path = (FIXTURE_ROOT / name).resolve()
    if FIXTURE_ROOT not in path.parents:
        raise ValueError("contract fixture path escapes the fixture root")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def load_contract_fixture():
    return load_contract


@pytest.fixture
def search_fixture(load_contract_fixture):
    return load_contract_fixture("local-search/search-image-200.json")


@pytest.fixture
def file_detail_fixture(load_contract_fixture):
    return load_contract_fixture("local-search/file-detail-image-200.json")


@pytest.fixture
def status_fixture(load_contract_fixture):
    return load_contract_fixture("local-search/status-200.json")
