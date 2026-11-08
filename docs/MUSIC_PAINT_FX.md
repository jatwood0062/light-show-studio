# Transitions, music sync, painted patterns and map effects (2.6.0)

Backend contract for the UI. Everything below is implemented and tested; the websocket commands use
`type: "light_show_studio/<name>"`. "Admin" means a Home Assistant administrator; every other command works for any
signed-in user. Error codes are Home Assistant's: schema failures (a wrong type, a number out of range, an unknown key)
come back as `invalid_format` with a message like `expected int @ data['sensitivity']`.

Code: `fade.py` (fade maths), `controller.py` (light fades), `music_engine.py` / `musicmanager.py` / `ws_music.py`,
`user_patterns.py` / `pattern_store.py` / `patternpreview.py` / `ws_patterns.py`, `map_effects.py` / `mapped_patterns.py`.

---

## 1. Transitions

* The light entity supports Home Assistant's `transition` (`LightEntityFeature.TRANSITION`) on `light.turn_on` and
  `light.turn_off`. The `set_pattern`, `start_zone` and `stop_zone` services take `transition` (seconds, 0-1800) too.
* Per light option `default_transition` (seconds, 0-30, default **0.7**, set in the light's Configure dialog; 0 = instant).
  Used when a call gives no `transition`. The light shows it as the attribute `default_transition`.
* **Turn on** fades in from black. **Turn off** fades out, then the light is switched off at the end (it reports `on` during
  the fade). Turning on again during a fade-out brings it back up from where it got to.
* **Changing the look** (a colour, an effect or a pattern, by `light.turn_on` or `set_pattern`; `set_pixels`, single-bulb
  edits and speed-only changes are not blended) cross-fades the previous picture into the new one: the old frame is frozen and dissolves while the new look
  keeps animating, and the effect clock is never restarted, so there is no stutter or jump.
* **Never faded**: show mode / xLights frames, camera-scan frames, zone frames (zones have their own fade, below).
  Entering show mode, a zone or a scan drops any fade in progress.
* Long fades (a 120 s sunset) are a **brightness ramp**, not a frame blend: still looks send about 2 frames a second,
  never more than 25.
* **Zones** (saved zones, area zones): `map/zone/start` takes `transition` (fade-in, and also the fade-out used when it is
  stopped); `map/zone/stop` takes `transition` (fade-out; omitted = the zone's own, else 0.7 s). The `start_zone` /
  `stop_zone` services take `transition` too. A zone that is changed while running (new pattern / effect / a map change)
  does **not** fade in again. `transition: 0` is instant. A stopped zone vanishes from `running` at once while its lights
  finish fading.
* **Light schedules**: new top-level fields `fade_in_s` and `fade_out_s` (0-1800 seconds, number, default **2** each), see
  `LIGHT_SCHEDULES.md`.

Music sessions and previews fade in/out by 0.4/0.6 s and 0.2/0.3 s.

---

## 2. Music sync

The phone listens with its microphone and sends audio features; the lights follow. Nothing here is admin-only.

A **session** takes its lights the way a zone does (so the light's `zone` attribute is `"Music"`, its `effect` attribute
is empty) and belongs to the websocket connection that started it: only that connection's frames feed it, and it ends when
the connection closes. A person picking a colour / effect, a show starting, or the light being turned off ends the session
**for that light** (the others carry on). When the session ends the light shows what it showed before (or goes off if the
session turned it on). Only one session can hold a light: starting another session on an overlapping light stops the
older one (starting the same targets again swaps in place without the lights flashing back). Max 16 sessions.

### `music/start`
```json
{"type": "light_show_studio/music/start",
 "targets": {"lights": ["<entry_id>"], "areas": ["<area id or name>"]},
 "pattern": "Candy Cane", "effect": "Pulse", "sensitivity": 50,
 "mode": "string", "axis": {"preset": "rise"}, "idle_effect": "Twinkle"}
```
| field | rules |
|---|---|
| `targets.lights` / `targets.areas` | each 0-64 strings of 1-64 characters; at least one entry in total. Lights are config entry ids (`map/get` `lights[].entry_id`); areas by id, name (any case) or Home Assistant area id. A light that has runs in a listed area is driven through them, not twice |
| `pattern` | optional; a built-in pattern or one of your own (by name; default `Classic Multicolor`) |
| `effect` | `"Pulse"` (default), `"Beat Chase"`, `"Spectrum"`, `"Flash"`, `"Beat Scatter"`, `"Gravity Drop"`, `"Beat Shell"` |
| `sensitivity` | whole number 1-100, default 50 (a gain from 0.2x to 5x on the incoming levels; 50 = as sent) |
| `mode`, `axis` | areas only, as for `map/zone/start`: `"string"` (default) or `"spatial"`. Spectrum in spatial mode maps by height (see below); the others use the pattern as laid out |
| `idle_effect` | optional; the look while no music arrives (any effect incl. map effects). Default: a pattern of yours uses its own `default_effect`, a built-in one `Steady` |

Result: `{"session": "music:ab12cd34", "lights": ["<entry ids actually taken>"], "effect", "pattern", "sensitivity",
"timeout": 3.0}`.
Errors (`failed`): `Effect must be one of ...`, `Unknown pattern`, `Unknown idle effect`, `That light isn't set up`,
`No area called <ref>`, `There are no pixels to light`, `None of those lights could be used`, `Too many music sessions are
running`; (`invalid_format`): `Pick at least one light or area`.

### `music/frame`
```json
{"type": "light_show_studio/music/frame", "targets": {"lights": ["<entry_id>"], "areas": []},
 "level": 0.62, "bass": 0.8, "mid": 0.4, "treble": 0.2, "beat": true, "bpm": 128}
```
`level`, `bass`, `mid`, `treble`: JSON numbers 0-1 (finite; strings, booleans, null, NaN, Infinity are refused). `beat`:
**must be a boolean**. `bpm`: optional number 30-300. Missing or extra fields are refused. The frame goes to this
connection's sessions that include any of the lights / areas named. Send about 20-40 a second.

Result `{"fed": <number of sessions>}`. Errors: `invalid_format` (the message names the field), `not_found` (`Music isn't
running for those lights or areas` - no session of yours matches, e.g. it ended: start again), **`rate_limited`** (more
than 40 frames a second on this connection, with a burst of 8; the frame is dropped, send fewer).
You may fire and forget (do not wait for the result) but handle `not_found`/`rate_limited` errors if you watch them.

### `music/stop`
`{"type": ..., "targets"?: {"lights": [...], "areas": [...]}}` -> `{"stopped": <sessions ended>}`. Without `targets` it ends
everything this connection started. Closing the websocket does the same.

### What the effects do
All use the pattern's colours, smoothed with attack/release envelopes (level: 35 ms up, 300 ms down; bands: 50 ms / 350 ms)
so nothing flickers.

| effect | looks like |
|---|---|
| Pulse | brightness follows `level`, with an extra kick on each beat |
| Beat Chase | the pattern steps along the string / axis by a stride on **every beat** (glides over ~70 ms); between beats it holds, brightness follows `level`. Stride = max(bulbs per colour, pixels / 30) |
| Spectrum | `bass`, `mid`, `treble` light the first, middle and last third of the string (soft edges). In spatial area mode: bottom / middle / top by the pixels' **height** (y) in the area |
| Flash | each beat flashes (whitened) and decays in ~150 ms; dim glow between |
| Beat Scatter | on every beat a fresh random 30 % of the bulbs flash to full (whitened) and fade over ~0.3 s; which bulbs is a hash of (bulb, beat number), so it is never the same set twice running. The previous beat's bulbs are still fading, so quick beats overlap. Dim glow (follows `level`) between |
| Gravity Drop | on every beat a bright band drops from the top (the end of the string) under gravity, lands in ~0.45 s with a flash at the bottom, bounces twice or three times with less height each time, then settles and fades after ~2.5 s. A trail follows it down. In spatial area mode it falls by the pixels' **height** (y) |
| Beat Shell | on every beat a ring of light opens from the centre of the area (the axis' `centre` point if it has one, else the middle of the area's mapped pixels; each area is its own space) out to the edge in ~0.7 s, thinning and fading. On a string (or in string mode) it opens from the middle of the string. Only works as a 3D shell in `mode: "spatial"` |

Beats closer than 120 ms count as one. **No frame for 3 s** (`timeout`): the music eases (about 1 s) into the pattern's normal look
(`idle_effect`) at 70 % brightness and the session stays alive; frames returning ease back into the music (about 0.2 s).
The light's attribute `music` tells the UI: `null` when no session holds it, else
`{"active": true, "effect", "pattern", "sensitivity", "live": bool}` (`live` = frames are arriving).

---

## 3. Your own patterns (paint and save)

Store `light_show_studio.user_patterns` (list of patterns). They work anywhere a built-in pattern name is accepted:
`set_pattern`, `start_zone`, `map/zone/start`, `map/set` zones, light schedules, music. They can be given by **name
(any case) or id**; the server stores and returns the **name**. They are listed after the built-ins in the light's
`pattern_list` attribute and in `map/get` `patterns`. If one is renamed, deleted or edited, lights, saved zones, running
zones and schedules that use it follow (renamed: new name; deleted: the default pattern; edited: running zones restart
with the new colours).

### The pattern object
```json
{"id": "ab12cd34", "name": "Warm porch", "kind": "palette",
 "colours": [[255, 120, 30], [255, 200, 120]], "bulbs_per_colour": 2,
 "default_effect": "Twinkle", "default_speed": 50}

{"id": "ef56ab78", "name": "Tree painting", "kind": "pixels",
 "lights": {"<entry_id>": {"first": 0, "colours": [[255, 0, 0], [0, 255, 0]]}},
 "areas": {"<area id>": [{"pos": [500, 800, 120], "colour": [255, 255, 255]}]},
 "default_effect": "Steady", "default_speed": 50}
```
| field | rules |
|---|---|
| `id` | assigned by the server (8 hex characters); send it to replace a pattern |
| `name` | 1-40 characters (inner whitespace collapsed), no control characters, not starting with `~`, **unique ignoring case**, and not the name of a built-in pattern |
| `kind` | `"palette"` or `"pixels"` |
| `colours` (palette) | 1-16 colours `[r,g,b]` whole numbers 0-255 |
| `bulbs_per_colour` (palette) | whole 1-20, default 1 |
| `lights` (pixels) | up to 64 light entry ids; each `{"first": 0-2039 (default 0), "colours": [[r,g,b], ...]}`, 1 to (2040 - first) colours - colour `i` is pixel `first + i` of that light |
| `areas` (pixels) | up to 64 area ids; each 1-2040 points `{"pos": [x, y, z], "colour": [r,g,b]}` with `pos` whole numbers **0-1000** (the map's position scale; x left to right, y up, z towards the viewer) |
| pixels, in total | at least one light or area; at most 12000 colours + points in all |
| `default_effect` | one of the string pattern effects (`patterns/list` -> `effects`; not map effects), default `Twinkle` |
| `default_speed` | whole 1-100, default 50 |

Max **200** patterns. The UI should apply `default_effect` / `default_speed` when someone picks a pattern; the server does
not apply them by itself (a service call with just a pattern keeps the current effect), except as the music idle look.

### How a pixels pattern is drawn
* On a single light: the pixels painted for that light show where painted; anywhere else (pixels outside the painted
  range, or a light the pattern doesn't cover) the pattern's **palette of used colours** (up to 16 distinct colours, in the
  order painted: lights first, then areas) repeats along the string.
* In a zone / area zone: each virtual pixel takes, in this order, the colour painted on **its own pixel of its light**,
  else the colour of the **nearest painted point in its area** (by 3D position; positions come from the map), else the
  palette repeated by virtual index.
* **Effects apply on top**: Twinkle / Fade / Wave / Marquee / Alternate dim the painted colours, Sparkle adds glints,
  Twinkle Swap picks from the used colours, **Chase slides the painted colours along** (in spatial zones, in order along
  the axis). Map effects (Sweep ...) use the painted colours as their base.
* A map effect given to a single light shows as its nearest string effect (Sweep/Rise/Rainbow Wash -> Chase, Ripple -> Wave,
  Snowfall -> Twinkle, Fireworks -> Sparkle, Breathe Outward -> Fade, Gradient Axis -> Gradient Flow, Plasma Field -> Plasma,
  Candy Helix -> Chase).

### `patterns/list`
`{type}` -> `{"patterns": [pattern, ...], "builtin": [names], "effects": [string effects], "map_effects": [map effects],
"limits": {"max_patterns": 200, "max_name": 40, "max_palette_colours": 16, "max_bulbs_per_colour": 20, "max_light_pixels":
2040, "max_area_points": 2040, "max_painted": 12000, "max_paint_batch": 500, "position_max": 1000}}`. Any user.

### `patterns/save` (admin)
`{type, pattern: {id?, ...}}` -> `{"pattern": <saved, with id>}`. No `id` creates; an `id` replaces that pattern (send every
field). Errors: `invalid_format` with a plain message (e.g. `There is already a pattern called Mine`, `Mine is the name of
a built-in pattern`, `A palette needs 1 to 16 colours`, `At most 200 patterns`), `not_found` (`No pattern <id>`).

### `patterns/delete` (admin)
`{type, pattern_id}` -> `{}`; `not_found` if unknown.

### `patterns/preview`
```json
{"type": "light_show_studio/patterns/preview", "entry_id": "<id>"  /* or */ "area": "<area id or name>",
 "pattern": { ...an unsaved pattern object (name optional, id ignored)... }  /* or the name of a saved / built-in one */,
 "effect": "Steady", "speed": 50}
```
`entry_id` and `area` are mutually exclusive; exactly one is required. `effect`: any effect incl. map effects (default
`Steady`); `speed` 1-100 (default 50). The pattern is shown live; it takes the lights like a zone does (fades in over 0.2 s;
a person or a show taking a light ends it for that light) and **restores them** on `preview_stop`, after **60 s without a
`preview` or `paint` call**, or when the websocket closes. One preview per connection: calling again changes it in place with no
flash back to the old look. Result `{"lights": [entry ids], "pattern": "<name used>", "idle_seconds": 60}`. Errors
(`failed`): `Unknown pattern`, `Unknown effect`, `No area called ...`, `That light isn't set up`, a validation message from the
pattern object (e.g. `A palette needs 1 to 16 colours`), `There are no pixels to light`.
The preview pattern is not saved and not listed anywhere.

### `patterns/preview_stop`
`{type}` -> `{"stopped": bool}`.

### `patterns/paint`
`{type, entry_id, set: [[index, r, g, b], ...]}`, 1-500 pixels (whole numbers: index 0-2039, colours 0-255). Paints the
pattern **being previewed** (it must be a `pixels` pattern sent as an object in `preview`) on that light (the light must be part of
the preview) and shows it immediately, before the call returns; also re-arms the 60 s timer. Painting beyond the painted range
extends it (gaps are black). Result `{"painted": n}`. Errors (`failed`): `No preview is running`, `Only a pixels pattern can be
painted`, `That light isn't part of the preview`, `A pixel index must be between 0 and <bulbs - 1>`, `That pattern has too many
painted pixels`. The server's copy of the pattern changes, not yours: when saving, send the full `pixels` object (keep your own copy of
the painted colours; or read `patterns/list` after saving).

---

## 4. Map effects (spatial)

Seven named effects for **zones and area zones**; they use the 3D positions of the map, one space per area. Listed in
`map/get` as `spatial_effects` (also `effects` = the string effects and `patterns` = every pattern name). Accepted as `effect` in
`map/zone/start` (zones and `area`), the `start_zone` service, saved zones in `map/set`, light-schedule `look.effect`, `music`'s
`idle_effect` and previews. They are always laid out by position, whatever the zone's `mode` says.

They read the zone's **axis** like the spatial string effects: `{"dir": [x,y,z]}` / a preset (`left_right`, `right_left`, `rise`,
`fall`, `front_back`, `back_front`), or `{"centre": [x,y,z]}` (outward from a point, what the "Outward" preset stores).

| effect | what it does | uses |
|---|---|---|
| Sweep | a soft band crosses the area (about every 5 s at speed 50) over a dim glow, in the pattern's colours | direction, or rings out from the centre |
| Ripple | rings spreading out from a centre point, each ring the next colour of the pattern | centre (default: middle of the area's mapped pixels) |
| Snowfall | flakes falling in y with a little sideways drift; pixels glow as a flake passes; colours from the pattern | nothing: always falls |
| Rise | a glow fills the area along the direction, brightest at the leading edge, then fades and repeats | direction (use `rise` to climb up), or outward |
| Rainbow Wash | hue changes along the direction and drifts with time (ignores the pattern's colours) | direction, or distance from centre |
| Fireworks | random bursts: a shell expanding in 3D from a random point, in a colour of the pattern, fading | nothing (random points inside each area) |
| Breathe Outward | brightness waves travel out from the centre | centre (default: middle of the area) |

The next three live in `map_effects_extra.py` (same interface as the others; `map_effects.py` registers them with
`_EFFECTS.update(EXTRA_EFFECTS)`). All three are pure functions of `t`.

| effect | what it does | uses |
|---|---|---|
| Gradient Axis | the pattern's colours as a smooth (OKLab) gradient along the direction, first colour at one end and last at the other, scrolling slowly. Set the axis to `rise` for a gradient by height | direction, or distance from centre |
| Plasma Field | a plasma in 3D: sine waves along x, y, z and out from the middle of the area, coloured through the pattern's gradient; scaled to each area | nothing (3D positions) |
| Candy Helix | stripes that spiral up a vertical axis (a tree): the colour depends on the angle around the area's middle plus the height, 3 stripes round, climbing 1.5 turns, turning slowly. The pattern's colours are the stripes; a pattern with fewer than two colours gives red and white (use `Candy Cane` for the classic look) | nothing (y is up) |

`speed` scales time as for any effect. Performance: geometry is worked out once per start / map change (about 3 ms for 1500
pixels); a frame takes about 0.1-1.6 ms on a laptop for 1500 pixels (a few times that on a Pi 4).
With a pixels pattern the painted colours are the base the effects light.
On a **single light** (service `set_pattern`, light schedule on a light) a map effect becomes its nearest string effect (table in
section 3).

---

## More pattern effects, palettes and the Palette type

New pattern effects (string effects: `PATTERN_EFFECTS`, usable on lights, zones, area zones, schedules, music idle looks). All are
pure functions of the clock (hashed event slots, smooth value noise; no per-frame randomness and no remembered state), so a late
frame just jumps to the right picture. They colour the string from the **pattern's colours**:

| effect | what it does |
|---|---|
| Gradient Flow | the pattern's colours as a smooth gradient scrolling along the string (~36 bulbs per loop on 50 bulbs, longer on long strips) |
| Plasma | a sine-sum plasma coloured through the pattern's gradient |
| Noise Drift | slow smooth value noise drifting the pattern's colours through each other |
| Storm Flash | a dark sky (the pattern's average at 6 %) with lightning: roughly every 3-4 s a strike of 2-3 flickers that spreads out from a point, in the pattern's lightest colour pulled towards white |
| Confetti | random bulbs pop in a random colour of the pattern and fade over about a second, over a dim copy of the pattern |
| Dissolve | bulbs fill in at random until the string is full, hold, then drain away in another random order; 12 s round, a new order each round |
| Sunrise Ramp | a slow warm-up over 60 s from an ember glow through orange to the pattern's colours, spreading in from the start of the string, holding 15 s, then dipping and starting over |

The light effect list (`effects.EFFECT_LIST`) gets the same seven names with built-in colours (Gradient Flow = dusk gradient, Plasma =
party gradient, Noise Drift = ocean-aurora gradient, Confetti = bright mixed colours; Storm Flash, Dissolve and Sunrise Ramp use the light's
colour), and **Fire** no longer calls `random` per frame: the same orange-red flicker, but smooth noise in time and along the string.

`effects.Palette` is a list of `(position 0-1, RGB)` gradient stops with `at(x)` (looping or clamped) and `mode` `"oklab"`
(default, even brightness) or `"linear"` (RGB); `Palette.even(colours)` spaces colours evenly and `effects.palette_for(tuple)`
is a cached looping palette. `effects.WARM_WHITE = (255, 176, 100)` is a warm white for RGB-only bulbs (a real white channel
should still use (255, 255, 255)).

New built-in patterns (`patterns/list` `builtin`): Diya Lamps, Lunar Red & Gold, Menorah Lights, Autumn Harvest, Cranberry & Gold,
Evergreen & Gold, Winter Frost, Romance Wine, Lucky Green, Easter Egg, Spring Blossom, Pride Six, Cosy Amber, Deep Ocean, Aurora
Borealis, Sunset Blaze. Existing patterns keep their colours; the colour sets that `patterns.py` and `effects.py` both used are
defined once in `effects.py` (`CLASSIC`, `PASTELS`, `HALLOWEEN_C9`, `GOLD_WHITE_C9`; the string effects' own two-colour Halloween and
soft Gold & White keep their looks).

---

## Limits and decisions the UI must know

* Music: rate limit 40 frames/s per connection (burst 8), strict field validation, 3 s timeout, 16 sessions, sessions die with the
  connection. The phone must keep the websocket open and send frames; there is no server-side microphone.
* `music/frame` and previews are open to every signed-in user (like switching a light); pattern `save` / `delete` need admin.
* Patterns: 200 max, names 1-40 characters unique ignoring case, references by name or id, deleting is allowed (users fall back to
  the default pattern). The first `pixels` preview needs the light's `entry_id` keys in `lights`.
* A pattern is rendered per light by the light's config entry id, so a pixels pattern painted for light A does not appear on light B
  except as its repeated palette.
* Fades never apply to xLights / show frames. `default_transition` is a per-light option; zones and schedules default to 0.7 s and
  2 s respectively.
* A light that is fading out still reports `on`; do not rely on `off` within the fade time.
