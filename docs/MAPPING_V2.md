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
  frame, `map/scan/stop {entry_id}`. Code frames (computed in a pure module `scan_codes.py`, tested),
  two modes chosen by `mode` in `scan/start`:
  - **colour** (default, `"mode": "colour"`): frame 0 = all off (dark reference); frames 1, 2, 3 = every pixel
    red, then green, then blue (calibration: the phone learns each LED's own red/green/blue as the camera sees it,
    so white balance, LED tint and diffusers cancel out); then D = digits of N base 3 (smallest D with 3^D > N)
    frames, one per base-3 digit of the pixel's code `index + 1` (least significant first), each pixel showing
    red, green or blue for its digit with the colour rotating per frame (digit d in digit frame j shows primary
    `(d + j) % 3`, so a pixel is never one colour all the time, and index 0 is not "all red"); and a last check
    frame showing `(sum of digits) % 3` the same way (j = D). Each lit pixel is one channel at 140 (of 255).
    Frames = 4 + D + 1: 904 LEDs -> 1 + 3 + 7 + 1 = 12 frames.
  - **white** (`"mode": "white"`, fallback; also used for recorded video, whose compression smears colour): frame 0
    = all off, frame 1 = all on white, then for each bit b of the pixel index (ceil(log2(N)) bits) two frames:
    pixels with bit b set lit, then the inverse. Lit = white at 128. 904 LEDs -> 2 + 2*10 = 22 frames.
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
  Colour mode (`decodeColourScan`, RGB frames): blobs come from the dark vs red+green+blue calibration difference;
  per blob its calibrated response to red, green and blue is recorded as a chromaticity (RGB difference vs dark,
  normalised to sum 1; rejected as `badCal` if dim or if the three look alike, e.g. a lamp); in each digit frame the
  blob is classified as the nearest calibrated colour (margin between the two nearest must be >= 0.35 and the
  brightness >= 30% of that colour's calibration, else `badDigit`); the digits give code - 1; the check digit must
  match (`badCheck`), the index must exist (`outOfRange`), duplicates keep the clearer read (`duplicate`).
  Chromaticity ignores frame gain, so exposure drift and warm/cool white balance do not matter.
- Two scans: front gives (x, y); side gives (z, y). Match by pixel index; scale/align the shared y.
- Phone tips shown in the UI: dark room/night, hold steady or lean the phone on something, whole
  string in view.

### Multi-angle scan

For lights that wrap round things (a tree, a roofline round a corner) a front scan plus a side scan is not enough. The
wizard's "Scan from several angles" (point method; a checkbox on the first screen, or a button on the review of a
single front scan) runs the ordinary colour scan from 3 to 6 places. No backend change: each angle is a normal
`scan/start` ... `scan/stop`; the panel keeps every angle's decoded pixel positions.

Flow: after each angle the wizard shows how many bulbs were seen in 2+ views ("612 of 904 bulbs seen in 2+ views"), a
top-down sketch of where it thinks you stood (first angle at the bottom, cameras as numbered circles, lights as dots),
and where to go next ("Move about 45 degrees to the left ..."; later, about 45 degrees past the furthest spot on the
side covered less). Buttons: Add another angle, Redo the last angle, Finish. Finish solves (the screen is drawn first;
a typical 1000-light, 6-view solve takes a fraction of a second), shows the result in the usual 3D review (drag dots
to fix them), and Save writes the positions into the run(s) exactly like the single scan, with `depth: true`.
On iOS the Next / "Scan from several angles" tap asks for motion access (`DeviceOrientationEvent.requestPermission`
inside the tap); if granted, each angle's alpha/beta/gamma is kept as a prior. Everything works without it.

The solver is `frontend/light-show-studio-multiview.js` (pure, no DOM; `tests/js/test_multiview.mjs`):

- Input: `solveMultiView(views, {n, fovDeg?})`, views = `[{width, height, points: {index: [u, v]} | Map, fovDeg?,
  orientation?: {alpha, beta, gamma}}]`. Lights are matched across views by index. Pixels are normalised by the longer
  side so one shared focal length `F` covers every view of one phone camera; the start is a 65 degree field of view.
- Pair: the two views sharing the most lights. Essential matrix by Hartley-normalised 8-point in RANSAC with local
  optimisation (Sampson error), the cheirality-valid pose of the four; for flat layouts (a plane homography explains
  90% of the matches) the homography is decomposed instead and both physical answers are tried. Because the focal length is
  a guess, a few values around it are tried and the one explaining the most matches is kept.
