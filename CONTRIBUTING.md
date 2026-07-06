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

```bash
python -m venv .venv
# activate: `source .venv/bin/activate` (Linux/macOS/WSL) or
#           `.venv\Scripts\activate`     (Windows PowerShell)
python -m pip install homeassistant pytest pytest-homeassistant-custom-component ruff
```

There are no runtime dependencies to install — the integration is pure Python
and ships `requirements: []` in its manifest. The packages above are only for
running the tests and the linter.

## Running the tests — note the split

The suite is split because the Home Assistant test helpers do not install on
Windows:

```bash
# Core suite — pure Python, runs ANYWHERE (Windows included).
# The -p no:homeassistant flag disables the HA pytest plugin.
python -m pytest tests/core -p no:homeassistant

# Full suite incl. the HA layer — needs Linux or WSL (this is what CI runs).
python -m pytest tests
```

On Windows, develop and test the planner core natively; run the HA-layer tests
under WSL (or let CI run them on your PR).

## Linting & formatting

CI enforces [ruff](https://docs.astral.sh/ruff/) (the project moved off
black/isort/flake8/mypy — see the CHANGELOG). Before pushing:

```bash
ruff check custom_components tests
ruff format --check .    # or `ruff format .` to apply
```

Match the style of the surrounding code, and comment the **why** for anything
non-obvious — the existing code documents intent (often the incident or review
that motivated a piece of logic), please keep that up.

## Golden snapshots (planner behaviour)

`tests/core/test_golden_topology.py` freezes the planner's output for a set of
scenarios (`tests/core/golden_topology.json`) so any behaviour change is caught.
If you **intentionally** change planner behaviour:

```bash
python scripts/gen_golden.py     # regenerates the golden file
git diff tests/core/golden_topology.json
```

Review the diff carefully — a good change touches only the scenarios you expect,
and no scenario should import more grid energy without a documented reason.
Explain the diff in your PR.

## Versioning & releases

- HACS installs this integration by tracking the `main` branch **by commit SHA**
  (there are no release tags in normal use).
- Bump `custom_components/battery_manager/manifest.json` `version` for any
  user-visible change, and move the entry from `[Unreleased]` to `[x.y.z]` in
  [CHANGELOG.md](CHANGELOG.md) (Keep a Changelog + SemVer).
- The device `sw_version` and the dashboard card version are derived from the
  manifest at runtime, so you only bump it in one place.

## Submitting a PR

1. Branch from `main`.
2. Make the change with a test that proves it.
3. Run the core suite (and the full suite under Linux/WSL if you touched the HA
   layer), plus `ruff check`/`ruff format`.
4. Update the CHANGELOG and any affected docs.
5. Open the PR and fill in the template.

Small, focused PRs are easiest to review. If you're planning something large,
open an issue first to discuss the approach.
