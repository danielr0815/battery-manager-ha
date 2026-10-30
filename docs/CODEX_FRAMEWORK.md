# Kostenoptimierte lokale Prüfungen

Die gemeinsame Policy und ausführbaren Helfer stammen aus
[danielr0815/codex-model-policy](https://github.com/danielr0815/codex-model-policy).
Nach lokaler Installation (Python 3.11+) unter dem tatsächlichen `CODEX_HOME`:

```sh
uv run --locked python ~/.codex/model-policy/run.py runner --project . --profile static
uv run --locked python ~/.codex/model-policy/run.py runner --project . --profile core
uv run --locked python ~/.codex/model-policy/run.py runner --project . --profile python-full
uv run --locked python ~/.codex/model-policy/run.py runner --project . --profile frontend
```

Bei abweichendem `CODEX_HOME` den Pfad entsprechend ersetzen. Die bestehenden
Kommandos in CONTRIBUTING und sämtliche CI-Gates bleiben verbindlich.
`uv sync --locked --group dev`, `npm ci` und bei Frontend-Änderungen `npm run build`
erfolgen vorher; Build-Änderungen erst prüfen, wenn der Quellstand stabil ist.
Die Aufrufe über `uv run --locked python` geben auch Unterprozessen den Python-Pfad
der Projektumgebung; der Playwright-Webserver kann dadurch `python` aufrufen.
Browser müssen für Playwright installiert sein. Der Runner installiert nichts.
Das Frontendprofil verlangt Unit- und lokale Browsertests; Live-HA-Prüfungen
bleiben ausschließlich beim lokalen Playwright-MCP.

`python-full` führt Core100, Gesamt95, das bestehende Modul-Gate und den separaten
Branch-Bericht aus. `static` prüft Ruff, Format und mypy. `core` ist kein Ersatz
für die vollständige HA-Abnahme. Erfolgreiche Ausgabe ist kompakt; vollständige
Logs, JUnit und Coverage liegen dauerhaft in `.codex-artifacts/checks/`.
Warnungen und unbekannte Werte bleiben gekennzeichnet. `--reuse` ist opt-in und
nur mit exakt passendem Nachweis zulässig. Geänderte Quellen/Tests/Abhängigkeiten,
Konfiguration, Umgebung oder Werkzeuge entwerten vorherige Belege.

Ein Beobachter pro Prozess, Werkzeug-Wartefunktion statt Sleep/Logtail-Schleife.
Private Eingaben unter `.codex-artifacts/` sichern, mit Herkunft/Hash dokumentieren
und nicht committen. Der lokale Backup der ursprünglichen AGENTS.md liegt unter
`.codex-artifacts/migration/`; eigenständige Projektregeln bleiben erhalten.

## Ohne globale Installation

Im Policy-Checkout `python3 install.py --portable-output /neuer/pfad/AGENTS.md`
exportiert die gemeinsamen Regeln in eine neue Datei und überschreibt nichts.
Diese Regeln als globale bzw. übergeordnete AGENTS.md der konkreten Umgebung
bereitstellen; die Projektdatei mit ihren HA-Verträgen behalten. Alternativ den
Installer mit explizitem `--codex-home` für diese Umgebung ausführen. Ein Link
allein aktiviert keine Modellregeln oder Tools.

## Pilot

Drei geeignete reale Aufgaben nach Einführung messen, Setup/Smokechecks zählen
nicht. Aktuell keine Gesamtersparnis nachgewiesen. Globaler Tracker:
`CODEX_HOME/model-policy-evaluation.md`. Messumfang, unbekannte Werte und
Vergleichbarkeit ausweisen; Qualität und Nacharbeit gehören zur Kostenbewertung.
