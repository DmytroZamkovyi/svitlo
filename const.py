"""Constants for the Svitlo integration."""

from __future__ import annotations

DOMAIN = "svitlo"

# Config entry data (address + power template)
CONF_REGION_ID = "region_id"
CONF_REGION_NAME = "region_name"
CONF_DSO_ID = "dso_id"
CONF_DSO_NAME = "dso_name"
CONF_STREET_ID = "street_id"
CONF_STREET_NAME = "street_name"
CONF_HOUSE_ID = "house_id"
CONF_HOUSE_NAME = "house_name"
CONF_GROUP = "group"
CONF_SUBGROUP = "subgroup"

# Options
CONF_POWER_TEMPLATE = "power_template"
CONF_SCHEDULE_INTERVAL = "schedule_interval"  # minutes
CONF_ADDRESS_INTERVAL = "address_interval"  # minutes
CONF_ADDRESS_FAIL_LIMIT = "address_fail_limit"
CONF_DELAY_OFF = "delay_off"  # seconds
CONF_DELAY_ON = "delay_on"  # seconds
CONF_MATCH_MIN_OVERLAP = "match_min_overlap"  # minutes
CONF_RETENTION_DAYS = "retention_days"

DEFAULT_SCHEDULE_INTERVAL = 15
DEFAULT_ADDRESS_INTERVAL = 60
DEFAULT_ADDRESS_FAIL_LIMIT = 3
DEFAULT_DELAY_OFF = 30
DEFAULT_DELAY_ON = 30
DEFAULT_MATCH_MIN_OVERLAP = 10
DEFAULT_RETENTION_DAYS = 365
DEFAULT_POWER_TEMPLATE = "{{ is_state('binary_sensor.example_power', 'on') }}"

# Consecutive failed schedule updates before a "stale data" repair appears
STALE_FAIL_LIMIT = 6

# Statistics periods
PERIOD_TODAY = "today"
PERIOD_YESTERDAY = "yesterday"
PERIOD_WEEK = "week"
PERIOD_MONTH = "month"
PERIOD_ALL = "all"
PERIODS = (PERIOD_TODAY, PERIOD_YESTERDAY, PERIOD_WEEK, PERIOD_MONTH, PERIOD_ALL)

# Event entity types
EVENT_GROUP_CHANGED = "group_changed"
EVENT_SCHEDULE_CHANGED = "schedule_changed"
EVENT_POWER_LOST = "power_lost"
EVENT_POWER_RESTORED = "power_restored"
EVENT_TYPES = [
    EVENT_GROUP_CHANGED,
    EVENT_SCHEDULE_CHANGED,
    EVENT_POWER_LOST,
    EVENT_POWER_RESTORED,
]

# Repair issues
ISSUE_ADDRESS_NOT_FOUND = "address_not_found"
ISSUE_STALE_DATA = "stale_data"

# Battery sub-entries (one per battery)
SUBENTRY_BATTERY = "battery"
CONF_BAT_CAPACITY = "capacity"  # kWh, usable
CONF_BAT_SOC = "soc"
CONF_BAT_ENERGY_IN = "energy_in"  # energy going into the battery (charge)
CONF_BAT_ENERGY_OUT = "energy_out"  # energy coming out of the battery (discharge)
CONF_BAT_POWER_MODE = "power_mode"
CONF_BAT_POWER = "power"
CONF_BAT_POWER_IN = "power_in"
CONF_BAT_POWER_OUT = "power_out"
CONF_BAT_RESERVE = "reserve"  # %
CONF_BAT_TARGET = "target"  # %
CONF_BAT_MAX_POWER = "max_power"  # W

POWER_MODE_NONE = "none"
POWER_MODE_STANDARD = "standard"  # one sensor, positive = discharging
POWER_MODE_INVERTED = "inverted"  # one sensor, positive = charging
POWER_MODE_TWO = "two"  # separate charge and discharge sensors
POWER_MODES = [POWER_MODE_NONE, POWER_MODE_STANDARD, POWER_MODE_INVERTED, POWER_MODE_TWO]

DEFAULT_BAT_RESERVE = 10
DEFAULT_BAT_TARGET = 100
