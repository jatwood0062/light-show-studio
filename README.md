# Light Show Studio

Local light shows for Govee, Twinkly and WLED lights in Home Assistant: play xLights / Light-O-Rama / Vixen / FPP
`.fseq` shows to music, paint single bulbs, and run effects for every season. Built on Govee
C9 string lights (H6860, which reports as R6861) and works with any Govee light that has
**LAN Control**. Twinkly strings are supported too (see [Twinkly lights](#twinkly-lights)).

- **Light Shows app**: a page in the Home Assistant sidebar to play shows with their music, paint
  bulbs, and add shows. No dashboard setup needed.
- **Any Govee LAN light**: lights that take per-segment frames (DreamView / "razer" mode, e.g.
  C9 strings, RGBIC strips) show every pixel; others get the show's blended colour.
  See [docs/DEVICES.md](docs/DEVICES.md).
- **Live from a sequencer**: Home Assistant looks like a pixel controller (DDP and E1.31), so
  xLights, EZPlayer, Light-O-Rama, Falcon Player or Vixen can drive the lights live.
- Effects for every season, one light per bulb, and a `light_show_studio.set_pixels` action.

Everything stays on your LAN (Govee LAN API on UDP 4003, and Twinkly's local API).

## Quick start

1. **Install**: in HACS → ⋮ → **Custom repositories**, add this repository's URL as type
   **Integration**. Download **Light Show Studio**, then restart Home Assistant.
2. **Add your lights**: in the Govee Home app, turn on **LAN Control** for each light first.
   Then go to Settings → Devices & services → **Add integration** → Light Show Studio and choose:
   - **Add a Govee light**: lights on your network are listed (or type the IP). Repeat per light.
   - **Add a Twinkly light**: see [Twinkly lights](#twinkly-lights).
   - **Add the Light Show Player**: pick the speaker for the music (optional).
3. **Add a show**: open **Light Shows** in the sidebar → **Add shows** → drop in your `.fseq`
   and its song → **Play**. New users land on **Setup** until everything is ready.

## Light Shows app

Open **Light Shows** in the sidebar. It has four tabs:

- **Play**: what's playing now, play/pause, next/previous, seek, shuffle and repeat, and the
  show list. Shows play with their song, or pick **No music** to run the lights alone.
  **Light delay** (player options) shifts the lights if the speaker lags.
- **Lights**: a live view of each string, updated during effects and shows. Pick a colour and
  tap or drag across bulbs to paint them. **Paint all** paints the whole string,
  **Holiday patterns…** fills it (Christmas, Valentine's, St. Patrick's, Easter, July 4th,
  Halloween, Thanksgiving, Hanukkah, …), **off** turns bulbs off, and **Let the show control it**
  hands the string back to the sequencer.
- **Add shows**: a guide, plus upload for the `.fseq` and the song.
- **Setup**: a checklist (lights added, Light Show Player set up, shows added) with a button
  for each step.

**Strands** are listed at the top of the Lights tab, one row per light. Each row shows its
status (online, offline, or checking), name, model, IP address, and the pixels and channels it
uses. **Identify** flashes the light so you can find it. Admins can **Edit** the name, start
pixel or bulb count. Two strands with overlapping pixels show a warning. **Copy** in the footer
gives the setup text for xLights.

## Twinkly lights

Twinkly strings are added the same way as Govee lights, and show live in the Lights tab.

1. Go to Settings → Devices & services → Light Show Studio → **Add entry** → **Add a Twinkly light**.
2. Pick the light from the list, or type its IP address.

- The light must be on the same network as Home Assistant.
- The card layout comes from the mapping in the Twinkly app, so map the light there first.
- While a show or colour from Home Assistant is active, the Twinkly app can't drive the light.
  Turn the light off in Home Assistant to give it back.

## WLED lights

WLED strips are added the same way, and show live in the Lights tab. Frames go out over DDP (UDP 4048).

1. Go to Settings → Devices & services → Light Show Studio → **Add entry** → **Add a WLED light**.
2. Pick the light from the list of WLED devices Home Assistant already knows, or type its IP address.

- WLED returns to its own state about 2.5 seconds after Home Assistant stops sending frames.
- Leave the built-in WLED integration installed for WLED's own controls (effects, presets, settings).

## Adding shows

1. In xLights, save/render your sequence; the `.fseq` is next to the `.xsq` in your show folder.
2. Upload the `.fseq` and the song in **Add shows** (same name, e.g. `Thriller.fseq` +
   `Thriller.m4a`). Files go to `/media/xlights/`; a folder per show (`/media/xlights/Thriller/`)
   works too if you copy files in yourself.
3. Press Play. Reorder with the arrows; shuffle and repeat are in the app or the media player.

Automations: `media_player.media_play` on `media_player.light_show_player` starts the playlist,
`media_player.select_source` plays one show, `media_player.media_stop` stops.

## Live from xLights / Light-O-Rama

Home Assistant listens for pixel data on **DDP (UDP 4048)** and **E1.31/sACN (UDP 5568,
510 channels per universe from universe 1)**, and looks like one pixel controller to xLights.
Each string takes its own slice, set by **First pixel** (Configure on the integration):
the first string is pixels 1–50, a second one defaults to 51–100, and so on. Up to 2040 pixels
in total (12 E1.31 universes).

1. In xLights → **Controllers** → **Add Ethernet**:
   - Protocol **DDP**, IP address = your Home Assistant IP
   - Channels **150 per string** (e.g. 300 for two 50-bulb strings)
2. **Layout**: add a **Single Line** model with 50 nodes per string. Start the first one at
   channel 1 of that controller, the second at channel 151, and so on.
3. Sequence a song (File → New Sequence → Musical, pick the audio file; the sequencer
   can mark beats for you). Turn on **Output to Lights** and press Play.

Light-O-Rama S6: add E1.31 universe 1 (510 channels covers three strings), unicast to the Home Assistant IP.

**Show or effects:** the string's effect list starts with **Light show**. In that mode it
follows xLights / EZPlayer / `play_sequence`. Pick a colour or any other effect and it ignores
the sequencer until you pick **Light show** again (`play_sequence` switches it back by itself).
The brightness slider dims shows too, and the string's own dimmer is set to 100 % so a low
setting left in the Govee app can't dim them.

## Dashboard cards (optional)

You don't need these: the Light Shows app does everything they do. They're for anyone who wants
the controls on a dashboard, and they appear in the card picker after a restart.

- **Govee C9 string** (`custom:light-show-studio-card`): shows every bulb live, including during
  effects and shows. Pick a colour and tap or drag to paint bulbs. **Paint all**, **Holiday
  patterns…**, **off** and **Let the show control it** work as in the Lights tab.
- **Light Show Player** (`custom:light-show-studio-player-card`): the Play tab on a dashboard.
  Drop the `.fseq` and the song onto it, and press Play.

```yaml
type: custom:light-show-studio-card
entity: light.light_show_studio
title: Porch          # optional
columns: 25           # optional: rows of this many bulbs
layout:               # optional: x, y of each bulb in order (y grows downward), e.g. your roofline
  - [0, 40]
  - [0, 30]
  # ...
colors: ["#ff0000", "#00ff00"]          # optional: your own paint swatches
patterns:                               # optional: your own Holiday patterns list
  Team colours: ["#002244", "#69be28"]  # colours repeat along the string
```

## Playing a sequence without xLights

Once a show is sequenced, Home Assistant can play it on its own, so xLights doesn't need to
be running.

1. **Export**: in xLights, save the sequence (or **Render and Save**). xLights writes a
   `.fseq` file next to the sequence in your show folder. Uncompressed and zstd/zlib
   compressed files both work; if one won't load, set Preferences → Output → FSEQ
   compression to None and save again.
2. **Copy** the `.fseq` (and the song, if you want music) into `/media/xlights/` on Home
   Assistant, e.g. with the Samba share or File editor app. `/config/www/xlights/` works
   too.
3. Call **`light_show_studio.play_sequence`**. The channels are read the same way as from xLights over
   the network, so a string with First pixel 51 plays channels 151–300 of the file.

| Field | |
|---|---|
| `file` | `.fseq` file; a bare name is looked up in `/media/xlights`, then `/config/www/xlights` |
| `loop` | start again when it ends (default off; with a speaker, its repeat setting decides) |
| `media_player`, `audio` | speaker and song to play with the lights; `audio` is any media content ID the player accepts |
| `audio_offset` | seconds to shift the lights later (positive) or earlier (negative); default 0 |

### Keeping in time with the music

With `media_player` and `audio`, the action starts the song with `media_player.play_media`, waits
up to 15 s for the player to report *playing*, then runs the lights from the player's own
position, not a fixed delay, so slow starts (Music Assistant and Sonos often take 1–3 s) don't
matter. Each position update re-syncs the lights: small drift (over 80 ms) is eased out by
running the lights up to 25% fast or slow; a jump over 1 s (a seek, or repeat) is followed
straight away. The lights pause with the player, and stop when it stops, goes idle or moves on
to a different song.

`audio` works with Music Assistant players: a library or provider URI such as
`library://track/123` or `spotify://track/…`, a URL, or a `media-source://` ID such as
`media-source://media_source/local/xlights/halloween.mp3`. If a player never reports a position,
the lights just start when it reports *playing*. Use `audio_offset` only to fine-tune: if the
lights are ahead of what you hear, try `0.1`–`0.3`.

`light_show_studio.stop_sequence` stops the show and turns the bulbs dark.

```yaml
automation:
  - alias: Halloween show
    triggers:
      - trigger: time
        at: "19:00:00"
    conditions:
      - condition: template
        value_template: "{{ now().month == 10 and now().day == 31 }}"
    actions:
      - action: light_show_studio.play_sequence
        data:
          file: halloween.fseq
          media_player: media_player.porch_sonos
          audio: library://track/123  # the song in Music Assistant
          audio_offset: 0.15
```
