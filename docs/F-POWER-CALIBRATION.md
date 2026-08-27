# F-POWER-CALIBRATION — manuelle Bestimmung der Last-Planungsleistung

## Anlass und Ziel

Der normale robuste Leistungsschätzer lernt nur während einer vom Battery
Manager veranlassten Lastlaufzeit. Nach einer am Fossibot geänderten Laderate
kann deshalb bis zum nächsten geplanten Lauf weiterhin der alte Wert gelten.
Dieses Feature gibt dem Betreiber einen kurzen, bewusst netzstromfähigen
Messlauf und macht zugleich den in jedem Plan tatsächlich verwendeten
Leistungswert sichtbar.

## Sichtbarkeit

Jede Überschusslast erhält den Sensor **„Planungsleistung“** in Watt. Sein Wert
ist exakt der vom letzten veröffentlichten Plan verwendete Skalar. Das Attribut
`source` erklärt dessen Herkunft:

- `live` — robuster Istwert des laufenden normalen Lastlaufs,
- `learned` — persistierter Lernwert,
- `configured` — konfigurierte Defaultleistung,
- `saturated` — F5-Override einer gelatchten Last.

Die Prognosekarte zeigt denselben Wert neben der geplanten Energie in der
Last-Legende. Der Wert fährt außerdem als `planning_power_w` und
`planning_power_source` im Lastplan und im Attribut `loads` des
SOC-Prognosesensors mit.

## Bedienung und Geltungsbereich

Der Button **„Ladeleistung neu bestimmen“** existiert nur, wenn die Last

- energielimitiert ist,
- einen direkten Steuerschalter besitzt,
- einen Leistungssensor besitzt und
- nicht durch eine Speicher-Kaskade verwaltet wird.

`button.press` ist zugleich die Automationsaktion. Ein zweiter Druck während
eines laufenden Messlaufs bricht ihn ab. Je Battery-Manager-Config-Entry kann
nur eine Kalibrierung gleichzeitig laufen.

## Ablauf

1. Die Last muss zu Beginn tatsächlich inaktiv sein und ihr Leistungssensor
   unterhalb der gemeinsamen Standby-Bar liegen. Der Vorwert wird als Baseline
   gespeichert.
2. Die Kalibrierung übernimmt die Last exklusiv und schaltet sie sofort ein.
   Der normale Executor überspringt nur diese Last. Dieser explizite
   Betreiberlauf darf Planung, G4 und Strict-Surplus umgehen und bis zu vier
   Minuten Netzstrom beziehen. Nach Rückkehr der Schaltservices wartet der
   Lauf bis zu 30 Sekunden auf die tatsächlich veröffentlichten `on`-Zustände
   von Eingangsschalter und optionalem Lade-Gate; ein verzögert meldender
   Shelly gilt nicht vorschnell als fehlgeschlagen.
3. Die Messphase beginnt beim ersten Leistungssprung
   `abs(Ist − Baseline) >= 20 % × konfigurierte Defaultleistung`, spätestens
   aber 60 Sekunden nach dem Einschalten.
4. Nur eine **neue Veröffentlichung** des Leistungssensors kann ein Sample
   erzeugen; derselbe Sensorzustand wird niemals durch Coordinator-Polls
   vervielfacht. Zwischen zwei Samples liegen mindestens fünf Sekunden. Werte
   unterhalb der Standby-Bar werden verworfen.
5. Sobald mindestens vier Samples vorhanden sind **oder** die Messphase seit
   mindestens 90 Sekunden läuft und wenigstens ein Sample existiert, wird der
   Median des Buffers auf 0,1 W gerundet und atomar als neuer Lernwert
   gespeichert. Spätestens nach vier Minuten endet der Versuch erfolglos.
6. Bei Erfolg, Fehler oder Abbruch geht die Last an einen sofort frisch
   berechneten Plan zurück. Will dieser Plan sie weiter betreiben, bleibt sie
   nahtlos an und erhält wieder eine normale Laufdeadline; andernfalls wird sie
   ohne `min_runtime`-Verlängerung ausgeschaltet.

## Fehler- und Neustartsemantik

Ein erfolgloser oder abgebrochener Versuch verändert den alten Lernwert nicht.
Bestätigt ein Actor seinen `on`-Zustand auch nach 30 Sekunden nicht, scheitert
der Lauf vor der Messphase; fehlende Fossibot-Leistungstelemetrie dagegen wird
innerhalb des normalen Vier-Minuten-Budgets weiter abgewartet.
Der aktive Actor-Marker wird vor dem Einschalten synchron persistiert. Bei
Integration-Reload oder sauberem Home-Assistant-Neustart wird ein
unterbrochener Messlauf vor dem ersten normalen Plan sicher beendet. Gelingt
die anschließende Neuplanung nicht, schaltet ein direkter Fallback die Last aus;
der Recovery-Marker bleibt bis zur bestätigten Freigabe erhalten.
