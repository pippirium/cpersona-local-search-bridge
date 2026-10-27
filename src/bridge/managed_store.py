"""No-follow, no-clobber publication for explicit managed image input."""
from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import stat
import uuid
from pathlib import Path

from bridge.errors import BridgeError

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW


def _open_root(root: Path) -> int:
    path = Path(os.path.abspath(root))
    parts = path.parts
    fd = os.open(parts[0], _DIR_FLAGS)
    try:
        for part in parts[1:]:
            next_fd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise BridgeError("path_conflict")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _verify_file(dir_fd: int, name: str, data: bytes) -> None:
    fd = os.open(name, _FILE_FLAGS, dir_fd=dir_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != len(data):
            raise BridgeError("path_conflict")
        digest = hashlib.sha256()
        total = 0
        while total <= len(data):
            block = os.read(fd, min(131072, len(data) + 1 - total))
            if not block:
                break
            total += len(block)
            digest.update(block)
        after = os.fstat(fd)
        entry = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if (total != len(data) or digest.digest() != hashlib.sha256(data).digest()
                or (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
                != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size)
                or (entry.st_dev, entry.st_ino) != (info.st_dev, info.st_ino)):
            raise BridgeError("path_conflict")
    finally:
        os.close(fd)


def publish(root: Path, relative_path: str, data: bytes) -> None:
    """Publish exact bytes atomically; existing targets are verified, never overwritten."""
    parts = relative_path.split("/")
    if len(parts) != 2 or not all(parts) or any(p in (".", "..") for p in parts):
        raise BridgeError("path_conflict")
    root_fd = shard_fd = None
    temporary = None
    try:
        root_fd = _open_root(root)
        fcntl.flock(root_fd, fcntl.LOCK_EX)
        root_stat = os.fstat(root_fd)
        shard, name = parts
        try:
            os.mkdir(shard, mode=0o700, dir_fd=root_fd)
            os.fsync(root_fd)
        except FileExistsError:
            pass
        shard_fd = os.open(shard, _DIR_FLAGS, dir_fd=root_fd)
        shard_info = os.fstat(shard_fd)
        if shard_info.st_uid != os.geteuid() or shard_info.st_mode & 0o077:
            raise BridgeError("path_conflict")
        temporary = "." + uuid.uuid4().hex + ".part"
        temp_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o600, dir_fd=shard_fd)
        try:
            with os.fdopen(temp_fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, name, src_dir_fd=shard_fd, dst_dir_fd=shard_fd,
                        follow_symlinks=False)
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary, dir_fd=shard_fd)
            temporary = None
        os.fsync(shard_fd)
        _verify_file(shard_fd, name, data)
        reopened = _open_root(root)
        try:
            current = os.fstat(reopened)
            entry = os.stat(shard, dir_fd=reopened, follow_symlinks=False)
            if ((root_stat.st_dev, root_stat.st_ino) != (current.st_dev, current.st_ino)
                    or (entry.st_dev, entry.st_ino) != (shard_info.st_dev, shard_info.st_ino)):
                raise BridgeError("path_conflict")
        finally:
            os.close(reopened)
    except BridgeError:
        raise
    except OSError as exc:
        if exc.errno in (errno.ENOSPC, errno.EDQUOT):
            raise BridgeError("storage_full") from None
        if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EEXIST):
            raise BridgeError("path_conflict") from None
        raise BridgeError("storage_error") from None
    finally:
        if temporary is not None and shard_fd is not None:
            try:
                os.unlink(temporary, dir_fd=shard_fd)
            except OSError:
                pass
        if shard_fd is not None:
            os.close(shard_fd)
        if root_fd is not None:
            os.close(root_fd)


def verify_digest(root: Path, relative_path: str, digest: str, expected_size: int) -> bool:
    """Recheck a managed copy without returning its bytes or following links."""
    root_fd = shard_fd = fd = None
    try:
        parts = relative_path.split("/")
        if len(parts) != 2 or not all(parts) or any(p in (".", "..") for p in parts):
            return False
        root_fd = _open_root(root)
        fcntl.flock(root_fd, fcntl.LOCK_EX)
        root_info = os.fstat(root_fd)
        shard_fd = os.open(parts[0], _DIR_FLAGS, dir_fd=root_fd)
        shard_info = os.fstat(shard_fd)
        if shard_info.st_uid != os.geteuid() or shard_info.st_mode & 0o077:
            return False
        fd = os.open(parts[1], _FILE_FLAGS, dir_fd=shard_fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size != expected_size):
            return False
        h = hashlib.sha256()
        remaining = expected_size
        while remaining:
            block = os.read(fd, min(131072, remaining))
            if not block:
                return False
            h.update(block)
            remaining -= len(block)
        after = os.fstat(fd)
        entry = os.stat(parts[1], dir_fd=shard_fd, follow_symlinks=False)
        reopened = _open_root(root)
        try:
            current = os.fstat(reopened)
            shard_entry = os.stat(parts[0], dir_fd=reopened, follow_symlinks=False)
        finally:
            os.close(reopened)
        return (h.hexdigest() == digest
                and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                and (entry.st_dev, entry.st_ino) == (before.st_dev, before.st_ino)
                and (root_info.st_dev, root_info.st_ino) == (current.st_dev, current.st_ino)
                and (shard_info.st_dev, shard_info.st_ino) == (shard_entry.st_dev, shard_entry.st_ino))
    except (OSError, BridgeError, TypeError, ValueError):
        return False
    finally:
        for item in (fd, shard_fd, root_fd):
            if item is not None:
                os.close(item)


def copy_is_missing(root: Path, relative_path: str) -> bool:
    """Distinguish absence without resolving a stored path outside the root."""
    parts = relative_path.split("/")
    if len(parts) != 2 or not all(parts) or any(part in (".", "..") for part in parts):
        return False
    root_fd = shard_fd = None
    try:
        root_fd = _open_root(root)
        shard_fd = os.open(parts[0], _DIR_FLAGS, dir_fd=root_fd)
        os.stat(parts[1], dir_fd=shard_fd, follow_symlinks=False)
        return False
    except FileNotFoundError:
        return True
    except (OSError, BridgeError):
        return False
    finally:
        if shard_fd is not None:
            os.close(shard_fd)
        if root_fd is not None:
            os.close(root_fd)
