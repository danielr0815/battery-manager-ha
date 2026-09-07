# Battery Manager: zehn weitere Optimierungen

Stand: 2026-09-07, Arbeitsstand 0.38.0. Umsetzung vom Nutzer beauftragt. Fortschritt und noch fehlende
Abnahmen werden unten dokumentiert. Die Schalterkorrektur
ist bereits umgesetzt und zählt nicht zu diesen zehn weiteren Punkten.
Ein Prüfauftrag ist kein bestätigter Defekt. Bestehende Operator-Regeln und
`docs/project-knowledge/06` (verworfene Ansätze) bleiben verbindlich.

1. **Kaskaden-Energiebilanz durchgehend prüfen.** Höchste Priorität.
   Root-Eingang, Endlast, Speicherladung, Entladung und Ausgangsverluste gegen
   Hausbatterie-Simulation und veröffentlichte Tageswerte abgleichen. Aus der
   Live-Nachrechnung besteht ein Prüfverdacht auf unterschiedlich angesetzte
   Ausgangsverluste, noch kein vollständig belegter Fehler.
   Abnahme: Energiebilanz schließt je Slot; keine fehlende oder doppelte
   Verlustbuchung; Tests für direkte Versorgung, Laden und isolierte Entladung.

2. **Eine verbindliche Zielhierarchie dokumentieren.** Sicherheitsgrenzen,
   zusätzlicher Netzbezug, Batterie-Tagesziel mit Toleranz, Lastpriorität und
   Einspeisung in einem aktuellen Strategie-Dokument festhalten. Historische
   Aussagen in STRATEGY, F-STRICT-SURPLUS und Feature-Nachträgen eindeutig
   referenzieren beziehungsweise als ersetzt markieren.
   Abnahme: Jeder Vorrangkonflikt hat eine eindeutige Regel und einen Test;
   insbesondere steht die neue Peak-Toleranz ausdrücklich bei R5.

3. **Ausführbarkeit in der Prognose besser abbilden.** Laufzeitpause,
   Mindestpause, Stabilitätswartezeit, Kaskaden-Aufwecken und bestätigte
   Schaltzustände gegen geplante Laufzeiten abgleichen. Verhindern, dass eine
   geplante, aber noch nicht ausführbare Endlast Einspeisung rechtfertigt.
   Abnahme: Kein automatischer Export aufgrund einer momentan gesperrten
   Endlast; Wartephasen sind in der Vorschau nachvollziehbar.

4. **Teilstunden-Läufe zeitlich korrekt anschließen.** Kürzere vorgezogene
   Startabschnitte und das Schließen vorhandener Pausen gemeinsam prüfen.
   `run_hours` allein beschreibt keinen Start um 09:45: genaue Startoffsets
   müssen Kern, Executor und Karte konsistent unterstützen.
   Abnahme: Vergleich 09:00/09:30/09:45 und Anschluss an 10:00 ohne versteckte
   Lücke; Mindestlaufzeit und Mindestpause bleiben eingehalten.

5. **Restexport mit begrenzten Alternativprüfungen absichern.** Nach der
   bestehenden Prioritätsplanung lokale Alternativen testen: Lauf früher
   beginnen, Pause schließen, zulässige Recovery oder weitere Aux-Episode.
   Verbesserungen nur bei Einhaltung sämtlicher höherrangiger Regeln annehmen.
   Abnahme: Kleine vollständig durchsuchbare Testszenarien dienen als
   Vergleichsmaßstab; kein zusätzlicher Netzbezug; feste Rechenzeitgrenze.
   Dafür ist keine neue Solver-Abhängigkeit im HA-Betrieb nötig.

6. **Toleranzen systematisch kalibrieren.** Die neue feste Ein-Prozent-Grenze
   gegen reale PV-/Verbrauchsabweichungen sowie verschiedene Batteriegrößen
   auswerten. Peak-Abweichung, SOC-Reserve, Leistungstoleranz und Import-Slack
   ausdrücklich trennen; Unsicherheit darf keine beliebige Lockerung erzeugen.
   Abnahme: Ein gemeinsames Tagesbudget ohne Aufsummierung über Lasten/Replans;
   Vergleich von Nutzenergie, Netzbezug und SOC-Minimum auf aufgezeichneten Tagen.

