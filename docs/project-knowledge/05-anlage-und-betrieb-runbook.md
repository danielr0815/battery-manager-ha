# Betriebsleitfaden — 0.56.0

Dieser Leitfaden verwendet neutrale Beispiele. Maßgeblich sind
[aktuelle Verträge](../CURRENT_CONTRACTS.md), der
[DC-Featurevertrag](../F-DC-PV-MARKET.md) und
[CONTRIBUTING](../../CONTRIBUTING.md). Frühere anlagenspezifische Befunde sind
historische Untersuchungen; sie sind keine allgemeine Installationsanleitung.

## Messung und Konfiguration

PV, Hausverbrauch und Netzteile liegen am gemeinsamen saldierenden Netzanschluss.
Hauslastmessung und konfigurierte Zusatzlasten dürfen nicht doppelt gebucht werden.
Beispiel: `sensor.house_power` für Hauslast, `sensor.pv_power` für PV,
`sensor.grid_power` für saldierte Netzleistung (Bezug positiv, Export negativ).
Der alternative Live-AC-Pfad benötigt alle drei vorzeichenbehafteten Messungen:
Netz, Inverter-AC-Eingang und Inverter-AC-Ausgang. DC-Leistung kommt beispielsweise
von `sensor.dc_load_power`; fehlt eine frische Messung, wird die Prognose plus
Verbrauchspuffer-Wh / eine feste Stunde verwendet.

Zusatzlasten außerhalb der Hauslastmessung erhalten `in_house_measurement: false`.
Ein Aktor gehört genau zu einer normalen Last, Kaskade oder Versorgung. Beispiel:
`switch.psu_24v`, `switch.psu_48v`, `switch.dc_converter`. Fremdautomationen dürfen
exklusiv besessene Aktoren nicht schalten. Bei vorhandenen doppelten Zuordnungen
zeigt Home Assistant eine Reparatur; betroffene Zusatzlasten können nicht starten.
Reine Lastaktoren werden geordnet freigegeben, Versorgungsschalter bleiben beim
Quellenbesitzer. Entferne die doppelte Zuordnung in den betreffenden Optionen.

## Planung nachvollziehen

Der gesamte verfügbare Prognosehorizont bestimmt den Reserveplan. Wirtschaftliche
Netzteilhaltung erhält SOC, wenn DC-Entnahme keinen benötigten PV-Speicherraum
schafft. PV übernimmt DC vorrangig, sobald Leistung, Verluste und Schutz dies
zulassen; auch eine volle Batterie verhindert die Versorgung nicht. Teil-PV
berechtigt zu keiner zusätzlichen Batterieentnahme.

Vorhandene DC-Entnahme kann zwischen netto ladenden PV-Phasen in teure Intervalle
verschoben werden. Der Marktschalter steuert diese Verteilung; hohe Preise erhöhen
weder DC-Budget noch AC-Freigabe. Ohne Budget können Netzteile auch im Peak laufen.
Die Karte erklärt PV-Versorgung, Peak-Versorgung oder SOC-Erhaltung und zeigt
verfügbares/verschobenes DC-Budget in Wh. Manuelle Vorgaben und Batterieschutz
behalten Vorrang. Die Vorschau ist ein geplanter Zustand, keine Gerätebestätigung.

## Sichere Quellenübergabe

Vor jedem Entfernen einer Quelle werden Planrevision, Gültigkeit, PV, SOC und
Netzbedingungen erneut geprüft. Bei 24 V wird die neue Quelle bestätigt, der
konfigurierte Überlappungszeitraum eingehalten und erst dann die alte entfernt.
Verzögerte Bestätigungen sind keine dauerhafte Freigabe. Bei Wolken oder veralteten
Messwerten übernimmt der gültige Plan; notwendiger Batterieschutz wartet nicht
auf eine wirtschaftliche Mindestschaltfrist. Fehlgeschlagene Rückkehr bleibt
als ausstehend diagnostiziert und wird erneut versucht.

## Installation und Abnahme

1. Konfiguration sichern; aktuellen veröffentlichten GitHub-Release über HACS
   installieren und Home Assistant neu starten. Ein lokaler Releasekandidat
   oder ein Commit auf `main` ist noch kein ausgeliefertes Update.
2. Zuordnung, Schutzschwellen, SOC und Messvorzeichen in Vorschau/Diagnose prüfen.
   24-V-Übergabe erst nach anlagenseitig geprüfter Verkabelung freigeben.
3. Optional Automatik aktivieren; bestätigte Quellenwechsel, Wolkenrückkehr,
   Schutz und Mindestschaltzeiten beobachten.
4. Bei Kaskaden Root→Leaf und Endlast konfigurieren. Nur Root erhält Input-Control.
   Fehlerursache vor Fault-Reset und bewusstem AUS→AN beheben. `shared` bedeutet
   nach einer Fremdänderung vollständige Übergabe an den anderen Besitzer.

Live-Prüfungen von Coding-Agenten erfolgen ausschließlich über den lokal
verbundenen Playwright-MCP und eine bestehende Home-Assistant-Sitzung. Ist dieser
nicht verfügbar, wird die Live-Abnahme pausiert. Zugangsdaten, private Anschlüsse
oder Tokens gehören nicht in öffentliche Dokumentation.

## Diagnosen teilen

Home-Assistant-Diagnosen, Profilexporte und Planner-Aufzeichnungen enthalten
Entity-Namen, SOC, Verbrauch, Lastlaufzeiten und gelernte Haushaltsmuster.
Secret-Redaktion anonymisiert diese Daten nicht. Vor einer öffentlichen Meldung
Inhalt prüfen und private Details entfernen. Der anonymisierte Export ist ein
separates zukünftiges Feature. Bei Sicherheitsproblemen gilt
[SECURITY](../../SECURITY.md).
