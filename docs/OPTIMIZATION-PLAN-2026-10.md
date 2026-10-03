# Vertiefter Optimierungsplan, Oktober 2026

Stand: 03.10.2026. Ausgangsbasis `a211549`, Version 0.53.0;
Umsetzung im Arbeitsbaum für **0.54.0**. Der Auftrag wurde anschließend um
Implementierung erweitert. Die Schutz-, Diagnose-, Persistenz- und UI-Änderungen
sind implementiert. Produktive HA-Konfiguration und Aktoren wurden dabei nicht
verändert. Forschung bleibt an ihre unten beschriebenen Messkriterien gebunden.

## Umsetzungsstand

| Paket | Stand | Konkreter Nachweis / verbleibender Schritt |
| --- | --- | --- |
| O01 | Implementiert | Physische DC-Defizitintervalle, Ausgangs-/akzeptierter Plan; `core/test_dc_service.py` |
| O02 | Implementiert | Reale Ladepfade in G4, Mindestlaufzeit-Schutzfall; `ha/test_october_regressions.py` |
| O03 | Implementiert | Isolierter Worker-Restore, Heartbeat und spätes Unload; gleiche Regressionen |
| O04 | Implementiert | Tatsächliche Publikation, normalisierte Einheiten, Quellenwechsel und Altprovenienz; Appliance-Tests |
| O05 | Implementiert | Quellenqualität und valide EPEX-Erkennung; `ha/test_source_health.py`, Marktadapter |
| O06 | Implementiert, Budgets vorläufig | Atomare separate exakte Chunks, Migration, Kapazitäts-/Abbruch-/Corruptiontests; tatsächliche Wochenabdeckung nach Release messen |
| O07 | Implementiert | Plan-/Live-/Kommando-Kontext und getrennte Gerätepublikation; Recorder-/Live-Tests |
| O08 | Implementiert | Kartenberichte und Interaktionsregressionen; Frontend- und Chromium-Suites |
| O09 | Implementiert | Physische Dauer, Quellenepoch-Nenner, gezielte DST-Migration; Core-/HA-Lerntests |
| O10 | Implementiert | Einheitliche Lastpriorität bei relevanten Preislücken; Core-Markt- und Live-Tests |
| O11 | Implementiert | Wiederverwendung identischer Simulationen, exakte Ergebnisse; bestehende Reserve-/Golden-Suites |
| O12 | Ausgebaut | Gemeinsame unabhängige Physik für Folgetage, AC-Spitze, Preisgap und DST; weitere Störfälle bleiben zusätzlich in Einzeltests |
| O13 | Offline-Werkzeug implementiert | Höchstens 15 Varianten mit DC- und Endenergiebilanz; keine produktive Auswahl |
| O14 | Diagnosegrundlagen implementiert; Modellversuche offen | Unbekanntes Band, Quellenqualität und Laufprovenienz sichtbar; valide Phasen-/Busdaten und Vergleich vor Modelländerung nötig |

Verträge und betriebliche Grenzen: [Oktoberumsetzung](F-OCTOBER-OPTIMIZATIONS.md).
Prüfergebnisse: [Validierung mit 2.500 Python-/HA-Tests](investigations/2026-10-03-implementation-validation.md).
Die unten stehenden Versuchs- und Abnahmekriterien bleiben als Reviewgrundlage
bestehen. Ein synthetischer Anlagenlauf erfüllt keinen Nachweis einer realen
Wochenersparnis und ersetzt keine Messung der konkreten PV/DC-Topologie.

Die ersten Arbeiten sollen Versorgungsschutz, Entscheidungsnachweise und
verständliche Anzeigen verbessern. Eine großzügigere AC-Freigabe lässt sich
aus den bisherigen Daten nicht allgemein rechtfertigen. Der frühere Vorschlag
einer erweiterten Reserve-Suche wird deshalb als bedingtes Experiment geführt.

Grundlagen: lokal aufbewahrtes Wochenreview und vertiefte Gegenprüfung,
[Zielhierarchie](STRATEGY-CURRENT.md), [aktuelle Verträge](CURRENT_CONTRACTS.md).
Detaillierte Betriebsanalysen und Rohdaten werden nicht im Release veröffentlicht.
Der [Septemberplan](OPTIMIZATION-PLAN.md) dokumentiert bereits erledigte Arbeit;
dieser Plan ersetzt dessen offene Empfehlungen, soweit hier konkretisiert.

## Evidenz und unveränderte Ziele

Die sieben Tage wurden anhand Recorder-Historie, Zählern und Tagesberichten
untersucht. Vollständige Einzelentscheidungen sind nur für etwa 22 Stunden
erhalten: 2.219 Planstände und 6.938 Ereignisse. Fehlende ältere Entscheidungen
lassen sich nachträglich nicht erzeugen. Alle erhaltenen Planstände wurden
decodiert; einzelne vollständige Replays und gezielte Gegenproben ergänzen den
Code-, Spezifikations- und Testreview. Kein behaupteter Wochengewinn wird aus
einem einzelnen Forecast berechnet.

