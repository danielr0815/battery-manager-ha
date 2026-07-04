# Battery Manager — Anforderungsanalyse und Überarbeitung

> Status: **Entwurf in Diskussion** (Stand 2026-07-03)
> Dieses Dokument hält den analysierten Ist-Zustand, die erkannten Schwächen und die
> zu klärenden Anforderungen für die Überarbeitung des Steuerungsalgorithmus fest.

## 1. Ist-Zustand (aus dem Code rekonstruiert)

### 1.1 Modelliertes System

Der Simulationskern modelliert folgende Topologie (aus `energy_flow.py` abgeleitet):

```
PV ──(AC-seitig!)──┐
                   ├── AC-Verbraucher (Basis + variabel + Zusatzlast)
Netz ──────────────┤
                   │
             Charger (AC→DC, 92 %)          Inverter (DC→AC, 95 %, min. SOC)
                   │                               │
                   └────────── Batterie ───────────┘
                                  │
                            DC-Verbraucher
```

**Wichtig:** Im Code wird die PV-Produktion auf der **AC-Seite** bilanziert
(`ac_balance = pv - ac_consumption`). Die Batterie wird ausschließlich über den
Charger (AC→DC) geladen. Das README behauptet dagegen „PV to DC Load: direct DC
consumption has highest priority" — **Code und Doku widersprechen sich**.
→ Zu klären: tatsächliche Hardware-Topologie (Frage F1).

### 1.2 Eingaben und Ausgaben

**Eingaben** (via HA-Entitäten): aktueller SOC (%), PV-Tagesprognosen heute/morgen/übermorgen (kWh).
**Interne Modelle:** stündliche PV-Verteilkurve (Morgen-/Nachmittagsfenster, Ratio),
statische Lastprofile (Basis + variables Zeitfenster) für AC und DC.

**Ausgaben** (HA-Entitäten): SOC-Schwelle (%), Inverter-Status (an/aus),
Min-/Max-SOC-Prognose, Stunden bis Max-SOC, Entladeprognose, Zusatzlast-Status,
Netz-Import/Export der Prognoseperiode.

### 1.3 Kernalgorithmus („Maximum-Based Controller")

1. Simuliere SOC-Verlauf stündlich von jetzt bis **08:00 in zwei Tagen**.
2. Bestimme `min_soc_forecast` — aber nur bis zu dem Zeitpunkt, an dem der SOC
   erstmals das Ziel (`target_soc`, Default 85 %) erreicht.
3. Schwelle: `threshold = current_soc − (min_soc_forecast − min_battery_soc) + forced_charger_soc`
   begrenzt auf `[max(batterie_min, inverter_min) … min(target_soc, current_soc)]`.
4. `inverter_enabled = current_soc > threshold` → Binary Sensor für die reale Steuerung.

### 1.4 Zusatzlast-Logik (`_calculate_additional_load_optimization`)

- Stundenweise Iteration; solange die Last inaktiv ist, prüft ein „Safety Check",
  ob mit dauerhaft aktiver Zusatzlast der SOC bis zum Erreichen des Ziel-SOC nie
  unter das Inverter-Minimum fällt. Wenn ja → **sofort aktivieren**.
- Deaktivierung nur, wenn **beides** gilt: >50 % der Zusatzlast kommt aus der
  Batterie **und** der Ziel-SOC ist aktuell erreicht.

## 2. Erkannte Schwächen des Algorithmus

### 2.1 Zusatzlast wird zu früh/zu lange aktiviert (vom Nutzer bestätigtes Problem)

1. **Aktivierung prüft nicht, ob Überschuss vorliegt.** Der Safety Check bewertet
   nur „SOC bleibt über Minimum und erreicht irgendwann das Ziel". Er aktiviert
   die Last also auch dann, wenn sie aktuell komplett aus Batterie/Netz gespeist
   wird — Hauptzweck „Überschussverwertung" wird nicht geprüft (controller.py:743–794).
2. **Safety Check endet beim ersten Ziel-Erreichen.** Alles danach ist ungeprüft
   (controller.py:952–962): Erreicht der SOC morgen Mittag kurz das Ziel, ist der
   Absturz morgen Abend unter das Minimum unsichtbar.