7. **Entscheidungsgründe verfügbar machen.** Neben angenommenen Buchungen die
   erste relevante Ablehnung veröffentlichen: Peak verfehlt, Reserve, Laufpause,
   Quellenkonflikt oder fehlende Messung. Einspeisung nennt ihren Freigabegrund.
   Abnahme: „Warum nicht früher?“ ist ohne Debug-Patch aus Diagnostik und Karte
   beantwortbar; Begründungen passen zum selben Planstand wie die Kennzahlen.

8. **Reproduzierbare Planaufzeichnungen ermöglichen.** Einen versionierten
   Diagnoseexport mit effektiver Konfiguration, Eingaben, Laufzeitsperren,
   Prognosebändern und Ergebnis bereitstellen. Lokal ohne HA wieder einlesen.
   Abnahme: Export und Replay erzeugen dieselben Entscheidungen und Energiesummen;
   keine Zugangsdaten im Export; Haushaltsdaten werden nicht automatisch geteilt.

9. **Prognose und Realität gezielt vergleichen.** Energieabweichungen nach
   Tageszeit und Quelle auswerten; separat PV-Prognose, Wohnungsverbrauch,
   Lastleistung und nicht ausgeführte Pläne betrachten. Anhaltende PV-Fehler
   gehen als belegter Auftrag an balcony-solar-forecast.
   Abnahme: Tagesbericht ordnet Abweichungen einer Ursache zu, statt pauschal
   Batteriepuffer zu erhöhen oder einen bereits verworfenen PV-Clamp einzuführen.

10. **Änderungen anhand geschlossener Tagesabläufe bewerten.** Replay-Szenarien
    mit wechselnder PV, Verbrauchsspitzen, Neustart, Telemetrieausfall und
    Tank-Sättigung verbinden; Schaltvorgänge und Neuplanung über virtuelle Zeit
    laufen lassen. Nach Release zunächst lesend in HA gegenprüfen.
    Abnahme: Vorher/nachher für Netzbezug, nutzbare Lastenergie, Export,
    Batterie-Minimum und Schaltanzahl; jeder Nachteil erhält eine fachliche
    Begründung. Tests warten keine produktiven Sekunden-/Minuten-Delays ab.

Empfohlene Reihenfolge: 1–3 sichern Bilanz und Verträge; 4–6 verbessern die
Entscheidungen; 7–9 schaffen die dafür nötige Diagnosebasis und können früh
begonnen werden. Punkt 10 begleitet jede Verhaltensänderung. Keine vorsorgliche
Tankabschaltung, kein pauschaler Telemetrie-Watchdog und keine zusätzliche
Netzbezugsfreigabe sind Teil dieses Plans.


## Umsetzungsstand

| Punkt | Stand | Noch offene Abnahme |
| --- | --- | --- |
| 1 | Root-Ausgangsverluste in Kandidaten, Recovery und Hausbilanz korrigiert; direkte Versorgung/Laden/Aux getestet | Zusätzlicher Vergleich mit vollständigen Live-Tagen |
| 2 | Verbindliche Hierarchie in STRATEGY-CURRENT.md; R5-Nachtrag und historische Verweise korrigiert | Erneute Prüfung bei den folgenden Algorithmusänderungen |
| 3 | Bestätigter Schaltzustand als zusätzliche Einspeisevoraussetzung; Aus/Unbekannt sperrt | Exakte zeitliche Abbildung sämtlicher Dwell-/Wake-Wartephasen in Lastprognosen |
| 4 | Teilstunden-Ende wird korrekt veröffentlicht; 15/30/45 Minuten geprüft | Späte Startoffsets, lückenloser Anschluss und zugehörige Executor-/Kaskadentimer |
| 5 | Ein gemeinsamer Lückenschluss-Versuch je Endlast und Tag; Aux bewahrt Direktläufe und erhält sichere Fensterpräfixe statt eines Gesamtvetos | Vollständiger kleiner Vergleichsmaßstab und Vergleich alternativer Quellenreihenfolgen |
| 6 | Absolute Tagesgrenze sowie Kapazitäten 2/5/10 kWh geprüft; Speicher-Zieltoleranz und Mindestgrößen neuer Aktionen durch Regressionen abgesichert | Empirischer Vergleich aufgezeichneter Tage, keine automatische Grenzwertänderung |
| 7 | Kandidatenablehnungen und Bestätigungswartezeit auch in Kaskadenkarte; Recovery nennt Mindestgröße, Endlastvorrang, Reserve, Versorgbarkeit und Tagesexport; Begründungen bei Blockzusammenfassung korrigiert | Weitere weiche Ablehnungen und explizite Einspeise-Freigabegründe |
| 8 | Versionierter vollständiger Kernexport und exakter lokaler Replay umgesetzt | Vollständiger externer Executor-Ereignisstrom ist Bestandteil von Punkt 10 |
| 9 | Lokaler Messvergleich mit Abdeckung und getrennter Laufzeit-/Leistungszuordnung | Automatische Messsammlung und Tagesbericht in HA |
| 10 | Bestehende virtuelle Executor-Verträge und Regressionen geprüft | Verbundener geschlossener Tagesreplay und spätere lesende Release-Prüfung |

