# Spezifikation: Zwei-Bus-DC-Modell & spannungsgeführte Stützpfade (F-N3)

> Status: **Entwurf — wartet auf Betreiber-Feedback** (2026-07-05)
> Erweitert ALGORITHM.md D-A9/F-N1/F-N2 um die physikalisch korrekte
> Abbildung der DC-Ebenen. Synthese aus drei unabhängigen Designentwürfen
> (Physik / HA-UX / Migration) + zwei Jury-Reviews; Zielversionen
> v0.7.0 … v0.7.6, je Phase einzeln deploybar und per Konfiguration
> rückrollbar.

## 1. Betreiber-Anforderungen (2026-07-05)

- **R1 — Spannungs-Gate 48-V-Netzteil:** Ausgangsspannung fix 49,56 V.
  Die ~60 W fließen nur, solange die Batteriespannung UNTER der Schwelle
  liegt; darüber liefert das Netzteil nichts, auch wenn es eingeschaltet
  ist. Schwelle konfigurierbar.
- **R2 — Manueller 48-V-Modus = Spannungsregler:** Im manuellen Modus
  soll das 48-V-Netzteil aktiv eingeschaltet werden, sobald die
  Batteriespannung unter der Schwelle liegt (und oberhalb wieder aus).
- **R3 — Manuell-Modus per Schalter:** Zusätzlich zur externen
  Aktivierung (F-N2) je Netzteil ein Schalter der Integration.
- **R4 — Geräteparameter:** 48-V-Netzteil, 24-V-Netzteil und
  DC/DC-Wandler bekommen konfigurierbare Parameter: max. Strom,
  Ausgangsspannung (→ Leistungs-Caps = U × I), Wirkungsgrade.
- **R5 — Kombinationsabhängige Lastflüsse:** (48 V + DC/DC),
  (48 V + 24 V), (nur 24 V), (nur DC/DC) ergeben unterschiedliche
  AC/DC-Verteilungen, die die Simulation jeweils korrekt abbilden muss.

## 2. Ist-Modell und seine bekannten Fehler

Der Kern ([simulate.py](../custom_components/battery_manager/core/simulate.py))
kennt **eine** ungeteilte DC-Lastreihe (`slot.dc_wh`) als Batterielast über
`eta_discharge` — keine Bus-Trennung, kein DC/DC-Wirkungsgrad, keine Caps:

| Fehler | Wirkung |
|---|---|
| 24-V-PSU verschiebt die GESAMTE DC-Last ins Netz (1:1) | native 48-V-Lasten wandern fälschlich mit; kein η, kein Cap |
| 48-V-PSU speist pauschal 60 W, ohne Spannungs-Gate | Winter-Prognose kreditiert Energie, die real nie fließt |
| `grid_import += psu_wh` IMMER (auch bei voller Batterie) | Überberechnung des Imports |
| Lernen (Rev. 3) nimmt „Schalter an == 60 W geliefert" an | über-korrigiert AC und DC in Gate-zu-Stunden |

**Live-Befund (2026-07-05):** Bei ~41 % SOC und −38 A Last lag
`sensor.victron_battery_voltage` bei **48,66 V** (< 49,56 V!), während die
Zellen 3,24 V (≈ 51,9 V) zeigten — Leitungs-/Shunt-Abfälle unter Last.
Konsequenzen: (a) das Gate ist **lastgekoppelt**, nicht nur SOC-abhängig —
das Netzteil trägt unter Winterlast schon bei mittlerem SOC; (b) es ist zu
klären, an welchem Messpunkt das Netzteil real vergleicht (Bus vs.
BMS-Klemme); (c) während Ladephasen hebt der Charger den Bus über die
Schwelle → Gate zu, unabhängig vom SOC.

## 3. Zielbild Datenmodell (Kern)

- Neue frozen Dataclasses: `Psu48(output_voltage_v, max_current_a, eta)`,
  `Psu24(output_voltage_v, max_current_a, eta)`,
  `DcDc(eta, max_current_a)`. **Ein-Parameter-Gate:** die Schwelle IST
  die Ausgangsspannung (kein zweites Feld).
- **Kein HourSlot-Split:** die Aufteilung 24-V-Schiene vs. nativer
  48-V-Bus kommt als konfigurierter Anteil `dc24_share` (Default 100 % =
  heutiges Verhalten) und wird IN `step_hour` angewandt — kleinster
  Blast-Radius (series.py, Lernserien, dynamischer Puffer bleiben
  unberührt).
