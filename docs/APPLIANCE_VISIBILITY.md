# Haushaltsgeräte: Zustände, Lernprofile und Startempfehlungen

Aktueller Vertrag ab **0.47.0**. Die Integration beobachtet Haushaltsgeräte und
berücksichtigt ihre Verbräuche. Sie startet oder beendet keine Programme.

## Einrichtung und Geräteansicht

Unter **Einstellungen → Geräte & Dienste → Battery Manager** ein Haushaltsgerät
anlegen oder anpassen. Der Erkennungssensor meldet den Betriebszustand oder eine
Leistung mit konfigurierten Ein-/Ausschaltschwellen. Separat können Leistung,
Energiezähler, aktives Programm, Programmauswahl, Gesamt- und Restlaufzeit
zugeordnet werden. Zeitangaben alleine beweisen keinen laufenden Durchgang.

Jedes Haushaltsgerät besitzt acht Sensoren, auch wenn **opportunistischer Start**
ausgeschaltet ist:

| Sensor | Bedeutung |
|---|---|
| Betriebszustand | Inaktiv, läuft, pausiert, beendet, Fehler; unbekannte Erkennung bleibt unbekannt |
| Programm | Aktives Programm oder ausdrücklich gekennzeichnete Auswahlvorschau |
| Restlaufzeit | Gemeldete Zeit; andernfalls Schätzung aus Gesamtdauer und beobachtetem Beginn |
| Voraussichtliches Ende | Zeitpunkt aus der Restlaufzeit |
| Verbrauch im laufenden Durchgang | Gemessene Zählerdifferenz oder integrierte Leistung; Vollständigkeit als Attribut |
| Angenommener Gesamtverbrauch | Energieansatz für die Planung samt Herkunft |
| Angenommene Gesamtdauer | Daueransatz für die Planung samt Herkunft |
| Lernstatus | Noch keine Daten, Messung läuft, Lernwerte vorhanden oder Messung nicht verwertbar |

Die Sensoren veröffentlichen keine erfundenen Nullwerte. Fehlende Messwerte
bleiben unbekannt. Bekannte Konfigurations- und Lernwerte bleiben auch bei
fehlender Prognose, während der ersten Planung und bei Planungsfehlern sichtbar.
Die Betriebszustand-Entity enthält zusätzlich die aktuelle Startempfehlung mit
Begründung und eine Revision für die Karte. Umfangreiche Historien stehen nicht
in häufig aufgezeichneten Entity-Attributen.

## Haushaltsgerätekarte

Im Dashboard unter **Karte hinzufügen → Battery Manager Haushaltsgeräte** die
Integration und bei Bedarf einzelne Geräte wählen. Eine leere Geräteauswahl
zeigt alle Geräte dieser Integration. Manuell:

```yaml
type: custom:battery-manager-appliances-card
entry_id: DEINE_CONFIG_ENTRY_ID
# appliance_ids: [SUBENTRY_ID]
```

Die Karte zeigt alle Geräte, auch inaktive, mit Status, Programm, Restzeit,
voraussichtlichem Ende und Verbrauch. Aufklappbare Bereiche erklären die
Planungsgrundlage, gelernte Programme, die letzten Läufe und die Datenquellen.
Die Quellen öffnen die zugehörige HA-Entity. Alle Zeiten verwenden die
HA-Zeitzone; die Sprache folgt HA (Deutsch/Englisch, Englisch als Rückfall).
Die Karte wird über das bestehende Bundle ausgeliefert; keine zusätzliche
Frontend-Ressource muss installiert werden.

## Herkunft der Werte

Für die Energie gilt unverändert: passendes Programmprofil, sonst geräteweiter
Energiemedian, sonst konfigurierte Energie. Für die Dauer gilt eine gültige
Gesamtdauer des Geräts, sonst passendes Programmprofil, sonst konfigurierte
Dauer. Bei einer Programmauswahl im Stillstand wird die eventuell noch gemeldete
Dauer des vorherigen Programms nicht als Dauer der Vorschau verwendet.

Gemeldete Restzeit hat Vorrang vor einer Schätzung. Verbleibende Energie im
Planner bleibt eine anteilige Schätzung des vollständigen Programms; sie ist
nicht der gemessene Verbrauch des bisherigen Durchgangs.

## Lernen und Historie

Programmprofile zeigen Energie- und Dauermedian, Anzahl der verwendeten Proben,
beobachtete Minima/Maxima und den letzten bekannten Lernzeitpunkt. Die Bereiche
sind **beobachtete Spannen, keine Prognoseintervalle**. Eine einzige Probe wird
als Einzelmessung gekennzeichnet. Geräteweite Altprofile enthalten nur Energie;
eine gelernte Dauer wird dafür nicht behauptet.

Es bleiben höchstens 20 Proben je Profil und 32 Programme erhalten. Zusätzlich
werden die letzten 20 angenommenen oder verworfenen Beobachtungen je Gerät
angezeigt: Programm, beobachteter Beginn/Ende, Dauer, Energie, Messmethode,
Vollständigkeit und Entscheidung. Gründe sind beispielsweise fehlender Start,
Messlücke, unbekannte Erkennung, Programmwechsel, Abbruch oder Neustart.
Ein Zählerrücksprung kann durch eine vollständige Leistungsmessung ersetzt
werden; die Anzeige erklärt dann sowohl den Rückfall als auch die Messmethode.

