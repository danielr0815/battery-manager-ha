# Entwicklung, Tests, Release

**Stand: `main` @ v0.16.2 (2026-07-25).** Dieses Dokument beschreibt die
**Arbeitsumgebung und die Konventionen** dieses Repos: wie und wo die Tests
laufen (die HA-Suite geht nur unter WSL), was die CI prüft und wo sie
zuverlässig zuschnappt, wie ein Release entsteht und welche Lektionen aus
früheren Reviews als Regel festgeschrieben sind. Was der Code tut, steht in
01–03; die Anlage und der Deploy-*Weg* in `05-anlage-und-betrieb-runbook.md`.

Alle Aussagen sind gegen `.github/workflows/*`, `pyproject.toml`,
`CONTRIBUTING.md` und den Testbaum geprüft. **Im Zweifel gilt der Code.**

---

## 1. Testumgebung

### 1.1 Der Split — und warum es ihn gibt

`pytest-homeassistant-custom-component` (und damit die HA-Testhelfer)
installiert **nicht unter nativem Windows**: die HA-Interna importieren `fcntl`,
das es dort nicht gibt. Deshalb ist der Testbaum zweigeteilt:

| Verzeichnis | Braucht HA? | Läuft wo? |
|---|---|---|
| `tests/core/` | **nein** — importiert `core/` direkt über einen `sys.path`-Trick in `tests/core/conftest.py` | überall, auch nativ unter Windows |
| `tests/ha/` | **ja** (`enable_custom_integrations`-Fixture, autouse in `tests/ha/conftest.py`) | Linux/WSL oder CI |

### 1.2 Die Kommandos

**Volle Suite (das, was CI fährt) — nur unter WSL:**

```
wsl -e bash -lc "cd /mnt/c/Users/jj/claude_space/battery-manager-ha && ~/bmha-venv/bin/python -m pytest tests -q"
```

**Nur der Kern — geht nativ unter Windows:**

```
python -m pytest tests/core -p no:homeassistant
```

Das `-p no:homeassistant` schaltet das HA-Pytest-Plugin ab; ohne den Schalter
scheitert schon die Plugin-Initialisierung.

**Linter — von Windows aus** (das `.venv` im Repo hat ruff):

```
ruff check custom_components tests
ruff format --check .        # oder `ruff format .` zum Anwenden
```

`pyproject.toml` konfiguriert beides: `target-version = "py313"`,
`line-length = 88`, `extend-exclude = [".venv"]`, Lint-Regelsatz
`E, F, I, UP, B, SIM` mit `ignore = ["E501"]` (die Zeilenlänge regelt der
Formatter, nicht der Linter). Pytest: `testpaths = ["tests"]`,
`asyncio_mode = "auto"`, `addopts = "-q"`.

### 1.3 Umfang (Stand v0.16.2)

**433 Tests**, davon ~197 im Kern und ~236 in der HA-Schicht. Die größten
Brocken: `tests/ha/test_load_switching.py` (112 Tests — der Executor ist die
regelreichste Stelle des Projekts) und `tests/core/test_optimize.py` (97).

### 1.4 Golden-Tests

Zwei eingefrorene Plan-Schnappschüsse im Kern:

| Datei | Was sie einfriert | Regenerieren |
|---|---|---|
| `tests/core/golden_topology.json` (**11 Szenarien**: `s1_evening_sunny`, `s2_cloudy_reserve`, `s3_loads_night`, `s3_low_soc_5am`, `s4_midday_full`, `short_peak_preempt`, `support_dc24_escalate`, `support_dc48_escalate`, `support_none_healthy`, `forced_dc24`, `forced_dc48`) | Die **Verhaltensneutralität** des F-N3-Zwei-Bus-Refactors: mit den neutralen Dataclass-Defaults (η = 1,0, Ströme ungedeckelt, Gate offen, `dc24_share` 100 %) muss jeder Plan **bit-identisch** zum Vor-F-N3-Verhalten bleiben | `scratchpad/gen_golden.py` |
| `tests/core/golden_night_predrain.json` | Das Pre-Drain-Verhalten mit den **empfohlenen Live-Parametern** (α 0,5, β 1,2) — bewusst **nicht** neutral, deshalb eine eigene Datei; die topology-Goldens müssen unter den neutralen Defaults eingefroren bleiben | `python tests/core/test_golden_night_predrain.py` (das Modul hat einen `__main__`-Zweig und setzt sich seinen `sys.path` selbst) |

