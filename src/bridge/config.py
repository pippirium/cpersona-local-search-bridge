"""Configuration (B-04): fail-closed validation, 0700 data directory.

Rules (docs/12 §1, §4; docs/02 §Platform):
- the provider base URL must be a literal IPv4 loopback http URL (T-06);
- the Bridge data directory is created 0700, must not be world-writable and
  must not sit inside a provider's data directory (T-15) — provider data dirs
  are configured values, never guessed;
- invalid configuration aborts startup with sanitized fixed wording (no paths
  in the message text).
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from bridge_policy import (
    CONNECT_TIMEOUT_S,
    READ_TIMEOUT_S,
    TOTAL_CONTENT_CAP_S,
    MAX_FETCH_BYTES,
    MAX_PIXELS,
)

_BRIDGE_VERSION = "0.0.0"
_SOURCE_INSTANCE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ConfigError(Exception):
    """Fixed-wording startup failure. Never carries paths or values."""


def is_literal_loopback(base_url: str) -> bool:
    parts = urlsplit(base_url)
    try:
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "http"
        and parts.hostname == "127.0.0.1"
        and parts.path in ("", "/")
        and not parts.username
        and not parts.password
        and port is not None
        and 1 <= port <= 65535
    )


@dataclass(frozen=True)
class BridgeConfig:
    data_dir: Path
    provider_base_url: str
    provider_instance_id: str
    source_instance_id: str
    max_fetch_bytes: int = MAX_FETCH_BYTES
    max_pixels: int = MAX_PIXELS
    connect_timeout_s: float = CONNECT_TIMEOUT_S
    read_timeout_s: float = READ_TIMEOUT_S
    total_content_cap_s: float = TOTAL_CONTENT_CAP_S
    # B-10 managed import: OPTIONAL opt-in storage. None = feature disabled and
    # register_asset refuses. Managed storage must never be an implicit
    # consequence of having a data dir (docs/03 §5, docs/08 §Managed files).
    managed_folder_id: int | None = None
    managed_root: Path | None = None
    staging_root: Path | None = None
    staging_input_root: Path | None = None


def validate_config(
    *,
    data_dir: str | Path,
    provider_base_url: str,
    provider_instance_id: str = "",
    source_instance_id: str = "",
    provider_data_dirs: tuple[str, ...] = (),
    max_fetch_bytes: int = MAX_FETCH_BYTES,
    max_pixels: int = MAX_PIXELS,
    managed_folder_id: int | None = None,
    managed_root: str | Path | None = None,
    staging_root: str | Path | None = None,
    staging_input_root: str | Path | None = None,
) -> BridgeConfig:
    """Validate and materialize the configuration; raises ConfigError only."""
    if not is_literal_loopback(provider_base_url):
        raise ConfigError("The provider URL must be a literal http://127.0.0.1:<port> loopback address.")
    if source_instance_id and not _SOURCE_INSTANCE_RE.match(source_instance_id):
        raise ConfigError("source_instance_id must match [A-Za-z0-9._-]{1,64} (empty is the single-instance default).")
    if not (1 <= max_fetch_bytes <= MAX_FETCH_BYTES):
        raise ConfigError("max_fetch_bytes may only be configured downward from the default bound.")
    if not (1_000_000 <= max_pixels <= MAX_PIXELS):
        raise ConfigError("max_pixels may only be configured downward from the default bound.")

    path = Path(data_dir).expanduser()
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        raise ConfigError("The Bridge data directory cannot be created or is not accessible.") from None
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        raise ConfigError("The Bridge data directory cannot be inspected.") from None
    if mode & stat.S_IROTH or mode & stat.S_IWOTH or mode & stat.S_IXOTH:
        raise ConfigError("The Bridge data directory must not be accessible to other users (needs 0700).")

    resolved = path.resolve()

    # -- managed import folder (B-10): all-or-nothing opt-in ------------------
    m_root: Path | None = None
    if (managed_folder_id is None) != (managed_root is None):
        raise ConfigError(
            "Managed import needs both BRIDGE_MANAGED_FOLDER_ID and "
            "BRIDGE_MANAGED_ROOT (or neither)."
        )
    if managed_root is not None:
        assert managed_folder_id is not None and managed_folder_id > 0, (
            "managed_folder_id must be a positive provider folder id"
        )
        mr = Path(managed_root).expanduser()
        # The managed root IS a provider-registered folder by contract, so the
        # provider-data-dir containment rule below must not reject it: the
        # containment check applies to the SQLite data dir only.
        try:
            mr.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            raise ConfigError(
                "The managed asset folder cannot be created or is not accessible."
            ) from None
        try:
            mmode = stat.S_IMODE(mr.stat().st_mode)
        except OSError:
            raise ConfigError(
                "The managed asset folder cannot be inspected."
            ) from None
        if mmode & 0o077:
            raise ConfigError(
                "The managed asset folder must not be accessible to other users (needs 0700)."
            )
        m_root = mr

    staging: Path | None = None
    input_root: Path | None = None
    if (staging_root is None) != (staging_input_root is None):
        raise ConfigError("Staging requires both staging and input roots.")
    if staging_root is not None:
        staging = Path(staging_root).expanduser()
        input_root = Path(staging_input_root).expanduser()
        try:
            if not input_root.is_dir():
                raise ConfigError("Staging needs a private root and an existing input root.")
            st = staging.resolve()
            inp = input_root.resolve()
            managed = m_root.resolve() if m_root else None
            if (st == inp or st in inp.parents or inp in st.parents
                    or st == resolved or st in resolved.parents or resolved in st.parents
                    or (managed is not None and (st == managed or st in managed.parents or managed in st.parents))):
                raise ConfigError("Staging must be separate from input, metadata and managed roots.")
            staging.mkdir(mode=0o700, parents=True, exist_ok=True)
            if stat.S_IMODE(staging.stat().st_mode) & 0o077:
                raise ConfigError("Staging needs a private root and an existing input root.")
        except OSError:
            raise ConfigError("Staging roots cannot be accessed.") from None

    for provider_dir in provider_data_dirs:
        try:
            pd = Path(provider_dir).expanduser().resolve()
        except OSError:
            raise ConfigError("A configured provider data directory cannot be resolved.") from None
        if resolved == pd or pd in resolved.parents:
            raise ConfigError("The Bridge data directory must live outside provider data directories.")

    pid = provider_instance_id or urlsplit(provider_base_url).netloc
    return BridgeConfig(
        data_dir=path,
        provider_base_url=provider_base_url,
        provider_instance_id=pid,
        source_instance_id=source_instance_id or "",
        max_fetch_bytes=max_fetch_bytes,
        max_pixels=max_pixels,
        managed_folder_id=managed_folder_id,
        managed_root=m_root,
        staging_root=staging,
        staging_input_root=input_root,
    )


def load_config(env: dict[str, str]) -> BridgeConfig:
    """Environment-driven config (stdio deployments). Missing required values
    are startup errors with fixed wording, not prompts."""
    data_dir = env.get("BRIDGE_DATA_DIR", "")
    if not data_dir:
        raise ConfigError("BRIDGE_DATA_DIR is required.")
    base_url = env.get("BRIDGE_PROVIDER_URL", "")
    if not base_url:
        raise ConfigError("BRIDGE_PROVIDER_URL is required.")
    return validate_config(
        data_dir=data_dir,
        provider_base_url=base_url,
        provider_instance_id=env.get("BRIDGE_PROVIDER_INSTANCE_ID", ""),
        source_instance_id=env.get("BRIDGE_SOURCE_INSTANCE_ID", ""),
        provider_data_dirs=tuple(
            d for d in (env.get("BRIDGE_PROVIDER_DATA_DIRS", "").split(os.pathsep)) if d
        ),
        managed_folder_id=_managed_folder_id_from(env),
        managed_root=env.get("BRIDGE_MANAGED_ROOT", "") or None,
        staging_root=env.get("BRIDGE_STAGING_ROOT", "") or None,
        staging_input_root=env.get("BRIDGE_STAGING_INPUT_ROOT", "") or None,
    )
def _managed_folder_id_from(env: dict[str, str]) -> int | None:
    raw = env.get("BRIDGE_MANAGED_FOLDER_ID", "")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError("BRIDGE_MANAGED_FOLDER_ID must be a positive integer.") from None
    if value <= 0:
        raise ConfigError("BRIDGE_MANAGED_FOLDER_ID must be a positive integer.")
    return value

