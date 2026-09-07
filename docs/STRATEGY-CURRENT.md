# Verbindliche Strategie und Zielhierarchie

Stand: 2026-09-07, Arbeitsstand 0.38.0. Diese Vorrangregeln ersetzen bei
Widersprüchen historische Zielfunktionsbeschreibungen in STRATEGY.md.
Die bestehenden technischen Schutzverträge bleiben gültig.

1. **Physische Ausführbarkeit und Schutz.** Verfügbare Quellen, bestätigte
   Schaltzustände, Leistungsgrenzen, Mindestlaufzeiten, Mindestpausen und
   SOC-Abschaltgrenzen sind keine Optimierungsbudgets. Eine Quellenumschaltung
   darf keinen parallelen Root-/Aux-Pfad erzeugen.
2. **Kein zusätzlicher Netzbezug für Überschusslasten.** Der vollständige Plan
   wird gegen dieselbe Basis ohne Lasten geprüft. 50 Wh Import-Artefakt-Slack
   gelten für den gesamten Horizont, nicht je Kandidat. Einzelne gebuchte
   Abschnitte müssen zusätzlich versorgbar bleiben. Kaskadenverluste gehören
   zum Bedarf; Ladeenergie und nutzbare Endlastenergie sind getrennte Größen.
3. **Hausbatterie-Tagesziel und Reserve.** Ein ohne Lasten erreichbares Maximum
   bleibt geschützt. Kontinuierlicher Pass-3-Vorlauf darf am eigenen Tag bis
   einen Prozentpunkt unter dem Maximum enden. Das ist eine absolute Grenze
   pro Tag, keine wiederholt verbrauchbare Gutschrift. Andere Buchungen dürfen
   diesen bereits akzeptierten Tageswert nicht weiter unterschreiten.
   Die SOC-Reserve und der Wechselrichter-Cutoff bleiben unabhängig davon.
4. **Nutzlasten vor steuerbarer Einspeisung.** Normale Lasten folgen ihrer
   konfigurierten Priorität. In einer Kaskade steht die Endlast vor Recovery
   aller Mitglieder, danach folgt Top-up. Zuerst werden der vollständige
   Direktlauf und der vorgezogene Dauerlauf berechnet. Bestehende Tagesläufe
   werden als ein zusammenhängender Block geprüft; schließbare Lücken haben
   Vorrang vor Mitgliedsladung. Dafür gilt einmalig je Last und Tag eine feste
   Energieabweichung von höchstens 50 Wh bei unveränderten Schutzprüfungen.
   Mitgliedsladung ist nur in vollständig von der Endlast belegten Slots
   zulässig (außer die Endlast ist nicht verfügbar). Sie bleibt durch direkten
   Überschuss und zeitlich vorhandenen Speicherplatz begrenzt.
   Zusätzliche Entladung unter das normale Mitgliedsziel mit anschließendem
   Laden setzt mehr als 50 Wh Restexport und eine lückenlos geplante Endlast
   in allen PV-Slots des Tages voraus. Der bisherige Nachweis vollständiger
   Rückladung am selben Tag bleibt erforderlich. Auch eine Aux-Neuplanung darf
   bereits akzeptierten direkten Endlastbetrieb nicht verkürzen. Normale Nutzung bereits
   gespeicherter Energie oberhalb des Entladeziels benötigt keine Ladezusage.
5. **Vorzeitige Einspeisung als letzter Schritt.** Automatisch nur bei aktivem
   Funktions- und Laufzeitschalter und wenn alle kontinuierlichen Lasten vom
   Exportbeginn bis zum tatsächlich erreichten Maximum lückenlos eingeplant
   sind. Auch eine Verschiebung des Maximums durch Export wird erneut geprüft.
   Ein nur tolerierter Peak unter Maximum genügt dafür nicht. Ohne vollständige
   Laufzeitdaten gibt es keine Freigabe. Natürlich anfallender Überschuss bei
   voller Batterie bleibt physisch möglich. Ein fremder manueller Sollwert ist
   ein exogener Eingang für heute und wird nicht automatisch überschrieben.

## Toleranzen haben unterschiedliche Aufgaben

| Größe | Grenze | Zweck |
| --- | --- | --- |
| Pass-3-Peak | 1 Prozentpunkt der Kapazität pro Tag | Kleine Prognose-/Rasterabweichung akzeptieren |
| Striktes Maximum | 0,1 Prozentpunkt | Numerische Erkennung des gefüllten Speichers |
| Speicher-Zielabweichung | max. 50 Wh oder 2 % der Mitgliedskapazität | Kleine bestehende Unterschreitung des Recovery-Ziels löst keine Nachladung aus |
| Neue Speicherladung | Mindestens 15 min (längere Konfiguration gilt) und 50 Wh | Keine isolierten Mikro-Ladungen, auch nicht am Horizontende |
| Neuer Aux-Quellenlauf | Mindestens 15 min je ununterbrochenem Quellenlauf | Kein kurzer Quellenwechsel; laufende Quelle darf Restlauf beenden |
| Laufblock-Lückenschluss | 50 Wh je Last und Tag | Ein gemeinsamer Versuch statt Toleranz je Lücke |
| Import-Slack | 50 Wh pro Horizont | Bestehende Standby-/Simulationsartefakte |
| Last-Batterieanteil | Konfiguration je Last | Zulässiger Anteil batterieversorgter Nutzenergie |
| SOC-Puffer | Konfiguration bzw. gelernt | Reserve gegen Verbrauchsunsicherheit |

Ein Prozentpunkt entspricht 20/50/100 Wh bei 2/5/10 kWh Kapazität. Keine dieser
Größen darf als Ersatz für eine andere oder zur Finanzierung echten Netzbezugs
verwendet werden. Eine automatische Kalibrierung setzt gemessene Tagesverläufe
voraus; synthetische Tests allein begründen keine Änderung der Grenzwerte.

## Prüfbare Konflikte

- `tests/core/test_feedin_load_priority.py`: Lücke vor Maximum sperrt Export;
  Peak-Toleranz bleibt absolut; Pause sperrt automatische Buchungen aller Tage.
- `tests/core/test_cascade.py`: Endlast/Recovery/Top-up, zeitkausale Aux-Energie,
  Pfadgrenzen sowie Root-Verluste gegen Hausbilanz.
- `tests/core/test_optimize.py`: Import-/Cutoff-/Tageszielschutz.
- `tests/ha/test_feedin.py`: sofortiges Nullsetzen, Neuplanung und Wiederanlauf.

Ein Sicherheitsveto darf Restexport hinterlassen. Die Erklärung muss den
konkreten begrenzenden Faktor nennen; ein besserer Energie-Endwert allein
beweist noch keinen ausführbaren Plan.
