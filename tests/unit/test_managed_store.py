import hashlib
import os

import pytest

from bridge.errors import BridgeError
from bridge.managed_store import publish


def relative(data):
    digest = hashlib.sha256(data).hexdigest()
    return f"{digest[:2]}/{digest}.png"


def test_atomic_copy_and_verified_reuse(tmp_path):
    data = b"synthetic"
    rel = relative(data)
    publish(tmp_path, rel, data)
    original = (tmp_path / rel).stat()
    publish(tmp_path, rel, data)
    assert (tmp_path / rel).read_bytes() == data
    assert (tmp_path / rel).stat().st_ino == original.st_ino
    assert list((tmp_path / rel.split('/')[0]).iterdir()) == [tmp_path / rel]


def test_existing_conflict_and_links_never_overwritten(tmp_path):
    data = b"synthetic"
    rel = relative(data)
    target = tmp_path / rel
    target.parent.mkdir(mode=0o700)
    target.write_bytes(b"different")
    with pytest.raises(BridgeError, match="path_conflict"):
        publish(tmp_path, rel, data)
    assert target.read_bytes() == b"different"
    target.unlink()
    foreign = tmp_path / "foreign"
    foreign.write_bytes(data)
    os.link(foreign, target)
    with pytest.raises(BridgeError, match="path_conflict"):
        publish(tmp_path, rel, data)
    assert foreign.read_bytes() == data


def test_symlinked_shard_and_root_are_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    data = b"synthetic"
    rel = relative(data)
    (root / rel.split('/')[0]).symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(BridgeError, match="path_conflict"):
        publish(root, rel, data)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(BridgeError, match="path_conflict"):
        publish(alias, rel, data)


def test_disk_full_removes_temporary_file(tmp_path, monkeypatch):
    import errno
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "synthetic")
    monkeypatch.setattr(os, "link", full)
    data = b"synthetic"
    rel = relative(data)
    with pytest.raises(BridgeError, match="storage_full"):
        publish(tmp_path, rel, data)
    assert not list((tmp_path / rel.split('/')[0]).iterdir())
