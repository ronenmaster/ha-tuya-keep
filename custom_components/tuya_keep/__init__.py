"""Tuya Keep: Tuya sensors keep their last value after a restart.

A temporary companion of the official Tuya integration until the "unknown
after restart" problem is fixed there (home-assistant/core#178105). It does
not replace or change the official integration's code; its unknown value
guard (guard.py) sits in front of the Tuya SDK's report handler at run time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CLOUD_ENDPOINTS,
    CLOUD_NONE,
    CONF_CLOUD,
    CONF_CLOUD_ACCESS_ID,
    CONF_CLOUD_ACCESS_SECRET,
    CONF_CLOUD_ENDPOINT,
    CONF_GUARD,
    DEFAULT_GUARD,
    GUARD_CHECK_EVERY,
)
from .guard import UnknownValueGuard
from .keeper import Keeper
from .shadow import ShadowSync, TuyaOpenApi

PLATFORMS = [Platform.SENSOR]


@dataclass
class TuyaKeepData:
    """What a loaded entry keeps."""

    keeper: Keeper
    sync: ShadowSync | None
    guard: UnknownValueGuard | None = None


type TuyaKeepConfigEntry = ConfigEntry[TuyaKeepData]


def cloud_settings(options: dict[str, Any]) -> tuple[str, str, str] | None:
    """Return (endpoint url, access id, secret) when the cloud layer is set."""
    cloud = options.get(CONF_CLOUD) or {}
    endpoint = CLOUD_ENDPOINTS.get(cloud.get(CONF_CLOUD_ENDPOINT) or CLOUD_NONE)
    access_id = (cloud.get(CONF_CLOUD_ACCESS_ID) or "").strip()
    secret = (cloud.get(CONF_CLOUD_ACCESS_SECRET) or "").strip()
    if endpoint and access_id and secret:
        return endpoint, access_id, secret
    return None


async def async_setup_entry(hass: HomeAssistant, entry: TuyaKeepConfigEntry) -> bool:
    """Start keeping values (and the optional cloud layer)."""
    options = dict(entry.options)
    keeper = Keeper(hass, options)
    sync: ShadowSync | None = None
    if (cloud := cloud_settings(options)) is not None:
        api = TuyaOpenApi(async_get_clientsession(hass), *cloud)
        sync = ShadowSync(hass, api)
        sync.on_update = keeper.notify
        keeper.unknown_hooks.append(sync.trigger)
    guard: UnknownValueGuard | None = None
    if options.get(CONF_GUARD, DEFAULT_GUARD):
        guard = UnknownValueGuard(hass, keeper.notify)
        # (a reload of Tuya makes a new manager: wrapped at once - its sensors
        # turn unknown - and checked every half minute as well)
        keeper.unknown_hooks.append(guard.check)
        entry.async_on_unload(
            async_track_time_interval(
                hass, guard.check, timedelta(seconds=GUARD_CHECK_EVERY)
            )
        )
        entry.async_on_unload(guard.stop)
    entry.runtime_data = TuyaKeepData(keeper, sync, guard)
    if guard is not None:
        guard.check()
    await keeper.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if sync is not None:
        sync.trigger()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: TuyaKeepConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TuyaKeepConfigEntry) -> bool:
    """Stop listening; restored values stay until the devices report."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    data = entry.runtime_data
    if data.sync is not None:
        data.sync.stop()
    await data.keeper.async_stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: TuyaKeepConfigEntry) -> None:
    """Delete the stored values when the integration is removed."""
    await Keeper(hass, {}).async_remove_store()
