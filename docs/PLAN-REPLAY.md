# Lokale Planaufzeichnung und Messvergleich

Der HA-Diagnoseexport enthält unter `planner_recording` die effektive
Konfiguration, sämtliche Kerneingaben einschließlich PV-Bändern und
Lastverfügbarkeit sowie das vollständige Ergebnis desselben erfolgreichen
Planungslaufs. `inputs.now` ist sein Zeitstempel. Die daneben stehende
`core_config` beschreibt dagegen die aktuell eingestellten Optionen.
Nach einem fehlgeschlagenen Update bleibt die letzte erfolgreiche Aufzeichnung
erhalten; sie darf nicht als neuer Messstand interpretiert werden.

Der Export wird nur auf Anfrage heruntergeladen. Er enthält Haushalts- und
Verbrauchsdaten, aber keine HA-Zugangsdaten. Es gibt keinen automatischen Upload.

```bash
python scripts/replay_plan.py /tmp/diagnostics.json
```

Kein Home Assistant ist dafür erforderlich. Das Skript akzeptiert sowohl den
vollständigen HA-Download als auch dessen `planner_recording`-Objekt. Es meldet
`exact_match` für sämtliche Ergebnisfelder und endet bei einer Abweichung mit
Exitcode 1. Änderungen am Planner können absichtlich Unterschiede erzeugen.
Die Schema-Version muss passen; unbekannte Typen werden nicht geladen.

## Beobachtete Abweichungen

```bash
python scripts/evaluate_plan.py /tmp/diagnostics.json /tmp/observations.json
```

Die Messdatei verwendet Schema 1 und eine Liste `slots`. Jeder Eintrag enthält
`start` (ISO-Zeitstempel mit demselben Zeitbezug wie der aufgezeichnete Slot) und
`duration_h`. Das Intervall muss exakt einem aufgezeichneten Slot entsprechen.
Doppelte Intervalle werden abgelehnt. Optional sind `pv_wh`, `ac_wh`,
`grid_import_wh`, `grid_export_wh`, `soc_percent`, `switch_count` und `loads`.
`loads` ordnet einer Last-ID die gemessenen `energy_wh` und `run_hours` zu.
Fehlende Werte werden nicht als Null ersetzt.

AC-Messung und Plan müssen dieselbe Bilanzgrenze verwenden: Wohnungsverbrauch
wie im Kerneingang, keine erneut addierten steuerbaren Lasten. Der Bericht
weist Messabdeckung je Größe aus. Tageswerte mit unterschiedlicher Abdeckung
sind nicht direkt vergleichbar. SOC-Minimum/-Maximum betreffen nur vorliegende
Messpunkte; ein unbeobachteter Einbruch wird dadurch nicht ausgeschlossen.

Für Lasten zerlegt der Bericht den Energiefehler exakt:

- Laufzeitanteil = Planleistung × (Ist-Laufzeit − Planlaufzeit).
- Leistungsanteil = Ist-Energie − Planleistung × Ist-Laufzeit.
- Summe = Ist-Energie − Planenergie.

Die Planleistung stammt aus der tatsächlichen Buchung inklusive Leistungscaps;
ohne Buchung wird der gekennzeichnete Zustands-/Konfigurationswert verwendet.
Ein eingeschalteter Stecker allein beweist keine gemessene Nutzenergie.
PV- und AC-Abweichungen sind Beobachtungen, keine automatische Ursachenanalyse.
Insbesondere wird kein PV-Clamp gesetzt und kein Auftrag an andere Projekte
versandt. Dafür sind wiederholte, sauber zugeordnete Messungen nötig.

## Automatische Betriebsaufzeichnung ab 0.40.0

Die Integration sammelt konfigurierte Leistungs-, SOC- und Aktorrückmeldungen
lokal. Unter den optionalen Messquellen in den Integrationseinstellungen können
vier **Leistungssensoren in W oder kW** ergänzt werden: PV, Wohnungsverbrauch,
Netzbezug und Netzeinspeisung. Bezugs- und Einspeisesensor müssen getrennte,
nichtnegative Leistungen liefern. Der Wohnungsverbrauch muss dieselbe AC-Grenze
wie die Prognose abbilden und BM-Zusatzlasten ausschließen. Energiezähler in kWh
und ein vorzeichenbehafteter Netto-Netzsensor sind hier keine Ersatzquelle.

