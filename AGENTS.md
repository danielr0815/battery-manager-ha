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
uv sync --locked --group dev  # veralteter Lockfile bricht ab; Python 3.14 bei Bedarf
```

Alternativ: Devcontainer (`.devcontainer/`) — der postCreateCommand macht
dasselbe. Kein `pip install` nötig; Python-Version steht in `.python-version`.
Der Devcontainer ist **CI-verifiziert**: der `devcontainer`-Job in
`validate.yml` baut ihn bei jedem Push/PR und führt die volle Suite + ruff +
mypy darin aus (`devcontainers/ci`); lokal geht das mit der devcontainer-CLI
(`devcontainer up` / `devcontainer exec`) genauso.

## Live-Systemzugriff

- Live-Prüfungen in Home Assistant ausschließlich mit dem lokalen
  **Playwright-MCP** (`mcp__playwright__browser_*`) durchführen.
- Nie den In-App-Browser, Browser-Plugin-Runtime oder eine nicht verbundene
  Browser-Instanz als Ersatz initialisieren. Ist der lokale Playwright-MCP
  nicht verfügbar, dies klar melden und die Live-Prüfung pausieren.
- Ziel: `hass.rsn.ro-la.de:8123/`.
- Die projektlokale `.codex/config.toml` startet denselben MCP auch im
  Devcontainer; dort läuft das mitgelieferte Chromium headless. Nach einem
  Rebuild die Codex-Erweiterung neu starten, damit sie die MCP-Konfiguration
  neu einliest.

## Tests

```bash
uv run pytest tests/core -p no:homeassistant   # Kern-Suite, läuft überall
uv run pytest tests -n 4 --dist=loadscope      # volle Suite, Linux/WSL/Devcontainer
```

**Zeitregel für Tests:** Produktive Sekunden-/Minuten-Delays nie real abwarten.
Zeit per Mock/virtueller Uhr kontrollieren und den echten Delay-Wert in einem
separaten Vertragstest prüfen. Kurze Poll-Schleifen sind nur für tatsächliche
Async-Publikationen zulässig und müssen auf beobachtete Zustände statt auf ein
angenommenes Scheduler-Timing synchronisieren.

**Coverage-Gates** (CI bricht bei Unterschreiten; laufen im `tests`-Job und
im Devcontainer von `validate.yml`):

```bash
# Kern 100 % — Konvention: der reine Planner bleibt vollständig getestet:
uv run pytest tests/core -p no:homeassistant \
    --cov=custom_components/battery_manager/core --cov-fail-under=100
# Gesamt 95 % und jedes HA-Modul >= 95 %:
uv run pytest tests -n 4 --dist=loadscope --cov --cov-report=json
uv run python scripts/check_module_coverage.py
```

Traceability- und Pseudotest-Regeln: `docs/TEST_QUALITY_AUDIT.md`.

Planner-Verhalten ist durch **Golden Snapshots** eingefroren
(`tests/core/golden_topology.json`) — dieser Workflow gilt unverändert: Bei
*beabsichtigten* Verhaltensänderungen
`uv run python scripts/gen_golden.py` und den Diff im PR erklären — kein
Szenario darf ohne dokumentierten Grund mehr Netzbezug importieren.

## Lint, Format, Typen (vor jedem Push)

```bash
uv run ruff check custom_components tests scripts
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
