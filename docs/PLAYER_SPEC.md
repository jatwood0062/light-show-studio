# Build spec: Light Show Player + any-Govee support

Repo: `govee-c9/` (Home Assistant custom integration, domain `govee_light_show`, HA 2026.10).
Integration code: `custom_components/govee_light_show/`. Read `README.md`, `playback.py`, `follow.py`,
`fseq.py`, `__init__.py` and `websocket.py` before starting.

Goal: an EZPlayer-like experience inside Home Assistant. Users export `.fseq` shows from xLights
(Light-O-Rama, Vixen, Falcon Player… anything that exports FSEQ v1/v2), drop them plus the song onto
a dashboard card, and press Play. Works with any Govee light the integration controls, not just C9.

## Ownership (do NOT edit files you don't own; other agents work in parallel)

| Owner | Files |
|---|---|
| Player backend agent | NEW `library.py`, `showplayer.py`, `media_player.py`, `upload.py`, `ws_shows.py`, `tests/test_library.py` |
| Player card agent | NEW `frontend/govee-light-show-player-card.js` |
| Device research agent | NEW `models.py`, `docs/DEVICES.md` |
| Parent (lead) | everything else: `__init__.py`, `config_flow.py`, `controller.py`, `protocol.py`, `light.py`, `websocket.py`, `const.py`, `manifest.json`, `strings.json`, `translations/`, `README.md`, `frontend/govee-light-show-card.js` |

Don't commit or push; the lead integrates and commits. Don't touch Home Assistant or ~/Documents.

## Show library (`library.py`)

- Root: `<first media dir>/xlights` (HA OS: `/media/xlights`), created if missing. Resolve via
  `hass.config.media_dirs` (prefer key `local`), falling back to `hass.config.path("media")`.
- A **show** is a folder `xlights/<Show Name>/` holding one `.fseq` and optionally one audio file
  (`.mp3 .m4a .aac .wav .ogg .flac`). Loose files at the root are grouped by basename
  (`Thriller.fseq` + `Thriller.m4a`) and treated the same way.
- `id` = folder name (or basename for loose files). Show `name` = id.
- Order: `xlights/playlist.json` → `{"order": [ids...]}`. Shows not listed go after, A→Z.
  Write it atomically (temp file + `os.replace`).
- `class ShowLibrary` with blocking methods (call through `hass.async_add_executor_job`):
  `scan() -> list[Show]`, `get(id)`, `delete(id)` (removes the folder / grouped files),
  `reorder(ids)`, `save_upload(filename, stream, show=None)`. `Show` is a dataclass: id, name,
  fseq (path), audio (path|None), duration (s, from the fseq header via `fseq.load`; cache by
  mtime), frames, size (bytes total).
- Never follow a path outside the root (realpath check). Sanitize names: strip path parts,
  reject leading dots, keep letters, digits, space, `-_.,'()&!`.

## Player (`showplayer.py` + `media_player.py`)

- `class ShowPlayer` (one per HA, `hass.data["govee_light_show_show_player"]`) wraps the existing
  `SequencePlayer` (create it the same way `__init__.py` does, output = `receiver.show`, and
  reuse `hass.data[PLAYER]` if present so `play_sequence`/`stop_sequence` stay consistent).
- Queue = library order (or shuffled). State: `idle | playing | paused`, current show, position
  (s), duration, shuffle, repeat (`off | all | one`), speaker entity, audio offset, gap between
  shows (s, default 3).
- With a speaker and a show that has audio: resolve the audio to a URL the speaker can fetch —
  `media_source.async_resolve_media(hass, "media-source://media_source/local/xlights/<rel path>",
  speaker)` then `homeassistant.components.media_player.browse_media.async_process_play_media_url`
  — and use `follow.play_with_speaker`. Without a speaker (or no audio): lights only via
  `SequencePlayer.play(seq, delay=offset)`.
- Pause/resume: with a speaker call `media_player.media_pause`/`media_play` on it (follow.py keeps
  the lights on its clock); without, pause/resume the `Timeline`.
- When a show ends (task done and not stopped by the user) wait `gap`, then advance per repeat/
  shuffle; at the end of the queue with repeat off → idle, strings blank.
- Before playing, switch every Govee light to show mode:
  `for e in hass.config_entries.async_loaded_entries(DOMAIN): if hasattr(e.runtime_data, "enable_show"): e.runtime_data.enable_show()`.
- Expose `add_listener(cb)` for state changes (used by the media_player entity and websocket).
- Expose `async def async_setup_player(hass, entry) -> ShowPlayer` and
  `async def async_unload_player(hass, entry)` in `showplayer.py`; the lead calls them for a config
  entry whose `entry.data["type"] == "player"`. Options on that entry: `speaker` (entity_id or
  absent), `audio_offset` (float s, default 0), `gap` (float s, default 3).