3. **Deaktivierung erfordert `current_soc >= target`.** Fällt der SOC nach der
   Aktivierung (Ziel nie erreicht), bleibt die Last aktiv, selbst wenn sie zu 100 %
   aus der Batterie gespeist wird — bis der SOC das Inverter-Minimum erreicht und
   der normale Verbrauch aus dem Netz gekauft werden muss (controller.py:823–830).
   → **Exakt das vom Nutzer beobachtete Fehlverhalten.**

### 2.2 Strukturelle Probleme

- **Simulierte Politik ≠ angewendete Politik.** Die Simulation nimmt an, der
  Inverter läuft, sobald SOC > Inverter-Minimum (20 %). Real wird der Inverter
  aber über die berechnete Schwelle geschaltet. Die Prognose simuliert also ein
  anderes Systemverhalten als das, das die Prognose selbst herbeiführt
  (Rückkopplung nicht modelliert) → unzuverlässige/oszillierende Schwellwerte.
- **Heuristische Schwellenformel** ohne klar definierte Zielgröße. Es wird nichts
  optimiert (keine Kosten-/Nutzenfunktion); die Formel ist eine Momentaufnahme-Heuristik.
- **Doppelte Simulationen mit inkonsistenten Annahmen:** `_calculate_total_grid_flows`
  simuliert ohne Zusatzlast-Fahrplan erneut (controller.py:393–460) und richtet die
  Folgestunden nicht an Stundengrenzen aus — Import/Export-Sensoren passen nicht
  zu den übrigen Ausgaben.
- **Mutierender globaler Zustand** (`set_additional_load_active` am geteilten
  `ac_consumer`, SOC-Manipulation an `battery`) macht die Logik fehleranfällig
  und schwer testbar; viel toter Code (`_test_additional_load_activation`,
  `_simulate_with_additional_load_schedule`, `_project_soc_*`).
- **Statische Lastprofile** (Basis + ein Zeitfenster) bilden reale Haushalte grob ab;
  keine Nutzung von HA-Historie, keine Wochentag-/Saisonprofile.
- **PV-Stundenkurve** ist ein einfaches Zwei-Fenster-Modell; stündliche
  Prognosedaten (z. B. Solcast/Forecast.Solar liefern Stundenwerte) werden nicht genutzt.

### 2.3 Bekannte Bugs der HA-Schicht (separat vom Algorithmus)

- `coordinator.py:91`: `_listeners_setup` wird nie `True` → sofortige Updates bei
  Entity-Änderungen sind wirkungslos, nur 5-Minuten-Polling. Listener werden beim
  Unload nicht entfernt.

## 3. Geklärte Rahmenbedingungen (Betreiber-Antworten, 2026-07-03)

- **Topologie bestätigt:** PV ist AC-seitig gekoppelt. Das Codemodell
  (PV → AC-Bilanz, Batterie nur über AC→DC-Charger, DC-Verbraucher an der
  Batterie, Entlade-Inverter DC→AC) entspricht der realen Anlage.
- **Optimierungsziel:** Maximaler Eigenverbrauch und minimaler Netzbezug.
  Einspeisung wird **nicht vergütet** — exportierte Energie ist verloren.
  Keine dynamischen Tarife.
- **Steuerkette:** HA-Automationen schalten real sowohl den Entlade-Inverter
  (über Inverter-Status/SOC-Schwelle) als auch die Zusatzlast
  (über `additional_load_status`).
- **Zusatzlasten (Zielbild, heute nur 1 pauschale Last):**
  - **Last 1 + Last 2:** je eine Fossibot F2400 Powerstation (2 kWh) als
    zusätzliche ladbare Speicher; beide sind in Home Assistant integriert
    (SOC/Schaltbarkeit vorhanden).
  - **Last 3:** Luftentfeuchter im Gemeinschaftskeller, optional (reine
    Überschussverwertung, kann jederzeit unterbrochen werden).
- **Haushaltsgeräte:** Geschirrspüler und Waschmaschine sollen berücksichtigt
  werden:
  1. Wird erkannt, dass ein Gerät gestartet wurde, soll dessen erwarteter
     Restverbrauch sofort in die Prognose einfließen.
  2. Ausbaustufe: Der Nutzer kann Geräte als „darf starten, wenn Überschuss
     verfügbar" markieren → das Plugin signalisiert/startet, wenn der Lauf ohne
     Netzbezug möglich ist.

