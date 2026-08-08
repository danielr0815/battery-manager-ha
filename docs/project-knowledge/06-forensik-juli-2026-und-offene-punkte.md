# Forensik Juli 2026 & offene Punkte

**Stand: `main` @ v0.17.0 (Arbeitsstand 2026-07-31).** Dieses Dokument destilliert die
7-Tage-Entscheidungsforensik (17.–24.07.2026, 65-Agenten-Analyse mit
adversarialer Verifikation: 23 Befunde bestätigt/plausibel, 3 widerlegt) und
die daraus entstandene Fix-Serie F1–F11 (v0.16.0–v0.16.2) — inklusive dessen,
was **bewusst nicht** gebaut wurde und was noch offen ist. Es ist das
Gedächtnis der Woche: Wer ein Verhalten „komisch" findet, prüft zuerst hier, ob
es bereits analysiert, gefixt, verworfen oder als Watch-Item bekannt ist.

---

## 1. Kontext: die Woche in Zahlen (17.–24.07.2026)

Live-Versionen während der Woche: 17.07. v0.12.0 → 18.07. v0.13.0/v0.13.1/v0.14.0
→ ab 19.07. 12:21 v0.15.1 → 24.07. 19:13 v0.16.1 → 25.07. v0.16.2. Beobachtetes
Verhalten immer der damals laufenden Version zuschreiben!

- Export-Zähler der Woche: 21,3 kWh — **davon ~11,3 kWh Messartefakt**: Der
  Entfeuchter hängt außerhalb des EM540-Zählerkreises, seine Versorgung
  erscheint 1:1 als „Export". Echter Netz-Export ≈ 10,2 kWh, Import ≈ 2,2 kWh.
- Zerlegung der echten ~10,2 kWh: ~2,2 kWh unvermeidbarer Klipp-Rest bei
  laufenden Lasten; ~4,8 kWh Tank-voll-Sättigung des Entfeuchters (21. + 24.07.);
  ~2,5–3 kWh BM-/prognoseseitig vermeidbar (flacher Pre-Drain 21./24.07.,
  T*-Oszillation 24.07., Dwell-Race 23.07.).
- Positiv verifiziert: Strict-Surplus hielt (keine netzgekaufte Lastminute),
  Median-Learning robust (409 W trotz 2-W- und 1,5-kW-Ausreißern),
  Aktuierungslatenz 0,1–1,2 s, Seamless-Runs wirken seit v0.14.0, G4 seit
  v0.13.1 ohne einen einzigen netzgespeisten Floor-Lauf.
- Frühaktivierungs-Frage des Operators: nur ~1 kWh/Woche durch „zu späte"
  Starts — der Haupthebel war die Prognosequalität, nicht Latest-First.

## 2. Die Fix-Serie F1–F11 (alle released)

