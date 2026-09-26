# Umsetzung und Nachweis der 32 Review-Findings

Zielversion: **0.46.0**, Mindestversion **Home Assistant 2026.8.0**.
Die Nummern entsprechen dem beauftragten Review. „Nachweis“ benennt ausführbare
Verhaltenstests bzw. die überprüfbare Modul-/CI-Struktur. Weitere Verbesserungen
sind möglich; diese Liste behauptet keine Fehlerfreiheit der gesamten Software.

| Nr. | Umsetzung | Nachweis |
|---|---|---|
| 1 | Sicherheitslatch und offene OFF-Aufträge getrennt persistiert; Rückmeldung, Wiedererreichbarkeit und 60-s-Wiederholungsgrenze berücksichtigt | `ha/test_stale_failsafe.py`; `test_failed_off_retries_only_known_state_after_sixty_seconds`, `test_reload_keeps_pending_off_and_retry_timestamp` |
| 2 | `LoadAction` und `ActorRequest`; 30-s-Gerätebestätigung statt Service-ACK, Dwell erst an bestätigter Flanke; verspätetes Feedback erhält Besitz | `ha/test_load_confirmation.py`: ACK, Frist, Fehlerpersistenz, verspätetes ON und unabhängige Abschaltungen |
| 3 | Freigabe und Schutzstatus erneut vor ON sowie nach wartender Bestätigung geprüft; veraltete Starts verworfen bzw. zurückgenommen | `test_queued_on_is_dropped_after_pause`, `test_gate_confirmation_followed_by_pause_never_starts_input`, `test_late_on_feedback_after_pause_is_immediately_stopped` |
| 4 | Eigene Pausenaufgaben vor Eingangsvalidierung; verbleibende Mindestlaufzeit, Resume-Abbruch, Kaskaden-Delegation | `test_pause_stops_without_a_planner_refresh`, `test_pause_honours_remaining_runtime_and_resume_cancels_stop`; bestehende Dwell-/Kaskadentests |
| 5 | Chargerbudget für DC, Eigenbedarf und Ladung gemeinsam; nicht lieferbare Energie bleibt unversorgt | `core/test_simulate.py`: Chargergrenze mit Stunde und Teilstunde; Support-/Reserve-Suiten |
| 6 | Appliance-Start benötigt gesamte Laufdauer im Horizont | `test_appliance_start_requires_its_entire_runtime_in_the_horizon`; vorhandene positive Startfenstertests |
| 7 | Hypothetische Eingaben mit `dataclasses.replace` statt unvollständigem Neuaufbau | `core/test_series.py`: Reserve-/Kaskadenbedingungen bleiben erhalten |
| 8 | Recorder und Offline-Evaluator nutzen dieselbe physische Root-/Aux-Intervallbilanz | `core/accounting.py`; `core/test_accounting.py`, `core/test_evaluate_plan.py`, `ha/test_operation_history.py` |
| 9 | Lernlauf auf Arbeitskopie; gültiger Zustand erst nach Erfolg ersetzt | `ha/test_history_recorder.py`: Fehler in zweiter Epoche; `ha/test_history_robustness.py`: Quellenwechsel mit Ausfall, expliziter Abbruch |
| 10 | Rekonstruierbare Lerndaten vor Übernahme strukturell und fachlich validiert; beschädigte Daten protokolliert/verworfen | Store-Roundtrip und beschädigte Unterstrukturen in `ha/test_history_robustness.py`; Store-Migrationstests |
| 11 | Historische Leistung inklusive Einheit über dieselbe Normalisierung wie Livewerte | `test_history_power_units_and_unknown_intervals`; Appliance-Learning-Suite |
| 12 | Historische Zustände dreistufig; unbekannte notwendige Bereinigungsintervalle ausgeschlossen | `ha/test_history_robustness.py`, `ha/test_history_recorder.py` |
| 13 | Schema-2-Archivhülle mit Segmentidentität und unveränderlicher Zeitzone; Schema 1 lesbar, globale Grenzen, segmentbezogener Replay/Vergleich | `ha/test_operation_archive.py`: Mitternacht, Migration, Replay, Ereignis-/Berichtsbudget, CLI-Vergleich |
| 14 | Gelöschter Reserve-Netzsensor als expliziter `None`-Override gespeichert | `test_cleared_reserve_grid_sensor_stays_cleared_after_reload` |
| 15 | Sichtbare und zugängliche Kartenzeiten in HA-Zeitzone, expliziter UTC-Fallback ohne HA-Zone | `browser/cards.spec.mjs`: Browser UTC/HA Berlin, doppelte 02-Uhr-Stunde; schnelle Zeittests |
| 16 | HA-Untergrenze in HACS, README und Entwicklungsmetadaten 2026.8.0 | Setup-, Subentry-Geräte- und Migrationstests laufen mit gelocktem HA 2026.8.0 |
| 17 | Einheitliche reine Kernimports; separate `core-test`-Gruppe und Linux-/Windows-CI | Kern lokal ohne HA/HA-Testhelfer und ohne System-IANA-Daten ausgeführt; `tzdata` nur als Testabhängigkeit, `core-only`-Job |
| 18 | Release prüft tatsächlichen Tag-/Zielcommit; alle Gates erhalten denselben aufgelösten SHA; keine Versionsschreibzugriffe auf main | `release.yml`, wiederverwendbares `validate.yml`, `core/test_release_metadata.py` |
| 19 | Lastausführung, koordinierte Versorgung, Persistenzabbildung und Tagesausgabe in fachliche Module delegiert; öffentlicher `CascadeExecutionSnapshot` | `load_actuation.py`, `coordinated_supply.py`, `runtime_persistence.py`, `plan_output.py`; bestehende Aktor-/Versorgungs-/Persistenz-/Ausgabetests |
| 20 | Allokation aus Optimizer getrennt; typisierter Kandidatenkontext/-ergebnis, explizite Last-ID im gemeinsamen Gate, benannte direkte/Storage-/Dauerlastpässe | `allocation.py`, `allocation_candidates.py`, `planning_rules.py`; komplette Kern- und unveränderte Golden-Suite |
| 21 | Konkrete Stunden-/Quantilprofile, Lernzyklen und Programmproben mit Einheiten; typisierte Entry-Daten und vollständiger mypy-Scope | `ActiveCycle`, `ProgramSample`, `runtime.py`; `uv run mypy` über gesamte Integration, keine neuen pauschalen Unterdrückungen |
| 22 | Prognosebänder und Unsicherheit in untergeordnetem reinem Modul; Reserve importiert keinen Optimizer mehr | `core/uncertainty.py`; Reserve-/Band-/Golden-Tests und reine Kernimports |
| 23 | Cachealter, Quellenprüfung, Bestätigungsfrist und Appliance-Lernlimits mit benannten Konstanten vereinheitlicht | `core/policy.py`, `const.py`, `appliance_learning.py`; bestehende Grenzwerttests und 30-/60-s-Vertragstests |
| 24 | Ergebnis-Maps defensiv kopiert und schreibgeschützt; Replay verarbeitet `Mapping` bei identischem JSON-Format | `test_published_result_mappings_are_defensive_and_read_only`, `core/test_replay.py`, Golden-Digests |
| 25 | Gemeinsame Endlichkeitsprüfung, physikalische Bereiche, positive Slotdauer, eindeutige IDs und geordnete Zeitreihen | `core/test_model.py`, `core/test_optimize.py`, `core/test_compare_plan_grids.py`; dokumentierte Altparameter bleiben toleriert |
| 26 | Services in `async_setup`, typisiertes `runtime_data`; Aufrufe nach Entladen liefern Servicefehler | `test_service_remains_registered_after_entry_unload_and_reports_missing_entry`, Export-/Metadaten-/Entity-Suiten |
| 27 | Vier Karten plus gemeinsame Zeit-/Mathe-, Übersetzungs-, Bericht-, DOM- und Stylemodule; reproduzierbares eingechecktes Bundle | `frontend/`, `package-lock.json`; ESLint, Prettier, `check:bundle`, schnelle JS-Suite |
| 28 | Quelltextfragment-Test entfernt; Energiehover tatsächlich ausgeführt | Browserfall `feed-in hover reports the selected partial slot energy at exact boundaries` und bestehende Wh-Berechnungstests |
| 29 | Lokale Playwright-Suite mit echtem DOM/Shadow DOM und asynchronem Frame | Sieben Browsertests: vier Karten, Tastatur, Fokus, Scrollposition, Energiehover, HA-Zone und DST |
| 30 | Risikoreiche Kombinationen explizit dokumentiert; zusätzlicher Branchbericht bei unveränderten Zeilengates | `TEST_MATRIX.md`, Branchbericht im CI-Testjob, 100-%-Core-/95-%-Modulgates |
| 31 | Recorder-/Aktor-Timeouts mit kontrollierter Zeit, produktive Fristen separat behauptet | `ha/test_history_robustness.py`: 300 s; `ha/test_load_confirmation.py`: 30 s, 60-s-Grenze und Mindestlaufzeit |
| 32 | Aktuelle Verträge, Architektur, Benutzer-/Entwicklungsworkflow, Migration, Coding Guidelines und Historienkennzeichnung aktualisiert | `CURRENT_CONTRACTS.md`, `ARCHITECTURE.md`, `CODING_GUIDELINES.md`, `CONTRIBUTING.md`, README, Changelog, Wissensbasis |

