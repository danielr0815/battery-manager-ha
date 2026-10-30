# Hausversorgung, Rückladepflichten und Speicherziele

Vertrag ab 0.63.0, Betreiberentscheidungen vom 27.10.2026.

**Implementiert: lokale Qualitätsgates und unabhängiges fachliches Review bestanden.**
Die im historischen Vergleich gefundene Suchrasterregression ist ebenfalls
korrigiert und abgenommen. Die spätere Live-Einführung wird getrennt behandelt.
[Abnahme und Messnachweise](investigations/2026-10-27-implementation-handoff.md).

## Zielhierarchie

Physischer Schutz, manuelle Quellen und bestätigte Verpflichtungen bleiben
vorrangig. Zusätzliche rückladegesicherte Hausversorgung bedient zuerst die
24-V-Verbraucher über DCDC, danach AC. Anschließend folgen normale optionale
Nutzlasten und Speicher-Recovery in konfigurierter Reihenfolge, dann Speicher-
Top-ups und schließlich steuerbare Einspeisung. Hausversorgung darf deshalb
erreichbare Speicher-Mindestziele verdrängen; dies ist gesondert zu erklären.

## Zusätzliche Entnahme

Die konservative Referenz enthält Haus-/DC-Verbrauch, laufende Appliances,
manuelle Vorgaben, bestätigte Restlaufzeiten und Quellenphasen. Zusätzliche
Freigaben sind konkrete geprüfte Blöcke, kein mehrfach nutzbares Wh-Guthaben.
Solare Anker stammen aus der eingefrorenen Referenz: Ende des letzten netto
solarladenden Schritts jedes lokalen Tages. Netzteilladung erzeugt keinen Anker.
Eine erste kleine PV-Ladung beendet die Rückladepflicht nicht automatisch.

Am frühesten gemeinsam bestandenen Anker müssen nominaler und pessimistischer
Kandidat mindestens die Energie ihrer jeweiligen Referenz erreichen; auch die
Horizont-Endenergie bleibt erhalten. Gesamtimport/-export dürfen nicht steigen,
zeitliche DC-Versorgung und Schutzgrenzen nicht schlechter werden. Quellenphasen,
Leistung, Wirkungsgrade und Fristen gelten unverändert. Zusätzliche Netzteilladung
und eine PSU-/Inverter-Rückspeiseschleife finanzieren keine Rückzahlung.

Die bisherige Marktumverteilung bleibt innerhalb ihrer ursprünglichen festen
Fenster. Nur zertifizierte zusätzliche Entnahme erhält eine längere Frist.
Nachfolgende Lasten und Marktänderungen müssen alle Rückladepflichten erhalten;
neu finanzierter DCDC-Dienst darf nicht zugunsten von AC entfallen.
Unvollständige Suche beweist keine Unmöglichkeit. Zusätzliche Freigaben und finale
Marktprüfung teilen höchstens 32 zusätzliche vollständige nominale/Stressläufe;
höchstens vier Kandidaten einschließlich Referenz bleiben gehalten.

Bei aktiver positiver manueller Einspeisung werden konservativ keine neuen
Rückladekredite vergeben. Der bisherige Hausversorgungsplan und die manuelle
Vorgabe bleiben wirksam; deren Energie darf keinen nur scheinbaren solaren
Rückladenachweis finanzieren. Ein manueller Sollwert von 0 W oder deaktivierte
Einspeisung sperrt zusätzliche Hausversorgung nicht pauschal. Die Diagnose zeigt
diese Grenze ausdrücklich an.

## Unsicherheit

AC-/DC-Verbrauchsabweichungen werden zeitbezogen bis zum festen Anker erfasst
und mit den jeweiligen Verlusten in gespeicherte Wh umgerechnet. P50-Verbrauch
plus Unsicherheitsvorhalt zählt denselben P80−P50-Anteil genau einmal; ein
zusätzliches P80-Verbrauchsszenario wäre eine Doppelzählung. Unabhängige
Mindestreserven bleiben erhalten. Fehlende Abdeckung erzeugt keine neue
Freigabe. Ein einzelner Tages-PV-Wert ersetzt keine Stundenkurve und keinen
Unsicherheitsnachweis. Die Summe stündlicher Quantilabweichungen ist kein
statistisch belegtes Quantil des gesamten Fensters.

Die Prüfung laufender Appliances bestätigt den bestehenden Trennvertrag:
`history_profile._subtractions` entfernt statistisch gemessenen
Appliance-Verbrauch aus dem Hausprofil; reine Statusmeldungen führen zum
Ausschluss betroffener Lernstunden. `_get_appliance_runs` und
`series._apply_appliance_runs` ergänzen den verbleibenden bekannten Lauf einmal
in den Referenzslots. Bereits enthaltene Läufe werden nicht nochmals als
optionale neue Starts gebucht. Bestehende Nachweise sind
`test_appliance_exclusion_and_coverage_rule`, die Recorder-Bilanztests und
`test_runtime_release_keeps_appliance_profile_at_original_time`. Fehlende
Messstatistik bleibt gemäß dem bisherigen Lernvertrag behandelt; diese
Prüfung begründet keine pauschale Absenkung der Verbrauchsreserve.

## Speicher

`recovery_soc_percent` ist ein optionales bevorzugtes Mindestladeziel jedes
energielimitierten Speichers und bleibt bei Kaskaden zusätzlich Entladeziel.
Gespeicherte Werte werden aktiv, fehlende Altwerte bleiben neutral. Das obere
Ladeziel bleibt `target_soc_percent`; Kaskaden benötigen ein gültiges Entladeziel.