Die bestehende Hierarchie bleibt maßgeblich: physische Versorgung und
Ausführbarkeit, DC-Vorrang, kein zusätzlicher Netzbezug für Überschusslasten,
Tagesziele und Reserve, Lastpriorität, anschließend steuerbare Einspeisung.
Der 50-Wh-Import-Slack ist kein Budget für nicht versorgten DC-Bedarf.
Bestehende manuelle Eingriffe bleiben exogene Eingaben. Nominaler Pass 3,
binäre Inverterfreigabe, voller verfügbarer Prognosehorizont, Quellenverriegelung
und bestehende Mindestlaufzeiten werden nicht beiläufig umdefiniert.

## Reihenfolge und Abhängigkeiten

P1 bezeichnet belegte Schutz- oder erhebliche Betriebsprobleme; P2 verbessert
Messbarkeit, Korrektheit oder Bedienung; P3 bezeichnet Härtung oder Forschung.
S/M/L sind relative Umfänge: eine lokale Änderung / mehrere verbundene Module /
Schema- oder Schnittstellenarbeit. Sie sind keine Terminzusagen.

| Paket | Priorität / Umfang | Ergebnis | Voraussetzung |
| --- | --- | --- | --- |
| O01 | P1 / M | DC-Defizite bei Zusatzlast, Advisor und Einspeisung verhindern | Eigenständige Reproduktionen |
| O02 | P1 / M | Tatsächlich gehaltene Lasten in G4 berücksichtigen | Core-/Executor-Gegenprobe |
| O03 | P1 / S–M | Archiv-Restore aus der HA-Ereignisschleife entfernen | Unabhängig |
| O04 | P2 / M | Frische und Einheiten im Geräte-Lernen korrekt behandeln | Unabhängig |
| O05 | P2 / M | Messquellen und EPEX eindeutig prüfen und erklären | O04 für gemeinsame Messsemantik |
| O06 | P2 / L | Exakte Wochenhistorie unter definierter Last ermöglichen | O03; Schema-/Kapazitätsentscheidungen |
| O07 | P2 / M | Plan, Live-Veto, Kommando und Geräteantwort korrelieren | O06 für Langzeitaufbewahrung |
| O08 | P2 / M | Plan-/Liveanzeige, Verfügbarkeit und Bedienung korrigieren | Mit O05/O07 abgestimmte Datenfelder |
| O09 | P2 / M | Lernabdeckung und Sommerzeit physikalisch konsistent machen | Unabhängig; Store-Migration prüfen |
| O10 | P3 / S–M | Marktpräferenz bei lückenhaften Preisen präzisieren | O05, Fachvertrag vor Verhaltensänderung |
| O11 | P2 / M | Redundante Reserve-Arbeit reduzieren | Exakte Ergebnis- und Cache-Grenztests |
| O12 | P2 / L | Gemeinsamen geschlossenen Vergleichsablauf etablieren | O01/O02/O04; reale Messung zusätzlich O05–O07 |
| O13 | P3 / M | Reserve-Alternativen nur bei belastbarem Nettovorteil zulassen | O01/O11/O12 |
| O14 | P3 / M–L | Unsicherheits-, Geräte- und Busmodelle kalibrieren | O04/O05/O09/O12 |

O01–O03 können parallel bearbeitet werden. Kleine UI-Korrekturen aus O08
benötigen keine fertige Archivmigration. O11 ist eine getrennte Änderung,
damit ein Performanceproblem keine fachliche Lockerung rechtfertigt.

## O01 — DC-Versorgung als ausdrückliche Invariante

**Beleg:** Vollständige `plan()`-Gegenproben für automatische Zusatzlast,
Geräte-Advisor und automatische Einspeisung erzeugen jeweils 100 Wh zusätzlichen
nicht versorgten DC-Verbrauch, obwohl Nullimport und das gleiche SOC-Minimum
den bisherigen Vergleich bestehen. Der Simulator klemmt am physischen Floor;
ein SOC-Vergleich allein kann den Verlust deshalb nicht erkennen.

**Änderung:** In `core/allocation.py`, `core/optimize.py` und den zugehörigen
Simulations-/Modelltypen einen gemeinsamen, reinen Vergleich verwenden.
Verglichen werden zeitlich identische physische Intervalle und identische
Quellenverfügbarkeit. Die Summe positiver zusätzlicher DC-Defizite pro Abschnitt
darf insgesamt höchstens ein numerisches Epsilon betragen; es entsteht kein
neues Toleranzbudget je Slot.
Zusätzliche DC-Netzversorgung bleibt daneben eine eigene bestehende Grenze.
Ein früherer Gewinn darf einen späteren Ausfall nicht kompensieren.

Der Vergleich braucht sowohl die unveränderte Basis als auch den zuletzt
akzeptierten Plan: Dadurch werden bestehende Defizite nicht pauschal verboten,
bereits erreichte Verbesserungen aber auch nicht schrittweise wieder verbraucht.
Zunächst Stundenflüsse schützen; für den vollständigen Zeitvertrag eine
kompakte Defizitspur auf dem vorhandenen physischen Simulationsraster ausgeben.
Allein kumulative Präfixsummen reichen nicht: `(5, 5) → (4, 6)` würde passieren,
obwohl der zweite Ausfall wächst. Ein reiner Summenvergleich ist noch schwächer.

