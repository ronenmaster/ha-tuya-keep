"""Tuya Keep: Tuya sensors keep their last value after a restart.

A temporary companion of the official Tuya integration until the "unknown
after restart" problem is fixed there (home-assistant/core#178105). It does
not replace or change the official integration's code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CLOUD_ENDPOINTS,
    CLOUD_NONE,
    CONF_CLOUD,
    CONF_CLOUD_ACCESS_ID,
    CONF_CLOUD_ACCESS_SECRET,
    CONF_CLOUD_ENDPOINT,
)
from .keeper import Keeper
from .shadow import ShadowSync, TuyaOpenApi


@dataclass
class TuyaKeepData:
    """What a loaded entry keeps."""

    keeper: Keeper
    sync: ShadowSync | None


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
        keeper.on_unknown = sync.trigger
    entry.runtime_data = TuyaKeepData(keeper, sync)
    await keeper.async_start()
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
    data = entry.runtime_data
    if data.sync is not None:
        data.sync.stop()
    await data.keeper.async_stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: TuyaKeepConfigEntry) -> None:
    """Delete the stored values when the integration is removed."""
    await Keeper(hass, {}).async_remove_store()
