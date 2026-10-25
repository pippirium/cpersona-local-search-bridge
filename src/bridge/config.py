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


def validate_config(
    *,
    data_dir: str | Path,
    provider_base_url: str,
    provider_instance_id: str = "",
    source_instance_id: str = "",
    provider_data_dirs: tuple[str, ...] = (),
    max_fetch_bytes: int = MAX_FETCH_BYTES,
    max_pixels: int = MAX_PIXELS,
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
    )