- Triangulate (midpoint of rays, positive depth, parallax of at least 1 degree, reprojection check), then add the view
  seeing the most known points by PnP in RANSAC (DLT, or the plane homography for flat point sets), triangulate what it
  newly sees, and run bundle adjustment: Levenberg-Marquardt over every pose, every point and the shared focal length,
  Huber loss, Schur complement (so a 1000-light, 6-view problem needs only a ~37 x 37 solve per step), numerical
  Jacobians, a weak prior on the focal length, first camera fixed. Observations over about 3 px (or 4 times the median)
  are dropped, then it is re-adjusted. A view that cannot be placed (too few lights in common with the others) is
  reported with `ok: false` and the wizard says so.
- Lights seen from only two views are also checked against the string: a point far from the cloud, or far off the line its
  neighbours make, is dropped.
- Output: `{ok, pos: Float32Array(3n) (0-1, y up, z towards the first camera, x right), est: Uint8Array (1 = placed by string
  order or from one view), count (views that saw each light), used (views that went into the 3D point), views: [{ok, rms, count,
  used, center, forward, R}], rms, fovDeg, found, pruned, leveled}`. The scene is fitted into the unit box with the biggest
  extent at 0.9 (like the single scan). Lights seen in fewer than two views: if seen in one view they stay on that view's
  ray with the depth interpolated from their string neighbours (or the view's middle depth); if seen nowhere they are
  interpolated along the string between solid neighbours (up to 60 apart), else copy the nearest; all flagged `est`.
- With orientation from two or more views the scene is levelled so gravity is vertical (the first camera need not be held
  level); it also breaks the tie between the two look-alike answers of a flat layout. Without it, up is the first picture's up.
- Limits: without a point seen in three views the scale of a new view cannot be fixed (an angle that shares lights only
  with one other angle is not placed); keep neighbouring angles within about 45 degrees. A wrongly decoded index seen in
  only two views can survive; the string check removes most.

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
| `light_show_studio/map/scan/start` | `{entry_id, first?, count?, mode?}` | `{bulbs, frames, first, level, mode, hold_ms}` |
| `light_show_studio/map/scan/frame` | `{entry_id, frame, seq?}` | `{seq?}` |
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
- **Areas** are in `map.areas` (`[{id, name, ha_area_id?, outdoor?}]`, also copied to the top-level `areas` of `map/get`). Every
  run has `area` (an area id). A run sent without `area` gets its light's default area (added to `areas`); an unknown
  area is an error on `map/set`. Default ids: `ha_<ha area id>` (name = HA area name, `ha_area_id` set), else
  `light_<entry id>` (name = light title). Limits: 64 areas, unique ids and names (any case), 64 areas per zone.
  `outdoor` (optional bool, default false, must be a real boolean or `map/set` fails) marks an outdoor area; it is
  written only when true (missing = false), so maps saved before 2.5.0 load unchanged. Light schedules use it for
  their `"outdoor"` / `"indoor"` targets (see LIGHT_SCHEDULES.md).
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
  - `map/scan/start {entry_id, first?, count?, mode?}` -> `{bulbs, frames, first, level, mode, hold_ms}`. `first`/`count` code only
    that stretch of the light (pixel `first + i` is code index `i`; the rest stay dark); the whole light by default.
    `mode` is `"colour"` (default) or `"white"`; `frames` and `level` (the lit channel value: 140 or 128) follow the
    mode, and an older light that does not know `mode` answers without it (always white). Lights without
    per-segment control are refused. The light goes dark at once. Closing the websocket ends the scan.
  - `map/scan/frame {entry_id, frame, seq?}` -> `{seq?}`; works for both modes. Colour: frame 0 dark, 1/2/3 all red/green/blue,
    then digit frames and the check frame. White: frame 0 = all dark, 1 = all lit, then bit pairs (see `scan_codes.py`,
    least significant bit first), lit pixels `(128,128,128)`. Never dimmed by the light's brightness setting. Errors
    if the light is no longer being scanned (a person took it) or the frame is past this scan's `frames`.
  - **`hold_ms`** (int, 250-1500): how long the phone should wait between the `scan/frame` / `scan/segment` reply and
    taking the camera picture. 350 by default; WLED 450; raised by the light's measured Wi-Fi latency (the health check
    records a smoothed and a recent-worst round trip; each adds its excess over 50 ms), so a WLED that sometimes takes
    0.7 s gets about 1.1 s. A light with no measurement yet gets the default for its kind. Treat it as the minimum wait;
    it is a recommendation, fixed for the scan (read it from `scan/start`).
  - **`seq`** (optional int, 0 to 2^53-1) on `scan/frame` and `scan/segment`: echoed back in the result as `{seq}`
    (`{}` when none was sent). The panel numbers its requests and drops a reply whose `seq` is not the latest one it
    sent. Errors carry no `seq`; they come as the usual `failed` error for that request id.
  - Delivery: the reply comes as soon as the frame is sent. Frames go out over UDP (Govee, Twinkly, WLED DDP), so each
    scan frame is sent again 50 and 150 ms later (identical, harmless) in case a packet is lost. A WLED held by a scan
    resends its last frame every 0.4 s (1 s normally) so it never drops out of realtime mode and back to its effect.
    The panel should still wait `hold_ms` after the reply before the picture.
  - Ownership and rate: the connection that sent `scan/start` owns the scan; `scan/frame`, `scan/segment` and `scan/stop`
    from another connection fail with `failed` ("started by another connection"). A scan accepts about 30 frame/segment
    commands a second (`failed`, "Too many scan commands"); the scan is not stopped by it.
  - Robustness: if the socket that started a scan closes, or `scan/stop` arrives, while the light is still turning on,
    the light is let go (restored) once it has. A new `scan/start` for a light replaces the old scan; the old
    connection's later close does not end it. `map/get.scanning` lists only lights actually held by a scan.
  - `map/scan/stop {entry_id}` -> `{}`; restores what the light showed (off if the scan turned it on). Safe to repeat.
  - 120 s without a frame (or start) also stops it. Frames can be sent as fast as the light takes them.
  - `map/get` also returns `scanning: [entry_id]`.
