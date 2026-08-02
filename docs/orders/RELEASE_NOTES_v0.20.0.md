At-max top-up for the powerstations, and the phantom-import fix that frees the pre-drain block on strong-sun days.

**At-max top-up (F-PEAK-FILL).** With the house battery sitting at max and
PV surplus fluctuating below its charge power, a powerstation with budget
left used to stall or pulse while the surplus clipped. Pass 1 may now book
those slots anyway: the house battery buffers the difference inside a 5 %
hysteresis band at the max line, and the planner's own gates prove the dip
refills from otherwise-lost export (the battery must still reach max SOC
the same day, no added import). Afternoon export spikes become Fossibot
charge instead of clipping — with the band check acting as the hysteresis:
from the band floor every run is rejected until PV refills. Reasons call it
`at-max top-up (peak fill)`; continuous loads keep their own buffer form
(the pass-3 block).

**Phantom import was silently vetoing the pre-drain block.** The AC
charger's 10 W standby was booked as grid import in every charging hour —
although the charger only ever runs on PV surplus, so that import was
always a modeling artifact. Over the 3-day planning horizon the artifacts
accumulated past the shared 50 Wh slack, and the pass-3 block was refused
on exactly the strong days it exists for (the 2026-08-03 analysis caught it
red-handed: the first 07:00 candidate died with 55.7 Wh of artifact import
against a 50 Wh budget). The standby now reduces the stored charge
honestly, with the charger ramping to cover its own standby — no import is
minted in surplus hours, and the battery still reaches soc_max exactly (no
charge asymptote disarming the full-line gates). The morning block now
books as intended: each day gets its own pre-drain, capped by the dynamic
SOC buffer as before.

588 tests green. Coverage gates unchanged: planner core 100 %, integration
total ≥ 95 %.
