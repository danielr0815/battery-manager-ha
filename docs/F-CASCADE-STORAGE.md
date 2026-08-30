# F-CASCADE-STORAGE — kaskadierte Überschusslasten mit Speichern

Status: normative Feature-Spezifikation für v0.26.0.

## Ziel und Geltungsbereich

Battery Manager darf mehrere disjunkte lineare Ketten steuern. Jede Kette
besteht aus mindestens einem energiebegrenzten Speicher und genau einer
abschließenden, nicht energiebegrenzten Last:

```text
Root/PV/Grid → B1.input → B1.output → B2 … → Bn.output → Endlast
```

Verzweigungen, gemeinsam genutzte Lasten oder Aktoren und parallele Verbraucher
an einem Storage-Output sind nicht Teil dieses Features. Ohne `cascades` bleibt
das bisherige Planner- und Entity-Verhalten unverändert.

## Konfigurationsvertrag

Eine `cascade`-Subentry enthält den Namen, die geordneten Storage-Load-IDs, die
terminale Load-ID und den Actor-Confirmation-Timeout. Storage-Load-Subentries
tragen zusätzlich Output-Aktor und -Leistung, Sicherheits-Floor,
Kaskaden-Entladeziel,
Wake-/Handover-Parameter, Actor-Besitzmodi sowie optionale Wirkungsgrade,
Leistungslimits, Output-Overhead und das vollständige Standalone-Idle-Paar.

Es gelten zwingend:

- mindestens ein Mitglied; jedes Mitglied und jeder Actor kommt nur einmal vor;
- Mitglieder sind `energy_limited`, die Endlast ist es nicht;
- alle Mitglieder besitzen SOC, Charge-Gate, Output und Output-Leistung;
- B1 besitzt den einzigen Input-Control-Actor;
- `0 <= Sicherheits-Floor < Kaskaden-Entladeziel <= Ladeziel <= 100`;
- Wirkungsgrade liegen in `(0, 1]`, Caps und Timeouts sind positiv;
- Handover-Timeout ist mindestens 60 Sekunden;
- Idle-Schwelle und -Dauer werden nur gemeinsam konfiguriert.

Eine ungültige Referenz bleibt fail-closed sichtbar und muss durch Reconfigure
oder Löschen der Kaskade behoben werden.

## Planungsregeln

Die globale Load-Reihenfolge bleibt die äußerste Prioritätsordnung. Innerhalb
einer terminalen Kaskadenlast gilt die Quellenfolge Root/PV, B1 … Bn und danach
Root aus der Hausbatterie unter den bestehenden No-Import-, Batterieanteil-,
Stress- und G4-Gates. Ein neuer Aux-Start ist nur zulässig, wenn die
vollständige Mindestlaufzeit aus der Energie oberhalb der konfigurierten
Kaskaden-Entladeziele erbracht werden kann. Eine bereits laufende Episode darf
ihren Rest bis zum Ziel in nachgewiesenen Quellenfenstern fortsetzen. Ein
heutiger PV-Slot oder der Nachweis einer Wiederaufladung am selben Tag ist
nicht erforderlich: Das Entladeziel ist die bewusst verfügbare Reservegrenze.
Späteres Laden bis zum höheren normalen Ladeziel nutzt weiterhin die normale
globale Priorität.

Der Core simuliert die Speicher-SOCs mit Lade-/Entladewirkungsgrad, Charge-,
Output- und Passthrough-Caps sowie Output-Overhead. `HourFlows.extra_ac_wh`
bleibt die externe Root-Bilanz; interne Durchleitung und Aux-Energie erscheinen
nur in `CascadePlan`. `LoadPlan.managed_by_cascade` unterdrückt die unabhängige
Aktuation der Mitglieder und der Endlast.

Pro lokalem Kalendertag ist eine Aux-Entladeepisode zulässig. Direkter
PV-Takeover und Root-Return sind one-way. Ein Block über Mitternacht wird sicher
beendet und für den neuen Tag neu bewertet. Cache-SOCs bis sieben Tage dürfen
Vorschau und Aggregat speisen; vor Entladung sind neue numerische Live-SOCs
aller Mitglieder zwingend. Die normale Aux-Planung entlädt nie unter das
Kaskaden-Entladeziel; der niedrigere Floor bleibt eine zusätzliche
Sicherheitsgrenze gegen Telemetrie- oder Schaltfehler.

## Executor- und Safety-Vertrag

Neue oder sicherheitsrelevant geänderte Kaskaden starten mit Automation AUS.
Der zentrale `CascadeManager` ist alleiniger Actor-Besitzer. Ketten sind
untereinander parallelisierbar, Operationen innerhalb einer Kette sind
serialisiert.

Der Erst-Wake ist geordnet:

1. alle Charge-Gates AUS;
2. Root-Eingang AN;
3. Outputs B1 bis Bn AN;
4. frische numerische SOCs abwarten und neu planen;
5. optionalen Endlast-Aktor AN;
6. übersprungene Upstream-Outputs trennen, Root AUS;
7. gewählte Quelle mit zwei ausschließlich nach der Umschaltung beobachteten,
   mindestens 60 Sekunden getrennten Leistungssamples bestätigen.

Der vollständige Wake erhält genau einen Retry nach 15 Minuten. Ein
erfolgreicher Service-Aufruf bestätigt einen `assumed_state`-Actor logisch; die
fehlende physische Rückmeldung ist als Diagnoseeinschränkung zu verstehen.
Bei Actoren mit echter Zustandsrückmeldung gilt der Service-Aufruf allein
dagegen nicht als Bestätigung: Der Manager wartet innerhalb des konfigurierten
Actor-Confirmation-Timeouts auf den Zielzustand und setzt erst dann seinen
Claim. Ein bereits bestätigter Zielzustand wird ohne redundanten Service-Aufruf
übernommen; damit bleibt insbesondere wiederholtes Safe-OFF idempotent.

