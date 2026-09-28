# DC-Vorrang und AC-Lastpriorität ab 0.49.0

Betreiberauftrag vom 28.09.2026: Wenn möglich vollständig auf zusätzliche
AC-Entnahme verzichten, solange Energie für DC benötigt wird. Unvermeidliche
AC-Vorbereitung bevorzugt bei höherem nutzbarem AC-Verbrauch statt schwacher
Nachtlast. Das präzisiert [SOC-Erhaltung](F-RESERVE-SOC-PRESERVATION.md) und
ersetzt die rein späteste Auswahl der AC-Gelegenheit.

## Verbindliche Reihenfolge

1. **Versorgung schützen:** Bestehende technische SOC-Grenzen, Quellenfreigaben,
   manuelle Wünsche und geordnete Übergaben gelten unverändert. Gleichzeitig
   aktive Netzteilversorgung und Inverter-Entladung bleiben ausgeschlossen.
2. **DC-Energie zuerst reservieren:** Ein rückwärts berechneter Mindestvorrat
   deckt den erwarteten DC-Verbrauch unter der jeweils simulierten PV-Prognose.
   Wirkungsgrade, Ladeleistung, Eigenbedarf und vorhandene Schutzschwellen werden
   berücksichtigt. Eine optionale AC-Abgabe darf diesen Vorrat nicht unterschreiten.
   Reicht selbst die volle Batterie nicht, bleibt Unterstützung zulässig;
   der Vorrat erfindet keine zusätzliche Kapazität.
3. **Nur nötige AC-Vorbereitung:** Die obere PV-Prognose begründet Speicherplatz,
   garantiert aber keine Energie für die zukünftige DC-Versorgung. Wenn DC allein
   den Speicherplatz schafft, bleibt AC aus. Die DC-Obergrenze übernimmt keinen
   SOC-Zielpfad aus einer Referenz mit maximaler AC-Entladung.
4. **Hohe nutzbare AC-Last bevorzugen:** Verglichen wird die vom Inverter nutzbare
   AC-Restleistung nach PV, bis zur Invertergrenze. Ein hoher, bereits durch PV
   gedeckter Hausverbrauch ist keine Batterie-Gelegenheit. Teilstunden werden in
   W verglichen. Bei gleicher Leistung wird die spätere Gelegenheit genutzt.
5. **Rechtzeitig vor PV:** Eine stärkere Last nach dem betreffenden Überschuss
   kann vorher fehlenden Speicherraum nicht schaffen. Niedrige Nachtlast darf
   deshalb weiter genutzt werden, wenn die rechtzeitigen besseren Gelegenheiten
   nicht ausreichen. Es gibt weder eine starre Nacht-Sperrzeit noch einen neuen
   festen Nacht-SOC. Das bindende Fenster bleibt heute/morgen in HA-Ortszeit.

Der Mindestvorrat ist eine Prognoseentscheidung, keine Zusage für ausgefallene
PV oder zusätzlichen, noch unbekannten Verbrauch. Eine neue Prognose bzw. ein
neuer lokaler Kalendertag kann Quellenwahl und AC-Freigabe ändern. Nominale und
Stressläufe verwenden jeweils ihre eigene PV für den DC-Vorrat; für den oberen
Speicherbedarf bleiben P90 bzw. der konfigurierte obere Ersatzfaktor maßgeblich.

## Ursache des Livefehlers

Im Plan vom 28.09.2026, 23:08 MESZ, waren am Dienstag 15–18 Uhr beide Netzteile
bei etwa 90 % SOC aktiv. Anschließend lief der Inverter bis Mittwochmorgen.
Die bisherige DC-Grenze wurde auf einen Referenzpfad mit maximaler AC-Abgabe
angehoben. Dadurch wurde DC-Energie kurzzeitig mit Netzstrom erhalten, die der
Inverter später zusätzlich verbrauchte. Die Anzeige gab den Plan korrekt wieder.

Die DC-Grenze verwendet nun ausschließlich die physische Rückwärtsrechnung.
Auch AC-Gelegenheiten werden nicht mehr auf den Energiepfad der maximalen
Referenzentladung festgelegt. Die Referenz bestimmt weiterhin den unter dem
DC-Vorrang erreichbaren Export; sie wird nicht zum zeitlichen SOC-Ziel.

## Umsetzung und Nachweise

`core/reserve_schedule.py` trennt DC-Mindestvorrat, DC-Obergrenze und die nach
AC-Leistung priorisierten Vorbereitungsgrenzen. Pro unterschiedlicher Leistung
wird ein begrenzter Rückwärtslauf ausgeführt; Präfixe vor dem ersten Auftreten
werden nicht erneut berechnet. Der bestehende reine Simulator setzt diese
Budgets um. Keine neue Laufzeitabhängigkeit, kein externer Solver und keine
zusätzliche Home-Assistant-I/O im Planner.

`tests/core/test_reserve_priority.py` enthält unabhängige Energiefälle:

- Bei 150 Wh nötiger AC-Entnahme erhält ein 600-W-Fenster Vorrang vor 100 W,
  unabhängig von seiner Reihenfolge; bei 300/300 W gewinnt das spätere Fenster.
- Kurze starke Fenster werden nicht wegen ihrer kleineren Slotenergie übergangen.
- Eine frühere AC-Entnahme darf keine zusätzliche spätere DC-Netzversorgung
  verursachen; nominal fehlende PV wird nicht durch die obere Prognose ersetzt.
- Ausreichender DC-Verbrauch lässt AC vollständig aus; hoher PV-gedeckter
  AC-Verbrauch erzeugt keine künstliche Entladegelegenheit.
- Eine Last erst nach dem PV-Peak kann nötige frühere Entladung nicht ersetzen.
- Die anonymisierte Fixture `reserve_load_priority.json` reproduziert die
  unnötige Nachmittags-Netzteilphase und prüft deren Entfall, Schutzgrenzen,
  Export und die bevorzugte höhere Abendlast.

Die bestehenden 16 Golden-Szenarien wurden neu erzeugt und bleiben bitidentisch.
Insbesondere kauft dort kein Szenario zusätzlichen Netzbezug. Die neue
Prioritätsregression scheitert am alten Code in drei unabhängigen Fällen
(hohe frühere Last, Teilstunden, spätere DC-Netzversorgung).

## Vergleich am vollständigen Live-Snapshot

Der vollständige Planner einschließlich optionaler Lastallokation benötigt im
untersuchten 49-Slot-Snapshot rund **1350,73 Wh** Netzbezug statt **1466,98 Wh**.
Export sinkt von **8,26 Wh** auf **0,45 Wh**. Die unnötige Netzteilphase am
Dienstagnachmittag entfällt, der End-SOC bleibt bei rund **89,23 %**.
Die Lastallokation plant dabei **249,50 Wh** optionale Lastenergie statt
**374,25 Wh**; die Importdifferenz ist deshalb kein isolierter Effizienznachweis
für unveränderte Verbraucher. Die Fixture prüft zusätzlich dieselben fest
gebuchten AC-Lasten ohne erneute Allokation. Diese Werte gelten für den
gespeicherten Forecast, nicht als Zusage für aktualisierte Liveprognosen.

Ein unprofilierter Lauf desselben Snapshots benötigte vor der Änderung
16,04 s und danach 16,77 s auf dieser Entwicklungsmaschine. Diese Einzelwerte
sind eine Laufzeitkontrolle, kein HA-Hardwareversprechen oder CI-Zeitlimit.
