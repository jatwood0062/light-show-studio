# Changelog

Changes to Light Show Studio, newest first.

## [Unreleased]

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
