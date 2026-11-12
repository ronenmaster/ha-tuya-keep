"""Companion sensors: the last value next to each Tuya sensor (the default).

Following the Home Assistant Tuya maintainers' advice (core#178105): the Tuya
entities themselves are never written to. For every watched Tuya sensor,
Tuya Keep adds its own "<name> last value" sensor on the same device:

- while the Tuya sensor reports, the companion shows the same value, live;
- when the Tuya sensor turns unknown (a restart, a reload), the companion keeps
  showing the last real value, marked as not live (attribute ``live: false``,
  ``last_seen``, a history icon), and after the age limit it turns unknown;
- it restores itself across restarts the way every Home Assistant sensor can
  (RestoreSensor), so it has a value right after a restart.

Dashboards and automations point at the companion when they want the last
known value, and at the Tuya sensor when they want only live readings.
"""

from __future__ import annotations

from collections.abc import Mapping
import time
from typing import Any

from homeassistant.components.sensor import RestoreSensor
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import ATTR_RESTORED, DOMAIN, LOGGER
from .keeper import Keeper, is_number

_NOT_REAL = {STATE_UNKNOWN, STATE_UNAVAILABLE, ""}
CHECK_EVERY = 300  # seconds: the age limit is looked at this often


def _entity_part(hass: HomeAssistant, entry: er.RegistryEntry) -> str:
    """The sensor's own name, without its device's name."""
    if entry.name or entry.original_name:
        return str(entry.name or entry.original_name)
    state = hass.states.get(entry.entity_id)
    friendly = str(state.attributes.get("friendly_name", "")) if state else ""
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    device_name = (device.name_by_user or device.name) if device else None
    if device_name and friendly.startswith(device_name + " "):
        return friendly[len(device_name) + 1 :]
    return friendly or entry.entity_id.split(".", 1)[1]


def is_numeric_source(entry: er.RegistryEntry) -> bool:
    """A number sensor (a unit or a state class), as far as the registry says."""
    caps = entry.capabilities or {}
    return bool(entry.unit_of_measurement or caps.get("state_class"))


