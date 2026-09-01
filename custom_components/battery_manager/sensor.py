"""Sensor platform for the Battery Manager integration."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    ATTR_GRID_EXPORT_KWH,
    ATTR_LAST_UPDATE,
    CONF_EXPORT_METER_ENTITY,
    CONF_FEEDIN_ENABLED,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
    DOMAIN,
    ENTITY_EARLY_FEED_IN_REALIZED_TODAY,
    ENTITY_FEEDIN_MODE,
    ENTITY_GRID_IMPORT_FORECAST,
    ENTITY_HOURS_TO_MAX_SOC,
    ENTITY_LOST_SURPLUS,
    ENTITY_LOST_SURPLUS_REALIZED_TODAY,
    ENTITY_LOST_SURPLUS_TODAY,
    ENTITY_LOST_SURPLUS_TOMORROW,
    ENTITY_MAX_SOC_FORECAST,
    ENTITY_MIN_SOC_FORECAST,
    ENTITY_PREVENTED_EXPORT_REALIZED_TODAY,
    ENTITY_PREVENTED_EXPORT_TODAY,
    ENTITY_PREVENTED_EXPORT_TOMORROW,
    ENTITY_SOC_FORECAST_CURVE,
    ENTITY_SOC_THRESHOLD,
    ENTITY_SUPPORT_DC24_MODE,
    ENTITY_SUPPORT_DC48_MODE,
    ENTITY_TRUE_EXPORT_ENERGY,
    SUBENTRY_TYPE_CASCADE,
    SUBENTRY_TYPE_LOAD,
    SUPPORT_MODE_AUTO,
    SUPPORT_MODE_MANUAL,
)
from .coordinator import BatteryManagerCoordinator
from .entity import BatteryManagerEntity, async_add_by_subentry

SENSOR_DESCRIPTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": ENTITY_SOC_THRESHOLD,
        "data_key": "soc_threshold_percent",
        "translation_key": "soc_threshold",
        "unit": PERCENTAGE,
        "icon": "mdi:battery-arrow-down",
    },
    {
        "key": ENTITY_MIN_SOC_FORECAST,
        "data_key": "min_soc_forecast_percent",
        "translation_key": "min_soc_forecast",
        "unit": PERCENTAGE,
        "icon": "mdi:battery-low",
    },
    {
        "key": ENTITY_MAX_SOC_FORECAST,
        "data_key": "max_soc_forecast_percent",
        "translation_key": "max_soc_forecast",
        "unit": PERCENTAGE,
        "icon": "mdi:battery-high",
    },
    {
        "key": ENTITY_HOURS_TO_MAX_SOC,
        "data_key": "hours_to_max_soc",
        "translation_key": "hours_to_max_soc",
        "unit": UnitOfTime.HOURS,
        "icon": "mdi:clock-outline",
    },
    {
        "key": ENTITY_GRID_IMPORT_FORECAST,
        "data_key": "grid_import_kwh",
        "translation_key": "grid_import_forecast",
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "icon": "mdi:transmission-tower-import",
    },
    {
        "key": ENTITY_LOST_SURPLUS,
        "data_key": "lost_surplus_kwh",
        "translation_key": "lost_surplus",
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "icon": "mdi:transmission-tower-export",
    },
)

# F-REALIZED-SURPLUS (docs/F-REALIZED-SURPLUS.md): the measured (realized)
# surplus accounting sensors. All read the coordinator's `realized` data
# block (only present with an export meter configured — the same gate these
# sensors are created under). Sensors 1-6 are day counters (reset at local
# midnight -> state_class TOTAL); the true-export total is monotone
# (TOTAL_INCREASING) so the energy dashboard can consume it. Sensor 7
# (realized early feed-in) is NOT here — it rides on the F-FEEDIN gate and
# reads the executor integral directly.
REALIZED_SENSOR_DESCRIPTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": ENTITY_LOST_SURPLUS_REALIZED_TODAY,
        "data_key": "lost_surplus_realized_kwh",
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:transmission-tower-export",
    },
    {
        "key": ENTITY_LOST_SURPLUS_TODAY,
        "data_key": "lost_surplus_today_kwh",
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:transmission-tower-export",
    },
    {
        "key": ENTITY_LOST_SURPLUS_TOMORROW,
        "data_key": "lost_surplus_tomorrow_kwh",
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:transmission-tower-export",
    },
    {
        "key": ENTITY_PREVENTED_EXPORT_REALIZED_TODAY,
        "data_key": "prevented_export_realized_kwh",
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:transmission-tower-off",
    },
    {
        "key": ENTITY_PREVENTED_EXPORT_TODAY,
        "data_key": "prevented_export_today_kwh",
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:transmission-tower-off",
    },
    {
        "key": ENTITY_PREVENTED_EXPORT_TOMORROW,
        "data_key": "prevented_export_tomorrow_kwh",
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:transmission-tower-off",
    },
    {
        "key": ENTITY_TRUE_EXPORT_ENERGY,
        "data_key": "true_export_total_kwh",
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "icon": "mdi:counter",
    },
)


def _per_day_attrs(daily: list[dict[str, Any]], value_key: str) -> dict[str, Any]:
    """today_kwh / tomorrow_kwh / daily for a forecast sensor (F-PERDAY-SURPLUS R2).

    ``today`` is the date of slot 0 (the first, chronological entry); ``tomorrow``
    is that day + 1. A day the planning horizon lacks renders 0.0. ``value_key``
    selects the metric ("lost_surplus_kwh", "grid_import_kwh" or — §5 v2 —
    "loads_kwh"); the full daily list (all metrics) is exposed as the single
    dashboard source.
    """
    by_date = {entry["date"]: entry[value_key] for entry in daily}
    today = date.fromisoformat(daily[0]["date"]) if daily else None
    tomorrow = today + timedelta(days=1) if today is not None else None
    return {
        "today_kwh": by_date.get(today.isoformat(), 0.0) if today is not None else 0.0,
        "tomorrow_kwh": (
            by_date.get(tomorrow.isoformat(), 0.0) if tomorrow is not None else 0.0
        ),
        "daily": daily,
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Battery Manager sensors."""
    coordinator: BatteryManagerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[Entity] = [
        BatteryManagerSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    entities.append(BatteryManagerSocForecastSensor(coordinator))
    # Manual/automatic mode per support PSU (F-N2) — only when the
    # respective switch is configured; a leftover sensor of a removed
    # switch is dropped from the registry instead of lingering.
    ent_reg = er.async_get(hass)
    for entity_key, conf_key, data_key in (
        (ENTITY_SUPPORT_DC24_MODE, CONF_SUPPORT_DC24_SWITCH, "support_dc24_mode"),
        (ENTITY_SUPPORT_DC48_MODE, CONF_SUPPORT_DC48_SWITCH, "support_dc48_mode"),
    ):
        if coordinator.raw_config.get(conf_key):
            entities.append(SupportModeSensor(coordinator, entity_key, data_key))
        else:
            stale = ent_reg.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.entry_id}_{entity_key}"
            )
            if stale:
                ent_reg.async_remove(stale)
    # Auto/manual mode of early grid feed-in (F-FEEDIN R7) — only while the
    # feature is enabled in the options; a leftover sensor of a disabled
    # feature is dropped from the registry instead of lingering.
    if coordinator.raw_config.get(CONF_FEEDIN_ENABLED):
        entities.append(FeedInModeSensor(coordinator))
    else:
        stale = ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{ENTITY_FEEDIN_MODE}"
        )
        if stale:
            ent_reg.async_remove(stale)
    # F-REALIZED-SURPLUS: the realized day counters + corrected export total
    # exist only while the export meter is configured (docs/F-REALIZED-SURPLUS.md
    # sensor set 1-6 + 8); leftovers of a removed meter are dropped from the
    # registry instead of lingering. Sensor 7 (realized early feed-in) rides
    # on the F-FEEDIN gate alone — independent of the export meter.
    if coordinator.raw_config.get(CONF_EXPORT_METER_ENTITY):
        entities.extend(
            RealizedSurplusSensor(coordinator, description)
            for description in REALIZED_SENSOR_DESCRIPTIONS
        )
    else:
        for description in REALIZED_SENSOR_DESCRIPTIONS:
            stale = ent_reg.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.entry_id}_{description['key']}"
            )
            if stale:
                ent_reg.async_remove(stale)
    if coordinator.raw_config.get(CONF_FEEDIN_ENABLED):
        entities.append(EarlyFeedInRealizedSensor(coordinator))
    else:
        stale = ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{ENTITY_EARLY_FEED_IN_REALIZED_TODAY}"
        )
        if stale:
            ent_reg.async_remove(stale)
    # Real active-runtime counter per surplus load (v0.7.18), scoped to its
    # subentry so it is removed automatically when the load is deleted (v0.7.19).
    per_subentry: dict[str, list[Entity]] = {}
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_TYPE_LOAD:
            per_subentry[subentry_id] = [
                SurplusLoadRuntimeSensor(coordinator, subentry_id, subentry.title),
                SurplusLoadPlanningPowerSensor(
                    coordinator, subentry_id, subentry.title
                ),
            ]
        elif subentry.subentry_type == SUBENTRY_TYPE_CASCADE:
            per_subentry[subentry_id] = [
                CascadeModeSensor(coordinator, subentry_id, subentry.title),
                CascadeSocSensor(coordinator, subentry_id, subentry.title),
            ]
    async_add_by_subentry(async_add_entities, entities, per_subentry)


