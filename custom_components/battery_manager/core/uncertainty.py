"""Forecast uncertainty shared by planning and reserve simulation."""

from .model import PlanInputs

# Below 25 Wh forecast ratios are dominated by rounding/cold-start noise.
QUANTILE_RATIO_MIN_WH = 25.0


def quantile_band_slots(slots) -> list[bool]:
    """Per-slot band presence per F-QUANTILE-BANDS D2 — THE cold-start rule.

    A slot HAS a band iff p10/p90 data covers it, the median PV is at least
    QUANTILE_RATIO_MIN_WH, and the spread `p90 - p10` exceeds
    max(1.0 Wh, 1 % of pv_wh). A COLLAPSED band (p10 == p90, the balcony
    forecaster's cold-start signature) counts as NO band: it means "no
    evidence", NOT "no uncertainty" — treating it as certainty would make the
    Z4 stress WEAKER than the scalar alpha on exactly the bins that have no
    history yet. Single source of truth, shared by `_effective_uncertainty`
    and the coordinator's `quantile_coverage` diagnostics (R7).
    """
    band: list[bool] = []
    for slot in slots:
        band.append(
            slot.pv_p10_wh is not None
            and slot.pv_p90_wh is not None
            and slot.pv_wh >= QUANTILE_RATIO_MIN_WH
            and (slot.pv_p90_wh - slot.pv_p10_wh) > max(1.0, 0.01 * slot.pv_wh)
        )
    return band


def effective_uncertainty(
    inputs: PlanInputs, alpha: float, beta: float
) -> tuple[list[float], list[float], list[bool]]:
    """Per-slot stress/optimism vectors from the empirical P10/P90 bands
    (F-QUANTILE-BANDS D1/D3), with per-slot scalar fallback.

    Where a slot carries a band (D2), the ratios against the median replace
    the scalar dials: `stress = clamp(p10/pv, 0.1, 1.0)` and
    `optimism = clamp(p90/pv, 1.0, 2.0)` — the clamps guard junk ratios, and
    simulate()'s FIX-8 physical peak clamp additionally bounds optimism
    downstream. Everywhere else the scalars apply unchanged, so with no bands
    anywhere the vectors are uniform and the plan is bit-identical to the
    scalar-era behaviour at the same alpha/beta (R8); a partially covered day
    mixes evidence and fallback IN THE SAME simulation vector (R9).
    """
    band = quantile_band_slots(inputs.slots)
    stress: list[float] = []
    optimism: list[float] = []
    for slot, has_band in zip(inputs.slots, band, strict=True):
        if has_band:
            # quantile_band_slots() only flags slots whose p10/p90 both exist.
            assert slot.pv_p10_wh is not None and slot.pv_p90_wh is not None
            stress.append(min(1.0, max(0.1, slot.pv_p10_wh / slot.pv_wh)))
            optimism.append(min(2.0, max(1.0, slot.pv_p90_wh / slot.pv_wh)))
        else:
            stress.append(alpha)
            optimism.append(beta)
    return stress, optimism, band
