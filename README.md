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
   - **Add the Light Show Player**: pick the speakers for the music (optional).
3. **Add a show**: open **Light Shows** in the sidebar → **Library** → **Add shows** → drop in your `.fseq`
   and its song → **Play**. A banner guides you until everything is ready.

## Light Shows app

Open **Light Shows** in the sidebar. A banner at the top lists anything still missing (lights,
the Light Show Player, shows) with a button for each; dismiss it once you're set. It has five tabs:

- **Play**: what's playing now, play/pause, next/previous, seek, shuffle and repeat, the audio
  output, the between-shows look and the queue. Shows play with their song, or pick **No music**
  to run the lights alone. **Light delay** (player options) shifts the lights if the speaker lags.
- **Library**: your shows, with play, reorder and delete, and a collapsible **Add shows** section to
  upload the `.fseq` and its song, and your **Playlists** (see below).
- **Schedule**: run a playlist automatically (see **Schedules** below).
- **Lights**: the strands list, one row per light: status (online, offline, or checking), name,
  model, IP address, and the pixels and channels it uses. **Identify** flashes the light so you can
  find it. Two strands with overlapping pixels show a warning. Tap a row to open it in Studio.
  **Add a light** is in the header. **Copy** gives the setup text for xLights / EZPlayer.
- **Studio**: pick one light for a large live view, power, brightness, colour and effect controls
  (holiday patterns, **Follow the show**), a status line showing whether it follows the show, and
  (for admins) its name, IP address, start pixel and bulb count with the pixel and xLights channel
  range shown as you type, plus **Remove light**. Link straight to a light with
  `/light-shows?studio=<entity_id>`.

### Audio output

