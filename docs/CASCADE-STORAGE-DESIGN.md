# Cascade storage design

Status: Implementierungsdesign zu [F-CASCADE-STORAGE](F-CASCADE-STORAGE.md).
Dieses Dokument beschreibt den Zielalgorithmus des normativen
Planungsnachtrags vom 2026-09-02. Abweichungen im Code sind Fehler und werden
durch die zugehörigen Vertrags- und Regressionstests sichtbar gemacht.

## Schichten und Verantwortungen

`core/model.py` definiert ausschließlich frozen Dataclasses. `core/cascade.py`
legt eine tagesweite Fixpunktplanung um den bestehenden Allokator. Der Pfad
`SystemConfig.cascades == ()` ruft ausschließlich den alten Planner auf und
ist der Kompatibilitätsanker für vorhandene Goldens.

```text
HA states → PlanInputs + persisted CascadeRuntimeState
                  │
       terminal/direct allocation
                  │
       recovery-to-50 allocation
                  │
          export-only top-up
                  │
       latest feasible Aux trial
                  │
        replan until fixed point
                  │
             CascadePlan
                  │
        CascadeManager actor FSM
```

Der Core kennt keine Entity-IDs oder Service-Aufrufe. Der Coordinator löst
Subentry-IDs in `LoadCascade`/`CascadeMember` auf, annotiert Live-/Cache-SOC und
reicht persistierten Episodenstatus ein. `CascadeManager` ist die einzige
Komponente, die Actors einer verwalteten Kette schaltet.

## Energie- und Prioritätsmodell

Die Root-Bilanz ist eine Systemgrenze. Eigene Speicherladung und terminaler
Root-Betrieb werden dort genau einmal erfasst; Output-Passthrough, Overhead und
Aux-Batterieenergie bleiben interne `CascadeSlotFlow`-Daten. So bleiben
`lost_surplus`, `prevented_export`, Consumption-History und bestehende
Energiekarten frei von Doppelzählung.

Die physische Quellenreihenfolge darf die globale Lastpriorität nicht umgehen.
Deshalb benutzt der Kaskadenlayer denselben globalen Replan, ergänzt ihn aber
um explizite Phasen. Die Kaskaden-Endlast wird zuerst mit den normalen
No-Import-/Stressregeln geplant. Danach wird jedes beim aktuellen Replan offene
Recovery-Defizit bis zum Ende des **heutigen lokalen Kalendertages** bedient.
Dabei darf ansonsten in die Hausbatterie fließende PV nur zeitverschoben
werden, wenn die vollständige Tagessimulation mindestens dieselbe spätere
AC-Exportmenge abbaut und die geschützten Haus-SOC-Ziele erhält. Erst danach
belegt Top-up ausschließlich schon sichtbaren Restexport. Energie nach
Mitternacht darf eine heutige Recovery weder maskieren noch erfüllen.

Jeder Aux-Kandidat wird in den aktuellen SOC-Vektor eingerechnet und löst
anschließend dieselbe vollständige Phasenfolge erneut aus. Akzeptiert wird er
nur, wenn Netzimport und geschützte SOC-Minima nicht schlechter werden, der
Restexport des heutigen Tages sinkt und sämtliche erreichbaren Recovery-Ziele
heute weiter erfüllt sind. Die Suche läuft über alle elektrisch freien Fenster
des Tages und wiederholt sich, bis kein Kandidat die lexikographische Zielfolge
mehr verbessert. Dadurch sind mehrere durch Root-Abschnitte getrennte
Aux-Episoden möglich. Erst das Fixpunktergebnis geht an `plan_feedin`; Feed-in
ist kein Input für die Kaskadenoptimierung und kann daher keine nutzbare
Kaskadenaufnahme verdecken.

Knappheit wird zweistufig aufgelöst: Alle Recovery-Defizite werden in globaler
Mitgliedspriorität geprüft, bevor irgendein Top-up Energie erhalten darf.
Top-up darf lediglich nicht nutzbare Fragmente übernehmen. Ein geringfügiges
Überschwingen des Recovery-Ziels durch das Mindestlaufzeitraster bleibt dieser
Recovery-Buchung zugerechnet und begründet keinen allgemeinen Top-up-Vorrang.

Für jede Last wird einmal `effective_power_w` gebildet: robuste
Planungsleistung, bei Mitgliedern begrenzt auf `max_charge_power_w`. Optimizer,
Kaskadenfluss, SOC-Simulation und HA-Zeitplan konsumieren genau diesen Wert.
Das verhindert die bisher mögliche Aufspaltung in gelernte Lastenergie,
nominale Root-Energie und anders begrenzte SOC-Energie.

