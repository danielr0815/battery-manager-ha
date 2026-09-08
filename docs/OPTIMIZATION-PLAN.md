# Battery Manager: zehn weitere Optimierungen

Stand: 2026-09-08, Arbeitsstand 0.41.1. Umsetzung vom Nutzer beauftragt. Fortschritt und noch fehlende
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
| 3 | Bekannte Mindestpausen, verbleibende normale Mindestlaufzeiten und Stabilitätsfristen im Plan; Kaskaden-Wake/Proof/Recovery/Neustart mit tatsächlicher Prüffrist separat ausgewiesen; letzter AC-Ausgang als Bestätigung der Endlast | Live-Abgleich der projizierten Grenzen; zukünftige Hardwarebestätigung bleibt ausdrücklich bedingt |
| 4 | Späte Starts an bekannten Freigaben mit lückenlosem Stundenanschluss; 09:00/09:15/09:30/09:45 geprüft; gemeinsame Plan-/Laufende-/Segmenttimer; Aux respektiert seinen Startoffset auch beim Wiederanlauf | Freie Optimierung später Starts innerhalb einer Stunde ohne vorgegebenen Freigabezeitpunkt (Alternativenprüfung, Punkt 5); Live-Abnahme |
| 5 | Ein gemeinsamer Lückenschluss-Versuch je Endlast und Tag; Aux bewahrt Direktläufe und sichere Fensterpräfixe; begrenzter Offline-Rastervergleich mit realem Planner, Laufblöcken und Schutzkennzahlen | Vollständiger kleiner Vergleichsmaßstab und kontrollierte automatische Alternativenauswahl; Quellenfolgen nur mit gesondertem elektrischen Vertrag |
| 6 | Absolute Tagesgrenze sowie Kapazitäten 2/5/10 kWh geprüft; Speicher-Zieltoleranz und Mindestgrößen neuer Aktionen durch Regressionen abgesichert | Empirischer Vergleich aufgezeichneter Tage, keine automatische Grenzwertänderung |
| 7 | Kandidatenablehnungen, Ausführbarkeitsgrenzen und explizite Einspeise-Freigabe-/Ablehnungsgründe aus demselben Planstand in Diagnostik und beiden Karten; Recovery erklärt seine Grenzen | Weitere weiche Alternativenentscheidungen gemeinsam mit Punkt 5; Live-Abnahme |
| 8 | Versionierte Planstände, korrelierte Schaltanforderungen/Service-Antworten, passive Rückmeldungen und Kaskaden-Recovery im lokalen Archiv; Offline-Replay aller erhaltenen Planstände | Reale Ursache anhand künftig erfasster Ereignisse eingrenzen |
| 9 | Automatische Messsammlung, persistente Tagesberichte und Kartenansicht mit Abdeckung, Laufzeit-/Leistungszuordnung und getrennten Kaskaden-Messgrenzen | Vollständige reale Tage nach Installation auswerten |
| 10 | Geschlossene virtuelle 24-Stunden-Läufe mit echtem Planner/Executor, Geräte-Rückkopplung, Neustart, Wolken, Verbrauchsspitze, Telemetrieausfall, Tank-Sättigung und verzögerter Bestätigung; Archivvergleich per CLI | Lesende Prüfung des installierten Releases und reale Tagesabnahme |

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


## Nachtrag: robuste Aktorsteuerung (0.38.1)

Umgesetzt: allgemeine begrenzte Zustands-Recovery bei Kaskaden-Schaltfehlern,
einmaliger Retry für idempotente HA-Helper, Ladefreigaben vor vorgelagerten
Ausgängen AUS, abschließende Safe-OFF-Beobachtung und persistentes Diagnosejournal.
Das Startbudget berücksichtigt die zusätzlichen Recovery-Zeitfenster.
Die physische Ursache der verspäteten Fossibot-Rückmeldung bleibt offen;
die neue Version liefert dafür Ereignis- und Zustandsbelege. Ein Live-Nachweis
nach Installation steht noch aus.


## Umsetzung 2026-09-08: bekannte Wartezeiten und exakte Starts

Punkte 3/4: `SurplusLoadState.not_before` transportiert bestätigte Mindestpausen
in den Kern. Nur an diesen tatsächlichen Freigaben wird das Stundenraster
geteilt; alle Energiekanäle bleiben erhalten. Neue Buchungen vor der Freigabe
werden in direkter Planung, Vorentladung, Recovery und Aux verworfen. Die
Versorgungsstrecke der Kaskade verwendet dieselben OFF-Zeitstempel wie der
Executor. Ein späterer Aux-Start wird nicht mehr auf den Slotanfang vorgezogen.
Ein gemeinsamer Timer fordert an den relevanten Zeitgrenzen eine neue Planung
an; er schaltet niemals einen inzwischen überholten Plan blind ein.

Vertrag und Grenzen: [F-PLAN-TIMING.md](F-PLAN-TIMING.md). Das ist keine
Behauptung, dass sämtliche Wartephasen exakt vorhersehbar wären: noch fehlende
Gerätebestätigungen und zukünftige stabile Planstände bleiben offen. Die
vollständige Phasenprognose und eine freie Suche nach alternativen späten
Starts sind weiterhin ausdrücklich in der Tabelle aufgeführt.


