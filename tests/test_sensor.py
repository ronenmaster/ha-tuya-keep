"""The integration's own sensors: what it handles, live."""

from __future__ import annotations

import time
from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.tuya_keep.config_flow import DEFAULT_OPTIONS
from custom_components.tuya_keep.const import DOMAIN, STORE_KEY, STORE_VERSION

UNIT = {"unit_of_measurement": "°C", "friendly_name": "Kitchen temperature"}


async def _setup(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, options={**DEFAULT_OPTIONS, **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_sensors_follow_what_is_kept(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Counts and lists change as values are kept and replaced."""
    eid = (
        er.async_get(hass)
        .async_get_or_create("sensor", "tuya", "k1", suggested_object_id="kitchen_t")
        .entity_id
    )
    hass_storage[STORE_KEY] = {
        "version": STORE_VERSION,
        "minor_version": 1,
        "key": STORE_KEY,
        "data": {"values": {eid: {"s": "21.5", "t": time.time() - 600}}},
    }
    hass.states.async_set(eid, "21.0", UNIT)
    entry = await _setup(hass)

    assert hass.states.get("sensor.tuya_keep_kept_now").state == "0"
    watched = hass.states.get("sensor.tuya_keep_watched")
    assert watched.state == "1"
    assert watched.attributes["entity_ids"] == [eid]
    assert watched.attributes["sensors"] == ["Kitchen temperature"]

    hass.states.async_set(eid, STATE_UNKNOWN, UNIT)
    await hass.async_block_till_done()
    kept = hass.states.get("sensor.tuya_keep_kept_now")
    assert kept.state == "1"
    assert kept.attributes["sensors"] == ["Kitchen temperature"]
    assert eid in kept.attributes["last_seen"]
    assert hass.states.get("sensor.tuya_keep_restored").state == "1"
    assert hass.states.get("sensor.tuya_keep_last_restore").state != STATE_UNKNOWN

    hass.states.async_set(eid, "22.0", UNIT)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.tuya_keep_kept_now").state == "0"

    # One service device holds them; no cloud sensors without keys.
    device = dr.async_get(hass).async_get_device({(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.entry_type is dr.DeviceEntryType.SERVICE
    assert hass.states.get("sensor.tuya_keep_cloud_status") is None


async def test_own_sensors_are_never_watched(hass: HomeAssistant) -> None:
    """Even when its own integration is listed, it does not watch itself."""
    await _setup(hass, platforms=["tuya", DOMAIN])
    watched = hass.states.get("sensor.tuya_keep_watched")
    assert watched.state == "0"
    assert not any(
        e.startswith("sensor.tuya_keep_") for e in watched.attributes["entity_ids"]
    )
