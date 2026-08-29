# Battery Manager v0.28.0

Kaskaden werden in der SOC-Prognose jetzt als eigene Zeitspur dargestellt.
Beim Überfahren eines Slots zeigt die Karte, ob die Energie von Root oder
einem Aux-Speicher kommt, welches Mitglied geladen wird und wie viel Energie
die Endlast erhält. Die physische Kette bleibt dabei eine gemeinsame Spur und
erscheint nicht irreführend als mehrere unabhängige Überschusslasten.

Der Kaskaden-Automationsschalter trägt nun Phase, Hands-off und Fault direkt als
Attribute. Schaltet ein anderer Besitzer einen Shared Actor gegen einen
weiterhin benötigten Planzustand um, ist die automatische Deaktivierung dadurch
als kontrollierte Besitzübergabe sichtbar; ein exklusiver Fremdeingriff bleibt
weiterhin ein Hard-Fault. Ein normales Nullleistungs-Auto-Off wird dagegen ohne
Deaktivierung übernommen, sobald der frische Slotplan ebenfalls Safe-OFF
verlangt.

Ein unbelegter Kaskaden-Slot erzeugt außerdem keine Phantom-Root-Energie mehr.
Zuvor interpretierte ein negativer Python-Slice den Zustand „keine Last aktiv"
als Output-Overhead von B1, schaltete dessen Root-Steckdose unnötig ein und
konnte über deren externes Auto-Off anschließend Hands-off auslösen.
