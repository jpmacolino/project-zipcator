"""scripts/build_data.py is a thin CLI wrapper over zipcator.data_build (SPEC §4.3)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from zipcator import data_build

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "zipcator_build_data_script", ROOT / "scripts" / "build_data.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


script = _load()


def test_flags_are_passed_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def fake_build(data_dir: Path, **kw: Any) -> data_build.BuildResult:
        seen.update(kw, data_dir=data_dir)
        return data_build.BuildResult(path=data_dir / "zcta.parquet", metadata={"row_count": 7})

    monkeypatch.setattr(data_build, "build", fake_build)
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    code = script.main(["--refresh", "--with-population", "--census-api-key", "KEY"])
    assert code == 0
    assert seen["refresh"] is True and seen["with_population"] is True
    assert seen["census_api_key"] == "KEY" and seen["data_dir"] == tmp_path
    assert "7" in capsys.readouterr().out


def test_key_falls_back_to_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def fake_build(data_dir: Path, **kw: Any) -> data_build.BuildResult:
        seen.update(kw)
        return data_build.BuildResult(path=data_dir / "z", metadata={"row_count": 1})

    monkeypatch.setattr(data_build, "build", fake_build)
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CENSUS_API_KEY", "ENVKEY")
    script.main(["--with-population"])
    assert seen["census_api_key"] == "ENVKEY" and seen["refresh"] is False


def test_build_error_gives_nonzero_exit_and_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(data_dir: Path, **kw: Any) -> data_build.BuildResult:
        raise data_build.BuildError("download failed: try again")

    monkeypatch.setattr(data_build, "build", boom)
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path))
    assert script.main([]) == 1
    assert "download failed: try again" in capsys.readouterr().err


def test_unknown_flag_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        script.main(["--nope"])
    assert exc.value.code == 2


def test_warnings_are_visibly_marked_but_progress_is_plain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import logging

    def noisy_build(data_dir: Path, **kw: Any) -> data_build.BuildResult:
        log = logging.getLogger("zipcator.data_build")
        log.info("downloading things")
        log.warning("12% of ZIPs have no city name")
        return data_build.BuildResult(path=data_dir / "z", metadata={"row_count": 1})

    monkeypatch.setattr(data_build, "build", noisy_build)
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path))
    script.main([])
    out = capsys.readouterr().out
    assert "\ndownloading things\n" in "\n" + out  # plain progress line
    assert "WARNING: 12% of ZIPs have no city name" in out
