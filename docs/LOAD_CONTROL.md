# Spezifikation: Gesteuerte Ladepfade für Überschusslasten (v0.3)

> Status: **Entwurf — wartet auf Betreiber-Feedback** (2026-07-04)
> Erweitert REQUIREMENTS.md (L-Anforderungen) und ALGORITHM.md um die direkte
> Ansteuerung von Powerstation-Ladepfaden durch die Integration.

## 1. Ausgangslage (Betreiber-Beschreibung, F2400-B als Beispiel)

- Der 230-V-**Eingang** des Fossibot hängt an einer schaltbaren Steckdose
  (`switch.shelly_01_switch_0`).
- Ist der Eingang **aus**, schläft der Fossibot: alle seine Sensoren (inkl.
  SOC) werden `unavailable`. Das ist der **Normalzustand** vor dem Laden —
  nicht „Gerät weg".
- Ist der Eingang **an**, wacht der Fossibot auf, **lädt** und versorgt
  gleichzeitig eine ggf. am **Ausgang** angeschlossene Last (z. B. PC) im
  Passthrough direkt aus dem Eingang.
- Über `input_boolean.charge_f2400_b` kann das **Laden der Batterie
  deaktiviert** werden, obwohl der Eingang an ist (Ausgang wird dann weiter
  aus dem Eingang versorgt, Batterie bleibt unverändert).
- Der Eingang wird auch von **externen Automationen** aktiviert — z. B. um
  den Fossibot zu wecken oder den Ausgang aus dem Netz statt aus der
  Batterie zu versorgen. Diese Nutzung gehört dem Betreiber, nicht dem
  Battery Manager.

**Zustandsmodell des Ladepfads:**

| Eingang (Shelly) | Freigabe (input_boolean) | Batterie lädt | Ausgang versorgt aus |
|---|---|---|---|
| aus | egal | nein | Fossibot-Batterie (wenn Ausgang an) |
| an | an | **ja** | Eingang (Passthrough) |
| an | aus | nein | Eingang (Passthrough) |

## 2. Neue Konfigurationsfelder je Überschusslast (optional)

| Feld | Beispiel F2400-B | Bedeutung |
|---|---|---|
| **Ladeeingang-Schalter** (`control_switch_entity`) | `switch.shelly_01_switch_0` | Schaltet die 230-V-Versorgung des Ladeeingangs. Wenn gesetzt, schaltet die Integration den Ladepfad **selbst** (statt nur zu empfehlen). |
| **Lade-Freigabe** (`charge_enable_entity`) | `input_boolean.charge_f2400_b` | Gate „Batterie darf laden". Wird von der Integration zusammen mit dem Eingang geschaltet. |

Ohne diese Felder bleibt alles wie bisher: reine Empfehlungs-Entität, der
Betreiber schaltet per Automation.

## 3. Schaltsemantik

**Laden aktiv** ⇔ Eingang AN **und** Freigabe AN.

