# Binäre Inverterfreigabe ab 0.52.0

## Befund

Die Live-Historie von `number.victron_settings_ess_maxdischargepower` zeigte am
01.10.2026 gegen 01:37–01:43 MESZ unter anderem 10, 30 und 60 W. Reserveplanung
und schnelle AC-Regelung verwendeten Leistungslimits, um kleine Energiebudgets
zu strecken. Der Betreiber verlangt stattdessen ausschließlich Sperre oder volle
Freigabe, um lange Laufzeiten bei künstlich begrenzter Leistung zu vermeiden.

## Regeln

1. **R1 – Aktor:** Der BM schreibt ausschließlich 0 W oder
   `inverter_max_power_w`. Die Bestätigung verlangt denselben Sollwert; eine
   Rückmeldung mit Teilleistung bestätigt keine volle Freigabe. Das ist eine
   Entladesperre, kein Wechsel des VE.Bus-Gerätemodus. Die tatsächliche Leistung
   folgt weiterhin dem ESS-Hausbedarf und kann unter der freigegebenen Leistung
   liegen. Es wird keine zusätzliche Einspeisung angefordert.
2. **R2 – Reserveplanung:** Der bestehende Fünf-Minuten-Planer wählt ganze
   ON-/OFF-Schritte. Das verfügbare AC-Budget muss die prognostizierte Restlast
   einschließlich Standby über den ganzen Schritt decken, gedeckelt durch die
   Inverter-Maximalleistung. Wirkungsgrade, nominale DC-Verpflichtungen,
   Inverter-Untergrenze, Lastpriorität und späteste Platzierung bleiben wirksam.
   Teilslots verwenden ihre tatsächliche Dauer. ON-Schritte erhalten das volle
   Leistungslimit; die Simulation liefert nur den prognostizierten Bedarf.
3. **R3 – Livefreigabe:** Das verfügbare Budget muss Maximalleistung für
   35 Sekunden (30 Sekunden Messgültigkeit plus fünf Sekunden Prüfung) tragen.
   Reicht es nicht, endet die Livefreigabe sofort mit `reserve_budget`;
   Ausschaltverzögerung und niedrige aktuelle Last rechtfertigen keine
   Teilleistung. Bestehende Schutz-, Frische- und Quellenprüfungen gelten weiter.
4. **R4 – Geplante Freigaben absichern:** Auch eine bereits kommandierte
   Planfreigabe wird alle fünf Sekunden geprüft. Ihre Energiegrenze ergibt sich
   aus dem Start-SOC abzüglich des freigegebenen Headrooms; der prognostizierte
   DC-Verbrauch des ersten Schritts ist darin bereits reserviert. Zusätzlich
   bleiben SOC-Hysterese als Messunsicherheit und Energie für 35 Sekunden volle
   Leistung zurück. Fehlender/veralteter SOC, Netzstatus, Quellenkonflikte oder
   Planablauf sperren wie beim Live-Pfad; ein unbekannter Netzstatus erlaubt
   keine ungeschützte volle Freigabe mehr. AC-Leistungsmesser sind für eine
   geplante Freigabe weiterhin optional. Der Timer startet keinen geplanten
   Lauf vor dem Quellenbesitzer und dessen Mindestschaltabstand; er überwacht
   bereits kommandierte Freigaben und wiederholt unbestätigte Sperren.
5. **R5 – Darstellung:** Reservekurve, Entscheidung, Live-Diagnose und
   Aktorbestätigung verwenden dieselbe binäre Semantik. Das zusätzliche
   Diagnosefeld `live_ac.planned_limit_w` zeigt die aktuell noch zulässige
   Planfreigabe. Ein bestätigter 0-W-Wert wird nie als aktiver Inverter gemeldet. Ein Stundenpunkt trägt
   weiterhin den Wert seines ersten Simulationsschritts; die Schaltzeitspur
   enthält die späteren ON-/OFF-Abschnitte.

## Energie und Grenzen

Ein 175-Wh-AC-Budget bei 600 W Restlast erlaubt drei fünfminütige Abschnitte
(150 Wh), keinen vierten. Bei zusätzlich 15 W Standby verbrauchen dieselben
Abschnitte 153,75 Wh. Die restlichen 25 bzw. 21,25 Wh werden nicht durch
Teilleistung ausgeschöpft. Dadurch können Netzbezug und späterer PV-Export
gegenüber dem alten kontinuierlichen Limit steigen; der DC-Vorrat wird nicht
für die Rundung angebrochen. Auch eine kleine numerische DC-Schutzmarge kann
den letzten ansonsten genau passenden Abschnitt sperren.

Es gibt keine neue schnelle Pulsweitensteuerung und keinen zusätzlichen
Einschalttimer. Die Reserveplanung bleibt prognoseabhängig und wird mit neuen
Messungen sowie spätestens an ihrer nächsten Fünf-Minuten-Grenze erneuert.
Der schnelle Pfad prüft beide Freigaben alle fünf Sekunden. Seine zusätzliche
Messunsicherheit kann geplante ON-Abschnitte in der Ausführung sperren oder
verkürzen; die Zeitspur bleibt ausdrücklich eine Prognose. Die tatsächliche Einsparung
an Eigenverbrauch muss an der Anlage gemessen werden: 0 W Entladelimit ist
nicht gleichbedeutend mit einem vollständig ausgeschalteten Wechselrichter.

## Nachweise

- `tests/core/test_reserve_priority.py`: ganze ON-Abschnitte, Standby-Bilanz,
  kleines ungenutztes Budget und DC-Vorrang.
- `tests/core/test_reserve.py`: quantisierte AC-Vorbereitung, PV-Quantile,
  Teilschritte und belegte zusätzliche Export-Restmengen.
- `tests/core/test_live_ac.py`: Grenze des vollen 35-Sekunden-Budgets,
  sofortige Abschaltung und unveränderte Zeitverträge.
- `tests/ha/test_coordinated_actuation.py`: ausschließlich 0/configured-max
  bei unterschiedlichen Maximalleistungen, einschließlich alter Zwischenwerte.
- `tests/ha/test_live_ac_runtime.py`: tatsächliche Folge 2300 → 0 bei
  kleinem Restbudget, Quellenwechsel, Messausfall und verspätete Bestätigung.

Die Topologie-Goldens verwenden die klassische Reserve-off-Politik und bleiben
unverändert. Änderungen der Reserve-Testbilanzen sind die beabsichtigte Folge
der vollständig freigegebenen Zeitabschnitte, keine abgesenkten Schutzgrenzen.
