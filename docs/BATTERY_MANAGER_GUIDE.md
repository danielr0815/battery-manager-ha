# Battery Manager Architektur und Betrieb

Stand: v0.57.1, 12.10.2026. Battery Manager plant die Nutzung einer Hausbatterie
und steuerbarer Lasten anhand von PV, Verbrauch, SOC und optionalen Marktpreisen.
Dieser Leitfaden beschreibt das Softwareverhalten. Für die konkrete Installation
sind passende Messgrenzen, Vorzeichen und bestätigte Gerätezustände erforderlich.

## Architektur und Planung

Der reine Python-Kern unter `custom_components/battery_manager/core/` verarbeitet
unveränderliche Eingaben und liefert einen Plan. Er importiert Home Assistant
nicht. Die HA-Schicht liest Messungen und Prognosen, verwaltet Lernzustände,
prüft Freigaben und schaltet Aktoren. Die Dashboard-Karten erklären Plan und
Ausführung; eine Empfehlung ist noch keine Gerätebestätigung.

Die Reserveplanung berücksichtigt den vollständigen verfügbaren Prognosehorizont.
Alle enthaltenen Tage können benötigten PV-Speicherraum begründen. DC-Versorgung,
Batterieschutz und physische Ausführbarkeit begrenzen wirtschaftliche Entscheidungen.
Zusatzlasten dürfen keinen zusätzlichen Netzbezug verursachen. Marktpreise
verteilen verfügbare Energie; sie erzeugen kein zusätzliches Entnahmebudget.

Die erste Planung startet nach Einrichtung der Entities im Hintergrund.
Telemetrie wird über schnelle Aktualisierungen verarbeitet; relevante Änderungen
lösen vollständige Planung aus. Während CPU-Arbeit bleiben Schutzprüfungen aktiv.
Gemessene Planungsdauer erhöht ab v0.57.0 den Vorlauf der nächsten Planung.
Die Freigabe bleibt fünf Minuten gültig, auch wenn die Berechnung länger dauert.

Quellen: [Code-Map](ARCHITECTURE.md), [aktuelle Verträge](CURRENT_CONTRACTS.md),
[Reserve und DC-Markt](F-DC-PV-MARKET.md).

## Quellensteuerung mit 24 V und 48 V

Eine 24-V-Übergabe bestätigt zuerst die neue Quelle, wartet die gesamte
konfigurierte Überlappung und entfernt erst danach die bisherige Quelle.
Das gilt auch bei ausgefallenem oder unbekanntem Netz. Netzrückkehr während
der Übergabe erfordert eine erneute Prüfung. Die Verkabelung muss die vorgesehene
Überlappung unterstützen.

Verspätete eigene Rückmeldungen werden dem jeweiligen Aktorauftrag zugeordnet.
Der interne Quellenzustand folgt der Bestätigung. Wirtschaftliche DC-Vorgaben
eines gültigen Plans bleiben bei aktivem AC-SOC-Schutz erhalten; abgelaufene
Vorgaben werden verworfen. Ein neuer wirtschaftlicher Netzteilstart hat einen
SOC-Abstand von einem Prozentpunkt zur DC-Vorbereitungsgrenze. Bereits laufende
Haltung nutzt die ursprüngliche Grenze. Schutz, manuelle Vorgaben und PV-Rückkehr
haben weiterhin Vorrang.

Aktoren benötigen eindeutige Besitzer. Bei Kaskaden übernimmt der Kaskadenmanager
die zugeordneten Schalter. Fremdautomationen dürfen exklusiv besessene Aktoren
nicht parallel steuern. Für 48 V gelten der eigene Quellenvertrag und die
physische Versorgung; der 24-V-Überlappungsvertrag lässt sich nicht pauschal
auf eine andere Verdrahtung übertragen.

Quellen: [Quellensteuerung](CURRENT_CONTRACTS.md),
[Kaskaden](F-CASCADE-STORAGE.md), [Laststeuerung](LOAD_CONTROL.md).

## Verbrauchslernen und Messung

Beide Netzteile können getrennte Messungen für AC-Aufnahme und DC-Abgabe erhalten.
AC-Aufnahme wird aus dem gemessenen Hausverbrauch entfernt; DC-Abgabe wird dem
DC-Verbrauch wieder zugerechnet. Messgrenzen müssen dazu passen, damit Energie
nicht doppelt gezählt wird. Fehlende oder negative Messungen bei eingeschaltetem
Netzteil liefern unbekannte Stunden, keinen Nullverbrauch. Neue Zuordnungen
gelten ab ihrer Konfigurationsepoche und ergänzen keine verlorenen Altwerte.

Ein erfolgreicher Lernlauf beweist keine frischen Messungen. Diagnose und
Verbrauchskarte zeigen letzte verwertbare Beobachtung, Stundenabdeckung,
Ausschlussgründe und Profilreife. Die Karte warnt bei leerem Vortag oder
mindestens 48 Stunden alten beziehungsweise fehlenden Messungen.

Vor Stundenbeginn verwendete P50- und verfügbare P80-Prognosen werden mit
Tagesart und Quelle gespeichert. Später vervollständigte Recorderstunden
werden gegen diese Originalprognosen bewertet. Fehlende historische P80-Werte
werden nicht durch heutige Profile ersetzt. Prognosefehler aus wenigen,
selektiv messbaren Stunden sind kein vollständiger Wochenfehler.

Quellen: [Lernvertrag](CURRENT_CONTRACTS.md),
[Messkonfiguration und Abnahme](project-knowledge/05-anlage-und-betrieb-runbook.md).

## Betriebsdiagnose und Performance

Für eine Schaltanalyse Anforderungen, Gerätemeldungen, SOC, Netzstatus, PV und
den damaligen Plan gemeinsam prüfen. Serviceerfolg allein beweist weder
Gerätezustand noch elektrische Unterbrechungsfreiheit. Telemetrie kann sehr kurze
Spannungsereignisse übersehen.

Planungsdauer, Prozess-CPU und VM-Auslastung sind unterschiedliche Messgrößen.
v0.57.0 vermeidet wiederholte Reservevorbereitung und ungenutzte Quellenproben.
Ein instrumentierter lokaler Replay benötigte 27,5 % weniger Prozess-CPU bei
gleicher Energiebilanz. Das ist keine nach Installation gemessene HA-Beschleunigung.
Die Ursache früherer HA-Speicherüberläufe bleibt offen.

Untersuchungen und ihre Grenzen stehen im [Untersuchungsindex](investigations/README.md).
Einzelentscheidungen bleiben nur innerhalb der tatsächlichen Archivgrenzen
erhalten; eine Kompression stellt verlorene Historie nicht wieder her.

## Entwicklung und Release

Aktuelle Befehle und Releasefolge stehen in [CONTRIBUTING](../CONTRIBUTING.md).
Der Kern hat 100 % Line-Coverage; Gesamtprojekt und jedes HA-Modul benötigen
mindestens 95 %. Tests kontrollieren produktive Verzögerungen mit virtueller Zeit.
Beabsichtigte Planneränderungen benötigen geprüfte Golden-Snapshots.

Vor Veröffentlichung wird der exakte Commit mit dem vorgesehenen Tag validiert.
Das veröffentlichte Release wird erneut geprüft. HACS verwendet den Release-Tag;
Installation und HA-Neustart erfolgen getrennt beim Betreiber. Ein GitHub-Release
ist kein Nachweis einer Installation oder erfolgreichen Anlagenabnahme.
