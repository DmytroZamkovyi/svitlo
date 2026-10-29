"""Sensors: schedule status, power timings, statistics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import CONF_GROUP, CONF_SUBGROUP, PERIODS
from .entity import SvitloEntity
from .runtime import SvitloRuntime
from .schedule import (
    STATES,
    current_state,
    day_summary,
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
        return {
            "timeline": timeline(self._snapshot, self.runtime.tz),
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