Aufzeichnung und Messformat: [PLAN-REPLAY.md](PLAN-REPLAY.md). Diese Tabelle
unterscheidet bewusst implementierte Teilstücke von noch nicht erfüllten
Abnahmen; der Zehn-Punkte-Plan ist noch nicht vollständig abgeschlossen.

## Ergänzung 2026-09-07: Mikro-Aktionen und kontinuierlicher Betrieb

Die Punkte 1, 4, 5 und 6 enthalten jetzt die Vorrangkorrektur aus
[STRATEGY-CURRENT.md](STRATEGY-CURRENT.md): vollständige Endlastplanung vor
Speichern, ein begrenzter Versuch zum Schließen vorhandener Tageslücken,
Speicher-Zieltoleranz und Mindestgrößen neuer Speicheraktionen. Verbleibende
Endlastpausen sperren zusätzliche Speicherzyklen. Genaue Startoffsets für
Root-Teilstunden und die Kalibrierung an aufgezeichneten realen Tagen sind
weiterhin eigenständige offene Abnahmen; diese Änderung ersetzt sie nicht.

### Nachweise der Vorrangkorrektur

- `test_continuous_bridge_preserves_cross_midnight_dwell_and_reserve`:
  Lückenschluss erhält grenzüberschreitende Mindestlaufzeiten und Reservevetos.
- `test_recovery_rejects_truncated_start_and_inverter_cutoff`:
  Ein kurzer Horizontrest und ein SOC am Wechselrichter-Cutoff rechtfertigen
  keine neue Speicherladung.
- `test_aux_window_trimming_must_not_create_short_source_handover`:
  Auch nach Fensteraufteilung darf kein neuer Quellenlauf unter 15 Minuten
  entstehen. Nutzbare Präfixe bleiben erhalten: im Regressionstest 48 Minuten
  aus B1 und 60 Minuten aus B2 statt Gesamtverwerfung. Zu kurze Quellenreste
  bleiben im Akku, zu kurze Gesamtepisoden werden verworfen. Die SOC-Bilanz
  wird aus den erhaltenen physischen Abschnitten berechnet.
- `test_aux_replan_must_preserve_existing_direct_terminal_service`:
  Zusätzliche Aux-Energie darf bereits geplanten direkten Endlastbetrieb
  an anderer Stelle nicht verkürzen.

Die Golden-Snapshots für Topologien, Kaskaden und Nacht-Vorlauf bleiben
unverändert. Die bewusst geänderten Kaskadenregressionen priorisieren mehr
zusammenhängende Endlastenergie gegenüber dem vorherigen Speicher-Endstand;
die Netzbezugsprüfung bleibt erhalten.

### Live-Abnahme und verbleibende Voraussetzungen

Lesend per lokalem Playwright am 2026-09-07 geprüft: HA verwendet weiterhin
0.37.1; der Diagnoseexport enthält kein `planner_recording`. Die lokale
0.38.0-Strategie ist dort noch nicht aktiv. Die realen Tagesvergleiche in
Punkt 1/6/10 benötigen deshalb zunächst ein installiertes Release und danach
vollständige zusammengehörige Plan-/Messaufzeichnungen. Eine rückwirkende
Kalibrierung aus den aktuellen Summensensoren wäre kein belastbarer Nachweis.
Die noch offenen Implementierungsteile der Punkte 3/4/5/7/9/10 sind davon
getrennt und bleiben in der Tabelle ausgewiesen.
