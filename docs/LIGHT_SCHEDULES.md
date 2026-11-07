# Light schedules (2.5.0)

Lights or areas that come on with a pattern at a set moment and go off later. Separate from the show scheduler
(`schedules/*`, which plays xLights playlists one at a time): any number of light schedules can be active at once.
Code: `light_schedules.py` (engine), `ws_light_schedules.py` (websocket). Store: `light_show_studio.light_schedules`.

## A schedule

```json
{
  "id": "a1b2c3d4",
  "name": "Porch evenings",
  "enabled": true,
  "days": [0, 1, 2, 3, 4, 5, 6],
  "start": {"type": "sunset", "offset_min": -15},
  "end": {"type": "time", "time": "23:00", "offset_min": 0},
  "date_from": "11-25",
  "date_to": "01-06",
  "targets": {"areas": ["outdoor", "Living Room"], "lights": ["<config entry id>"]},
  "look": {"pattern": "Warm White", "effect": "Steady", "speed": 50, "brightness": 80}
}
```

| field | rules |
|---|---|
| `name` | required, 1-60 chars |
| `enabled` | bool, default `true` (a real boolean) |
| `days` | required, 1+ of 0-6 (Mon..Sun): the weekday the window **starts** on. Stored sorted, unique |
| `start` | required edge: `{"type": "time"\|"sunset"\|"sunrise", "time": "HH:MM", "offset_min": -720..720}`. `time` is required for type `time` only; `offset_min` (default 0) is ignored for `time` |
| `end` | optional (default `null`). An edge (same shape; if it is not later than the start it means the next day), or `{"type": "duration", "minutes": 1..1440}`, or `null` = turn on and **leave on** (no off) |
| `date_from`, `date_to` | `"MM-DD"` or null; both or neither. Season by month/day, may wrap the year (`11-25`..`01-06`) |
| `targets.areas` | list of map area ids, area names (any case), Home Assistant area ids, or the specials `"all"`, `"outdoor"`, `"indoor"` (outdoor = areas with `outdoor: true`) |
| `targets.lights` | list of light config entry ids (`map/get` `lights[].entry_id`) |
| | at least one area or light in total; max 64 of each |
| `look.pattern` | required, one of `patterns.PATTERN_LIST` (the same list as zones) |
| `look.effect` | required, one of `patterns.PATTERN_EFFECTS` |
| `look.speed` | 1-100, default 50 |
| `look.brightness` | 1-100 percent, default 100 |

Unknown keys are an error. Max 100 schedules. `id` is assigned by the server.

The server stores edges normalised: a `time` edge always has `offset_min: 0`; a sun edge has no `time`.

## Websocket

All payloads below use `type: "light_show_studio/light_schedules/<command>"`. `save`, `delete`, `toggle`, `run_now`,
`stop` need an admin user; `list` and `subscribe` do not.

### The schedule as the server returns it ("view")

The saved fields above, plus:

| field | meaning |
|---|---|
| `active` | `true` while a window with an end is open (and not stopped early) |
| `next_on` | ISO string (UTC, with offset) of the next start after now, or `null` (disabled / no season date ahead). While active, this is the *following* start |
| `next_off` | while active: when the open window ends; otherwise when the next window ends. `null` when disabled or the schedule has no end ("leave on") |

### `list`
`{type}` -> `{"schedules": [view, ...]}`

### `save`
`{type, schedule_id?, name, days, start, targets, look, enabled?, end?, date_from?, date_to?}` -> `{"schedule": view}`.
Without `schedule_id` a new schedule is made, with it that one is replaced entirely (send all fields). Editing an
open window's settings (anything but `name`) turns off what it had on and starts the window again with the new
settings; a rename changes nothing running.

### `delete`
`{type, schedule_id}` -> `{}`. An active schedule is ended first (what it turned on goes off).

### `toggle`
`{type, schedule_id, enabled?}` (omitted = flip) -> `{"enabled": bool, "schedule": view}`. Disabling an active
schedule ends it; enabling one whose window is still open starts it again.

### `run_now`
`{type, schedule_id}` -> `{"schedule": view}`. Opens a window now: until the schedule's next end time, for its
duration, or, when it has no end, applies the look and leaves the lights on. Works on a disabled schedule (a disabled
schedule has no windows of its own, but this one runs). Replaces a run-now already going.

### `stop`
`{type, schedule_id}` -> `{"stopped": bool, "schedule": view}`. Ends the open window early (what it turned on goes
off). `stopped` is `false` when no window was open. The same window does not start again, even after a restart; the
next day's does.

### `subscribe`
`{type}` -> result `null`, then an event `{"schedules": [view, ...]}` immediately and after every change (save, delete,
toggle, a window opening or closing).

### Errors
| code | when |
|---|---|
| `not_found` | unknown `schedule_id`; the scheduler is not set up |
| `invalid_format` | a field is not valid; the message says which (e.g. `pick at least one area or light`, `set both season dates, or neither`, `expected a value between 1 and 100 @ data['look']['speed']`) |
| `failed` | other refusals: `No light <id>` / `No area <ref>` (target does not exist), `At most 100 light schedules`, run-now could not work out the end |
| `unauthorized` | not an admin (writes) |

## Behaviour the UI should know

- **Opening a window**: each target light gets brightness then pattern/effect/speed (turned on). A light whose runs all
  lie inside the selected areas (together) is driven directly; a light only partly inside is driven by an area zone
  (`start_zone` on that area, whole area incl. its other lights' runs). Lights with no runs follow their Home
  Assistant area (or count as indoor / loose for `all` and `indoor`).
- **Closing**: a light is turned off only if its state (on, effect, pattern, speed, brightness, show/zone state) is
  unchanged since the schedule set it. A person's change keeps it on. Same for area zones (still the zone the schedule
  started). "Leave on" schedules never turn anything off.
- **Shows**: a light that an xLights show is feeding (show mode with frames in the last 2 s) is skipped, never
  interrupted; an area zone is skipped if any light in it is in a show. Skipped lights are retried every minute while
  the window is open.
- **Overlap**: per light/area, the open window that started last wins. When it ends the earlier one's look returns.
  Overlapping schedules on a light that were never applied because of this are not an error.
- **Restart**: ~8 s after Home Assistant starts, open windows are applied once (not again for a window already
  applied, stopped early, or when only the integration reloads). A "leave on" start that was missed within the last
  10 minutes is applied. Area zones do not survive a restart, so an area window is re-applied.
- **Events** (bus): `light_show_studio_light_schedule_started` `{schedule_id, name, start, end}` and
  `light_show_studio_light_schedule_ended` `{schedule_id, name, reason}` where reason is `ended`, `stopped`,
  `disabled`, `deleted`, `edited` or `restarted`.
- **Timer**: one timer at the next change, at most 30 minutes ahead; the wanted state is recomputed from the clock
  each time. Map edits are noticed within 30 minutes (or at the next window change).
- Area `outdoor` flag lives on `map/get` / `map/set` areas: `{id, name, ha_area_id?, outdoor?}` (omitted = false).
  The schedule UI needs a way to set it (area editor in the map panel).