class KeptValueSensor(RestoreSensor):
    """The last real value of one Tuya sensor."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "kept_value"
    _unrecorded_attributes = frozenset({"source", "last_seen"})

    def __init__(
        self,
        keeper: Keeper,
        source: er.RegistryEntry,
        name_part: str,
        device: DeviceInfo | None,
    ) -> None:
        """One companion for one source sensor."""
        self._keeper = keeper
        self._source = source.entity_id
        self._attr_unique_id = f"kept_{source.id}"
        self._attr_translation_placeholders = {"name": name_part}
        self._attr_device_info = device
        self._attr_native_unit_of_measurement = source.unit_of_measurement
        self._attr_device_class = source.original_device_class  # type: ignore[assignment]
        self._attr_state_class = (source.capabilities or {}).get("state_class")
        self.entity_id = f"sensor.{source.entity_id.split('.', 1)[1]}_last"
        self._live = False
        self._seen: float | None = None
        self._unsubs: list = []

    @property
    def source(self) -> str:
        """The Tuya sensor this one follows."""
        return self._source

    async def async_added_to_hass(self) -> None:
        """Restore, then follow the source sensor."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        last_state = await self.async_get_last_state()
        if last is not None and last.native_value is not None:
            self._attr_native_value = last.native_value
            if last.native_unit_of_measurement:
                self._attr_native_unit_of_measurement = last.native_unit_of_measurement
        if last_state is not None:
            seen = dt_util.parse_datetime(
                str(last_state.attributes.get("last_seen") or "")
            )
            self._seen = seen.timestamp() if seen else None
        self._follow()
        self._take(self.hass.states.get(self._source))
        self._seed()
        self._check_age()

    async def async_will_remove_from_hass(self) -> None:
        """Stop following."""
        self._unfollow()

    def _follow(self) -> None:
        self._unfollow()
        self._unsubs = [
            async_track_state_change_event(self.hass, [self._source], self._changed),
            async_track_state_report_event(self.hass, [self._source], self._changed),
        ]

    def _unfollow(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def renamed(self, new_source: str) -> None:
        """The source sensor got a new entity id."""
        self._source = new_source
        self._follow()
        self._take(self.hass.states.get(new_source))

    @callback
    def _changed(self, event: Event[Mapping[str, Any]]) -> None:
        self._take(event.data.get("new_state"))

    @callback
    def _take(self, state: State | None) -> None:
        """Copy a real value, or mark the kept one as not live."""
        real = (
            state is not None
            and state.state not in _NOT_REAL
            and not state.attributes.get(ATTR_RESTORED)
            and (is_number(state.state) or self._attr_state_class is None)
        )
        if real:
            assert state is not None
            self._attr_native_value = (
                float(state.state) if is_number(state.state) else state.state
            )
            unit = state.attributes.get("unit_of_measurement")
            if unit:
                self._attr_native_unit_of_measurement = unit
            self._seen = getattr(state, "last_reported_timestamp", None) or time.time()
            self._live = True
        else:
            self._live = False
        if self.hass is not None and self.entity_id:
            self.async_write_ha_state()

    @callback
    def _seed(self) -> None:
        """No value yet (first install): the keeper's stored or recorded one."""
        if self._attr_native_value is not None:
            return
        rec = self._keeper.value_of(self._source)
        if rec is None:
            return
        value = rec["s"]
        self._attr_native_value = float(value) if is_number(value) else value
        self._seen = float(rec["t"])
        self.async_write_ha_state()

    @callback
    def check(self) -> None:
        """From the keeper (a value seeded) and every few minutes (the age)."""
        self._seed()
        self._check_age()

    @callback
    def _check_age(self) -> None:
        limit = self._keeper.max_age
        if (
            not self._live
            and limit is not None
            and self._seen is not None
            and time.time() - self._seen > limit
            and self._attr_native_value is not None
        ):
            self._attr_native_value = None  # too old to show
            self.async_write_ha_state()

    @property
    def icon(self) -> str | None:
        """A history icon while the value is a kept one."""
        return None if self._live else "mdi:history"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Whether it is live, and when the value was really seen."""
        return {
            "live": self._live,
            "last_seen": dt_util.utc_from_timestamp(self._seen).isoformat()
            if self._seen
            else None,
            "source": self._source,
        }


class Companions:
    """Adds, renames and removes the companion sensors as Tuya's change."""

    def __init__(
        self, hass: HomeAssistant, keeper: Keeper, add: AddConfigEntryEntitiesCallback
    ) -> None:
        """Nothing is made until start()."""
        self.hass = hass
        self._keeper = keeper
        self._add = add
        self._by_source: dict[str, KeptValueSensor] = {}
        self._unsubs: list = []

    @callback
    def start(self) -> None:
        """A companion for each watched number sensor; follow the registry."""
        registry = er.async_get(self.hass)
        new = [
            self._make(e) for e in list(registry.entities.values()) if self._wanted(e)
        ]
        self._add([c for c in new if c is not None])
        self._unsubs.append(
            self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry)
        )
        self._unsubs.append(self._keeper.add_listener(self._keeper_changed))
        from datetime import timedelta  # noqa: PLC0415

        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._tick, timedelta(seconds=CHECK_EVERY)
            )
        )

    @callback
    def stop(self) -> None:
        """Stop following."""
        while self._unsubs:
            self._unsubs.pop()()

    def _wanted(self, entry: er.RegistryEntry) -> bool:
        return (
            entry.domain == "sensor"
            and entry.disabled_by is None
            and entry.entity_id not in self._by_source
            and self._keeper.is_watched(entry.entity_id)
            and (is_numeric_source(entry) or self._keeper.non_numeric)
        )

    def _make(self, entry: er.RegistryEntry) -> KeptValueSensor | None:
        device = None
        if entry.device_id:
            dev = dr.async_get(self.hass).async_get(entry.device_id)
            if dev is not None and dev.identifiers:
                device = DeviceInfo(identifiers=set(dev.identifiers))
        sensor = KeptValueSensor(
            self._keeper, entry, _entity_part(self.hass, entry), device
        )
        self._by_source[entry.entity_id] = sensor
        return sensor

    @callback
    def _registry(self, event: Event[Mapping[str, Any]]) -> None:
        data = event.data
        action = data.get("action")
        entity_id = data.get("entity_id")
        if not entity_id or str(entity_id).startswith(f"sensor.{DOMAIN}_"):
            return
        registry = er.async_get(self.hass)
        if action == "create":
            entry = registry.async_get(entity_id)
            if entry is not None and entry.platform != DOMAIN and self._wanted(entry):
                sensor = self._make(entry)
                if sensor is not None:
                    self._add([sensor])
        elif action == "remove":
            sensor = self._by_source.pop(entity_id, None)
            if sensor is not None and sensor.entity_id:
                LOGGER.debug("Removing the companion of a removed sensor")
                registry.async_remove(sensor.entity_id)
        elif action == "update" and data.get("old_entity_id") in self._by_source:
            sensor = self._by_source.pop(data["old_entity_id"])
            self._by_source[entity_id] = sensor
            sensor.renamed(entity_id)

    @callback
    def _keeper_changed(self) -> None:
        for sensor in list(self._by_source.values()):
            if sensor.hass is not None:
                sensor.check()

    @callback
    def _tick(self, _now: Any) -> None:
        self._keeper_changed()
