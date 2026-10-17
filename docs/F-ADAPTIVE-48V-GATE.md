# Adaptive 48-V-Zuschaltung und SOC-Kalibrierung

Implementiert ab 0.59.0. Maßgeblicher Betriebsvertrag:
[CURRENT_CONTRACTS](CURRENT_CONTRACTS.md#adaptive-48-v-zuschaltung-ab-0590).

## Anlass

Die wirtschaftliche Reservehaltung forderte beide verfügbaren Netzteile an,
auch wenn die Batteriespannung über der 48-V-Netzteilspannung lag. Die Physik
bilanzierte dabei korrekt null Lieferung, der Schalter blieb jedoch an.
Die bisherige Kalibrierung sammelte lediglich zwei dauerhafte SOC-Extremwerte.
Sie konnte Lastabhängigkeit und eine bis zur BMS-Rekalibrierung schrittweise
wandernde SOC-Anzeige nicht abbilden und beeinflusste die Planung nicht.

## Zuständigkeiten

- `core/voltage_gate.py` projiziert die elektrische Freigabe auf den zukünftigen
  SOC. `VoltageGateBand` in `core/model.py` ist unveränderlich und replayfähig.
- `gate_learning.py` speichert begrenzte Beobachtungen und berechnet aktuelle,
  altersgewichtete SOC-Koordinaten um die konfigurierten Spannungsschwellen.
- `voltage_gate.py` prüft frische HA-Messungen und hält die Live-Hysterese mit
  Einschalt-/Ausschaltverzögerung. Es schaltet keine Aktoren selbst.
- `coordinated_supply.py` bleibt alleiniger Quellenbesitzer. Der schnelle
  Fünfsekundenpfad darf diesen Besitzer erneut aufrufen; alte und nach einem
  Await überholte Anforderungen werden erneut geprüft.

Ein Unterstützungsbedarf bleibt über `support_dc48_requested` erhalten, auch
wenn die physische Schaltprognose zunächst OFF ergibt. Stundenaggregation
behält die Anforderung des ersten Teilschritts. Der schnelle Live-Pfad bekommt
diesen Bedarf statt einer dauerhaften OFF-Anweisung. Ein ausgeschaltetes
48-V-Netzteil darf weder die 24-V-Quelle entfernen noch selbst den Inverter
freigeben. Bei Bedarf null erfolgt kein wirtschaftlicher Netzladestart.

## Lernen und Grenzen

Lernziel ist die vom BMS **gemeldete** SOC-Koordinate einer Spannung, keine
Korrektur des BMS und keine universelle Ruhespannungskennlinie. Vorzeichengetrennte
Strombereiche verhindern die Vermischung von Ladung und Entladung. Die Gültigkeit
wird zusätzlich auf den beobachteten Strombereich mit derselben kleinen Toleranz
wie die Stabilitätsprüfung begrenzt. Es wird nicht in unbekannte Lastbereiche
extrapoliert. Widersprüchliche Schwellen dürfen die Hysterese nicht umkehren.

Zeitgewichtete Mediane folgen wiederholter schrittweiser Verschiebung. P10–P90
beschreibt die beobachtete Streuung und ist kein kalibriertes Konfidenzintervall.
Jede Schwelle braucht ausreichend nahe Beobachtungen; bei knapper Evidenz bleibt
sie unbekannt. Die älteste noch benötigte Schwellenevidenz begrenzt die Gültigkeit
auch innerhalb der zukünftigen Prognose.

Lernen ist ausschließlich bei bestätigt ausgeschaltetem Netzteil erlaubt.
Damit kann die vom Netzteil geklemmte Spannung keine falsche Drift lehren. Es
gibt keine aktiven Messproben oder Testentladungen. Ein verdächtiger SOC-Sprung
beginnt eine neue Epoche und entwertet bestehende wirtschaftliche Freigaben.
Normale Drift wird spätestens mit der nächsten regulären Planung berücksichtigt.

Ein Kaltstart oder fehlende Batterieleistungsmessung verwendet die konfigurierte
SOC-Ersatzschwelle nur für die Bereitschaftsprognose. Ohne gültige Lernwerte wird
keine zukünftige Stützenergie erfunden. Ein gelerntes offenes Gate erlaubt im
nominalen Modell höchstens direkte Lastdeckung, begrenzt durch das Netzteil.
Es begründet keine zusätzliche Netzladung. Pessimistische Szenarien erhalten
weiterhin keine ungemessene PSU-Leistung. Live haben aktuelle Spannung und die
bestehenden Schutz-/Manual-Regeln Vorrang vor jeder gelernten SOC-Schwelle.

## Nachweise und Golden-Diff

- `tests/core/test_voltage_gate.py`: Live-/Zukunftsgrenze, unabhängige 24-V-Last,
  Lastbereich, Hysterese, kalte/veraltete Evidenz, Stressrechnung, keine erfundene
  Ladung, physische Schaltspur und Replay.
- `tests/ha/test_gate_learning.py`: Drift, getrennte Betriebsrichtungen,
  Rekalibrierung, Schalttransienten, PSU-Klemmung, Alterung, Konfigurationswechsel,
  beschädigte Persistenz und rückwärts eintreffende Ereignisse.
- `tests/ha/test_voltage_gate_runtime.py`: virtuelle Fristen, fehlende/falsche
  Messwerte, Neuplanung nach Rekalibrierung, geordnete Quellenübergänge,
  verspätete Bestätigung, schneller Wiedereinstieg und Wiederherstellung.
- Frontend-Test: Schaltgrund, Bereitschaft und gelernter SOC bleiben getrennt.

Drei neue Golden-Szenarien ergänzen die 16 unveränderten Altszenarien. Für dieselbe
Dunkelprognose bedeutet frühere gelernte Stützung mehr direkten DC-Netzbezug und
weniger Batterieentladung: kalter Stand 6,871 kWh / 41,03 % minimaler SOC,
gelernte 75-%-Grenze 8,720 kWh / 74,95 %, nach Verschiebung auf 78 %
8,884 kWh / 77,96 %. Die zusätzliche Energie erhält SOC und ist beabsichtigt;
sie finanziert keine gezielte Netzladung. Erzeugt mit `scripts/gen_golden.py`.
