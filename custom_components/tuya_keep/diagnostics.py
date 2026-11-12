"""Diagnostics: counts and settings only, no keys and no entity names."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from . import TuyaKeepConfigEntry
from .const import (
    CONF_CLOUD,
    CONF_CLOUD_ACCESS_ID,
    CONF_CLOUD_ACCESS_SECRET,
    CONF_CLOUD_ENDPOINT,
    CONF_EXCLUDE,
    TUYA_DOMAIN,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TuyaKeepConfigEntry
) -> dict[str, Any]:
    """Return what helps to understand a problem, nothing personal."""
    options = dict(entry.options)
    cloud = dict(options.get(CONF_CLOUD) or {})
    options[CONF_CLOUD] = {
        CONF_CLOUD_ENDPOINT: cloud.get(CONF_CLOUD_ENDPOINT),
        CONF_CLOUD_ACCESS_ID: "**set**" if cloud.get(CONF_CLOUD_ACCESS_ID) else "",
        CONF_CLOUD_ACCESS_SECRET: "**set**"
        if cloud.get(CONF_CLOUD_ACCESS_SECRET)
        else "",
    }
    options[CONF_EXCLUDE] = f"{len(options.get(CONF_EXCLUDE) or [])} entities"
    data = entry.runtime_data
    tuya_loaded = sum(
        1
        for e in hass.config_entries.async_entries(TUYA_DOMAIN)
        if e.state is ConfigEntryState.LOADED
    )
    return {
        "options": options,
        "tuya_entries_loaded": tuya_loaded,
        "watched_sensors": data.keeper.watched_count,
        "stored_values": data.keeper.stored_count,
        "silent_now": len(data.keeper.silent()),
        "came_up_unknown_last_start": len(data.keeper.came_up_unknown),
        "restore": dict(data.keeper.stats),
        "cloud": dict(data.sync.stats) if data.sync is not None else None,
    }