Vollständige Messungen beginnen erst nach beobachtetem Stillstand. Messlücken
über zehn Minuten oder unvollständige Erkennung können keine vollständige
Lernprobe erzeugen. Ein Neustart verwirft die aktive Lernmessung; eine begrenzte
Metadatenmarkierung erklärt die Unterbrechung, ohne fehlende Energie nachzulernen.
Bestehende Lernproben bleiben erhalten. Ihre fehlenden Zeitstempel werden nicht
erfunden; die neue Laufhistorie beginnt mit diesem Update. Leistungskurven und
rückwirkende Rekonstruktion aus Recorder-Daten sind nicht Bestandteil dieser
Funktion. Wenn eine Geräteintegration den finalen Energiezähler erst nach dem
Endstatus meldet, kann dieser Nachlauf nicht mehr dem bereits abgeschlossenen
Durchgang zugeordnet werden. Angezeigt und gelernt wird der beim Abschluss
verfügbare Messstand; die Erweiterung rekonstruiert keine verspäteten Messungen.

## Startempfehlung

**„Start laut Prognose möglich“** bedeutet, dass die bestehende Bewertung den
vollständigen Durchgang innerhalb des Horizonts ohne zusätzlichen Netzbezug und
unter den bestehenden SOC-Bedingungen zulässt. Sie ist keine Garantie vollständiger
Netzunabhängigkeit. Gründe erläutern deaktivierte Beratung, fehlenden gültigen
Plan, zu kurzen Horizont, zusätzlichen Netzbezug, SOC-Bedingungen und Schutzsperren.
Bei verändertem Programm oder veränderter Planungsgrundlage gilt eine alte
Empfehlung nicht weiter. Schutzsperren haben Vorrang.

## Technischer Vertrag

`ApplianceRuntime` veröffentlicht defensive Snapshots. Ein synchroner Pfad im
HA-Eventloop beobachtet Quellenereignisse und aktive Läufe im Minutentakt. Das
Lesen von Entities, Karten und Diagnosen verändert weder Messzyklen noch Lernen
und startet keine Optimierung. Die wirtschaftliche Planung bleibt unabhängig.

Der authentifizierte, lesende WebSocket-Befehl `battery_manager/appliances`
liefert ohne `entry_id` die geladenen Einträge (`entry_id`, `title`); mit
`entry_id` liefert er `entry_id`, `revision`, `appliances`. Optional filtert
`appliance_ids` auf die angegebenen Subentries. Fremde IDs liefern `not_found`,
fehlende/entladene Einträge `not_loaded`. Jeder Gerätesnapshot enthält
`observation`, `planning`, `learning`, `recommendation` und `sources`.
Die Karte bündelt Aktualisierungen und verwirft überholte Antworten.

Der Laufzeitspeicher behält die bisherigen Energie- und Programmproben bei.
`appliance_metadata` ist ein separater Block mit Schema 1 für Historie,
Lernzeitpunkte und Unterbrechungsmarkierungen. Beschädigte Zusatzdaten werden
verworfen, ohne gültige Profile zu löschen. Die Diagnose exportiert dieselben
Gerätesnapshots wie die Karte. Der Kern liefert additive, unveränderliche
`appliance_advisories`; alte Planneraufzeichnungen bleiben lesbar.

## Abnahme

Kern-Goldens und bisherige Energieentscheidungen bleiben unverändert. Die
zusätzlichen Empfehlungsgründe werden im vorhandenen Trial ermittelt; es gibt
keine weitere Simulation. Tests decken vollständige/unvollständige Messungen,
Persistenzmigration, blockierte/fehlgeschlagene Planung, Quellenereignisse,
authentifizierte Abfragen, Gerätezuordnung und Abbau der Listener ab. Die
Browserprüfung kontrolliert leere Profile, Datenfehler, Zeitzone, Sommerzeit,
Tastaturbedienung sowie Fokus- und Scroll-Erhalt.

Die Regressionen stehen in `tests/core/test_appliance_advisories.py`,
`tests/ha/test_appliance_history.py`, `tests/ha/test_appliance_views.py`,
`tests/frontend/appliances-card.test.mjs` und `tests/browser/appliances.spec.mjs`.
Der vorhandene Startfenster-Sensor veröffentlicht dieselbe Empfehlung wie die
Karte; Programmänderungen und Schutzsperren werden ohne weiteren Planerlauf
sichtbar. Häufige Messereignisse verschieben die Speicherung nicht unbegrenzt;
aktive Zwischenstände werden höchstens minütlich zum Speichern angemeldet,
Zykluswechsel unmittelbar. Listener werden auch nach fehlgeschlagenem Setup
entfernt.

Abnahme des Arbeitsstands 0.47.0: **2.154 Python-Tests**, **55 Frontendtests** und
**13 Chromium-Browsertests** erfolgreich. Gesamt-Coverage **97,73 %**, Kern
**100 %**, alle 49 Modul-Gates bestanden. Ruff, Format, mypy, Bundle-Abgleich
und Versionsprüfung sind grün; Golden-Dateien wurden nicht geändert.
