"""Layer 1: keep the last known value of Tuya sensors.

Every real value a watched sensor reports is remembered (with the time it was
last seen). When the sensor's state turns "unknown" (after a restart of Home
Assistant, a reload of the Tuya integration, or an online/offline event of a
device whose cloud status came back empty) the last known value is written
back into the state machine, marked with two attributes. The next real report
of the device replaces it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import timedelta
from functools import partial
import math
import time
from typing import Any

from homeassistant.const import (
    EVENT_STATE_CHANGED,
    EVENT_STATE_REPORTED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_LAST_SEEN,
    ATTR_RESTORED,
    CONF_EXCLUDE,
    CONF_MAX_AGE_HOURS,
    CONF_NON_NUMERIC,
    CONF_PLATFORMS,
    CONF_SILENT_HOURS,
    DEFAULT_MAX_AGE_HOURS,
    DEFAULT_NON_NUMERIC,
    DEFAULT_PLATFORMS,
    DEFAULT_SILENT_HOURS,
    DOMAIN,
    LOGGER,
    RECORDER_SEED_DAYS,
    RECORDER_SEED_LIMIT,
    SILENT_CHECK_EVERY,
    START_WINDOW,
    STORE_DROP_AFTER,
    STORE_KEY,
    STORE_SAVE_DELAY,
    STORE_VERSION,
)

_NOT_REAL = {STATE_UNKNOWN, STATE_UNAVAILABLE, ""}


def is_number(value: Any) -> bool:
    """Return True for a finite number (as a state string)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