**Abnahme:**

- Alle drei vollständigen Reproduktionen lehnen die DC-schädliche zusätzliche
  Aktion ab und nennen einen spezifischen DC-Versorgungsgrund. Eine separat
  nachgewiesene sichere Teilbuchung bleibt erlaubt.
- Gleichbleibendes oder sinkendes Bestandsdefizit bleibt zulässig; neue, frühere,
  spätere und innerhalb einer Stunde verschobene Defizite werden erkannt.
- Kaskade, Recovery, Top-up, unterschiedliche Netzteilverfügbarkeit, vorhandener
  manueller Export und mehrere nacheinander akzeptierte Kandidaten sind geprüft.
- Stressvergleiche verwenden dieselbe jeweilige Referenz. Der ausdrücklich
  nominale Pass 3 erhält kein neues pauschales P10-Veto.
- Golden-Diffs zeigen jeden beabsichtigten Wegfall einer bisherigen Buchung;
  keine numerische Toleranz übernimmt die 50-Wh-Importfreigabe.

**Auslieferung:** Separate Korrektur mit genau diesen Regressionen und
aktualisierten Schutzverträgen. Keine gleichzeitig geänderte Suchstrategie.

## O02 — Laufende Last trotz abgelehnter Buchung schützen

**Beleg:** Eine reale HA-Testkonfiguration hält einen bereits laufenden
Verbraucher wegen 25 Minuten Restmindestlaufzeit eingeschaltet. Der neue Plan
verwirft die Fortsetzung wegen zusätzlichem Import und schaltet den Inverter
aus. Die aktuelle G4-Eingangsberechnung zählt nur `load_plan.active_now`; damit
wird die physisch weiterlaufende Last als 0 W behandelt. Bei 0 W PV greift G4
nicht. Die Gegenprobe verbindet Core, echten Guard und Executor; sie ist kein
beobachteter Vorfall auf der Live-Anlage.

**Änderung:** In `coordinator.py` tatsächliche, durch Runtime/Executor gehaltene
Lastzustände und geplante Starts getrennt erfassen. Schutzentscheidungen
bekommen die physisch wirksame Last mit ihrem Haltegrund. Ein abgelehnter
Planabschnitt darf die laufende Last nicht aus der Schutzbilanz entfernen.
Gemäß `F-EXECUTOR-GUARDS.md` überschreibt G4 in diesem Szenario die
Mindestlaufzeit. Keine Inverterfreigabe erzwingen, um eine zuvor verworfene
Buchung zu retten.

**Abnahme:** Produktionspfad des Coordinators mit virtueller Zeit prüfen:
Last läuft, PV fällt aus, Reserveplan lehnt Fortsetzung ab, Inverterfreigabe
endet. Die in G4 vorgesehene Schutzaktion erfolgt auch vor Ende der
Mindestlaufzeit. Normale Haltezeit bei ausreichend PV, pausierte Last,
unbekannte Leistungsquelle, Quellenwechsel und verspätete Rückmeldung bleiben
gesonderte Fälle. Journal und Karte erklären die tatsächliche Aktion.

## O03 — Schneller, nebenläufig sicherer Archivstart

**Beleg:** `OperationArchive.restore()` wird synchron aus `coordinator.py`
aufgerufen. Der isolierte Restore des erhaltenen etwa 32-MiB-Archivs benötigte
lokal 15,28 s. Das ist ein lokaler Rechenzeitbeleg, keine gemessene HA-Ausfallzeit.

**Änderung:** Entpacken, Validieren und Wiederaufbau auf einem isolierten
Archivobjekt im vorhandenen HA-Executor ausführen. Erst das fertige Ergebnis im
Eventloop übernehmen. Kleine persistente Aktorpflichten unabhängig davon
rechtzeitig laden. Kein Worker mutiert gleichzeitig das aktive Runtimeobjekt.
Unload/Reload und Entry-Wechsel verhindern die spätere Übernahme eines
veralteten Worker-Ergebnisses. Fehlerzustand des Archivs sichtbar erhalten.

**Abnahme:** Realistische große Fixture, beobachteter Eventloop-Heartbeat während
Restore, identische Archivsemantik, Corruption-Fall, Unload während Restore,
zweiter Setup-Versuch und verspäteter Workerabschluss. Synchronisation über
Ereignisse; kein Test schläft produktive Verzögerungen ab. Hardwareabhängige
Millisekundenlimits ersetzen diese Nebenläufigkeitsprüfung nicht.

## O04 — Messfrische und Leistungsnormalisierung

**Beleg:** Ein seit zwei Stunden nicht neu publizierter 600-W-Wert wird durch
Runtime-Minutenbeobachtungen als 1.200-Wh-Gerätelauf gelernt.
`_load_is_running()` behandelt 500 W und 0,5 kW unterschiedlich und akzeptiert
`inf W` als laufende Last.