class CascadeModeSensor(BatteryManagerEntity, SensorEntity):
    """Executor phase and energy/recovery diagnostics for a cascade."""

    _unrecorded_attributes = frozenset({"member_details", "schedule"})
    _attr_icon = "mdi:source-branch"
    _attr_translation_key = "cascade_mode"

    def __init__(self, coordinator, subentry_id: str, title: str) -> None:
        super().__init__(coordinator, f"cascade_mode_{subentry_id}", subentry_id)
        self._subentry_id = subentry_id
        self._attr_translation_placeholders = {"name": title}

    def _plan(self) -> dict[str, Any]:
        return ((self.coordinator.data or {}).get("cascade_plans") or {}).get(
            self._subentry_id, {}
        )

    @property
    def native_value(self) -> str | None:
        return self._plan().get("phase")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._plan()


class CascadeSocSensor(BatteryManagerEntity, SensorEntity):
    """Capacity-weighted member SOC, with explicit cache staleness."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "cascade_soc"

    def __init__(self, coordinator, subentry_id: str, title: str) -> None:
        super().__init__(coordinator, f"cascade_soc_{subentry_id}", subentry_id)
        self._subentry_id = subentry_id
        self._attr_translation_placeholders = {"name": title}

    def _plan(self) -> dict[str, Any]:
        return ((self.coordinator.data or {}).get("cascade_plans") or {}).get(
            self._subentry_id, {}
        )

    @property
    def native_value(self) -> float | None:
        value = self._plan().get("aggregate_soc_percent")
        return round(float(value), 1) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self._plan()
        return {
            "stale": bool(plan.get("aggregate_soc_stale")),
            "members": plan.get("members", []),
        }


class BatteryManagerSensor(BatteryManagerEntity, SensorEntity):
    """A value from the last planning run."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: BatteryManagerCoordinator, description: dict[str, Any]
    ) -> None:
        super().__init__(coordinator, description["key"])
        self._data_key = description["data_key"]
        self._attr_translation_key = description["translation_key"]
        self._attr_native_unit_of_measurement = description["unit"]
        self._attr_icon = description.get("icon")
        if "device_class" in description:
            self._attr_device_class = description["device_class"]

    @property
    def native_value(self) -> float | int | None:
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get(self._data_key)
        if isinstance(value, float):
            return round(value, 2)
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        attrs = {ATTR_LAST_UPDATE: str(data.get("last_update", ""))}
        if self._data_key == "grid_import_kwh":
            attrs[ATTR_GRID_EXPORT_KWH] = data.get("grid_export_kwh")
        # F-PERDAY-SURPLUS R2: the today/tomorrow split and per-day list on the
        # lost-surplus and grid-import forecast sensors. NB (V10): the sensor
        # STATE is the whole-horizon sum (all forecast days), NOT a "today"
        # figure — e.g. 7.9 kWh at 05:00 spans every forecast day while that one
        # day realised 0.58 kWh. `daily` / today_kwh / tomorrow_kwh carry the
        # per-calendar-day breakdown (mirrored on the soc_forecast sensor).
        if self._data_key in ("lost_surplus_kwh", "grid_import_kwh"):
            attrs.update(
                _per_day_attrs(data.get("daily_surplus") or [], self._data_key)
            )
        return attrs