class Keeper:
    """Remember the last value of watched sensors and put it back."""

    def __init__(self, hass: HomeAssistant, options: Mapping[str, Any]) -> None:
        """Set up from the entry options."""
        self.hass = hass
        hours = float(options.get(CONF_MAX_AGE_HOURS, DEFAULT_MAX_AGE_HOURS) or 0)
        self._max_age: float | None = hours * 3600 if hours > 0 else None
        self._platforms = set(options.get(CONF_PLATFORMS) or DEFAULT_PLATFORMS)
        self._non_numeric = bool(options.get(CONF_NON_NUMERIC, DEFAULT_NON_NUMERIC))
        self._exclude = set(options.get(CONF_EXCLUDE) or [])
        silent = float(options.get(CONF_SILENT_HOURS, DEFAULT_SILENT_HOURS) or 0)
        self.silent_hours = silent
        self._silent_after: float | None = silent * 3600 if silent > 0 else None
        self._silent_last: frozenset[str] = frozenset()
        # the sensors that came up "unknown" at the last start -> when
        self._came_up: dict[str, float] = {}
        self._came_up_at = 0.0
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, STORE_KEY)
        # entity_id -> {"s": state, "t": last seen (unix seconds)}
        self._values: dict[str, dict[str, Any]] = {}
        self._watched: dict[str, bool] = {}
        self._pending: set[str] = set()
        self._unsubs: list[CALLBACK_TYPE] = []
        # entity_id -> when its kept value was really seen (unix seconds),
        # for the sensors that show a kept value right now
        self._kept: dict[str, float] = {}
        self._listeners: list[Callable[[], None]] = []
        self._notify_pending = False
        self.on_unknown: Callable[[str], None] | None = None
        self.stats: dict[str, Any] = {
            "restored": 0,
            "skipped_too_old": 0,
            "skipped_no_value": 0,
            "seeded_from_recorder": 0,
            "last_restore": None,
        }

    # ------------------------------------------------------------------ setup

    async def async_start(self) -> None:
        """Load the stored values and start listening."""
        data = await self._store.async_load() or {}
        now = time.time()
        for entity_id, rec in (data.get("values") or {}).items():
            if (
                isinstance(rec, dict)
                and isinstance(rec.get("s"), str)
                and isinstance(rec.get("t"), (int, float))
                and now - rec["t"] < STORE_DROP_AFTER
            ):
                self._values[entity_id] = {"s": rec["s"], "t": float(rec["t"])}

        bus = self.hass.bus
        self._unsubs.append(
            bus.async_listen(
                EVENT_STATE_CHANGED, self._on_changed, event_filter=self._filter
            )
        )
        self._unsubs.append(
            bus.async_listen(
                EVENT_STATE_REPORTED, self._on_reported, event_filter=self._filter
            )
        )
        self._unsubs.append(
            bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._on_registry)
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._tick, timedelta(seconds=SILENT_CHECK_EVERY)
            )
        )

        # Entities that are already there (this entry was set up after Tuya,
        # or the options changed and the entry was reloaded).
        for state in self.hass.states.async_all("sensor"):
            if not self._is_watched(state.entity_id):
                continue
            if state.state == STATE_UNKNOWN:
                self._came_up_unknown(state.entity_id)
                self._unknown(state.entity_id)
            else:
                self._remember(state)
        self._silent_last = frozenset(self.silent())

    async def async_stop(self) -> None:
        """Stop listening and save what we have."""
        while self._unsubs:
            self._unsubs.pop()()
        await self._store.async_save(self._data())

    async def async_remove_store(self) -> None:
        """Delete the stored values (the entry was removed)."""
        await self._store.async_remove()

    @property
    def watched_count(self) -> int:
        """Return how many sensors are watched now."""
        return len(self.watched_entities())

    @property
    def stored_count(self) -> int:
        """Return how many last values are kept."""
        return len(self._values)

    @property
    def kept(self) -> dict[str, float]:
        """Sensors showing a kept value now -> when it was really seen."""
        return dict(self._kept)

    @property
    def came_up_unknown(self) -> dict[str, float]:
        """Sensors that came up "unknown" at the last start -> when."""
        return dict(self._came_up)

    @property
    def came_up_at(self) -> float:
        """When the last start (with sensors coming up unknown) was."""
        return self._came_up_at

    @callback
    def silent(self) -> dict[str, float]:
        """Watched sensors with no real value for longer than the limit.

        A battery device that stopped reporting (empty battery, out of range)
        usually still looks fine in Home Assistant; this finds it.
        """
        if self._silent_after is None:
            return {}
        limit = time.time() - self._silent_after
        return {
            eid: rec["t"]
            for eid, rec in self._values.items()
            if rec["t"] < limit and self._is_watched(eid)
        }

    @callback
    def watched_entities(self) -> list[str]:
        """Every sensor in the registry that is watched (live list)."""
        registry = er.async_get(self.hass)
        return sorted(
            entry.entity_id
            for entry in registry.entities.values()
            if entry.domain == "sensor" and self._is_watched(entry.entity_id)
        )

    @callback
    def add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call listener when what is kept or watched changes."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    @callback
    def notify(self) -> None:
        """Tell the listeners (the integration's own sensors), once per turn.

        Many changes at once (a start: dozens of sensors) become one update.
        """
        if self._notify_pending:
            return
        self._notify_pending = True
        self.hass.loop.call_soon(self._notify_now)

    @callback
    def _notify_now(self) -> None:
        self._notify_pending = False
        for listener in list(self._listeners):
            listener()

    # --------------------------------------------------------------- watching

    @callback
    def _is_watched(self, entity_id: str) -> bool:
        if not entity_id.startswith("sensor."):
            return False
        known = self._watched.get(entity_id)
        if known is not None:
            return known
        entry = er.async_get(self.hass).async_get(entity_id)
        ok = (
            entry is not None
            and entry.platform in self._platforms
            and entry.platform != DOMAIN  # never our own sensors
            and entity_id not in self._exclude
        )
        self._watched[entity_id] = ok
        return ok

    @callback
    def _filter(self, event_data: Mapping[str, Any]) -> bool:
        return self._is_watched(event_data["entity_id"])

    @callback
    def _on_registry(self, event: Event) -> None:
        data = event.data
        entity_id = data.get("entity_id")
        old_id = data.get("old_entity_id")
        for eid in (entity_id, old_id):
            if eid:
                self._watched.pop(eid, None)
        if data.get("action") == "remove" and entity_id:
            if self._values.pop(entity_id, None) is not None:
                self._save_later()
        elif old_id and entity_id and old_id in self._values:
            self._values[entity_id] = self._values.pop(old_id)
            self._save_later()
        if old_id:
            self._kept.pop(old_id, None)
        if data.get("action") == "remove" and entity_id:
            self._kept.pop(entity_id, None)
        self.notify()

    @callback
    def _came_up_unknown(self, entity_id: str) -> None:
        now = time.time()
        if now - self._came_up_at > START_WINDOW:
            self._came_up.clear()  # a new start
        self._came_up_at = now
        self._came_up[entity_id] = now
        self.notify()

    @callback
    def _tick(self, _now: Any) -> None:
        silent = frozenset(self.silent())
        if silent != self._silent_last:
            self._silent_last = silent
            self.notify()

    @callback
    def _on_changed(self, event: Event) -> None:
        new: State | None = event.data.get("new_state")
        if new is not None and new.attributes.get(ATTR_RESTORED):
            return  # our own write
        old: State | None = event.data.get("old_state")
        if (
            new is not None
            and new.state == STATE_UNKNOWN
            and (old is None or old.state == STATE_UNAVAILABLE)
        ):
            # Added (start of Home Assistant or a reload of Tuya) as unknown.
            self._came_up_unknown(new.entity_id)
        # Anything else (a real value, unknown, unavailable, removed) means
        # the sensor no longer shows its kept value.
        if self._kept.pop(event.data["entity_id"], None) is not None:
            self.notify()
        if new is None:
            return
        if new.state == STATE_UNKNOWN:
            self._unknown(new.entity_id)
            return
        self._remember(new)

    @callback
    def _on_reported(self, event: Event) -> None:
        new: State | None = event.data.get("new_state")
        if new is None or new.attributes.get(ATTR_RESTORED):
            return
        self._remember(new)

    @callback
    def _remember(self, state: State) -> None:
        if state.state in _NOT_REAL or state.attributes.get(ATTR_RESTORED):
            return
        if not self._non_numeric and not is_number(state.state):
            return
        seen = getattr(state, "last_reported_timestamp", None) or time.time()
        self._values[state.entity_id] = {"s": state.state, "t": float(seen)}
        self._save_later()
        if state.entity_id in self._silent_last:
            self._silent_last = frozenset(self.silent())
            self.notify()

    @callback
    def _save_later(self) -> None:
        self._store.async_delay_save(self._data, STORE_SAVE_DELAY)

    @callback
    def _data(self) -> dict[str, Any]:
        return {"values": self._values}

    # -------------------------------------------------------------- restoring

    @callback
    def _unknown(self, entity_id: str) -> None:
        if self.on_unknown is not None:
            self.on_unknown(entity_id)
        if entity_id in self._pending:
            return
        rec = self._values.get(entity_id)
        if rec is not None:
            # The usual case: put the value back right away. call_soon lets
            # the "unknown" event reach every other listener first, so no
            # listener gets the restored value before the "unknown" one.
            self.hass.loop.call_soon(self._apply, entity_id, rec)
            return
        self._pending.add(entity_id)
        self.hass.async_create_background_task(
            self._seed_and_apply(entity_id), f"{STORE_KEY} seed {entity_id}"
        )

    async def _seed_and_apply(self, entity_id: str) -> None:
        try:
            rec = await self._from_recorder(entity_id)
            if rec is None:
                self.stats["skipped_no_value"] += 1
                return
            self.stats["seeded_from_recorder"] += 1
            if entity_id not in self._values:
                self._values[entity_id] = rec
                self._save_later()
            self._apply(entity_id, self._values[entity_id])
        except Exception:  # noqa: BLE001 - never break the caller
            LOGGER.exception("Could not read the last value of %s", entity_id)
        finally:
            self._pending.discard(entity_id)

    @callback
    def _apply(self, entity_id: str, rec: Mapping[str, Any]) -> None:
        current = self.hass.states.get(entity_id)
        if current is None or current.state != STATE_UNKNOWN:
            return  # a real value (or "unavailable") came meanwhile
        if self._max_age is not None and time.time() - rec["t"] > self._max_age:
            self.stats["skipped_too_old"] += 1
            LOGGER.debug("Not restoring %s: the last value is too old", entity_id)
            return
        attrs = dict(current.attributes)
        attrs[ATTR_RESTORED] = True
        attrs[ATTR_LAST_SEEN] = dt_util.utc_from_timestamp(rec["t"]).isoformat()
        self.hass.states.async_set(entity_id, rec["s"], attrs)
        self._kept[entity_id] = float(rec["t"])
        self.stats["restored"] += 1
        self.stats["last_restore"] = dt_util.utcnow().isoformat()
        self.notify()
        LOGGER.debug("Restored %s to its last value", entity_id)

    async def _from_recorder(self, entity_id: str) -> dict[str, Any] | None:
        """Find the newest real value of an entity in the recorder."""
        if "recorder" not in self.hass.config.components:
            return None
        # Imported here: the recorder is optional.
        from homeassistant.components.recorder import get_instance, history  # noqa: PLC0415

        instance = get_instance(self.hass)
        if not await instance.async_db_ready:
            return None
        days = RECORDER_SEED_DAYS
        if self._max_age is not None:
            days = min(days, self._max_age / 86400)
        start = dt_util.utcnow() - timedelta(days=days)
        found = await instance.async_add_executor_job(
            partial(
                history.state_changes_during_period,
                self.hass,
                start,
                None,
                entity_id,
                False,  # no_attributes
                True,  # descending
                RECORDER_SEED_LIMIT,
                True,  # include_start_time_state
            )
        )
        best: dict[str, Any] | None = None
        for state in found.get(entity_id, []):
            value = state.state
            if value in _NOT_REAL:
                continue
            if not self._non_numeric and not is_number(value):
                continue
            seen = state.last_updated.timestamp()
            if state.attributes.get(ATTR_RESTORED):
                # A value we restored before: its real time is in the attribute.
                parsed = dt_util.parse_datetime(
                    str(state.attributes.get(ATTR_LAST_SEEN) or "")
                )
                if parsed is None:
                    continue
                seen = parsed.timestamp()
            if best is None or seen > best["t"]:
                best = {"s": value, "t": seen}
        return best
