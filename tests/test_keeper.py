"""Layer 1: keeping and restoring last values."""

from __future__ import annotations

import time
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from custom_components.tuya_keep.config_flow import DEFAULT_OPTIONS
from custom_components.tuya_keep.const import (
    ATTR_LAST_SEEN,
    ATTR_RESTORED,
    CONF_EXCLUDE,
    CONF_MAX_AGE_HOURS,
    CONF_NON_NUMERIC,
    DOMAIN,
    STORE_KEY,
    STORE_VERSION,
)

UNIT = {"unit_of_measurement": "°C", "device_class": "temperature"}


def _stored(hass_storage: dict[str, Any], values: dict[str, tuple[str, float]]) -> None:
    hass_storage[STORE_KEY] = {
        "version": STORE_VERSION,
        "minor_version": 1,
        "key": STORE_KEY,
        "data": {"values": {k: {"s": s, "t": t} for k, (s, t) in values.items()}},
    }


def _register(hass: HomeAssistant, object_id: str, platform: str = "tuya") -> str:
    entry = er.async_get(hass).async_get_or_create(
        "sensor", platform, f"uid_{object_id}", suggested_object_id=object_id
    )
    return entry.entity_id


async def _setup(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, options={**DEFAULT_OPTIONS, **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_restores_last_value_from_store(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A sensor that comes up unknown gets its stored value back."""
    eid = _register(hass, "kitchen_temperature")
    seen = time.time() - 600
    _stored(hass_storage, {eid: ("21.5", seen)})
    await _setup(hass)

    hass.states.async_set(eid, STATE_UNKNOWN, UNIT)
    await hass.async_block_till_done()

    state = hass.states.get(eid)
    assert state.state == "21.5"
    assert state.attributes[ATTR_RESTORED] is True
    assert state.attributes["unit_of_measurement"] == "°C"
    assert ATTR_LAST_SEEN in state.attributes


async def test_real_value_replaces_restored_and_is_remembered(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The next real report wins, and becomes the new kept value."""
    eid = _register(hass, "kitchen_temperature")
    _stored(hass_storage, {eid: ("21.5", time.time() - 600)})
    entry = await _setup(hass)

    hass.states.async_set(eid, STATE_UNKNOWN, UNIT)
    await hass.async_block_till_done()
    hass.states.async_set(eid, "22.75", UNIT)
    await hass.async_block_till_done()

    state = hass.states.get(eid)
    assert state.state == "22.75"
    assert ATTR_RESTORED not in state.attributes

    # Unknown again (an online/offline event, a reload): the new value comes back.
    hass.states.async_set(eid, STATE_UNKNOWN, UNIT)
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "22.75"

    # Saved on unload.
    assert await hass.config_entries.async_unload(entry.entry_id)
    saved = hass_storage[STORE_KEY]["data"]["values"][eid]
    assert saved["s"] == "22.75"


async def test_value_too_old_is_not_restored(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Older than the limit: stays unknown."""
    eid = _register(hass, "attic_temperature")
    _stored(hass_storage, {eid: ("30.0", time.time() - 30 * 3600)})
    await _setup(hass, **{CONF_MAX_AGE_HOURS: 24})

    hass.states.async_set(eid, STATE_UNKNOWN, UNIT)
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == STATE_UNKNOWN


async def test_no_age_limit(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    """0 hours means no limit."""
    eid = _register(hass, "attic_temperature")
    _stored(hass_storage, {eid: ("30.0", time.time() - 20 * 24 * 3600)})
    await _setup(hass, **{CONF_MAX_AGE_HOURS: 0})

    hass.states.async_set(eid, STATE_UNKNOWN, UNIT)
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "30.0"


async def test_unavailable_is_left_alone(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """An offline device stays unavailable (that is the truth)."""
    eid = _register(hass, "garden_temperature")
    _stored(hass_storage, {eid: ("18.0", time.time() - 60)})
    await _setup(hass)

    hass.states.async_set(eid, STATE_UNAVAILABLE, UNIT)
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == STATE_UNAVAILABLE


async def test_other_integrations_and_excluded_are_ignored(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Only the watched integrations, minus the excluded sensors."""
    other = _register(hass, "mqtt_temperature", platform="mqtt")
    excluded = _register(hass, "excluded_temperature")
    _stored(
        hass_storage,
        {other: ("10.0", time.time() - 60), excluded: ("11.0", time.time() - 60)},
    )
    await _setup(hass, **{CONF_EXCLUDE: [excluded]})

    hass.states.async_set(other, STATE_UNKNOWN, UNIT)
    hass.states.async_set(excluded, STATE_UNKNOWN, UNIT)
    await hass.async_block_till_done()
    assert hass.states.get(other).state == STATE_UNKNOWN
    assert hass.states.get(excluded).state == STATE_UNKNOWN


@pytest.mark.parametrize(
    ("include", "expected"), [(False, STATE_UNKNOWN), (True, "low")]
)
async def test_text_values(hass: HomeAssistant, include: bool, expected: str) -> None:
    """Text states are kept only when asked for."""
    eid = _register(hass, "sensor_battery_state")
    await _setup(hass, **{CONF_NON_NUMERIC: include})

    hass.states.async_set(eid, "low")
    await hass.async_block_till_done()
    hass.states.async_set(eid, STATE_UNKNOWN)
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == expected


async def test_sensor_already_unknown_at_setup(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Set up after Tuya: sensors that are unknown already are restored."""
    eid = _register(hass, "bedroom_humidity")
    _stored(hass_storage, {eid: ("55", time.time() - 60)})
    hass.states.async_set(eid, STATE_UNKNOWN, {"unit_of_measurement": "%"})
    await _setup(hass)
    assert hass.states.get(eid).state == "55"


async def test_other_listeners_see_unknown_before_restored(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """No listener may get the restored value before the unknown one."""
    eid = _register(hass, "kitchen_temperature")
    _stored(hass_storage, {eid: ("21.5", time.time() - 60)})
    await _setup(hass)

    seen: list[str] = []

    @callback
    def _listen(event) -> None:
        if event.data["entity_id"] == eid and event.data["new_state"]:
            seen.append(event.data["new_state"].state)

    hass.bus.async_listen(EVENT_STATE_CHANGED, _listen)
    hass.states.async_set(eid, STATE_UNKNOWN, UNIT)
    await hass.async_block_till_done()
    assert seen == [STATE_UNKNOWN, "21.5"]


async def test_rename_and_remove_follow_the_registry(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A renamed sensor keeps its value; a removed one is forgotten."""
    registry = er.async_get(hass)
    eid = _register(hass, "old_name")
    _stored(hass_storage, {eid: ("19.0", time.time() - 60)})
    entry = await _setup(hass)

    registry.async_update_entity(eid, new_entity_id="sensor.new_name")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.new_name", STATE_UNKNOWN, UNIT)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.new_name").state == "19.0"

    registry.async_remove("sensor.new_name")
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert "sensor.new_name" not in hass_storage[STORE_KEY]["data"]["values"]


async def test_remove_entry_deletes_store(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Removing the integration leaves nothing behind."""
    eid = _register(hass, "kitchen_temperature")
    entry = await _setup(hass)
    hass.states.async_set(eid, "20.0", UNIT)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert STORE_KEY not in hass_storage