Ein neuer Aux-Block wird ohne späteren Exportnutzen in jedem geeigneten, heute
noch elektrisch freien Fenster an den spätestmöglichen Slotrand geschoben. Ein
exportvermeidender Kandidat wird dagegen früh genug vor die Solaraufnahme
gelegt. Der Legacy-Replan erhält je Mitglied zwei kumulative Zeitreihenlimits
(Recovery und normales Ladeziel). Sie geben Ladeenergie erst nach der
tatsächlichen Aux-Entladung frei; ein am Abend geplanter SOC-Abfall erzeugt
also keinen fiktiven Lade-Headroom am Vormittag. Root- und Aux-Aktivität
überlappen nie; ein Root-Fenster schließt eine Episode ab, lässt aber eine
spätere neue Episode zu. Eine akzeptierte, noch Mitglieder weckende, laufende
oder im Leistungsnachweis befindliche Episode bleibt zwingend in Slot 0 und
wird nie nachträglich verschoben. Der Executor behandelt den begonnenen
Aux-Wake zusätzlich bis zur Proof-Grenze als atomare Transition.

Neue Episoden reservieren vor der Nutzenergie ein konservatives
Transitionsbudget aus Actor-Bestätigung, den für den Pfad benötigten
Mitglieds-Wake-Timeouts und dem mindestens 60-sekündigen Leistungsnachweis.
Dieses Budget zählt nicht als gelieferte Endlastenergie. Ein Fenster muss
Transition plus Mindestlaufzeit tragen; ein bereits bewiesener laufender Pfad
hat keinen erneuten Startabzug. Der konkrete Slotvertrag veröffentlicht beide
Anteile getrennt, damit Simulation und Executor dieselbe Zeitachse besitzen.

Der Mitglieder-Wake trennt Versorgungsevidenz von Befehlsbereitschaft. Eine
frische SOC-/Input-/Output-Publikation nach dem vorgelagerten Schaltvorgang
beweist nur, dass Energie und Telemetrie das Mitglied erreicht haben. Der
interne AC-Ausgang gilt erst mit bestätigtem Zielzustand als eingeschaltet.
Scheitert ein erster Bestätigungsversuch während das Gerät noch bootet, bleibt
der sichere Root-gespeiste Wake-Präfix bestehen und der Executor wiederholt
ausschließlich diesen Output-AN-Schritt in folgenden Zyklen. Das je Mitglied
konfigurierte Wake-Timeout begrenzt Telemetrie und diese Versuche gemeinsam;
danach folgen Diagnose, Fault/Retry-Regel und geordnetes Safe-OFF. Andere
Actor-Fehler werden nicht auf diese Weise verlängert. Ein One-shot-Refresh an
der absoluten Wake-Deadline verhindert, dass eine während der blockierenden
Actor-Bestätigung absorbierte Telemetrieflanke den Retry bis zum regulären
Fünf-Minuten-Poll verzögert.

Rolling Replans starten stets mit aktuellem SOC und dem Rest des laufenden
Slots. Tatsächlich gelieferte Energie steckt damit bereits im neuen Zustand
und darf nur noch diagnostisch kumuliert werden. Geplante Vergangenheit wird
abgeschnitten; weder sie noch künftige Folgetage dürfen den heutigen Nachweis
erfüllen. Diese Regel macht Standby/HA-Restart zu einem normalen Replan statt
zu einer zweiten Energiegutschrift.

## Executor-Zustände

Stabile Zustände sind `idle`, `root`, `running`, `recovering`, `complete`,
`fault` und `hands_off`; `waking`, `waking_members` und `proving` sind
transient. Nach Neustart werden
transiente Zustände nie blind fortgesetzt. Der aktuelle physische Zustand wird
entweder sicher beendet oder bei konsistenter frischer Telemetrie durch ein
neues 60-s-Fenster bewiesen.

Actor-Claims werden pro Kaskade in einem Lock serialisiert. Disjunkte Ketten
verwenden getrennte Locks. Ein Proof-Fenster existiert nur im Speicher und wird
bei unbekannter Leistung, Transition oder Restart verworfen. Persistiert werden
nur bestätigte Claims und stabile Episode-/Fault-Evidenz. Bei einem
unterbrochenen Aux-Wake bleibt die beabsichtigte Quelle als ausdrücklicher
Restart-Hinweis erhalten, damit der erste Rolling Plan die bereits begonnene
Episode nicht als neuen Start behandelt; der Hinweis selbst gilt weder als
Actor-Claim noch als Leistungsnachweis. Dadurch erzeugt HA-Downtime keine
Timeout- oder Energie-Gutschrift.

