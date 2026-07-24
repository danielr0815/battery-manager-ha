# F-TANK — consumable-tank runtime model + saturation feed-back (F5, V6)

Verified 7-day forensics (2026-07-24). The cellar dehumidifier is a surplus
load (~409 W learned, power feedback `sensor.fritz_powerline_546e_power`). When
its water tank is full the device shuts down internally and draws only ~2 W,
although the control switch and the recommendation stay ON.

The existing power-deviation warning (F-L7, v0.12.0) latches correctly in that
state (real draw below the tolerance band for the dwell). Two problems remained:

- **F5 (confirmed):** the planner kept booking full 409 W slots against the 2 W
  reality — 5.4 kWh of phantom plan on 24.07, ~1.2 kWh on 21.07 07:34–11:07.
  That skewed T\*, the grid-import and lost-surplus forecast and could displace
  other loads. Tank-full saturation cost the week ~4.8 kWh of export.
- **V6 (operator request):** tank-full is roughly predictable from the runtime
  counter since the last emptying.

## F5 — the latched power warning feeds planning

While a load's F-L7 power warning is **latched**, the optimizer plans that load
with its **measured** draw (`SurplusLoadState.saturated_power_w`, ~2 W) instead
of the learned/nominal power. Practically the load then books no energy, stops
displacing others, and T\*/forecasts become honest.

Invariants:

- **Recommendation and switch stay ON-capable** (deadlock ban). The latch clears
  only when the device runs in-band again, so switching the load off — or
  dropping it from the recommendation as "saturated" — would make the
  tank-emptied restart undetectable and the latch could never clear. F5 changes
  **only the planning power**; the executor/recommendation logic is untouched.
- **The learned power is not poisoned.** 2 W readings sit below the standby bar
  (`max(10 W, 25 % × nominal)`), so the median estimator already discards them —
  `saturated_power_w` is a separate override that leaves `learned_power_w`
  intact, so the **latch release restores the normal planning power in the same
  cycle**.

`saturated_power_w` is the highest-precedence branch of
`SurplusLoadState.planning_power_w`. The coordinator sets it in
`_get_load_states` from the current feedback reading whenever the latch is on
(one cycle old — `_update_power_warnings` runs at the end of the cycle, like the
other coordinator latches). Neutral default `None` = not latched.

## F10 — breaking the F5 latch deadlock (recovery)

F5 is correct against phantom plans but created a **deadlock** (live 2026-07-24:
the cellar dehumidifier planned 0 kWh for the next day despite 6.5 kWh
lost_surplus, tank already emptied, counter reset — yet the latch stood):

- F5 plans a latched load at its **measured ~0 W**, so the planner books no
  slots and the BM **never commands the load** again.
- But the F-L7 latch clears **only** while the load runs **BM-commanded and back
  in band** (`_update_power_warnings`: active requires
  `_load_charging_active`/an active plan). No command → no in-band run → the
  latch can never release on its own.

In v0.15.1 the phantom full-power plan was the accidental self-healing path (it
kept commanding the load, so the emptied tank surfaced). F5 removed that side
effect, so F10 adds two **explicit** recovery paths:

### 1. The reset button clears the latch

`reset_load_runtime` (the per-load reset button) already means **"tank emptied"**
since V6. F10 makes it also **clear a latched power warning**: it pops the
deviation timer and calls `_set_power_warning(..., False)`. The pending tank-full
capture is dropped **before** the release, so the release path learns **no**
premature sample (a manual reset is the operator asserting the cause is fixed,
not an observed full-tank cycle). The next cycle then plans the load at normal
power again. **Self-correcting:** if the tank is really still full, the power
collapses again on the next run and the latch returns after the F-L7 dwell.

### 2. Opportunistic latch hold (nobody pressed reset)

Operator rule 2026-07-24: *"the dehumidifier can ALWAYS run when the power would
be there, not only every few hours."* So instead of probing on a timer, while a
**switchable** load (control switch **and** power feedback) is latched the
executor **holds it ON** for as long as the surplus gates hold (`_latch_hold_ok`
+ the `latch hold` branch of `_apply_load_switching`). It runs whenever a surplus
run may legitimately run — **never grid-fed**:

