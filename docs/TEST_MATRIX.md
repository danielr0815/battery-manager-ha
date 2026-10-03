# Risikomatrix und Testnachweise

Stand: Zielversion 0.49.0. Die Matrix ergänzt einzelne Featuretests um kritische
Wechselwirkungen. Coverage zeigt ungetestete Pfade; sie beweist keine fachliche
Richtigkeit. Bestehende Zeilengates bleiben erhalten. CI liefert zusätzlich
Branch-Coverage als Bericht ohne neues Prozentziel.

| Kombination / Risiko | Beobachteter Vertrag | Ausführbarer Nachweis |
|---|---|---|
| Service-ACK + verspätetes Feedback | Kein bestätigter Ladezustand/Dwell vor Rückmeldung | `ha/test_load_confirmation.py` |
| Wartendes ON + Pause | Kein neuer Start; verspätetes ON wird korrigiert | `ha/test_load_confirmation.py` |
| Pause + fehlende Planung + Mindestlaufzeit + Resume | Abschaltung unabhängig vom Planner, wartender Stopp abbrechbar | `ha/test_load_confirmation.py` |
| OFF-Fehler + unbekannter Zustand + Wiedererreichbarkeit | Frühestens 60 s, nur bekannte Gegenstellung | `ha/test_load_confirmation.py`, `ha/test_stale_failsafe.py` |
| Kaskadenbesitz + generischer Aktorauftrag | Ein Besitzer, keine gegensätzlichen Befehle | `ha/test_cascade_manager.py` |
| Dwell + Latch + Planverlängerung | Mindestlaufzeit/-pause beginnen am bestätigten Zustandswechsel | `ha/test_load_switching.py` |
| Chargergrenze + DC + Eigenbedarf + Teilstunde | Gemeinsames physisches Budget, Unserved statt erfundener Versorgung | `core/test_simulate.py` |
| Appliance + kurzer Horizont | Vollständige Laufdauer erforderlich | `core/test_optimize.py` |
| Hypothese + Reserve + Kaskade | Unveränderte Randbedingungen erhalten | `core/test_series.py` |
| Root/Aux + Teilintervall + Quellengrenze | Keine Doppelzählung, physische Messgrenzen | `core/test_accounting.py`, `ha/test_operation_history.py` |
| Quellenwechsel + Recorderfehler | Vorheriger gültiger Lernstand bleibt erhalten | `ha/test_history_robustness.py`, `ha/test_history_recorder.py` |
| Beschädigter Store + Setup | Rekonstruierbare Lerndaten verwerfen | `ha/test_history_robustness.py`, `ha/test_store_migration.py` |
| Historie + W/kW + unbekannt | Gleiche Einheiten, keine falsche OFF-Abdeckung | `ha/test_history_robustness.py` |
| Zeitzonenwechsel + Mitternacht + Replay | Schema-1-Migration, getrennte Tage/Zeitzonen | `ha/test_operation_archive.py` |
| Geteiltes Archivbudget + mehrere Segmente | Grenzen gelten global | `ha/test_operation_archive.py` |
| Entfernte Sensoroption + Reload | Explizites Entfernen überschreibt alten Wert | `ha/test_config_flow.py`, `ha/test_reserve_runtime.py` |
| Services + fehlender/entladener Entry | Verständlicher Fehler, stabile Registrierung | `ha/test_export_services.py`, `ha/test_services_metadata.py` |
| Browserzone != HA-Zone + DST | Richtige Labels und Hover in beiden 02-Uhr-Stunden | `browser/cards.spec.mjs` |
| Refresh + Shadow DOM + Fokus + Scroll | Zustand bleibt über asynchrones Rendering erhalten | `browser/cards.spec.mjs` |
| Teilstunden + Energiehover + Tastatur | Exakte Wh, erreichbare Diagrammdaten | `browser/cards.spec.mjs`, `frontend/cascade-card.test.mjs` |
| Kern ohne HA + Windows | Kein HA-Import und keine Testhelfer nötig | CI `core-only` |
| Release-Tag + Versionsstand | Tag, Manifest und Projektversion konsistent; gleicher SHA für Gates | `scripts/check_release.py`, Releaseworkflow |

