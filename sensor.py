"""Sensors: schedule status, power timings, statistics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from . import battery as bat
from .batteries import BatteryManager
from .const import CONF_GROUP, CONF_SUBGROUP, PERIODS
from .entity import SvitloEntity
from .runtime import SvitloRuntime
from .schedule import (
    STATES,
    current_state,
    day_summary,
    emergency_on,
    next_outage_start,
    next_power_on,
    timeline,
)
from .stats import METRIC_KEYS

PARALLEL_UPDATES = 0
PROBABLE_STATES = ["none", "present", "unknown"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: SvitloRuntime = entry.runtime_data
    entities: list[SensorEntity] = [
        StatusSensor(runtime),
        NextOutageSensor(runtime),
        NextPowerOnSensor(runtime),
        GroupSensor(runtime),
        ScheduleUpdatedSensor(runtime),
        ProbableSensor(runtime),
        *[DaySensor(runtime, d) for d in DAY_DESCRIPTIONS],
        LastChangeSensor(runtime),
        StateDurationSensor(runtime),
    ]
    for period in PERIODS:
        for key in METRIC_KEYS:
            entities.append(StatSensor(runtime, period, STAT_DESCRIPTIONS[key]))
    async_add_entities(entities)
    registry = er.async_get(hass)
    for subentry_id, manager in runtime.batteries.items():
        batteries = battery_entities(manager)
        valid = {b.unique_id for b in batteries}
        # Drop sensors of earlier versions (24h / 7d / 30d windows) that no longer exist
        for item in er.async_entries_for_config_entry(registry, entry.entry_id):
            if (
                getattr(item, "config_subentry_id", None) == subentry_id
                and item.domain == "sensor"
                and item.unique_id not in valid
            ):
                registry.async_remove(item.entity_id)
        async_add_entities(batteries, config_subentry_id=subentry_id)


# --------------------------------------------------------------------- schedule
class YasnoSensor(SvitloEntity, SensorEntity):
    def __init__(self, runtime: SvitloRuntime, key: str) -> None:
        super().__init__(runtime, key, "yasno")

    @property
    def available(self) -> bool:
        return self.runtime.coordinator.data is not None

    @property
    def _snapshot(self):
        data = self.runtime.coordinator.data
        return data.snapshot if data else None


class StatusSensor(YasnoSensor):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATES

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "status")

    @property
    def native_value(self) -> str:
        return current_state(self._snapshot, dt_util.utcnow(), self.runtime.tz)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.runtime.coordinator.data
        today = dt_util.now(self.runtime.tz).date()
        return {
            "timeline": timeline(self._snapshot, self.runtime.tz),
            "emergency": emergency_on(self._snapshot, today),
            "stale": data.stale if data else None,
        }


class NextOutageSensor(YasnoSensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "next_outage")

    @property
    def native_value(self) -> datetime | None:
        return next_outage_start(self._snapshot, dt_util.utcnow(), self.runtime.tz)


class NextPowerOnSensor(YasnoSensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "next_power_on")

    @property
    def native_value(self) -> datetime | None:
        return next_power_on(self._snapshot, dt_util.utcnow(), self.runtime.tz)


class GroupSensor(YasnoSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "group")

    @property
    def native_value(self) -> str:
        return self.runtime.coordinator.group_key

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        address = self.runtime.coordinator.address
        return {"group": address[CONF_GROUP], "subgroup": address[CONF_SUBGROUP]}


class ScheduleUpdatedSensor(YasnoSensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "schedule_updated")

    @property
    def native_value(self) -> datetime | None:
        snap = self._snapshot
        return snap.updated_on if snap and snap.updated_on else None


class ProbableSensor(YasnoSensor):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = PROBABLE_STATES

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "probable")

    @property
    def native_value(self) -> str:
        data = self.runtime.coordinator.data
        if data is None or data.probable is None:
            return "unknown"
        return "present" if data.probable else "none"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.runtime.coordinator.data
        return {"slots": data.probable if data and data.probable else []}


_MIN = UnitOfTime.MINUTES
_PCT = {
    "native_unit_of_measurement": PERCENTAGE,
    "state_class": SensorStateClass.MEASUREMENT,
    "suggested_display_precision": 1,
}
_DUR = {
    "device_class": SensorDeviceClass.DURATION,
    "native_unit_of_measurement": _MIN,
    "suggested_unit_of_measurement": UnitOfTime.HOURS,
    "state_class": SensorStateClass.MEASUREMENT,
    "suggested_display_precision": 1,
}
_CNT = {"state_class": SensorStateClass.MEASUREMENT}
_DEV = {
    "native_unit_of_measurement": _MIN,
    "state_class": SensorStateClass.MEASUREMENT,
    "suggested_display_precision": 1,
}
_OFF = {"entity_registry_enabled_default": False}


@dataclass(frozen=True, kw_only=True)
class DayDescription(SensorEntityDescription):
    tomorrow: bool
    field: str


DAY_DESCRIPTIONS = [
    DayDescription(
        key=f"{prefix}_planned_{name}",
        translation_key=f"{prefix}_planned_{name}",
        tomorrow=prefix == "tomorrow",
        field=field,
        **extra,
    )
    for prefix in ("today", "tomorrow")
    for name, field, extra in (
        ("outage", "outage", _DUR),
        ("power", "power", _DUR),
        ("outage_count", "count", _CNT),
        ("longest_outage", "longest", _DUR),
        ("possible_outage", "possible", {**_DUR, **_OFF}),
    )
] + [
    DayDescription(
        key="today_planned_outage_remaining",
        translation_key="today_planned_outage_remaining",
        tomorrow=False,
        field="remaining",
        **_DUR,
    ),
]


class DaySensor(YasnoSensor):
    """Planned figures for today or tomorrow from the current schedule."""

    entity_description: DayDescription

    def __init__(self, runtime: SvitloRuntime, description: DayDescription) -> None:
        super().__init__(runtime, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | None:
        now = dt_util.utcnow()
        day = now.astimezone(self.runtime.tz).date()
        if self.entity_description.tomorrow:
            day += timedelta(days=1)
        summary = day_summary(self._snapshot, day, self.runtime.tz, now)
        return summary[self.entity_description.field] if summary else None


# ------------------------------------------------------------------------ power
class PowerSensor(SvitloEntity, SensorEntity):
    def __init__(self, runtime: SvitloRuntime, key: str) -> None:
        super().__init__(runtime, key, "power")


class LastChangeSensor(PowerSensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "last_change")

    @property
    def native_value(self) -> datetime | None:
        return self.runtime.power.machine.since


class StateDurationSensor(PowerSensor):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "state_duration")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(self.hass, self._tick, timedelta(minutes=1))
        )

    @callback
    def _tick(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        since = self.runtime.power.machine.since
        if since is None:
            return None
        return round((dt_util.utcnow() - since).total_seconds() / 60, 1)


# ------------------------------------------------------------------- statistics
@dataclass(frozen=True, kw_only=True)
class StatDescription(SensorEntityDescription):
    pass


def _d(key: str, **kw: Any) -> StatDescription:
    return StatDescription(key=key, translation_key=key, **kw)


STAT_DESCRIPTIONS: dict[str, StatDescription] = {
    d.key: d
    for d in [
        _d("actual_outage_percent", **_PCT),
        _d("scheduled_outage_percent", **_PCT),
        _d("outage_count", **_CNT),
        _d("outage_duration", **_DUR),
        _d("avg_outage_duration", **_DUR, **_OFF),
        _d("longest_outage", **_DUR),
        _d("longest_uptime", **_DUR),
        _d("planned_realized_percent", **_PCT),
        _d("outage_planned_percent", **_PCT),
        _d("matched_count", **_CNT, **_OFF),
        _d("unplanned_count", **_CNT),
        _d("unplanned_duration", **_DUR, **_OFF),
        _d("skipped_planned_count", **_CNT),
        _d("avg_start_deviation", **_DEV),
        _d("avg_end_deviation", **_DEV),
        _d("avg_power_off_time"),
        _d("avg_power_on_time"),
        _d("power_duration", **_DUR),
        _d("scheduled_outage_duration", **_DUR),
        _d("scheduled_power_duration", **_DUR),
        _d("scheduled_outage_count", **_CNT),
        _d("outage_vs_schedule", **_DEV),
        _d("schedule_changes", **_CNT),
        _d("avg_schedule_publish_time"),
    ]
}
assert set(STAT_DESCRIPTIONS) == set(METRIC_KEYS)


class StatSensor(SvitloEntity, SensorEntity):
    """One statistic for one period."""

    entity_description: StatDescription

    def __init__(
        self, runtime: SvitloRuntime, period: str, description: StatDescription
    ) -> None:
        super().__init__(runtime, f"{period}_{description.key}", f"stats_{period}")
        self.entity_description = description
        self._period = period
        # Entity names come from the metric, not from the unique key
        self._attr_translation_key = description.key

    def _value(self) -> tuple[Any, dict[str, Any]]:
        return self.runtime.stats.get(self._period, {}).get(
            self.entity_description.key, (None, {})
        )

    @property
    def native_value(self) -> Any:
        return self._value()[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self._value()[1] or None


# --------------------------------------------------------------------- batteries
@dataclass(frozen=True, kw_only=True)
class BatteryDescription(SensorEntityDescription):
    """A battery sensor: how to compute its value (and attributes) from the manager."""

    value_fn: Callable[[BatteryManager, datetime], Any]
    attrs_fn: Callable[[BatteryManager, datetime], dict[str, Any]] | None = None


def _rounded(value: float | None, digits: int = 0) -> float | int | None:
    return None if value is None else round(value, digits)


def _eta(hours: float | None, now: datetime) -> dict[str, Any]:
    return {"estimated_at": (now + timedelta(hours=hours)).isoformat()} if hours else {}


def battery_entities(manager: BatteryManager) -> list[SensorEntity]:
    """Sensors for the sensors and parameters configured for this battery."""
    cap = manager.cfg.capacity_kwh
    has_soc = manager.soc_id is not None
    descriptions: list[BatteryDescription] = [
        BatteryDescription(
            key="state",
            translation_key="battery_state",
            device_class=SensorDeviceClass.ENUM,
            options=[
                bat.STATE_CHARGING,
                bat.STATE_DISCHARGING,
                bat.STATE_IDLE,
                bat.STATE_UNKNOWN,
            ],
            value_fn=lambda m, now: m.live.state(now),
        ),
        BatteryDescription(
            key="charge_power",
            translation_key="battery_charge_power",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
            value_fn=lambda m, now: _rounded(m.live.charge_w(now)),
        ),
        BatteryDescription(
            key="load",
            translation_key="battery_load",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
            value_fn=lambda m, now: _rounded(m.live.discharge_w(now)),
        ),
    ]
    if manager.cfg.max_discharge_w:
        descriptions.append(
            BatteryDescription(
                key="load_percent",
                translation_key="battery_load_percent",
                native_unit_of_measurement=PERCENTAGE,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=0,
                value_fn=lambda m, now: _rounded(m.live.load_percent(now)),
            )
        )
    if has_soc:
        descriptions.append(
            BatteryDescription(
                key="soc",
                translation_key="battery_soc",
                device_class=SensorDeviceClass.BATTERY,
                native_unit_of_measurement=PERCENTAGE,
                state_class=SensorStateClass.MEASUREMENT,
                value_fn=lambda m, now: m.live.soc,
            )
        )
    if has_soc and cap:
        descriptions += [
            BatteryDescription(
                key="stored_energy",
                translation_key="battery_stored_energy",
                device_class=SensorDeviceClass.ENERGY_STORAGE,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=2,
                value_fn=lambda m, now: _rounded(m.live.stored_kwh(), 2),
            ),
            BatteryDescription(
                key="usable_energy",
                translation_key="battery_usable_energy",
                device_class=SensorDeviceClass.ENERGY_STORAGE,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=2,
                value_fn=lambda m, now: _rounded(m.live.usable_kwh(), 2),
            ),
            BatteryDescription(
                key="time_to_empty",
                translation_key="battery_time_to_empty",
                device_class=SensorDeviceClass.DURATION,
                native_unit_of_measurement=UnitOfTime.HOURS,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=1,
                value_fn=lambda m, now: _rounded(m.live.time_to_empty_h(now), 2),
                attrs_fn=lambda m, now: _eta(m.live.time_to_empty_h(now), now),
            ),
            BatteryDescription(
                key="time_to_full",
                translation_key="battery_time_to_full",
                device_class=SensorDeviceClass.DURATION,
                native_unit_of_measurement=UnitOfTime.HOURS,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=1,
                value_fn=lambda m, now: _rounded(m.live.time_to_full_h(now), 2),
                attrs_fn=lambda m, now: _eta(m.live.time_to_full_h(now), now),
            ),
        ]
    return [BatterySensor(manager, d) for d in descriptions]


class BatterySensor(SensorEntity):
    """One value of one battery, refreshed from the manager's signal."""

    entity_description: BatteryDescription
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: BatteryManager, description: BatteryDescription) -> None:
        self.manager = manager
        self.entity_description = description
        self._attr_unique_id = (
            f"{manager.entry.entry_id}_{manager.subentry_id}_{description.key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={manager.device_identifier()},
            name=manager.title,
            manufacturer="Svitlo",
            model="Battery",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.manager.signal, self._refresh)
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.manager, dt_util.utcnow())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return (fn(self.manager, dt_util.utcnow()) or None) if fn else None