**Änderung:** Bestehende Leistungsreader wiederverwenden; endliche W/kW-Werte
normalisieren. Für Leistung die Veröffentlichungszeit von der lokalen
Beobachtungszeit trennen. Identische echte HA-Publikationen aktualisieren
`last_reported`; erneutes Lesen durch BM tut dies nicht. Abgelaufene Leistung
beendet gültige Integration und wird als Lücke geführt. Ein solcher Lauf darf
nicht nachträglich als vollständig gemessen in das Modell gelangen.

Zähler benötigen gültige Endpunkte, Resetbehandlung und einen passenden
Publikationsvertrag. Programm, Status, Restdauer und schrittweise steigende
Energiezähler erhalten keine pauschale 30-Sekunden-Frist. Ein eventuell nötiger
`state_reported`-Listener wird auf die tatsächlich genutzten Quellen begrenzt.

**Abnahme:** Konstant frisch publizierte Leistung bleibt gültig; eingefrorene
Leistung bei weiterlaufenden BM-Ticks nicht. Virtuelle Zeit prüft Lücke,
Wiederaufnahme, Zählerreset, Einheitenwechsel, NaN/Inf, unbekannte Werte und
Quellenwechsel mitten im Lauf. Bereits gespeicherte verdächtige Läufe werden
markiert bzw. konservativ ausgeschlossen, nicht durch erfundene Werte repariert.

## O05 — Quellen prüfbar zuordnen

**Beleg:** Live-AC meldet fehlende Messungen; alle vier Betriebsberichtquellen
fehlen. Eine gültige EPEX-Intervallserie wird durch gleichzeitig erkannte
Statistik-Sensoren bei der Autoerkennung verdrängt.

**Änderung:** Vorhandene Optionsgruppen und Berichte erweitern. Pro Rolle
anzeigen: Entity, Einheit, Messgrenze/Vorzeichen, Publikationsalter, gültige
Abdeckung, Fehler und wirksamer Fallback. Für EPEX nur verwendbare
Intervallserien als Kandidaten zählen. Bei mehreren echten Serien explizite
Auswahl; fehlender Preis bleibt unbekannt. Keine automatische Auswahl anhand
eines vieldeutigen Entity-Namens.

**Konkreter Live-Prüfplan:** Die drei vorhandenen AC-Quellen
`sensor.system_grid_total_power`, `sensor.victron_vebus_activein_l1_power_228`
und `sensor.victron_vebus_out_l1_power_228` lesend über den lokalen Playwright-MCP
auf gemeinsame Bilanzgrenze, Vorzeichen und tatsächliche Publikationsfrische
prüfen. `sensor.epex_spot_data_price` als ausdrückliche Serienauswahl bewerten.
Für PV/Hausverbrauch/Import/Export geeignete vollständige Quellen nachweisen.
Diese Analyse ändert keine Live-Optionen.

**Abnahme:** Eine gültige EPEX-Serie plus vier Statistik-Sensoren, zwei gültige
Serien, überlappende/ungültige/abgelaufene Intervalle und Sommerzeit. AC-Bilanz
bei Import, Export und Eigenversorgung prüfen. Die UI erklärt weiterhin
wirksame Reservevetos: Der untersuchte Plan hat `live_ac_floor_percent=100`;
eine Quellenzuordnung allein garantiert deshalb keine AC-Freigabe.

## O06 — Verlustloses, begrenztes Wochenarchiv

**Beleg:** Im finalen lokalen Prototyp sinken Planstände durch stündliche Segmente
und strukturelle Deltas mit Checkpoint spätestens alle 64 Pläne von rund
26,77 MB komprimiert auf 3,90 MB; Ereignisse zusätzlich auf 0,37 MB.
Alle 2.219 Planstände wurden per Hash und 2.218 benachbarte
Delta-Rekonstruktionen byteidentisch geprüft. Konfigurations-/Input-Deduplizierung
allein spart deutlich weniger, weil Ergebnisse den Großteil der Rohdaten stellen.

**Entwurf:**

1. Kleinen Runtimezustand von der Diagnosehistorie trennen. Versioniertes
   Manifest referenziert abgeschlossene Segmente mit Schema, Codeversion,
   Zeitbereich, Größe und Prüfsumme.
2. Segmente enthalten vollständige Checkpoints und exakt rekonstruierbare
   Änderungen; keine gerundeten Energiewerte oder verlorenen Ablehnungsgründe.
   Stunden- und Schemawechsel beginnen einen neuen Checkpoint. Kettenlänge
   begrenzen, Kompression/Validierung außerhalb des Eventloops.
3. Ein Writer besitzt die Segmente. Erst temporär vollständig schreiben und
   prüfen, dann atomar veröffentlichen und das Manifest aktualisieren.
   Queue begrenzen; Überlast/Lücken ausdrücklich zählen statt still verlieren.
4. Alte Schemata weiterhin lesen. Migration nur nach erfolgreicher Verifikation
   übernehmen; Original bis dahin erhalten. Ein defektes Segment darf andere
   Segmente und den Runtimezustand nicht unlesbar machen.
