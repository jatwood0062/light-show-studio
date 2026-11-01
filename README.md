# Govee Light Show

Local light shows for Govee lights in Home Assistant: play xLights / Light-O-Rama / Vixen / FPP
`.fseq` shows to music, paint single bulbs, and run effects for every season. Built on Govee
C9 string lights (H6860, which reports as R6861) and works with any Govee light that has
**LAN Control**.

- **Light Show Player**: an EZPlayer-style player inside Home Assistant. Drag `.fseq` files and
  their songs onto the player card, press Play. Playlist, shuffle, repeat, next/previous, seek,
  with the music on any Home Assistant speaker (Sonos, Music Assistant, …) and the lights kept in
  sync with it. It's a normal media player, so automations and voice work too.
- **Any Govee LAN light**: lights that take per-segment frames (DreamView / "razer" mode, e.g.
  C9 strings, RGBIC strips) show every pixel; others get the show's blended colour.
  See [docs/DEVICES.md](docs/DEVICES.md).
- **Live from a sequencer**: Home Assistant looks like a pixel controller (DDP and E1.31), so
  xLights, EZPlayer, Light-O-Rama, Falcon Player or Vixen can drive the lights live.
- Effects for every season, a dashboard card that shows and paints every bulb live, one light
  per bulb, and a `govee_c9.set_pixels` action.

Everything stays on your LAN (Govee LAN API, UDP 4003). In the Govee Home app, turn on
**LAN Control** for each light first.

## Install (HACS)

1. HACS → ⋮ → **Custom repositories** → add this repository's URL, type **Integration**.
2. Download **Govee Light Show**, then restart Home Assistant.
3. Settings → Devices & services → **Add integration** → Govee Light Show:
   - **Add a Govee light**: lights on your network are listed (or type the IP). Repeat per light.
   - **Add the Light Show Player**: pick the speaker for the music (optional).

## Light Show Player

1. In xLights, save/render your sequence; the `.fseq` is next to the `.xsq` in your show folder.
2. Add the **Govee light show player** card to a dashboard and drop the `.fseq` and the song
   onto it (same name, e.g. `Thriller.fseq` + `Thriller.m4a`). Files go to `/media/xlights/`;
   a folder per show (`/media/xlights/Thriller/`) works too if you copy files in yourself.
3. Press Play. Reorder with the arrows, shuffle/repeat from the card or the media player.

Automations: `media_player.media_play` on `media_player.light_show_player` starts the playlist,
`media_player.select_source` plays one show, `media_player.media_stop` stops.
**Light delay** (player options) shifts the lights if the speaker lags.

## Live from xLights / Light-O-Rama

Home Assistant listens for pixel data on **DDP (UDP 4048)** and **E1.31/sACN (UDP 5568,
510 channels per universe from universe 1)**, and looks like one pixel controller to xLights.
Each string takes its own slice, set by **First pixel** (Configure on the integration):
the first string is pixels 1–50, a second one defaults to 51–100, and so on.

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

## Dashboard card

The integration ships a **Govee C9 string** card (no extra install: it appears in the dashboard's
card picker after a restart). It shows every bulb live, including during effects and shows, and
you can pick a colour and tap or drag across bulbs to paint them. **Fill all** paints the whole
string, **Patterns** fills it with a holiday pattern (Christmas, Valentine's, St. Patrick's, Easter,
July 4th, Halloween, Thanksgiving, Hanukkah, …), **off** turns bulbs off, and **Light show** hands
the string back to the sequencer.

```yaml
type: custom:govee-c9-card
entity: light.govee_c9
title: Porch          # optional
columns: 25           # optional: rows of this many bulbs
layout:               # optional: x, y of each bulb in order (y grows downward), e.g. your roofline
  - [0, 40]
  - [0, 30]
  # ...
colors: ["#ff0000", "#00ff00"]          # optional: your own paint swatches
patterns:                               # optional: your own Patterns list
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
3. Call **`govee_c9.play_sequence`**. The channels are read the same way as from xLights over
   the network, so a string with First pixel 51 plays channels 151–300 of the file.

| Field | |
|---|---|
| `file` | `.fseq` file; a bare name is looked up in `/media/xlights`, then `/config/www/xlights` |
| `loop` | start again when it ends (default off; with a speaker, its repeat setting decides) |
| `media_player`, `audio` | speaker and song to play with the lights; `audio` is any media content ID the player accepts |
| `audio_offset` | seconds to shift the lights later (positive) or earlier (negative); default 0 |

### Keeping in time with the music

With `media_player` and `audio`, the action starts the song with `media_player.play_media`, waits
(up to 15 s) for the player to report *playing*, then runs the lights from the player's own
position, not a fixed delay, so slow starts (Music Assistant and Sonos often take 1–3 s) don't
matter. Whenever the player updates its position the lights re-sync: small drift (over 80 ms)
is eased out by running the lights up to 25% fast or slow, a jump over 1 s (a seek, or repeat)
is followed straight away. The lights pause with the player, and stop when it stops, goes idle
or moves on to a different song.

`audio` works with Music Assistant players: use a library or provider URI such as
`library://track/123` or `spotify://track/…`, a URL, or a `media-source://` ID such as
`media-source://media_source/local/xlights/halloween.mp3`. If a player never reports a position, the lights
just start when it reports *playing*. Use `audio_offset` only to fine-tune: if the lights are
ahead of what you hear, try `0.1`–`0.3`.

`govee_c9.stop_sequence` stops the show and turns the bulbs dark.

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
      - action: govee_c9.play_sequence
        data:
          file: halloween.fseq
          media_player: media_player.porch_sonos
          audio: library://track/123  # the song in Music Assistant
          audio_offset: 0.15
```
