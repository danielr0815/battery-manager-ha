# Abschluss der Framework-Piloten 1–3

Stand: 30.10.2026. Alle drei realen Aufgaben sind fachlich abgenommen.
Die Messungen belegen noch keine Gesamtkostenersparnis. Live-Nachbeobachtung
ist offen; dieser Abschluss enthält keine Installation oder Live-Schaltprüfung.

## Fachliches Ergebnis und Qualität

| Pilot | Ergebnis | Tatsächliche Rollen und Abnahme |
|---|---|---|
| 1 | `bm_control` OFF gibt die Geräteaktoren frei; veröffentlicht als 0.64.0 | Sol/Medium Umsetzung, unabhängiges Astra/High-Review mit geschlossenen Korrekturbefunden, Sol/Low Prüfungen und Veröffentlichung |
| 2 | Sieben-Tage-Untersuchung: Recorder-Übergröße und unklare SOC-Herkunft erkannt | Sol/Medium Analyse und unabhängige Prüfung, Luna/Low überprüfbare Extraktion; keine Produktänderung |
| 3 | Recorderfilter und zum Plan passende SOC-Herkunft/-Alter; veröffentlicht als 0.64.1 | Sol/Medium Umsetzung und getrennte unabhängige Abnahme ohne Befunde; Sol/Low Prüfungen und Veröffentlichung |

Pilot 1 erforderte Korrekturen an Terminalzustand, Aktionsgeneration und Queue.
Pilot 3 erforderte keine fachliche Reviewkorrektur; die Testausführung musste
wegen Sandbox-Beschränkungen angepasst werden. Qualitätsgates wurden nicht
abgesenkt. Core-Coverage jeweils 100 %, Gesamt-Coverage jeweils über 98 % und
jedes HA-Modul mindestens 95 %; statische und Frontend-Prüfungen bestanden.

## Messgrenzen und Vergleichskosten

Alle Zeiten sind UTC am 30.10.2026. Beträge sind hypothetische USD-Vergleichskosten
mit dem datierten offiziellen Standard/Short-Preissnapshot, keine Rechnung.
Cache ist eine Input-Teilmenge, Reasoning eine Output-Teilmenge.

| Pilot / Abschnitt | Messfenster | Vergleichskosten USD |
|---|---|---:|
| 1: Abnahme | 10:04:14.859702–10:39:03.195412 | 10,861898 |
| 1: kumulativ bis Precommit | 10:04:14.859702–12:02:26.348419 | 12,1684296 |
| 1: zusätzliche Veröffentlichung | 12:02:26.348419–12:17:39.085424 | 1,0888472 |
| 2: Abnahme | 13:28:00.963906–13:37:49.550559 | 1,34193924 |
| 3: Abnahme | 13:53:39.825343–14:12:34.579780 | 1,949832 |
| 3: kumulativ bis Precommit | 13:53:39.825343–15:09:12.125877 | 2,1887504 |
| 3: zusätzliche Veröffentlichung | 15:09:12.125877–15:24:32.439951 | 1,1820372 |

Abnahme und kumulativen Precommit-Stand niemals addieren. Die separaten
Veröffentlichungsfenster schließen weitere finale CI-Beobachtung und Abschlussarbeit
aus. Frühe Sichtungen außerhalb der Deklarationen und nicht erfasste Nacharbeiten
bleiben unbekannt. Die lokalen Ledger enthalten die vollständigen Tokenkategorien,
Request-Zähler und den Abgleich mit kumulativen Sitzungszählern; es wird keine
nachträgliche Aufteilung in geschätzte Arbeitsphasen vorgenommen.

## Endgültige Releaseprüfung

Die Angaben „am Messstichtag noch offen“ in früheren Berichten waren korrekt.
Inzwischen sind alle folgenden Läufe erfolgreich abgeschlossen:

| Release | Commit | Push / Vorbereitung / Tag / Release |
|---|---|---|
| 0.64.0 | `212d5b4adda2fef638ff37ddfc0eef4f014e755a` | 41052534323 / 41052653170 / 41054011769 / 41054014385 |
| 0.64.1 | `cd1f7f98b27b04106f3936f6d28339db405138e1` | 41078236259 / 41078254924 / 41080133209 / 41080136119 |

Belege: lokal archivierte `*-ci-status.json` unter den jeweiligen Pilotverzeichnissen
in `.codex-artifacts/pilots/`. Private Diagnosen und Sitzungsdaten werden nicht
veröffentlicht.

## Aussagegrenzen und Fortsetzung

Die drei Aufgaben unterscheiden sich erheblich in Risiko und Umfang. Ihre Kosten
dürfen nicht als Vorher-Nachher-Vergleich verwendet werden. Lokale Loggrößen sind
keine modellgelesene Textmenge und keine Tokenmessung.

Die neue Framework-Messung ergänzt Werkzeugaufrufe, eindeutig zugeordnete
Statusabfragen und im Modellkontext protokollierte Ausgabebytes. Historische Daten
werden nur bei eindeutiger Zuordnung nacherfasst; Messlücken bleiben sichtbar.
Ein Vergleich vollständiger Logs mit kompakten Ausgaben desselben erfolgreichen
Prüflaufs belegt ausschließlich die mögliche Ausgabereduktion.

Die einmalige Nacherfassung der ursprünglichen Abnahmefenster ergab folgende
beobachtete äußere Werkzeugaufrufe und protokollierte Textbytes:

| Abnahmefenster | Äußere Aufrufe | Warteaufrufe | Protokollierte UTF-8-Ausgabebytes |
|---|---:|---:|---:|
| Pilot 1 | 242 | 47 | 807.479 |
| Pilot 2 | 66 | 0 | 346.847 |
| Pilot 3 | 98 | 17 | 280.145 |

Diese Zahlen sind keine vollständige Rekonstruktion aller ausgeführten
Unterwerkzeuge. Verschachtelte Orchestrierung bleibt unzugeordnet; an den
Fenstergrenzen fehlen einzelne Antworten, und einige Ausgaben sind abgeschnitten.
Die Zuordnung zu erfolgreichen Prüfungen beziehungsweise wiederholten Logabrufen
ist historisch nicht ausreichend belegt. Die Zahl unveränderter Prozessabfragen
bleibt deshalb unbewertet, auch wenn eine beobachtete Teilmenge null ergibt.
Die versionierten privaten Berichte liegen unter
`.codex-artifacts/framework-metrics/historical/`; dort werden auch kumulative
Precommit- und separate Veröffentlichungsfenster einzeln geführt. Rohdialoge
bleiben außerhalb öffentlicher Artefakte.

Die Ziele „50 % weniger unveränderte Statusabfragen“ und „80 % weniger erfolgreiche
Prüfausgabe“ sowie die Gesamtkostenersparnis sind für die bisherigen drei Piloten
**nicht bewertbar**. Weitere drei reale Aufgaben werden vor Beginn deklariert;
keine Aufgabe wird eigens für einen Modellvergleich doppelt bearbeitet. Spätere
Fehler werden der ursprünglichen Aufgabe zugeordnet. Ohne vergleichbare Grundlage
folgt keine Modellherabstufung oder Einsparungsbehauptung.
