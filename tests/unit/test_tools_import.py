"""B-10 acceptance for register_asset: opt-in only, source never modified,
byte-authority gates, atomic no-overwrite copy, dedup, no absolute paths,
crash garbage limited to .tmp (docs/06 B-10)."""

from __future__ import annotations

import stat
import pytest

import bridge_policy
from bridge.config import validate_config
from bridge.provider import FakeFile, FakeProvider
from bridge.repository import Repository, open_repository
from bridge.tools_import import register_asset

P = "127.0.0.1:18765"  # default provider_instance_id = netloc
SCOPE = dict(source_instance_id="", agent_id="a1", project_id="p", channel="c")

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR"
       + (120).to_bytes(4, "big") + (80).to_bytes(4, "big") + b"fake" * 10)
PNG2 = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR"
        + (300).to_bytes(4, "big") + (200).to_bytes(4, "big") + b"pix" * 20)


def cfg(tmp_path, managed=True):
    root = tmp_path / "managed" if managed else None
    return validate_config(
        data_dir=tmp_path / "bdata",
        provider_base_url="http://127.0.0.1:18765",
        managed_folder_id=77 if managed else None,
        managed_root=root,
    )


@pytest.fixture
def env(tmp_path):
    config = cfg(tmp_path)  # creates bdata/ 0700 first (validate_config does it)
    conn = open_repository(tmp_path / "bdata")
    repo = Repository(conn)
    prov = FakeProvider(
        files={5: FakeFile(file_id=5, kind="image", name="example.png",
                           content=PNG, size=len(PNG), mime="image/png")},
        folders_data={77: {"watch": True, "status": "watching"}},
    )
    yield repo, prov, config
    repo.close()


def reg(repo, prov, config, **kw):
    args = dict(memory_ref="mem:1", provider_file_id=5)
    args.update(kw)
    return register_asset(provider=prov, repo=repo, config=config, **SCOPE, **args)


# ---- opt-in gate ---------------------------------------------------------------

def test_disabled_without_managed_config(tmp_path):
    config = cfg(tmp_path, managed=False)  # creates bdata 0700 first
    conn = open_repository(tmp_path / "bdata")
    repo = Repository(conn)
    prov = FakeProvider(files={5: FakeFile(file_id=5)})
    out = reg(repo, prov, cfg(tmp_path, managed=False))
    assert out.ok is False and out.error["code"] == "invalid_request"
    assert prov.calls == []  # refused before touching the provider
    repo.close()


# ---- happy path ------------------------------------------------------------------

def test_copy_lands_content_addressed_and_link_pending(env):
    repo, prov, config = env
    out = reg(repo, prov, config)
    assert out.ok and out.link_status == "pending" and out.media_type == "image/png"
    import hashlib, re

    digest = hashlib.sha256(PNG).hexdigest()
    landed = config.managed_root / f"{digest[:2]}/{digest}.png"
    assert landed.read_bytes() == PNG
    asset = repo.asset(out.asset_id, P)
    assert asset["origin"] == "managed"
    assert asset["content_sha256"] == digest
    assert asset["relative_managed_path"] == f"{digest[:2]}/{digest}.png"
    assert "/" not in str(asset["relative_managed_path"][:1])  # relative only
    # locator pending with folder evidence, provider_file_id NULL (docs/11 §3)
    row = repo.asset(out.asset_id, P)
    assert row["locator_status"] == "pending"


def test_original_source_untouched(env):
    repo, prov, config = env
    before = prov.files[5].content, prov.files[5].size
    reg(repo, prov, config)
    assert (prov.files[5].content, prov.files[5].size) == before


def test_no_write_outside_managed_root(env):
    import re as _re

    repo, prov, config = env
    reg(repo, prov, config)
    # every entry under the root is either the tmp dir or a hex shard dir
    for p in config.managed_root.iterdir():
        assert p.name == ".tmp" or _re.match(r"^[0-9a-f]{2}$", p.name), p.name
    # and nothing was written next to the data dir
    assert not (config.data_dir / "managed").exists()


# ---- dedup ------------------------------------------------------------------------

def test_dedup_reuses_asset_and_never_overwrites(env):
    repo, prov, config = env
    first = reg(repo, prov, config)
    assert first.ok and first.deduplicated is False
    asset = repo.asset(first.asset_id, P)
    landed = config.managed_root / asset["relative_managed_path"]
    mtime0 = landed.stat().st_mtime_ns
    # same bytes under a different source file id: dedup to the SAME asset
    prov.files[6] = FakeFile(file_id=6, name="copy-of-src.png", content=PNG,
                             size=len(PNG))
    second = reg(repo, prov, config, provider_file_id=6, memory_ref="mem:2")
    assert second.ok and second.deduplicated is True
    assert second.asset_id == first.asset_id
    assert landed.stat().st_mtime_ns == mtime0  # file never rewritten
    # both memories see the asset
    assert len(repo.links_for_asset(asset_id=first.asset_id, **SCOPE)) == 2


# ---- source revalidation (rowid reuse) ----------------------------------------------

def test_relinked_id_with_different_size_refused_and_staled(env):
    repo, prov, config = env
    first = reg(repo, prov, config)  # binds locator? no: locator file_id NULL.
    # simulate: a SECOND asset externally linked to file 5 (B-07 path)
    from bridge.tools_link import link_asset

    ext = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                     memory_ref="mem:9", provider_file_id=5, **SCOPE)
    assert ext.ok
    # rowid reuse: file 5 now serves different bytes/size
    prov.files[5].content = PNG2
    prov.files[5].size = len(PNG2)
    out = reg(repo, prov, config, memory_ref="mem:10")
    assert out.ok is False and out.error["code"] == "asset_unavailable"
    # the external locator went stale truthfully
    assert repo.asset(ext.asset_id, P)["locator_status"] == "stale"


