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

## Grenzen des jetzigen Standes

Die Aufzeichnung reproduziert einen Planner-Aufruf. Sie ersetzt noch keinen
vollständigen Executor-Tagesreplay mit allen Ereignissen, Mindestpausen,
Neustarts und Schaltbestätigungen. Die Einzelverträge der HA-Zustandsmaschinen
werden weiterhin mit virtueller Zeit geprüft. Für eine empirische Kalibrierung
der Ein-Prozent-Peak-Toleranz fehlen zunächst vollständig aufgezeichnete Tage.

## Planungsgründe in der Kaskadenkarte

Das Forecast-Attribut `load_decisions` enthält Entscheidungen für normale und
kaskadierte Lasten, zugeordnet über ihre Last-ID. Die Kaskadenkarte zeigt
verfügbare Ablehnungsgründe und Bestätigungswartezeit aufklappbar an. Die
Zeitangaben beziehen sich auf geprüfte Kandidaten; sie bedeuten nicht, dass
für denselben Slot keine kleinere Alternative angenommen werden konnte.
Auch dieses Diagnoseattribut wird nicht zusätzlich im Recorder gespeichert.
