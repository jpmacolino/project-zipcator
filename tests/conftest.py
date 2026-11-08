"""Shared pytest fixtures and marker policy (docs/SPEC.md §8.1).

Marker policy
-------------
* ``tests/integration/**`` is implicitly ``data`` and ``tests/live/**`` is implicitly
  ``network``, so a forgotten decorator cannot leak them into the default run.
* ``network`` and ``visual`` tests only run when ``-m`` explicitly names them.
  ``addopts`` already deselects them, but a user-supplied ``-m "not slow"`` *replaces*
  that expression; this hook keeps live-API and browser tests opt-in regardless.
* ``data`` tests fail (not skip) with a clear instruction when the artifact is missing,
  so ``pytest -m data`` can never go green without actually testing anything.

Synthetic geodata fixtures live in the sibling modules/fixtures added per phase.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

OPT_IN_MARKERS = ("network", "visual")
DIR_MARKERS = {"integration": "data", "live": "network"}


def _data_dir() -> Path:
    env = os.environ.get("ZIPCATOR_DATA_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[1] / "data"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    markexpr = config.getoption("markexpr", default="") or ""
    for item in items:
        parts = Path(str(item.path)).parts
        for dirname, marker in DIR_MARKERS.items():
            if dirname in parts and marker not in {m.name for m in item.iter_markers()}:
                item.add_marker(getattr(pytest.mark, marker))
        for marker in OPT_IN_MARKERS:
            if item.get_closest_marker(marker) and marker not in markexpr:
                item.add_marker(
                    pytest.mark.skip(
                        reason=f"'{marker}' tests are opt-in: run `pytest -m {marker}`"
                    )
                )


@pytest.fixture(autouse=True)
def _require_data_artifact(request: pytest.FixtureRequest) -> Iterator[None]:
    if request.node.get_closest_marker("data"):
        path = _data_dir() / "zcta.parquet"
        if not path.exists():
            pytest.fail(
                f"{path} not found. Build it first: uv run python scripts/build_data.py",
                pytrace=False,
            )
    yield
