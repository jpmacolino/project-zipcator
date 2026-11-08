"""Tests for scripts/check.py (QA phase 0 #L2, #L3)."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("zipcator_check", ROOT / "scripts" / "check.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check = _load()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    return tmp_path


def test_oversized_flags_big_tracked_file(repo: Path) -> None:
    (repo / "big.bin").write_bytes(b"x" * (check.MAX_FILE_BYTES + 1))
    (repo / "small.txt").write_text("hi")
    _git(repo, "add", ".")
    assert [p for p, _ in check.oversized_tracked_files(repo)] == ["big.bin"]


def test_oversized_ignores_untracked_and_exempt(repo: Path) -> None:
    (repo / "untracked.bin").write_bytes(b"x" * (check.MAX_FILE_BYTES + 1))
    (repo / "uv.lock").write_bytes(b"x" * (check.MAX_FILE_BYTES + 1))
    _git(repo, "add", "uv.lock")
    assert check.oversized_tracked_files(repo) == []


def test_exactly_at_limit_is_allowed(repo: Path) -> None:
    (repo / "edge.bin").write_bytes(b"x" * check.MAX_FILE_BYTES)
    _git(repo, "add", ".")
    assert check.oversized_tracked_files(repo) == []


def test_repo_itself_has_no_oversized_files() -> None:
    assert check.oversized_tracked_files(ROOT) == []


def test_unknown_flag_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    assert check.main(["--bogus"]) == 2
    assert "unknown" in capsys.readouterr().err.lower()


def test_gates_run_from_repo_root_regardless_of_cwd() -> None:
    assert check.REPO_ROOT == ROOT
