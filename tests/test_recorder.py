"""First run: the last value comes from the recorder history."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from homeassistant.components.recorder import Recorder
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.tuya_keep.config_flow import DEFAULT_OPTIONS
from custom_components.tuya_keep.const import ATTR_LAST_SEEN, ATTR_RESTORED, DOMAIN

UNIT = {"unit_of_measurement": "°C"}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The recorder must be set up before Home Assistant itself."""
    return


def _register(hass: HomeAssistant, object_id: str) -> str:
    return (
        er.async_get(hass)
        .async_get_or_create("sensor", "tuya", object_id, suggested_object_id=object_id)
        .entity_id
    )


async def _setup(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=DEFAULT_OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_seed_from_recorder(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """Nothing stored yet: the newest real value in the history is used."""
    eid = _register(hass, "living_room_temperature")
    hass.states.async_set(eid, "20.5", UNIT)
    await async_wait_recording_done(hass)
    hass.states.async_set(eid, "21.0", UNIT)
    await async_wait_recording_done(hass)
    hass.states.async_set(eid, STATE_UNKNOWN, UNIT)
    await async_wait_recording_done(hass)

    await _setup(hass)

    state = hass.states.get(eid)
    assert state.state == "21.0"
    assert state.attributes[ATTR_RESTORED] is True


async def test_recorded_restored_value_keeps_its_real_age(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """A value restored before carries its real time; too old is too old."""
    eid = _register(hass, "attic_temperature")
    old = (dt_util.utcnow() - timedelta(hours=30)).isoformat()
    hass.states.async_set(
        eid, "25.0", {**UNIT, ATTR_RESTORED: True, ATTR_LAST_SEEN: old}
    )
    await async_wait_recording_done(hass)
    hass.states.async_set(eid, STATE_UNKNOWN, UNIT)
    await async_wait_recording_done(hass)

    await _setup(hass)

    assert hass.states.get(eid).state == STATE_UNKNOWN
