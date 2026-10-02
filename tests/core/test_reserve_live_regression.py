"""Anonymous September forecast: retain energy when tomorrow needs no headroom.

The fixture keeps the observed plant physics and hourly energies, without
entity IDs, optional loads or recorded decisions. Assertions concern physical
outcomes, not a snapshot of the implementation's intermediate envelopes.
"""

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from core.model import (
    BatteryParams,
    ControlParams,
    ConverterParams,
    HourSlot,
    PlanInputs,
    PVParams,
    ReserveParams,
    SupportParams,
    SystemConfig,
)
from core.simulate import simulate

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "reserve_september_forecast.json"
HA_TIME_ZONE = ZoneInfo("Europe/Berlin")


@pytest.fixture
def september_forecast() -> tuple[SystemConfig, PlanInputs]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    values = data["config"]
    config = SystemConfig(
        battery=BatteryParams(**values["battery"]),
        charger=ConverterParams(**values["charger"]),
        inverter=ConverterParams(**values["inverter"]),
        pv=PVParams(**values["pv"]),
        control=ControlParams(**values["control"]),
        support=SupportParams(**values["support"]),
        reserve=ReserveParams(**values["reserve"]),
    )
    slots = []
    for index, (timestamp, duration, pv_wh, ac_wh, dc_wh) in enumerate(data["slots"]):
        start = datetime.fromisoformat(timestamp).astimezone(HA_TIME_ZONE)
        slots.append(HourSlot(index, start, duration, start.hour, pv_wh, ac_wh, dc_wh))
    inputs = PlanInputs(
        datetime.fromisoformat(data["now"]).astimezone(HA_TIME_ZONE),
        data["start_soc_percent"],
        tuple(slots),
    )
    return config, inputs


@pytest.mark.parametrize("pv_scale", [1.0, 1.2], ids=["forecast", "upper-pv"])
def test_september_today_and_tomorrow_need_no_ac_preparation(
    september_forecast, pv_scale
):
    config, recorded = september_forecast
    horizon_day = recorded.now.date() + timedelta(days=2)
    inputs = replace(
        recorded,
        slots=tuple(slot for slot in recorded.slots if slot.start.date() < horizon_day),
    )
    result = simulate(
        config, inputs, config.control.inverter_min_soc_percent, pv_scale=pv_scale
    )

    assert result.flows[0].inverter_limit_w == pytest.approx(0, abs=1e-6)
    assert sum(flow.inverter_output_wh for flow in result.flows) == pytest.approx(
        0, abs=1e-6
    )
    assert result.total_export_wh == pytest.approx(0, abs=1e-6)
    assert result.max_soc_percent < config.battery.soc_max_percent
    if pv_scale == 1.2:
        # Independently replayed upper scenario remains below the physical 95%
        # ceiling, so AC preparation would throw away needed stored energy.
        assert result.max_soc_percent == pytest.approx(91.7396, abs=1e-4)


@pytest.mark.parametrize("third_day_sun", ["recorded", "peak"])
def test_september_day_three_is_considered_without_a_midnight_reset(
    september_forecast, third_day_sun
):
    config, recorded = september_forecast
    third_day = recorded.now.date() + timedelta(days=2)
    bounded = replace(
        recorded,
        slots=tuple(slot for slot in recorded.slots if slot.start.date() < third_day),
    )
    sunny_third_day = replace(
        recorded,
        slots=tuple(
            replace(slot, pv_wh=config.pv.peak_power_w * slot.duration)
            if third_day_sun == "peak"
            and slot.start.date() == third_day
            and 7 <= slot.hour_of_day < 19
            else slot
            for slot in recorded.slots
        ),
    )
    if third_day_sun == "peak":
        assert sum(slot.pv_wh for slot in sunny_third_day.slots) > sum(
            slot.pv_wh for slot in recorded.slots
        )
    assert all(
        slot.pv_wh <= config.pv.peak_power_w * slot.duration
        for slot in sunny_third_day.slots
    )

    short = simulate(config, bounded, config.control.inverter_min_soc_percent)
    full = simulate(config, sunny_third_day, config.control.inverter_min_soc_percent)
    # A distant deadline can now use earlier AC opportunities, but nominal
    # DC coverage and battery limits still apply to the complete horizon.
    assert (
        full.reserve_decision.preparation_horizon_end
        > short.reserve_decision.preparation_horizon_end
    )
    assert full.min_soc_percent >= config.battery.soc_min_percent
    assert full.max_soc_percent <= config.battery.soc_max_percent + 1e-6
    assert sum(f.unserved_dc_wh for f in full.flows) == pytest.approx(0)
    assert any(f.inverter_output_wh > 0 for f in full.flows)
