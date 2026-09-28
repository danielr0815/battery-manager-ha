# Prognoseabhängige SOC-Erhaltung für DC und AC ab 0.47.1

> Ab 0.49.0 präzisiert [Lastpriorisierung](F-RESERVE-LOAD-PRIORITY.md) den
> zeitübergreifenden DC-Vorrang und ersetzt die rein späteste AC-Auswahl.

Dieser Vertrag ersetzt die wirtschaftliche Quellenwahl aus F-RESERVE-DC-FIRST.
Die physische Energieabbildung, Schutzbedingungen und lokalen Kalenderhorizonte
bleiben bestehen. Operatorvorgabe 28.09.2026: SOC insgesamt nur so weit absenken,
wie für die erwartete PV-Aufnahme nötig; das schließt native DC-Verbraucher ein.

## Befund und Live-Maßnahmen

Am 28.09.2026 meldete die aktive Reserve nachts `no_preparation_needed`, 0 Wh
zusätzlichen Speicherbedarf und 0 W AC-Freigabe. Trotzdem entlud DC bis 6 %.
48 V wurde dauerhaft erst um 07:06:35 MESZ bei 7 % aktiviert. Eine parallele
HA-Automation schaltete bereits um 06:57:47 bei 9 % ein; der BM schaltete wieder aus.
Die 24-V-Übernahme war durch `reserve_transfer_verified=false` gesperrt. Um
07:43:46/49 stellte der BM deshalb DC/DC wieder her und schaltete das Netzteil aus.

Auf ausdrücklichen Auftrag wurde die konkurrierende Automation
`automation.energie_steuerung_48v_netzteil` deaktiviert (Zustand `off` geprüft).
Nach ausdrücklicher Bestätigung des unabhängig geprüften Hardware-Rückfalls wurde
nur `reserve_transfer_verified` auf `true` gesetzt. Der Vergleich sämtlicher
Optionen vor/nach dem Speichern ergab ausschließlich diese Änderung. Danach
waren beide Netzteile bestätigt an, DC/DC aus. Diese Live-Maßnahmen sind bereits
mit 0.47.0 wirksam; die neue Prognoseregel benötigt die Installation von 0.47.1.

## Regeln

1. **Vorhandene Energie erhalten:** Bei prognostiziertem DC-Defizit dürfen beide
   verfügbaren Netzteile bereits oberhalb der Schutzschwellen übernehmen. Es gibt
   keinen historischen Haltewert, keine neue feste SOC-Grenze und kein Netzladeziel.
2. **DC vor zusätzlichem AC:** Eine separate rückwärts berechnete DC-Grenze
   lässt benötigte native Entnahme zu, bevor optionale AC-Abgabe erforderlich wird.
   Sie nutzt dieselben physisch erreichbaren Referenzzustände und Exportbudgets.
   DC wird nicht netzgespeist, nur um später dieselbe Energie über AC abzugeben.
3. **Spät und bedarfsgerecht:** Quellenwahl erfolgt vor jedem Fünfminutenabschnitt.
   Der gestützte Endzustand muss innerhalb der DC-Grenze bleiben. Er darf außerdem
   keinen zusätzlichen Export gegenüber natürlicher Versorgung verursachen;
   eine am Maximum abgeschnittene SOC-Zahl allein beweist dies nicht.
4. **Neuberechnung:** Wirtschaftliche Netzteilhaltung ist von Schutz-Hysteresen
   getrennt. Bereits eingeschaltete Quellen oberhalb ihrer Erholungsschwellen
   bleiben nicht allein wegen des Ist-Zustands gelatcht. Neue PV-Prognosen dürfen
   die Batterie wieder freigeben. Niedriger SOC und manuelle Wünsche bleiben
   vorrangig. Gleichzeitige AC-Entladung bei Netzteilbetrieb bleibt ausgeschlossen.
5. **Physik und Versorgung:** Unbekannte/zu hohe Busspannung und begrenzte
   Netzteilleistung können vollständige SOC-Erhaltung verhindern. Schaltwunsch
   ist kein Leistungsnachweis; prognostizierte Restentladung bleibt sichtbar.
   Ohne bestätigten unabhängigen 24-V-Rückfall bleibt DC/DC aktiv. Bekannter oder
   unbekannter Netzausfall bleibt gesondert behandelt. Bestätigungen und geordnete
   Übergaben werden weiterhin abgewartet; bestehende Schaltintervalle gelten.
6. **Diagnose:** `dc_reserve_holding` benennt wirtschaftliche SOC-Erhaltung;
   `dc_support_protection` bleibt der Schutzgrund. `dc24_transfer_verified=false`
   wird im Reservebericht erklärt; `dc24_block_reason=transfer_unverified` und
   das endgültige `desired.dc24=false` erklären eine Sperre im Executor.

## Beabsichtigte Golden-Änderungen

Nur die drei aktiven Reserve-Szenarien ändern sich. Alle übrigen Goldens bleiben
identisch. Der zusätzliche Netzbezug ist die explizit gewünschte Erhaltung von
Batterieenergie und wird nicht Zusatzlasten als vermeidbarer Export gutgeschrieben.

| Szenario | Netzbezug vorher → nachher (kWh) | Export vorher → nachher (kWh) | Wirkung |
|---|---|---|---|
| reserve_dark_winter | 4,118539 → 5,871348 | 0 → 0 | Mindest-SOC 6,445 → 41,0309 %; unbekannte 48-V-Abgabe schützt die native Last nicht fiktiv. |
| reserve_sunny_winter | 1,750000 → 2,851124 | 0 → 0 | Benötigte Vorbereitung bleibt; spätere DC-Netzübernahme erhält übrige Energie. |
| reserve_summer_preparation | 0,450000 → 0,663483 | 15,318641 → 15,318641 | SOC-Erhaltung außerhalb nötiger Vorbereitung ohne zusätzlichen Export. |

## Regressionen

`tests/core/test_reserve.py` prüft frühe Unterstützung bei 15/38/80 %, gemessene
vollständige Haltung, unbekannte Leistung, Prognoserücknahme und -verstärkung,
DC-Vorrang, späteste DC-Vorbereitung und PV-Verdrängung nahe dem Maximum.
`test_reserve_reachability.py` und die September-Fixture erhalten die Verträge
für Kalenderhorizont, physische Grenzen und unerreichbare Exportvermeidung.
`tests/ha/test_reserve_policy.py` und `test_reserve_runtime.py` prüfen reale
Planner-/Executor-Ausgaben einschließlich Schattenmodus, Hardwarefreigabe,
Quellenbestätigungen und Vorrang aktueller Schutzbedingungen. Frontendtests
prüfen beide Sprachen und die sichtbare 24-V-Sperre.

## Validierung

Abschließender lokaler Lauf: 2234 Python-Tests erfolgreich, 97,78 % Gesamt-Coverage;
alle 50 Module erfüllen ihre Gates (Kern 100 %, jedes HA-Modul mindestens 95 %).
Die separate Kernprüfung umfasst 562 Tests mit 100 % Coverage. Zusätzlich bestehen
61 Frontendtests, 18 Browsertests, ESLint/Prettier, Bundle-Abgleich, Ruff, mypy und
`uv sync --locked --group dev`. Versionen in Manifest, Projekt und Lockfile sind
0.47.1. Die Release-Validierung prüft anschließend den exakten Commit; die
Installation über HACS bleibt ein separater Deployment-Schritt.
