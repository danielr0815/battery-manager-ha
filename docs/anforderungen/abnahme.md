# Fachliche Freigabe und Prüfnachweise

[Zur Gesamtspezifikation](README.md) · [Zum Quellenregister](quellen.md)

**Freigabestand der Fassung 1.1 am 28. November 2026.** Der Betreiber hat D1 bis D3 ausdrücklich angenommen. Diese Entscheidungen gelten ab dieser Fassung. Für die übrige Gesamtausgabe steht die Betreiberfreigabe weiterhin aus. Die Statusfelder in den Fachkapiteln weisen die Umsetzung und die vorhandenen Tests separat aus.

<a id="entscheidungen-für-den-betreiber"></a>

## Verbindliche Betreiberentscheidungen

| Entscheidung | Verbindliche Regel | Beobachtbare Folge |
|---|---|---|
| D1 · Geltungsbereich des 24-V-Quellenwechsels | Für automatische Wechsel zwischen zwei schaltbaren 24-V-Quellen gelten in beiden Richtungen dieselben Bestätigungs- und Überlappungsregeln. Dies schließt den Betrieb ohne koordinierten Inverteraktor ein. | Die bisherige Quelle bleibt bei nur angenommenem Ziel-EIN eingeschaltet. Die volle konfigurierte Überlappung beginnt mit belastbarem Ziel-EIN und nach einer Unterbrechung von vorn. Bestätigte manuelle Übernahme und bestehender Netzausfallschutz behalten Vorrang. [Zielbestätigung](04-energie.md#bm-ener-001) · [Überlappung](04-energie.md#bm-ener-302) |
| D2 · Herkunft der Schaltbestätigung | Die aktuelle, zur Umschaltung gehörende Meldung des tatsächlichen Aktorkanals gilt als Bestätigung. Liefert der Aktor lediglich ein Befehlsecho oder einen geschätzten Wert, ist für den Nachweis eine unabhängige Zustandsrückmeldung erforderlich. | Beim Ziel-EIN wartet die Abschaltung der alten Quelle auf den belegten Zustand und die volle Überlappung. Beim AUS der alten Quelle bleiben der Abschluss und davon abhängige Schritte bis zum Nachweis offen. Eine zwischendurch angezeigte Übereinstimmung mit dem Befehl und gemessene 0 W reichen jeweils für sich allein nicht aus. [Abschluss](04-energie.md#bm-ener-004) |
| D3 · Erneuter automatischer Export nach einem Schutzimpuls | Ein neuer Export erfordert bestätigte Null am betroffenen, weiterhin automatisch geführten Sollwert, geklärte Reihenfolge der alten Aufträge und eine neue gültige Freigabe nach Wegfall aller Schutzgründe. | Kann ein altes −500 W noch eintreffen, steht der Stopp weiter aus. Wird ein alter Export später erneut wirksam oder wird seine Reihenfolge wieder unklar, ist die geordnete Null neu nachzuweisen. Eine nachweislich historische Meldung lässt einen weiterhin gültigen aktuellen Sollwert unberührt. Eine bereits belastbare Null ohne offenen Export erfordert kein erneutes Schreiben. Nach erfüllten Bedingungen darf ein neuer Auftrag ohne zusätzliche Wartefrist erfolgen. Bei bestätigter manueller Übernahme gilt die Bedienerhoheit. [Neufreigabe](04-energie.md#bm-ener-309) · [Nullnachweis](04-energie.md#bm-ener-008) |

## KNX-Rückmeldung am Standort

**Für beide 24-V-Schaltkanäle ist die Herkunft der Rückmeldung belegt.** Die HA-Konfiguration ordnet jedem Kanal eine eigene Schalt- und eine eigene Statusadresse zu. Das in HA geladene ETS-Projekt weist die Rückmeldung jeweils als Statusobjekt desselben Aktorkanals aus. Im Zehntagesfenster vom 18. bis 28. November 2026 stammten alle 194 eingegangenen Wertmeldungen der Netzteil-Statusgruppe und alle 1.467 der Wandler-Statusgruppe von diesem Aktor. Für beide Kanäle waren EIN und AUS belegt.

Im Abgleich mit dem HA-Verlauf lagen die Schalteranzeigen in vier beobachteten Schaltvorgängen 99 bis 142 Millisekunden vor dem Eingang des passenden Statustelegramms. Die frühere Anzeige kann daher keinen dieser vier Nachweise liefern. Abnahmekriterium für die Ausführung ist die passend zugeordnete Rückmeldung. Die beobachtete Verzögerung ist keine Wartezeitvorgabe. Ein Gerätestatus belegt den gemeldeten Ausgangszustand. Die tatsächliche Lastübernahme erfordert einen gesonderten elektrischen Nachweis.

Die privaten Einzelbelege, Zeitstempel, Prüfsummen und das Auswerteskript sind lokal unter `.codex-artifacts/knx-freigabe/` gesichert. Die Prüfung nutzte vorhandene Telegramme und Recorder-Einträge.

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

Die folgende Tabelle dokumentiert die Prüfung der Vorfassung 1.0.

| Prüfung | Beobachtetes Ergebnis |
|---|---|
| Struktur und Herkunft | 427 eindeutige Anforderungen in sechs Fachkapiteln. Alle 427 sind im Quellenregister auffindbar. 806 Zuordnungszeilen erschließen 96 Quellen. Die Prüfung der lokalen Dateiziele, der Anker und der benannten Testfunktionen ergab keine Fehler. |
| Drei Pilotfälle | 197 ausgewählte HA-Testfälle bestanden. Die offen ausgewiesenen Schutzgegenproben in Kapitel 04 erfordern eigene künftige Nachweise. |
| Benannte Python-Regressionen der Fachkapitel | 454 direkt referenzierte Testfunktionen ergaben mit ihren Parametrisierungen 742 bestandene Testfälle. Die Pilotauswahl überschneidet sich mit diesem Lauf. |
| Benannte Dashboard-Testdateien | 101 einzelne JavaScript-Testfälle bestanden. |
| Statische Projektprüfung | Das Projektprofil `static` bestand mit Ruff, Formatprüfung und mypy. |
| Unabhängiges fachliches Review | Sol / Medium prüfte Verständlichkeit, Altzuordnung und Prüfnachweise. Astra / High prüfte Versorgung, Schalthoheit, Einspeisung, Reserve, Nebenläufigkeit und Datenschutz anhand von Gegenbeispielen. Sol präzisierte unter anderem die Zuständigkeit für den alten Einspeisehelfer und den Neustart während eines Zählerausfalls. Astra bestätigte den korrigierten Stand ohne verbleibenden kritischen Redaktionsbefund. |

### Prüfung der Fassung 1.1

Die Strukturprüfung der 430 Anforderungen bestätigte eindeutige IDs, gültige Verweise und Rückverfolgbarkeit im Quellenregister. Die tatsächlichen Quellen der beiden KNX-Rückmeldungen und die Reihenfolge gegenüber der HA-Schalteranzeige wurden anhand der oben beschriebenen Aufzeichnungen geprüft. Die bestehenden Regressionen zu ausbleibender Zielbestätigung in beiden Betriebsarten, gesperrter Inverterfreigabe, verspätetem eigenem Export und dessen Abgrenzung von einer manuellen Übernahme ergaben 13 bestandene Testfälle. Vier weitere bestandene Fälle prüfen ein bereits gesendetes DC/DC-AUS mit ungewissem Ergebnis bei erneuter Ausführungsprüfung. Die neu verlangten Gegenproben zur Herkunft und zur erneuten Freigabe nach einem Schutzimpuls bleiben bis zu ihrem eigenen Ausführungsnachweis offen.
