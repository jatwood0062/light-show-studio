# Changelog

Changes to Light Show Studio, newest first.

## [Unreleased]

## [2.6.5] - 2026-11-11

### Fixed
- Govee strings freezing mid-effect or mid-show: realtime mode was switched on once and never again, so a light
  that dropped out of it (a lost packet, a command from another app) ignored every frame. It is now switched on
  again every 2 seconds while frames flow, and retried at once if the packet couldn't be sent.
- Show frames lost on Govee strings, WLED and Twinkly: a frame arriving less than 20 ms after the previous one was
  thrown away (common with xLights live output and while a show catches up with its speaker), and if it was the
  last frame the light stayed on the wrong picture. The newest frame is now held and sent as soon as it's due.
- A still picture (a colour, a settled fade, a held show frame) was sent once; if that packet was lost the light kept
  the wrong picture. Govee strings now resend it every second for 10 seconds.
- WLED and Twinkly dropped back to their own effect when a show paused or held its last picture (WLED leaves realtime
  mode after 2.5 s without a frame). A held show picture is now kept up; a show ending on black still lets go.
- Strings running the same effect drifted out of step (each kept its own clock and beat). All lights now tick on one
  shared beat, and a light starting an effect another light is already running picks up its timing.

### Changed
- DDP packets to WLED carry a sequence number, so a late packet from an older frame can't tear the current one.
- Twinkle, Sparkle, Confetti, Noise Drift, Storm Flash, Fire and Starfield draw 1.5-2.5x faster (per-bulb values that
  never change between frames are worked out once), which matters on large WLED zones on a Raspberry Pi.
- The show player wakes once per frame instead of several times unless it is catching up with its speaker.

## [2.6.4] - 2026-11-10

### Fixed
- "Login attempt failed" alerts from Sonos speakers: after a show, the speaker kept the show's song in its queue and
  kept asking for it after its signed link expired. When a show ends and the speaker has stopped on the show's song,
  that song is now cleared from the speaker's queue.

## [2.6.3] - 2026-11-09

### Fixed
- Saving a light schedule failed with "No light …" when it still named a light that had since been removed. Removed
  lights are now dropped on save (the editor drops them too); it only asks you to pick again if nothing is left.

## [2.6.2] - 2026-11-08

