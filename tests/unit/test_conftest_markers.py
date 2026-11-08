"""Regression tests for the marker policy in tests/conftest.py (QA phase 0 #M1, #M2).

Uses ``pytester`` with the repo's *real* ``[tool.pytest.ini_options]`` so the default
``addopts`` and the conftest hooks are exercised together.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

TEST_BODY = """
import pytest

def test_plain():
    pass

@pytest.mark.network
def test_net():
    print("NETWORK TEST RAN")

@pytest.mark.visual
def test_vis():
    print("VISUAL TEST RAN")

@pytest.mark.slow
def test_slow():
    pass
"""


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    """A scratch project with the repo's real pytest config and conftest."""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ini = cfg["tool"]["pytest"]["ini_options"]
    pytester.makepyprojecttoml(
        "[tool.pytest.ini_options]\n"
        f'addopts = "{ini["addopts"]}"\n'
        "markers = [\n" + "".join(f'    "{m}",\n' for m in ini["markers"]) + "]\n"
    )
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    pytester.makeconftest(conftest.replace('pytest_plugins = ["pytester"]', ""))
    return pytester


def test_default_run_excludes_network_and_visual(project: pytest.Pytester) -> None:
    project.makepyfile(test_body=TEST_BODY)
    result = project.runpytest_subprocess("-s")
    result.assert_outcomes(passed=1, deselected=3)


def test_overriding_m_does_not_enable_network_or_visual(project: pytest.Pytester) -> None:
    """`-m "not slow"` replaces addopts' -m; network/visual must still not run."""
    project.makepyfile(test_body=TEST_BODY)
    result = project.runpytest_subprocess("-m", "not slow", "-s")
    result.assert_outcomes(passed=1, skipped=2, deselected=1)
    result.stdout.no_fnmatch_line("*NETWORK TEST RAN*")
    result.stdout.no_fnmatch_line("*VISUAL TEST RAN*")


def test_explicit_network_marker_runs_network(project: pytest.Pytester) -> None:
    project.makepyfile(test_body=TEST_BODY)
    result = project.runpytest_subprocess("-m", "network", "-s")
    result.assert_outcomes(passed=1, deselected=3)
    result.stdout.fnmatch_lines(["*NETWORK TEST RAN*"])


def test_directory_implies_marker(project: pytest.Pytester) -> None:
    """Tests under integration/ and live/ without decorators are still data/network."""
    project.mkdir("integration")
    project.mkdir("live")
    project.makepyfile(**{"integration/test_i": "def test_i():\n    raise AssertionError"})
    project.makepyfile(**{"live/test_l": "def test_l():\n    raise AssertionError"})
    result = project.runpytest_subprocess()
    result.assert_outcomes(deselected=2)


def test_data_marker_errors_with_instruction_when_artifact_missing(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    project.makepyfile(
        test_d="import pytest\n\n@pytest.mark.data\ndef test_d():\n    raise AssertionError\n"
    )
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(project.path / "nope"))
    result = project.runpytest_subprocess("-m", "data")
    # A fixture-setup failure is reported as an error; the point is it is loud, not skipped.
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*build_data.py*"])
