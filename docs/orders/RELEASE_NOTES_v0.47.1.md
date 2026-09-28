# v0.47.1 — Prognoseabhängige SOC-Erhaltung auch für DC

Der aktive Reservebetrieb erhält jetzt auch die Energie für native DC-Verbraucher.
24- und 48-V-Netzteile übernehmen bereits oberhalb der Schutzschwellen, wenn die
Batterieentnahme nicht für den erwarteten PV-Speicherraum heute oder morgen nötig
ist. Benötigter DC-Verbrauch hat weiter Vorrang vor zusätzlicher AC-Entladung.
Neue Prognosen können die Batterie wieder freigeben; wirtschaftliche Haltung
wird nicht zur dauerhaften Schutz-Hysterese. Der dadurch höhere Netzbezug zugunsten
des erhaltenen SOC ist beabsichtigt.

Die Netzteilübernahme verdrängt keine nutzbare PV. Leistungsgrenzen, unbekannte
48-V-Abgabe, Schutzschwellen, manuelle Vorgaben und bestätigte Quellenübergaben
bleiben berücksichtigt. Die drei geänderten Reserve-Golden-Szenarien behalten
identische Exportmengen; die übrigen Szenarien bleiben unverändert.

Das Dashboard erklärt wirtschaftliche DC-Haltung und eine fehlende Bestätigung
des unabhängigen 24-V-Rückfalls. Diese Freigabe wird durch das Update nicht
automatisch gesetzt: Sie setzt einen tatsächlich geprüften Rückfall bei Netz-
und Home-Assistant-Ausfall voraus. Die Aktordiagnose zeigt das endgültige Schaltziel.

Validierung: 2234 Python-Tests, 61 Frontendtests und 18 Browsertests erfolgreich.
Gesamt-Coverage 97,78 %, Kern 100 %, jedes HA-Modul mindestens 95 %. Ruff, mypy,
Frontend-Lint, Formatierung und Bundle-Abgleich bestanden.