### Added (Trace and edit)
- Breaks in a traced run: flag a corner as starting after a break ("Break before this corner (no LEDs)" in the Trace panel; "Remove
  break" undoes it). The stretch before it has no LEDs and takes no length when the pixels are spread, so a strip that skips
  a doorway or a wall keeps even spacing on both sides. Breaks draw as a dashed line. They are saved in the same `pts` as
  repeated corners (`[.. A, A, B, B ..]`), so there is no backend change, and they load back as breaks.
- Light probe "Paint" mode: with "Light the pixel I pick" on, choose "Paint" and the real strip shows the pixels before the focus
  pixel green, the focus pixel white and the rest dim red (`scan/segment` with up to 3 `segments`). Tapping or dragging a dot
  moves the focus.

### Fixed (Trace and edit)
- Moving, turning or dragging a corner of a whole traced run, and "Start at the other end", now keep the breaks; turning a run
  no longer shifts its corners when it has a break. The first corner never carries a break flag.
- Tests: `tests/js/test_trace.mjs`.


### Added
- WLED option `device_offset` (0-2039, default 0): the first LED on the controller this light writes, for controllers with a
  dummy output or another strip before it. Every frame (shows, effects, zones, scan, identify, keepalive) goes out over DDP
  at byte offset `device_offset * 3`, so earlier LEDs are untouched. Setup and Configure refuse bulbs + offset beyond the LEDs
  the device reports. The light's on/off and brightness are still the whole controller's.
- Option `show_lead_ms` (-500 to 500, default 0) on every light: during .fseq show playback the light gets the frame for
  time + lead (positive = early, for a light whose network delivers late), clamped to the show. Other lights and the audio
  sync are unaffected; frames from xLights / E1.31 are not shifted.

## [2.6.1]

### Added
- Light schedules: look rotation (`looks` + `rotate`: a new look every N minutes, or a different one each night, the same
  after a restart), a built-in holiday calendar (`holiday_looks`; Easter, Thanksgiving, Mother's / Father's Day, Memorial /
  Labor Day, fixed dates, and Hanukkah / Diwali / Lunar New Year for 2026-2030) whose looks, windows and enabled flags can be
  overridden (`light_schedules/holidays`, `holiday_set`, `holiday_reset`), a vacation-style `random_offset_min` (0-60) that
  moves the on and off times by a steady per-day amount, and `only_when` home / away / any (checked when the window opens).
  Schedules saved before this load unchanged. See `docs/LIGHT_SCHEDULES.md`.
- New pattern effects (also in the light effect list), all pure functions of the clock so late frames just jump: Gradient Flow,
  Plasma, Noise Drift, Storm Flash, Confetti, Dissolve and Sunrise Ramp. They colour the string from the pattern's own colours.
- New map effects for areas (`map_effects_extra.py`): Gradient Axis (a gradient along the axis or by height), Plasma Field (3D
  plasma) and Candy Helix (stripes that spiral up a tree; red and white unless the pattern has more colours).
- New music effects: Beat Scatter (a random handful of bulbs flashes on each beat), Gravity Drop (a band drops on each beat and
  bounces) and Beat Shell (a ring opens from the centre of the area on each beat).
- 16 new built-in patterns: Diya Lamps, Lunar Red & Gold, Menorah Lights, Autumn Harvest, Cranberry & Gold, Evergreen & Gold, Winter
  Frost, Romance Wine, Lucky Green, Easter Egg, Spring Blossom, Pride Six, Cosy Amber, Deep Ocean, Aurora Borealis, Sunset Blaze.
- `effects.Palette` (gradient stops, OKLab or linear blending) and `effects.WARM_WHITE` (255, 176, 100) for RGB-only bulbs.

### Changed
- Twinkle, Rainbow / Vintage / Christmas Twinkle, Sparkle and the Twinkle, Twinkle Swap and Sparkle pattern effects are now truly
  random instead of every bulb swelling on its own regular cycle. Each bulb pops at random moments for a random time and peak,
  with only a few bulbs changing at once, so a late or dropped Wi-Fi frame no longer reads as a stutter. They depend only on the
  clock (not on how many frames were drawn), bulbs rest at the same base look as before, and each light twinkles differently.
- Fire no longer uses a fresh random number per bulb per frame: the same orange-red flicker, but stable (smooth noise in time).
- The colour sets shared by `patterns.py` and `effects.py` (Classic, Pastel, Halloween, Gold & White) are defined once; no colours change.

### Fixed
- A running zone no longer starts its chase / sweep over from the beginning when its map, pattern or look is edited: the new run
  carries on from the old one's clock.

## [2.6.0] - 2026-11-07

### Added
- Transitions: turning a light on fades it in, turning it off fades it out, and changing the look (colour, effect, pattern)
  cross-fades the old picture into the new one without restarting the effect clock. Home Assistant's `transition` works on
  `light.turn_on` / `light.turn_off` (`LightEntityFeature.TRANSITION`); new light option `default_transition` (0.7 s, 0 =
  instant). xLights / show frames are never faded. Zones and area zones fade in and out too (`transition` on `map/zone/start`,
  `map/zone/stop` and the `start_zone` / `stop_zone` / `set_pattern` services).
- Light schedules: `fade_in_s` / `fade_out_s` (0-1800 s, default 2 each). Long fades such as 120 s at sunset are a slow brightness
  ramp.
- Music sync (backend): `light_show_studio/music/start`, `music/frame` and `music/stop`. A phone listens with its microphone and
  sends level / bass / mid / treble / beat; effects Pulse, Beat Chase, Spectrum and Flash on lights and areas, with attack/release
  smoothing, a 3 s timeout back to the pattern's normal look, 40 frames a second per connection, and everything restored on stop
  or when the connection closes. New light attribute `music`.
- Your own patterns: palettes and painted pixels (per light, and per area by 3D position), stored in
  `light_show_studio.user_patterns` (max 200). `light_show_studio/patterns/list|save|delete`, live `patterns/preview` /
  `preview_stop` and the instant brush `patterns/paint`. Usable by name or id in `set_pattern`, zones, area zones, light
  schedules and music; they appear after the built-ins in `pattern_list`. Effects work on top of painted pixels.
- Map effects for zones and area zones: Sweep, Ripple, Snowfall, Rise, Rainbow Wash, Fireworks and Breathe Outward, drawn from the
  3D positions of the map (`map/get` `spatial_effects`). Light schedules accept them as `look.effect` for area targets.
- See `docs/MUSIC_PAINT_FX.md` for the exact websocket contract.

### Changed
- Light schedules apply a look to a light in one step (pattern, effect, speed and brightness together).
- `async_turn_off` and `async_set_pattern` on the controller take `transition` (and `brightness` for set_pattern).

## [2.5.0] - 2026-11-07

### Added
- Colour camera mapping, now the default: instead of bright white flashes the lights show red, green and blue. Three
  calibration frames (all red, all green, all blue) teach the phone how it sees each LED, then one frame per base-3 digit
  of the light's number and a check frame; 904 lights take 12 frames instead of 22, at a gentler level (140 of 255 on one
  channel), and work with the room lights dimmed rather than fully dark. White flashes remain under More options ("Use white
  flashes"), and a recorded video always uses white. `light_show_studio/map/scan/start` takes `mode` ("colour" or "white")
  and returns `{bulbs, frames, first, level, mode}`.
- Glow scan: `map/scan/segment` takes `colour: [r, g, b]` and `segments: [{start, count, colour}]` (up to three at once,
  red/green/blue by default), so hidden-strip anchors can be found three at a time. The decoder side is in place; the
  wizard still lights one spot at a time.
- Scan from several angles: scan from 3 to 6 places and get a true 3D map. The wizard shows how many lights are seen from
  two or more angles, a top-down sketch of where you stood and where to go next; the phone's tilt (if allowed) levels the
  model. The solver (essential matrix or plane homography, PnP, bundle adjustment with a shared focal length) is in
  `light-show-studio-multiview.js`.

## [2.4.0] - 2026-11-06

### Added
- Glow scan (hidden strips): a second way to map a light, for strips the camera never sees as dots (under cabinets,
  behind covers) where only a soft glow lands on the counter, floor or ceiling. The wizard lights a short stretch of a
  run at a time (about 16-24 spots per run, 6-10 pixels each, both adjustable under More options), with dark reference
  pictures in between, finds the brightness-weighted centre of the biggest glow for each, and interpolates the pixels
  between the spots in string order. Spots it can't find are shown frozen and you tap where the glow is, or skip them.
  The review shows how sure each spot was. Saves into the runs' positions like the point scan; an optional side scan
  adds depth; without a secure context it falls back to a filmed video like the point scan.
- The wizard offers both methods, recommends Glow for WLED lights, and offers a one-tap switch to Glow after a point
  scan that finds few lights.
- `light_show_studio/map/scan/segment {entry_id, start, count}` (admin only): lights only pixels start to
  start+count-1 of the light at the scan level and everything else off; must lie inside the scanned range. Same
  ownership, idle timeout and restore rules as `scan/frame` (frame 0 is still the dark reference).

## [2.3.0] - 2026-11-06

### Added
- Areas (backend): every run belongs to an area, a Home Assistant area or a name of your own, defaulting to the
  light's device area, else the light's name. Each area is its own 3D space. A zone can list areas as well as runs.
- 3D positions: runs keep a position per pixel once mapped (compact, 0-1000 per axis), otherwise they sit along a 3D
  polyline. Twinkly layouts are imported per pixel (z too when the strip reports one). Zones "by position" take any
  3D direction (presets: left to right, right to left, rise, fall, front to back, back to front) or sweep out from a
  point, worked out within each area.
- Camera scan (backend): `light_show_studio/map/scan/start`, `scan/frame` and `scan/stop` (admin only) make a light
  flash a binary code (2 + 2 x ceil(log2 N) frames) at a fixed white so the phone can read where each pixel is. The
  scan takes the light like a zone and gives it back, restoring what it showed, on stop, after 120 s without a
  frame, or when the connection closes.
- `light_show_studio.start_zone` and `stop_zone` take an `area` instead of a zone; `map/zone/start` takes `area` too.
- The light has a `scanning` attribute.

### Changed
- A saved map from 2.2.0 is migrated to the new format on load (y now points up, runs go in one area called House, the
  old file is kept as `light_show_studio.map.v1`). Zones behave as before.
- Zones keep the same fixed frame beat as effects, and a long stall no longer makes them jump ahead.

## [2.2.0] - 2026-11-06

### Added
- Zones (backend; the Map tab is the panel side): a zone shows one pattern, effect and speed across several runs of pixels
  (parts of lights, or whole lights) as one thing, in sync. Along the string they run in the order you list them, with a
  per-run reverse, and a chase carries on through the joins. By position they follow where the pixels are: a sweep across
  the house, rising up a tree, or bursting from a point. Runs and zones are saved in `.storage/light_show_studio.map`;
  until you save one, every light is one run (a WLED light with 904 LEDs gets the four kitchen runs).
- `light_show_studio.start_zone` and `light_show_studio.stop_zone` actions, and `light_show_studio/map/*` websocket
  commands (changes are admin only). The light gets a `zone` attribute while a zone has it.
- A zone keeps its lights until you pick a colour or effect on one, turn it off, or a show starts; brightness changes
  keep it.

## [2.1.0] - 2026-11-06

### Added
- Patterns: pick a pattern (Classic Multicolor, Candy Cane, Rainbow, ...), then how it moves
  (Steady, Twinkle, Chase, Twinkle Swap, Marquee, Fade, Sparkle, Alternate, Wave), then a speed
  (1-100, 50 = normal) that also speeds up or slows down every other animated effect. New "Pattern"
  light effect and the `light_show_studio.set_pattern` action; the choice is restored after a restart.
  Schedules can end on the Pattern effect.

### Fixed
- Speed 1 now runs at exactly 0.25x (it was 0.257x). Twinkle is gentler and each bulb swells at its own
  pace; Chase keeps each bulb its own colour instead of smearing; Sparkle glints fade instead of flickering.

### Security
- DDP (UDP 4048) and E1.31 (UDP 5568) accept packets only from private / link-local IPv4 senders, so
  nothing arriving over Tailscale or the internet is processed. New player option "allowed senders"
  restricts them further to listed IPs / networks. Senders are rate limited, packets are parsed
  strictly (bad headers, lengths and offsets are dropped), pushes are coalesced, and multicast is
  joined on the LAN interface only. The sockets exist only while a light is set up.
- Light addresses (manual entry, strand update, discovery replies, stored entries) must be private or
  link-local IPv4 addresses; public, loopback, multicast and unspecified addresses are refused. HTTP
  calls to Twinkly and WLED no longer follow redirects and cap the size of replies.
- Upload: admin only, size limits per file / request / field enforced while streaming, name checks,
  stale temp files swept. The show library never follows symbolic links, and `play_sequence` only
  plays real `.fseq` files inside the media / www folders or an allowed external folder.
- Websocket commands: ids, names and lists are length limited; setting bulbs checks the bulb count;
  light addresses are shown to admins only. Playlists and schedules are capped and validated on load.
- `.fseq` files: frame, channel and block-size limits and capped decompression (zstd / zlib bombs).
- `set_pixels` accepts at most 1020 colours and rejects malformed ones cleanly.
- Dashboard cards build show rows from DOM nodes instead of markup.
- `zstandard` requirement raised to `>=0.22,<1` (bundles zstd 1.5.5 or newer).

## [2.0.0] - 2026-11-01

### Changed
- The Light Shows app now has five tabs: Play, Library, Schedule, Lights and Studio. Shows
  are added in a collapsible section of the Library; the Add shows and Setup tabs are gone, replaced by a
  dismissible first-run banner. The Lights tab is just the strands list (live views are in Studio).
- The player's options keep only the light delay and the pause between shows; speakers and the
  between-shows look are set in the app. An old single `speaker` option is migrated to `speakers`.
- One update rule for config entries: only changes that were applied in place (speakers, transition,
  a light's device id) skip the reload.
- `play_sequence` shares one sequence player and enables every light type (Govee, Twinkly, WLED).
- Wording no longer assumes Govee lights; removed the stale build specs from docs.

### Added
- Playlists: named, ordered lists of shows, built in Library -> Playlists (create, rename, delete, add,
  remove, reorder with up/down). The Play tab has a selector (All shows or a playlist) above the queue.
  Deleting a show removes it from playlists. Websocket: `playlists/list`, `playlists/save`, `playlists/delete`,
  `player/set_playlist`; the media player has `playlist` and `playlist_id` attributes.
- Scheduler (Schedule tab): run a playlist on chosen days from a time, sunset or sunrise (with offsets) to an
  end or once, within an optional season that may cross New Year, with speakers, volume, shuffle, repeat and
  an end action (lights off, an effect, or leave). Windows can cross midnight; a window that is open at
  startup starts then; the later-starting of two overlapping windows wins; stopping a scheduled show pauses
  it until the window ends (Resume in the app); Skip today. Adds a switch per schedule,
  `sensor.light_show_next_show`, `sensor.light_show_schedule_status`, and the events
  `light_show_studio_schedule_started` / `_ended`. Websocket: `schedules/list`, `save`, `delete`, `toggle`,
  `skip_today`, `resume` and `schedule/subscribe`. Playlists and schedules are stored in
  `.storage/light_show_studio.schedule`.
- 25 new effects: Vintage Pastel, Retro Christmas, Icy Blue & White, Gold & White, Red/Green/White, Rainbow Bands,
  Ombre, Snowy (still); Rainbow Twinkle, Vintage Twinkle, Christmas Twinkle, Sparkle, Starfield, Snowfall, Icicle Drip,
  Candle Flicker, Aurora, Lava, Red & Blue, Color Wipe, Larson Scanner, Rainbow Breathe, Peppermint Swirl, Heartbeat,
  Meteor Shower (animated). They scale with the bulb count and none flash faster than about 1 Hz.
- Studio tab in the Light Shows app: one light at a time, with a big live view, power, brightness, colour,
  effect chips ("Follow the show" first), a show-mode line with a hand-back button, Identify, and admin
  settings (name, IP address, start pixel, bulb count) with live pixel and channel ranges. Tap a strand
  or a light card's title in the Lights tab to open it; `/light-shows?studio=<entity or entry id>` deep-links.
- `strand/update` accepts `host` (IPv4); an address used by another light is rejected (`already_configured`).
- `size` option on the light card (bulb size multiplier).
- Audio output: play the music on one or several speakers (grouped in sync, the first leads and the lights
  follow it). Set in the player options or with the new Output picker (with volume) on the player card.
- Transitions between shows: a "Between shows" setting on the Light Show Player (None, Fade,
  Crossfade or Effect, with a choice of intermission effect), also on the player card.
- WLED lights: realtime frames over DDP; add them via Add entry → Add a WLED light.
- Twinkly lights: local realtime control. The card layout uses the Twinkly app's camera mapping.
- Strands list in the Light Shows app's Lights tab: IP, model, pixel and channel ranges, online
  status, Identify, Edit, overlap warnings, and a copyable xLights setup.
- Online and offline detection: lights go unavailable when they stop answering.
- Automatic follow-up when a light's IP address changes.
- IP, pixel and channel attributes on lights.
- MAC address on the Twinkly device page.

### Changed
- **Breaking:** renamed to Light Show Studio (domain light_show_studio). Remove the old Govee Light Show entries and add the lights again.
- Pixel space up to 2040 pixels (12 E1.31 universes).

### Fixed
- Lights that went offline no longer show as on.

## [1.0.0] - 2026-11-01 (unreleased)

### Added
- "Light Shows" sidebar app with Play, Lights, Add shows and Setup tabs, plus a first-run checklist.
- Friendlier wording on the cards.

### Fixed
- Player options can be saved.
- Lights can be turned off during a show.
- A plain turn-on works after a restart.
- Next and Previous stop the old song on the speaker.
- Uploads and deletes during a playlist no longer stop it.
- Damaged show files are logged.
- Stop sequence also stops the player.
- Non-C9 lights default to one colour, so they no longer get 50 bulb entities.
- Light state is restored after a restart.
- E1.31 works without multicast.
- Uploads from non-admins get 403.
- Uploads keep the old show file until the new one is in place.

## [0.9.0]

### Changed
- **Breaking:** domain renamed from `govee_c9` to `govee_light_show`. Remove and re-add the integration after updating.
- Cards are now `custom:govee-light-show-card` and `custom:govee-light-show-player-card`.
- Actions are `govee_light_show.*`; the WebSocket and HTTP APIs moved to `/govee_light_show`.

## [0.8.0]

### Added
- Light Show Player: plays a playlist of .fseq shows from /media/xlights through any Home Assistant speaker, with a dashboard card.
- Any Govee LAN light works. Lights without per-segment control show the frame's blended colour.
- Setup scans the LAN for Govee lights and fills in known models.

## [0.7.0]

### Added
- Seasonal effects: Valentine's, St. Patrick's, Easter, July 4th, Thanksgiving, Hanukkah, Valentine's Chase, July 4th Chase and Fireworks.
- Colour palette and a Patterns menu of holiday fills on the card, both configurable.

## [0.6.x]

### Added
- Dashboard card with a live view of every bulb; tap or drag to paint.
- Bulbs scale with card width, so the card fits phones.

## [0.5.0]

### Added
- Light show effect to choose between shows and effects.

### Fixed
- Brightness slider applies to shows, and dim shows are fixed.
- Picking a colour or effect stops sequencer frames from taking over the string.

## [0.4.0]

### Added
- Play xLights .fseq sequences synced to a media player (play_sequence / stop_sequence).
- Several strings on one xLights controller.

## [0.2.0]

### Added
- First release as Govee C9 Pixels: per-bulb control, effects, xLights/E1.31 receiver.