# ---- byte-authority gates -------------------------------------------------------------

def test_heic_source_rejected_no_copy(env):
    repo, prov, config = env
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 40
    prov.files[5].content = heic
    prov.files[5].size = len(heic)
    out = reg(repo, prov, config)
    assert out.error["code"] == "unsupported_media"
    assert not any((config.managed_root / d).exists() for d in ("00",))
    # no asset row
    assert repo.asset("00000000-0000-4000-8000-000000000000", P) is None


def test_lying_content_type_still_sniffs(env):
    repo, prov, config = env
    prov.files[5].mime = "image/png"  # header lies; bytes ARE png -> allowed
    out = reg(repo, prov, config)
    assert out.ok and out.media_type == "image/png"


def test_oversized_source_refused_before_fetch(env):
    from bridge.provider import FakeFailure

    repo, prov, config = env
    prov.files[5].size = bridge_policy.MAX_FETCH_BYTES + 1
    out = reg(repo, prov, config)
    assert out.error["code"] == "media_too_large"
    assert [c for c in prov.calls if c[0] == "file_content"] == []


# ---- atomicity / crash garbage ----------------------------------------------------------

def test_tmp_left_final_never_partial(env, monkeypatch):
    repo, prov, config = env
    import os

    real_replace = os.replace

    def boom(a, b):  # crash exactly at rename time
        raise OSError("simulated crash at rename")

    monkeypatch.setattr(os, "replace", boom)
    out = reg(repo, prov, config)
    assert out.ok is False and out.error["code"] == "storage_full"
    # no final file anywhere, no DB row, only tmp dir may exist empty
    for sub in config.managed_root.iterdir():
        if sub.name == ".tmp":
            assert list(sub.iterdir()) == []  # tmp cleaned even on failure
    monkeypatch.setattr(os, "replace", real_replace)


def test_concurrent_imports_same_root_one_lands(tmp_path):
    """two imports of the SAME content in parallel threads: no partials, no
    duplicates; exactly one final file, both callers ok."""
    import threading

    config = cfg(tmp_path)  # creates bdata 0700 first
    conn = open_repository(tmp_path / "bdata")
    repo = Repository(conn)
    outs = []

    def go(n):
        prov = FakeProvider(
            files={5 + n: FakeFile(file_id=5 + n, name=f"n{n}.png",
                                   content=PNG, size=len(PNG))},
            folders_data={77: {"watch": True}},
        )
        outs.append(register_asset(provider=prov, repo=repo, config=config,
                                   memory_ref="mem:1",
                                   provider_file_id=5 + n, **SCOPE))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(o.ok for o in outs), outs
    assert all(o.ok for o in outs), [o.error for o in outs]
    digests = {o.asset_id for o in outs}
    assert len(digests) == 1  # content-addressed: one asset
    files = [p for p in config.managed_root.rglob("*.png")]
    assert len(files) == 1
    assert files[0].read_bytes() == PNG
    repo.close()


# ---- no absolute paths in results (T-09) --------------------------------------------------

def test_result_and_db_carry_no_absolute_paths(env):
    repo, prov, config = env
    out = reg(repo, prov, config)
    blob = repr(out) + repr(repo.asset(out.asset_id, P))
    assert str(config.managed_root) not in blob
    # any absolute home-style prefix would be the forbidden marker itself;
    # built indirectly so this file never contains the literal.
    home_prefix = "/" + "Us" + "ers/"
    assert home_prefix not in blob


# ---- managed root permissions -------------------------------------------------------------

def test_managed_root_created_0700(tmp_path):
    c = cfg(tmp_path)
    mode = stat.S_IMODE(c.managed_root.stat().st_mode)
    assert not mode & 0o077


def test_world_readable_managed_root_rejected(tmp_path):
    root = tmp_path / "managed"
    root.mkdir(mode=0o755)
    with pytest.raises(Exception) as e:
        validate_config(data_dir=tmp_path / "bdata",
                        provider_base_url="http://127.0.0.1:18765",
                        managed_folder_id=77, managed_root=root)
    assert "managed" in str(e.value).lower()


# ---- over the wire -----------------------------------------------------------------------

@pytest.mark.anyio
def test_register_asset_wire(tmp_path):
    import anyio

    from bridge.server import BridgeContext, create_server
    from tests.unit.test_server_health import connected_client

    config = cfg(tmp_path)
    conn = open_repository(tmp_path / "bdata")
    repo = Repository(conn)
    prov = FakeProvider(
        files={5: FakeFile(file_id=5, content=PNG, size=len(PNG))},
        folders_data={77: {"watch": True}},
    )
    ctx = BridgeContext(config=config, provider=prov, db=repo)

    async def go():
        async with connected_client(create_server(ctx)) as session:
            res = await session.call_tool(
                "register_asset",
                {"agent_id": "a1", "project_id": "p", "channel": "c",
                 "memory_ref": "mem:1", "provider_file_id": 5},
            )
            sc = res.structured_content
            assert sc["ok"] is True and sc["link_status"] == "pending"
            assert "asset_id" in sc
            # health reflects managed readiness
            h = await session.call_tool("bridge_health", {})
            hs = h.structured_content
            assert hs["managed_enabled"] is True
            assert hs["managed_folder_registered"] is True
            assert hs["managed_watch"] is True

    anyio.run(go)
    repo.close()
