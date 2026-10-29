"""Event entity: group/schedule changes and power transitions."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_TYPES
from .entity import SvitloEntity
from .runtime import SvitloRuntime

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SvitloEventEntity(entry.runtime_data)])


class SvitloEventEntity(SvitloEntity, EventEntity):
    _attr_event_types = EVENT_TYPES

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "changes", "yasno")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.runtime.event_signal, self._handle_event
            )
        )

    @callback
    def _handle_event(self, event_type: str, data: dict[str, Any]) -> None:
        self._trigger_event(event_type, data)
        self.async_write_ha_state()