class SupportModeSensor(BatteryManagerEntity, SensorEntity):
    """Manual/automatic control mode of a support PSU (F-N2).

    'manual' while the PSU was switched on externally: the integration
    keeps hands off until it is switched off externally again.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [SUPPORT_MODE_AUTO, SUPPORT_MODE_MANUAL]
    _attr_icon = "mdi:hand-back-right-outline"

    def __init__(
        self, coordinator: BatteryManagerCoordinator, key: str, data_key: str
    ) -> None:
        super().__init__(coordinator, key)
        self._data_key = data_key
        self._psu_key = "dc48" if "dc48" in data_key else "dc24"
        self._attr_translation_key = key

    @property
    def available(self) -> bool:
        # Reflects persisted mode — known and in sync with the always-available
        # manual switch even while an update is failing (review #15).
        return True

    @property
    def native_value(self) -> str:
        return (
            SUPPORT_MODE_MANUAL
            if self.coordinator.support_manual(self._psu_key)
            else SUPPORT_MODE_AUTO
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        # Surface the R2 voltage-controller diagnostic on the 48 V mode sensor
        # so the log-only shakedown and live regulation are observable.
        if self._psu_key != "dc48":
            return None
        return {"controller": self.coordinator.dc48_controller_diagnostic()}


class RealizedSurplusSensor(BatteryManagerEntity, SensorEntity):
    """A realized (measured) surplus accounting value (F-REALIZED-SURPLUS,
    docs/F-REALIZED-SURPLUS.md).

    Reads the coordinator's `realized` data block — created only while the
    export meter is configured, which is exactly when that block exists. The
    day counters reset at local midnight (state_class TOTAL); the true-export
    total is monotone (TOTAL_INCREASING) and energy-dashboard compatible.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self, coordinator: BatteryManagerCoordinator, description: dict[str, Any]
    ) -> None:
        super().__init__(coordinator, description["key"])
        self._data_key = description["data_key"]
        # The translation key matches the entity key by construction.
        self._attr_translation_key = description["key"]
        self._attr_state_class = description["state_class"]
        self._attr_icon = description.get("icon")

    @property
    def native_value(self) -> float | None:
        realized = (self.coordinator.data or {}).get("realized")
        if not realized:
            return None
        return realized.get(self._data_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {ATTR_LAST_UPDATE: str(data.get("last_update", ""))}


class EarlyFeedInRealizedSensor(BatteryManagerEntity, SensorEntity):
    """Realized early feed-in energy today (F-REALIZED-SURPLUS sensor 7).

    Gated on the F-FEEDIN feature alone — unlike the other realized sensors
    it does not need the export meter, so it reads the executor's delivered
    integral (persisted in the `realized` store block) directly instead of
    the `realized` data block.
    """

    _attr_translation_key = ENTITY_EARLY_FEED_IN_REALIZED_TODAY
    _attr_icon = "mdi:transmission-tower-export"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator: BatteryManagerCoordinator) -> None:
        super().__init__(coordinator, ENTITY_EARLY_FEED_IN_REALIZED_TODAY)

    @property
    def available(self) -> bool:
        # Reflects the persisted counter — usable even without plan data
        # (same pattern as the load runtime sensor).
        return True

    @property
    def native_value(self) -> float:
        return self.coordinator.feedin_delivered_today_kwh()