5. Aufbewahrung nach Zeit, Bytes und Ereigniszahl gemeinsam begrenzen. Den
   realen Beginn und jede Verdrängungsursache veröffentlichen.

Die gemessenen Größen gelten für stundenweise Kompression; unabhängige
Kompression jedes Checkpointblocks müsste neu vermessen werden. Decodierte
Segmentgröße, Delta-Tiefe und aktiver Schreibpuffer erhalten eigene Grenzen.
Ein lesender Export bindet Manifestgeneration und Ereignis-Watermark an einen
Snapshot; referenzierte Segmente dürfen währenddessen nicht gelöscht werden.
Shutdown wartet ausstehende Kommandoereignisse ab, bevor der Writer endgültig
schließt. Ein alter Coordinator-Worker darf nach Reload nichts mehr übernehmen.

**Kapazitätsentscheidung:** 32 MiB genügen weiterhin nicht verlässlich für
sieben Tage. Checkpoints alle 64 Pläne ergeben im Mittel extrapoliert
30,87 MiB/Woche, 168-mal die größte beobachtete Stunde aber 41,83 MiB.
Bei längeren Ketten sinkt der Mittelwert, die Rekonstruktionskosten steigen.
Das aktuelle Ereignislimit muss ebenfalls geprüft werden: Die erhaltene Rate
entspricht etwa 52.550 Ereignissen/Woche, 168-mal die Spitzenstunde rund 79.632.
Ein vorläufiges Referenzprofil mit 64 MiB und 100.000 Ereignissen einschließlich
Reserve für aktives Segment, Index und neue Diagnosefelder ist zu vermessen,
kein zugesicherter universeller Bedarf. Größere Anlagen können
weiter früher verdrängen; UI und Export müssen das offenlegen.

**Abnahme:** Gesamte erhaltene Historie exakt rekonstruieren; 168-Stunden-
Referenzlast mit mittlerer und beobachteter Spitzenrate; begrenzter RAM/Queue;
Crash vor/nach Segment- und Manifestwechsel; gekürzte/defekte Dateien;
Migration, Versionswechsel, Reload und Löschung eines Config-Entries.
Höchstens das betroffene Segment geht verloren. Export und Replay nennen
unvollständige Zeitbereiche. Rückweg zum alten Reader bleibt getestet.

## O07 — Entscheidung und Ausführung erklären

**Änderung:** Pro Entscheidungswechsel ein begrenztes Ereignis mit Plan-ID,
Controllergeneration, Entscheidungszeit, Grund, Quellenqualität, Live-Bedarf,
Budget/Floor und angefordertem Sollwert aufzeichnen. Serviceantwort und
physische Rückmeldung bleiben getrennte Ereignisse. Inverter-Number,
Blockzustand, Binärschalter und Fremdänderungen eindeutig unterscheiden.
Wiederholte identische Fünfsekundenentscheidungen brauchen keinen vollständigen
Planstand; ihre Dauer/Zähler können kompakt erfasst werden.

Das Backend veröffentlicht das tatsächlich angefragte Limit aus dem
Bestätigungspfad. `live_ac.limit_w` ist eine zusätzliche Freigabe, nicht allein
das effektive Aktorziel. Die UI berechnet deshalb kein vermeintlich bestätigtes
Limit aus Plan und Livewert. Freigegebene W sind außerdem keine gemessene Leistung.

Die Diagnose führt getrennte Zeiten für Eingabeaufnahme, Planaktivierung,
letzte Live-Entscheidung und Gerätebestätigung. `entity.last_updated` ist kein
Planzeitpunkt, weil Live-Publikationen ohne Neuplanung stattfinden.

**Abnahme:** Eine durchgehende Kette erklärt „Plan 0 W, Livefreigabe 2.300 W,
Gerätebestätigung 2.300 W“ sowie Freigabe mit ausgebliebener Bestätigung,
Fremdänderung, veraltete Messung und Reload. Requestzahl und binäre Wechsel
werden nicht als Fehlerquote verglichen. Ein sieben Tage alter Grund bleibt
innerhalb der in O06 nachgewiesenen Last rekonstruierbar.

## O08 — Anzeigen und Interaktion korrigieren

**Priorisierte Änderungen:**

- `reports.js`: „Im Plan erlaubt“ und „Jetzt freigegeben“ mit eigener Quelle
  und Zeit darstellen. Derzeit wird das Planlimit als aktuelle Freigabe
  bezeichnet; die Gegenprobe zeigt 0 W, obwohl Live 2.300 W bestätigt.
- Bei `unavailable` alte Prognose sichtbar als veraltet kennzeichnen. Leere
  Tagesdaten dürfen Archivfehler nicht vollständig ausblenden.
- Forecast-/Verbrauchsdiagramm: Auswahl über Zeitstempel erhalten, SVG-Fokus
  nach Publikation wiederherstellen, Touch/Pointer-Auswahl ergänzen.
