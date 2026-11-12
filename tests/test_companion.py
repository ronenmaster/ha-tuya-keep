"""Companion sensors: the last value next to each Tuya sensor (default)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.tuya_keep.config_flow import DEFAULT_OPTIONS
from custom_components.tuya_keep.const import ATTR_RESTORED, DOMAIN

UNIT = {
    "unit_of_measurement": "°C",
    "device_class": "temperature",
    "state_class": "measurement",
}


def _source(hass: HomeAssistant, object_id: str) -> str:
    return (
        er.async_get(hass)
        .async_get_or_create(
            "sensor",
            "tuya",
            f"uid_{object_id}",
            suggested_object_id=object_id,
            unit_of_measurement="°C",
            capabilities={"state_class": "measurement"},
            original_device_class="temperature",
            original_name="Temperature",
        )
        .entity_id
    )


async def _settle(hass: HomeAssistant) -> None:
    for _ in range(3):
        await hass.async_block_till_done()


async def _setup(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, options={**DEFAULT_OPTIONS, **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await _settle(hass)
    return entry


async def test_companion_keeps_the_last_value_tuya_untouched(
    hass: HomeAssistant,
) -> None:
    """The Tuya sensor stays unknown; its companion shows the last value."""
    src = _source(hass, "kitchen_t")
    hass.states.async_set(src, "21.5", UNIT)
    await _setup(hass)
    comp = f"{src}_last"

    state = hass.states.get(comp)
    assert state.state == "21.5"
    assert state.attributes["live"] is True
    assert state.attributes["source"] == src
    assert state.attributes["unit_of_measurement"] == "°C"
    assert state.attributes["friendly_name"] == "Temperature last value"

    hass.states.async_set(src, STATE_UNKNOWN, UNIT)  # a restart or reload
    await _settle(hass)
    assert hass.states.get(src).state == STATE_UNKNOWN  # never written to
    assert ATTR_RESTORED not in hass.states.get(src).attributes
    state = hass.states.get(comp)
    assert state.state == "21.5"
    assert state.attributes["live"] is False
    assert state.attributes["icon"] == "mdi:history"

    hass.states.async_set(src, "22.0", UNIT)
    await _settle(hass)
    state = hass.states.get(comp)
    assert state.state == "22.0"
    assert state.attributes["live"] is True


async def test_companion_restores_after_a_restart(hass: HomeAssistant) -> None:
    """Right after a restart it has its value (RestoreSensor)."""
    src = _source(hass, "hall_t")
    seen = (dt_util.utcnow() - timedelta(minutes=20)).isoformat()
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(f"{src}_last", "19.5", {"last_seen": seen}),
                {"native_value": 19.5, "native_unit_of_measurement": "°C"},
            )
        ],
    )
    hass.states.async_set(src, STATE_UNKNOWN, UNIT)
    await _setup(hass)
    state = hass.states.get(f"{src}_last")
    assert state.state == "19.5"
    assert state.attributes["live"] is False
    assert state.attributes["last_seen"] == seen


async def test_too_old_turns_unknown(hass: HomeAssistant) -> None:
    """Past the age limit the kept value is not shown."""
    src = _source(hass, "attic_t")
    seen = (dt_util.utcnow() - timedelta(hours=30)).isoformat()
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(f"{src}_last", "30.0", {"last_seen": seen}),
                {"native_value": 30.0, "native_unit_of_measurement": "°C"},
            )
        ],
    )
    hass.states.async_set(src, STATE_UNKNOWN, UNIT)
    await _setup(hass, max_age_hours=24)
    assert hass.states.get(f"{src}_last").state == STATE_UNKNOWN


async def test_new_and_removed_sources(hass: HomeAssistant) -> None:
    """A Tuya sensor added later gets a companion; a removed one loses it."""
    await _setup(hass)
    src = _source(hass, "new_t")
    hass.states.async_set(src, "18.0", UNIT)
    await _settle(hass)
    assert hass.states.get(f"{src}_last").state == "18.0"

    er.async_get(hass).async_remove(src)
    await _settle(hass)
    assert er.async_get(hass).async_get(f"{src}_last") is None


async def test_text_sensors_get_no_companion(hass: HomeAssistant) -> None:
    """Only number sensors, unless text values are kept too."""
    reg = er.async_get(hass)
    text = reg.async_get_or_create(
        "sensor", "tuya", "uid_bat", suggested_object_id="battery_state"
    ).entity_id
    hass.states.async_set(text, "high")
    await _setup(hass)
    assert hass.states.get(f"{text}_last") is None


async def test_entry_without_mode_keeps_writing_back(hass: HomeAssistant) -> None:
    """An entry made before the choice existed: as before, no companions."""
    src = _source(hass, "old_t")
    hass.states.async_set(src, "20.0", UNIT)
    options = {k: v for k, v in DEFAULT_OPTIONS.items() if k != "mode"}
    entry = MockConfigEntry(domain=DOMAIN, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await _settle(hass)
    assert hass.states.get(f"{src}_last") is None
    hass.states.async_set(src, STATE_UNKNOWN, UNIT)
    await _settle(hass)
    assert hass.states.get(src).state == "20.0"  # written back in place

    # and its settings form shows "in place" for it
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    mode_key = next(k for k in schema if str(k) == "mode")
    assert mode_key.description["suggested_value"] == "in_place"


async def test_age_check_runs_by_itself(hass: HomeAssistant, freezer: Any) -> None:
    """The age limit is looked at every few minutes."""
    src = _source(hass, "slow_t")
    hass.states.async_set(src, "21.0", UNIT)
    await _setup(hass, max_age_hours=1)
    hass.states.async_set(src, STATE_UNKNOWN, UNIT)
    await _settle(hass)
    assert hass.states.get(f"{src}_last").state == "21.0"
    freezer.tick(timedelta(hours=2))
    async_fire_time_changed(hass)
    await _settle(hass)
    assert hass.states.get(f"{src}_last").state == STATE_UNKNOWN


def _tuya_device(hass: HomeAssistant) -> tuple[MockConfigEntry, dr.DeviceEntry, er.RegistryEntry]:
    """A Tuya sensor on a Tuya device, both of the official integration."""
    tuya = MockConfigEntry(domain="tuya")
    tuya.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=tuya.entry_id,
        identifiers={("tuya", "abc123")},
        name="Living room sensor",
    )
    src = er.async_get(hass).async_get_or_create(
        "sensor",
        "tuya",
        "uid_lr",
        suggested_object_id="lr_t",
        unit_of_measurement="°C",
        capabilities={"state_class": "measurement"},
        original_device_class="temperature",
        original_name="Temperature",
        device_id=dev.id,
        config_entry=tuya,
    )
    return tuya, dev, src


async def test_companion_on_the_tuya_device(hass: HomeAssistant) -> None:
    """Linked to the Tuya sensor's own device and named after it; no device of its own."""
    _tuya, dev, src = _tuya_device(hass)
    hass.states.async_set(src.entity_id, "22.0", UNIT)
    entry = await _setup(hass)

    comp = er.async_get(hass).async_get(f"{src.entity_id}_last")
    assert comp.device_id == dev.id
    state = hass.states.get(comp.entity_id)
    assert state.attributes["friendly_name"] == "Living room sensor Temperature last value"
    own = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert own  # its service device
    assert all((DOMAIN, entry.entry_id) in d.identifiers for d in own)


async def test_copies_made_by_1_4_0_are_removed(hass: HomeAssistant) -> None:
    """1.4.0 made a copy of each Tuya device, owned by Tuya Keep: it goes at start."""
    _tuya, dev, src = _tuya_device(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    entry = MockConfigEntry(domain=DOMAIN, options={**DEFAULT_OPTIONS})
    entry.add_to_hass(hass)
    # (what 1.4.0 left: a device of Tuya Keep's with the Tuya device's identifiers, the
    # companion on it)
    copy = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("tuya", "abc123")}
    )
    assert copy.id != dev.id  # identifiers are unique per config entry
    ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"kept_{src.id}",
        suggested_object_id="lr_t_last",
        device_id=copy.id,
        config_entry=entry,
    )
    hass.states.async_set(src.entity_id, "22.0", UNIT)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await _settle(hass)

    assert dev_reg.async_get(copy.id) is None
    assert dev_reg.async_get(dev.id) is not None  # the Tuya device itself stays
    comp = ent_reg.async_get(f"{src.entity_id}_last")
    assert comp.device_id == dev.id
    assert hass.states.get(comp.entity_id).state == "22.0"
