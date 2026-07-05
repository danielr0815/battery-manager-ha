"""Config/options flow smoke tests (schema construction must never raise)."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DOMAIN,
)

ENTRY_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}


async def _setup_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_options_flow_renders_form(hass):
    """Regression: unit-less number selectors raised vol.Invalid, which the
    HTTP layer turns into a bare '400: Bad Request' on opening the flow."""
    entry = await _setup_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"


async def test_config_flow_all_steps_render(hass):
    """Every base-flow step must build its schema without raising."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "form"
    steps = [
        (ENTRY_DATA, "battery"),
        (
            {
                "battery_capacity_wh": 5000.0,
                "battery_min_soc_percent": 5.0,
                "battery_max_soc_percent": 95.0,
                "battery_charge_efficiency": 0.97,
                "battery_discharge_efficiency": 0.97,
            },
            "pv",
        ),
        (
            {
                "pv_max_power_w": 3200.0,
                "pv_morning_start_hour": 7,
                "pv_morning_end_hour": 13,
                "pv_afternoon_end_hour": 18,
                "pv_morning_ratio": 0.8,
            },
            "consumers",
        ),
        (
            {
                "ac_base_load_w": 50.0,
                "ac_variable_load_w": 75.0,
                "ac_variable_start_hour": 6,
                "ac_variable_end_hour": 20,
                "dc_base_load_w": 50.0,
                "dc_variable_load_w": 25.0,
                "dc_variable_start_hour": 6,
                "dc_variable_end_hour": 22,
            },
            "power",
        ),
    ]
    for user_input, next_step in steps:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
        assert result["type"] == "form", f"step before {next_step} failed"
        assert result["step_id"] == next_step
    hass.config_entries.flow.async_abort(result["flow_id"])


BASIC_CONTINUOUS = {
    "name": "Entfeuchter Test",
    "power_w": 400.0,
    "battery_tolerance_percent": 15.0,
    "min_runtime_min": 30,
    "energy_limited": False,
    "in_house_measurement": False,
    "power_warning_percent": 50.0,
}


async def test_load_subentry_flow_skips_storage_for_continuous_loads(hass):
    """Operator wish (2026-07-05): capacity/target-SOC/SOC-sensor and the
    charging-path fields make no sense for a continuous consumer — the
    storage step must not appear."""
    from custom_components.battery_manager.const import (
        CONF_LOAD_CAPACITY_WH,
        CONF_LOAD_CHARGE_ENABLE,
        CONF_LOAD_CONTROL_SWITCH,
        CONF_LOAD_INPUT_OFF_POLICY,
        CONF_LOAD_TARGET_SOC,
        SUBENTRY_TYPE_LOAD,
    )

    entry = await _setup_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_LOAD), context={"source": "user"}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    schema_keys = {str(k) for k in result["data_schema"].schema}
    for moved in (
        CONF_LOAD_CAPACITY_WH,
        CONF_LOAD_TARGET_SOC,
        CONF_LOAD_CONTROL_SWITCH,
        CONF_LOAD_CHARGE_ENABLE,
        CONF_LOAD_INPUT_OFF_POLICY,
    ):
        assert moved not in schema_keys, f"{moved} belongs to the storage step"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(BASIC_CONTINUOUS)
    )
    # No storage step: the subentry is created directly, with preserved
    # defaults for the hidden fields.
    assert result["type"] == "create_entry"
    sub = next(iter(entry.subentries.values()))
    assert sub.title == "Entfeuchter Test"
    assert sub.data[CONF_LOAD_CAPACITY_WH] == 2000.0
    assert sub.data[CONF_LOAD_TARGET_SOC] == 100.0
    assert sub.data[CONF_LOAD_INPUT_OFF_POLICY] == "auto"


async def test_load_subentry_flow_shows_storage_for_energy_limited(hass):
    """Energy-limited loads get the second step with the storage and
    charging-path fields."""
    from custom_components.battery_manager.const import (
        CONF_LOAD_CAPACITY_WH,
        CONF_LOAD_CONTROL_SWITCH,
        CONF_LOAD_INPUT_OFF_POLICY,
        CONF_LOAD_SOC_ENTITY,
        CONF_LOAD_TARGET_SOC,
        SUBENTRY_TYPE_LOAD,
    )

    entry = await _setup_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_LOAD), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**BASIC_CONTINUOUS, "name": "Fossibot Test", "energy_limited": True},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "storage"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_LOAD_CAPACITY_WH: 2000.0,
            CONF_LOAD_TARGET_SOC: 90.0,
            CONF_LOAD_SOC_ENTITY: "sensor.fossibot_soc",
            CONF_LOAD_CONTROL_SWITCH: "switch.fossibot_plug",
            CONF_LOAD_INPUT_OFF_POLICY: "auto",
        },
    )
    assert result["type"] == "create_entry"
    sub = next(iter(entry.subentries.values()))
    assert sub.data[CONF_LOAD_TARGET_SOC] == 90.0
    assert sub.data[CONF_LOAD_SOC_ENTITY] == "sensor.fossibot_soc"
    assert sub.data[CONF_LOAD_CONTROL_SWITCH] == "switch.fossibot_plug"


async def test_load_subentry_storage_step_validates_charging_path(hass):
    """The keep_on-requires-enable rule now lives in the storage step."""
    from custom_components.battery_manager.const import (
        CONF_LOAD_CAPACITY_WH,
        CONF_LOAD_CONTROL_SWITCH,
        CONF_LOAD_INPUT_OFF_POLICY,
        CONF_LOAD_TARGET_SOC,
        SUBENTRY_TYPE_LOAD,
    )

    entry = await _setup_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_LOAD), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**BASIC_CONTINUOUS, "name": "Fossibot Test", "energy_limited": True},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_LOAD_CAPACITY_WH: 2000.0,
            CONF_LOAD_TARGET_SOC: 90.0,
            CONF_LOAD_CONTROL_SWITCH: "switch.fossibot_plug",
            CONF_LOAD_INPUT_OFF_POLICY: "keep_on",  # no enable entity!
        },
    )
    assert result["type"] == "form"
    assert result["step_id"] == "storage"
    assert result["errors"] == {"base": "keep_on_requires_enable"}