Ziel-, Floor- und Safety-Abbrüche übersteuern Dwell. Safe-OFF schaltet die
Endlast, Outputs downstream→upstream, Charge-Gates und Root aus. Ein
Safe-OFF-Fehler
setzt einen Hard-Fault und Repair; Reset versucht Safe-OFF erneut, löscht nur
bei Erfolg und lässt Automation AUS. `exclusive` wertet Fremdänderungen bei
aktiver Automation als Fault. `shared` beendet Claims und Automation ohne
Rollback (Hands-off); Wiederaufnahme erfordert bewusst AUS→AN. Bewusstes
Automation-AUS führt genau einmal Safe-OFF aus und gibt danach alle Actors für
manuelle Bedienung frei. Vor einem erneuten Automation-AN muss die Kette
vollständig AUS sein.

## HA-Datenvertrag

Je Kaskade entstehen Root-Empfehlung, Mode/Forecast, kapazitätsgewichteter SOC,
Automation, Fault und Fault-Reset. Alte Member-/Leaf-Empfehlungen bleiben für
API-Kompatibilität vorhanden, sind aber AUS und tragen
`managed_by_cascade=<id>`.

Der Forecast-Sensor veröffentlicht je Kaskade zusätzlich den lesbaren
`source_name`, `member_details`, `terminal_name` und `schedule`: genau ein
Block pro belegtem Plan-Slot mit
`start`/`end`, Root-Grenzenergie, terminaler Energie, den Quellen `root`/`aux`
und einer Aktivitätsliste. Aktivitäten unterscheiden Mitgliedsladung,
Mitgliedsentladung, benötigte AC-Ausgänge und die von Root bzw. einem
Aux-Speicher gelieferte Endlastenergie. Lade-/Entladeaktivitäten tragen die
Slot-SOC-Grenzen; Laden nennt zusätzlich die gespeicherte Energie.

Die SOC-Forecast-Card behandelt die gesamte Kaskade als Black Box: Eine
einzige Spur zeigt ausschließlich Slots mit Root-Aufnahme und deren Wh.
Aux-only-Aktivität und interne Details erscheinen dort nicht. Die separate
`battery-manager-cascade-card` zeichnet Root-Aufnahme, Laden, Entladen und
AC-Ausgang jedes Mitglieds sowie die Endlast als eigene Zeilen. Damit bleibt
der elektrische Außenbezug im Gesamtbild eindeutig, während die eigene
Kaskaden-Zeitspur den vollständigen internen Plan erklärt. Kaskadenmitglieder
bleiben aus den normalen Lastspuren ausgeschlossen.

Ein Slot ohne Mitgliedsladung und ohne Endlastsegment hat exakt `0 Wh`
Root-Energie. Insbesondere darf der interne Output-Overhead bei der
Sentinel-Position `deepest = -1` keine Phantomenergie erzeugen und B1 dadurch
ohne geplante Aktivität starten.

`cascade_plans` trennt geplante Root- und Aux-Energie sowie tatsächliche
Aux-Energie. Root-/Surplusbilanz wird ausschließlich am Root-Messpunkt gebucht;
interne Zähler werden nie summiert. Topologie, Automation, Episode, Quelle,
Zielunterschreitungen, Claims, Fault/Hands-off, Retry, SOC-Cache und
Tagesenergie werden
persistiert; Powerfenster und HA-Offline-Zeit nicht.

Die Executor-Phasen `recovering` und `complete` sind bei aktiver Automation
reine Diagnosen, keine Actor-Freigabe. Auch in diesen Phasen wird Safe-OFF
idempotent durchgesetzt.
Entfällt ein Aux-Segment während `running`, wird der Pfad sofort beendet und
wechselt abhängig vom offenen Recovery-Ziel nach `recovering` oder `complete`.

## Migration und Betrieb

Die Runtime-Store-Hülle ist Version 2 und übernimmt die defensiv gelesene
v1-Payload. Vor Aktivierung im exklusiven Modus müssen Fremdautomationen auf
denselben Aktoren deaktiviert werden, insbesondere die bekannte
`automation.f2400_b_ac_out_off`; alternativ wird der betreffende Actor bewusst
auf `shared` gestellt.

Wird ein `shared` Actor entgegen dem letzten Manager-Claim extern geändert,
obwohl der frische Slotplan den geclaimten Zustand weiterhin benötigt, ist
Automation AUS bei `hands_off=true` eine kontrollierte Besitzübergabe und kein
Hard-Fault. Ein externes AUS, das bei einem inzwischen unbelegten Slot bereits
dem frischen Safe-OFF-Ziel entspricht, wird dagegen als Konvergenz übernommen;
das unterstützt insbesondere Root-Steckdosen mit normalem Nullleistungs-
Auto-Off. Bei aktivem `exclusive` bleibt eine Abweichung ein Fault. Der
Automation-Schalter veröffentlicht `phase`, `hands_off` und `fault` direkt als
Attribute; nach Safe-OFF kann `shared` bewusst wieder aktiviert werden. Bei
bewusst ausgeschalteter Automation bleiben nach dem einmaligen Safe-OFF auch
exklusive Actors manuell bedienbar; sie werden erst mit erfolgreichem
Automation-AN erneut geclaimt.
