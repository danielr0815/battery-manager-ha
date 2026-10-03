# Dashboard: aktuelle Steuerung, Quellen und Bedienzustand

Die vorhandenen Karten und ihre Ressourcen-URL bleiben bestehen. Die neuen
Details verwenden additive Attribute des SOC-Prognosesensors. Bei älteren
Payloads bleiben fehlende Werte unbekannt; die Karte rekonstruiert keine
Gerätebestätigung aus einer Planempfehlung.

## Plan und Geräteantwort

Die Prognosekarte zeigt „Inverterkommando und Geräterückmeldung“ neben dem
bestehenden Reservebericht. Diese Größen haben unterschiedliche Bedeutung:

| Anzeige | Backendfeld | Bedeutung |
| --- | --- | --- |
| Inverterfreigabe im Plan | `reserve.inverter_limit_w` | Empfehlung des aufgenommenen Plans |
| Planeingaben aufgenommen / Plan aktiviert | `plan_metadata.captured_at` / `activated_at` | Aufnahme und Übernahme des Plans |
| Angefragtes Inverterlimit | `inverter_control.requested_limit_w` | Tatsächlich angefragter Aktorsollwert |
| Kommandozeit | `inverter_control.requested_at` | Zeitpunkt der Anfrage |
| Gemeldetes Gerätelimit | `inverter_control.observed_limit_w` | Physische Geräterückmeldung |
| Gerätepublikation | `inverter_control.observed_at` | Echte Publikationszeit des Aktors |
| Gerätebestätigung | `inverter_control.confirmed` | Bestätigung, ausstehend oder unbekannt |

Ein Planlimit von 0 W kann gleichzeitig mit einer bestätigten Livefreigabe
von 2.300 W auftreten. Die Karte zeigt beide. Die Freigabe ist eine Obergrenze,
keine gemessene Leistung. `live_ac.limit_w` wird nicht als tatsächliches
Gerätelimit ausgegeben. Ein Live-Update verändert die dargestellte Planzeit
nicht. Bestätigungsbedarf und bekannte Schutzgründe werden übersetzt.

Wenn der Prognosesensor `unknown` oder `unavailable` ist, kennzeichnen alle
Prognosekarten vorhandene Daten als letzten bekannten Plan. Ohne Daten steht
ein eigener Unverfügbarkeitshinweis. Die Inverterdetails heißen dann
„Letztes bekanntes Inverterkommando und Rückmeldung“.

Ab 0.54.1 behalten Prognose-, Verbrauchs-, Kaskaden- und Lastkarten ihren
zuletzt empfangenen Plan auch dann, wenn HA die Attribute bei Unverfügbarkeit
vollständig entfernt. Der Hinweis nennt zusätzlich die Aufnahmezeit. Das ist
ein Cache je Karteninstanz und Entität im Arbeitsspeicher. Entitätswechsel,
Entfernen der Entität und verfügbare leere Daten verwerfen ihn. Ein neues
Browserfenster ohne empfangenen Plan zeigt weiterhin keine erfundene Historie.
Ein frischer verfügbarer Plan ersetzt die alte Darstellung und entfernt den
Warnhinweis. Der Cache verändert weder HA-Zustand noch Steuerfreigaben.

## Quellen und Lernen

`source_health` ist eine Liste von Quellenrollen mit `entity_id`, `status`,
`value`, `unit`, `reported_at`, optionaler Preisabdeckung und Fallback.
Statuswerte unterscheiden unkonfigurierte, fehlende, unbekannte, nicht
verfügbare, ungültige und veraltete Quellen. Die Karte übernimmt die
rollenbezogenen Backendverträge; sie erfindet keine allgemeine Altersgrenze.
Konfigurierte Entities öffnen die vorhandenen HA-Details. Eine unkonfigurierte
optionale Quelle erzeugt keinen klickbaren `null`-Eintrag. Haushaltsgeräte
zeigen ihre eigenen Quellen entsprechend an.

Der Marktbericht nennt die ausgewählte Entity und zeitliche Abdeckung auch
bei Fallback. Die Lernansicht trennt `coverage_detail.window_occupancy` von
`measurement_coverage`, gültigen und berücksichtigbaren Stunden sowie der
Reife der Stunden-Bins. `learned_duration_fraction` bezeichnet den Anteil
gelernter Prognosedauer. Fehlende Metadaten ergeben keine Ausfallquote.

Leere Tagesdaten blenden Aufzeichnungsfehler nicht aus. Kennzahlen mit
ausdrücklich null Stunden Messabdeckung erscheinen als unbekannt, nicht als
gemessene Nullenergie. Die vorhandenen Aufbewahrungshinweise bleiben sichtbar.

## Bedienung und Aktualisierung

- Diagrammfokus und gewählte Prognosezeit bleiben bei HA-Publikationen und
  Größenänderungen erhalten. Entfallene Zeitpunkte werden ausdrücklich
  gemeldet. Zeitraumknöpfe folgen der Lastidentität bei neu sortierten Lasten.
- Verbrauchs-Hover wählt das enthaltene Zeitintervall. 10:45 gehört zum
  Slot 10–11 Uhr; eine echte Lücke liefert keinen benachbarten Messwert.
- Ein Touch-Tap zeigt dieselben Details. Vertikales Scrollen bleibt erlaubt;
  Escape hebt eine Auswahl auf. Beide wiederholten DST-Stunden tragen im
  Readout unterscheidbare UTC-Offsets.
- Sprach- und HA-Zeitzonenwechsel aktualisieren die Darstellung auch bei
  unverändertem Sensorobjekt.
- Bei Speicherlasten mit unbekanntem SOC bleibt eine Buchung vorläufig:
  Aufwecken und gültige Telemetrie sind vor Ausführung erforderlich.
- Die Gerätekarte verwirft Antworten anhand einer eigenen monotonen
  Publikationsgeneration. A→B→A darf keine erste A-Antwort veröffentlichen;
  ein legitimer Backendrevisionreset bleibt erlaubt.

## Prüfung

`tests/frontend/reports.test.mjs`, `appliances-card.test.mjs` und
`tests/browser/review-regressions.spec.mjs` prüfen diese Verträge. Die
Browsertests verwenden lokale synthetische Daten und echte Chromium-
Interaktionen. Sie verändern keine Live-Anlage.