| Reserve + Prognose übermorgen | Gemeinsamer vollständiger Horizont; frühere Heute/Morgen-Bindung ersetzt, lokale DST-Tage | `core/test_reserve.py`, `core/test_reserve_live_regression.py` |
| Reserve + unvermeidbarer Export + AC-/DC-Floor | Späte nötige Entladung, keine unerreichbare Null-Export-Schuld | `core/test_reserve_reachability.py`, `core/test_reserve_energy.py` |
| Reserve + Ledger 0 + SOC 6 % | Schutz bleibt aktiv; Herkunft ist ausschließlich Diagnose | `ha/test_reserve_policy.py`, `ha/test_reserve_runtime.py` |
| Upgrade + alte PSU-Haltung + fehlende Bestätigung | Physischer Zustand bleibt erhalten, geordnete Rückkehr wird erneut abgeglichen | `ha/test_reserve_policy.py` |
| Reserve + wartender Aktorauftrag + gefallener SOC | Aktueller Schutz vor alter AC-Freigabe | `ha/test_reserve_policy.py` |
| Active/Shadow + HA-Zeitzone + DST + Tastatur | Reservegründe statt T*-Ziel; technische Untergrenze bleibt zugänglich | `browser/cards.spec.mjs` |
| Haushaltsgerät + fehlender Plan/Profil + Programmwechsel | Beobachtung unabhängig verfügbar, alte Startfreigabe widerrufen | `ha/test_appliance_views.py`, `ha/test_appliance_history.py`, `browser/appliances.spec.mjs` |

Golden-Dateien werden nur für beabsichtigte fachliche Änderungen erneuert und
jeder zusätzliche Netzbezug begründet. Die Refactorings für 0.46.0 verändern die
bestehenden Golden-Dateien nicht. Der korrigierte Charger-Test berücksichtigt
10 W tatsächlichen Eigenbedarf statt denselben Betrag gleichzeitig zu exportieren.

## Schaltprognose

| Kombination / Risiko | Beobachteter Vertrag | Ausführbarer Nachweis |
|---|---|---|
| Schaltspur + stündliche Aggregation + Teilstunden | Ein-/Aus-Wechsel aus kleinen Planerschritten bleiben erhalten, keine Änderung der Energiebilanzen | `core/test_switching_schedule.py`, Golden-Suites |
| SOC-Sensor + Schaltspur + Recorder | Vollständige Intervallliste mit allen drei Zuständen, Attribut von Recorder ausgenommen | `ha/test_coordinator.py::test_forecast_curve_carries_support_flags` |
| Optionale Netzteile + HA-Refresh + Maus/Tastatur + fehlende Daten | Einblendung/Fokus erhalten, subhourige Grenzen erreichbar, fehlend bleibt unbekannt | `frontend/switching.test.mjs`, `browser/cards.spec.mjs` |
| Schaltspur + DST + Replay | Zeitintervalle in verstrichener Zeit, wiederholte Stunden unterscheidbar, alte Aufzeichnungen lesbar | `core/test_switching_schedule.py`, `frontend/switching.test.mjs` |

## Lastabhängige Reservevorbereitung

| Kombination / Risiko | Beobachteter Vertrag | Ausführbarer Nachweis |
|---|---|---|
| Hohe Abendlast + geringe Nachtlast + Teilstunden | Nötige AC-Abgabe nach W priorisiert, Gleichstand spät, keine Entladung allein für Eigenbedarf | `core/test_reserve_priority.py` |
| AC-Entnahme + späterer DC-Bedarf + unsichere PV | Kein optionaler AC-Verbrauch zulasten der prognostizierten DC-Versorgung; obere PV ist kein DC-Versorgungsnachweis | `core/test_reserve_priority.py` |
| Netzteilhaltung + spätere AC-Abgabe im Liveplan | Kein Nachmittags-Netzbezug zur Finanzierung späterer AC-Entladung | Anonymisierte Fixture `reserve_load_priority.json`, `core/test_reserve_priority.py` |
| Hohe Last nach PV-Peak + niedrige Last davor | Rechtzeitiger Speicherraum bleibt möglich; keine starre Nachtsperre | `core/test_reserve_priority.py` |

## Oktoberreview 0.54.0

