"""O12: independent measured plant through reserve planning, live AC and actors.

The plant integrates measured power; it never reads planner trajectories or
calls the production simulator. Every hour is a new measured initial state.
These synthetic days establish safety and accounting, not field savings.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from homeassistant.util import dt as dt_util
from test_coordinated_actuation import BLOCK, DCDC, LIMIT, PSU24, PSU48
from test_coordinated_actuation import rig as rig
from test_market_adapter import publish, rows
from test_reserve_policy import configure, update


@dataclass
class MeasuredPlant:
    stored_wh: float = 4000
    import_wh: float = 0
    dc_grid_wh: float = 0
    export_wh: float = 0
    unserved_dc_wh: float = 0
    useful_ac_wh: float = 0

    def step(self, hass, pv_w, ac_w, hours):
        # This fixture's independent hardware contract: 5 kWh, 5–95%,
        # ideal battery/inverter/DC-DC, PSUs with 80% efficiency, 50W per rail.
        dc24 = hass.states.is_state(PSU24, "on")
        dc48 = hass.states.is_state(PSU48, "on")
        native24 = hass.states.is_state(DCDC, "on")
        inverter_w = float(hass.states.get(LIMIT).state)
        assert not (dc24 and native24)
        assert not (dc48 and inverter_w > 0)
        assert (inverter_w == 0) == hass.states.is_state(BLOCK, "on")
        grid_dc = (50 * dc24 + 50 * dc48) / 0.8
        dc_demand = 50 * (not dc48) + 50 * (not dc24 and native24)
        if not dc24 and not native24:
            self.unserved_dc_wh += 50 * hours
        available_w = pv_w + max(0, self.stored_wh - 250) / hours
        dc_supplied = min(dc_demand, available_w)
        self.unserved_dc_wh += (dc_demand - dc_supplied) * hours
        available_w -= dc_supplied
        ac_supplied = min(ac_w, inverter_w, available_w)
        battery_w = pv_w - dc_supplied - ac_supplied
        before = self.stored_wh
        self.stored_wh = min(4750, self.stored_wh + battery_w * hours)
        assert self.stored_wh >= 250 - 1e-6
        self.export_wh += max(0, before + battery_w * hours - 4750)
        self.import_wh += (grid_dc + ac_w - ac_supplied) * hours
        self.dc_grid_wh += grid_dc * hours
        self.useful_ac_wh += ac_supplied * hours
        return ac_w - ac_supplied


@pytest.mark.parametrize(
    "start,hours,sun",
    [
        ("2026-10-02T00:00:00Z", 48, False),
        ("2026-03-28T23:00:00Z", 23, True),
        ("2026-10-24T22:00:00Z", 25, True),
    ],
)
async def test_measured_multiday_and_dst_plant_preserves_dc_priority(
    rig, hass, freezer, start, hours, sun
):
    await hass.config.async_set_time_zone("Europe/Berlin")
    began = datetime.fromisoformat(start.replace("Z", "+00:00"))
    c, calls, *_ = rig
    freezer.move_to(began)
    configure(c, hass, 80, sun=8 if sun else 0)
    c.raw_config.update(
        battery_charge_efficiency=1,
        battery_discharge_efficiency=1,
        charger_efficiency=1,
        charger_standby_power_w=0,
        inverter_efficiency=1,
        inverter_standby_power_w=0,
        dcdc_efficiency=1,
        dc24_share_percent=50,
        native48_base_w=0,
        psu24_efficiency=0.8,
        psu48_efficiency=0.8,
        ac_base_load_w=200,
        ac_variable_load_w=0,
        dc_base_load_w=100,
        dc_variable_load_w=0,
        operation_pv_power_entity="sensor.actual_pv",
        operation_house_power_entity="sensor.house",
        operation_import_power_entity="sensor.actual_import",
        market_enabled=True,
        market_price_entity="sensor.epex_spot_data_price",
    )
    plant = MeasuredPlant()
    import_w = 200
    seen_offsets = set()
    for hour in range(hours):
        now = began + timedelta(hours=hour)
        freezer.move_to(now)
        local = dt_util.as_local(now)
        seen_offsets.add(local.utcoffset())
        pv = 1000 if sun and 9 <= local.hour < 17 else 0
        house = 1400 if local.hour == 19 else 200  # unannounced AC spike
        for entity, value, unit in (
            ("sensor.test_soc", plant.stored_wh / 50, "%"),
            ("sensor.actual_pv", pv, "W"),
            ("sensor.house", house, "W"),
            ("sensor.actual_import", import_w, "W"),
        ):
            hass.states.async_set(
                entity, str(value), {"unit_of_measurement": unit}, force_update=True
            )
        hass.states.async_set("binary_sensor.grid", "on", force_update=True)
        # Partial price coverage tests energy-only fallback over the real horizon.
        publish(hass, rows(now, values=(100, 300, 100)))
        data = await update(c)
        assert data["plan_metadata"]["captured_at"]
        assert data["source_health"]
        await c.live_ac.run()
        assert c.inverter_control_snapshot()["confirmed"]
        import_w = plant.step(hass, pv, house, 1)
        assert plant.unserved_dc_wh == 0
    assert plant.import_wh >= plant.dc_grid_wh >= 0
    assert 250 <= plant.stored_wh <= 4750
    assert len(calls) > 0
    if sun:
        assert len(seen_offsets) == 2
    else:
        assert plant.import_wh > 48 * 200
    c.cleanup()
