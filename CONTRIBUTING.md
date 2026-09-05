# Contributing to Battery Manager

Thanks for your interest! This is a Home Assistant **custom integration** (not a
PyPI package): Home Assistant loads it from `custom_components/`. New here? Read
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** first — it maps the code and
explains the shorthand used throughout the comments and design docs.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Project layout in one minute

- `custom_components/battery_manager/core/` — the **pure planner**. No Home
  Assistant imports, no side effects: frozen dataclasses in, frozen dataclasses
  out. Runs on any Python (incl. Windows).
- `custom_components/battery_manager/` (the rest) — the **Home Assistant layer**:
  coordinator, config flow, entities, consumption learning, the bundled card.
- `tests/core/` — tests for the pure core (no Home Assistant needed).
- `tests/ha/` — tests for the HA layer (need Home Assistant test helpers).

## Dev setup

Two ways — pick one:

**A. Devcontainer** (VS Code / GitHub Codespaces): reopen the repo in the
[devcontainer](.devcontainer/) — its `postCreateCommand` installs the locked
dev environment (and the recommended extensions) automatically. The
devcontainer is **verified in CI** (the `devcontainer` job builds it and runs
the full suite inside) and locally via the
[devcontainer CLI](https://github.com/devcontainers/cli)
(`devcontainer up --workspace-folder .`, then `devcontainer exec
--workspace-folder . uv run pytest tests`).

The container also includes Node.js and Chromium for the project-scoped
Playwright MCP server. Codex starts it headlessly inside the container and
stores its login profile under the ignored `.playwright-mcp/` directory. After
the first container build, restart the Codex extension once so it reloads
`.codex/config.toml`.

**B. Local with [uv](https://docs.astral.sh/uv/):**

```bash
uv sync --locked --group dev  # fails instead of silently rewriting a stale lock
```

`uv.lock` is the single source of truth for every dev-tool version — CI runs
the same `uv sync`, and Dependabot keeps the pins current. The interpreter
floor is Python 3.14.2 (`.python-version`), matching homeassistant's own
requirement since HA 2026.3.

There are no runtime dependencies to install — the integration is pure Python
and ships `requirements: []` in its manifest. The dev group is only for
running the tests and the linters. (No uv at hand? `pip install --group dev`
with pip ≥ 25.1 into a venv works too, but without the lockfile's guarantees.)

## Running the tests — note the split

The suite is split because the Home Assistant test helpers do not install on
Windows:

```bash
# Core suite — pure Python, runs ANYWHERE (Windows included).
# The -p no:homeassistant flag disables the HA pytest plugin.
uv run pytest tests/core -p no:homeassistant

# Full suite incl. the HA layer — Linux, WSL or the devcontainer.
uv run pytest tests -n 4 --dist=loadscope

# Exact CI coverage gates: core 100 %, every HA module >= 95 %.
uv run pytest tests/core -p no:homeassistant \
    --cov=custom_components/battery_manager/core --cov-fail-under=100
uv run pytest tests -n 4 --dist=loadscope --cov --cov-report=json
uv run python scripts/check_module_coverage.py
```

The full HA suite also requires Node.js 22+ to verify real backend payloads
against the bundled cards. CI and the devcontainer provide it. Run the standalone
frontend suite with `node --test tests/frontend/cascade-card.test.mjs`.
The pure core suite still needs only Python.

HA tests replace the coordinator's production five-second entity debounce with
an immediate yield in `tests/ha/conftest.py`; a dedicated mock-based test keeps
the production delay covered without adding real wall-clock sleeps. Four xdist
workers keep each module and its HA fixtures together while combining coverage.
CI additionally runs the suite serially inside the devcontainer to detect
cross-module state leakage in one HA/Python process.
Never make a test wait through a production-scale seconds/minutes delay: mock or
advance the clock and cover the configured delay separately. Polling in tests
must stop on an observed state/publication, not assume that a fixed millisecond
sleep gave a background task enough CPU time.

(Without uv, activate `.venv` and call `python -m pytest …` instead.)

On Windows, develop and test the planner core natively; run the HA-layer tests
under WSL (or let CI run them on your PR).

The test-to-requirement traceability and the rules against coverage-only tests
are documented in [docs/TEST_QUALITY_AUDIT.md](docs/TEST_QUALITY_AUDIT.md).

## Linting, formatting & type checking

CI enforces [ruff](https://docs.astral.sh/ruff/) (the project moved off
black/isort/flake8 — see the CHANGELOG). Dev versions are **minimums**
(`>=`) in `pyproject.toml`; the exact ones live in `uv.lock` — updates
arrive via Dependabot or `uv lock --upgrade`, reviewed in a PR (new ruff
releases have broken this CI before, so read the changelog on bumps). The
one exception is `pytest-homeassistant-custom-component`, which stays
fully pinned: it pins homeassistant exactly itself and thus leads the HA
coupling (the comment in `pyproject.toml` has the details). Before pushing:

```bash
uv run ruff check custom_components tests scripts
uv run ruff format --check .    # or `uv run ruff format .` to apply
```

Optional but recommended: `uvx pre-commit install` — the hooks in
`.pre-commit-config.yaml` run the locked ruff on every commit.

[mypy](https://mypy.readthedocs.io/) runs in CI as a **baseline**, not a full
gate: it reports errors only for the pure planner core
(`custom_components/battery_manager/core/`); the HA layer is analysed for
types but its errors are suppressed (`follow_imports = "silent"`, see
`[tool.mypy]` in `pyproject.toml`). Widen the scope before tightening rules.

```bash
uv run mypy
```

Match the style of the surrounding code, and comment the **why** for anything
non-obvious — the existing code documents intent (often the incident or review
that motivated a piece of logic), please keep that up.

## Golden snapshots (planner behaviour)

`tests/core/test_golden_topology.py` freezes the planner's output for a set of
scenarios (`tests/core/golden_topology.json`) so any behaviour change is caught.
If you **intentionally** change planner behaviour:

```bash
uv run python scripts/gen_golden.py     # regenerates the golden file
git diff tests/core/golden_topology.json
```

Review the diff carefully — a good change touches only the scenarios you expect,
and no scenario should import more grid energy without a documented reason.
Explain the diff in your PR.

## Versioning & releases

- Releases are immutable Git tags/GitHub releases built from `main`; HACS uses
  those versions rather than an undocumented moving branch SHA.
- For every user-visible change, bump
  `custom_components/battery_manager/manifest.json` **and** `pyproject.toml` to
  the same version, and move the entry from `[Unreleased]` to `[x.y.z]` in
  [CHANGELOG.md](CHANGELOG.md) (Keep a Changelog + SemVer). CI rejects a
  version mismatch.
- The device `sw_version` and the dashboard card version are derived from the
  manifest at runtime; they need no separate hand-maintained version.

## Submitting a PR

1. Branch from `main`.
2. Make the change with a test that proves it.
3. Run the core suite (and the full suite under Linux/WSL if you touched the HA
   layer), plus `ruff check`/`ruff format`.
4. Update the CHANGELOG and any affected docs.
5. Open the PR and fill in the template.

Small, focused PRs are easiest to review. If you're planning something large,
open an issue first to discuss the approach.
