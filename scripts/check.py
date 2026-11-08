"""Run every quality gate: ruff lint, ruff format check, mypy, pytest + coverage.

Usage: uv run python scripts/check.py [--no-cov]
Exits non-zero if any gate fails. Prints a summary table at the end.
"""

from __future__ import annotations

import subprocess
import sys
import time

GATES: list[tuple[str, list[str]]] = [
    ("ruff lint", ["ruff", "check", "."]),
    ("ruff format", ["ruff", "format", "--check", "."]),
    ("mypy", ["mypy"]),
    ("pytest", ["pytest", "--cov=zipcator", "--cov-report=term-missing"]),
]


def main() -> int:
    no_cov = "--no-cov" in sys.argv
    results: list[tuple[str, bool, float]] = []
    for name, cmd in GATES:
        if no_cov and name == "pytest":
            cmd = ["pytest"]
        print(f"\n=== {name}: {' '.join(cmd)} ===", flush=True)
        start = time.perf_counter()
        code = subprocess.call([sys.executable, "-m", *cmd])
        results.append((name, code == 0, time.perf_counter() - start))

    print("\n=== summary ===")
    for name, ok, secs in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<12} {secs:6.1f}s")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