Pick one or several speakers with the **Output** button on the Play tab (it also sets each
speaker's volume). The first speaker leads and the lights follow its position. When you pick more,
the others are grouped with the leader before a show and ungrouped when the queue ends or you
stop; this works for speakers of the same kind that support grouping (e.g. Sonos with Sonos, Music
Assistant with Music Assistant). Others can't play in sync, so they are skipped and marked in the
picker. If the leader is unavailable, the next speaker leads. Changes made during a show apply from
the next show. No speakers means lights only. The player's options hold only the light delay and
the pause between shows.

### Playlists

A playlist is a named, ordered list of shows, such as "Halloween" or "Christmas". In **Library →
Playlists** (admins) create one, rename it, add shows, and move them up or down or remove them with the
buttons. Deleting a show from the library takes it out of every playlist; a playlist that a schedule uses
can't be deleted until you change the schedule.

On the **Play** tab, the selector above the show list switches between **All shows** (the whole library,
as before) and a playlist. With a playlist chosen the queue is just its shows, in its order (shuffle still
applies); switching while a show plays stops it unless that show is in the new playlist. The player's
`playlist` attribute holds the name (`playlist_id` the id); `source_list` stays the shows.

### Schedules

The **Schedule** tab plays a playlist for you. Each schedule has:

- **Days** of the week it may start on, and a **start**: a clock time, **sunset** or **sunrise** (with an
  offset in minutes, e.g. sunset -15 min).
- An **end** (same choices) or **Play once**, which plays the playlist through a single time. A window may
  cross midnight (6 PM to 1 AM). Clock times are local, so they follow daylight saving changes.
- An optional **season** by month and day, repeating every year and allowed to cross New Year (Nov 25 to Jan 6).
- The **playlist**, optional **speakers** (none = the player's current output) and **volume**, **shuffle**
  and **repeat** (the playlist repeats while the window is open).
- **When it ends**: lights off, an effect on every light, or leave as is.

At the start the scheduler sets the playlist, speakers, volume, shuffle and repeat, and presses play. At
the end it stops the player, gives back the speakers it changed (if you haven't changed them since), and
does the end action. After a restart, a window that is open right now starts from the beginning of the
playlist. If two windows overlap, the one that started later takes over, and the app warns you.

If you press stop during a scheduled show, that window is **paused**: it stays off until the window ends
(or until you press **Resume** on the Schedule tab), and no end action runs. **Skip today** skips (or stops)
today's window. Each schedule has an on/off switch in the app and as an entity.

The banner at the top shows the next start ("Next: Fri 6:00 PM · Christmas (until 10:00 PM)") and
whether a schedule is running, paused or idle.

Entities (on the Light Show Player device):

| Entity | What it is |
| --- | --- |
| `switch.light_show_schedule_<name>` | One per schedule: on = enabled. Created and removed as you add and delete schedules. |
| `sensor.light_show_next_show` | When the next show starts (timestamp), with `schedule`, `playlist` and `ends` attributes. |
| `sensor.light_show_schedule_status` | `idle`, `running`, `paused` or `off` (no schedule is turned on). |

Events: `light_show_studio_schedule_started` and `light_show_studio_schedule_ended` (data: `schedule_id`,
`name`, and for started the `playlist`, `start`, `end`; for ended the `reason`: `window_end`, `preempted`,
`skipped`, `disabled`, `deleted`, `edited`, `finished` or `stopped`).

## Patterns

Pick **what colours** the string shows, then **how it moves**, then **how fast**, in the Light Shows
panel (choose the "Pattern" effect on the light, then a pattern, an effect and the speed slider).

- **Pattern**: Classic Multicolor (the old ceramic-bulb order), Warm White, Cool White, Red & Green,
  Candy Cane, Blue & White, Pastel, Halloween, Rainbow and more.
- **Effect**: Steady, Twinkle (every bulb swells and fades on its own, like old incandescent bulbs),
  Chase (the pattern glides along the string), Twinkle Swap, Marquee, Fade, Sparkle, Alternate, Wave.
- **Speed**: 1-100, 50 is normal (0.25x to 4x). It applies to every animated effect, not only patterns.

Patterns also work from automations with the `light_show_studio.set_pattern` action. All fields are
optional; sending only `speed` changes the speed of whatever is running.

```yaml
action: light_show_studio.set_pattern
target:
  entity_id: light.c9_string
data:
  pattern: Classic Multicolor
  effect: Twinkle
  speed: 40
```

## Zones and the map

A **run** is a stretch of pixels on one light (all of it, or part: the toe-kick of a kitchen strip),
placed on a plain 0-1 map. A **zone** is an ordered list of runs that shows one pattern, effect and
speed as one thing, in sync, across several lights. Set them up in the Light Shows app's **Map** tab.
Per-light effects stay the default; a zone is opt-in, and xLights shows are unaffected.

- **Along the string**: the runs are joined in the order you list them (each run can be reversed), and the
  pattern runs through the joins as if they were one long string.
- **By position**: the pattern follows where the pixels are: a sweep across the house at an angle
  (0 = left to right, 90 = bottom to top), or out from a centre point.
- A zone keeps its lights until you pick a colour or effect on one of them, turn it off, or a show
  starts. Changing the brightness keeps the zone. Stopping a zone puts each light back as it was.
- Until you save a map, each light is one run. A WLED light with exactly 904 LEDs gets the kitchen
  runs (toe-kick 0-278, island 279-423, counter 424-615, above cabinets 616-903). Twinkly lights take their
  positions from the layout the strip reports.
- Lights without per-segment control (some Govee models) show a zone as one average colour.

Automations can start and stop a zone by name or id. Pattern, effect and speed are optional and are kept
as the zone's own.

```yaml
action: light_show_studio.start_zone
data:
  zone: Kitchen
  pattern: Candy Cane
  effect: Chase
  speed: 60
```

```yaml
action: light_show_studio.stop_zone
data:
  zone: Kitchen
```

The map is stored in `.storage/light_show_studio.map`.

## Twinkly lights

Twinkly strings are added the same way as Govee lights, and show live in the Studio tab.

1. Go to Settings → Devices & services → Light Show Studio → **Add entry** → **Add a Twinkly light**.
2. Pick the light from the list, or type its IP address.

- The light must be on the same network as Home Assistant.
- The card layout comes from the mapping in the Twinkly app, so map the light there first.
- While a show or colour from Home Assistant is active, the Twinkly app can't drive the light.
  Turn the light off in Home Assistant to give it back.

## WLED lights

WLED strips are added the same way, and show live in the Studio tab. Frames go out over DDP (UDP 4048).

1. Go to Settings → Devices & services → Light Show Studio → **Add entry** → **Add a WLED light**.
2. Pick the light from the list of WLED devices Home Assistant already knows, or type its IP address.

- WLED returns to its own state about 2.5 seconds after Home Assistant stops sending frames.
- Leave the built-in WLED integration installed for WLED's own controls (effects, presets, settings).

## Adding shows

1. In xLights, save/render your sequence; the `.fseq` is next to the `.xsq` in your show folder.
2. Upload the `.fseq` and the song in **Add shows** (same name, e.g. `My Show.fseq` +
   `My Show.m4a`). Files go to `/media/xlights/`; a folder per show (`/media/xlights/My Show/`)
   works too if you copy files in yourself.
3. Press Play. Reorder with the arrows; shuffle and repeat are in the app or the media player.

Automations: `media_player.media_play` on `media_player.light_show_player` starts the playlist,
`media_player.select_source` plays one show, `media_player.media_stop` stops.

### Transitions

**Between shows** (player options, or the select on the Light Show Player card) sets what the
lights do during the pause between shows, whose length is the **Pause between shows** option:
*None* holds the last frame, *Fade* (the default) fades the finished show to black and fades the
next one in over 1 s, *Crossfade* blends the last frame into the next show's first frame, and
*Effect* runs an intermission effect (**Intermission effect**, Twinkle by default) on every light
and then fades the next show in. Pressing stop, next, previous or a show cuts a transition short.
Changing the setting from the card doesn't interrupt the show that is playing.

## Security

Light Show Studio opens **two UDP ports on Home Assistant, and only while at least one light is set up**:

| Port | Protocol | Purpose |
|------|----------|---------|
| 4048 | DDP | pixel data from xLights and similar |
| 5568 | E1.31 / sACN (unicast, plus multicast groups 239.255.0.1-12 on your LAN interface) | pixel data from xLights and similar |

Both are bound on all interfaces, so every packet's **source address is checked**: only private / link-local
IPv4 senders (10/8, 172.16/12, 192.168/16, 169.254/16) are processed. Packets arriving over Tailscale (100.x), from the
internet or from any other address are dropped. Senders are also rate limited and every packet is parsed strictly.

To accept data from your xLights PC only, open **Settings -> Devices & services -> Light Show Studio -> Light Show Player
-> Configure** and put its address in *Only accept sequencer data from these IPs* (comma separated; networks such as
`192.168.4.0/24` work too). Empty means any device on your home network. For defence in depth you can also block
UDP 4048 and 5568 on your firewall for every interface except the LAN.

Lights are only ever contacted at private / link-local IPv4 addresses; changing a light's address, uploading shows and
every configuration change need an administrator account. Uploads accept only `.fseq` and common audio files.

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
  patterns…**, **off** and **Let the show control it** work as in the Studio tab.
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
`media-source://media_source/local/xlights/My Show/My Show.m4a`. If a player never reports a position,
the lights just start when it reports *playing*. Use `audio_offset` only to fine-tune: if the
lights are ahead of what you hear, try `0.1`–`0.3`.

`light_show_studio.stop_sequence` stops the show and turns the bulbs dark.

```yaml
automation:
  - alias: My Show
    triggers:
      - trigger: time
        at: "19:00:00"
    conditions:
      - condition: template
        value_template: "{{ now().month == 10 and now().day == 31 }}"
    actions:
      - action: light_show_studio.play_sequence
        data:
          file: My Show.fseq
          media_player: media_player.porch_sonos
          audio: library://track/123  # the song in Music Assistant
          audio_offset: 0.15
```
