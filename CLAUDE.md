# Zipcator — instructions for Claude Code

Zipcator: enter a US address + radius (miles) → every ZIP (Census ZCTA) whose
area overlaps the circle, with overlap %, table, map, CSV/ZIP-list export.
Python + Streamlit, MIT-licensed, public GitHub repo.

**The spec is the source of truth: read `docs/SPEC.md` in full before writing
code.** If the spec is ambiguous or wrong, make a reasonable call, record it in
`docs/DECISIONS.md` (date, decision, why, alternatives), and keep going. Only
stop to ask the user for things that are irreversible or outside the repo
(e.g., pushing to GitHub, changing the license, adding a paid service).

## Environment
- Primary machine is **Windows**. Use `uv` for everything (install it if
  missing: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`).
  `uv` manages the Python version (`.python-version` = 3.12).
- Use `pathlib` everywhere; never hard-code `/` or `\` separators.
- Commands:
  - setup: `uv sync`
  - tests (default, fast): `uv run pytest`
  - with coverage: `uv run pytest --cov=zipcator --cov-report=term-missing`
  - data tests: `uv run pytest -m data` · live API: `uv run pytest -m network`
  - benchmarks: `uv run pytest -m slow`
  - visual QA (one-time setup: `uv sync --group visual` then
    `uv run playwright install chromium`): `uv run pytest -m visual`
  - lint/format: `uv run ruff check . --fix` and `uv run ruff format .`
  - types: `uv run mypy src`
  - build data: `uv run python scripts/build_data.py`
  - app: `uv run streamlit run app/streamlit_app.py`
  - all gates at once: `uv run python scripts/check.py`
- Network: needed for `uv sync`, the data build (census.gov, geonames.org) and
  live tests. Unit tests must never touch the network.

## How to work
1. **Follow the phases in SPEC §10 in order.** Track progress in your task
   list. Don't start a phase until the previous phase's exit criteria pass.
2. **Test-first for library code** (`src/zipcator`): write the failing tests
   from SPEC §8.2, watch them fail for the right reason, then implement.
3. **Run the gates after every meaningful change**, not just at the end:
   `uv run python scripts/check.py` (ruff, format check, mypy, pytest + coverage).
4. **Never weaken a test to make it pass.** If a test is wrong, say why in the
   commit message or QA report, then fix the test. Don't add `skip`/`xfail`
   without a logged reason.
5. **Every bug gets a regression test** and an entry in `docs/QA_REPORT.md`
   (symptom → root cause → fix → test name).
6. **Golden/integration expectations must be independently verified** (SPEC
   §8.3) — never snapshot the code's first output and call it correct.
7. **Look at the app.** For UI work, run the visual QA tests and *open the
   screenshots* in `qa/screenshots/` before calling a UI phase done.
8. **End of each phase:** run all gates, then run the independent review
   (`/qa-review` command, which uses the `qa-reviewer` subagent). Triage every
   finding: fix it (with a test) or record why it's deferred. Then commit.
9. **Git:** run `git init` in phase 0 if needed. Commit at least once per phase
   with clear messages. Never commit `data/` contents (except
   `data/README.md`), `qa/screenshots/`, `.env`, or files > 1 MB.
   **Do not push or create the GitHub repo** — the user will do that.
10. Keep `app/streamlit_app.py` thin; all logic goes in `src/zipcator`.

## Conventions
- ZIPs are always 5-char strings (`"02108"`), never ints — in pandas dtypes,
  CSV output, and tests.
- Library code uses `logging`, not `print`. Type-hint all public functions.
- Distances/areas: geodesic circle via `pyproj.Geod`; area math in a local LAEA
  projection centered on the search point (SPEC §5.3). Don't use EPSG:4326
  degrees for area or distance, and don't use folium's `Circle` for the radius.
- Mock HTTP with `responses`; inject clocks/sleepers instead of real sleeping.

## Done means
SPEC §9 acceptance criteria all met, the final `/qa-review` has no unresolved
high/medium findings, and `docs/QA_REPORT.md` summarizes test counts, coverage,
bugs found/fixed and golden-scenario verification.