- **Ladebeginn (Planstunde beginnt):**
  1. Merken, ob der Eingang bereits an war (→ „Fremdbesitz", Passthrough).
  2. Lade-Freigabe EIN.
  3. Eingang EIN (falls aus).
- **Ladeende (keine Planstunde mehr / Last gesättigt):**
  1. Lade-Freigabe AUS — immer.
  2. Eingang AUS — **nur wenn die Integration ihn selbst eingeschaltet
     hat** („Ownership-Regel"). War er beim Ladebeginn schon an (z. B.
     Passthrough-Versorgung des PCs durch eine Betreiber-Automation),
     bleibt er an.
- **Mindestlaufzeit** (`min_runtime_min`, Default 30 min) wirkt als
  Mindest-Ein/Aus-Verweilzeit der realen Schaltung (kein Takten in
  Wolkenlücken).
- Die Schaltvorgänge laufen — wie bei den Stützpfaden — in einem
  abbruchsicheren Hintergrund-Task; reale Schalterzustände werden im
  Leerlauf rückgelesen (heilt manuelle Eingriffe).
- Die Empfehlungs-Entität der Last bleibt bestehen (Transparenz +
  Trigger-Möglichkeit für zusätzliche Betreiber-Automationen).

**Konfliktvermeidung:** Wenn die Integration den Ladepfad selbst schaltet,
muss die bisherige Lade-Automation („F2400 Intelligente Ladesteuerung")
deaktiviert oder auf andere Aufgaben beschränkt werden — zwei Regler auf
demselben Schalter erzeugen Ping-Pong.

## 4. SOC-Handling bei schlafendem Gerät (ersetzt bisheriges Verhalten)

Bisher: energiebegrenzte Last mit unlesbarem SOC ⇒ nicht verfügbar.
**Neu:**

- SOC-Wert wird bei jeder gültigen Lesung **gecacht** und bei
  `unavailable`/`unknown` **weiterverwendet** (letztgültiger Wert, ohne
  Altersgrenze — Selbstentladung ist klein, Korrektur erfolgt beim nächsten
  Aufwachen).
- Der Cache wird **persistiert** (HA-Storage), damit er einen
  HA-Neustart mit schlafendem Fossibot überlebt.
- Ist gar kein SOC bekannt (Erstinstallation, Storage leer): Last gilt als
  **ladebedürftig** (Annahme SOC = 0). Selbstheilend: Beim ersten geplanten
  Laden wacht das Gerät auf, meldet den echten SOC, und der Plan
  korrigiert sich innerhalb eines Zyklus (≤ 5 min). Ein evtl. volles Gerät
  beendet das Laden ohnehin über sein internes Limit.
- Kein aktives „Wecken zum Messen": Der SOC wird genau dann live, wenn er
  gebraucht wird (während des Ladens). Externe Weck-Automationen des
  Betreibers aktualisieren den Cache nebenbei.

## 5. Leistungsmessung und Passthrough

`total_input` (IN Total) misst Eingang = Laden **+** Passthrough-Ausgang.
Aus Sicht der AC-Bilanz des Hauses ist das korrekt die Leistung, die die
Last bei aktivem Eingang zieht — sie wird weiterhin (geglättet) als
Planungsleistung verwendet. Der Energiefortschritt der Ladung wird ohnehin
über den SOC (Ground Truth) verfolgt, nicht über die Leistung integriert.

## 6. Weitere Punkte aus dem Betreiber-Wunsch

### 6.1 SOC-Prognoseverlauf visualisieren

- Neuer Sensor `sensor.…_soc_forecast`: Zustand = prognostizierter SOC in
  1 h; Attribut `forecast` = Liste `[{t: ISO-Zeit, soc: %}, …]` über den
  ganzen Horizont (aus der finalen Plan-Trajektorie, inkl. Lastwirkung).
- Anzeige über die bereits installierte **ApexCharts-Card** mit
  `data_generator` — fertiges Karten-YAML wird in der README mitgeliefert.

### 6.2 Icon

- **Lokal ausgeliefert** (kein brands-PR nötig): Seit HA 2026.3 dürfen
  Custom Integrations ihre Marken-Bilder direkt mitliefern. Die Dateien
  liegen unter `custom_components/battery_manager/brand/`
  (`icon.png` 256×256, `icon@2x.png` 512×512, dazu `logo.png`/`logo@2x.png`).
  HA serviert sie über die lokale Brands-Proxy-API
  (`/api/brands/integration/battery_manager/icon.png`); lokale Bilder haben
  Vorrang vor dem CDN, keine manifest-Konfiguration nötig. Motiv:
  Batterie mit Blitz + Sonne.

## 7. Betreiber-Entscheidungen (2026-07-04)

- **F-L1: Eingang-aus-Politik ist PRO LAST KONFIGURIERBAR** (Feld
  `input_off_policy`):
  - `auto` (Default): Ownership-Regel — Eingang nur aus, wenn die
    Integration ihn selbst eingeschaltet hat.
  - `always_off`: Eingang beim Ladeende immer ausschalten.
  - `keep_on`: Eingang nie ausschalten (nur Freigabe toggeln). Hinweis:
    Ohne konfigurierte Lade-Freigabe kann das Laden in diesem Modus nicht
    gestoppt werden — nur sinnvoll mit Freigabe-Entität.
- **F-L2: Ja** — unbekannter SOC ⇒ ladebedürftig annehmen (Annahme 0 %).
- **F-L3: Ja** — die Integration übernimmt die Ladeschaltung; die bisherige
  Automation „F2400 Intelligente Ladesteuerung" wird vom Betreiber
  deaktiviert.
- **F-L4 (revidiert 2026-07-04):** Der Betreiber möchte das Icon **nicht**
  offiziell einreichen. Genutzt wird stattdessen der lokale
  brand/-Mechanismus (HA ≥ 2026.3, siehe §6.2) — Icon liegt in
  `custom_components/battery_manager/brand/`, kein PR nötig.

## 8. Betreiber-Entscheidung Ladezeitpunkt (2026-07-05)

- **F-L5: Zusatzlasten so spät wie möglich aktivieren** — aber noch
  rechtzeitig genug, dass keine Energie eingespeist werden muss. Nachholen
  (bei jedem Replan mit besserer Information) schlägt das frühe Vorziehen
  auf Prognosebasis; Vorziehen kostet zudem den Umweg über die Hausbatterie
  (~18 % Zyklus-Verluste mit Default-Wirkungsgraden). Umsetzung: Pass 2
  latest-first + Mindestlaufzeit-ehrliche Bewertung (ALGORITHM.md D-A4 v3).
  Anlass war der Nachtlade-Vorfall vom 05.07. (Degenerierter-Slot-0-Bug:
  drei Starts jeweils in Minute :59, real ~250 Wh je „5-Wh-Plan").
- Flankierend persistiert der Coordinator den Schalt-Dwell über Neustarts
  (der Verlust war Mitverursacher). Der Leistungs-EMA wird bewusst NICHT
  persistiert und bei Feedback-Lücken nur serviert, solange die Last real
  lädt — nach Ladeende würde der Taper-Restwert (10–40 W) sonst dauerhaft
  als „gemessene" Planungsleistung alle Gates schwächen. Die Logzeilen
  „Charging started/stopped" nennen den Klartext-Lastnamen.