| Fix | Version | Kern | Schlüssel-Anker |
|---|---|---|---|
| **F1** T*-Bistabilität | v0.16.0 | Terminal-Credit rampt stetig über die Stress-Clip-Marge (0–250 Wh) statt binär zu kippen; Horizont-Truncation erst am Rampenende. Live-Schaden: 24.07. 07:28–08:02 lokal 24 T*-Flanken 20↔61, 9 G4-Trips, Entfeuchter ~67 min zwangsaus | `optimize.py::_threshold_merge_probe`, `_terminal_credit_factor`, `MERGE_TERMINAL_RAMP_WH` |
| **F1b** Executor-G4-Parity | v0.16.0 | „rec off"-Zweig des Floor-Guards trippt nur noch bei PV < laufender Lastleistung; SOC≤Floor-Zweig bleibt unconditional; WARN-Ratenlimit 15 min | `coordinator.py::_update_floor_guard` |
| **F8** Flicker-Continuation | v0.16.0 | Empfehlungs-Rückkehr binnen 5 min nach empfehlungsgetriebenem Stopp = Fortsetzung ohne min_off (Ping-Pong-Cap 2/30 min; G4-/G1-Stopps ausgenommen) | `REC_FLICKER_*`, flicker_eligible |
| **F4** Freeze-Guard | v0.16.0 | G2-Latch für rec-only-Lasten erreichbar + 6-h-Telemetrie-Freeze-Wächter (SOC UND Leistung exakt unverändert trotz aktiver Empfehlung). Anlass: Fossibot B2 über eine Woche eingefroren (174,7 h exakt 87,5 %/144 W, 20.–27.07.; „95 h" war Zwischenstand 24.07.), Phantom-Planung ohne Warnung | `FREEZE_STALE_HOURS`, `_update_telemetry_freeze` |
| **F5** Saturated-Power | v0.16.0 | Gelatchtes power_warning → Planner bucht Messleistung (~0 W) statt 409-W-Phantom; Empfehlung bleibt AN-fähig | `SurplusLoadState.saturated_power_w` |
| **V6** F-TANK | v0.16.0 | Opt-in `tank_full_runtime_min`; Laufzeit seit Leerung, Auto-Reset + Median-Lernen (5 Samples, persistiert), Push „Tank in <60 min voll". **Operator-Regel: NIE vorsorglich kürzen/abschalten** — Planner-Kappung wurde nach Operator-Einspruch wieder entfernt; Prognose ist nur Benachrichtigung+Diagnose | `docs/F-TANK.md` |
| **F9** Seamless Spill | v0.16.0 | Wurzelfix der stündlichen :31-Empfehlungs-Rücknahmen: Quantum-Spill in den eigenen Folgeslot ist Fortsetzung, keine Kollision. Mechanik: ab Minute :31 ist Slot 0 kürzer als das 30-min-Quantum → erzwungener Spill kollidierte mit der eigenen Pass-1-Buchung in Slot 1 → Overlap-Guard verwarf Slot 0 | `optimize.py::_seamless_spill` |
| **F10** Latch-Deadlock | v0.16.1 | v0.16.0-Regression: F5 (0-W-Planung) + Latch-Release-Bedingung (BM-kommandierter In-Band-Lauf) = Henne-Ei — Last blieb nach Tankleerung dauerhaft ungeplant. Fix: Reset-Button (= „Tank geleert") löst den Latch; plus opportunistisches Halten | `reset_load_runtime`, `_latch_hold_ok` |
| **F11** Schatten-Plan-Hold | v0.16.2 | Operator-Einspruch: Das Hold-Gate „PV ≥ Lastleistung" war STRENGER als die normale Planung (die z. B. früh morgens batteriegespeist via Pass 2 aktiviert). Jetzt: Hold feuert genau dann, wenn der Schatten-Plan (F5-Override aufgehoben, nie publiziert) Slot 0 aktivieren würde. PV-Vergleich nur noch als Überleistungs-Zusatzschutz (Messwert ≥ 1,5× Planleistung) | `_latch_shadow_active`, `LATCH_HOLD_OVERPOWER_FACTOR` |

Flankierend in v0.16.0: `diagnostics.py` (V7), 120-s-Startup-SOC-Grace (V8),
INFO-Zeile je Schaltaktion mit Grund (V9a), README-Klarstellung
lost_surplus/grid_import = **Horizontsummen** (V10), `in_house_measurement`-
Audit (V3: count-once-Verrechnung war korrekt; Doku/Optionstexte erweitert).

**Live-Heilungsnachweis (25.07.):** 05:33:51 Reset gedrückt → „power warning
cleared" → 05:35:50 `Entfeuchter Keller -> ON (plan slot)` → Pass-2-Frühlauf bei
PV ≈ 0 (batteriegespeist, „covered by otherwise-lost export"), ab 09:00
Direktüberschuss; lost_surplus des Tages 5,15 → 2,06 kWh.

## 3. Geprüft und VERWORFEN (nicht wieder vorschlagen)

1. **Harter Intraday-Scalar-Clamp** (Prognose) — bestraft echte
   Unterschätzungstage (21.07. brauchte legitim >1,6).
2. **Kosten-ε-Tie-Break / einfacher N-Zyklen-Debounce gegen F1** — die
   T*-Regime lagen ~1,8 kWh auseinander und hielten bis 49 min: beides
   nachweislich wirkungslos.
3. **Tank-Budget im Planner** (vorsorgliche Kappung) — vom Operator explizit
   untersagt; das Gerät schaltet sich selbst ab, der BM reagiert auf den
   echten Leistungseinbruch.
4. **Intervall-basierte Latch-Probe** (alle N Stunden) — Operator will
   „immer versuchen, wenn Leistung prinzipiell da wäre" → opportunistisches
   Halten (F10/F11).
5. **R6-Veto-These 22.07., Charger-Limit-1,4-kW-These 18.07.,
   B-Ladepause-Export-These 20.07.** — in der Forensik adversarial widerlegt.
6. **Pauschaler last_changed-Watchdog gegen eingefrorene Fossibot-Sensoren** —
   Cached-Values tragen frische Timestamps; nur „exakt unverändert TROTZ
   aktiver Empfehlung" ist ein valides Signal (so gebaut in F4).

## 4. Offene Punkte (Stand 25.07.2026)

### Code-Kandidaten (aus der Forensik, bewusst noch nicht gebaut)
- **V1 — messwertbasiertes Export-Opportunitäts-Gate** (M–L): Der
  Stundenmittel-Planner ist strukturell blind für subhourly Burst-Export
  (~3 kWh in der Forensik-Woche bei nicht voller Batterie). Idee: gemessener
  Export > X W über Y min + unsaturierte Last → außerplanmäßiges Quantum;
  Strict-Surplus-Bedingung zwingend. Voraussetzung sinnvollerweise V3-Betrieb
  (Entfeuchter aus dem Exportzähler heraus, sonst ist das Signal verseucht).
- **Stage 3 der Verbrauchsprognose (D-C10 „erwartete Läufe"/Appliance-
  Signaturen, CONSUMPTION_FORECAST.md §6):** spezifiziert, aber **nicht
  implementiert** — die Entscheidung, ob es gebaut wird, ist offen
  (Stand v0.17.0).
- **V4 — Ladeverlust-Modell energielimitierter Lasten** (S–M): Fossibot B
  stoppte bei 86,9 % statt 90 % (841 Wh Wand → 574 Wh intern, η ≈ 0,7);
  Wirkungsgradfaktor in der Wh-Dimensionierung.
- **Kosmetik Empfehlungs-Binary**: zeigt PLAN, nicht Gerätezustand — Operator
  war zweimal verwirrt („Empfehlung aus, Gerät läuft" bei nahtloser
  Fortsetzung). Angebot offen: Anzeige = Plan ∨ aktiver Lauf.
- **Deutscher Entity-Name für den Reset-Button** („… reset runtime" →
  „Behälter geleert (Reset)"): Operator fand den Button nicht; Angebot offen.

### Betrieb/Konfiguration (Operator)
- **Entfeuchter-Optionen setzen**: `tank_full_runtime_min` = **800** (Operator-
  Erfahrungswert; NICHT die früher geschätzten 240–360) und
  `in_house_measurement` = **aus** (hängt außerhalb des Messkreises).
  Stand 25.07. früh: noch nicht gesetzt.
- **Fossibot B2 bleibt manuell**: Der Operator schließt B2 von Hand ans Netz —
  eingefrorene Telemetrie (seit 20.07.) und fehlender Aktor sind **gewollt/
  akzeptiert**, kein Bug-Backlog. F4 verhindert seither die Phantom-Planung.
  Die aktiven Legacy-Automatisierungen `f2400_b(_2)_ladesteuerung` sind KEINE
  Doppelsteuerung, sondern eine komplementäre Firmware-Absicherung
  (setzen nur `ac_charging_upper_limit` 20↔90; Stop-Pfad greift nur bei
  Gate-Boolean=off). Hartkodierte 90 muss mit dem BM-Ziel-SOC synchron bleiben.
- **Geschirrspüler als Appliance-Subentry anlegen** (Empfehlung): heute sieht
  der Planner nur die Waschmaschine modellhaft; der Geschirrspüler wirkt nur
  indirekt über die SOC-Rückkopplung.
- **V5 — B-Laderate schwankt** (305–790 W je nach Tag): Median-Learning fängt
  das mit Verzögerung; ggf. am Gerät fixieren.
- **V2 — Victron-ESS-Überschwingen** (17.07., Export bei entladender
  Batterie): außerhalb der BM-Verantwortung, beim ESS-Setpoint zu prüfen.

### Watch-Items
- **F1-Rest-Kante bei ~250 Wh Clip-Marge** (nur R11-Geometrie): Tage mit
  Mini-Clip (0–250 Wh) bleiben jetzt konservativ hoard-fähig statt zu drainen
  — an einem Grenztag beobachten.
- **G4-PV-Quelle ist Slot-0-Forecast**, nicht Messwert: bei krasser
  Morgen-Unterprognose könnte der rec-off-Zweig zu früh trippen; falls der BM
  je einen echten PV-Ist-Eingang bekommt, dort nachschärfen.
- **BM-Reloads unklarer Ursache** (19.07. 15:12/16:43, Muster auch
  17./18./23.07.) — nie geklärt, seither unauffällig.

## 5. Kopplung zum balcony-solar-forecast (Prognose-Lieferant)

Die zwei Prognose-Wurzelbefunde der Forensik (F6 Intraday-Scalar-Overshoot an
klaren Morgen bis 2,36; F7 day-0-Stundenband-Kollaps auf `source=scalar`)
liegen im Repo `danielr0815/balcony-solar-forecast` und wurden dort in
**v0.21.0** ursächlich adressiert (Doppelkorrektur-Fix, Beam-Gain,
Quantile-Bootstrap-Seeding; seit 25.07. ~06:30 live, day-0-Bänder tragen
56/56 Slots). Vollständiger Schriftwechsel:
`docs/orders/balcony-solar-forecast-auftrag-2026-07-24.md` (BM → Balcony) und
`…-antwort-2026-07-24.md` (Balcony → BM, inkl. korrigierter Attributionen).

Für den BM daraus relevant:
- **Abnahmekriterien** (ersetzen das ursprüngliche BM-Kriterium):
  Wochenmittel served/Ist 07–10 Uhr ±20 % UND Scalar 08–09 Uhr ≤ ~1,25 an
  Tagen mit |Tagesfehler| < 10 %; `quantile_coverage[heute].coverage > 0,5`
  mit `source != scalar`.
- **Scalar-Peaks > 1,3 an klaren Morgen sind ab 25.07. ein Regressionssignal**
  (bitte an das Balcony-Repo melden), ebenso häufiges Anschlagen der
  BM-seitigen p10==p90-Fallback-Heuristik.
- **Neue Observability-Felder (Balcony v0.21.0)**, die der BM nutzen könnte:
  `get_issued_forecast.hourly_wh_ac` (DC/AC-Verwechslung hatte die
  BM-Forensik um ~8 % verzerrt — issued-Vergleiche künftig gegen AC führen),
  `cloud_class_by_hour`, `applied_factor_by_hour`, `band_source(_by_day)`,
  `day_ahead_bias_status[*].clamped`.
- **Balcony 0.22/0.23 ändern die RAW-Physik erneut** (Horizont-τ-Rampe,
  diffuse_tau, In-App-Re-Bootstrap); in Übergangswochen Tagesabweichungen
  nicht überinterpretieren.

## 6. Methodik-Notizen für künftige Forensiken

- Datenquellen: HA-History-API (UTC! ohne end_time nur 1 Tag),
  `recorder/statistics_during_period` via WS (Victron-SOC roh ist zu groß —
  immer Statistics nutzen), `/api/hassio/core/logs` mit
  `Range: entries=:-30000:` (~2–3 Tage Puffer; HA loggt LOKALZEIT).
- PV-Ist: `sensor.balcony_solar_forecast_measured_ac_power` (die nackten
  solar_*-Sensoren haben keine Langzeit-Statistik).
- Export-/Import-Wahrheit: `victron_grid_energy_reverse/forward_total_30`
  als Zählerstands-Deltas — Achtung Entfeuchter-Artefakt (s. o.), bis V3
  betrieblich umgesetzt ist.
- Der Recorder speichert das `forecast`-Attribut des soc_forecast-Sensors
  NICHT — Plan-Historie ist nur über abgeleitete Sensoren rekonstruierbar.
- Empfehlungs-Flanken um :31 ± 25 s = Rasterkanten-Artefakt-Signatur (seit F9
  behoben — Wiederauftreten wäre eine Regression).

---

Verifikation: destilliert aus der Live-Forensik-Session 24./25.07.2026
(adversarial verifizierte Befunde) und geprüft gegen Commit `a172d48`
(v0.16.2); die v0.17.0-Einträge gegen den Arbeitsstand vom 2026-07-31. Wichtigste Code-Anker: `core/optimize.py::_threshold_merge_probe`,
`core/optimize.py::_seamless_spill`, `coordinator.py::_latch_shadow_active`,
`coordinator.py::_update_telemetry_freeze`, `docs/F-TANK.md`.