## Umsetzung 2026-09-08: Betriebsnachweis und Tagesvergleich (0.40.0)

Der Softwareumfang des Clusters 8/9/10 ist umgesetzt. Verbindlicher Vertrag:
[F-OPERATION-HISTORY.md](F-OPERATION-HISTORY.md); Bedienung und Replay:
[PLAN-REPLAY.md](PLAN-REPLAY.md). Die Instrumentierung verändert keine
Planungsentscheidungen und kalibriert keine Toleranzen automatisch.
Die virtuellen Tage prüfen Energierückmeldung, Aktorzeiten, Messlücken,
Schaltzahlen, Speicherreserve und identische Rekonstruktion des Archivs.
Reale Tagesdaten können erst nach Installation entstehen; ihre Auswertung
bleibt die ausgewiesene empirische Abnahme von 1/6/8/9/10.


## Umsetzung: Ausführbarkeit und Entscheidungsgründe (0.41.0)

Cluster 3/7: [F-EXECUTION-PROJECTION.md](F-EXECUTION-PROJECTION.md) beschreibt
verbleibende Mindestlaufzeiten normaler Lasten, stabile Vorlaufstarts und die
Abgrenzung zu unbekannter Hardwarebestätigung. Das Stundenraster wird auch an
bekannten Laufenden und Stabilitätsfristen geteilt. Laufzeitenergie wird vor
optionalen Buchungen geprüft; ein abschaltbares Ladeziel bleibt vorrangig.

Die Karten erklären Mindestlaufzeit, Vorlaufwartezeit, Vorschlagszahl und
Kaskaden-Prüffristen. Einspeisungsgründe kommen unmittelbar aus den Prüfungen des
Kerns. Ein nachfolgender Schaltversuch verändert diese Planbegründung nicht
rückwirkend. Neutrale Kerneingaben behalten ihre Energie- und Schaltentscheidungen;
Golden-Snapshots bleiben unverändert. Reale Abnahme bleibt nach Installation
nötig. Der nächste eigenständige Implementierungsumfang ist der begrenzte
Alternativenvergleich für freie Startzeiten und Quellenfolgen (4/5).


## Abschlussprüfung 2026-09-08: sinnvoller nächster Schritt (0.41.1)

Der neue [Offline-Rastervergleich](PLAN-GRID-COMPARISON.md) macht Punkt 4/5
an vorhandenen Planständen überprüfbar. Er ist bewusst keine Behauptung eines
globalen Optimums und erfüllt noch nicht die vollständige Alternativen-Abnahme.
Die Produktivstrategie bleibt unverändert: synthetische Gegenbeispiele zeigen,
dass ein feineres Raster trotz mehr Nutzenergie zusätzliche Laufunterbrechungen
erzeugen kann. Ein pauschaler Rasterwechsel wäre daher kein sicherer Abschluss.
Ein Umordnen von `cascade.members` würde außerdem die elektrische Topologie
ändern, nicht nur eine zulässige zeitliche Quellenalternative ausprobieren.

Lesend per lokalem Playwright am 08.09.2026 gegen 17:14 Uhr Europe/Berlin geprüft:
0.41.0 ist installiert, Integration geladen, Planaufzeichnung und neue
Einspeisungsgründe vorhanden. Der aktuelle Plan meldet `runtime_paused`.
Der Tagesbericht enthält erst den angebrochenen 08.09. (rund 9,7 Stunden
Beobachtung); PV-, Wohnungsverbrauchs-, Netzbezugs- und Netzeinspeiseleistung
sind darin noch nicht zugeordnet. Last- und Kaskaden-Eingangsmessungen sind
vorhanden. Die Aufzeichnung meldet keine Servicefehler und keinen Speicherfehler;
3.882 bereits verworfene Detailereignisse zeigen, dass Detailhistorie zusätzlich
zur Zeitgrenze mengenbegrenzt ist. Tagesberichte bleiben getrennt erhalten.

Der gesicherte aktuelle Plan wurde offline exakt reproduziert. Im gleichen
55-Slot-Horizont ergeben sich mit Viertelstunden-Grenzen 220 Slots, 225,5 Wh
mehr geplante Endlastenergie und zwei statt vier Laufblöcke. Netzbezug bleibt
0 Wh, Restexport sinkt von 106,91 auf 8,67 Wh. Das ist ein positiver Einzelbefund,
keine gemessene Einsparung oder Freigabe für eine allgemeine Strategieänderung.
Vollständige Kennzahlen und Grenzen: [PLAN-GRID-COMPARISON.md](PLAN-GRID-COMPARISON.md).

**Offen bleiben:** reale Tagesabnahme und empirische Kalibrierung (1/6/8/9/10),
Live-Abgleich der Fristen (3) sowie der abgesicherte automatische
Alternativenvergleich (4/5/7). Sinnvoll ist zunächst die Zuordnung der vier
Messkanäle in den Optionen und die Auswertung mehrerer vollständiger Tage.
Die Zuordnung darf insbesondere den Wohnungsverbrauch ohne BM-Lasten und die
Richtung der Netzmessung nicht verwechseln; sie wurde bei der lesenden Prüfung
nicht verändert. Softwareseitig stehen die dafür nötigen Werkzeuge bereit.