Die Prognose- und Kaskadenkarte zeigen unter **Tagesvergleich · Plan und Betrieb**
die Tagesberichte. Lastsensoren werden übernommen. Ein Kaskaden-Eingang misst
auch Durchleitung und wird deshalb separat mit dem vollständigen geplanten
Versorgungspfad verglichen. Er beweist keine im Akku gespeicherte Energie.
Bestätigte Aktorzeiten bleiben auch ohne Leistungsmessung sichtbar; ihre
Abdeckung kann größer sein als die Abdeckung der Energiefehlerzerlegung.

Der Diagnoseexport enthält `operation_history` und `operation_report`.
Ereignisse erhalten fortlaufende Nummern und den aktiven Planbezug.
`command_requested` ist eine aktive Anforderung, `command_result` die
Service-Antwort, `state_changed` eine passive Rückmeldung mit HA-Context-ID.
Ein erfolgreicher Service beweist keinen physischen Zustandswechsel.
`cascade_actor` liefert zusätzliche Phasen- und Recovery-Belege.

```bash
python scripts/replay_operation.py /tmp/diagnostics.json
python scripts/replay_operation.py /tmp/diagnostics.json --observations-only
python scripts/replay_operation.py /tmp/before.json --compare /tmp/after.json
```

Das CLI benötigt kein laufendes HA. Es rekonstruiert die Tagesauswertung und
prüft jeden erhaltenen Kernaufruf. `exact_plans`, `daily_matches` und
`complete_event_history` weisen Ergebnisgleichheit und Vollständigkeit getrennt
aus. Ein Unterschied bei vollständiger Aufzeichnung oder einem Planner-Ergebnis
führt zu Exitcode 1. Verdrängte Ereignisse bleiben ausdrücklich unvollständig.
Beide Archive werden unabhängig auf Replay-Gleichheit geprüft; ein Fehler
im zweiten Archiv wird ebenfalls gemeldet. Der Vergleich enthält außerdem
SOC-Grenzen, Laufzeiten und Servicefehler. Er zeigt nur gemeinsame Tage und
Messgrößen, jeweils mit
beiden Abdeckungen; das ist keine automatische Bewertung verschiedener Haushalte
oder Wetterlagen. Die beobachtete Messreihe simuliert keine alternative Physik.

Detailereignisse und komprimierte Pläne bleiben höchstens sieben Tage,
50.000 Ereignisse und 32 MiB erhalten. Tagesberichte bleiben 30 Kalendertage.
Leistungen werden höchstens fünf Minuten fortgeschrieben; veraltete Werte,
fehlende Quellen und Neustartlücken werden nicht zu Nullenergie umgedeutet.
Für eine empirische Toleranzkalibrierung müssen zuerst vollständige reale Tage
mit passenden Messquellen aufgezeichnet werden.

Die Regression `test_closed_operation_day_with_feedback_restart_and_telemetry`
koppelt unabhängig davon den echten Planner und Kaskaden-Executor an ein
simuliertes Speicher-/Lastgerät. Sie vergleicht einen Grundablauf und einen
Ablauf mit Störungen über jeweils 24 Stunden mit virtueller Zeit. Ihr
vereinfachtes Gerätemodell ersetzt keine Live-Abnahme. Vertrag und Grenzen:
[F-OPERATION-HISTORY.md](F-OPERATION-HISTORY.md).

## Planungsgründe in der Kaskadenkarte

Das Forecast-Attribut `load_decisions` enthält Entscheidungen für normale und
kaskadierte Lasten, zugeordnet über ihre Last-ID. Die Kaskadenkarte zeigt
verfügbare Ablehnungsgründe und Bestätigungswartezeit aufklappbar an. Die
Zeitangaben beziehen sich auf geprüfte Kandidaten; sie bedeuten nicht, dass
für denselben Slot keine kleinere Alternative angenommen werden konnte.
Auch dieses Diagnoseattribut wird nicht zusätzlich im Recorder gespeichert.
