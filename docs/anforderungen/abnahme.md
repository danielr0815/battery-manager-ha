# Fachliche Freigabe und Prüfnachweise

[Zur Gesamtspezifikation](README.md) · [Zum Quellenregister](quellen.md)

**Freigabestand der Fassung 1.0: bei der fachlichen Abnahme.** Die bestehende Schutzregel gilt im dokumentierten Geltungsbereich. Eine neue Regel wird durch Aufnahme in diese Fassung oder durch einen erfolgreichen Regressionstest allein nicht freigegeben. Der Betreiber entscheidet über die offenen Auslegungen. Die unabhängigen Reviews prüfen Fachinhalt, Konsistenz und Nachweisführung. Die Prüfergebnisse unten betreffen den bezeichneten Arbeitsstand.

## Entscheidungen für den Betreiber

| Nr. | Situation | Welche Entscheidung steht an? | Was wäre für den Betreiber beobachtbar? |
|---|---|---|---|
| D1 | Eine ältere Installation betreibt die automatische 24-V-Quellenumschaltung ohne koordinierten Inverteraktor. Der neue Vertrag benennt den koordinierten Betrieb. | Den strengeren Rückmeldungsnachweis auch für den automatischen Altpfad verbindlich übernehmen oder seinen bisher separat vereinbarten Nachweis beibehalten. | Bei einer lediglich angenommenen EIN-Meldung bliebe die bisherige Quelle unter der erweiterten Pflicht weiterhin eingeschaltet. Bei getrenntem Geltungsbereich ist der Altpfad eigenständig abzunehmen. Die bereits geltende Pflicht zur unterbrechungsarmen Übergabe bleibt bestehen. [Übergangsvertrag](04-energie.md#bm-ener-001) |
| D2 | Die neue Quelle ist bestätigt EIN. Für die vorige Quelle erscheint nach dem AUS-Auftrag eine Zustandsanzeige, deren Herkunft unklar ist. | Je unterstütztem Aktor festlegen, welche frische, dem tatsächlichen Vorgang zuordenbare Meldung als bestätigtes AUS gilt. Denkbar sind die qualifizierte Gerätepublikation oder ein gesonderter Rückmeldekontakt bei Geräten mit ausschließlich geschätztem Status. | Ohne den vereinbarten Beleg bleibt die Umschaltung als offen sichtbar. Mit gültigem Beleg kann sie abgeschlossen werden. Eine fehlende elektrische Leistung allein weist bei Leerlauf keinen ausgeschalteten Aktor nach. [Abschluss](04-energie.md#bm-ener-004) |
| D3 | Ein automatischer Export von 500 W ist unterwegs. Der SOC-Schutz wird für einen Augenblick aktiv. Er entfällt, bevor der korrigierende Nullbefehl bestätigt ist. | Entweder bestätigte Null und geklärte Reihenfolge als zwingende Schranke für jeden neuen Export verlangen oder einen nachweislich neuen, gültigen Auftrag nach vollständig aufgelöster Altreihenfolge zulassen. | Bei der ersten Variante ist der Nullzustand am richtigen Sollwert vor einem neuen Export beobachtbar. Bei der zweiten könnte der neue Sollwert früher eintreffen. In beiden Fällen bleibt der alte 500-W-Auftrag widerrufen und eine bestätigte manuelle Übernahme hat Vorrang. [Neufreigabe](04-energie.md#bm-ener-309) · [Nullnachweis](04-energie.md#bm-ener-008) |

**Separat offen ist ein Messnachweis.** Für die Standortabnahme einer zusätzlichen Inverterfreigabe fehlt ein aufgezeichnetes, gültiges positives Energiebudget über das gesamte Schutzfenster mit vollständigen Quellen- und Aktorrückmeldungen. Die vorliegenden Standortpläne hatten durchgehend 0 Wh zusätzliches Budget. Der synthetische Mehrtagestest prüft die Logik unter kontrollierten Bedingungen. Er ersetzt den noch ausstehenden Standortfall nicht. [Budgetprüfung](04-energie.md#bm-ener-038) · [Mehrtagestest](../../tests/ha/test_reserve_closed_loop.py)

## Abnahmeszenarien für die drei Pilotfälle

| Fall | Gegenprobe | Erfolg bzw. erwartete Sperre |
|---|---|---|
| Sensorversorgung | Der direkt am Netz erwartete Kellerfühler ist als bindend konfiguriert und meldet `unavailable`. Danach meldet er bestätigte 0 W. | Der Ausfall entzieht die Planbarkeit des Entfeuchters. 0 W erfüllt die Messpflicht. Für die Planung gelten weiterhin die übrigen Freigaben. [Messpflicht](05-verbraucher.md#bm-last-001) · [Nullmessung](05-verbraucher.md#bm-last-014) |
| Geschaltete Messstelle | Die Waschmaschinensteckdose ist bestätigt AUS. Nach dem Einschalten meldet der Matter-Sensor zunächst den alten Zustand. Ein Fossibot liefert während des Schlafs keinen SOC. | Der ausbleibende Waschmaschinensensor wird der bekannten Versorgung zugeordnet. Der alte Zustand belegt keinen neuen Lauf. Ein gültiger letzter Fossibot-SOC derselben Bindung kann befristet in die Planung eingehen. Für Aufwecken und Laden gelten eigene Nachweise. [Haushaltsgerät](05-verbraucher.md#bm-last-062) · [Speicher](05-verbraucher.md#bm-last-032) |
| 24-V-Quellenwechsel | Die Gegenquelle zeigt zuerst angenommenes EIN, meldet dann echtes EIN und verliert ihre Bestätigung während der Überlappung. | Das erste EIN erteilt keine Abschaltfreigabe für die bisherige Quelle. Nach Unterbrechung beginnt die vollständige Überlappung mit der erneuten gültigen Bestätigung. Die Diagnose bleibt bis zum Abschluss offen. [Zielbestätigung](04-energie.md#bm-ener-001) · [Überlappung](04-energie.md#bm-ener-302) |
| Einspeise-Nullbefehl | Der alte eigene Exportauftrag erreicht den Helfer erst nach Eintritt des Schutzes. Zwischenzeitlich zeigt der Helfer den vorherigen Wert 0 W. | Der alte Anzeigewert erledigt die offene Schutzpflicht nicht. Die Rückmeldung zum überholten Export wird dem alten Auftrag zugeordnet. Bei weiterhin automatisch geführtem Helfer und fehlender neuer Freigabe folgt eine Nullkorrektur. [Bestätigung](04-energie.md#bm-ener-008) · [Verspätete Meldung](04-energie.md#bm-ener-310) |

Die konkreten Testverweise an den BM-IDs unterscheiden vorhandene Regressionen, Teilnachweise und noch auszuführende Gegenproben. Für die gemeldeten Übergangsfehler ist ein offener Prüffall eine Arbeitsgrundlage für das spätere Design und die Implementierung.

## Aufgezeichnete Daten

Für den historischen Fehlervergleich wurde je Stunde der erste vollständig auswertbare Originalplan mit 55 bis 60,1 Minuten bis zum Ende des ersten Slots gewählt. Der BMS-Vergleichswert ist der Mittelwert der letzten fünf Minuten dieses Slots. Die tatsächliche Anzeige wurde an drei Zeitpunkten nach Publikation verglichen. Für die Altcode-Gegenrechnung wurden die übrigen Planeingaben festgehalten. Der separate lokale HA-Ereignistest änderte bei gleichem Ablauf ausschließlich die AC-Portzuordnungen. Die zusätzliche Tages-Gegenprobe verwendete den wirksamen Standort-Sollwert.

Das Verzeichnis `.codex-artifacts/entkopplung-2026-11-27/replay/` enthält die Skripte und gespeicherten Resultate. In einer lokalen Kopie mit den gesicherten Messreihen lassen sich die drei Verfahren vom Projektstamm aus wiederholen. Die Skripte für die historische Gegenrechnung und Aufbereitung schreiben ihre Ergebnisdateien erneut. Für den Vergleich mit den Originalprüfsummen eine Arbeitskopie verwenden.

```bash
.venv/bin/python .codex-artifacts/entkopplung-2026-11-27/replay/historical.py
.venv/bin/python .codex-artifacts/entkopplung-2026-11-27/replay/restlast.py
.venv/bin/pytest -q .codex-artifacts/entkopplung-2026-11-27/replay/test_replay.py
```

Die ursprünglichen Messdateipfade und Eingangshashes stehen in den lokalen Ergebnisdateien. `historical.json` trägt SHA-256 `41c59dcd0b349331505b5ec5b79ef102e470d14c19ff58c0833c253fc746b538`, das Abnahmeprotokoll `acceptance.json` trägt `837a12543c325ae45beaad0e665bb9131adb2e976d572a1562dd61353acecdec`. Der nachgerechnete Altcode stammt aus dem Bezugsrelease, Git-Blob `d558270fd39924240ec697991ac5136b71c368bf`. Öffentliche Testfälle benutzen synthetische Daten. [Fachliche Auswertung der aufgezeichneten Fälle](04-energie.md#aufgezeichnete-multiplus-gegenprobe) · [Öffentliche SOC-Gegenproben](../../tests/ha/test_psu_net_discharge.py)

## Verifikation der Fassung

Die redaktionelle Prüfung erfasst eindeutige IDs, Tabellenfelder, Statusangaben, interne Links, Anker, benannte Testfunktionen und die Vorwärts- und Rückwärtszuordnung im Quellenregister. Die fachliche Prüfung verwendet konkrete Gegenbeispiele. Ein Link auf eine teilweise passende Assertion wird als Teilnachweis ausgewiesen. Die Produktivschwelle eines Ressourcenlimits ist zusätzlich per Inspektion nachzuvollziehen, wenn der Test mit einem künstlich verkleinerten Budget arbeitet.

Die lokale Belegablage dieser Fassung ist `.codex-artifacts/srs/`. Sie enthält das Eingangsmanifest, die Prüfauswahl und die vollständigen Prüfprotokolle. Änderungen am Produkt oder an der Anlage sind ein eigener Auftrag.

### Ergebnisse am 28. November 2026

| Prüfung | Beobachtetes Ergebnis |
|---|---|
| Struktur und Herkunft | 427 eindeutige Anforderungen in sechs Fachkapiteln. Alle 427 sind im Quellenregister auffindbar. 806 Zuordnungszeilen erschließen 96 Quellen. Die Prüfung der lokalen Dateiziele, der Anker und der benannten Testfunktionen ergab keine Fehler. |
| Drei Pilotfälle | 197 ausgewählte HA-Testfälle bestanden. Die offen ausgewiesenen Schutzgegenproben in Kapitel 04 erfordern eigene künftige Nachweise. |
| Benannte Python-Regressionen der Fachkapitel | 454 direkt referenzierte Testfunktionen ergaben mit ihren Parametrisierungen 742 bestandene Testfälle. Die Pilotauswahl überschneidet sich mit diesem Lauf. |
| Benannte Dashboard-Testdateien | 101 einzelne JavaScript-Testfälle bestanden. |
| Statische Projektprüfung | Das Projektprofil `static` bestand mit Ruff, Formatprüfung und mypy. |
| Unabhängiges fachliches Review | Sol / Medium prüfte Verständlichkeit, Altzuordnung und Prüfnachweise. Astra / High prüfte Versorgung, Schalthoheit, Einspeisung, Reserve, Nebenläufigkeit und Datenschutz anhand von Gegenbeispielen. Sol präzisierte unter anderem die Zuständigkeit für den alten Einspeisehelfer und den Neustart während eines Zählerausfalls. Astra bestätigte den korrigierten Stand ohne verbleibenden kritischen Redaktionsbefund. |

Die drei Entscheidungen D1 bis D3 und die je Anforderung ausgewiesenen Nachweislücken bleiben für die Betreiberabnahme sichtbar.