- `media_player.py`: platform with ONE `MediaPlayerEntity` "Light Show Player"
  (unique_id `govee_light_show_show_player`) for the player entry. Features: PLAY, PAUSE, STOP,
  NEXT_TRACK, PREVIOUS_TRACK, SELECT_SOURCE (source = show name, source_list = queue),
  SHUFFLE_SET, REPEAT_SET, PLAY_MEDIA, BROWSE_MEDIA, SEEK (only lights-only playback; with a
  speaker forward `media_seek` to it). `media_content_type` music, `media_title` show name,
  `media_duration`, `media_position` + `media_position_updated_at`, `media_image_url` none.
  `browse_media` lists the shows (content id `govee_light_show_show://<id>`); `play_media` accepts that
  id or a bare show name. Extra attributes: `show_id`, `speaker`, `queue` (list of ids).

## Websocket API (`ws_shows.py`, register with `websocket_api.async_register_command`)

Expose `def async_register(hass)`; the lead calls it once.

| type | params | result |
|---|---|---|
| `govee_light_show/shows/list` | – | `{"shows": [{id, name, duration, has_audio, size, fseq, audio}]}` (file names only) |
| `govee_light_show/shows/delete` | `show_id` | `{}` (admin only) |
| `govee_light_show/shows/reorder` | `order: [id]` | `{}` (admin only) |
| `govee_light_show/player/subscribe` | – | event messages `{state, show_id, position, position_updated_at (ISO), duration, shuffle, repeat, speaker, queue: [id], entity_id}` on every change and every 1 s while playing |

Controls go through the entity's normal `media_player.*` services.

## Upload (`upload.py`)

`HomeAssistantView` at `POST /api/govee_light_show/shows/upload`, `requires_auth = True`, admin only
(`request["hass_user"].is_admin`, else 401). Multipart: one or more `file` parts, optional `show`
text part (target show name; default = file basename). Allowed extensions: `.fseq` + the audio
ones above; 300 MB max per file; stream to a temp file in the target folder in chunks (write in
the executor), then `os.replace`. Returns `{"shows": [ids touched]}`. Expose
`def async_register(hass)`; the lead calls it.

## Player card (`frontend/govee-light-show-player-card.js`)

Vanilla JS custom element `govee-light-show-player-card` (no build step, no external imports; match the
style of `frontend/govee-light-show-card.js`: shadow DOM, HA theme CSS variables, `getStubConfig`,
`getConfigForm`, `window.customCards` entry). Config: `entity` (the player media_player;
autodetect `media_player.*` with attribute `queue` when omitted), `title`.

- **Now playing**: show name, progress bar + `1:42 / 5:57` (interpolate from
  position_updated_at, update every 250 ms), big play/pause, previous, stop, next, shuffle and
  repeat (off/all/one) toggles, speaker name. Seek by tapping the progress bar.
- **Shows list**: each row = ▶ play-this, name, duration, a music-note badge if it has audio,
  up/down buttons to reorder (touch friendly, no drag library), delete with a confirm. Highlight
  the playing one.
- **Add shows**: a drop zone ("Drop .fseq and song files here") plus an "Add files" button
  (`<input type=file multiple accept=".fseq,audio/*">`). Upload with `XMLHttpRequest` to
  `/api/govee_light_show/shows/upload`, header `Authorization: Bearer ${hass.auth.data.access_token}`,
  showing per-file progress; refresh the list after.
- Data: `hass.connection.subscribeMessage(..., {type: "govee_light_show/player/subscribe"})` and
  `hass.callWS({type: "govee_light_show/shows/list"})`; controls via `hass.callService("media_player", ...)`.
- Empty state explains: "Export your sequence from xLights (File → Export / the .fseq next to
  your .xsq) and drop it here with the song."
- Must look right from 320 px phone width to desktop; no horizontal scroll.
- Test it yourself with a mock page (fake `hass` object) served from your scratch dir, using the
  in-app browser (`mcp__Claude_Browser__*`) at mobile and desktop size.

## Device research (`models.py`, `docs/DEVICES.md`)

Which Govee lights accept the **LAN API** (UDP 4003 JSON) and which also accept the undocumented
per-segment "razer" realtime command (the DreamView / PC-sync one this integration uses; see
`protocol.py`). For each SKU family: name, kind (string lights, strip, rope/neon, curtain, outdoor
permanent, bulb, lamp, TV backlight, …), LAN API yes/no, razer per-segment yes/no/unknown,
segment/pixel count the razer frame should use (and whether it depends on length). Sources:
Govee's LAN API doc, Govee developer API supported models list, and open-source projects that
use razer mode (e.g. govee2mqtt, govee_lan / Govee DreamView reverse-engineering repos, WLED/
Hyperion/OpenRGB Govee drivers). Only record what a source supports; mark guesses "unknown".

`models.py` exports `MODELS: dict[str, Model]` with a frozen dataclass `Model(sku, name, kind,
segments: int | None, razer: bool | None)` and `def lookup(sku: str) -> Model | None` (case-
insensitive; matches family prefixes like `H70` only if you list them as such). Include the C9
string we use (`H70C1`/`H70C2`-family — check the real SKU) and Govee's own `R6861` if it exists.