- `HourFlows` erweitert um: `psu48_delivered_wh`, `psu24_delivered_wh`,
  `dcdc_input_wh`, `dcdc_loss_wh`, `unserved_dc_wh`, `gate_open` —
  für hourly_details, Debug-Export und Karten-Diagnose.

## 4. Simulationsgleichungen (ein Durchlauf, zwei orthogonale Dimensionen)

Notation je Slot: `dt`, `L24 = dc_wh × dc24_share`,
`L48 = dc_wh × (1 − dc24_share)`, Caps `P48 = U48 × I48`,
`P24 = U24 × I24`, η24/η48/η_dcdc; Batterie-η wie bisher.

**Dimension 1 — Schienenquelle** (`dc24_from_grid`, Semantik unverändert):

- *DC/DC (Normalfall):* `served24 = min(L24, P_dcdc·dt)`;
  `bus_draw24 = served24 / η_dcdc`; Verlust = Differenz;
  `unserved += L24 − served24` (Schienen-Brownout: darf in keinem
  akzeptierten Plan auftreten — Warnpfad).
- *24-V-PSU:* `served24 = min(L24, P24·dt)`;
  `grid_import += served24 / η24`; `bus_draw24 = 0`; ein bindender Cap
  wird als `unserved` SICHTBAR gemacht, nicht still aus der Batterie
  nachgefüllt (DC/DC ist in dieser Kombination aus).
- *Beide an* (Parallelbetrieb, Betreiber-Antwort 8): **keine Entkopplung,
  die Quelle mit der höheren Ausgangsspannung liefert** — die Simulation
  vergleicht `psu24_output_voltage_v` vs. `dcdc_output_voltage_v` und
  ordnet den Slot der höheren zu (die andere ~0 W). Damit ist auch der
  3-s-Make-before-break-Überlapp brownout-frei abgebildet.

