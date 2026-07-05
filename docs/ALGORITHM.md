# Battery Manager — Algorithmus-Design im Detail

> Status: **In Diskussion** (Stand 2026-07-03)
> Vertiefung zu [STRATEGY.md](STRATEGY.md); Entscheidungspunkte D-A1 … D-A8.
> Ein lauffähiger Prototyp der Kernlogik existiert (Szenario-Ergebnisse in §3).

## 1. Ablauf eines Planungslaufs

Alle ~5 Minuten (bzw. bei Änderung einer Eingangs-Entität):

```
1. Eingangsreihen bauen (Stundenraster bis Ende der Prognosedaten):
   PV(h)        aus Tagesprognosen × Verteilkurve (später: Stundenprognosen)
   AC-Last(h)   Basisprofil + erkannte Geräte-Restläufe (Waschmaschine/Spüler)
   DC-Last(h)   Basisprofil
2. Schwellwertsuche:
   für T in [max(batt_min, inv_min) … batt_max], 1-%-Schritte:
       Trajektorie = simulate(T)        # Politik: Inverter an ⇔ SOC > T
       kosten(T) = Import(T) − f·SOC_Ende(T) + w_e·Export(T)
   T* = argmin kosten     (Gleichstand → siehe D-A1)
3. Überschuss-Allokation auf T*-Trajektorie:
   Export-Stunden identifizieren → Lasten nach Priorität zuteilen
   (parallel, wenn Überschuss reicht); jede Zuteilung per Re-Simulation
   gegen Z2 (kein Zusatz-Import) und Z3 (SOC-Grenzen, ganzer Horizont) geprüft
4. Geräte-Advisor: „Lauf ab jetzt ohne Zusatz-Import möglich?" je Gerät
5. Ausgaben: T*, Inverter-Empfehlung (mit Hysterese), Lastpläne/-empfehlungen,
   Min/Max-SOC, Import/Export-Prognose — alles aus EINER Trajektorie
```

## 2. Entscheidungspunkte

### D-A1 Terminalwert & Gleichstands-Regel (wichtigste Stellschraube)

Am Horizontende verbleibende Batterieenergie muss bewertet werden, sonst
„verheizt" der Optimierer sie grundlos bis zum Minimum.

- **Terminalwert:** `f = η_entladen · η_inverter ≈ 0,92` — gespeicherte Energie
  ist so viel wert wie der Import, den sie später ersetzt. Damit ist
  „jetzt entladen vs. aufheben" bei fehlendem PV-Überschuss kostenneutral —
  mathematisch korrekt, denn genau so ist es real.
