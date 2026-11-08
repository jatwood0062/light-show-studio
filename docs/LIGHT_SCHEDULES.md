# Light schedules (2.5.0, fades and more in 2.6.0, rotation / holidays / random / presence in 2.6.1)

Lights or areas that come on with a pattern at a set moment and go off later. Separate from the show scheduler
(`schedules/*`, which plays xLights playlists one at a time): any number of light schedules can be active at once.
Code: `light_schedules.py` (engine), `ws_light_schedules.py` (websocket), `schedule_math.py` (windows, random offset, rotation),
`holiday_calendar.py` (the holiday table and date maths). Store: `light_show_studio.light_schedules`.

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
  "look": {"pattern": "Warm White", "effect": "Steady", "speed": 50, "brightness": 80},
  "fade_in_s": 120,
  "fade_out_s": 120,
  "looks": [], "rotate": null, "holiday_looks": false, "random_offset_min": 0, "only_when": "any"
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
| `fade_in_s` | optional number of seconds, 0-1800, default **2** (a real number, not a string or boolean): the lights fade up from black over this long when the window opens |
| `fade_out_s` | optional, 0-1800, default **2**: they fade down over this long when the window closes (or the schedule is stopped / disabled / deleted) and are then switched off |
| `look.pattern` | required: a built-in pattern or **one of your own** (name or id; stored by name, see `MUSIC_PAINT_FX.md`). Deleting / renaming one of your patterns updates schedules that use it |
| `look.effect` | required: a string effect (`Steady`, `Twinkle`, `Chase` ...) or a **map effect** (`Sweep`, `Ripple`, `Snowfall`, `Rise`, `Rainbow Wash`, `Fireworks`, `Breathe Outward`, `Gradient Axis`, `Plasma Field`, `Candy Helix`). Map effects need an area target: an area zone draws them in 3D; a target **light** shows the nearest string effect instead (Sweep/Rise/Rainbow Wash -> Chase, Ripple -> Wave, Snowfall -> Twinkle, Fireworks -> Sparkle, Breathe Outward -> Fade, Gradient Axis -> Gradient Flow, Plasma Field -> Plasma, Candy Helix -> Chase) |
| `look.speed` | 1-100, default 50 |
| `look.brightness` | 1-100 percent, default 100 |

New in 2.6.1 (all optional; schedules saved earlier load with these defaults and behave exactly as before):

| field | rules |
|---|---|
| `looks` | list of 1-12 looks (same shape as `look`). `look` is then always `looks[0]` (the server sets it; if you send both, `look` must equal `looks[0]`). Send `look` alone for a single look, as before. Default `[]` |
| `rotate` | `null`, `{"type": "night"}` (a different look each night) or `{"type": "interval", "minutes": 1..1440}`. Needs 2+ looks; with 2+ looks and no `rotate` the server stores `{"type": "night"}` |
| `holiday_looks` | bool, default `false`. In a window that **starts** on a day inside an enabled holiday's date window, the holiday's look replaces the schedule's look (and rotation). Season dates (`date_from`/`date_to`) still apply first |
| `random_offset_min` | whole number 0-60, default 0. The start and the end each move by a deterministic per-day amount in -N..+N minutes, so the house looks lived in. A `duration` end moves with the start |
| `only_when` | `"any"` (default), `"home"` (at least one person is home) or `"away"` (nobody home). Based on the state of `zone.home` (its person count) |

Unknown keys are an error. Max 100 schedules. `id` is assigned by the server.

The server stores edges normalised: a `time` edge always has `offset_min: 0`; a sun edge has no `time`.

## Websocket

All payloads below use `type: "light_show_studio/light_schedules/<command>"`. `save`, `delete`, `toggle`, `run_now`,
`stop` need an admin user; `list` and `subscribe` do not.

### The schedule as the server returns it ("view")
(New view fields in 2.6.1 are listed in the table below.)

The saved fields above, plus:

| field | meaning |
|---|---|
| `active` | `true` while a window with an end is open (and not stopped early) |
| `next_on` | ISO string (UTC, with offset) of the next start after now, or `null` (disabled / no season date ahead). While active, this is the *following* start |
| `look_now` | the look in force right now for the open window (after rotation / holiday), else `null` |
| `holiday_now` | id of the holiday whose look replaced the schedule's, else `null` |
| `skipped_now` | `true` when a window that is open now did not start because of `only_when` |
| `next_off` | while active: when the open window ends; otherwise when the next window ends. `null` when disabled or the schedule has no end ("leave on") |

### `list`
`{type}` -> `{"schedules": [view, ...]}`

### `save`
`{type, schedule_id?, name, days, start, targets, look | looks, enabled?, end?, date_from?, date_to?, fade_in_s?, fade_out_s?, rotate?, holiday_looks?, random_offset_min?, only_when?}` -> `{"schedule": view}`.
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

