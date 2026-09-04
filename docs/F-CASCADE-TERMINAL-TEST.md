# F-CASCADE-TERMINAL-TEST — versteckte Endlast-Diagnose

## Zweck

Die Home-Assistant-Werkzeugaktion
`battery_manager.test_cascade_terminal` prüft den vollständigen physischen
Pfad einer linearen Speicher-Kaskade, ohne eine dauerhaft sichtbare Entity
anzulegen. Sie ist eine explizit angeforderte Diagnose und keine
Planner-Freigabe.

## Aufruf

In **Entwicklerwerkzeuge → Aktionen**:

```yaml
action: battery_manager.test_cascade_terminal
data:
  device_id: "<aus der Kaskadenliste auswählen>"
```

`device_id` wird in der Oberfläche als Liste der konfigurierten
Speicher-Kaskaden mit deren Namen dargestellt. `entry_id` und `cascade_id`
bleiben nur als erweiterte, abwärtskompatible Felder für bestehende
YAML-Aufrufe verfügbar. Die Aktion
läuft synchron: Erfolg bedeutet, dass der Nachweis beziehungsweise die
sensorlose Haltezeit abgeschlossen und der Ausgangszustand wiederhergestellt
ist. Ein paralleler zweiter Aufruf derselben Kaskade bricht den laufenden Test
ab.

## Ablauf und Nachweis

1. Alle konfigurierten Aktoren müssen einen bestätigten Zustand `on` oder
   `off` melden. Dieser Vektor sowie Phase und Aux-Quelle werden vor der
   ersten Änderung persistent gespeichert.
2. Alle Charge-Gates werden ausgeschaltet.
3. Root wird eingeschaltet. Jedes Kaskadenmitglied muss nach dem Anlegen seiner
   Versorgung frische numerische Telemetrie veröffentlichen, bevor sein Output
   eingeschaltet wird. Damit gilt dieselbe elektrische Reihenfolge wie beim
   normalen Wake.
4. Ein konfigurierter Aktor der Endlast wird eingeschaltet. Fehlt er, versorgt
   bereits der letzte Fossibot-Output die automatisch startende Endlast.
5. Ist ein Leistungssensor der Endlast konfiguriert, muss er nach der
   Aktivierung frisch publizieren und mindestens die zentrale Standby-Schwelle
   `max(10 W, 10 % der Nennleistung)` erreichen. Das Timeout ist die
   konfigurierte Wake-Zeit der Endlast, standardmäßig 60 Sekunden. Ohne
   Leistungssensor bleibt der Pfad exakt 60 Sekunden aktiv.
6. Die Endlast wird zuerst getrennt. Danach werden nicht zuvor aktive Outputs
   von hinten nach vorn geöffnet, Gates und zuvor aktive Pfade wiederhergestellt
   und Root bei einem vorherigen Aux-Zustand zuletzt getrennt.

## Sicherheitsvertrag

- Eine fehlerhafte, Hands-off- oder bereits in einer Wake-/Proof-Transition
  befindliche Kaskade lehnt den Start ab.
- Der per Kaskade vorhandene Actor-Lock verhindert Eingriffe des normalen
  Executors während des Tests.
- Globale Safety-Gates und das Entladen der Integration brechen einen Test ab,
  warten auf die geschützte Wiederherstellung und führen danach ihre normale
  Abschaltung aus.
- Der Wiederherstellungsvektor wird vor jeder Aktoränderung synchron
  persistiert. Nach einem Reload wird er vor dem ersten Planner-Lauf
  wiederhergestellt.
- Scheitert die Wiederherstellung, wird keine erfolgreiche Diagnose gemeldet:
  Die Kaskade erhält eine harte Störung und durchläuft Safe-OFF.