**Der Anspruch ist Bit-Identität, nicht „ungefähr gleich".** Der Digest umfasst
Schwelle, Inverter-Zustand, Import/Export, Min/Max-SOC, `hours_to_max`,
`lost_surplus`, `import_trade_used_wh`, `stressed_min_soc`, `pv_window_ends`,
die SOC-Kurve und die Lastpläne.

**Regenerierungs-Regel:** Goldens werden **ausschließlich** für eine
beabsichtigte, reviewte Verhaltensänderung neu erzeugt — danach **den Diff
lesen**. Ein guter Change fasst nur an, was man erwartet hat; überrascht der
Diff, ist der Change falsch, nicht das Golden. Diese Regel ist der Grund, warum
die *Dataclass*-Defaults in `core/model.ControlParams` neutral sind (α = β = 1,0,
Pre-Drain-Ratio 0) und die empfohlenen Live-Werte in `const.DEFAULT_CONFIG`
stehen: sonst würde jede Default-Änderung die Goldens verschieben.

### 1.5 Executor-Test-Lektion: „Listener kappen statt drainen"

Der Coordinator registriert `async_track_state_change_event` auf seine
Eingangs-Entities; jedes `hass.states.async_set(...)` in einem Test löst darüber
einen **entprellten Refresh** aus. Ein Test, der danach nur die Event-Queue
leerlaufen lässt (`async_block_till_done`), rennt gegen einen zweiten,
ungewollten Planungslauf, der die gerade gesetzte Latch-/Dwell-Situation wieder
verändert — das Ergebnis flackert und der Test wird flaky.

**Die Lösung im Repo:** den Listener **abhängen**, nicht das Ergebnis abwarten.
Genau dieser Dreizeiler steht in `tests/ha/test_load_switching.py` vor jedem
hermetischen Executor-Test:

```
coordinator._unsub_state_listener()
coordinator._unsub_state_listener = None
coordinator._listeners_setup = False
```

Kommentiert als „Hermetic executor test: detach the entity listener so no
debounced refresh interferes". Wer einen neuen Executor-Test schreibt, kopiert
das Muster — sonst debuggt er die Testinfrastruktur statt seines Features.

---

## 2. CI

### 2.1 `.github/workflows/validate.yml`

Trigger: **jeder Push**, jeder Pull Request, **nächtlich** (`cron: 0 0 * * *`)
und manuell. Vier unabhängige Jobs:

| Job | Was er tut |
|---|---|
| **`lint` (Lint (ruff))** | Python 3.13, frisches `pip install ruff`, dann drei Schritte: `ruff check custom_components tests`, **`ruff format --check .`** und das **Versions-Gate** |
| **`tests` (Tests (pytest))** | Python 3.13, `pip install homeassistant pytest pytest-homeassistant-custom-component`, `python -m pytest tests -q` — also die **volle** Suite inkl. HA-Schicht |
| **`validate-hacs`** | `hacs/action@main`, `category: integration`, `ignore: brands` |
| **`validate-hassfest`** | `home-assistant/actions/hassfest@master` |

**Das Versions-Gate** (inline Python im Lint-Job):

```
mf = json.loads(...manifest.json...)["version"]
pp = re.search(r'^version\s*=\s*"([^"]+)"', ...pyproject.toml..., re.M).group(1)
assert mf == pp, f"version drift: manifest {mf} != pyproject {pp}"
```

Wer nur eine der beiden Dateien bumpt, bekommt einen roten Build. Die
**Laufzeit-Quelle der Wahrheit** ist `manifest.json` (Geräte-`sw_version`,
Karten-Version); `pyproject.toml` trägt dieselbe Zahl als Metadatum.

