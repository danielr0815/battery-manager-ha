# Betriebsaufzeichnung und Tagesvergleich

Implementiert für 0.40.0, 2026-09-08. Cluster aus Optimierungsplan 8/9/10.

## Verbindlicher Umfang

1. Die Integration zeichnet erfolgreiche Planstände mit effektiver Kernkonfiguration,
   Eingaben, Ergebnis und Integrationsversion auf. Eine fortlaufende Ereignisnummer
   ordnet Planwechsel, Messungen, Schaltanforderungen, Service-Ergebnisse und
   Kaskaden-Recovery zu. Eine Service-Antwort ist keine physische Bestätigung.
2. Messungen werden bei Zustandsänderungen und erfolgreichen Aktualisierungen
   gesammelt. Es werden nur konfigurierte Betriebsentitäten gelesen; gespeichert
   werden Zustand, Einheit, Publikationszeit und Context-ID, keine beliebigen Attribute.
3. Jeder Messabschnitt gehört zum vorher gültigen Plan. Prognose und Messung
   werden über dasselbe reale Zeitintervall verglichen. Ein neuer Plan ersetzt
   keine Vergangenheit. Laufzeiten sind bestätigte Aktorzeiten, Energie benötigt
   eine Leistungsmessung. Fehlende, nichtnumerische, veraltete oder falsch
   dimensionierte Werte bleiben unbekannt, niemals implizit null.
4. Leistungsintegration verwendet den zuletzt beobachteten Wert für höchstens
   300 Sekunden und nur innerhalb seiner Frischegrenze; ein noch gültiger
   Teilabschnitt bleibt erhalten. SOC-Extrema berücksichtigen den neuesten
   Messpunkt sofort. Neustarts beginnen
   einen neuen Messabschnitt; HA-Ausfallzeit wird nicht hochgerechnet. Tage
   werden in der HA-Zeitzone getrennt, einschließlich Sommerzeitwechsel.
5. Tagesberichte enthalten Messabdeckung, beobachtete/geplante Energie,
   Laufzeit- und Leistungsabweichung je Last, SOC-Minimum/-Maximum der Messpunkte,
   Schaltanforderungen, tatsächliche Zustandswechsel und fehlgeschlagene Services.
   PV-/Verbrauchsabweichungen sind Beobachtungen, keine behauptete Root Cause.
6. Archiv und Berichte bleiben lokal. Ereignisse und komprimierte Planstände
   werden höchstens sieben Tage, 50.000 Ereignisse und 32 MiB aufbewahrt; das
   zuerst erreichte Limit gilt. Tagesberichte bleiben höchstens 30 Tage.
   Verdrängte Daten und Messlücken werden ausdrücklich ausgewiesen. Persistenz
   erfolgt verzögert und beim Entladen. Fehler der Diagnose dürfen die Steuerung
   nicht aussetzen. Kein automatischer Upload und keine Toleranzänderung.
7. Der HA-Diagnoseexport enthält das Archiv; die Prognosekarte zeigt eine
   kompakte Tagesübersicht. Zusätzliche optionale Leistungssensoren für PV,
   Wohnungsverbrauch (ohne BM-Zusatzlasten), Netzbezug und Netzeinspeisung
   ermöglichen vollständige Vergleiche. Ohne passende Messquelle bleibt die
   entsprechende Spalte offen. Lastleistung und SOC werden aus bestehenden
   Entitätszuordnungen übernommen. Kaskaden-Eingangsmessungen erhalten eine
   eigene Bilanz aus nachgelagerter Ladung, Endlastversorgung und Ausgangsverlusten;
   sie werden nicht als reine Akkuladung ausgewiesen. SOC-Extrema gelten je Speicher.
   Tagesvergleich und Diagnosearchive sind Betriebsdaten, keine Nutzenergienachweise
   bei einem gesättigten Entfeuchtertank.
8. Ein lokales CLI rekonstruiert Tagesberichte und führt sämtliche erhaltenen
   Planner-Aufrufe erneut aus; ein Vergleich zweier Archive stellt Energie,
   Abdeckung und Schaltzahlen gegenüber. Ein separater virtueller Tagesablauf
   koppelt echten Executor, simulierte Gerätezustände, Leistungsrückmeldung,
   Neuplanung, Neustart und Ausfall. Ein aufgezeichneter realer Messverlauf ist
   kein physikalischer Gegenbeweis für eine andere Schaltstrategie.

## Abnahme

Energie-/Zeitzuordnung bei Replan und Mitternacht, Messlücken/Frische,
fehlende oder ungültige Einheiten, unveränderte Telemetrie, Neustart,
begrenzte Aufbewahrung, beschädigter Store, Servicefehler, Recovery,
Export-/Replay-Identität und Tagesablauf mit Rückkopplung werden getestet.
Produktive Wartezeiten werden ausschließlich virtuell gesteuert.


Die Tagesregression verwendet ein vereinfachtes, ideales AC-Bus-/Speichermodell
mit fünf Minuten pro Energiebilanz und zusätzlichen virtuellen Leistungsbestätigungen.
Gemessene PV und Hauslast ändern den simulierten Haus-SOC und damit den nächsten
Planner-Aufruf. Netzbezug/-export und Endlastenergie werden unabhängig aus den
bestätigten Gerätezuständen bilanziert. Das ist ein Integrationsnachweis der
Rückkopplung, keine Kalibrierung realer Geräteverluste.