- no floor guard (SOC above the inverter floor);
- inverter recommendation on;
- the current slot-0 forecast PV power **covers** (`>=`) the load's **effective
  power** — the **same** planner-G4-parity PV source the floor guard uses
  (`inputs.slots[0].pv_wh / duration`);
- the min_off dwell allows an ON (only relevant after a previous gate-loss stop).

**Effective power** = the learned (nominal fallback) power, raised to the
**measured** draw when that clears the standby bar (`max(10 W, 25 % × nominal)`).
A latch can also mean the outlet draws **more** than configured (a foreign
consumer); the gate then tests PV against the real draw, not the too-small
config. A full tank (~2 W, below the bar) keeps the learned power, so the device
only runs when PV could cover its **real** run.

The hold drives the **normal** actuation path (INFO log reason `latch hold`, V9a
style) and sets `_load_charging_active[sub] = True` so the existing F-L7 release
path can supervise the run. There is **no interval and no auto-stop** — the run
continues until one of two things happens:

- **Tank emptied** → the device runs in band → the latch **releases** (V6
  auto-reset + learning as usual), the F5 override lifts, and the normal plan
  takes over the run **seamlessly** from the next cycle.
- **A gate drops** (PV falls below the effective power, the recommendation goes
  off, or the floor guard trips) → the load stops via the **normal** path: a G4
  floor-guard stop is immediate/dwell-exempt, any other gate loss keeps the full
  `min_runtime` dwell. The stop is classified **flicker-ineligible** (a gate
  loss is not a recommendation flap, so it never seeds an F8 continuation), and a
  later re-hold is guarded only by the usual `min_off` dwell. While the tank is
  really full the device draws ~2 W, so the hold is energetically ~free; an
  emptied tank is exactly the surplus run we want.

## V6 — the tank model (opt-in, per load)

Opt-in via the per-load option **`tank_full_runtime_min`** (default `0` = off;
only meaningful for a load with a power-feedback sensor). The operator enters a
starting estimate; the feature then refines it by learning.

1. **Runtime since emptying** = the existing `active_runtime` counter
   (v0.7.18). The `reset_runtime` button now also means **"tank emptied"**.
2. **Auto-reset.** When the tank was surely full (F-L7 latch active — sustained
   low power despite ON) and the load then runs for real again (power back in
   band → latch release), the tank was evidently emptied → the runtime counter
   is reset to 0. The runtime reached at the latch **entry** is taken as a
   learning sample first.
3. **Learning.** The learned full-tank runtime is the **median of the last 5**
   tank-full samples (runtime at the latch entry — the tank-full event, not the
   release). Samples are persisted like the F-L7 latch. Until a sample exists,
   the configured `tank_full_runtime_min` is used.
4. **Notification.** When the predicted remaining tank RUN time drops below
   60 min (based on the load's planned upcoming run, not wall-clock time) a
   single push "tank nearly full — please empty it" is sent via the existing
   power-warning notify targets. Once per tank cycle (latch → reset); no spam.
5. **NO planner curtailment** (operator rule, 2026-07-24): the tank prediction
   is deliberately **not** fed into the planner. A dehumidifier is NEVER
   switched off — or booked shorter — preemptively because the tank *might* be
   full. The device stops **itself** when the tank really is full, and that
   real event is what the BM reacts to: power collapse → F-L7 latch → F5 plans
   at the measured ~0 W. Prediction informs the human (notification), reality
   informs the planner (F5).
6. **Diagnostics.** Remaining-runtime prognosis, learned full-tank runtime and
   the sample count are exposed as attributes on the per-load `active_runtime`
   sensor (present only while the feature is opted in).

### Safety anchor

A misconfiguration (`tank_full_runtime_min = 0` / feature off) reproduces
**exactly today's behaviour** — no notification, no diagnostics; planning is
identical in BOTH cases because the prediction never touches the planner.
Loads without the tank option (Fossibots and others) are unaffected.