- **Ad-hoc area zone**: `map/zone/start {area, pattern?, effect?, speed?, mode?, axis?}` (give `area` or `zone_id`, not
  both); `area` is an area's id, name or HA area id. The zone id is `area:<area id>` and appears in `running`; stop and
  speed use that id in `zone_id`. It is never saved, follows `map/set`, and ends if its area is removed.
- **Services**: `start_zone` and `stop_zone` take `zone` or `area` (one of them).
- The light entity gets a `scanning` attribute.

### Glow scan (2.4.0)

For hidden strips. `map/scan/segment {entry_id, start, count, colour?, seq?}` (admin only) -> `{seq?}` lights pixels
`start..start+count-1` of the light, all others dark; `colour` is `[r, g, b]` (0-255), white at 128 by default.
`start` is a pixel of the light and the stretch must lie inside the range given to `scan/start` (else `failed`).
Instead of `start`/`count` a call may give `segments: [{start, count, colour?}]` (1-3 stretches, must not overlap) to
light up to three stretches at once; their colours default to red, green, blue (at 140) in that order. In the panel
(`glowScheduleColour`, `glowColourModel`, `findGlowsColour`): the whole scanned range is first lit red, green, blue to
learn how the camera sees each colour on those surfaces, then each picture lights three well-separated anchors of a
run at once, and the picture is unmixed into a red, a green and a blue glow map, one glow per colour, so a run's
anchors take a third of the pictures. Dark reference = `scan/frame` 0. Ownership, 120 s idle timeout and restore
are as for `scan/frame`. The panel does the rest (`glow*` functions in `light-show-studio-scan.js`, tests in
`tests/js/test_glow.mjs`): anchors per run, per-anchor glow centroid, interpolation, manual taps.

### Map effects (2.6.0)

Zones and area zones accept seven more effects that are drawn from the 3D positions: Sweep, Ripple, Snowfall, Rise,
Rainbow Wash, Fireworks, Breathe Outward (`map/get` lists them as `spatial_effects`). They use the zone's axis (a direction, or a
`centre` point) and are always spatial, whatever `mode` says. See `MUSIC_PAINT_FX.md` section 4. Zones also take your own
patterns (including painted ones: per-light paint, and paint by 3D position per area) and fade in / out (`transition`).

### Trace and edit: breaks and paint probe (2.6.2)

A traced run can have **breaks**: a corner flagged as starting after a break (`corner[3] = 1`, the "Break before this corner (no
LEDs)" button, shown when any corner but the first is selected). The stretch before that corner has no LEDs, so `Geo.spread`
gives it no length and the pixels keep an even spacing along the stretches that do. Breaks draw as a dashed line. In `pts` a break
is saved as repeated corners, `[.., A, A, B, B, ..]` (A the last corner before the break, B the first after), so the saved format
and backend are unchanged (`Geo.encodeBreaks` / `Geo.decodeBreaks`). A saved run whose `pts` repeat a corner any other way
(`[A, A, B, C]`) is not a break. Moving, turning, mirroring and reversing a whole run keep its breaks.

The light probe has a **Paint** mode: with "Light the pixel I pick" on, the real strip shows the pixels before the focus pixel
green, the focus pixel white and the rest dim red, using `map/scan/segment` with up to 3 `segments` (`start`, `count`, `colour`).
Tap or drag a dot to move the focus. Tests: `tests/js/test_trace.mjs`.
