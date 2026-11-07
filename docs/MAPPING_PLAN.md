# Mapping: build plan and API contract

Goal: run one pattern + effect + speed across several lights (or parts of a light) as one
thing, in sync, either along a chosen order ("string" mode) or by where the pixels physically are
("spatial" mode: sweep across the house, rise up the tree, burst from a point).

xLights shows are untouched: `start_pixel`/`bulbs`, the receiver, and show mode work as today.
A zone is opt-in; per-light effects stay the default.

## Data model (HA `Store`, key `light_show_studio.map`, version 1; spans entries so not options)

```json
{
  "version": 1,
  "runs": [
    {"id": "r1", "entry_id": "<config entry id>", "name": "Toe-kick",
     "first": 0, "count": 279, "reverse": false,
     "pts": [[0.1, 0.9], [0.6, 0.9]]}
  ],
  "zones": [
    {"id": "z1", "name": "Kitchen", "runs": ["r1", "r2"], "mode": "string",
     "axis": {"angle": 0}, "pattern": "Classic Multicolor", "effect": "Chase", "speed": 50}
  ],
  "backdrop": {"kind": "none", "w": 1, "h": 1}
}
```

- Pixel indexes are 0-based within the light. `pts` is a polyline in normalised 0-1 coordinates
  (y down); pixels are spread evenly along it. `pts` may hold one point per pixel (import).
- Spatial axis: `{"angle": deg}` (0 = left to right, 90 = bottom to top) or `{"centre": [x, y]}` (radial).
- Defaults built on load when the store is empty: one run per loaded light covering all its bulbs.
  For a WLED light with exactly 904 bulbs, add the kitchen preset runs (toe-kick 0-278, island
  279-423, counter 424-615, above cabinets 616-903) instead of one run. For a Twinkly light,
  positions come from `ctl.layout` when available.
- On load, drop runs whose entry_id is no longer loaded and zone references to missing runs.

## Modules

- `mapping.py` (pure): dataclasses, `from_dict`/`to_dict` + validation (bounds, overlap within a
  light, unique ids, length limits), `default_map(lights)`, `run_positions(run)`,
  `virtual_order(zone)`, `zone_slices(zone)`.
- `mapped_patterns.py` (pure): `render_zone(model, zone, t) -> list[Colour]` for the virtual
  string; string mode = `patterns.render(pattern, effect, t, N)`; spatial mode computes a 0-1
  `u` per pixel (projection on the axis, or distance from centre), precomputed once per map change,
  and renders Chase/Wave/Marquee/Fade along `u` (index effects like Twinkle/Sparkle as usual).
- `zoneplayer.py`: one asyncio task per active zone (a Steady zone re-sends once a second), one shared clock scaled by speed, 25 fps
  (20 fps if > 1000 pixels); render once, slice per light, `ctl.push_zone_frame(...)`.
- `mapmanager.py`: Store load/save, model, start/stop runners; `hass.data[MAP_KEY]`.
- `controller.py`: `zone` owner field; `push_zone_frame(run_first, colours)` collects run slices
  into a full frame and sends it via `_send_frame` (unmapped pixels black); `release_zone()`;
  any user colour/effect/pattern, or `enable_show()`, releases the zone.
- `light.py`: expose `zone` (zone name or null) in attributes.

## Websocket API (writes admin-only)

| type | payload | returns |
|---|---|---|
| `light_show_studio/map/get` | – | `{map, lights: [{entry_id, title, bulbs, kind, segmented, loaded, layout?}], running: [zone_id]}` |
| `light_show_studio/map/set` | `{map}` (full replace, validated) | `{map}` or error with a readable message |
| `light_show_studio/map/zone/start` | `{zone_id, pattern?, effect?, speed?}` | `{}` |
| `light_show_studio/map/zone/stop` | `{zone_id}` | `{}` |
| `light_show_studio/map/zone/speed` | `{zone_id, speed}` | `{}` (live, no restart) |
| `light_show_studio/map/subscribe` | – | event `{running: [zone_id]}` now and on every change (a zone ends by itself when all its lights are taken) |

Backend notes (as built, 2.2.0):

- `map/set` errors are `invalid_format` with a readable message. `map/zone/*` errors are `failed` / `not_found`.
  Limits: 200 runs, 64 zones, 64 runs per zone, 60-char names, ids `[A-Za-z0-9_.-]{1,64}`, 4096 points per run,
  run `count` 1-2040, runs must fit the light's bulbs and not overlap within a light, zone names are unique
  (case-insensitive), `pts` coordinates are clamped to 0-1. `reverse` must be a real boolean; zone `speed` an int.
  Zone fields other than id and name are optional on write and take defaults (mode `string`, axis `{"angle": 0}`, pattern
  Classic Multicolor, effect Chase, speed 50). `backdrop` is `{kind: str, w, h}`, kind free text up to 32 chars.
- Until the first `map/set`, `map/get` returns the defaults computed live from the current lights (so lights added
  later, and Twinkly layouts that arrive after startup, show up). The first `map/set` makes the store authoritative.
  The frontend should send back the full map it got (with edits). Runs of a removed light disappear from `map/get`.
- `map/zone/start` with `pattern`/`effect`/`speed` keeps them as the zone's own (saved). `map/zone/speed` also saves.
  Starting a running zone restarts it; `map/set` restarts running zones that still exist with the new definition.
- Stopping a zone restores each light (what it showed before; off if the zone turned it on). A light that a person
  takes (colour/effect/pattern/off) or a show starts on is dropped from the zone; when none are left the zone ends
  and `running` changes. Brightness changes keep the zone.
- Several zones may share a light (different runs); their frames are combined. Runs in two zones at once: last push wins.
- Controller API: `async_claim_zone(zone_id, name)`, `push_zone_frame(zone_id, [(first, colours)]) -> bool`
  (False = no longer owned), `release_zone(zone_id=None, restore=False)`, attribute `zone` (names, comma separated).
  This replaces the sketched `push_zone_frame(run_first, colours)`: one push per light per frame carries all its runs.
- Dangling runs are dropped on load only when their config entry no longer exists (a light that is merely not loaded
  yet keeps its runs).

Services: `light_show_studio.start_zone` (zone name or id, pattern?, effect?, speed?) and
`light_show_studio.stop_zone` (zone).

## Panel "Map" tab (frontend/light-show-studio-panel.js), phone-first

- **Zones** list: each zone card shows its runs as an ordered chip list with up/down buttons and a
  reverse toggle, mode (Along the string / By position), axis (angle slider or tap to set the
  centre), and the same pattern -> effect -> speed controls as the Studio Pattern box (reuse its
  helpers), Start / Stop buttons, and a "Running" state.
- **Runs** editor: pick a light, name, first, count, reverse; the WLED kitchen preset button.
- **Canvas** (full width): every run drawn as dots at its positions on a plain grid; drag a run's
  end points to place it; live colours from each light's frames while a zone runs (use the frame
  feed the Studio tab already uses).
- **Import positions**: paste JSON `{"w": 1999, "h": 1500, "points": [[x, y], ...]}` (bulb placer)
  onto a run; Twinkly lights use their own layout automatically.
- Save = `map/set`; show validation errors inline. 44 px targets, no horizontal scroll at 375 px.

## Risks

Cross-device latency (~100 ms skew is acceptable); CPU on a Pi (~1450 px at 25 fps: precompute
`u` and per-index colours, skip resend while Steady); non-segmented lights only show an average
colour, so flag them in the UI; run counts must fit the light's bulbs.