- Verbrauchsbalken über Intervallzugehörigkeit auswählen: 10:45 gehört zum
  Slot 10–11 Uhr. Zeitgrenzen, Teilslots und wiederholte DST-Stunde prüfen.
- Loads-Zeitraumknopf behält Fokus beim HA-Refresh; Zeitzone invalidiert alle
  betroffenen Karten auch bei identischem Sensorobjekt.
- Unkonfigurierte optionale Quelle (`entity_id: null`) als „Nicht konfiguriert“
  darstellen, ohne klickbaren `null`-Link. Eine konfigurierte, fehlende Entity
  heißt dagegen „Nicht gefunden“. Appliances-Antworten über eine lokale
  monotone Publikationsgeneration absichern; A→B→A ist ein reproduzierter
  Clientfehler unter künstlicher Antwortverzögerung, P3-Härtung ohne belegten
  Live-Datenfehler.
- Zukünftige Fossibot-Buchungen bei unbekanntem SOC als vorläufig und mit
  erforderlichem Aufwecken erklären. Schlafende Geräte bleiben gemäß bestehendem
  Vertrag planbar; Telemetrie, Aktorfähigkeit und Einspeisebereitschaft werden
  getrennt ausgewiesen. Eine geplante Ladegelegenheit ist keine Bestätigung.

**Abnahme:** Lokale Browser-Tests für alle genannten Benutzeraktionen,
Tastatur und Touch, deutscher/englischer Text, kleines Display, HA-Zeitzone,
mehrere Karten und Refresh während aktiver Interaktion. Auswahlverlust durch
einen tatsächlich entfallenen Slot erhält einen nachvollziehbaren Fallback.
Live-AC-Daten erklären existierende Backendverträge; die UI erfindet keine
eigene einheitliche Frischefrist oder eigene Freigabelogik. Die verzögerte erste
A-Antwort wird nach A→B→A nie angezeigt; ein legitimer Backendrevisionreset
20→1 bleibt zulässig. Die lokale Publikationsgeneration ist unabhängig von
der numerischen Backendrevision.

## O09 — Lernabdeckung und Sommerzeit

**Abdeckung:** Live werden 656 gültige AC-Stunden durch 2.880 konfigurierte
Fensterstunden geteilt. Daraus folgen 22,8 %, obwohl die Gültigkeit erst am
30.08. beginnt. Bezogen auf 33 seitdem vergangene Kalendertage sind es grob
82,8 %; dieser Hilfswert berücksichtigt noch nicht alle Quellenepochen.
Wochenendbins haben 3–6 statt der verlangten 10 Proben. Deshalb verwendet der
aktuelle Horizont sieben Freitagslots gelernt und 48 Wochenendslots statisch.

**Änderung:** Fensterbelegung, Messabdeckung im tatsächlich gültigen Zeitraum,
Bin-Reife und Anteil gelernter Prognosezeit separat ausweisen. Beim letzten
Wert reale Dauer statt nur Slotanzahl berücksichtigen. Fehlende Daten,
bereinigte Ausschlüsse und noch zu wenige vergleichbare Tage unterscheiden.
Grenzwerte nicht allein zur Verbesserung einer Prozentanzeige senken.

**Sommerzeit:** `history_profile.py` summiert die doppelte lokale Stunde in
ein Wh-Feld; `core/load_profile.py` interpretiert dieses später numerisch als W.
Der Planner verwendet inzwischen korrekt 25 reale Stunden. Im isolierten
Transformationsbeispiel werden so aus konstanten 100 W an einem 25-Stunden-Tag
2.700 statt 2.500 Wh. Ein eingeschalteter Abschnitt von 02:10 Sommerzeit bis
02:50 Winterzeit wird als 40 statt 100 Minuten gezählt.

**Änderung:** Energie und beobachtete reale Dauer gemeinsam aggregieren;
W-Bins durch Dauer normalisieren. Schaltintervalle in UTC integrieren und
erst danach lokalen Stunden zuordnen. Bestehende D-C3/D-C5-Verträge und Tests
gemeinsam aktualisieren. Alte gefaltete Stunden nur bei ausreichender
Metadaten-/Recordergrundlage korrigieren, andernfalls gezielt ausschließen.

**Abnahme:** 23/24/25 reale Stunden, konstante Leistung, beide Fold-Werte,
partielle Schaltung über den Rücksprung, Zähler und Leistung, gemischte
fehlende Daten sowie Summe der Tagesenergie. Das Transformationsbeispiel
verwendet `min_samples=1`; zusätzlich den normalen Median-/Dämpfungspfad
testen. Kein Nachweis eines bereits live entstandenen DST-Fehlers behaupten.

## O10 — Marktpräferenz bei Teilabdeckung

