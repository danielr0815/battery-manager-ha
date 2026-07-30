Dev tooling 2026 — no behaviour change, operators can update or skip at will.

**This release changes nothing the integration does.** There is no planner,
executor, entity or card change — the battery keeps being planned exactly as
on v0.16.2. What ships is the development infrastructure round plus the move
of the dev environment to current versions, so if you only run the
integration you may update and notice nothing (or skip without missing
anything).

**Devcontainer, actually exercised.** The repository now has a one-command
dev setup: open it in the devcontainer (or run `uv sync --group dev`) and
the exact locked toolchain appears. The devcontainer is no longer a file
that can silently rot — it was built and test-driven locally with the
devcontainer CLI, and a dedicated CI job now builds it on every push and
pull request and runs the full test suite, ruff and mypy inside the
container, so a broken devcontainer fails the build the same day.

**Reproducible toolchain via uv + lockfile.** Dev dependencies are minimum
versions in `pyproject.toml`; the exact set lives in the committed
`uv.lock`, which local setups and CI share as the single source of truth
(updates flow through Dependabot or a reviewed `uv lock --upgrade`). The
one deliberate exception is pytest-homeassistant-custom-component, kept
exact-pinned because it pins Home Assistant itself and therefore leads the
HA coupling. Around it: pre-commit hooks running the locked ruff, a mypy
baseline over the pure planner core, coverage measurement with the report
kept as a build artifact, and CI actions pinned to real tags/SHAs instead
of floating `@main`/`@master` refs that have bitten this pipeline before.

**Python 3.14 / Home Assistant 2026.7.4.** The dev environment follows HA
upstream, which requires Python >= 3.14.2 since HA 2026.3. The migration
surfaced one real test bug: under HA 2026.7's eager task scheduling the
config-flow suite left the coordinator's refresh-interval timer armed, and
the HA test harness flagged it as a lingering timer — flaky and
order-dependent. The suite now unloads its entries via an autouse fixture,
the same pattern the coordinator tests already used. ruff's formatter also
adopted Python 3.14's unparenthesized except-tuples (syntax only).

**HACS check while the repository is private.** The HACS validation action
downloads files from raw.githubusercontent.com without authentication,
which cannot work against a private repository — the job now detects the
visibility and skips instead of going red, and it revives on its own once
the repository is public again.

433 tests green.
