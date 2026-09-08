# v0.40.0 – Betriebsaufzeichnung und Tagesvergleich

Planänderungen und tatsächliche Gerätereaktionen lassen sich jetzt gemeinsam
nachvollziehen: Battery Manager zeichnet Planstände, aktive Schaltanforderungen,
Service-Antworten, passive Zustandswechsel und Kaskaden-Recovery in einem lokalen
Archiv auf. Eine erfolgreiche Service-Antwort gilt weiterhin nicht als physische
Bestätigung. Der HA-Diagnoseexport enthält die Aufzeichnung für spätere Analysen.

Prognose- und Kaskadenkarte zeigen einen aufklappbaren Tagesvergleich mit
Plan-/Ist-Energie, Messabdeckung, bestätigten Aktorzeiten, SOC-Grenzen und
Schaltzahlen. Laufzeit- und Leistungsabweichungen werden getrennt ausgewiesen.
Kaskaden-Eingangsmessungen berücksichtigen die Durchleitung zu nachgelagerten
Geräten und werden nicht als reine Akkuladung interpretiert. Fehlende oder
veraltete Messwerte bleiben unbekannt; gültige Teilintervalle bleiben erhalten,
und ein Neustart erzeugt keine hochgerechnete Ausfallenergie.

Bestehende Last- und SOC-Sensoren werden automatisch übernommen. Für einen
umfassenden Tagesvergleich können in den Integrationseinstellungen vier
optionale Leistungssensoren in W oder kW ergänzt werden: PV, Wohnungsverbrauch
ohne BM-Zusatzlasten, Netzbezug und Netzeinspeisung. Bezugs- und Einspeisewerte
müssen getrennte, nichtnegative Leistungen liefern.

Das Detailarchiv ist auf sieben Tage, 50.000 Ereignisse und 32 MiB begrenzt;
das zuerst erreichte Limit gilt. Kompakte Tagesberichte bleiben zusätzlich
30 Kalendertage erhalten. Die Aufzeichnung bleibt lokal und wird verzögert
sowie beim Entladen gespeichert. Es gibt keinen automatischen Upload. Der
Tagesbericht wird nicht zusätzlich in der HA-Recorder-Historie abgelegt.

Das lokale Werkzeug `scripts/replay_operation.py` rekonstruiert Tagesberichte,
prüft sämtliche erhaltenen Planner-Aufrufe und vergleicht zwei Archive mit
getrennt ausgewiesener Messabdeckung. Virtuelle 24-Stunden-Läufe verbinden den
echten Planner und Kaskaden-Executor mit simulierten Geräten, schwankender PV,
Verbrauchsspitzen, Neustart, Telemetrieausfall, Tank-Sättigung und verzögerter
OFF-Bestätigung. Die Planungsstrategie und ihre Toleranzen bleiben unverändert;
eine reale Tagesabnahme folgt nach Installation und Datensammlung.

Validierung: 1.784 Python-Tests und 36 Frontend-Tests bestanden. Gesamt-Coverage
97,33 %, Kern 100 %, jedes HA-Modul mindestens 95 %. Ruff, Formatprüfung,
mypy und Lockfile-Prüfung bestanden.