**Reproduzierbarer Zielkonflikt, keine belegte Live-Fehlentscheidung:** Der aktuelle Vertrag
vergleicht bekannte Intervalle mit `W × Preisgewicht`, bei einem unbekannten
Intervall mit W allein. Das kann zyklische Präferenzen erzeugen: A=500 W mit
Gewicht 3 schlägt B=600 W mit Gewicht 1; B schlägt unbekanntes C=550 W; C
schlägt A. Die Implementierung folgt dabei der aktuellen Spezifikation.
Bei 200 Wh Vorbereitungsspielraum vor 350 Wh PV erhält B 200 Wh, A und C
erhalten nichts. Wird ausschließlich C auf 0 W gesetzt, erhält A 166,67 Wh.
Das ungenutzte unbekannte Fenster verändert somit die Wahl zwischen A und B.
Dass B dabei mehr AC-Energie nutzt, zeigt zugleich den Zielkonflikt zwischen
Marktpräferenz, binären Restquanten und Exportvermeidung.

**Entscheidung vor Änderung:** An kleinen vollständig prüfbaren Szenarien
bewerten, wann diese lokale Präferenz gewünschte Energie-/Zeitentscheidungen
verhindert. Bei relevantem Nachteil einen konsistenten Vergleichsbereich
definieren, etwa vollständige Preisabdeckung aller konkurrierenden Intervalle
oder eine ausdrücklich spezifizierte partielle Ordnung. Unbekannte Preise
werden weder Nullpreise noch ein erfundener günstiger Tarif. Ein globaler
Fallback kann bekannte Peaks verlieren; diesen Zielkonflikt dokumentieren.

**Abnahme:** Drei oder mehr konkurrierende Fenster, fehlende Preise vor/nach
einem bekannten Peak, negativer Preis, PV-Deadline, Restquanten und gleiche
Lasten. Kein neues Energiebudget, kein Marktpreisvorrang vor DC-Versorgung.

## O11 — Weniger Rechenarbeit ohne andere Entscheidungen

**Beleg:** Ein vollständiger identischer Replay benötigt lokal 37,89 s mit
292 Reserve-Proben, 870 Hüllkurvenberechnungen, rund 8,79 Mio.
`incoming_ceiling`-Auswertungen und 1,18 Mio. `step_hour`-Aufrufen.
Raw-AC, DC-only und Rückhalt-Retry derselben Probe berechnen dieselbe
`preparation_envelope` mehrfach.

**Änderung:** Diese Hüllkurve innerhalb des bestehenden auf einen Planaufruf
begrenzten Caches wiederverwenden. Den vorhandenen Probe-Schlüssel um alle
tatsächlich relevanten Eingaben absichern, keine teuren vollständigen
Objekthashes in inneren Schleifen erzeugen. Speicher und Lebenszeit begrenzen.
Abbruchprüfung auch bei Cachetreffern erhalten.

**Abnahme:** Der In-Memory-Prototyp erzielt exakt dasselbe Ergebnis mit
292 statt 870 Berechnungen. Er benötigt aber 39,59 s; ein Geschwindigkeitsgewinn
ist damit noch nicht nachgewiesen. Produktionsänderung erst übernehmen, wenn
Arbeitsmenge **und** wiederholte Laufzeit-/RAM-Messung überzeugen. Verschiedene
Konfigurationen, Quellen, Preise, Zusatzlasten, Ausnahme/Abbruch, verschachtelte
Kaskaden und aufeinanderfolgende Planaufrufe dürfen keinen Cachewert verwechseln.
Goldens und Replay bleiben exakt, einschließlich Begründungen. Liefert dieser
Cache keinen Nettozeitgewinn, nicht ausliefern; anschließend die wiederholten
`step_hour`-Aufrufe und sichere Kandidatenausschlüsse gezielt profilieren.

## O12 — Vergleich mit unabhängiger Anlagenphysik

**Ausbau vorhandener Tests:** Ein bestehender 24-Stunden-Test bildet Kaskaden
mit unabhängiger Physik ab. Für die aktuelle Markt-/Live-AC-/PSU-Kombination
fehlt noch ein gemeinsamer geschlossener Ablauf durch den Coordinator.
Offline-Replay beweist Determinismus, aber nicht den realen Nutzen einer
anderen Strategie auf derselben Anlage.

**Versuch:** Virtuelle Uhr, unabhängige Energie-/Aktorantwort, reale
Coordinator-Lifecyclepfade, konfigurierbare Messverzögerung und Neuplanung.
Training und Auswahl eines Vorschlags von dessen Bewertung trennen.
Aufeinanderfolgende Tage fortsetzen, damit ein leererer Endspeicher nicht als
kostenlose Importersparnis erscheint.

**Szenarien:** Sonnig, wechselhaft, mehrere dunkle Tage, unerwartete AC-Spitze,
DC-Spitze, PSU-Ausfall, eingefrorene Telemetrie, nicht bestätigter Aktor,
Lastmindestlaufzeit, Kaskaden-Wake, Tankfüllung, Neustart, Preisabdeckungslücke,
23/25-Stunden-Tag und abgeschnittener Forecast.

**Bewertung:** Getrennt berichten: Import/Export, DC-Netzenergie einschließlich
Verlusten, DC-Unterversorgung je Intervall, nutzbare Lastenergie, Standby,
SOC-Verlauf/Endenergie, Schaltzahl, Bestätigungslatenz, Planalter, CPU/RAM und
Messabdeckung. Kein einzelner gewichteter Score verdeckt eine Schutzverletzung.
Ein synthetisches Ergebnis wird nicht als gemessene Wochenersparnis bezeichnet.