### 2.2 Zwei CI-Fallen, die real zugeschlagen haben

**Falle 1 — `ruff format --check .` formatiert auch Markdown.** Der Punkt am
Ende ist wörtlich zu nehmen: *das ganze Repo*, nicht nur Python-Dateien.
Neuere ruff-Versionen formatieren **Python-Codeblöcke innerhalb von Markdown**
(also Fences, die als Sprachkennung `python` tragen). Weil CI ruff
**frisch installiert**, kann eine
nächtliche Validate-Runde auf **unverändertem** `main` rot werden, sobald ruff
eine neue Version veröffentlicht — genau das passierte am 24.07.2026 und wurde
in Commit `ea8286a` („CI: format markdown code block for new ruff") mit einer
Umformatierung von `docs/CONSUMPTION_FORECAST.md` behoben.

> **Regel für alle Markdown-Dateien in diesem Repo — inklusive dieser
> Wissensbasis: Code-Fences NIE mit der Sprachkennung `python` öffnen.
> Immer nur die nackten drei Backticks.**
> Ein Block ohne Sprachkennung wird von ruff nicht angefasst. Die
> Wissensbasis-Dokumente 00–07 halten sich durchgängig daran.

**Falle 2 — der HACS-Job flaked.** `hacs/action@main` lädt zur Laufzeit weitere
Actions nach und läuft dabei gelegentlich in ein **HTTP 429** (Rate-Limit) beim
Download. Das ist kein Repo-Fehler: **Job neu starten genügt**. Nicht
„reparieren", nicht am Workflow drehen.

### 2.3 `scratchpad/` und CI — die genaue Mechanik

`scratchpad/` enthält Wegwerf-Reproduktionsskripte (Forensik-Repros,
Golden-Generator, Ablations- und Fuzz-Läufe). Es ist:

- **nicht committet** (in `git status` als `?? scratchpad/`) und deshalb für die
  CI **unsichtbar** — der Checkout enthält es nie;
- aber **weder in `.gitignore` noch in `extend-exclude` von ruff eingetragen**.

Das hat eine praktische Konsequenz, die man einmal erlebt haben muss:
**lokal** schlägt `ruff format --check .` an Scratchpad-Dateien fehl (Stand
25.07.2026: 8 von 100 Dateien „would be reformatted", alle unter
`scratchpad/`), **CI aber nicht**. Vor einem Push also entweder
`ruff format custom_components tests docs` gezielt laufen lassen oder die
Scratchpad-Treffer bewusst ignorieren — sie sind kein Blocker.

### 2.4 `.github/workflows/release.yml`

Trigger: **`release: published`** (also erst, wenn ein GitHub-Release wirklich
veröffentlicht wurde). Zwei Jobs:

1. **`validate`** — HACS- und Hassfest-Validierung noch einmal.
2. **`release`** (nur nach grünem `validate`, `permissions: contents: write`) —
   checkt **`ref: main`** aus (nicht das Tag! das Release-Event lässt den Runner
   sonst in detached HEAD, aus dem kein Push auf `main` auflösbar ist — genau
   daran scheiterte früher die Push-Action) und **synchronisiert die
   Versionsstrings auf das Tag**: `sed` in `manifest.json` und `pyproject.toml`,
   dann Commit + Push **nur wenn sich wirklich etwas geändert hat**
   (`git diff --staged --quiet`-Guard, damit ein „nichts zu tun"-Release den
   Workflow nicht rot macht).

Im Normalfall ist dieser Sync ein No-op, weil das Release aus einem Commit
geschnitten wird, der die Version bereits trägt. Er ist das Sicherheitsnetz
gegen ein vergessenes Bumpen.

---

## 3. Release-Konvention

### 3.1 Die Schritte

1. **Version bumpen** in **`custom_components/battery_manager/manifest.json`
   UND `pyproject.toml`** (Versions-Gate!).
2. **CHANGELOG-Eintrag** nach **Keep a Changelog** (`## [0.16.2] - 2026-07-25`
   mit `### Fixed` / `### Added` / `### Changed`; oben steht ein leerer
   `## [Unreleased]`-Abschnitt). Der Eintrag erklärt **das Verhalten und das
   Warum**, nicht die geänderten Dateien — siehe den v0.16.2-Eintrag als
   Vorbild.
3. `ruff format` + `ruff check`, volle Suite unter WSL grün.
4. Commit, Push auf `main`, **CI grün abwarten**.
5. `gh release create vX.Y.Z --target main --title "vX.Y.Z" --notes "…"`.
   **HACS trackt Releases, nicht Commits** — ohne diesen Schritt kommt beim
   Betreiber nichts an.
6. Deploy und Neustart übernimmt der Operator (Rezept in Dokument 05 §3).

### 3.2 Commit-Stil

Titel nennt die **F-Nummer(n)** und die Version, Body erklärt Symptom und
Mechanik, Footer trägt den Co-Author-Trailer. Beispiele aus der Historie:

```
F11: latch hold follows the planner via a shadow plan (v0.16.2)
F10: break the F5 latch deadlock - reset clears the latch + opportunistic latch hold (v0.16.1)
F9: seamless plan continuation at raster edges + tank prediction is notification-only
F8+F4: rec-flicker continuation window + telemetry-freeze guard for rec-only loads
Release v0.13.1: G4 floor guard — surplus loads never run grid-fed
CI: format markdown code block for new ruff, sync pyproject version to 0.16.0
```

Reine Release-Commits heißen `Release vX.Y.Z: <Einzeiler des Kernnutzens>`.

### 3.3 Release-Notes

Liegen als `docs/orders/RELEASE_NOTES_vX.Y.Z.md` im Repo (v0.16.0, v0.16.1,
v0.16.2 vorhanden) und werden als `--notes` verwendet. Stil: **Fließtext für
Menschen**, ein Absatz je Fix, jeweils Symptom → Ursache → Mechanik → Wirkung,
zum Schluss die Testzahl („433 tests green"). Keine Datei-Listen.

### 3.4 Feature-Doku-Pflicht

> **Jede neue Verhaltensregel bekommt ein `docs/F-*.md`.**

Der Bestand (Stand v0.16.2): `F-EXECUTOR-GUARDS`, `F-GATE-PARITY`,
`F-GATE-TOPUP`, `F-LOAD-PRIORITY`, `F-NIGHT-RESCUE`, `F-PERDAY-SURPLUS`,
`F-PLANNER-HONESTY`, `F-PREDRAIN`, `F-QUANTILE-BANDS`, `F-RECONFIGURE-PV`,
`F-RESCUE-EXPORT`, `F-RESIDUAL-TOPUP`, `F-ROBUST-POWER`, `F-SEAMLESS-RUNS`,
`F-STRICT-SURPLUS`, `F-SUBHOUR-ALLOCATION`, `F-TANK`.

Aufbau eines F-Dokuments: **Befund** (mit Live-Daten und Zeitstempeln) →
**Regeln** durchnummeriert (R1, R2, …) → **Abgrenzung** (was bewusst *nicht*
getan wird) → **Tests**. Die R-Nummern werden im Code als Kommentar-Anker
zitiert (`# F-STRICT-SURPLUS R2: …`) — so findet man von jeder Codezeile zur
Begründung und zurück.

Der Code verweist außerdem auf `docs/LOAD_CONTROL.md` (aktuell) und
`docs/DC_TOPOLOGY.md` (Spezifikation, teils stale). `ARCHITECTURE.md`,
`ALGORITHM.md`, `REQUIREMENTS.md` und `STRATEGY.md` sind **v0.7.x-Stand** und
werden nur noch als Namensregister der D-Regeln benutzt (siehe Dokument 05 §0).

### 3.5 `docs/orders/` — Aufträge über Repo-Grenzen

Die PV-Prognose kommt aus dem Schwester-Repo
`danielr0815/balcony-solar-forecast`. **Prognose-Wurzelprobleme werden nicht im
Battery Manager „weggefixt"**, sondern als **selbsttragender Arbeitsauftrag**
formuliert und dort eingekippt. Das Muster steht in
`docs/orders/balcony-solar-forecast-auftrag-2026-07-24.md`; die Antwort des
anderen Repos landet daneben (`…-antwort-2026-07-24.md`).

Aufbau eines Auftrags:

1. **Kontext** in zwei Sätzen + der ausdrückliche Hinweis, dass der Text
   selbsttragend ist (eine frische Session im Zielrepo kann ihn direkt
   übernehmen).
2. Je Befund: **Befund (CONFIRMED, mit Datumsspanne)** — harte Zahlen, Ist vs.
   Modell, konkrete Uhrzeiten, und die **Folgewirkung im BM** in kWh.
3. **Gewünschte Maßnahmen**, nach Priorität nummeriert, mit konkreten
   Konstantennamen und Dateistellen im *Zielrepo*.
4. **Ausdrücklich NICHT gewünscht** — die Abgrenzung, damit der Empfänger nicht
   die naheliegende, aber falsche Lösung baut (Beispiel: „kein harter
   Scalar-Clamp auf 1,3–1,5, der würde echte Unterschätzungstage bestrafen").
5. **Abnahme-Kriterium, vom BM aus messbar** — also formuliert als Zustand einer
   BM-Entity, z. B. „`quantile_coverage[<heute>].coverage > 0,5` mit
   `source != scalar` über den Tagesverlauf".

BM-seitig sind nur **Robustheit und Fallbacks** legitim (α-Skalar,
`p10 == p90`-Heuristik) — keine Kompensation eines fremden Modellfehlers.

---

## 4. Code- und Review-Konventionen

### 4.1 Wohin gehört was

- **Kern-Logik nach `core/`** — HA-frei, pur, frozen dataclasses rein/raus,
  keine Seiteneffekte. Die Grenze ist hart: kein einziger
  `homeassistant`-Import unter `core/`. Sie ist praktisch wichtig, weil der
  Coordinator `plan()` pro Zyklus **zweimal** ruft, wenn ein F11-Latch-Kandidat
  existiert (Schatten-Plan) — das ginge nur, weil `plan` garantiert
  seiteneffektfrei ist.
- **Zustands- und Schaltlogik nach `coordinator.py`.**
- **Konstanten nach `const.py`** — mit **Begründungs-Kommentar**. Zwei
  Ausnahmen wohnen bewusst in `core/optimize.py` und werden nach `const.py`
  re-exportiert (`GATE_TOPUP_MIN_WH`, `MERGE_TERMINAL_RAMP_WH`), weil ihr
  Konsument der pure Kern ist, den die Kern-Testumgebung ohne das HA-Paket
  importiert.

### 4.2 Kommentar-Stil

Der Code kommentiert **das Warum**, nicht das Was — und zwar in einer sehr
spezifischen Form, die man beim Weiterschreiben treffen sollte:

- **Jede Konstante trägt ihre Begründung**, inklusive der Live-Beobachtung, aus
  der sie stammt. Vorbild `const.STALE_LOAD_SOC_MIN`: „*die
  Fossibot-Integration serviert Cached-Values mit FRISCHEN Zeitstempeln, ein
  Alters-Check greift also nicht; ihre Kadenz ist ~1 min, 12 min eingefrorener
  SOC bei ~500 W Bezug sind also eindeutig*" — plus der Nachsatz
  „*Deliberately a constant, not a config key*".
- **Operator-Regeln werden mit Datum zitiert**, oft im Originalwortlaut:
  `operator rule 2026-07-18`, `operator wish 2026-07-24: "the dehumidifier can
  ALWAYS run when the power would be there"`, `operator design 2026-07-24`. So
  ist später entscheidbar, ob eine Regel noch gilt oder überholt wurde.
- **Vorfälle werden mit Zeitstempel und Zahl benannt**: „*2026-07-05 live
  incident: 11 h × 22 Wh planned vs. ~4.4 kWh real*", „*live 2026-07-24 flap
  window: PV 1391-1680 W vs a ~410 W dehumidifier, yet the old unconditional
  branch forced it off for ~67 min*". Diese Kommentare sind der eigentliche
  Regressionsschutz — sie verhindern, dass jemand die Bedingung „vereinfacht".
- **Was bewusst NICHT gemacht wird, steht dabei** („*A plain last_changed
  watchdog is deliberately avoided: cached values carry fresh timestamps*").

### 4.3 Die MUTANT-Regel

> **Nach jedem Agenten-Review:**
> `grep -rn MUTANT custom_components tests`

Hintergrund: Review-Agenten setzen zur Verifikation gezielt **Mutationen** in
den Code (um zu prüfen, ob ein Test sie fängt) und markieren sie mit `MUTANT`.
Bei der v0.15.0-Runde blieben **drei Mutanten im Arbeitsbaum liegen**. Ein
grüner Testlauf schützt davor nicht zuverlässig — ein Mutant, den kein Test
fängt, ist gerade der gefährliche Fall. Der Grep gehört deshalb **vor jeden
Commit nach einem Review**.

### 4.4 Weitere stehende Regeln

- **Vor jedem Push:** `ruff format` **und** `ruff check`. Der Formatter kommt
  zuerst — sonst meldet der Check Dinge, die der Formatter ohnehin löst.
- **Tests sind Pflicht** für jede Verhaltensänderung; ein neuer Executor-Zweig
  ohne Test in `tests/ha/test_load_switching.py` ist unvollständig.
- **Nicht aus der Verworfen-Liste vorschlagen:** Dokument 06 §3 führt bewusst
  abgelehnte Ideen. Vor jedem Verbesserungsvorschlag dort nachsehen.
- **Unverhandelbare Operator-Regeln** (gelten für jeden Change):
  (a) **Strict-Surplus** — Zusatzlasten kaufen nie Import und laufen nie
  netzgespeist; (b) Lasten **so spät wie möglich**, gerade früh genug um Export
  zu vermeiden; (c) Geräte mit Verbrauchsbehälter werden **nie vorsorglich**
  gedrosselt, weil ein Zustand *vermutet* wird — Prognosen dürfen nur
  benachrichtigen; (d) eine gelatchte Last darf **nie strengeren Regeln**
  unterliegen als dieselbe Last im Normalzustand (F11-Prinzip).

---

## 5. Kurz-Checkliste vor dem Push

```
1. ruff format .            (Scratchpad-Treffer ignorieren, s. §2.3)
2. ruff check custom_components tests
3. wsl -e bash -lc "cd /mnt/c/Users/jj/claude_space/battery-manager-ha \
     && ~/bmha-venv/bin/python -m pytest tests -q"
4. grep -rn MUTANT custom_components tests      (nach Agenten-Reviews)
5. Goldens: unbeabsichtigt gewandert?  -> Change prüfen, nicht Golden
6. manifest.json == pyproject.toml     (Versions-Gate)
7. CHANGELOG-Eintrag (Keep a Changelog) + ggf. docs/F-*.md
8. Markdown: Code-Fences ohne Sprachkennung "python"
```

---

**Verifikation: geprüft gegen Commit `a172d48` (v0.16.2).**

Wichtigste Anker dieses Dokuments:

1. `.github/workflows/validate.yml` — die vier Jobs, `ruff format --check .`
   (Repo-weit, inkl. Markdown) und das inline Versions-Gate.
2. `.github/workflows/release.yml` — `release: published`, `ref: main` gegen
   detached HEAD, Versions-Sync mit `git diff --staged --quiet`-Guard.
3. `pyproject.toml` — Version (Metadatum), ruff- und pytest-Konfiguration,
   Dev-Dependency-Group.
4. `tests/core/test_golden_topology.py` / `test_golden_night_predrain.py` —
   Bit-Identitäts-Anspruch und die beiden Regenerationswege.
5. `tests/ha/test_load_switching.py` (Block „Hermetic executor test") — das
   Listener-Kappen-Muster für Executor-Tests.