## 4. Anforderungen an die überarbeitete Lösung (Entwurf)

### 4.1 Zielfunktion

- **Z1:** Primäres Ziel ist die Minimierung des Netzbezugs UND der Einspeisung
  über den Prognosehorizont (Export ist wertlos, Import kostet). Da beide Ziele
  aus derselben Bilanz folgen: Minimiere `grid_import + grid_export` (gewichtbar).
- **Z2:** Harte Nebenbedingung: Der normale Hausverbrauch hat immer Vorrang vor
  allen optionalen Lasten. Optionale Lasten dürfen nie dazu führen, dass für den
  Normalverbrauch Netzstrom gekauft werden muss.
- **Z3:** Die Batterie-Betriebsgrenzen (min/max SOC, Inverter-Minimum) sind
  harte Nebenbedingungen über den GESAMTEN Horizont (nicht nur bis zum ersten
  Erreichen eines Ziel-SOC).

### 4.2 Überschusslast-Management (neu)

- **L1:** Unterstützung mehrerer optionaler Lasten mit Prioritätsreihenfolge
  (konfigurierbar), statt einer pauschalen Zusatzlast.
- **L2:** Je Last konfigurierbar: Leistung (W), Typ (unterbrechbar wie
  Entfeuchter / Energiemenge bis „fertig" wie Powerstation-Ladung),
  optional SOC-Entität (Fossibot) zur Bestimmung der Restenergie.
- **L3:** Eine optionale Last wird nur aktiviert, wenn ihr Verbrauch im
  Aktivierungszeitraum (nahezu vollständig) aus PV-Überschuss gedeckt ist, der
  sonst exportiert würde oder wegen voller Batterie verloren ginge —
  konfigurierbare Toleranz (z. B. max. X % aus Batterie).
  *Erweiterung v2 (2026-07-04):* Auch zeitversetzte Deckung über die Batterie
  ist zulässig („vorsorgliches Platzschaffen"), wenn die Simulation über den
  gesamten Horizont beweist, dass kein zusätzlicher Netzimport entsteht und
  der verlorene Überschuss um mindestens (1 − Toleranz) × Lastenergie sinkt
  (Details: ALGORITHM.md D-A4 v2).
- **L4:** Deaktivierung, sobald die Überschussbedingung nicht mehr erfüllt ist —
  unabhängig davon, ob ein Ziel-SOC erreicht wurde.
- **L5:** Pro Last eine eigene HA-Entität (Schaltempfehlung/Status), damit
  Automationen sie einzeln schalten können.

### 4.3 Haushaltsgeräte (neu)

- **G1:** Konfigurierbare Geräte (Waschmaschine, Geschirrspüler) mit
  Erkennungs-Entität (z. B. Steckdosen-Leistung oder Status-Sensor) und
  hinterlegtem Restlauf-Verbrauchsprofil (kWh, Dauer).
- **G2:** Läuft ein Gerät, wird sein erwarteter Restverbrauch in die
  AC-Lastprognose eingerechnet.
- **G3 (Ausbaustufe):** „Startfenster-Empfehlung": Entität je Gerät, die
  anzeigt, ob ein kompletter Lauf ab jetzt (oder in Stunde X) ohne Netzbezug
  möglich wäre.

### 4.4 Prognose & Modell

- **P1:** Konsistente Politik-Simulation: Die Simulation muss dasselbe
  Schaltverhalten annehmen, das die berechneten Ausgaben real bewirken
  (Rückkopplung Schwelle → Inverter → SOC-Verlauf).
- **P2:** Eine einzige Simulation pro Update liefert alle Ausgaben konsistent
  (keine parallelen Simulationen mit abweichenden Annahmen).
- **P3 (Ausbaustufe):** Stündliche PV-Prognosen direkt nutzen, wenn verfügbar
  (z. B. Solcast/Forecast.Solar-Stundenwerte), statt Tageswerte über eine
  statische Kurve zu verteilen.
- **P4 (Ausbaustufe):** Lastprofile aus HA-Historie lernen statt statischer
  Basis-/Zeitfensterwerte.

### 4.5 Qualität

- **Q1:** Der Simulationskern bleibt HA-unabhängig und pur (keine Seiteneffekte,
  keine mutierten Shared-Objects) → deterministisch testbar.
- **Q2:** pytest-Testsuite (Kern + HA-Schicht), lauffähig in CI.
- **Q3:** Bekannte Bugs der HA-Schicht werden behoben (Entity-Listener,
  Listener-Cleanup).

## 5. Geklärte Detailfragen (2026-07-03)

- **D1 Priorität:** Lasten dürfen **parallel** laufen, wenn der Überschuss für
  mehrere reicht; die Prioritätsreihenfolge entscheidet nur bei Knappheit.
- **D2 Fossibots:** Nur laden, gedrosselt auf **300 W**. Die Energie versorgt
  direkt angeschlossene Verbraucher (z. B. PC) — keine Rückspeisung ins
  Hausnetz. Die **tatsächliche Ladeleistung soll aus dem Feedback der
  HA-Entitäten gelesen** und im Algorithmus verwendet werden (gemessene
  Leistung statt Festwert; Festwert nur als Fallback/Startschätzung).
- **D3 Geräte:**
  - Geschirrspüler: erkennbar über Steckdosen-Leistungsmessung; hat WLAN und
    kann evtl. direkt integriert werden.
  - Waschmaschine: bereits integriert via LG ThinQ
    (`F_V8_Y___W.B_2QEUK`, DEVICE_WASHER).
- **D4 Migration:** **Breaking Change ist akzeptiert** — Config Flow und
  Datenmodell dürfen neu aufgebaut werden; einmalige Neukonfiguration ist ok.

### Ergänzungen aus der Algorithmus-Diskussion (siehe ALGORITHM.md)

- **Topologie-Detail:** Zwei DC-Ebenen — 48-V-Batterie und 24-V-Schiene über
  DC/DC-Wandler. Notfall-Stützpfade: 48-V-Netzteil mit fixer Leistung
  (Default 60 W, Parameter) und 24-V-Netzteil als DC/DC-Ersatz.
- **N1:** Beide Stützpfade werden vom Plugin **direkt geschaltet**
  (konfigurierte Switch-Entitäten) und als letzte Eskalationsstufe zum Schutz
  der Batterie in der Simulation berücksichtigt (Details: ALGORITHM.md D-A9).
- **N1a (Make-before-break, 2026-07-04):** Die 24-V-Schiene darf nie ohne
  Quelle sein: Das 24-V-Netzteil wird erst aktiviert und nach kurzem Delay
  der DC/DC-Wandler abgeschaltet; umgekehrt wird erst der DC/DC-Wandler
  aktiviert und nach dem Delay das Netzteil abgeschaltet. Delay
  konfigurierbar (Default 3 s); bei nicht bestätigter neuer Quelle wird die
  Umschaltung abgebrochen (alte Quelle bleibt an).
- **N2 (Ausbaustufe):** Risikobewertung aus Jahreszeit/vergangener
  Prognosegüte → dynamischer SOC-Puffer statt fixer 5 %.
- **Entschiedene Stellschrauben:** Gleichstand → „Nutzen" (Batterie vor
  starker Sonne leerfahren hat Vorrang; Backstop N1); Hysterese ±1 % mit max.
  1 Inverter-Schaltung/min; SOC-Puffer +5 %; Zusatzlast-Batterieanteil
  Default 15 % (0–50 % konfigurierbar), Mindest-Ein-/Ausschaltdauer 30 min.

### Ergänzte Anforderungen aus D1–D3

- **L6:** Parallelbetrieb mehrerer Überschusslasten; Priorität greift nur bei
  knappem Überschuss.
- **L7:** Je Last optional eine Leistungs-Feedback-Entität (z. B. Fossibot-
  Ladeleistung): Bei aktiver Last wird die gemessene Leistung (geglättet) als
  Lastleistung verwendet; konfigurierter Nennwert dient als Fallback.
- **L8:** Je Last optional eine SOC-/Fertig-Entität (Fossibot-SOC), um „Last
  ist gesättigt" zu erkennen (voll geladen → Last steht nicht mehr zur
  Verfügung und wird übersprungen).
