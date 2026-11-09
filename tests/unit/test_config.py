"""Tests for zipcator.config (SPEC §6 Config: env vars only)."""

from __future__ import annotations

from pathlib import Path

import pytest

import zipcator
from zipcator import config


def test_default_data_dir_is_repo_data_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZIPCATOR_DATA_DIR", raising=False)
    assert config.data_dir() == Path(__file__).resolve().parents[2] / "data"
    assert config.zcta_path().name == "zcta.parquet"
    assert config.metadata_path().name == "metadata.json"
    assert config.raw_dir() == config.data_dir() / "raw"


def test_data_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path))
    assert config.data_dir() == tmp_path
    assert config.zcta_path() == tmp_path / "zcta.parquet"


def test_user_agent_default_names_project_version_and_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ZIPCATOR_USER_AGENT", raising=False)
    ua = config.user_agent()
    assert ua.startswith(f"zipcator/{zipcator.__version__} (+")
    assert config.REPO_URL in ua


def test_user_agent_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZIPCATOR_USER_AGENT", "my-app/1.0 (me@example.com)")
    assert config.user_agent() == "my-app/1.0 (me@example.com)"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_nominatim_disabled_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ZIPCATOR_DISABLE_NOMINATIM", value)
    assert config.nominatim_disabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "garbage"])
def test_nominatim_disabled_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ZIPCATOR_DISABLE_NOMINATIM", value)
    assert config.nominatim_disabled() is False


def test_nominatim_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZIPCATOR_DISABLE_NOMINATIM", raising=False)
    assert config.nominatim_disabled() is False


def test_census_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    assert config.census_api_key() is None
    monkeypatch.setenv("CENSUS_API_KEY", "  abc123 ")
    assert config.census_api_key() == "abc123"
    monkeypatch.setenv("CENSUS_API_KEY", "   ")
    assert config.census_api_key() is None


def test_data_dir_expands_a_leading_tilde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", "~/zipcator-data")
    assert config.data_dir() == Path.home() / "zipcator-data"
    assert "~" not in str(config.zcta_path())
