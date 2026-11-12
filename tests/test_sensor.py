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


async def _settle(hass: HomeAssistant) -> None:
    """Let the batched sensor updates (call_soon) run too."""
    for _ in range(3):
        await hass.async_block_till_done()


async def _setup(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, options={**DEFAULT_OPTIONS, "mode": "in_place", **options}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await _settle(hass)
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
    await _settle(hass)
    kept = hass.states.get("sensor.tuya_keep_kept_now")
    assert kept.state == "1"
    assert kept.attributes["sensors"] == ["Kitchen temperature"]
    assert eid in kept.attributes["last_seen"]
    assert hass.states.get("sensor.tuya_keep_restored").state == "1"
    assert hass.states.get("sensor.tuya_keep_last_restore").state != STATE_UNKNOWN

    hass.states.async_set(eid, "22.0", UNIT)
    await _settle(hass)
    assert hass.states.get("sensor.tuya_keep_kept_now").state == "0"

    # One service device holds them; no cloud sensors without keys.
    reg = dr.async_get(hass)
    # (Home Assistant 2026.10 and later: identifiers are unique per config entry)
    by_id = getattr(reg, "async_get_device_by_identifier", None)
    device = (
        by_id((DOMAIN, entry.entry_id), entry.entry_id)
        if by_id
        else reg.async_get_device({(DOMAIN, entry.entry_id)})
    )
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


async def test_silent_sensors(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A sensor with no new value for longer than the limit is listed."""
    reg = er.async_get(hass)
    quiet = reg.async_get_or_create(
        "sensor", "tuya", "q1", suggested_object_id="attic_t"
    ).entity_id
    fresh = reg.async_get_or_create(
        "sensor", "tuya", "f1", suggested_object_id="hall_t"
    ).entity_id
    hass_storage[STORE_KEY] = {
        "version": STORE_VERSION,
        "minor_version": 1,
        "key": STORE_KEY,
        "data": {
            "values": {
                quiet: {"s": "19.0", "t": time.time() - 8 * 3600},
                fresh: {"s": "22.0", "t": time.time() - 600},
            }
        },
    }
    await _setup(hass, silent_hours=6)
    silent = hass.states.get("sensor.tuya_keep_silent")
    assert silent.state == "1"
    assert silent.attributes["entity_ids"] == [quiet]
    assert silent.attributes["silent_after_hours"] == 6

    # It reports again: no longer silent.
    hass.states.async_set(quiet, "19.5", UNIT)
    await _settle(hass)
    assert hass.states.get("sensor.tuya_keep_silent").state == "0"


async def test_came_up_unknown_at_start(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Sensors added as unknown (a start or a reload) are counted."""
    reg = er.async_get(hass)
    a = reg.async_get_or_create(
        "sensor", "tuya", "a1", suggested_object_id="a_t"
    ).entity_id
    b = reg.async_get_or_create(
        "sensor", "tuya", "b1", suggested_object_id="b_t"
    ).entity_id
    await _setup(hass)
    assert hass.states.get("sensor.tuya_keep_came_up_unknown").state == "0"

    hass.states.async_set(a, STATE_UNKNOWN, UNIT)  # added as unknown
    hass.states.async_set(b, "20.0", UNIT)  # added with a value
    await _settle(hass)
    came = hass.states.get("sensor.tuya_keep_came_up_unknown")
    assert came.state == "1"
    assert came.attributes["entity_ids"] == [a]
    assert came.attributes["start"] is not None
