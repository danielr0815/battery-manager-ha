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
tragen zusätzlich Output-Aktor und -Leistung, Entlade-Floor, Recovery-Ziel,
Wake-/Handover-Parameter, Actor-Besitzmodi sowie optionale Wirkungsgrade,
Leistungslimits, Output-Overhead und das vollständige Standalone-Idle-Paar.

Es gelten zwingend:

- mindestens ein Mitglied; jedes Mitglied und jeder Actor kommt nur einmal vor;
- Mitglieder sind `energy_limited`, die Endlast ist es nicht;
- alle Mitglieder besitzen SOC, Charge-Gate, Output und Output-Leistung;
- B1 besitzt den einzigen Input-Control-Actor;
- `0 <= floor < recovery <= target <= 100`;
- Wirkungsgrade liegen in `(0, 1]`, Caps und Timeouts sind positiv;
- Handover-Timeout ist mindestens 60 Sekunden;
- Idle-Schwelle und -Dauer werden nur gemeinsam konfiguriert.

Eine ungültige Referenz bleibt fail-closed sichtbar und muss durch Reconfigure
oder Löschen der Kaskade behoben werden.

## Planungsregeln

Die globale Load-Reihenfolge bleibt die äußerste Prioritätsordnung. Innerhalb
einer terminalen Kaskadenlast gilt die Quellenfolge Root/PV, B1 … Bn und danach
Root aus der Hausbatterie unter den bestehenden No-Import-, Batterieanteil-,
Stress- und G4-Gates. Ein Aux-Start ist nur zulässig, wenn die vollständige
Mindestlaufzeit über nachgewiesene Quellen erbracht und jedes Mitglied bis zum
Ende des letzten heutigen PV-positiven Slots auf sein Recovery-Ziel gebracht
werden kann. Diese Recovery nutzt die normale Priorität; das höhere normale
Ziel bleibt optional.

Der Core simuliert die Speicher-SOCs mit Lade-/Entladewirkungsgrad, Charge-,
Output- und Passthrough-Caps sowie Output-Overhead. `HourFlows.extra_ac_wh`
bleibt die externe Root-Bilanz; interne Durchleitung und Aux-Energie erscheinen
nur in `CascadePlan`. `LoadPlan.managed_by_cascade` unterdrückt die unabhängige
Aktuation der Mitglieder und der Endlast.

Pro lokalem Kalendertag ist eine Aux-Entladeepisode zulässig. Direkter
PV-Takeover, Root-Return und Recovery sind one-way. Ein Block über Mitternacht
wird sicher beendet und für den neuen Tag neu bewertet. Cache-SOCs bis sieben
Tage dürfen Vorschau und Aggregat speisen; vor Entladung sind neue numerische
Live-SOCs aller Mitglieder zwingend.

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

Floor- und Safety-Abbrüche übersteuern Dwell. Safe-OFF schaltet die Endlast,
Outputs downstream→upstream, Charge-Gates und Root aus. Ein Safe-OFF-Fehler
setzt einen Hard-Fault und Repair; Reset versucht Safe-OFF erneut, löscht nur
bei Erfolg und lässt Automation AUS. `exclusive` wertet Fremdänderungen als
Fault. `shared` beendet Claims und Automation ohne Rollback (Hands-off);
Wiederaufnahme erfordert bewusst AUS→AN.

## HA-Datenvertrag

Je Kaskade entstehen Root-Empfehlung, Mode/Forecast, kapazitätsgewichteter SOC,
Automation, Fault und Fault-Reset. Alte Member-/Leaf-Empfehlungen bleiben für
API-Kompatibilität vorhanden, sind aber AUS und tragen
`managed_by_cascade=<id>`.

`cascade_plans` trennt geplante Root- und Aux-Energie sowie tatsächliche
Aux-Energie. Root-/Surplusbilanz wird ausschließlich am Root-Messpunkt gebucht;
interne Zähler werden nie summiert. Topologie, Automation, Episode, Quelle,
Recovery, Claims, Fault/Hands-off, Retry, SOC-Cache und Tagesenergie werden
persistiert; Powerfenster und HA-Offline-Zeit nicht.

## Migration und Betrieb

Die Runtime-Store-Hülle ist Version 2 und übernimmt die defensiv gelesene
v1-Payload. Vor Aktivierung im exklusiven Modus müssen Fremdautomationen auf
denselben Aktoren deaktiviert werden, insbesondere die bekannte
`automation.f2400_b_ac_out_off`; alternativ wird der betreffende Actor bewusst
auf `shared` gestellt.

