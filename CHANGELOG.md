# Changelog

Changes to Light Show Studio, newest first.

## [2.0.0] - 2026-11-01

### Changed
- The Light Shows app now has five tabs: Play, Library, Schedule (placeholder), Lights and Studio. Shows
  are added in a collapsible section of the Library; the Add shows and Setup tabs are gone, replaced by a
  dismissible first-run banner. The Lights tab is just the strands list (live views are in Studio).
- The player's options keep only the light delay and the pause between shows; speakers and the
  between-shows look are set in the app. An old single `speaker` option is migrated to `speakers`.
- One update rule for config entries: only changes that were applied in place (speakers, transition,
  a light's device id) skip the reload.
- `play_sequence` shares one sequence player and enables every light type (Govee, Twinkly, WLED).
- Wording no longer assumes Govee lights; removed the stale build specs from docs.

### Added
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