| Kombination / Risiko | Beobachteter Vertrag | Ausführbarer Nachweis |
| --- | --- | --- |
| Zusatzlast + gleicher Floor-SOC + Nullimport + DC-Ausfall | Zeitliches DC-Veto gegen Ausgangs- und akzeptierten Plan | `core/test_dc_service.py` |
| Recovery/Kaskade + späteres Defizit + Stress + manueller Export | Automatische Buchung zurücknehmen, externe Vorgabe erhalten | `core/test_dc_service.py` |
| Abgelehnte Fortsetzung + physisch ON + Restmindestlaufzeit | G4 zählt gehaltene Leistung und überschreibt Dwell | `ha/test_october_regressions.py` |
| Archivrestore + Ereignisloop + Unload | Worker hält Loop frei, keine späte Übernahme | `ha/test_october_regressions.py` |
| Exakte Plan-Deltas + Typen/Nullvorzeichen + Checkpoint | Unveränderte Planhashes und Ereignisse | `ha/test_archive_storage.py` |
| Chunk-/Manifestbeschädigung + Kapazität + Abbruch | Gültige andere Evidenz und Runtimepflichten erhalten | `ha/test_archive_storage.py`, `ha/test_operation_recorder.py` |
| Power-Publikation + Quellenwechsel + alter Lernstand | Kein Lernen aus eingefrorener Leistung; Gegenkanal und Zähler bleiben nutzbar | `ha/test_appliance_learning.py`, `ha/test_appliance_history.py` |
| Zwei Herbststunden + fehlender Fold + Quellenepoch | Wh durch reale Dauer teilen; fehlend bleibt unbekannt; Nenner trennen | `core/test_load_profile.py`, `ha/test_history_profile.py` |
| Unbrauchbare EPEX-Entität + relevante Preislücke | Eindeutige valide Serie; kohärente Lastpriorität | `ha/test_market_adapter.py`, `core/test_market.py` |
| Folgetage + reale gemeldete SOC + Reserve/Live/PSUs + DST | Unabhängige Bilanz, kein Reset der Endenergie, DC-Vorrang | `ha/test_reserve_closed_loop.py` |
| Alternativer Rückhalt + pessimistische PV + Endenergie | Begrenzte Offline-Varianten, keine Live-Freigabe | `core/test_reserve_comparison.py` |
| Plan/Live/Rückmeldung + fehlende Daten + Touch/Fokus/ABA | Getrennte wahrheitsgemäße Anzeigen und erhaltener Bedienzustand | `frontend/reports.test.mjs`, `browser/review-regressions.spec.mjs` |


## Planlatenz und Attributverlust 0.54.1

| Kombination / Risiko | Beobachteter Vertrag | Ausführbarer Nachweis |
| --- | --- | --- |
| Verfügbare Planung → HA entfernt Attribute bei `unavailable` → frischer Plan | Vier Karten erhalten alte Darstellung mit Warnung/Zeit; frische Daten ersetzen sie | `frontend/plan-state.test.mjs`, `browser/review-regressions.spec.mjs` |
| Browserstart ohne Plan + Entitätswechsel + leere verfügbare Publikation | Keine erfundene oder entitätsfremde Historie | Dieselben Suites |
| Verschiedene Lastkandidaten + identische Preise + neue Preise + DST-Fold + Eviction | Unveränderte Ergebnisse, begrenzte Wiederverwendung nur im selben Planner-Aufruf | `core/test_reserve_cache.py`, Markt-/Golden-Suites |
| AC-Off-Referenz + Rückhalt-Retry + geänderte Physik-/Planeingaben + Abbruch + Cache-Verdrängung | Identische Hülle wiederverwenden, jede Betriebsgröße im Physik-Schlüssel, vollständige Gleichheit mit ungecachten Ergebnissen, kein Ergebnis nach Abbruch | `core/test_reserve_cache.py` |
| Gleiche angrenzende Schaltzustände + Zustandswechsel + Zeitlücken + DST | Nur fertige Intervalle konstruieren; Zustände und Zeitgrenzen bleiben exakt erhalten | `core/test_switching_schedule.py` |
| Neue Messpublikation während langer Berechnung | Diagnosealter ab Ergebnisveröffentlichung; Aufnahmezeit bleibt erhalten | `ha/test_planning.py::test_publications_during_calculation_use_current_diagnostic_age` |
