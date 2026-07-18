"""Tests for the robust planning-power estimator (docs/F-ROBUST-POWER.md)."""

from datetime import datetime, timedelta

from core.power_learning import (
    FAST_WINDOW_S,
    GAP_CAP_S,
    WARMUP_COVERAGE_S,
    WINDOW_S,
    robust_power_estimate,
)

T0 = datetime(2026, 7, 18, 6, 0)


def _stream(*segments, cadence_s=30.0, start=T0):
    """Build (ts, watts) samples from (duration_s, watts) segments."""
    samples = []
    t = start
    for duration_s, watts in segments:
        end = t + timedelta(seconds=duration_s)
        while t < end:
            samples.append((t, watts))
            t += timedelta(seconds=cadence_s)
    return samples, t


def test_818_regression_transient_never_learned():
    """THE incident: a stable 426 W run with ONE 1711 W transient held 60 s
    (inverter-transfer compressor restart) must never move the estimate —
    no 818-class value at any evaluation step after warm-up."""
    samples, now = _stream((1200.0, 426.0), (60.0, 1711.0), (300.0, 426.0))
    probe = T0 + timedelta(seconds=WARMUP_COVERAGE_S + 30)
    while probe <= now:
        est = robust_power_estimate([s for s in samples if s[0] <= probe], probe)
        if est.watts is not None:
            assert 420.0 <= est.watts <= 470.0, (
                f"estimate {est.watts} left the 426 W band at {probe}"
            )
        probe += timedelta(seconds=60)


def test_short_dip_ignored_sustained_dip_adopted():
    """A defrost-style 150 W dip of 8 min is ignored; the same dip lasting
    20 min is the window majority and IS adopted (durably low = real)."""
    short, now_s = _stream((1500.0, 426.0), (480.0, 150.0))
    est_short = robust_power_estimate(short, now_s)
    assert est_short.watts is not None and est_short.watts >= 400.0

    long, now_l = _stream((1500.0, 426.0), (1200.0, 150.0))
    est_long = robust_power_estimate(long, now_l)
    assert est_long.watts is not None and est_long.watts <= 160.0


def test_sustained_low_adopts_via_median_majority():
    """The slow median flips once the new level covers >50 % of the window
    (~15 min) even without the fast-adopt path (oscillation-free ramp)."""
    samples, now = _stream((900.0, 426.0), (16 * 60.0, 210.0))
    est = robust_power_estimate(samples, now)
    assert est.watts is not None and est.watts <= 215.0


def test_fast_adopt_stable_10min():
    """Operator rule: ~10 stable minutes of a new level re-learn quickly —
    500 W for 20 min, then 300 W stable for 10 min -> ~300 (fast_adopted).
    An oscillating tail (250/600 alternating) must NOT fast-adopt."""
    samples, now = _stream((1200.0, 500.0), (FAST_WINDOW_S + 30.0, 300.0))
    est = robust_power_estimate(samples, now)
    assert est.watts is not None and abs(est.watts - 300.0) < 1e-6
    assert est.fast_adopted

    osc = [(60.0, 250.0), (60.0, 600.0)] * 5
    samples2, now2 = _stream((1200.0, 500.0), *osc)
    est2 = robust_power_estimate(samples2, now2)
    assert not est2.fast_adopted
    assert est2.watts is not None and est2.watts >= 400.0


def test_warmup_returns_none():
    """Below WARMUP_COVERAGE_S of accepted coverage there is NO estimate —
    a pure-inrush run start can never be served or learned."""
    samples, now = _stream((WARMUP_COVERAGE_S - 60.0, 1711.0))
    est = robust_power_estimate(samples, now)
    assert est.watts is None
    assert est.coverage_s < WARMUP_COVERAGE_S


def test_single_sample_never_clears_warmup():
    """Review finding (2026-07-18): GAP_CAP_S == WARMUP_COVERAGE_S, so ONE
    sample followed by silence reaches exactly the coverage bar — it must
    STILL not produce an estimate (a lone transient caught by the first
    poll would otherwise be served and learned verbatim)."""
    one = [(T0, 1711.0)]
    est = robust_power_estimate(one, T0 + timedelta(seconds=GAP_CAP_S))
    assert est.watts is None
    # Two samples where the newest has zero weight (evaluated at its own
    # timestamp): still only one weight-bearing sample -> no estimate.
    two = [(T0, 1711.0), (T0 + timedelta(seconds=GAP_CAP_S), 426.0)]
    est2 = robust_power_estimate(two, T0 + timedelta(seconds=GAP_CAP_S))
    assert est2.watts is None


def test_fast_adopt_needs_two_weightbearing_samples():
    """A single accepted glitch held by GAP_CAP through a sensor outage
    must not fast-adopt once it dominates the fast window — the stability
    test is vacuous on one sample."""
    samples, last = _stream((1500.0, 426.0))
    samples.append((last, 150.0))  # one glitch, then the sensor goes silent
    # ~10 min later the fast window holds (almost) only the glitch.
    est = robust_power_estimate(samples, last + timedelta(seconds=595.0))
    assert not est.fast_adopted
    assert est.watts is not None and est.watts >= 400.0


def test_time_weighting_sparse_vs_chatty_equivalent():
    """A sparse (240 s cadence) and a chatty (5 s cadence) sensor with the
    same physical shape give equivalent estimates, and a long silence after
    a spike credits it with at most GAP_CAP_S of weight."""
    chatty, now_c = _stream((1200.0, 426.0), cadence_s=5.0)
    sparse, now_s = _stream((1200.0, 426.0), cadence_s=240.0)
    est_c = robust_power_estimate(chatty, now_c)
    est_s = robust_power_estimate(sparse, now_s)
    assert est_c.watts is not None and est_s.watts is not None
    assert abs(est_c.watts - est_s.watts) < 1.0

    # Spike then 20 min of silence: the spike's weight is capped at
    # GAP_CAP_S, so the pre-spike level keeps the majority.
    samples, last = _stream((900.0, 426.0))
    samples.append((last, 1711.0))
    now = last + timedelta(seconds=1200.0)
    est = robust_power_estimate(samples, now)
    assert est.watts is not None and est.watts <= 470.0
    # sanity: the spike really was capped, not zero-weighted
    assert est.coverage_s <= 900.0 + GAP_CAP_S + 1.0


def test_window_prune_old_level_has_no_influence():
    """A level older than WINDOW_S is fully aged out of the estimate."""
    samples, now_mid = _stream((1200.0, 800.0), (WINDOW_S + 300.0, 426.0))
    est = robust_power_estimate(samples, now_mid)
    assert est.watts is not None and abs(est.watts - 426.0) < 1e-6
