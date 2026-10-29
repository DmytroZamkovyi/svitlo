"""Binary sensors: actual power and scheduled outage."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .entity import SvitloEntity
from .runtime import SvitloRuntime
from .schedule import STATE_EMERGENCY, STATE_OUTAGE, current_state, day_summary

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: SvitloRuntime = entry.runtime_data
    async_add_entities(
        [
            PowerBinarySensor(runtime),
            ScheduledOutageBinarySensor(runtime),
            TomorrowScheduleBinarySensor(runtime),
        ]
    )


class PowerBinarySensor(SvitloEntity, BinarySensorEntity):
    """On = power is present (confirmed, debounced)."""

    _attr_device_class = BinarySensorDeviceClass.POWER

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "power", "power")

    @property
    def is_on(self) -> bool | None:
        return self.runtime.power.machine.state


class ScheduledOutageBinarySensor(SvitloEntity, BinarySensorEntity):
    """On = the schedule says there is an outage right now."""

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "scheduled_outage", "yasno")

    @property
    def available(self) -> bool:
        return self.runtime.coordinator.data is not None

    @property
    def is_on(self) -> bool:
        data = self.runtime.coordinator.data
        state = current_state(
            data.snapshot if data else None, dt_util.utcnow(), self.runtime.tz
        )
        return state in (STATE_OUTAGE, STATE_EMERGENCY)


class TomorrowScheduleBinarySensor(SvitloEntity, BinarySensorEntity):
    """On = the schedule for tomorrow is already published."""

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "tomorrow_schedule", "yasno")

    @property
    def available(self) -> bool:
        return self.runtime.coordinator.data is not None

    @property
    def is_on(self) -> bool:
        data = self.runtime.coordinator.data
        tz = self.runtime.tz
        tomorrow = dt_util.now(tz).date() + timedelta(days=1)
        return day_summary(data.snapshot if data else None, tomorrow, tz) is not None
