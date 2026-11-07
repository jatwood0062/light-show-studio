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
