# Cascade storage design

Status: Implementierungsdesign zu [F-CASCADE-STORAGE](F-CASCADE-STORAGE.md),
aktualisiert für v0.30.0.

## Schichten und Verantwortungen

`core/model.py` definiert ausschließlich frozen Dataclasses. `core/cascade.py`
legt die Kaskadenplanung um den bestehenden Allokator: Der Legacy-Plan wird
zuerst unverändert erzeugt. Ein Aux-Kandidat darf ausschließlich Energie
oberhalb der Kaskaden-Entladeziele nutzen und verändert den gemeinsamen
Storage-SOC-Vektor; ein Replan verteilt spätere Root-Energie weiterhin nach der
normalen globalen Priorität. Eine Wiederaufladung am selben Tag ist keine
Vorbedingung. Der Pfad `SystemConfig.cascades == ()` ruft ausschließlich den
alten Planner auf und ist der Kompatibilitätsanker für vorhandene Goldens.

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
normale priorisierte Überschusslast.

## Executor-Zustände

Stabile Zustände sind `idle`, `running`, `recovering`, `complete`, `fault` und
`hands_off`; `waking` und `proving` sind transient. Nach Neustart werden
transiente Zustände nie blind fortgesetzt. Der aktuelle physische Zustand wird
entweder sicher beendet oder bei konsistenter frischer Telemetrie durch ein
neues 60-s-Fenster bewiesen.

Actor-Claims werden pro Kaskade in einem Lock serialisiert. Disjunkte Ketten
verwenden getrennte Locks. Ein Proof-Fenster existiert nur im Speicher und wird
bei unbekannter Leistung, Transition oder Restart verworfen. Persistiert werden
nur bestätigte Claims und stabile Episode-/Fault-Evidenz, wodurch HA-Downtime
keine Timeout- oder Energie-Gutschrift erzeugt.

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
