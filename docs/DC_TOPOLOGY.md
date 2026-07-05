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
- *Beide an* (realer Übergangs-/Parallelzustand): der Coordinator mappt
  auf PSU-Priorität (offene Frage Q8); der Kern braucht keinen dritten
  Fall. Der 3-s-Make-before-break-Überlapp liegt unter Slot-Auflösung.

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

- **Echtzeit (R2-Regler):** direkter Spannungssensor, neues Konfigfeld
  `battery_voltage_entity` (Kandidaten live vorhanden:
  `sensor.victron_battery_voltage`, `sensor.victron_system_battery_voltage`
  — Messpunkt-Entscheidung Q12/Q13).
- **Simulation (SOC-Raster):** konfigurierbares `gate_soc_percent` als
  Proxy. Kalibrierung über eine mitlaufende **14-Tage-Diagnose** (SOC an
  beobachteten Schwellen-Kreuzungen, als Attribut des Modus-Sensors).
  **Explizit in der Saison kalibrieren** (Jury-Gap #3: LiFePO4-OCV und
  Sag verschieben sich im Winter, genau wenn es zählt).
- Grenzen dokumentiert: LiFePO4-Flachkurve, Lastsag (Gate öffnet unter
  Last früher als in Ruhe), Ladung schließt das Gate. Sanity-Warnung,
  wenn `gate_soc ≤ soc_min + support_buffer` („Netzteil kann nie
  helfen").

## 6. R2-Regler: spannungsgeführter manueller 48-V-Modus

Kleiner Zustandsautomat im Coordinator (nie im Kern), aktiv NUR im
dc48-Manuell-Modus:

- **Hybrid-Trigger:** State-Listener auf dem Spannungssensor + Fallback
  je Koordinator-Zyklus (Selbstheilung nach Neustart/Listener-Verlust).
- **Asymmetrische Hysterese + Dwell** (ein unnötiges EIN ist gratis —
  das Gerät gated intern; ein falsches AUS kostet Stützung):
  EIN bei `V ≤ U_out − 0,1 V` für 60 s; AUS bei `V ≥ U_out + 0,5 V` für
  300 s; dazwischen halten. Defaults fest, keine Options (Q9).
- **Plausibilität:** nur 40–60 V akzeptieren; stale/unavailable ⇒
  einfrieren; Sensor > 10 min ungültig ⇒ **Fail-safe = EIN** + Warnung.
- Aktionen laufen über `_switch_lock`, zählen gegen
  `min_switch_interval_s` (geteiltes Budget mit dem Planner, D-A2) und
  werden in `_last_support_cmd`/`_support_pending_confirm` registriert —
  der F-N2-Detektor darf Regler-Aktionen nie als „extern" werten.
- **48-h-Log-only-Shakedown** vor der ersten scharfen Aktivierung (Q10).
- **Feature-Gate:** ohne konfigurierten Spannungssensor bleibt
  dc48-manuell exakt F-N2-hands-off — die bestehenden 9 Tests bleiben
  unverändert gültig.
- **24 V manuell bleibt hands-off** (unverändert F-N2).

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

| Feld | Default | Phase |
|---|---|---|
| `battery_voltage_entity` | — (Feature-Gate) | 2 |
| `psu48_output_voltage_v` / `psu48_max_current_a` / `psu48_eta` | 49,56 / — / 1,0 | 2 |
| `psu24_output_voltage_v` / `psu24_max_current_a` / `psu24_eta` | — / — / 1,0 | 2 |
| `dcdc_eta` / `dcdc_max_current_a` | 1,0 / — | 2 |
| `gate_soc_percent` | 100 (= offen) | 3 |
| `dc24_share_percent` | 100 | 2 |

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

## 11. Offene Betreiberfragen

1. **Zellenzahl/Spannungsfenster** der Batterie (15s/16s LiFePO4)? Bestimmt, wo 49,56 V relativ zur Flachkurve liegt (Start-`gate_soc`).
2. **24-V-Schienen-Messung** (Stand 2026-07-05 teilbeantwortet): Der
   Victron SmartShunt misst die 24-V-Schiene als „Starterspannung"
   (`sensor.victron_dcsystem_starter_voltage_229`) — aber nur SPANNUNG,
   kein Strom/keine Leistung → `dc24_share` bleibt vorerst Schätzwert
   (Startwert?). Die HA-Entität liefert derzeit 2,43 V statt real
   24,28 V: bekannter 10×-Skalierungsfehler der hass-victron-Integration
   (Register 4402 mit Skala 100 statt 10 dekodiert; für `dcsource` als
   Issue #27 „specsheet mismatch" bereits gefixt, für `dcsystem` nicht).
   → Upstream-Issue + übergangsweise Template-Sensor ×10. Nach der
   Korrektur wird die Schienenspannung als optionaler
   `rail24_voltage_entity` wertvoll: reale **Dead-Rail-Verifikation** für
   den Schienen-Schutz und die Make-before-break-Bestätigung (Schiene
   wirklich versorgt statt nur Schalterzustand „on"), Plausibilität
   ~20–29 V.
3. **DC/DC**: Datenblatt-η und max. Ausgangsstrom?
4. **Typenschilder**: 48-V-PSU max. Strom (sind 60 W = 49,56 V × ~1,21 A?); 24-V-PSU U/I; AC-Wirkungsgrade beider Netzteile?
5. **R2-Scope:** Soll auch ein rein EXTERNES Einschalten des 48-V-Netzteils den Spannungsregler aktivieren (Plan-Annahme: ja — d. h. die Integration darf es oberhalb der Schwelle wieder ausschalten), oder nur der R3-Schalter?
6. **Exit:** Externes AUS im geregelten Modus ⇒ zurück zu Automatik (Plan-Annahme, konsistent F-N2)?
7. **Schienen-Überlast** (L24 > Cap): nur warnen (Vorschlag) oder Planungsstufe verweigern?
8. **Beide 24-V-Quellen parallel** (kein DC/DC-Schalter): Dioden-Entkopplung — wer liefert real?
9. **Reglerkonstanten** (EIN ≤ 49,51 V/60 s, AUS ≥ 50,06 V/5 min, Fail-safe EIN nach 10 min) als feste Defaults ok?
10. **48-h-Log-only-Shakedown** vor scharfem Regler ok?
11. **Standby-Verbrauch** der Netzteile (~1–3 W) vernachlässigen (Vorschlag) oder abrechnen?
12. **Spannungs-Entität + Messpunkt**: `victron_battery_voltage` (BMS) oder `victron_system_battery_voltage` (Bus)? An welchem Punkt vergleicht das Netzteil real? (Live-Delta heute: Bus 48,7 V vs. Zellen ≈ 51,9 V bei −38 A!)

## 12. Hauptrisiken

- **Gate-Proxy-Fehler** (Flachkurve + Sag + Saison): darum Kalibrier-Diagnose, In-Saison-Kalibrierung, Taper, und das Lernen klassifiziert über echte Spannungs-LTS statt über den Proxy.
- **Regler-Flattern** an der Schwelle: asymmetrische Hysterese + Dwell + Log-only-Shakedown.
- **Regressionen** in frisch stabilisiertem Planerverhalten (v0.6.1–v0.6.5): Golden-Snapshots + verhaltensneutrale Defaults + eine Phase pro Version mit Live-Soak.
