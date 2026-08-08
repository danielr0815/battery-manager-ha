A week-long live audit of the plant (2026-07-31 → 2026-08-07) put every
decision under the microscope — five fixes came out of it.

**The planner simulated sunshine that does not exist.** Forecast sources whose
hourly `wh_period` attributes are a DC model curve while the entity state
reports the AC day energy (balcony_solar_forecast, verified live: 6.129 kWh DC
vs. 5.649 kWh AC) fed the AC-side simulation ~8.5 % too much PV — about
1.1 kWh of phantom energy per normal day. On 2026-08-07 that contributed to an
avoidable 0.7 kWh grid import while the SOC fell to 13 %. The hourly maps —
median and p10/p90 bands alike — are now scaled by the ratio state ÷ Σ(curve)
derived from the same entity, clamped to [0.5, 1.0]; sources whose state and
curve already agree see no change, and explicit `wh_period_ac` attributes are
preferred once a forecaster offers them.

**The house-SOC watchdog cried wolf 37 times in 7 days.** Its mid-band drift
allowance (2 % of capacity ≈ 100 Wh) sat only two display steps above the
Victron source's 1 % quantization, so ordinary forecast wobble read as a
frozen sensor: ~7 hours of steering blackout in a week, one episode 65
seconds short of the 2-hour fail-safe that force-sheds every surplus load.
The default allowance is now 3 % — 2 % physical drift plus 1 % display
quantization. Installations that already saved a value keep it.

**A failed plug-ON orphaned the charge-enable gate for 3.4 days.** When the
enable switch confirmed ON but the plug switch failed (BLE RPC error), no
later path ever switched the gate off — voiding the firmware's protective
threshold the whole time. The ON sequence now rolls the enable back on
failure, and a dwell-exempt sweep turns off any gate that is physically on
while its load neither charges nor should charge.

**The freeze watchdogs forgot everything on reload.** The F4 telemetry-freeze
and G2 stale-SOC latches lived in memory only; a coordinator reload dropped a
latch and the recommendation duty-cycled 110 minutes for a verifiably
unplugged load (~225 Wh misbooked). The latches now persist their reference
values and accumulated evidence — downtime itself still does not count as
freeze evidence.

**The pre-drain log line rediscovered "on change".** The booking signature
ratcheted with the clock and logged 4,196 INFO lines in 7 days (1,753 in one
day). A booking now logs when it materializes or materially changes (start
shifted ≥ 30 min or energy ≥ 200 Wh), at most once per block per hour; the
rest goes to DEBUG.

685 tests green. Coverage gates unchanged: planner core 100 %, integration
total ≥ 95 %.