## O13 — Reserve-Suche als bedingtes Experiment

Die ursprüngliche 500-Wh-Gegenprobe senkt nominalen Import um 573,98 Wh,
verbraucht aber 671,71 Wh Endspeicher. Mit den vorhandenen Wirkungsgraden
entspricht das 618,98 Wh später nutzbarer AC-Energie; die Bilanz ist dann
45 Wh schlechter. Bei pessimistischer PV entstehen zusätzlich 37,42 Wh
DC-Netzbezug. Bei oberer PV gibt es dagegen einen echten Vorteil bei gleicher
Endenergie. Die 15 berechneten Varianten beweisen keine allgemein bessere
Reservepolitik.

**Versuch nach O12:** Kleine feste Menge Rückhaltskandidaten nur im lokalen
Schattenvergleich. Gleiche Eingaben, Quellen und bindende Lasten; DC-Gates aus
O01; nominale, pessimistische und obere PV plus längere Fortsetzung vergleichen.
Endenergie ausdrücklich bewerten und verwendete Annahme veröffentlichen.
Rückhalt wirkt wegen binärer Quanten und Supportübergängen nicht monoton;
eine einfache Bisektion erhält deshalb keine Optimierungsgarantie.
Keine feste Konfiguration „500 Wh“ und keine unbeschränkte Suche.

**Übernahmekriterium:** Nachgewiesener Vorteil über unabhängige vollständige
Tagesabläufe, keine Verletzung höherrangiger Regeln, begrenzte Mehrarbeit,
erklärbarer Freigabegrund. Bei unklarem Vorteil bleibt die vorhandene Politik.
Der Operatorvertrag über Unsicherheit darf nicht indirekt geändert werden.

## O14 — Drei getrennte Modellversuche

1. **Unbekannte Unsicherheit:** D-C8 setzt fehlende Quantilbänder auf null.
   Damit kann ein gelernter DC-Pfad mit Bandbreite null bei unbekanntem AC den
   dynamischen Mindestpuffer 3 % aktivieren, obwohl statisch 5 % konfiguriert
   wären. Das ist spezifiziertes Verhalten. Erst getrennte Kennzeichnung
   unbekannter und bekannter kleiner Streuung schaffen; dann konservative
   Ersatzband-/Floor-Regeln anhand ausreichend abgedeckter Tage vergleichen.
   P50 bleibt unverzerrte Lastprognose, Schutz bleibt im Puffer. Keine pauschale
   Senkung des aktuell wirksamen 13,8-%-Puffers.
2. **Geräterestenergie:** Gemessene bisherige Zyklusenergie und
   programmspezifische Phasen gegen lineare Restzeitprojektion vergleichen.
   Nur valide Läufe aus O04 verwenden, Cross-Validation nach ganzen Läufen,
   Mindestzahl je Programm und linearer Fallback. Frühe Heizphase darf späte
   Restenergie nicht künstlich verdoppeln; unbekannte Programme bleiben robust.
3. **Gemeinsamer PV/DC-Bus:** Direkte Versorgung und Verluste der konkreten
   Topologie messen. Die 18,28-Wh-Modellabweichung eines synthetischen Slots
   beweist noch keinen Fehler der installierten Anlage. Erst danach explizite
   topologieabhängige Bilanz; unterstützte getrennte Pfade weiter prüfen.

## Gemeinsame Liefer- und Prüfregeln

Jedes Paket bekommt einen kleinen nachvollziehbaren Änderungssatz mit
Problemfall, neuem Verhalten, Vertragsreferenz und aussagekräftigem Test.
Kein pauschales Umschreiben der hohen bestehenden Coverage. Doppelte
Implementierungsberechnungen sind kein unabhängiges Testorakel.

Vor nutzersichtbaren Releases Manifest und Projektversion gemeinsam erhöhen,
Changelog aktualisieren, Golden-Änderungen begründen und vorhandene Gates
ausführen: Python/HA-Tests, Coverage, Ruff, mypy; bei Frontendänderungen Build,
Bundle, Lint/Format, Unit- und Browser-Tests. Zeit in Tests kontrollieren.
`TEST_MATRIX.md`, Feature-Verweise und Architektur-/Benutzertexte an die
tatsächlichen Verträge anpassen. Bestehende uncommittete Nutzeränderungen
bleiben bei der Umsetzung ausdrücklich erhalten.

Nach Release zunächst lesend auf HA prüfen. Für eine belastbare
Wirkungsbewertung mindestens einen vollständig messbaren zusammenhängenden
Zeitraum erfassen; Wetter-/Lastunterschiede benennen. Schutzregressionen führen
zur Rücknahme des jeweiligen kleinen Änderungssatzes. Archiv-Rücknahme darf
Runtimezustand und lesbare alte Segmente nicht verlieren. Forschung bleibt
getrennt auslieferbar und wird bei unklarem Nutzen nicht aktiviert.
