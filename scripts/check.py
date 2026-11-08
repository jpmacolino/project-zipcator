"""Run every quality gate: ruff lint, ruff format check, mypy, pytest + coverage,
and a repo-hygiene check (no tracked file over 1 MB — SPEC §9.7).

Usage: uv run python scripts/check.py [--no-cov]
Exits non-zero if any gate fails. Prints a summary table at the end.
Always runs from the repository root, whatever the current directory is.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 1_000_000  # SPEC §9.7: "no data files > 1 MB"
# The uv lockfile is generated, not data; it is the only exemption.
SIZE_EXEMPT = frozenset({"uv.lock"})

GATES: list[tuple[str, list[str]]] = [
    ("ruff lint", ["ruff", "check", "."]),
    ("ruff format", ["ruff", "format", "--check", "."]),
    ("mypy", ["mypy"]),
    ("pytest", ["pytest", "--cov=zipcator", "--cov-report=term-missing"]),
]


def oversized_tracked_files(root: Path) -> list[tuple[str, int]]:
    """Return (path, bytes) for git-tracked files larger than ``MAX_FILE_BYTES``."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    big: list[tuple[str, int]] = []
    for rel in filter(None, out.split("\0")):
        path = root / rel
        if rel in SIZE_EXEMPT or not path.is_file():
            continue
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            big.append((rel, size))
    return big


def _size_gate() -> int:
    big = oversized_tracked_files(REPO_ROOT)
    for rel, size in big:
        print(f"  too large: {rel} ({size:,} bytes > {MAX_FILE_BYTES:,})")
    return 1 if big else 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    unknown = [a for a in args if a != "--no-cov"]
    if unknown:
        print(
            f"unknown argument(s): {' '.join(unknown)}\nusage: check.py [--no-cov]", file=sys.stderr
        )
        return 2
    no_cov = "--no-cov" in args
    results: list[tuple[str, bool, float]] = []
    for name, cmd in GATES:
        if no_cov and name == "pytest":
            cmd = ["pytest"]
        print(f"\n=== {name}: {' '.join(cmd)} ===", flush=True)
        start = time.perf_counter()
        code = subprocess.call([sys.executable, "-m", *cmd], cwd=REPO_ROOT)
        results.append((name, code == 0, time.perf_counter() - start))

    print("\n=== file size: tracked files <= 1 MB ===", flush=True)
    start = time.perf_counter()
    results.append(("file size", _size_gate() == 0, time.perf_counter() - start))

    print("\n=== summary ===")
    for name, ok, secs in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<12} {secs:6.1f}s")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