### `holidays`
`{type, days?}` (`days` 1-366, default 120; not admin-only) -> 
```json
{"today": "2026-12-07", "active": "hanukkah", "table_years": [2026, 2030],
 "holidays": [{"id": "christmas", "name": "Christmas", "rule": "fixed|easter|nth|table", "note": "", "enabled": true,
               "look": {"pattern": "Red & Green", "effect": "Twinkle", "speed": 50, "brightness": 100},
               "before": 24, "after": 1, "default_look": {}, "default_before": 24, "default_after": 1, "overridden": false,
               "date": "2026-12-25", "window_start": "2026-12-01", "window_end": "2026-12-26", "active": true}],
 "upcoming": [{"id": "christmas", "name": "Christmas", "date": "2026-12-25", "window_start": "2026-12-01",
               "window_end": "2026-12-26", "enabled": true, "look": {}}]}
```
`holidays` has every built-in holiday with its next occurrence (`date` etc. are `null` for table holidays - Hanukkah, Diwali,
Lunar New Year - once past 2030). `upcoming` lists occurrences whose window has not ended and starts within `days` days,
soonest window first (disabled ones included, `enabled: false`). Dates are local `YYYY-MM-DD`.

### `holiday_set` (admin)
`{type, holiday_id, look?, before?, after?, enabled?}` -> `{"holiday": <one entry of holidays[]>}`. Each key given overrides
that setting; a key set to `null` clears the override (back to the built-in value). `look` is a full look
(`pattern`, `effect`, optional `speed`, `brightness`; patterns may be your own), `before`/`after` are whole days 0-60,
`enabled` a boolean. Unknown keys are an error. Stored in the schedule Store (`holidays`). Schedules with `holiday_looks`
follow immediately (an open window swaps its look). Unknown `holiday_id` -> `not_found`; bad values -> `invalid_format`.

### `holiday_reset` (admin)
`{type, holiday_id}` -> `{"holiday": ...}`. Removes every override of that holiday.

### Errors
| code | when |
|---|---|
| `not_found` | unknown `schedule_id`; the scheduler is not set up |
| `invalid_format` | a field is not valid; the message says which (e.g. `pick at least one area or light`, `set both season dates, or neither`, `expected a value between 1 and 100 @ data['look']['speed']`) |
| `failed` | other refusals: `No light <id>` / `No area <ref>` (target does not exist), `At most 100 light schedules`, run-now could not work out the end |
| `unauthorized` | not an admin (writes) |

## Behaviour the UI should know

- **Look rotation**: the look is worked out from the clock each time, so it is the same after a restart. `night`: the look
  index is `(day number + a per-schedule offset) mod count` for the day the window starts on, so neighbouring nights always
  differ and it cycles in list order. `interval`: starts at that night's index and steps to the next look every N minutes
  from the window start (a cross-fade; a light a person changed is left alone). Windows with no end apply the night's look once.
- **Holidays**: built in (look uses existing patterns only): New Year (Dec 31-Jan 1), Lunar New Year, Valentine's Day,
  St Patrick's Day (Rainbow, there is no green pattern), Easter, Mother's Day, Memorial Day, Father's Day, Independence Day,
  Labor Day, Halloween (14 days before), Diwali, Thanksgiving, Hanukkah (first candle + 7 days), Christmas (Dec 1-26).
  Fixed dates; Easter by the computus; Thanksgiving = 4th Thursday of November; Mother's / Father's Day = 2nd Sunday of May /
  3rd Sunday of June; Memorial = last Monday of May; Labor = first Monday of September; Hanukkah / Diwali / Lunar New Year come
  from a table for 2026-2030 (no date, so no window, outside those years). When windows overlap the one that opened last wins
  (Hanukkah over Christmas). The holiday is chosen by the day the schedule's window starts on.
- **Random offset**: the shift is a hash of the schedule id, the day and start / end, so it is stable across calls and restarts.
  `next_on` / `next_off` show the shifted times. Run-now windows are not shifted.
- **Presence** (`only_when`): checked once, when a window opens (also for "turn on and leave" starts). If it fails the window
  is skipped for the day (`skipped_now`) and is not re-checked, even if someone arrives later. A window that is already open is
  never closed because people leave. If `zone.home` is missing or not a number the schedule runs. Run now ignores it.

- **Fades**: a light that is off fades up from black over `fade_in_s` (a brightness ramp - a 120 s sunset fade is a slow ramp,
  a couple of frames a second, not a frame blend); a light that is already on cross-fades into the new look. At the end the
  light fades down over `fade_out_s`, then is switched off; it still reports `on` while it fades, and anyone turning it on meanwhile
  brings it back up. Area zones fade in and out the same way (`map/zone/start` `transition`). A light a person changed is
  still left alone at the end (no fade, no off). Windows with no end ("turn on and leave on") use `fade_in_s` only. Schedules
  saved before 2.6.0 load with the 2 s defaults. With `fade_in_s: 0` / `fade_out_s: 0` everything is instant as before.
- **Opening a window**: each target light gets brightness and pattern/effect/speed in one step (turned on). A light whose runs all
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
