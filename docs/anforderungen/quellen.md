# Quellen, Bezugsstand und Rückverfolgbarkeit

[Zur Gesamtspezifikation](README.md) · [Zur Zuordnung der Einzelregeln](zuordnung.md)

Die **[Zuordnung](zuordnung.md)** lässt sich in beide Richtungen lesen. Wer eine alte Regel wie `F-FEEDIN R9` oder `24V-04` kennt, findet darunter die heutigen Einzelanforderungen. Wer mit einer BM-ID beginnt, findet Quelle und Prüffall direkt in der Zeile des Fachkapitels. Dieselbe Bezeichnung „R1“ hat je Ursprungsdokument eine andere Bedeutung.

## Welcher Stand ist beschrieben?

| Ebene | Festgehaltener Stand | Verwendung |
|---|---|---|
| Veröffentlichung | `v0.70.0`, Commit `4bc96b1db52b9010b61a00be00e6eae490efe29c` | Vergleichsstand für R. Statische oder gezielt getestete Nachweise beschränken sich auf die jeweils genannte Aussage. |
| Lokale Weiterentwicklung | Arbeitsbaum zu Beginn der Redaktion am 28.11.2026, 10:32 MEZ, Manifestversion 0.71.0. SHA-256 des Eingangsmanifests über die 476 vorgefundenen Versions- und Arbeitsdateien: `88934976ef73c5de5be726ee48b7225e1929aafdc52673fe50669b9de421e075`. | Vergleichsstand für L. Er enthält insbesondere AC/SOC-Entkopplung, Sensorpflicht, geschaltete Geräteversorgung und zugehörige neue Regressionen. Das Manifest mit den Einzelprüfsummen liegt lokal unter `.codex-artifacts/srs/eingangsstand.json`. |
| Schutzverträge | [24-V-Quellenwechsel und Einspeise-Nullbefehl](../F-SAFE-TRANSITIONS.md), Stand 28.11.2026 | Fassung 1.1 enthält die drei ausdrücklich freigegebenen Betreiberentscheidungen. Frühere Teilpflichten, Umsetzung und Nachweis behalten ihre jeweiligen Stände. |
| Anlagenbeobachtung | Lokal gesicherte Originalpläne, Recorder-Auszüge und Ereignis-Replays vom 26./27.11.2026 | Grundlage der konkreten Gegenproben. Datenherkunft, Wiederholbarkeit und Aussagegrenzen stehen im [Abnahmekapitel](abnahme.md#aufgezeichnete-daten). |

Der unveränderte lokale Eingangsbaum ist als Git-Tree `ccde8ee1d3d67bba197ad3714bddafe1affd8ccd` gesichert. Im lokalen Zweig `codex/anforderungsspezifikation-1.0` liegt die Fassung 1.0 unmittelbar auf diesem Quellenstand. Dadurch verweisen die Testlinks beim Checkout auf dieselben Dateien und Zeilen wie im Review.

Die Vorfassung 1.0 ist im lokalen Commit `ffb8f9a` erhalten. Der Betreiber hat D1 bis D3 am 28.11.2026 im Anschluss an die Erläuterung der Varianten freigegeben. Die Standortprüfung für D2 gleicht die geladenen Entitätszuordnungen mit dem ETS-Projekt, eingehenden Telegrammen und aufgezeichneten Schaltzuständen ab. [Entscheidungsprotokoll und Standortprüfung](abnahme.md#knx-rückmeldung-am-standort)

Bei einem Widerspruch zählt eine ausdrücklich dokumentierte Nachfolgeentscheidung für ihren Geltungsbereich. Ein Versionsdatum allein ändert den Vertrag für eine andere Betriebsart nicht. Ein gegenteiliger Codepfad zeigt eine Umsetzungsabweichung. Gibt es nur einen Test oder beobachtbares Verhalten, bleibt die fachliche Validierung sichtbar offen. Ein neuer Schutzvorschlag erhält unabhängig vom bestehenden Normalverhalten einen eigenen Status.

## Beispiele für geklärte Abweichungen

| Fall | Ergebnis des Abgleichs |
|---|---|
| Frühere gewichtete Summe aus Import und Export | Die Zielrangfolge schützt die Versorgung und den Hausverbrauch vor optionaler Nutzung. Der ursprüngliche Zielkatalog wurde in diesem Punkt ersetzt. [Ursprüngliche Ziele](../REQUIREMENTS.md) · [Verbindliche Zielhierarchie](../STRATEGY-CURRENT.md) |
| Archivgröße | Der ältere Replay-Leitfaden nennt 50.000 Ereignisse und 32 MiB. Seit 0.54.0 gelten 100.000 Ereignisse und 64 MiB auf Disk, einschließlich Transaktionen. Innerhalb dieses Budgets stehen 48 MiB für aufbewahrte Blöcke zur Verfügung. Das logische Journal hat ein eigenes 256-MiB-Limit. [Vertrag](../F-OCTOBER-OPTIMIZATIONS.md) · [Aufbewahrungsanforderungen](06-betrieb.md#bm-bet-043) |
| Temporäre Speicherdiagnose | Das Release 0.57.3 entfernte Prozessmessung, Einmal-Tracing und das externe Erfassungsskript. Die Archiv-Schreibsperre beim Start gilt weiter. [Freigabehistorie](../../CHANGELOG.md) · [Archiv-Schreibsperre](06-betrieb.md#bm-bet-048) |
| Gemeinsamer AC-Messpfad | Die Formel für die schnelle AC-Restlast und die Sperre ohne Rückfall auf die alternative Bilanz bei unvollständiger direkter Zuordnung existierten bereits in 0.70.0. Lokal neu ist die Unabhängigkeit des 48-V-Batterienachweises von beiden AC-Ports. [AC-Bilanz](02-signale.md#bm-sig-011) · [Batterienachweis](02-signale.md#bm-sig-022) |
| Konfigurierbare benannte Verbrauchsprofile | Frei benannte Tagesprofile und saisonale Gewichtung sind als Entwurfsentscheidungen dokumentiert. Der beschriebene Bezugsstand nutzt die bestehenden Tagestypen, die gemessenen Lernstunden und deren Rückfallregeln. [Entwurfsquelle](../F-CONSUMPTION-PROFILES.md) · [Verbrauchslernen](03-planung.md#bm-plan-078) |

## Weitere Belegarten

Die Zuordnung enthält die `F-*.md`-Verträge, fachliche Hauptdokumente, den öffentlichen Aktionskatalog sowie die relevanten Vorgänger-IDs. Ergänzend wurden folgende Bestände nach ihrer Funktion betrachtet:

- Die [Projektwissensbasis](../project-knowledge/00-projekt-instruktionen-und-index.md) und der [Betriebsleitfaden](../BATTERY_MANAGER_GUIDE.md) erklären die Anlage. Historische Beispielwerte gelten ausschließlich für die jeweils untersuchte Anlage.
- [Untersuchungen](../investigations/README.md), Releaseberichte und Golden-Replays belegen Anlässe oder Szenarien. Die Anforderungen beziehen sich auf das wiederholbare Verhalten, nicht auf einen einzelnen beobachteten SOC oder Verbrauch.
- Die [Aufträge zum PV-Prognoseanbieter](../orders/balcony-solar-forecast-auftrag-2026-07-24.md) betreffen ein anderes Produkt. Der Battery Manager verantwortet die Verarbeitung der ihm gelieferten Prognose und den Umgang mit ihren Lücken.
- Das [Architekturdokument](../ARCHITECTURE.md), die internen Datenklassen und Klassenbezeichnungen dienen der Zuordnung zum Design. Für einen Prüffall ist die beobachtbare Systemwirkung maßgeblich.
- [Testmatrix](../TEST_MATRIX.md), [Testqualitätsregeln](../TEST_QUALITY_AUDIT.md) und die konkret benannten Tests tragen die Verifikation. Ein Treffer in einem Quelltext oder eine hohe Codeabdeckung ersetzt die Prüfung des speziellen Grenzfalls nicht.

Bei einer späteren Änderung wird zuerst die betroffene BM-ID gesucht. Die Fachentscheidung, ihr Abnahmekriterium und der Bezug zur Vorgängerregel werden gemeinsam angepasst. Ein eigener Implementierungsauftrag kann dann Design und Code auf diese ID beziehen.
