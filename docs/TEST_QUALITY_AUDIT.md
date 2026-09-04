# Testqualitäts- und Anforderungs-Audit

**Stand: 2026-09-04.** Ziel dieses Audits ist nicht maximale Zeilenabdeckung,
sondern die Frage, ob ein Test ein von außen beobachtbares Verhalten, eine
fachliche Regel oder einen expliziten Fehlervertrag beweist.

## Methode und Ergebnis

- Alle Testfunktionen wurden per AST auf `assert`, `pytest.raises` und
  Mock-Verifikationen geprüft. Es gibt keine tautologischen `assert True`-
  Assertions.
- Der einzige Test ohne lokale Assertion ist
  `test_support_path_exclusions_end_to_end`; er verifiziert vier vollständige
  Stundenserien über `_assert_series`, das selbst konkrete Assertions enthält.
- Die zwei zuvor reinen „darf nicht werfen“-Tests wurden gestärkt:
  `test_battery_params_valid_defaults` prüft nun die erhaltenen Grenzwerte;
  der Lovelace-YAML-Test prüft die exakte global registrierte, versionierte URL.
- Die ungedeckten HA-Zeilen wurden nach Risiko gelesen. Neue Tests wurden nur
  für benannte Verträge ergänzt: Export-Confinement/TTL, Migration,
  Diagnostics-Degradation, Cross-Field-Validierung, UI-Delegation,
  SOC-Dropout sowie Cascade-Actor-/Wake-/Safe-OFF-Fehlerpfade.
- Ein echter Befund entstand dabei: Die Nullbyte-Prüfung des Exportpfads lag
  hinter `Path.resolve()` und war damit plattformabhängig. Sie läuft nun vor
  jeder Pfadauflösung und besitzt einen expliziten Sicherheitstest.

Kein vorhandener Test wurde als reiner Coverage-Pseudotest identifiziert.
Nicht jeder Test zitiert eine nummerierte F-/R-Regel: Framework-Glue wird gegen
den öffentlichen HA-Vertrag oder den Modulvertrag geprüft. Ein Test ist nur
dann ausreichend, wenn seine Assertions diesen Vertrag konkret beobachten;
ein bloßer Funktionsaufruf reicht nicht.

## Traceability nach Testmodul

| Tests | Verifizierte Quelle bzw. Vertrag |
|---|---|
| `core/test_model.py` | physikalische Dataclass-Invarianten und unveränderte akzeptierte Werte |
| `core/test_series.py`, `test_forecast_hours.py` | `F-PREDRAIN` F1, Forecast-Bucket-/Fallback-Verträge |
| `core/test_simulate.py` | Energieerhaltung, `DC_TOPOLOGY`, `F-PREDRAIN`, `F-FEEDIN` |
| `core/test_optimize.py` | nummerierte Regeln der `F-*.md`-Planner-Spezifikationen |
| `core/test_cascade.py` | `F-CASCADE-STORAGE.md` und `CASCADE-STORAGE-DESIGN.md` |
| `core/test_load_profile.py` | `CONSUMPTION_FORECAST.md`, D-C-Lernregeln |
| `core/test_power_learning.py` | `F-ROBUST-POWER.md` |
| `core/test_golden_topology.py` | bitidentischer Topologie-Regressionsvertrag |
| `core/test_golden_night_predrain.py` | bitidentischer `F-PREDRAIN`-Liveparametervertrag |
| `core/test_module_coverage_gate.py` | 100-%-Core-/95-%-HA-Gate einschließlich fehlender Messdaten |
| `ha/test_config_flow.py` | Config-/Options-Verträge und Feature-spezifische Cross-Field-Regeln |
| `ha/test_coordinator.py` | HA→Core-Mapping und Attributverträge der jeweiligen `F-*.md` |
| `ha/test_load_switching.py` | Executorregeln aus `LOAD_CONTROL.md`, `F-EXECUTOR-GUARDS` und Last-Features |
| `ha/test_cascade_manager.py` | Actor-Ownership, Reihenfolge, Proof und Fail-closed aus `F-CASCADE-STORAGE` |
| `ha/test_support_switching.py`, `test_dc48_controller.py` | `REQUIREMENTS.md` N1a und `DC_TOPOLOGY.md` §§6–7 |
| `ha/test_feedin.py` | `F-FEEDIN.md` einschließlich R6/R7/R16 und Fail-safes |
| `ha/test_predrain_block.py` | `F-PREDRAIN-BLOCK.md` R7 und Log-Dämpfung |
| `ha/test_realized_surplus.py` | `F-REALIZED-SURPLUS.md` |
| `ha/test_dynamic_buffer.py` | dynamische Reserve-/Buffer-Verträge des Planners |
| `ha/test_stale_failsafe.py` | D-A8/G2 Stale-SOC- und Shed-Verträge |
| `ha/test_history_profile.py`, `test_history_recorder.py`, `test_history_robustness.py` | `CONSUMPTION_FORECAST.md`, D-C1–D-C9 |
| `ha/test_export_services.py` | Export-Sicherheitsvertrag in `__init__.py`: Verzeichnis, Suffix, TTL, Fehler |
| `ha/test_diagnostics.py` | Diagnostics-V7-Schema, Redaction und Fehlerdegradation |
| `ha/test_frontend.py` | Kartenressource und öffentliche Sensorattributverträge |
| `ha/test_binary_sensor.py` | konfigurationsabhängige Entity-Existenz und Registry-Bereinigung |
| `ha/test_device_per_subentry.py` | HA-2026.8-Geräte-/Subentry-Invariante und Registry-Migration |
| `ha/test_debug_utils.py` | stabile menschenlesbare Exportrepräsentation |
| `ha/test_services_metadata.py` | Übereinstimmung von Service-Metadaten und registriertem Schema |
| `ha/test_store_migration.py` | persistentes Store-Envelope und Major-/Minor-Migration |

## Durchgesetzte Coverage-Regel

Coverage bleibt ein Lückenindikator, kein Ersatz für die obige Traceability.
CI erzeugt `coverage.json`; `scripts/check_module_coverage.py` verlangt danach:

- jedes Modul unter `custom_components/battery_manager/core/`: **100 %**;
- jedes übrige Python-Modul der Integration: **mindestens 95 %**;
- jedes vorhandene Modul muss im Coverage-Bericht enthalten sein.

Damit kann ein sehr großes, gut getestetes Modul ein schwaches Nachbarmodul
nicht mehr im Gesamtdurchschnitt verstecken. Das bestehende Gesamt-Gate von
95 % bleibt als zusätzlicher Schutz bestehen.

## Review-Regel für neue Tests

Ein neuer Test soll im Namen oder Docstring den Vertrag nennen und mindestens
eine relevante Ausgabe, Zustandsänderung, Reihenfolge, Persistenzwirkung oder
Fehlerklasse prüfen. Zulässige „darf nicht werfen“-Verträge müssen zusätzlich
den erreichten Zustand beobachten. Private Methoden dürfen direkt getestet
werden, wenn sie eine sicherheitsrelevante Zustandsmaschine oder einen
fail-closed Fehlervertrag bilden; reine Implementierungsdetails ohne eigenen
Vertrag werden nicht für Coverage ausgeführt.

Mutation Testing wurde in diesem Audit nicht als CI-Gate eingeführt: Die HA-
Suite ist zustands- und zeitintensiv, und ein pauschaler Mutator würde viel
Rauschen erzeugen. Für neue Plannerregeln bleiben gezielte Gegenbeispiele und
Golden-Diffs die verbindliche Mutationsprobe.
