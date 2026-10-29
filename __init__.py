"""Svitlo: Yasno outage schedule, real power state and statistics."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .runtime import SvitloRuntime
from .store import SvitloStore

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.EVENT,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = SvitloRuntime(hass, entry)
    await runtime.async_start()  # may raise ConfigEntryNotReady
    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await SvitloStore(hass, entry.entry_id).async_remove()
