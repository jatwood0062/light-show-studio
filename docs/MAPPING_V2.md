# Mapping v2: areas and 3D camera mapping

What the owner asked for: "map strings to different areas, and a way to 3D map it like Twinkly".
Builds on v1 (docs/MAPPING_PLAN.md: runs, zones, map store, zone runner). Keep v1 data working
(migrate the store to version 2 on load; never lose a saved map).

## 1. Areas

- Every run belongs to an **area**: a Home Assistant area id (Porch, Kitchen, Living Room...) or a
  custom area name for places HA doesn't have (e.g. "Garage roofline"). Default: the light's HA
  device area, else its HA config entry title.
- Each area has its own 3D space (positions are per area; areas don't share coordinates).
- A zone can be defined by **areas** (all runs in those areas, ordered by the area's run order) or by
  an explicit run list as today. "Whole house" = every area, in a chosen area order.
- Studio and the panel can pick an area as the target: "Kitchen: Classic Multicolor, Twinkle".
- Service `light_show_studio.start_zone` accepts `area` too.

## 2. 3D positions

- A run's positions become per-pixel `[x, y, z]` (0-1, y up, z towards the viewer) once mapped;
  until then they come from the run's polyline (`pts`, now 3D with z default 0.5).
- Store per-pixel positions compactly (a flat list of ints 0-1000 per axis is fine; ~1404 pixels).
- Twinkly: import its own layout (already fetched as `ctl.layout`), including z when it has one.
- Spatial zone modes use 3D: a **direction** (any 3D vector, with presets: left->right, bottom->top
  (rise), front->back, and "outward from a point"), applied within each area's own space.

## 3. Camera mapping ("Scan", like Twinkly)

Flow in the panel (phone-first): pick a light (or a run) -> "Scan from the front" -> point the phone
at it -> the lights flash a code sequence while the phone camera records -> positions appear on the
3D view -> optionally "Scan from the side" (about 90 degrees around) to get depth -> review, fix
outliers by dragging -> Save.

How it works:
- Backend: websocket `light_show_studio/map/scan/start {entry_id}` takes the light (like a zone,
  released afterwards, restoring what it showed), `map/scan/frame {entry_id, frame}` shows one code
  frame, `map/scan/stop {entry_id}`. Code frames (computed in a pure module `scan_codes.py`, tested):
  frame 0 = all off (dark reference), frame 1 = all on dim white (where any LED is), then for each bit
  b of the pixel index (ceil(log2(N)) bits) two frames: pixels with bit b set lit, then the inverse
  (robust thresholding). Lit = full white at a fixed moderate level. 904 LEDs -> 2 + 2*10 = 22 frames.
- Frontend drives timing: shows frame k, waits ~350 ms (Govee/WLED latency + camera exposure),
  grabs a camera frame, repeats. Live camera via getUserMedia when the page is a secure context
  (https or localhost); otherwise fall back to "record a video" with `<input type=file accept=video/*
  capture=environment>` and a frame schedule overlay (the lights run the sequence with a sync blink
  pattern at the start so the video can be aligned), then decode the video with a `<video>` element
  and canvas seeking.
- Decoding (pure JS, unit-testable function in the panel file or a separate module file):
  difference images vs the dark frame, blob detection over the "all on" frame (bright local maxima,
  min distance), then per blob read each bit by comparing the bit frame with its inverse. Index =
  decoded bits; reject impossible/duplicate indexes; interpolate missing pixels along the string
  order; normalise to 0-1 within the area.
- Two scans: front gives (x, y); side gives (z, y). Match by pixel index; scale/align the shared y.
- Phone tips shown in the UI: dark room/night, hold steady or lean the phone on something, whole
  string in view.

## 4. Panel design (Map tab, redesigned)

- **Areas** overview: one card per area with a small 3D preview, its strings/runs with live colour,
  and quick actions (Run a pattern here, Scan, Edit).
- **3D view**: canvas with orbit (drag to rotate, pinch/scroll to zoom, double tap to reset), dots
  in live colours, a floor grid, area name; run end points editable in a 2D "front" view toggle.
- **Zones**: as today but you can pick areas instead of runs.
- Cohesive with the redesigned panel (HA theme variables, light/dark, 44 px targets, no horizontal
  scroll at 375 px, reduced motion respected).

## API additions (writes admin-only, strict schemas and size limits as in ws_map.py)

| type | payload | returns |
|---|---|---|
| `light_show_studio/map/get` | – | adds `areas: [{id, name, ha_area_id?}]` and per-run `area`, `positions?` |
| `light_show_studio/map/set` | `{map}` v2 | `{map}` |
| `light_show_studio/map/scan/start` | `{entry_id}` | `{bulbs, frames}` (frame count) |
| `light_show_studio/map/scan/frame` | `{entry_id, frame}` | `{}` |
| `light_show_studio/map/scan/stop` | `{entry_id}` | `{}` |
| `light_show_studio/map/zone/start` | adds optional `area` instead of `zone_id` (ad-hoc zone) | `{}` |

## Backend notes

As built in 2.3.0. Deviations and details the frontend needs:

- **Coordinates**: y is now UP (1 = top), z towards the viewer. Version 1 had y down; migration flips every `pts` y
  and a `centre`. Polyline `pts` entries are `[x, y]` (z = 0.5) or `[x, y, z]` on input, always `[x, y, z]` on output.
- **Store**: still a Home Assistant `Store` of version 1; the map inside carries `"version": 2`. A map with
  `"version": 1` (store or `map/set`) is migrated by `mapping.migrate`; a map with no version is read as 2. On load,
  a v1 store is first copied to `light_show_studio.map.v1`, then written back as v2. Migration puts ALL v1 runs in one
  area `{id: "main", name: "House"}` (their coordinates shared one space, so zones sweep exactly as before); angle
  axes become the same direction vector.
- **Areas** are in `map.areas` (`[{id, name, ha_area_id?}]`, also copied to the top-level `areas` of `map/get`). Every
  run has `area` (an area id). A run sent without `area` gets its light's default area (added to `areas`); an unknown
  area is an error on `map/set`. Default ids: `ha_<ha area id>` (name = HA area name, `ha_area_id` set), else
  `light_<entry id>` (name = light title). Limits: 64 areas, unique ids and names (any case), 64 areas per zone.
  `map/get` `lights[]` rows gain `area` (`{id, name, ha_area_id?}`: where a run of that light starts).
- **Run positions**: `positions` on a run is a flat list `[x0, y0, z0, x1, ...]` of ints 0-1000 (floats are rounded,
  out-of-range clamped; `[[x, y, z], ...]` is accepted on input). It must be exactly `3 * count` long or empty (empty =
  not mapped, the polyline is used); a wrong length is an error on `map/set` (drop `positions` when you change `count`).
  A saved map with damaged `positions` keeps the run and loses the positions. Unmapped runs omit the key.
- **Twinkly**: unsaved defaults give a Twinkly run `positions` from `ctl.layout`. The layout may have `[x, y, z]` rows
  and then z is used; `twinkly.flip_layout` currently emits `[x, y]` only (not changed here), so z needs that one-liner.
- **Zones** gain `areas: [area id]`. Slices = the listed `runs` first, then every run of each listed area in the map's
  run order, none twice. A zone may have no runs and only areas.
- **Axis** is stored as `{"dir": [x, y, z]}` (a unit vector) or `{"centre": [x, y, z]}`, plus `"preset"` when the
  vector is one of `left_right`, `right_left`, `rise`, `fall`, `front_back` (dir 0,0,-1), `back_front`, or `outward`
  (with `centre`). Accepted on input: `{"preset": name}`, `{"dir": [...]}` (any non-zero length), v1 `{"angle": deg}`
  (0 = left to right, 90 = up), `{"centre": [x, y]}` (z = 0.5). Default is `left_right`.
- **Spatial u is per area**: each area's pixels are projected on the direction (or distance from the centre) and
  scaled to 0-1 within that area, so a zone over several areas sweeps through all of them at once.
- **Scan** (all admin only; errors are `failed`):
  - `map/scan/start {entry_id, first?, count?}` -> `{bulbs, frames, first, level}`. `first`/`count` code only that stretch
    of the light (pixel `first + i` is code index `i`; the rest stay dark); the whole light by default. Lights without
    per-segment control are refused. The light goes dark at once. Closing the websocket ends the scan.
  - `map/scan/frame {entry_id, frame}` -> `{}`; frame 0 = all dark, 1 = all lit, then bit pairs (see `scan_codes.py`,
    least significant bit first). Lit pixels are `(128,128,128)`, never dimmed by the light's brightness setting. Errors
    if the light is no longer being scanned (a person took it).
  - `map/scan/stop {entry_id}` -> `{}`; restores what the light showed (off if the scan turned it on). Safe to repeat.
  - 120 s without a frame (or start) also stops it. Frames can be sent as fast as the light takes them.
  - `map/get` also returns `scanning: [entry_id]`.
- **Ad-hoc area zone**: `map/zone/start {area, pattern?, effect?, speed?, mode?, axis?}` (give `area` or `zone_id`, not
  both); `area` is an area's id, name or HA area id. The zone id is `area:<area id>` and appears in `running`; stop and
  speed use that id in `zone_id`. It is never saved, follows `map/set`, and ends if its area is removed.
- **Services**: `start_zone` and `stop_zone` take `zone` or `area` (one of them).
- The light entity gets a `scanning` attribute.

### Glow scan (2.4.0)

For hidden strips. `map/scan/segment {entry_id, start, count}` (admin only) -> `{}` lights pixels `start..start+count-1`
of the light at the scan level, all others dark; `start` is a pixel of the light and the stretch must lie inside the
range given to `scan/start` (else `failed`). Dark reference = `scan/frame` 0. Ownership, 120 s idle timeout and restore
are as for `scan/frame`. The panel does the rest (`glow*` functions in `light-show-studio-scan.js`, tests in
`tests/js/test_glow.mjs`): anchors per run, per-anchor glow centroid, interpolation, manual taps.
