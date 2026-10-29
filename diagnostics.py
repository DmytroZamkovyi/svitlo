"""Diagnostics."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HOUSE_ID,
    CONF_HOUSE_NAME,
    CONF_POWER_TEMPLATE,
    CONF_STREET_ID,
    CONF_STREET_NAME,
)

TO_REDACT = {
    CONF_STREET_ID,
    CONF_STREET_NAME,
    CONF_HOUSE_ID,
    CONF_HOUSE_NAME,
    CONF_POWER_TEMPLATE,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    machine = runtime.power.machine
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "address": async_redact_data(dict(coordinator.address), TO_REDACT),
        "group": coordinator.group_key,
        "last_update_success": coordinator.last_update_success,
        "stale": coordinator.data.stale if coordinator.data else None,
        "schedule_versions": len(runtime.store.versions),
        "power": {
            "state": machine.state,
            "pending": machine.pending,
            "outages": len(machine.outages),
            "tracking_since": (
                machine.tracking_since.isoformat() if machine.tracking_since else None
            ),
        },
    }