Recovery nutzt direkte PV mit Exportgegennachweis am selben lokalen Tag.
Top-up eines Speichers mit Mindestziel benötigt sichtbaren Restexport. Auch
Speicher ohne eigenes Mindestziel dürfen erreichbare fremde Recovery nicht
verdrängen; ihre übrigen Quellenregeln bleiben erhalten. Defizite bis
max(50 Wh, 2 % der Kapazität) gespeicherter Energie lösen keine Recovery aus.
Neue Aktionen benötigen mindestens 15 Minuten (oder längere konfigurierte
Mindestlaufzeit) und 50 Wh AC-Eingangsenergie. Das obere Ladeziel bleibt bindend.
Die Mindestaktionsdauer vergröbert kein vorhandenes feineres Suchraster:
Bei konfigurierten 5-Minuten-Schritten bleibt beispielsweise eine neue
40-Minuten-Aktion prüfbar. Bereits aktive Ladung darf ihr Ziel mit einem
kürzeren bestätigten Gate-Stopp erreichen.

Ein durchgängiges Energiekonto verhindert Tagesresets und doppelte Ladebedarfe.
Aux-Entladung erzeugt Bedarf erst zum tatsächlichen Zeitpunkt; morgige Ladung
erfüllt kein heutiges Defizit. Kaskadenpfade, vollständiger Endlastdienst und
bestätigte Verpflichtungen behalten ihren Schutz.

## Eingangssteuerung

`auto` erhält fremde Eingänge. Eigene Eingänge werden nach bestätigtem
Lade-Gate-OFF bei bestätigt ausgeschaltetem AC-Ausgang fristgerecht abgeschaltet.
Ein konfigurierter Ausgang ON (auch 0 W), unknown/unavailable oder eine fehlende
konfigurierte Entity hält den Eingang mit erhaltener Schalthoheit. Ohne
Ausgangszuordnung bleibt das frühere generische auto-Verhalten bestehen.
`keep_on` bleibt dauerhaftes Erhalten des Eingangs; `always_off` behält seinen
ausdrücklichen Vertrag.

Passthrough-Schutz benötigt eine vom Eingang getrennte Ladefreigabe. Neue
Konfigurationen mit `auto` und Ausgangszuordnung werden ohne diese Trennung
abgewiesen. Bei entsprechenden Alt-Konfigurationen bleibt der bisherige
Ladestopp über den einzigen Eingangsaktor bestehen; ein geschützter
Passthrough-Nachlauf ist damit nicht verfügbar. Vor einer B1/B2-Umstellung
werden deshalb Ausgangsrückmeldung und getrennte Ladefreigabe geprüft.

Ausgangsereignisse, periodische Nachprüfung und Neustart holen eigene ausstehende
Abschaltungen auch ohne erfolgreiche Planung nach. Eingangsnachlauf erneuert
keine Lade-Dwell- oder Flicker-Zeitstempel. Alle Abschaltpfade verwenden dieselbe
Entscheidung und prüfen vor einem Befehl aktuelle Ladeabsicht, Besitz,
Aktorzuordnung, Ausgang, Fristen und Kaskadenbesitz erneut.

Persistierte Schalthoheit ist an die Eingangs-Entity gebunden. ON allein beweist
keinen Besitz. Fremdes OFF widerruft Besitz; spätes Feedback darf widerrufenen
Besitz nicht wiederherstellen. Der Button „Eingang manuell übernehmen“ entzieht
nur die Eingangs-Schalthoheit, ohne Ladefreigabe, Ausgang oder Planung zu ändern.
Ein externer ON-Befehl auf bereits ON ist ohne eigenen Ereignisnachweis nicht
zuverlässig an einer Zustandskante erkennbar. Gleichzeitig abgesendete externe
Befehle schließen eine garantierte Unterbrechungsfreiheit aus.

## Historische Evidenz und Einführung

Die Nachtaufzeichnung vom 27.10. enthielt bereits 3,017 kWh Tages-PV. Eine
isolierte DCDC-Alternative 01–06 Uhr vermied nominal 138,539 Wh Netzbezug bei
136,681 Wh zusätzlicher Entnahme, verletzte aber das alte Fensterbudget.
Die +300-Wh-Probe war nur eine Reserve-/DC-Markt-Sensitivität. Die spätere
11:03-Prognose konnte 250 Wh frühere Entnahme zurückladen und damit die
optionale B1-Aufnahme nahezu ersetzen. Keiner dieser Vergleiche beweist eine
unter der Nachtprognose vollständig zulässige zusätzliche Freigabe.

Vor aktiver Einführung stehen reproduzierbare Replays, Shadow-Prüfung,
vollständige Qualitätsgates und unabhängiges Astra-/High-Review. Liveprüfungen
verwenden ausschließlich den lokalen Playwright-MCP. B1/B2 werden erst nach
Prüfung von Ausgangsrückmeldung und Schalthoheit auf auto umgestellt. Aus einem
bereits eingeschalteten Eingang wird dabei kein Besitz erfunden.

## BM-Steuerung OFF ab 0.64.0

Die manuelle Übergabe hat Vorrang vor allen Eingangs-Abschaltpflichten dieser
Spezifikation: OFF widerruft Besitz und ausstehende Anforderungen ohne Befehl.
Auch Schutz, Neustart und verspätete Rückmeldungen dürfen das freigegebene Gerät
nicht schalten. ON übernimmt einen bereits eingeschalteten Eingang nicht.
Der aktuelle vollständige Vertrag steht in [CURRENT_CONTRACTS](CURRENT_CONTRACTS.md#lasten-pausieren-und-schalten).
