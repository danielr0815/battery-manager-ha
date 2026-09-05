# Review-Korrekturen: Kaskaden, Zeitintervalle und Messwerte

Befund: Im Code-Review im September 2026 wurden 14 Fehler reproduziert. Sie
betrafen insbesondere endlose Leistungsnachweise, bei Konfigurationsänderungen
vergessene Aktoren, physisch unzulässige Durchleitung, erfundenen SOC und
abweichende Zeitintervalle zwischen Backend und Karten. Umgesetzt in v0.36.2.

## Regeln und Regressionstests

| Regel | Korrigiertes Verhalten | Nachweis |
| --- | --- | --- |
| R1 | Leistungsnachweis verwendet Veröffentlichungszeiten, zwei Messungen mit mindestens 60 s Abstand und eine feste Frist ab Quellenaktivierung. Fehlende Telemetrie verlängert die Frist nicht. Ein Timer fordert neue Prüfungen an; bei Fehlschlag wird abgeschaltet. | `test_equal_power_publications_prove_output_and_schedule_refresh`, `test_power_proof_timeout_stops_terminal_without_fresh_feedback` |
| R2 | Zwei Kaskaden dürfen dieselbe physische Endlast nicht über verschiedene Last-IDs beanspruchen. | `test_separate_cascades_cannot_alias_one_terminal_switch` |
| R3 | Leistungswerte werden zentral in W umgerechnet; unbekannte Einheiten und nicht endliche Werte gelten als fehlend. Ohne Einheit gilt aus Kompatibilitätsgründen W. | `test_power_units_and_invalid_numbers_at_input_boundary` |
| R4 | Beim Start werden alte öffentliche Exporte gelöscht und jüngere Dateien mit ihrer verbleibenden TTL eingeplant, einschließlich Unterverzeichnissen. | `test_startup_sweep_removes_stale_downloads`, `test_restart_rearms_nested_exports_for_remaining_ttl` |
| R5 | Im Speicherformular geleerte optionale Felder werden entfernt. Alte Werte bleiben nur erhalten, wenn das gesamte Speicherformular beim Umschalten der Lastart übersprungen wird. | `test_load_reconfigure_checks_cascade_before_writing_and_clears_optional_fields` |
| R6 | Ein gemeinsames Raster läuft in realen Stunden bis zur lokalen Mitternacht. Lokale Zeitstempel behalten feste UTC-Offsets; beide Herbststunden sind separate Schlüssel. PV und gelernte Profile verwenden genau dieses Raster. | `test_dst_slots_conserve_real_elapsed_energy`, `test_hourly_pv_and_learning_share_dst_grid` |
| R7 | Eine unvollständig konfigurierte Einspeisefunktion gibt einen von BM übernommenen Sollwert durch einmaliges Nullsetzen frei. Manuell übernommene Werte bleiben unter manueller Kontrolle. | `test_removing_battery_sensor_releases_owned_export_setpoint`, bestehende manuelle Einspeisetests |
| R8 | Die letzte vollständige Aktor-Zuordnung wird vor der Ausführung gespeichert. Änderungen oder gelöschte Referenzen führen zur Abschaltung der bisherigen Endlast, Ausgänge, Gates und Eingänge. Bei Fehlschlag bleiben Zuordnung und Eigentümerschaft für einen erneuten Versuch erhalten. Nach erfolgreicher Abschaltung einer ungültigen Kaskade ist deren Automatik deaktiviert. | `test_removed_topology_stops_persisted_actors_after_reload`, `test_failed_topology_cleanup_retains_old_actor_ownership_for_retry`, `test_missing_legacy_terminal_still_stops_surviving_output` |
| R9 | Eine Änderung an einem Kaskadenmitglied oder einer Endlast wird vor jeglichen Schreibzugriffen gegen die betroffene vollständige Kaskade geprüft. Fehlermeldungen sind Deutsch und Englisch verfügbar. | `test_load_reconfigure_checks_cascade_before_writing_and_clears_optional_fields` |
| R10 | Jede Allokationsphase einschließlich Recovery prüft die gleichzeitige Leistung aller Verbraucher hinter einer begrenzten AC-Durchleitung. Eine kürzere Laufzeit drosselt kein Gerät. | `test_root_passthrough_caps_bound_simultaneous_power` |
| R11 | Verbrauchsdiagramme nutzen explizite Slot-Dauern und schneiden das letzte Intervall am gewählten Horizont ab. Ein Punkt genau am Ende erzeugt keine zusätzliche Stunde. Tageswerte richten sich nach der HA-Zeitzone. | Frontendtests zu Teilstunden und Herbstumstellung, `test_real_consumption_payload_obeys_card_horizon` |
| R12 | Speicherenergie ändert sich ausschließlich durch Ladung und Entnahme. Dispatch-Ziele verändern keinen bestehenden Energieinhalt. Fehlender SOC ist ausdrücklich unbekannt; interne Planungsannahmen werden nicht als Messwert oder Kurve ausgegeben. | `test_idle_soc_is_conserved_outside_dispatch_targets`, `test_missing_soc_remains_unknown_in_core_flow`, `test_real_unknown_soc_payload_cannot_draw_fabricated_charge` |
| R13 | NaN/Unendlich können weder Zählerdifferenzen noch persistierte Energiesummen vergiften. Nach ungültigen Messungen wird eine neue Ausgangsmessung eingelernt; bereits beschädigte gespeicherte Werte werden verworfen. | `test_nan_counter_does_not_poison_realized_ledger`, `test_power_units_and_invalid_numbers_at_input_boundary` |
| R14 | Öffentliche Downloadlinks verwenden den vollständigen relativen Pfad mit URL-Kodierung. | `test_nested_download_link_preserves_and_quotes_relative_path` |

## Prüfung und Grenzen

Die Tests arbeiten mit virtuellen Zeitpunkten und gemockten Timern; produktive
Sekunden-/Minutenfristen werden nicht real abgewartet. Zwei HA-Tests übergeben
wirkliche Backend-Ausgaben an die JavaScript-Karten. Deren Millisekundenauflösung
wird bei der Energiesumme mit weniger als 0,001 Wh Toleranz berücksichtigt.
Die eigenständige Frontend-Suite prüft zusätzlich 6-Stunden- und Viertelstunden-
Fenster sowie Tagesgrenzen in einer vom Testprozess abweichenden HA-Zeitzone.

Der reine Planner unterstützt weiterhin naive Datetimes für bestehende reine
Python-Aufrufer. Für eine Zeitumstellungsplanung muss der Startzeitpunkt eine
Zeitzone mit Umstellungsregeln besitzen; der HA-Coordinator liefert diese.
Die erzeugten Slots tragen feste lokale Offsets für eindeutige Vergleiche und
Energieintervalle. Es wurden keine Golden Snapshots neu erzeugt: das Verhalten
der bisherigen Golden-Szenarien bleibt unverändert.

Ohne gespeicherte Aktor-Zuordnung kann eine alte Installation nur noch
auflösbare Referenzen und bereits gespeicherte Aktor-Ansprüche abschalten.
Ein vollständig gelöschter und nie gespeicherter Aktor ist nicht rekonstruierbar.
Reale Hardware-Schaltungen und eine Prüfung im laufenden Home Assistant sind
nicht Bestandteil dieser lokalen Regressionstests.