**Dimension 2 — 48-V-Bus:** `bus_load = L48 + bus_draw24`.
`gate_open = dc48_an UND soc_start < gate_soc UND kein Netto-Ladeslot`
(Charger/PV-Ladung hebt den Bus über die Schwelle — Jury-Gap #1).

- PSU-Einspeisung verrechnet **zuerst direkt** gegen die gleichzeitige
  Buslast (ohne Batterie-Umweg — behebt den heutigen Doppel-η-Fehler),
  der Rest lädt die Batterie MIT `eta_charge`;
  **Abrechnung nach GELIEFERTER Energie**: `grid_import += delivered/η48`
  (volle Batterie oder zues Gate ⇒ ~0 statt heute 60 Wh).
- **Taper am Gate-Rand** (Jury-Gap #2): im Grenz-Slot ist
  `delivered ≤ Energie bis gate_soc` — halbiert den
  Worst-Case-Slotfehler.
- Rest wie bisher: Batterie-Entnahme über η_discharge bis floor,
  Shortfall über Charger aus dem Netz.

**Testnetz:** Energieerhaltungs-Property je Slot (Zuflüsse = Abflüsse +
ΔSpeicher + Verluste) über JEDE Trajektorie der Suite; Golden-Plan-
Snapshots beweisen Bit-Gleichheit unter neutralen Defaults.

## 5. Spannung ↔ SOC (Gate-Proxy)

- **Echtzeit (R2-Regler):** direkter Spannungssensor `battery_voltage_entity`
  = `sensor.victron_battery_voltage` (BMS, 15s-Zellsumme).
- **15s-Kontext (Betreiber 2026-07-05):** Batterie = Pylontech US5000 =
  15 Zellen. 49,56 V / 15 = **3,304 V/Zelle** — knapp unter der
  Plateau-Ruhespannung bei mittlerem SOC. Bus ≈ Zellsumme (Live:
  48,66–48,7 V ≈ 3,245 V × 15), also nur geringer Leitungsabfall; die
  frühere „Bus sackt tief unter Zellen"-Sorge war ein 16s-Rechenfehler.
- **Simulation (SOC-Raster):** konfigurierbares `gate_soc_percent` als
  Proxy. Kalibrierung über eine mitlaufende **14-Tage-Diagnose** (SOC an
  beobachteten Schwellen-Kreuzungen, als Attribut des Modus-Sensors).
  **Explizit in der Saison kalibrieren** (Jury-Gap #3: LiFePO4-OCV und
  Sag verschieben sich im Winter, genau wenn es zählt).
- Grenzen dokumentiert: LiFePO4-Flachkurve, Lastsag (Gate öffnet unter
  Last früher als in Ruhe — bei 15s moderat), Ladung schließt das Gate.
  Sanity-Warnung, wenn `gate_soc ≤ soc_min + support_buffer` („Netzteil
  kann nie helfen").

## 6. R2-Regler: spannungsgeführter manueller 48-V-Modus

Kleiner Zustandsautomat im Coordinator (nie im Kern), aktiv NUR im
dc48-Manuell-Modus:

- **Hybrid-Trigger:** State-Listener auf dem Spannungssensor + Fallback
  je Koordinator-Zyklus (Selbstheilung nach Neustart/Listener-Verlust).
- **Asymmetrische Hysterese + Dwell** (ein unnötiges EIN ist gratis —
  das Gerät gated intern; ein falsches AUS kostet Stützung): EIN bei
  `V ≤ 49,56 V` (Ausgangsspannung), AUS bei `V ≥ 49,8 V` (Betreiber 9);
  beide Schwellen + Dwell als **Options-Felder** (nicht fest). Bei
  Unterschreitung schaltet der Regler das PSU zwingend wieder ein
  (Betreiber 5).
- **Plausibilität:** nur 40–60 V akzeptieren; stale/unavailable ⇒
  einfrieren; Sensor > 10 min ungültig ⇒ **Fail-safe = EIN** + Warnung.
- Aktionen laufen über `_switch_lock`, zählen gegen
  `min_switch_interval_s` (geteiltes Budget mit dem Planner, D-A2) und
  werden in `_last_support_cmd`/`_support_pending_confirm` registriert —
  der F-N2-Detektor darf Regler-Aktionen nie als „extern" werten. **Ein
  vom Regler verursachtes PSU-AUS beendet daher NICHT den Manuell-Modus**
  (löst Restfrage A; Modus-Ende nur über den R3-Schalter, §7).
- **48-h-Log-only-Shakedown** vor der ersten scharfen Aktivierung
  (Restfrage D).
- **Feature-Gate:** ohne konfigurierten Spannungssensor bleibt
  dc48-manuell exakt F-N2-hands-off — die bestehenden Tests bleiben gültig.
- **24 V manuell bleibt hands-off** (unverändert F-N2): dort gibt es keinen
  Regler, also gilt weiter „externes AUS = Modus-Ende".

## 7. R3: Manuell-Schalter

- Zwei neue Schalter-Entitäten (`… Stützung 24 V manuell` / `… 48 V
  manuell`), immer verfügbar (Muster: Urlaubsschalter).
- **Ein** gemeinsamer Eintrittspunkt `async_set_support_manual(key,
  on, source)` für Schalter UND externe Erkennung; Quelle wird
  mitpersistiert (Anzeige/Diagnose). Externe EIN-Erkennung setzt den
  Schalter mit; externes AUS beendet den Modus und setzt ihn zurück.
- Eintritt/Austritt des 24-V-Modus über die Make-before-break-Sequenz.
- **Race-Regeln** (Jury-Gap #5): Modus-Mutation nie während einer
  laufenden N1a-Sequenz (bis Sequenzende verzögern); Doppel-Toggle
  idempotent; Anzeigeverhalten während der Grace-Fenster definiert.

## 8. Konfiguration (Basis-Flow, Support-Schritt)

Neue Felder — ALLE mit verhaltensneutralen Defaults (η = 1,0, Caps
unbegrenzt, Gate offen, `dc24_share` = 100 %): das Upgrade ändert nichts,
bis der Betreiber reale Werte einträgt (Rollback = Felder leeren):

| Feld | Default | Live-Wert (Betreiber) | Phase |
|---|---|---|---|
| `battery_voltage_entity` | — (Feature-Gate) | `sensor.victron_battery_voltage` | 2 |
| `battery_cells_series` | 16 | **15** (Pylontech US5000) | 3 |
| `psu48_output_voltage_v` / `psu48_max_current_a` / `psu48_eta` | 49,56 / — / 1,0 | 49,56 / **1,15** / 0,89 | 2 |
| `psu24_output_voltage_v` / `psu24_max_current_a` / `psu24_eta` | — / — / 1,0 | **offen (Restfrage B)** | 2 |
| `dcdc_output_voltage_v` / `dcdc_eta` / `dcdc_max_current_a` | 24 / 1,0 / — | 24 / **0,93** / **20** | 2 |
| `psu48_off_voltage_v` (Regler-AUS) / `psu48_on_voltage_v` (EIN) | 49,8 / 49,56 | 49,8 / 49,56 | 5 |
| `gate_soc_percent` | 100 (= offen) | kalibriert (Phase 3) | 3 |
| `dc24_share_percent` | 100 | Schätzwert | 2 |

`rail24_voltage_entity` (optional): `sensor.victron_dcsystem_starter_voltage_229`
(×10-Fix erledigt) → Dead-Rail-Verifikation.

## 9. Lernen (Reinigungsregeln Rev. 4)

- `_psu48_series` wird spannungs-gated über **LTS-Stunden-Min/Max**
  (Jury-Gap #4): `max < U_thr` ⇒ volle Stunde geliefert;
  `min > U_thr` ⇒ nichts geliefert; sonst (Clamp-Regime, PSU liefert
  genau die Buslast) ⇒ Stunde **ausschließen statt klassifizieren**.
- Optionaler AC-seitiger Messsensor fürs 48-V-Netzteil als Tier-1-Quelle —
  mit **Deadband gegen Standby-Poisoning** (Lehre aus v0.6.2).
- η-bewusste 24-V-Korrektur; `_CLEANING_RULES_VERSION = 4` +
  Fingerprint ⇒ einmaliger Voll-Refetch.

## 10. Phasenplan (je Phase: deploybar, Tests, Live-Verifikation, Rollback)

| Phase | Version | Inhalt | Live-Verifikation |
|---|---|---|---|
| 0 ✓ | v0.6.5 | F-N2 committet (erledigt) | 24-h-Soak der Override-Logik |
| 1 | v0.7.0 | Kern: Dataclasses, HourFlows, Kombinations-Gleichungen, Gate verdrahtet aber default-offen; Golden-Snapshots bit-exakt | `export_hourly_details` vorher/nachher identisch |
| 2 | v0.7.1 | Config-Flow + Verdrahtung + Diagnose-Spalten | reale Typenschild-Werte eintragen (Gate offen lassen), Plan-Deltas plausibilisieren |
| 3 | v0.7.2 | R1-Gate scharf + Kalibrier-Diagnose | PSU manuell bei hohem SOC an ⇒ Prognose kreditiert KEINE 60 W; Abend-Entladung gegen Victron-Spannungsgraph |
| 4 | v0.7.3 | R3-Schalter + Modus-Konsolidierung (ein Eintrittspunkt) + korrekte PSU-Direktverrechnung (eigene Golden-Diffs) | Schalter toggeln, Modussensoren, Schiene nie quellenlos, Neustart mitten im Manuell-Modus |
| 5 | v0.7.4 | R2-Spannungsregler (Log-only-Flag) | 48 h Log-only gegen Victron-Historie, dann scharf über einen Abend-/Morgenzyklus |
| 6 | v0.7.5 | Lernen Rev. 4 (LTS-Min/Max-Gating) | Relearn-Lauf, Profil-Export-Vergleich, 14-d-Watchdog |
| 7 | v0.7.6 | Karten-Stützungs-Spur + Doku-Abschluss | Dashboard-Check |

## 11. Betreiber-Entscheidungen (2026-07-05) und Restfragen

**Beantwortet:**

1. **Batterie: Pylontech US5000 → 15s LiFePO4** (nominal 48 V, Ladeschluss
   ~53,2 V, deckt sich mit `victron_battery_info_maxchargevoltage`).
   Zellenzahl/Spannungsfenster werden **konfigurierbar** (Default aus dem
   15s-Profil). Damit ist auch Q12 aufgelöst — siehe unten.
2. **24-V-Schienenspannung: gefixt** — der ×10-Skalierungsfehler wurde
   lokal korrigiert, `sensor.victron_dcsystem_starter_voltage_229` liefert
   jetzt die realen ~24,3 V. Wird als optionaler `rail24_voltage_entity`
   für die Dead-Rail-Verifikation eingeplant (Plausibilität ~20–29 V).
   Weiterhin nur SPANNUNG gemessen (kein Strom) → `dc24_share` bleibt
   konfigurierter Schätzwert.
3. **DC/DC-Wandler:** max. **20 A**, η **> 0,93** → `dcdc_max_current_a =
   20`, `dcdc_eta = 0.93` (Cap ≈ 24 V × 20 A = 480 W schienenseitig).
4. **48-V-PSU:** **Meanwell HGL-60H-54A**, max. **1,15 A**, Ausgang auf
   49,56 V eingestellt → `psu48_max_current_a = 1.15`,
   `psu48_output_voltage_v = 49.56` (Cap ≈ 57 W). AC-η ~0,89 (Datenblatt),
   konfigurierbar.
5. **R2-Scope: ja** — externes EIN aktiviert den Spannungsregler; er darf
   oberhalb der Schwelle abschalten UND **muss bei Unterschreitung
   automatisch wieder einschalten**. → Der 48-V-Manuell-Modus ist ein
   geregelter Standby, kein hands-off (siehe §6, unterscheidet ihn von der
   24-V-Logik). ⚠️ Zusammenspiel mit (6) klärungsbedürftig — Restfrage A.
6. **Exit: ok** — externes AUS beendet den Manuell-Modus (zurück zu
   Automatik). ⚠️ Kollidiert im geregelten 48-V-Modus mit (5) — Restfrage A.
8. **Parallele 24-V-Quellen: keine Entkopplung — die Quelle mit der
   HÖHEREN Ausgangsspannung liefert** (die andere ~0 W). Ersetzt die
   bisherige „PSU-Priorität"-Annahme: bei beiden aktiv vergleicht die
   Simulation `psu24_output_voltage_v` vs. `dcdc_output_voltage_v`; die
   höhere versorgt die Schiene. Nebeneffekt: Make-before-break ist dadurch
   physikalisch brownout-frei (die höhere Quelle trägt im Überlapp).
9. **Regler-AUS-Schwelle: 49,8 V** (statt 50,06 V). EIN-Schwelle bei
   ≤ 49,56 V (Ausgangsspannung); beide Schwellen + Dwell als Options-Felder.
11. **PSU-Standby-Verbrauch: vernachlässigen** (dokumentiert).
12. **AUFGELÖST (Rechenfehler meinerseits):** Meine 51,9 V beruhten auf
    einer 16s-Annahme. Mit **15s** gilt 3,24 V/Zelle × 15 = 48,6 V ≈
    gemessene Busspannung 48,66–48,7 V — Bus und Zellsumme stimmen also
    praktisch überein, es gibt KEINE große Messpunkt-Diskrepanz. Die
    Sorge „Gate stark lastgekoppelt/Bus sackt tief unter Zellen" entfällt
    weitgehend; die Schwelle 49,56 V liegt schlicht knapp unter der
    Plateau-Ruhespannung. Spannungs-Entität: `victron_battery_voltage`
    (BMS) bevorzugt.

**Restfragen (blockieren nur Phase 4/5):**

- **A — Regler-AUS vs. Benutzer-AUS (aus 5 + 6):** Im geregelten
  48-V-Modus schaltet der REGLER das PSU oberhalb 49,8 V ab und unter
  49,56 V wieder ein — ein „aus" darf dann NICHT als „Benutzer beendet
  Manuell-Modus" gelten. Vorschlag: Der **R3-Schalter** „48 V manuell" ist
  die alleinige Modus-Wahrheit; ein externes physisches EIN startet den
  Modus und setzt den Schalter mit; den Modus beendet man durch Ausschalten
  des R3-Schalters, nicht durch physisches PSU-Aus. Die reine
  F-N2-hands-off-Logik (externes AUS = Modus-Ende) bleibt nur für die
  24-V-PSU. OK so?
- **B — 24-V-Stütznetzteil:** Antwort (8) impliziert ein netzgespeistes
  24-V-Netzteil parallel zum DC/DC. Dessen **Ausgangsspannung und
  max. Strom** fehlen noch (für den Spannungsvergleich aus 8 und den Cap).
  Existiert es, und mit welchen Werten?
- **C — Q7 (neu erklärt):** siehe Gesprächsantwort — Verhalten, wenn die
  24-V-Last die Quellen-Caps übersteigt (Vorschlag: nur warnen).
- **D — Q10 (neu erklärt):** siehe Gesprächsantwort — 48-h-Trockenlauf des
  Reglers vor scharfem Schalten (ja/nein).

## 12. Hauptrisiken

- **Gate-Proxy-Fehler** (Flachkurve + Sag + Saison): darum Kalibrier-Diagnose, In-Saison-Kalibrierung, Taper, und das Lernen klassifiziert über echte Spannungs-LTS statt über den Proxy.
- **Regler-Flattern** an der Schwelle: asymmetrische Hysterese + Dwell + Log-only-Shakedown.
- **Regressionen** in frisch stabilisiertem Planerverhalten (v0.6.1–v0.6.5): Golden-Snapshots + verhaltensneutrale Defaults + eine Phase pro Version mit Live-Soak.
