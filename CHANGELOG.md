# Changelog

## 1.3.0

- Fix for false smoke alarms: a Tuya report value that is neither in the data point's own mapping nor in its range is ignored (the Tuya SDK's enum conversion turned it into the first value of the range, "alarm" for smoke detectors). Real values pass untouched. New option *Ignore unknown values (false alarms)*, on by default; new sensor *Unknown values ignored* with the last ones.

## 1.2.0

- New sensor *Silent sensors*: watched sensors with no new value for longer than *Silent after* (new option, default 6 h), usually an empty battery or a device out of range.
- New sensor *Came up unknown at the last start*: which sensors came up "Unknown" at the last start; when it stays 0 the official fix has probably landed.
- Sensor updates are batched (one update for many changes at once).

## 1.1.0

- Live sensors on the integration's page (a *Tuya Keep* service device): sensors showing a kept value now (with their names and when each value was really seen), watched sensors, values restored since start, last restore; with the cloud layer also values filled from the cloud and the cloud status.

## 1.0.2

- Cloud layer: only number readings (status type Integer) are filled from the device shadow. Alarm and on/off states (smoke, water leak, door, motion) are never filled, so an old "alarm" kept in the cloud cannot become a false alarm after a restart.

## 1.0.1

- Own icon (custom_components/tuya_keep/brand), shown by Home Assistant 2026 and later.

## 1.0.0

First release.

- Layer 1: Tuya sensors that turn "unknown" get their last known value back at once, marked with `tuya_keep_restored` and `tuya_keep_last_seen`. Works for all current and future devices of the official Tuya integration. Seeds from the recorder history on the first start.
- Layer 2 (optional): with your own Tuya IoT cloud project keys, the real last values are read from the device shadow and handed to the official integration's own update path.
- Options: age limit, text values, watched integrations, excluded sensors, cloud keys (checked before saving).
- Diagnostics with counts only (no keys, no entity names).
- English and Hebrew translations.
