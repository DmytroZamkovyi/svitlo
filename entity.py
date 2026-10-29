"""Base entity."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .runtime import SvitloRuntime


class SvitloEntity(Entity):
    """Entity refreshed through the runtime's dispatcher signal."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime: SvitloRuntime, key: str, device: str) -> None:
        self.runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = runtime.device(device)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.runtime.signal, self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