- **Gleichstands-Regel entscheidet dann das Verhalten:**
  - **(a) höhere Schwelle bevorzugen („Halten")**: robust gegen
    Prognosefehler (volle Batterie schützt vor Import, Export ist eh wertlos).
    Konsequenz: An trüben Tagen kauft das Haus Netzstrom, obwohl die Batterie
    z. B. 60 % hat — sie wird erst später (durch DC-Last/nächste Lücke) genutzt.
    Sieht „dumm" aus, ist aber kostenneutral. → Prototyp-Szenario S2.
  - **(b) niedrigere Schwelle bevorzugen („Nutzen")**: Haus läuft bevorzugt aus
    der Batterie („gefühlter Eigenverbrauch"), Risiko: bei unerwartetem
    Verbrauch ist die Reserve weg.
  - **Entscheidung des Betreibers (2026-07-03): (b) Nutzen.** Begründung:
    1. Es soll vermieden werden, dass die Batterie bei der nächsten starken
       Sonne nicht weit genug entladen ist (Energie würde verschenkt).
    2. Als Backstop existiert eine **Notfall-Stützung** (§D-A9): Netzteile
       können die DC-Ebenen übernehmen und verhindern hart, dass die Batterie
       in den unerlaubten Bereich fällt.
    3. Der Puffer aus D-A3 (+5 %) fängt normale Prognosefehler.
    Zusätzlich bleibt der Export-Malus `w_e = 0,05` als zweiter Tiebreaker.
  - **Ausbaustufe (Phase 4):** Risikobewertung aus Jahreszeit und
    Prognosegüte der Vergangenheit (beobachteter Forecast-Fehler) → Puffer
    dynamisch statt fix 5 %.
  - **Erkenntnis aus der Kern-Implementierung (2026-07-04):** Bei Tagen ohne
    nennenswerten PV-Überschuss ist „Halten" meist **strikt** günstiger, kein
    Gleichstand: Die Batterie ist auf dem direkten DC-Pfad (η≈0,97) mehr wert
    als über den Inverter (η≈0,92 + Standby), und eine leere Batterie zwingt
    die DC-Schiene in teuren Netzbezug über den Charger (Faktor 1/0,92). Der
    Optimierer findet das selbst; der „Nutzen"-Tiebreak greift nur bei echten
    Gleichständen (er wählt dann die untere Kante des kostengleichen
    Plateaus). Das gewünschte „vor starker Sonne Platz schaffen" entsteht
    automatisch, sobald Überschuss prognostiziert ist (Szenario S1).

### D-A2 Hysterese & Schaltstabilität

Problem: `SOC > T*` wird alle 5 min neu bewertet; nahe der Schwelle droht
Flattern des realen Inverters (und T* selbst kann zwischen fast gleichwertigen
Kandidaten springen).

- **Entscheidung des Betreibers (2026-07-03):** Der Inverter verträgt häufiges
  Schalten; Limit nur **max. 1 Schaltvorgang pro Minute**.
  1. Ausgabe-Hysterese: an bei `SOC ≥ T*+1 %`, aus bei `SOC ≤ T*−1 %`,
     dazwischen letzten Zustand halten.
  2. Schwellen-Trägheit: neues T* nur übernehmen, wenn es ≥ 2 % vom alten
     abweicht oder die Kosten um > ε besser sind (verhindert Springen zwischen
     gleichwertigen Kandidaten).
  3. Mindest-Schaltabstand für die Inverter-Empfehlung: **1 min**.

### D-A3 Umgang mit Prognose-Unsicherheit

- Optionen: (a) PV-Pessimismusfaktor (Prognose × 0,9), (b) SOC-Puffer über dem
  Minimum (Planung rechnet mit `min+X %`), (c) nichts — häufiges Replanning
  korrigiert.
- **Entscheidung des Betreibers (2026-07-03): (b) mit X = 5 %** (konfigurierbar)
  plus (c). Ein Pessimismusfaktor (a) verzerrt auch die Lastallokation
  (Überschuss wird systematisch unterschätzt → Fossibots laden zu selten).

### D-A4 Zusatzlasten: Toleranz & Taktung

- **Batterieanteil-Toleranz:** **Entscheidung des Betreibers: etwas Toleranz
  erlauben.** Default **15 %** Batterieanteil pro Last (konfigurierbar 0–50 %).
  Die harte Bedingung „kein zusätzlicher Netzimport über den Horizont" (Z2)
  bleibt davon unberührt und wird weiterhin per Re-Simulation geprüft — die
  Toleranz erlaubt nur, dass eine Last kurzzeitig anteilig aus der Batterie
  läuft (z. B. Wolkenlücke), solange die Bilanz stimmt.
- **v2 — zielbasiertes Gate (Entscheidung des Betreibers, 2026-07-04):**
  Zusätzlich zu den direkten Überschussstunden (Pass 1) gibt es einen
  zweiten Allokationspass: Eine Last darf auch in einer Stunde **ohne**
  direkten Überschuss laufen (z. B. Vorladen vor einer kurzen, starken
  Mittagsspitze), wenn die Re-Simulation über den gesamten Horizont beweist:
  (a) der Netzimport steigt nicht, (b) der SOC-Puffer hält, und (c) der
  verlorene Überschuss sinkt um mindestens `(1 − Toleranz) × Lastenergie` —
  die Energie stammt also nachweislich, zeitversetzt über die Batterie, aus
  sonst verlorenem Überschuss. Energiebegrenzte Lasten (Fossibots) kommen
  nur mit Restbudget aus Pass 1 in Pass 2 (Sättigung im Sonnenfenster hat
  Vorrang, spart Batteriezyklen). Das schädliche Szenario „5 Uhr, 50 % SOC"
  wird durch (a) automatisch verworfen (Batterie würde das
  Inverter-Minimum reißen → Import); das nützliche Szenario „kurzes
  Sonnenfenster, hoher SOC" wird automatisch erlaubt.
- **Merge-Prinzip (Betreiber-Einsicht, 2026-07-04):** Alle Gate-Bedingungen
  sind **Differenzvergleiche** zweier vollständiger Trajektorien. Sobald
  beide Varianten (mit/ohne Laststunde) den Max-SOC erreichen, sind sie ab
  dort identisch — alles nach dem „Merge-Punkt" kürzt sich automatisch
  heraus. Ein explizites Abschneiden der Simulation ist deshalb unnötig.
  Konsequenz für die min-SOC-Bedingung (b): Sie prüft **relativ** — eine
  Laststunde wird nur verworfen, wenn sie den minimalen SOC unter den Puffer
  drückt UND gegenüber dem Plan ohne diese Stunde verschlechtert. Ein
  SOC-Tief am trüben Horizontende, das in beiden Varianten identisch
  auftritt, kann heutige Überschussstunden nicht mehr blockieren.
- **v3 — Mindestlaufzeit-ehrliche Bewertung + latest-first (Betreiber-
  Entscheidung, 2026-07-05):** Zwei Korrekturen nach dem Nachtlade-Vorfall
  vom 05.07. (04:59:50, ~250 Wh real für 5 Wh Plan):
  1. Jede Aktivierungsentscheidung wird mit der Energie bewertet UND
     simuliert, die der Executor real erzwingt: `Leistung ×
     max(Slot-Restdauer, Mindestlaufzeit)`, zeitlich über Slot-Grenzen
     verteilt („Spill", alle betroffenen Slots werden gemeinsam geplant).
     Damit kann der degenerierte Slot 0 (angebrochene Stunde, in Minute :59
     nur 1/60 h) keine Mini-Energien mehr durch die Gates schleusen, die
     die reale Mindestlaufzeit anschließend um Faktor 50 übertrifft. Das
     Sättigungs-Gate ist zusätzlich auf die Nennleistung gefloort, damit
     ein leerer/verfallener Feedback-EMA es nicht schwächt.
  2. Pass 2 läuft **latest-first** (späteste begründbare Stunde zuerst):
     Zusatzlasten werden so spät wie möglich aktiviert, aber noch
     rechtzeitig, dass kein Überschuss verloren geht. Nachholen mit
     besserer Information schlägt die frühe Wette auf die Prognose;
     Vorziehen ist nur gerechtfertigt, wenn das Überschussfenster
     leistungsbegrenzt ist. Slots nach dem letzten Export-Slot können
     Bedingung (c) nie erfüllen und werden übersprungen; ohne Export im
     Horizont entfällt Pass 2 komplett.
- **Taktung:** Planung im Stundenraster; reale Empfehlung mit
  Mindest-Ein-/Ausschaltdauer (Default 30 min) — schont Geräte und Relais
  und geht seit v3 als committed-Energie in die Bewertung ein.
- **Sättigung:** Fossibot-SOC ≥ konfigurierbarem Ziel (Default 100 %) → Last
  gilt als gesättigt und wird übersprungen (L8). Restenergiebedarf
  `= (Ziel−SOC) × 2 kWh`, geplante Ladeleistung aus Feedback-Entität
  (EMA-geglättet), Fallback 300 W (L7).

### D-A5 Geräteprofile (Waschmaschine/Geschirrspüler)

- v1: pro Gerät konfiguriert: Erkennungs-Entität (Status oder Leistung),
  Programm-Energie (kWh) und -Dauer (h). Läuft das Gerät, wird die Restenergie
  gleichverteilt über die Restdauer der AC-Prognose aufaddiert (G2).
- LG ThinQ (Waschmaschine) liefert Restzeit → genauere Verteilung möglich.
- Startfenster-Empfehlung (G3): Testeinfügung des kompletten Profils ab jetzt;
  `an`, wenn Import-Delta = 0 (und SOC-Grenzen halten).

### D-A6 Horizont

- Bisher: bis 08:00 übermorgen (willkürlich). Mit Terminalwert ist das
  Horizontende unkritisch. **Empfehlung:** volle verfügbare Prognose nutzen
  (bis Mitternacht von Tag 3).

### D-A7 Zeitraster

- **Empfehlung:** Stunden in v1 (Datenlage: Tagesprognosen). Kern so bauen,
  dass das Raster ein Parameter ist → 15-min-Raster in Phase 4 möglich.

### D-A8 Verhalten bei Datenausfall

- Wie bisher: letzte gültige Werte mit Altersgrenzen (SOC 1 h/6 h, Prognose
  24 h/72 h). **Empfehlung ergänzend:** Bei Überschreiten wird der letzte Plan
  eingefroren und nach weiteren 2 h werden alle Empfehlungs-Entitäten
  `unavailable` + Zusatzlasten-Empfehlung „aus" (fail-safe: lieber Überschuss
  verschenken als Netzbezug riskieren).

### D-A9 Notfall-Stützung der DC-Ebenen (neu, aus Betreiber-Antwort zu D-A1)

Die Anlage hat zwei DC-Ebenen: die **48-V-Batterie** und eine **24-V-Schiene**,
die normal über einen DC/DC-Wandler aus der Batterie versorgt wird. Für den
Notfall existieren zwei Stütz-Möglichkeiten:

1. **48-V-Stütznetzteil:** speist mit fixer Leistung (Default **60 W**,
   Parameter) auf die Batterieebene → kompensiert Grundlast, Batterie fällt
   nicht in den unerlaubten Bereich.
2. **24-V-Netzteil statt DC/DC:** übernimmt die 24-V-Verbraucher komplett aus
   dem Netz → entlastet die Batterie um die DC-Last.

**Design:** Das Plugin bekommt je Stützpfad eine Empfehlungs-Entität
(binary_sensor), die „an" geht, wenn die Simulation trotz Inverter-aus ein
Unterschreiten von `batt_min + Puffer` erwartet. Die Stützpfade gehen als
schaltbare Lasten/Quellen in die Simulation ein (48-V-Stützung = −60 W auf die
Batteriebilanz; 24-V-Netzteil = DC-Last → 0, Netzimport stattdessen).
Priorität: Stützung ist letzte Eskalationsstufe — normale Planung soll sie nie
brauchen; sie deckt Prognosefehler und Ausnahmesituationen ab.

**Entscheidung F-N1 (2026-07-03):** Beide Stützpfade sind über HA-Entitäten
schaltbar und das Plugin soll sie **direkt schalten** (Entitäts-IDs in der
Konfiguration; zusätzlich Status-Entitäten zur Transparenz). Anders als bei
Inverter/Zusatzlasten (Empfehlung + Nutzer-Automation) übernimmt die
Integration hier selbst die Schaltung — Schutzfunktion mit Vorrang.

**Ergänzung Make-before-break (2026-07-04, Betreiber-Anforderung):** Die
24-V-Schiene darf beim Umschalten nie ohne Quelle sein. Deshalb ist auch der
**48-V→24-V-DC/DC-Wandler** als Schalter konfigurierbar, und die Umschaltung
läuft sequenziert mit konfigurierbarer Verzögerung (Default 3 s):

- **Aktivierung Netzbetrieb:** 24-V-Netzteil EIN → Delay → DC/DC AUS.
- **Rückkehr Batteriebetrieb:** DC/DC EIN → Delay → 24-V-Netzteil AUS.
- **Fehlerfall:** Meldet die neu eingeschaltete Quelle nach dem Delay nicht
  „on", wird die Umschaltung abgebrochen — die bisherige Quelle bleibt an,
  der nächste Planungszyklus versucht es erneut (Mindest-Schaltabstand gilt).
- Ist kein DC/DC-Schalter konfiguriert, wird nur das Netzteil geschaltet
  (Annahme: Parallelspeisung/Dioden-Entkopplung ist zulässig).
- Beim Start übernimmt die Integration den realen Ist-Zustand der Schalter
  (wichtig nach HA-Neustart mit aktiver Stützung).

## 3. Prototyp-Ergebnisse (Stundenraster, Defaults der Integration)

Prototyp: `standalone_test/`-unabhängiges Skript; Batterie 5 kWh (5–95 %),
Inverter-Min 20 %, Charger 92 %/Inverter 95 %, Lasten: 2× Fossibot 300 W
(je 2 kWh Bedarf) + Entfeuchter 400 W, Priorität in dieser Reihenfolge.

| Szenario | Ergebnis |
|---|---|
| **S1** 20:00, SOC 80 %, morgen 14 kWh | T\* = 57 % → Batterie wird nachts ins Haus entladen (Platz schaffen), **Import 0**, Lasten laden 8–14 Uhr: Export sinkt 17,1 → 9,9 kWh |
| **S2** 20:00, SOC 60 %, morgen 1,5 kWh | T\* = 95 % („Halten", D-A1a): kein Inverter-Betrieb, Batterie als Reserve, Import 2,5 kWh (wäre bei jeder anderen Schwelle gleich oder schlechter) |
| **S3 = Nutzer-Fehlerszenario** 21:00, SOC 84 %, morgen 13 kWh | **Keine Last nachts aktiv** — Fossibots/Entfeuchter laufen erst 8–14 Uhr im echten Überschuss; Import bleibt 0. Alte Logik hätte die Last sofort aktiviert. |
| **S4** 11:00, SOC 93 %, sonnig | Lasten laufen **sofort** (Batterie fast voll, Überschuss da), Export 10,8 → 4,8 kWh |

Beobachtungen:

- Die Schwellwertsuche erzeugt genau das erwartete Verhalten „vor sonnigen
  Tagen Platz schaffen, vor trüben Tagen sparen" — ohne dass dieses Verhalten
  irgendwo einprogrammiert wäre; es folgt aus der Zielfunktion.
- Die Allokation verhindert konstruktionsbedingt das alte Fehlverhalten
  (S3): Lasten laufen nur in Stunden mit realem Überschuss.
- Restlicher Export in S1/S3/S4 ist physikalisch unvermeidbar (PV-Überschuss >
  Speicher + Lastaufnahme) — sichtbar im geplanten Sensor
  `verlorener Überschuss`.

## 4. Offene Punkte nach dieser Diskussion

- Bestätigung/Änderung der Empfehlungen D-A1 … D-A4 durch den Betreiber.
- Exakte Konfigurationsfelder je Last/Gerät → wird beim Config-Flow-Design
  (Phase 2/3) festgelegt.
