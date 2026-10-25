"""B-04: configuration fail-closed rules (docs/12 §1, T-06, T-15)."""

from __future__ import annotations

import os
import stat

import pytest

from bridge.config import ConfigError, load_config, validate_config


def test_valid_minimal_config(tmp_path):
    cfg = validate_config(data_dir=tmp_path / "bdata", provider_base_url="http://127.0.0.1:18765")
    assert cfg.provider_instance_id == "127.0.0.1:18765"
    assert cfg.source_instance_id == ""
    mode = stat.S_IMODE((tmp_path / "bdata").stat().st_mode)
    assert not mode & 0o077  # group/other have no access


def test_world_writable_data_dir_refused(tmp_path):
    d = tmp_path / "bdata"
    d.mkdir()
    os.chmod(d, 0o777)
    with pytest.raises(ConfigError, match="other users"):
        validate_config(data_dir=d, provider_base_url="http://127.0.0.1:18765")


def test_inside_provider_data_dir_refused(tmp_path):
    prov = tmp_path / "provider-data"
    prov.mkdir()
    with pytest.raises(ConfigError, match="outside provider"):
        validate_config(
            data_dir=prov / "bridge",
            provider_base_url="http://127.0.0.1:18765",
            provider_data_dirs=(str(prov),),
        )


def test_provider_dir_sibling_allowed(tmp_path):
    prov = tmp_path / "provider-data"
    prov.mkdir()
    cfg = validate_config(
        data_dir=tmp_path / "bridge",
        provider_base_url="http://127.0.0.1:18765",
        provider_data_dirs=(str(prov),),
    )
    assert cfg.data_dir.exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:18765",
        "http://127.0.0.1.1:18765",
        "https://127.0.0.1:18765",
        "http://evil.example:18765",
        "http://127.0.0.1:18765/api",
        "http://user@127.0.0.1:18765",
        "http://127.0.0.1",  # no explicit port
        "",
    ],
)
def test_non_literal_loopback_refused(tmp_path, url):
    with pytest.raises(ConfigError, match="loopback"):
        validate_config(data_dir=tmp_path / "b", provider_base_url=url)


def test_caps_downward_only(tmp_path):
    with pytest.raises(ConfigError, match="downward"):
        validate_config(
            data_dir=tmp_path / "b",
            provider_base_url="http://127.0.0.1:18765",
            max_fetch_bytes=99_000_000,
        )
    cfg = validate_config(
        data_dir=tmp_path / "b",
        provider_base_url="http://127.0.0.1:18765",
        max_fetch_bytes=1_000_000,
    )
    assert cfg.max_fetch_bytes == 1_000_000


def test_load_config_requires_env(tmp_path):
    with pytest.raises(ConfigError, match="BRIDGE_DATA_DIR"):
        load_config({})
    with pytest.raises(ConfigError, match="BRIDGE_PROVIDER_URL"):
        load_config({"BRIDGE_DATA_DIR": str(tmp_path / "b")})
    cfg = load_config(
        {
            "BRIDGE_DATA_DIR": str(tmp_path / "b"),
            "BRIDGE_PROVIDER_URL": "http://127.0.0.1:18765",
            "BRIDGE_SOURCE_INSTANCE_ID": "lab-01",
        }
    )
    assert cfg.source_instance_id == "lab-01"


def test_config_error_messages_carry_no_paths(tmp_path):
    prov = tmp_path / "provider-data"
    prov.mkdir()
    with pytest.raises(ConfigError) as ei:
        validate_config(
            data_dir=prov / "bridge",
            provider_base_url="http://127.0.0.1:18765",
            provider_data_dirs=(str(prov),),
        )
    assert str(prov) not in str(ei.value)
    assert "/Users/" not in str(ei.value)
