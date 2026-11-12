"""Constants for Tuya Keep."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "tuya_keep"
LOGGER = logging.getLogger(__package__)

# Options
CONF_MAX_AGE_HOURS: Final = "max_age_hours"
CONF_PLATFORMS: Final = "platforms"
CONF_NON_NUMERIC: Final = "include_non_numeric"
CONF_EXCLUDE: Final = "exclude_entities"
CONF_CLOUD: Final = "cloud"
CONF_CLOUD_ENDPOINT: Final = "endpoint"
CONF_CLOUD_ACCESS_ID: Final = "access_id"
CONF_CLOUD_ACCESS_SECRET: Final = "access_secret"

DEFAULT_MAX_AGE_HOURS: Final = 24
DEFAULT_PLATFORMS: Final = ["tuya"]
DEFAULT_NON_NUMERIC: Final = False

# Attributes added to a state written by this integration.
ATTR_RESTORED: Final = "tuya_keep_restored"
ATTR_LAST_SEEN: Final = "tuya_keep_last_seen"

# Last known values, kept in .storage/tuya_keep.last_values
STORE_KEY: Final = f"{DOMAIN}.last_values"
STORE_VERSION: Final = 1
STORE_SAVE_DELAY: Final = 60  # seconds
STORE_DROP_AFTER: Final = 30 * 24 * 3600  # forget values not seen for 30 days

# How far back the recorder is searched when a value is not in the store yet.
RECORDER_SEED_DAYS: Final = 10
RECORDER_SEED_LIMIT: Final = 25

# Tuya cloud (OpenAPI) data centers. The key is stored in the options.
CLOUD_NONE: Final = "none"
CLOUD_ENDPOINTS: Final = {
    "eu": "https://openapi.tuyaeu.com",
    "eu_west": "https://openapi-weaz.tuyaeu.com",
    "us": "https://openapi.tuyaus.com",
    "us_east": "https://openapi-ueaz.tuyaus.com",
    "cn": "https://openapi.tuyacn.com",
    "in": "https://openapi.tuyain.com",
    "sg": "https://openapi-sg.iotbing.com",
}

# The official integration's names (homeassistant/components/tuya).
TUYA_DOMAIN: Final = "tuya"

# Shadow sync: wait this long after the last "unknown" before reading the
# cloud, so one integration reload becomes one batch of calls.
SHADOW_DEBOUNCE: Final = 3.0
SHADOW_RETRIES: Final = 3
SHADOW_RETRY_DELAY: Final = 60.0