class FeedInModeSensor(BatteryManagerEntity, SensorEntity):
    """Auto/manual mode of early grid feed-in (F-FEEDIN R7, docs/F-FEEDIN.md).

    'manual' while the operator owns the AC setpoint (changed externally): the
    integration keeps hands off until the next midnight, then resumes.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [SUPPORT_MODE_AUTO, SUPPORT_MODE_MANUAL]
    _attr_icon = "mdi:transmission-tower-export"
    _attr_translation_key = ENTITY_FEEDIN_MODE

    def __init__(self, coordinator: BatteryManagerCoordinator) -> None:
        super().__init__(coordinator, ENTITY_FEEDIN_MODE)

    @property
    def available(self) -> bool:
        # Reflects persisted mode — known and in sync with the always-available
        # runtime switch even while an update is failing (review #15 pattern).
        return True

    @property
    def native_value(self) -> str:
        return (
            SUPPORT_MODE_MANUAL
            if self.coordinator.feedin_manual()
            else SUPPORT_MODE_AUTO
        )


class BatteryManagerSocForecastSensor(BatteryManagerEntity, SensorEntity):
    """Forecasted SOC curve: state = SOC in one hour, attribute = full curve.

    The `forecast` attribute contains [{t, soc}, ...] over the whole planning
    horizon (final trajectory incl. scheduled loads). The remaining attributes
    carry the full plan context (threshold, SOC limits, per-load schedules,
    detected appliance runs) so the bundled forecast card can render
    everything from this one entity; third-party cards such as ApexCharts
    work too (see README).
    """

    # No state_class: forecast values must not feed long-term statistics.
    # The bulky per-hour attributes are also kept out of the recorder.
    _unrecorded_attributes = frozenset(
        {
            "forecast",
            "loads",
            "appliances",
            "consumption_profile",
            "consumption_forecast",
            "cascades",
        }
    )
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:chart-timeline-variant"
    _attr_translation_key = "soc_forecast"

    def __init__(self, coordinator: BatteryManagerCoordinator) -> None:
        super().__init__(coordinator, ENTITY_SOC_FORECAST_CURVE)

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        curve = data.get("soc_forecast") or []
        if len(curve) > 1:
            return curve[1]["soc"]
        if curve:
            return curve[0]["soc"]
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        loads = [
            {
                "name": plan.get("name"),
                "active": plan.get("active"),
                "planned_energy_kwh": plan.get("planned_energy_kwh"),
                "planning_power_w": plan.get("planning_power_w"),
                "planning_power_source": plan.get("planning_power_source"),
                # Per-load today/tomorrow planned energy (coordinator, plan
                # slot-0 anchored) so the card renders a per-load heute/morgen
                # split just like the aggregate surplus figures.
                "today_kwh": plan.get("today_kwh"),
                "tomorrow_kwh": plan.get("tomorrow_kwh"),
                "schedule": plan.get("schedule") or [],
            }
            for plan in (data.get("load_plans") or {}).values()
            if not plan.get("managed_by_cascade")
        ]
        daily = data.get("daily_surplus") or []
        per_day_loads = _per_day_attrs(daily, "loads_kwh")
        return {
            "forecast": data.get("soc_forecast") or [],
            "soc_threshold_percent": data.get("soc_threshold_percent"),
            "grid_import_kwh": data.get("grid_import_kwh"),
            "lost_surplus_kwh": data.get("lost_surplus_kwh"),
            # F-PERDAY-SURPLUS R3: the per-day lost-surplus / import list, the
            # single source dashboard cards read (totals above stay untouched).
            "daily": daily,
            # F-PERDAY-SURPLUS §5 v2 (R-V2-2): per-day SURPLUS-LOAD energy as
            # today/tomorrow convenience scalars (slot-0 day / +1, 0.0
            # fallback — the exact v0.9.1 convention). Appliances are NOT
            # included: they enter the AC forecast, not `extra_ac_wh`.
            "loads_today_kwh": per_day_loads["today_kwh"],
            "loads_tomorrow_kwh": per_day_loads["tomorrow_kwh"],
            "loads": loads,
            "cascades": list((data.get("cascade_plans") or {}).values()),
            # Detected appliance runs (washer, dishwasher, …) as card lanes
            # (operator request 2026-08-08); [] when nothing is running.
            "appliances": data.get("appliance_plans") or [],
            "consumption_profile": data.get("consumption_profile") or {},
            # Consumption forecast card (v0.25.5): per-slot planned W split
            # by voltage level (AC / 48 V / 24 V) + planned surplus loads.
            "consumption_forecast": data.get("consumption_forecast") or [],
            "gate_calibration": data.get("gate_calibration") or {},
            # F-PREDRAIN observability (docs/F-PREDRAIN.md §3.5): per-day PV
            # source, the import the allocation added over base (bounded by the
            # 50 Wh artifact slack since F-STRICT-SURPLUS R1, not a trade), the
            # stressed lower-buffer reserve, and the per-day PV-window end hours.
            "pv_source": data.get("pv_source") or {},
            # F-QUANTILE-BANDS R7: per-day P10/P90 band coverage of daylight
            # slots ("p10/p90" | "scalar" | "mixed") — watch the bands mature.
            "quantile_coverage": data.get("quantile_coverage") or {},
            "import_trade_used_wh": data.get("import_trade_used_wh"),
            "stressed_min_soc": data.get("stressed_min_soc"),
            # F-NIGHT-RESCUE R7: end of the merge-bounded threshold horizon
            # (null = full-horizon scan).
            "threshold_horizon_end": data.get("threshold_horizon_end"),
            "pv_window_ends": data.get("pv_window_ends") or {},
            # F-REALIZED-SURPLUS R14: the measured day counters the card's
            # stats line renders. The key is OMITTED (not an empty dict) when
            # no export meter is configured — the card branches on the
            # attribute existing, and an empty object is truthy in JS, which
            # would render a permanent "(Ist 0.0)" instead of the unchanged
            # pure-forecast line.
            **({"realized": data["realized"]} if data.get("realized") else {}),
            **(data.get("plan_params") or {}),
        }


class SurplusLoadRuntimeSensor(BatteryManagerEntity, SensorEntity):
    """Real active runtime of a load in minutes (v0.7.18).

    Counts the minutes the load ACTUALLY runs — measured from its power
    feedback sensor when configured (so manual runs count too), otherwise from
    BM's charging state. Resettable via the matching button. TOTAL_INCREASING so
    long-term statistics treat a reset as a new period.
    """

    _attr_translation_key = "load_runtime"
    _attr_icon = "mdi:timer-play-outline"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self, coordinator: BatteryManagerCoordinator, subentry_id: str, title: str
    ) -> None:
        super().__init__(coordinator, f"load_runtime_{subentry_id}", subentry_id)
        self._subentry_id = subentry_id
        self._attr_translation_placeholders = {"name": title}

    @property
    def available(self) -> bool:
        # Reflects the persisted counter — usable even without plan data.
        return True

    @property
    def native_value(self) -> float:
        return round(self.coordinator.load_runtime_minutes(self._subentry_id), 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        # V6 (F-TANK): remaining tank runtime prognosis, learned full-tank
        # runtime and the sample count — present only when the tank model is
        # opted in for this load (else None, no attributes).
        return self.coordinator.tank_diagnostics(self._subentry_id)


class SurplusLoadPlanningPowerSensor(BatteryManagerEntity, SensorEntity):
    """Exact power scalar consumed by the latest plan for one surplus load."""

    _attr_translation_key = "load_planning_power"
    _attr_icon = "mdi:flash-outline"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: BatteryManagerCoordinator, subentry_id: str, title: str
    ) -> None:
        super().__init__(coordinator, f"load_planning_power_{subentry_id}", subentry_id)
        self._subentry_id = subentry_id
        self._attr_translation_placeholders = {"name": title}

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.load_planning_power_diagnostics(self._subentry_id).get(
            "planning_power_w"
        )
        return round(float(value), 1) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        diag = self.coordinator.load_planning_power_diagnostics(self._subentry_id)
        return {
            "source": diag["source"],
            "configured_power_w": diag["configured_power_w"],
            "learned_power_w": diag["learned_power_w"],
            "calibration": diag["calibration"],
        }
