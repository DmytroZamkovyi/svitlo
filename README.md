# Svitlo

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=DmytroZamkovyi&repository=svitlo&category=integration)
[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=svitlo)

[Українською](README.uk.md)

Home Assistant integration for Ukrainian power outages: the Yasno outage schedule for your address, the real power state from your own template, detailed statistics (actual vs schedule) and three read-only calendars.

Everything is configured in the UI. No YAML, no custom cards.

## Install (HACS custom repository)
1. Click the first button above, or in HACS → three dots → Custom repositories → add this repository, category **Integration**.
2. Download **Svitlo**, restart Home Assistant.
3. Click the second button above, or Settings → Devices & services → Add integration → **Svitlo**.

## Setup
1. Region → operator (DSO) → street search → house search. The group is detected automatically.
2. Power template: must return `true` when power is present and `false` when it is not, e.g. `{{ is_state('binary_sensor.example_power', 'on') }}`.

Options (Configure): power template, confirmation delays for power loss/return, schedule update interval, group/street ID refresh interval, failed lookups before a repair issue, minimum overlap for matching, history retention.
Reconfigure: change the address.

Street/house IDs and the group change in Yasno over time, so they are re-resolved by the saved names periodically.

## Entities
- **Yasno** device: scheduled status, next outage, next power on, group, schedule updated, probable outages, scheduled outage (binary), tomorrow's schedule published (binary), emergency shutdowns (binary: Yasno announced emergency shutdowns for today; the day's schedule is kept, `today` and `tomorrow` in attributes), today/tomorrow planned time with and without power, planned outage count and longest planned outage, scheduled outage time left today, changes (event: group changed, schedule changed, power lost, power restored), calendars "Current schedule" (today and tomorrow only) and "Schedule history" (final slots per day).
- **Power** device: power (binary), last state change, current state duration, calendar "Actual outages".
- **Statistics** devices (last 24 hours, previous 24 hours, 7 days, 30 days, all time; all periods are rolling windows ending now, not calendar days): actual and scheduled outage share, count, durations, planned time that happened, actual time that was planned, unplanned outages, planned outages that did not happen, power-off/on deviation from the schedule (negative = earlier; median/min/max in attributes), average power-off/on time, time with power, scheduled time with and without power, scheduled outage count, actual minus scheduled outage time, schedule changes, average publish time of tomorrow's schedule.

Calendar events have stable `uid`s, so another tool can mirror them to an external calendar.

## Batteries

Open the integration and use **Add battery** (as many as you like). Pick sensors the same way as in the Energy dashboard: the energy going into the battery (charge) and coming out of it (discharge), optionally a power sensor (one sensor, positive = discharging or charging, or two separate ones) and a charge level sensor (%). Add the usable capacity (**kWh**), the reserve and target charge to get time estimates. At least one sensor is required; each battery becomes its own device and can be changed or removed at any time.

Each battery gets two devices:
- **Now** (the latest readings): state (charging, discharging, idle), charge power, load (also as a share of the maximum power if you set it), time to empty and time to full at the current load (with the estimated clock time in an attribute), plus charge level, stored energy and usable energy above the reserve.
- **5-minute average**: the same state, powers, load share and time estimates, but averaged over the last 5 minutes, so they do not jump with short load spikes.
- Without a power sensor the power is derived from the energy counters over the last 30 minutes (counters with a coarse step need the longer window), and failing that from the charge level trend (a rough estimate). Both devices then show the same smoothed value. A power sensor of the battery itself gives the most accurate values and the real difference between the two devices.

## Limitations
- Uses the public, undocumented Yasno API; it may change without notice.
- If Home Assistant is down during an outage, the outage start is the moment HA came back.

## License
MIT
