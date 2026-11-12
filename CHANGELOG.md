# Changelog

## 1.0.1

- Own icon (custom_components/tuya_keep/brand), shown by Home Assistant 2026 and later.

## 1.0.0

First release.

- Layer 1: Tuya sensors that turn "unknown" get their last known value back at once, marked with `tuya_keep_restored` and `tuya_keep_last_seen`. Works for all current and future devices of the official Tuya integration. Seeds from the recorder history on the first start.
- Layer 2 (optional): with your own Tuya IoT cloud project keys, the real last values are read from the device shadow and handed to the official integration's own update path.
- Options: age limit, text values, watched integrations, excluded sensors, cloud keys (checked before saving).
- Diagnostics with counts only (no keys, no entity names).
- English and Hebrew translations.
