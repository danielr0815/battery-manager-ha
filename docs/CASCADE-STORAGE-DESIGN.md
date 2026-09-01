# Cascade storage design

Status: Implementierungsdesign zu [F-CASCADE-STORAGE](F-CASCADE-STORAGE.md),
aktualisiert für v0.34.1.

## Schichten und Verantwortungen

`core/model.py` definiert ausschließlich frozen Dataclasses. `core/cascade.py`
legt die Kaskadenplanung um den bestehenden Allokator: Der Legacy-Plan wird
zuerst mit der Kaskaden-Prioritätsordnung und einem internen PV-only-Set für
alle Mitglieder erzeugt. Ein Aux-Kandidat nutzt Energie bis zum normalen Ziel
beziehungsweise exportgedeckt bis zum Floor und verändert den gemeinsamen
Storage-SOC-Vektor; ein Replan verteilt spätere Root-Energie weiterhin nach der
globalen Priorität, darf Kaskadenmitglieder aber ausschließlich aus
gleichzeitigem Restexport laden. Der Pfad `SystemConfig.cascades == ()` ruft
ausschließlich den alten Planner auf und ist der Kompatibilitätsanker für
vorhandene Goldens.

```text
HA states → PlanInputs + persisted CascadeRuntimeState
                  │
          legacy Root allocation
                  │
       Aux discharge above target
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
Deshalb besitzt der Kaskadenlayer keinen separaten Root-Surplus-Allocator. Er
nutzt das Ergebnis und denselben Replan des bestehenden Optimizers. Die
Kaskaden-Endlast darf unabhängig von heutigem PV Energie oberhalb der
Mitgliedsziele nutzen; Root-Ladung bis zum höheren Ladeziel bleibt danach eine
priorisierte Überschusslast mit einer zusätzlichen harten Quellengrenze. Für
jeden belegten Ladeabschnitt muss der Restexport desselben Slots dessen Energie
vollständig decken. Damit können weder `battery_tolerance` noch Pass 2 oder ein
At-Max-Topup die Hausbatterie zum Laden eines Fossibots heranziehen. Diese
Einschränkung gilt nicht für die terminale Endlast.

Ein neuer Aux-Block wird innerhalb des heute noch elektrisch freien Fensters
an den spätestmöglichen Slotrand geschoben. Das ist die feinste ohne separaten
Executor-Timer sicher ausführbare Auflösung; der partielle Slot 0 und die
Rolling Replans lassen den Start bei Annäherung weiter konvergieren. Eine
akzeptierte, noch Mitglieder weckende, laufende oder im Leistungsnachweis
befindliche Episode bleibt zwingend in Slot 0 und wird nie nachträglich
verschoben. Der Executor behandelt den begonnenen Aux-Wake zusätzlich bis zur
Proof-Grenze als atomare Transition, damit selbst ein vorübergehend
widersprüchlicher Rolling Plan keinen Root-Schaltzyklus auslösen kann.

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

Bewusstes Automation-AUS führt einmal Safe-OFF aus und gibt die Actors danach
frei. Ein Shared-Fremdeingriff wechselt ohne spätere Rücknahme in `hands_off`.
Faults bleiben dagegen bis zum Reset fail-closed; Automation-AN übernimmt eine
Kette nur, wenn alle Actors AUS sind und frische Mitglieds-SOCs vorliegen.

## Fehler- und Recovery-Modell

Jeder automatische Übergang ist fail-closed. Wake-Fehler führen zunächst zu
Safe-OFF und einem einmaligen 15-Minuten-Retry; ein zweiter Fehler wird zum
Hard-Fault. Entladeziel, Floor, ungültige Topologie und fehlgeschlagenes
Safe-OFF beenden die Episode. `recovery_deadline` ist für die bedingungslos bis
zum Entladeziel laufende Episode `None`. Nutzt der Planner exportgedeckt die
Reserve darunter, bezeichnet es dagegen das Ende des ersten Exporttages, bis
zu dem der globale Replan die Rückkehr aller Mitglieder zum Entladeziel
nachgewiesen hat; eine verfehlte Zusage erzeugt die Recovery-Warnung.

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