## Fachliche Änderungen und Kompatibilität

Die bestehenden Golden-Dateien bleiben unverändert. Im Charger-Grenztest sinkt
Export um den tatsächlichen 10-W-Eigenbedarf: dieser Betrag konnte bisher
zugleich exportiert und vom Charger verbraucht werden. Nicht lieferbare DC-Energie
wird ausgewiesen, statt physisch unmögliche Versorgung zu behaupten.

IDs, Konfigurationen, Services, Kartenkonfigurationen und Karten-URL bleiben
bestehen. Das Planner-Replayformat bleibt unverändert; nur das Betriebsarchiv
bekommt die dokumentierte Schema-2-Hülle. Vorhandene unversionierte Dateien unter
`docs/investigations/` wurden nicht verändert.

Die zusätzlichen Pillow-/PyJWT-Mindestversionen bleiben als bestehende
Sicherheitsvorgabe erhalten. Die automatische Freigabeprüfung lehnte ihre
Entfernung ab; sie sind mit dem gelockten HA 2026.8.0 kompatibel.

## Abnahme

Lokale Abnahme am 2026-09-26 nach den letzten Aktorkorrekturen:

| Prüfung | Ergebnis |
|---|---|
| Vollständige Python-Suite mit Zeilen-Coverage | **2.056 bestanden**, 97,62 % Gesamt-Coverage |
| Modul-Gate | **43 Module bestanden**; Kern jeweils 100 %, HA jeweils mindestens 95 % |
| Separate Kern-Suite | **513 bestanden**, 100 % Kern-Coverage |
| Isolierter Kern ohne HA und ohne System-IANA-Daten | **513 bestanden**; nur gelockte `core-test`-Abhängigkeiten |
| Zusätzlicher vollständiger Branch-Lauf | **2.056 bestanden**, 4.010 von 4.282 Zweigen abgedeckt (93,65 %) |
| Schnelle ausgeführte Frontendtests | **46 bestanden** |
| Lokale Chromium-Browsertests | **7 bestanden** |
| Ruff, Format, mypy | Bestanden; mypy prüft alle **43 Integrationsmodule** |
| ESLint, Prettier, Bundlevergleich | Bestanden |
| Versions-/Mindestversionsprüfung | Manifest und Projekt 0.46.0, HA-Untergrenze 2026.8.0, leere Runtime-Requirements |
| Workflow-YAML, aktualisierte Dokumentationslinks, `git diff --check` | Bestanden |

Die Branch-Zahl ist ein zusätzlicher Lückenbericht, kein Ersatz für konkrete
Verhaltensnachweise oder die bestehenden Zeilengates. Der Coordinator bleibt
Eigentümer des gemeinsamen Laufzeitzustands; seine Fachmodule übernehmen die
oben benannten Ausführungs- und Projektionsaufgaben.

Die CI-Konfiguration für Windows, Hassfest, HACS und Devcontainer ist geprüft
hinterlegt; ein Remote-CI-Lauf oder Live-HA-Test wurde lokal nicht ausgeführt.
Die Browsertests verwenden ausschließlich den lokalen Harness.
