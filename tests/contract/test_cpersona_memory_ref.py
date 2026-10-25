"""B-01 contract tests: CPersona memory identifiers as the Bridge must treat them.

Verified against the pinned CPersona fork revision (docs/10-contract-audit.md):
- store returns a SQLite INTEGER rowid under result=="stored";
- recall/reconstruct refs are spelled "mem:<int>" / "ep:<int>"
  (cpersona/recall_trace.py:ref_of);
- memories and episodes are separate tables whose numeric ids can collide,
  so the prefix is part of the identity and must not be discarded.

The Bridge treats memory_ref as an opaque string and never assumes INTEGER.
"""

from __future__ import annotations

import re

MEMORY_REF_RE = re.compile(r"^(mem|ep):[1-9][0-9]{0,17}$")
MEMORY_REF_MAX_LEN = 128


def test_store_result_contract(load_contract_fixture):
    body = load_contract_fixture("cpersona/store-stored-200.json")
    assert body["ok"] is True
    assert body["result"] == "stored"
    # store.id is an int rowid, but the Bridge never builds memory_ref from it
    # directly: refs come from recall/reconstruct as strings.
    assert isinstance(body["id"], int) and body["id"] >= 1
    assert isinstance(body["embedded"], bool)


def test_recall_refs_are_prefixed_strings(load_contract_fixture):
    shape = load_contract_fixture("cpersona/recall-refs-shape.json")
    refs = [m["ref"] for m in shape["messages"]]
    assert all(isinstance(r, str) for r in refs)
    assert all(MEMORY_REF_RE.match(r) for r in refs)
    # both prefixes are real record kinds; "profile" (-1) is synthetic and not
    # a memory a file can be attached to.
    assert "mem:42" in refs and "ep:7" in refs


def test_reconstruct_head_ref_is_a_memory_ref(load_contract_fixture):
    shape = load_contract_fixture("cpersona/reconstruct-ref-shape.json")
    item = shape["item"]
    assert MEMORY_REF_RE.match(item["head_ref"])
    claim = item["claims"][0]
    assert MEMORY_REF_RE.match(claim["ref"])


def test_memory_ref_opaque_handling():
    # The Bridge validates shape only; it never parses the numeric part as an
    # identity, never renumbers, never assumes int-vs-text storage.
    valid = ["mem:1", "mem:42", "ep:7", "mem:" + "9" * 18]  # SQLite rowid <= 2**63-1 (19 digits)
    invalid = [
        "", "mem:", "ep:0", "mem:-1", "profile", "mem:42 ", " mem:42",
        "mem:007", "mem:1e3", "mem:" + "9" * 19, "x:42", "mem:42:extra",
    ]
    for ref in valid:
        assert MEMORY_REF_RE.match(ref) and len(ref) <= MEMORY_REF_MAX_LEN
    for ref in invalid:
        assert not MEMORY_REF_RE.match(ref)
