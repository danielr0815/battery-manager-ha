# AGENTS.md — Einstieg für Coding-Agenten

Home-Assistant-**Custom-Integration** (HACS, Domain `battery_manager`), kein
PyPI-Paket: HA lädt den Code aus `custom_components/`. Reiner Python-Code,
keine Runtime-Dependencies (`requirements: []` im Manifest).

## Projektstruktur

- `custom_components/battery_manager/core/` — reiner Planner ohne HA-Imports:
  frozen dataclasses rein, frozen dataclasses raus. Läuft überall (auch Windows).
- `custom_components/battery_manager/` (Rest) — HA-Schicht: Coordinator,
  Config Flow, Entities, Consumption-Learning, gebündelte Dashboard-Card.
- `tests/core/` — Tests für den Kern (ohne Home Assistant).
- `tests/ha/` — Tests für die HA-Schicht (brauchen die HA-Testhelfer; laufen
  nicht nativ unter Windows → Linux/WSL/CI).
- `docs/project-knowledge/` — kuratierte Wissensbasis (00 = Index/Einstieg,
  07 = Entwicklung/Tests/Release). **Vor größeren Änderungen lesen.**
- `docs/ARCHITECTURE.md` — Code-Map und Glossar der Kommentar-Kürzel.

## Setup (einmalig)

```bash
uv sync --group dev        # erzeugt .venv aus uv.lock (einzige Wahrheit,
                           # auch in CI); installiert Python 3.14 bei Bedarf
```

Alternativ: Devcontainer (`.devcontainer/`) — der postCreateCommand macht
dasselbe. Kein `pip install` nötig; Python-Version steht in `.python-version`.

## Tests

```bash
uv run pytest tests/core -p no:homeassistant   # Kern-Suite, läuft überall
uv run pytest tests                            # volle Suite (433 Tests), Linux/WSL
```

Planner-Verhalten ist durch **Golden Snapshots** eingefroren
(`tests/core/golden_topology.json`). Bei *beabsichtigten* Verhaltensänderungen:
`uv run python scripts/gen_golden.py` und den Diff im PR erklären — kein
Szenario darf ohne dokumentierten Grund mehr Netzbezug importieren.

## Lint, Format, Typen (vor jedem Push)

```bash
uv run ruff check custom_components tests
uv run ruff format --check .    # bzw. `uv run ruff format .` zum Anwenden
uv run mypy                     # Baseline: prüft nur core/, siehe [tool.mypy]
```

- Dev-Versionen sind **Mindestversionen** (`>=`) in pyproject
  `[dependency-groups]`; die exakten stehen im `uv.lock` — Updates via
  Dependabot oder `uv lock --upgrade` (im PR prüfen; neue ruff-Releases
  haben die CI schon gebrochen). Einzige Ausnahme mit Voll-Pin:
  `pytest-homeassistant-custom-component`, weil es homeassistant selbst
  exakt pinnt und damit die HA-Kopplung führt (Begründung im
  pyproject-Kommentar).
- `ruff format --check .` formatiert auch Python-Codeblöcke in Markdown
  (Details und Fallstricke: `docs/project-knowledge/07`).
- mypy ist bewusst eine Baseline (nur `core/`, HA-Schicht per
  `follow_imports = "silent"` typisiert, aber fehlerfrei gehalten wird nur
  der Kern). Scope erweitern, bevor Regeln verschärft werden.
- Optional: `pre-commit install` — die Hooks (`.pre-commit-config.yaml`)
  spiegeln die im Lockfile fixierte ruff-Version.

## Versionierung (CI-Gate, nicht brechen!)

`custom_components/battery_manager/manifest.json` `version` und
`pyproject.toml` `version` müssen **immer gleich** sein — die CI bricht sonst.
Bei nutzersichtbaren Änderungen beide bumpen + `CHANGELOG.md` (Keep a
Changelog/SemVer); `sw_version` und Card-Version leiten sich zur Laufzeit aus
dem Manifest ab.

## Konventionen

- Kommentare dokumentieren das **Warum** (oft den Vorfall/Review, der eine
  Logik motiviert hat) — bitte so weiterführen. Kürzel im
  `docs/ARCHITECTURE.md`-Glossar nachschlagen.
- Kleine, fokussierte Änderungen; jede Änderung mit Test, der sie beweist.
- Voller Workflow: `CONTRIBUTING.md`.
