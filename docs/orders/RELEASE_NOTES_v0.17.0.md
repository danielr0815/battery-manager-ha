Security hardening, fail-safe automation and a full review round — update recommended, two breaking changes to check first.

**Breaking — check before updating.** (1) The export services no longer write
anywhere under `/config`: plain exports go to `<config>/battery_manager/`,
downloads to `<config>/www/battery_manager/`, only `.txt`/`.json` are allowed,
and custom `file_path` automations outside those directories now fail loudly
(previously they could overwrite HA-internal state like `.storage`). (2) Only
a single Battery Manager instance is accepted — a second config entry is
aborted. Everything else is additive or a fix.

**Security round.** The export services were the only write-surface outside
the entities: beyond the path confinement above, download files are now
deleted automatically after one hour — `/local/` is served without login,
and the learned-profile export contains household presence patterns (when
nobody is home). A startup sweep removes leftovers, and the download
notification says plainly what is exposed. Service failures are raised as
real errors instead of only appearing in the log.

**Fail-safe on sustained data loss.** If the SOC or PV forecast feeds stay
stale past their caches (6 h / 72 h), the entities already went unavailable —
but running loads kept their state. Now, after 2 more hours of data loss, all
managed surplus loads are force-switched off once (charge-enable always, the
plug follows your `input_off_policy`), with a repair issue and push
notification until recovery. A new adaptive watchdog also detects a *frozen*
house-battery SOC sensor (value unchanged while the plan expects real battery
flow) and treats it as data loss, and three consecutive support-switch
failures escalate to a repair issue instead of retrying silently forever.

**Operability.** Battery capacity, SOC bounds, PV and charger/inverter
ratings are finally editable in the options flow — no more delete-and-recreate
(which used to sacrifice subentries and learned data). Recorder problems can
no longer stall the nightly learning run (5-minute timeout with a
self-resolving repair issue), both local stores survive version mismatches,
diagnostics include the redacted subentry options, and the services carry
proper names, descriptions and a typed config-entry selector.

**Planner core.** Slot-0 energies were systematically overestimated by up to
59 s per plan run, negative/NaN PV forecasts could act as phantom load, and
counter-reset hours were learned as 0 W consumption — all fixed, together
with fail-fast input validation. The pass-1/pass-2 gate pipeline was
consolidated into shared helpers with golden snapshots proving bit-identical
behaviour.

**Dashboard card.** Defensive validation throughout (a malformed entity can
no longer crash the render), proper error display, and a full accessibility
pass: keyboard navigation, `aria-label`, screen reader summary and a
WCAG-AA-checked, theme-overridable palette.

561 tests green. Coverage gates now enforced in CI: planner core 100 %,
integration total ≥ 95 %.
