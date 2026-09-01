# Battery Manager v0.34.2

Der Aux-Wake einer Speicherkaskade bleibt jetzt vom ersten akzeptierten
Root-Einschalten bis zur sicheren Proof-Grenze als bereits laufende Episode in
Slot 0 verankert. Zuvor meldete der Executor `waking_members` dem Rolling
Planner als `idle`. Die Latest-First-Planung konnte den gerade begonnenen Lauf
deshalb beim nächsten Coordinator-Zyklus wieder in die Zukunft verschieben und
Root abschalten, obwohl der erste Fossibot bereits aufwachte. Im Live-Vorfall
vom 1. September taktete `switch.bad_waschmaschine` dadurch etwa alle zehn
Sekunden AUS/AN und die Kaskade erreichte den AC-Ausgang von B1 nicht.

Der Executor behandelt einen angenommenen Aux-Wake zusätzlich als atomare,
durch die konfigurierten Wake-Timeouts begrenzte Aktor-Transition. Ein
kurzfristig zurückgezogener oder neu positionierter Rolling Plan kann Root
daher nicht mehr mitten im Wake power-cyclen. Globale Floor- und
Daten-Sicherungen, deaktivierte Mitglieder sowie manuelle Besitzübergaben
brechen weiterhin sofort ab. Wird der Plan nach abgeschlossenem Wake und
Leistungsnachweis tatsächlich zurückgezogen, führt der normale Running-Pfad
geordnet Safe-OFF aus.

799 Tests sind grün. Der reine Planner-Core bleibt vollständig abgedeckt; die
Gesamt-Coverage beträgt 95,21 Prozent.
