## Description

<!-- What does this change do, and why? Link any related issue. -->

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change
- [ ] Documentation
- [ ] Refactor (no functional change)
- [ ] Test / CI

## How was it tested?

- [ ] Core suite: `python -m pytest tests/core -p no:homeassistant`
- [ ] Full suite (Linux/WSL): `python -m pytest tests`
- [ ] Lint: `ruff check custom_components tests` and `ruff format --check .`
- [ ] Golden snapshots regenerated **and the diff reviewed** (only if planner
      behaviour changed): `python scripts/gen_golden.py`
- [ ] Manual test in Home Assistant (if it touches the HA layer)

## Checklist

- [ ] `manifest.json` version bumped if this is a user-visible change
      (HACS tracks `main` by commit SHA)
- [ ] CHANGELOG.md updated
- [ ] Comments explain the *why* for any non-obvious logic
- [ ] Docs updated if behaviour or config changed

Test setup — HA version: `…`  ·  Python version: `…`
