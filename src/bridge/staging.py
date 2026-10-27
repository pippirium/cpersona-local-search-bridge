"""Operator-only source selection and staging; MCP accepts tokens, never paths."""
from __future__ import annotations

import fcntl
import hashlib
import os
import secrets
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bridge.config import BridgeConfig
from bridge.errors import BridgeError
from bridge.image_input import validate_image
from bridge.managed_store import publish, remove_expired_copy
from bridge.repository import Repository, StorageError, normalize_scope

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def token_hash(token: str) -> str:
    if (not isinstance(token, str) or len(token) != 43
            or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in token)):
        raise BridgeError("invalid_request")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def stage_relpath(token_digest: str) -> str:
    return f"{token_digest[:2]}/{token_digest}.stage"


def _open_input_root(root: Path) -> int:
    parts = Path(os.path.abspath(root)).parts
    fd = os.open(parts[0], _DIR_FLAGS)
    try:
        for part in parts[1:]:
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        if os.fstat(fd).st_uid != os.geteuid():
            raise BridgeError("invalid_request")
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_source(root: Path, relative_path: str, max_bytes: int) -> tuple[bytes, os.stat_result]:
    """Read a completed, explicitly selected regular file under an allowed root."""
    if (not isinstance(relative_path, str) or os.path.isabs(relative_path)
            or "\x00" in relative_path):
        raise BridgeError("invalid_request")
    parts = relative_path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise BridgeError("invalid_request")
    root_fd = directory = file_fd = None
    try:
        root_fd = _open_input_root(root)
        root_info = os.fstat(root_fd)
        directory = os.dup(root_fd)
        for part in parts[:-1]:
            child = os.open(part, _DIR_FLAGS, dir_fd=directory)
            os.close(directory)
            directory = child
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        fcntl.flock(file_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        before = os.fstat(file_fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size <= 0):
            raise BridgeError("invalid_request")
        if before.st_size > max_bytes:
            raise BridgeError("media_too_large")
        data = bytearray()
        while len(data) < before.st_size:
            chunk = os.read(file_fd, min(131072, before.st_size - len(data)))
            if not chunk:
                raise BridgeError("invalid_request")
            data.extend(chunk)
        after = os.fstat(file_fd)
        entry = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        reopened = _open_input_root(root)
        walk = reopened
        try:
            current = os.fstat(reopened)
            for part in parts[:-1]:
                child = os.open(part, _DIR_FLAGS, dir_fd=walk)
                if walk != reopened:
                    os.close(walk)
                walk = child
            current_entry = os.stat(parts[-1], dir_fd=walk, follow_symlinks=False)
        finally:
            if walk != reopened:
                os.close(walk)
            os.close(reopened)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or after.st_nlink != 1 or entry.st_nlink != 1
                or (entry.st_dev, entry.st_ino) != (before.st_dev, before.st_ino)
                or (current_entry.st_dev, current_entry.st_ino) != (before.st_dev, before.st_ino)
                or (root_info.st_dev, root_info.st_ino) != (current.st_dev, current.st_ino)):
            raise BridgeError("invalid_request")
        return bytes(data), before
    except BridgeError:
        raise
    except OSError:
        raise BridgeError("invalid_request") from None
    finally:
        for fd in (file_fd, directory, root_fd):
            if fd is not None:
                os.close(fd)


def stage_image(*, config: BridgeConfig, repo: Repository, scope: tuple[str, str, str, str],
                relative_path: str, confirmed: bool) -> str:
    if (confirmed is not True or config.staging_root is None or config.staging_input_root is None
            or not all(isinstance(value, str) for value in scope)):
        raise BridgeError("invalid_request")
    try:
        scope = normalize_scope(*scope)
        data, evidence = read_source(config.staging_input_root, relative_path, config.max_fetch_bytes)
        image = validate_image(data, max_bytes=config.max_fetch_bytes, max_pixels=config.max_pixels)
        token = secrets.token_urlsafe(32)
        digest = token_hash(token)
        stage_info = publish(config.staging_root, stage_relpath(digest), data)
        expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
        repo.record_staged_image(scope=scope, token_hash=digest, digest=image.sha256,
            media_type=image.media_type, size_bytes=len(data), source_device=evidence.st_dev,
            source_inode=evidence.st_ino, source_mtime_ns=evidence.st_mtime_ns,
            stage_device=stage_info.st_dev, stage_inode=stage_info.st_ino,
            stage_mtime_ns=stage_info.st_mtime_ns, expires_at=expires)
        return token
    except StorageError:
        raise BridgeError("storage_error") from None


def gc_expired_staging(*, config: BridgeConfig, repo: Repository, confirmed: bool) -> dict:
    """Explicit, bounded cleanup of expired ledger-owned temporary files only."""
    if confirmed is not True or config.staging_root is None:
        raise BridgeError("invalid_request")
    removed = blocked = 0
    try:
        for digest, device, inode, mtime in repo.expired_staged_images(limit=100):
            if (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                    or not remove_expired_copy(config.staging_root, stage_relpath(digest), (device, inode, mtime))):
                blocked += 1
                continue
            if repo.delete_expired_staged_image(token_hash=digest):
                removed += 1
        return {"removed": removed, "blocked": blocked}
    except StorageError:
        raise BridgeError("storage_error") from None
