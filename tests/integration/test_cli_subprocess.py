"""The CLI as a real process on the real artifact (marker ``data``; coordinates, no network).

In-process tests cannot prove pipe behaviour or console encodings: these spawn
``python -m zipcator`` with redirected/piped stdio exactly as a shell would.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.data

TAMPA = "27.9506, -82.4572"


def _env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env.update(extra)
    return env


def _run(args: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "zipcator", *args],
        capture_output=True,
        timeout=120,
        **kw,  # type: ignore[call-overload]
    )


def test_zips_with_csv_pipes_nothing_but_the_list_on_stdout(tmp_path: Path) -> None:
    target = tmp_path / "z.csv"
    p = _run([TAMPA, "-r", "3", "--zips", "--csv", str(target)], env=_env())
    assert p.returncode == 0
    out = p.stdout.decode("utf-8")
    assert out.strip().startswith("33602") and "Wrote" not in out and "overlap" not in out
    assert b"Wrote" in p.stderr and target.exists()


def test_zips_newline_output_uses_lf_not_crlf_when_piped() -> None:
    p = _run([TAMPA, "-r", "3", "--zips", "--newline"], env=_env())
    assert p.returncode == 0 and b"\r" not in p.stdout and p.stdout.count(b"\n") >= 5


def test_cp1252_redirected_output_with_a_cjk_csv_path_does_not_crash(tmp_path: Path) -> None:
    """A redirected Windows stdout is cp1252: the final 'Wrote ...' line used to raise
    UnicodeEncodeError for a path containing 東京."""
    target = tmp_path / "東京 目録.csv"
    p = _run([TAMPA, "-r", "2", "--csv", str(target)], env=_env(PYTHONIOENCODING="cp1252"))
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    assert target.exists() and b"Traceback" not in p.stderr
    assert b"Wrote" in p.stdout


def test_closed_pipe_is_quiet_and_still_writes_the_csv(tmp_path: Path) -> None:
    """`zipcator ... | head -1`: no traceback, exit 0, and the CSV is not lost."""
    target = tmp_path / "big.csv"
    cmd = [sys.executable, "-m", "zipcator", "40.7128, -74.0060", "-r", "100", "--csv", str(target)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env())
    assert proc.stdout is not None and proc.stderr is not None
    first = proc.stdout.readline()  # read one line, then hang up like `head -1`
    proc.stdout.close()
    err = proc.stderr.read()
    code = proc.wait(timeout=120)
    assert first.strip() and b"Traceback" not in err and b"BrokenPipe" not in err
    assert code == 0 and target.exists() and target.stat().st_size > 10_000


def test_negative_first_coordinate_hint_on_the_real_command_line() -> None:
    p = _run(["-14.27,-170.7"], env=_env())
    assert p.returncode == 2 and b"--" in p.stderr and b"negative" in p.stderr.lower()
    ok = _run(["-r", "5", "--", "-14.27,-170.7"], env=_env())  # options go before the --
    assert ok.returncode == 0 and b"ZIP code" in ok.stdout
