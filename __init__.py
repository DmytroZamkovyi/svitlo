"""Svitlo: Yasno outage schedule, real power state and statistics."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .runtime import SvitloRuntime, battery_signature
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
    entry.async_on_unload(entry.add_update_listener(_entry_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when batteries are added, changed or removed.

    Option changes reload through the options flow itself, so they are ignored here.
    """
    if battery_signature(entry) != entry.runtime_data.battery_signature:
        hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await SvitloStore(hass, entry.entry_id).async_remove()