Der persistierte Snapshot ersetzt `waking`, `waking_members`, `proving` und
sonstige unbekannte Phasen durch einen stabilen logischen Ausgangszustand,
behauptet damit aber keinen physischen Safe-OFF. Nach dem Neustart wartet eine
aktive Kette bis zu 60 Sekunden auf explizite ON-/OFF-Zustände aller Actors und
klassifiziert dann den vollständigen Live-Vektor gegen den frischen Plan.
Vollständige Root- und Aux-Pfade werden ohne Schalten übernommen; Aux beginnt
ein neues Proof-Fenster. Ein geordneter Root-gespeister Wake-Präfix setzt mit
einer frischen Gerätepublikation an der ersten noch offenen Stufe fort. Auch die
bereits begonnene geordnete Break-Seite eines Aux-Takeovers darf abgeschlossen
werden. Nur unbekannt gebliebene oder keinem sicheren Übergang entsprechende
Vektoren führen zum geordneten Safe-OFF. Eine deaktivierte Kette erhält keine
Reconciliation, weil ihre Actors bereits dem Operator gehören. Eine
erfolgreiche manuelle Aktivierung aus bestätigt vollständig AUS löscht
unabhängig davon sämtliche transiente Evidenz und beginnt als neue
Besitzepisode. Alle drei Bedienpfade (AN, AUS, Fault-Reset) laufen unter
demselben Per-Kaskaden-Lock wie der automatische Executor.
Ein erneutes AN bei bereits aktiver Kaskade ist dagegen ein reines No-op. Es
darf auch dann keine Wake-/Proof-Evidenz löschen, wenn ein Shared Actor gerade
kurz AUS meldet; diese Abweichung klassifiziert anschließend ausschließlich der
normale Executor und übergibt sie gegebenenfalls geordnet an `hands_off`.

Actor-Besitz wird nicht nur anhand der Planner-Last-ID geprüft. Unmittelbar vor
jedem `homeassistant.turn_on`/`turn_off` wird die Ziel-Entity erneut gegen alle
gespeicherten Kaskadentopologien aufgelöst. Kaskadenaktoren akzeptieren dort nur
die ID ihres `CascadeManager`; ein generischer oder verspäteter Hintergrundpfad
kann die physische Kette deshalb auch bei einem zuvor verfehlten Load-ID-Filter
nicht verändern.

Bewusstes Automation-AUS führt einmal Safe-OFF aus und gibt die Actors danach
frei. Ein Shared-Fremdeingriff wechselt ohne spätere Rücknahme in `hands_off`.
Faults bleiben dagegen bis zum Reset fail-closed; Automation-AN übernimmt eine
Kette nur, wenn alle Actors AUS sind und frische Mitglieds-SOCs vorliegen.

## Fehler- und Recovery-Modell

Jeder automatische Übergang ist fail-closed. Wake-Fehler führen zunächst zu
Safe-OFF und einem einmaligen 15-Minuten-Retry; ein zweiter Fehler wird zum
Hard-Fault. Entladeziel, Floor, ungültige Topologie und fehlgeschlagenes
Safe-OFF beenden die Episode. `recovery_deadline` bezeichnet für jede unter das
Entladeziel führende Episode das Ende des aktuellen lokalen Kalendertages. Der
Nachweis darf nicht auf den ersten beliebigen Exporttag des
Mehrtageshorizonts ausweichen; eine verfehlte Zusage bleibt offen und erzeugt
die Recovery-Warnung.

`assumed_state` kann nur logische Bestätigung liefern. Das Design akzeptiert
dies ausdrücklich, markiert die eingeschränkte physische Garantie aber in
Diagnose und Dokumentation. `shared` ist ein bewusster vollständiger
Kontrollverzicht nach erkannter Fremdänderung, nicht eine schwächere Variante
des exklusiven Rollbacks. Dieselbe Freigabe gilt nach einem bewusst
ausgeschalteten Automationsschalter auch für exklusiv konfigurierte Actors.

## Erweiterungspunkte und Tests

`CascadePlan` ist der gemeinsame Vertrag für Entity-Plattformen und die
gebündelte Kaskadenkarte. Neue Topologien müssen linear und disjunkt bleiben;
Verzweigungen erfordern ein neues Fluss- und Claim-Modell und dürfen nicht durch
zusätzliche IDs in diesem Vertrag emuliert werden.

Tests sind entlang der Schichtgrenze aufgebaut: Core testet Physik, Priorität,
Entladeziel, Cache und Legacy-Neutralität; HA testet Service-Reihenfolge,
Bestätigungsfenster, Retry, Ownership, Safe-OFF, Persistenz und Entity-Vertrag.
Zusätzliche Invarianten decken mindestens ab: Folgetagsenergie maskiert keine
heutige Recovery; Recovery aller erreichbaren Mitglieder schlägt Top-up;
Residualexport wird nach terminalem Dauerblock erneut für Recovery und Top-up
verwendet; mehrere Aux-Episoden sind möglich; die Summe aller Slotenergien
entspricht Root-/Aux-/Feed-in-Aggregaten; jeder neue Start trägt sein
Transitionsbudget; kein akzeptierter Aux-Kandidat erhöht Import oder verletzt
das Tagesendziel.
