# Build spec: v1.1.0 — Strands view, online status, Twinkly

Repo `govee-c9/` (HA custom integration, domain `govee_light_show`, HA 2026.10, Python 3.14).
Code: `custom_components/govee_light_show/`. Don't commit, push, or touch Home Assistant / ~/Documents.
Other agents work in parallel: edit ONLY the files your section owns.

## Ownership

| Owner | Files |
|---|---|
| Backend agent | `const.py`, `controller.py`, NEW `twinkly.py`, NEW `health.py`, `__init__.py`, `config_flow.py`, `light.py`, `websocket.py`, `receiver.py`, `protocol.py`, `strings.json`, `translations/en.json`, NEW `tests/test_twinkly.py` |
| Frontend agent | `frontend/govee-light-show-panel.js`, `frontend/govee-light-show-card.js` |
| Docs agent | `README.md`, `CHANGELOG.md`, `manifest.json` (version → 1.1.0), `docs/DEVICES.md` |

## Real devices (for reference; agents must not send to them)

- Govee C9 "Govee C9" 192.168.4.42 (SKU R6861, 50 bulbs, start pixel 1); "Govee C9 Indoor" 192.168.4.73 (offline now, start 51).
- Twinkly "Tree" 192.168.4.45, product TWS400STV, fw 2.9.1, 400 LEDs, led_profile RGB, mac 34:94:54:2e:76:bd.
  Verified working protocol (lead tested it):
  - Discovery: UDP broadcast `b"\x01discover"` to port 5555; reply from the device (first 4 bytes = IP reversed, then `OK`, then name, NUL).
  - `GET http://IP/xled/v1/gestalt` (no auth): device_name, product_code, number_of_led, led_profile ("RGB"/"RGBW"), mac, fw_family…
  - Login: `POST /xled/v1/login {"challenge": base64(32 random bytes)}` → `authentication_token`, `authentication_token_expires_in` (s, ~14400), `challenge-response`; then `POST /xled/v1/verify {"challenge-response": ...}` with header `X-Auth-Token: <token>`. All later calls send that header, `Content-Type: application/json`. HTTP 401 = log in again.
  - `GET/POST /xled/v1/led/mode` (`{"mode": "rt"|"off"|"effect"|"movie"...}`).
  - `POST /xled/v1/led/out/brightness {"mode":"enabled","type":"A","value":100}`.
  - `GET /xled/v1/led/layout/full` → `{"source":"2d"|"3d"|"linear", "coordinates":[{"x":-0.5,"y":0.23,"z":1.0},...]}` (x ≈ -1..1, y ≈ 0..1 with y UP).
  - Realtime UDP to port 7777, v3 packets: `b"\x03" + base64decode(token) (8 bytes) + b"\x00\x00" + bytes([fragment_index]) + data[≤900 bytes]`, data = R,G,B per LED (RGBW profile: W,R,G,B with W = min(r,g,b) subtracted from each). Tested at 22 fps, 2 fragments for 400 LEDs.

## Backend

### const.py
`TYPE_TWINKLY = "twinkly"`, `CONF_DEVICE_ID = "device_id"`, `CONF_LED_PROFILE = "led_profile"`. Pixel space grows to 2040 (receiver `MAX_PIXELS = 2040`, 12 E1.31 universes); config-flow `PIXEL` max 2040, bulbs max 1020.

### controller.py — hardware hooks + health + identify
Refactor so every hardware write goes through three overridable methods (Govee behaviour unchanged):
- `_hw_power(on: bool)` → `self.lan.send(turn_msg(on))`
- `_hw_frame(frame)` (segmented path, frame already brightness-scaled) → arm realtime if `not self._realtime` (brightness_msg(100) + realtime_msg(True)), then `colours_msg(frame)`
- `_hw_close()` → `self.lan.close()`
Create the Govee link in a `_make_link(host)` method so subclasses can avoid opening a Govee socket. Add `self.host`.

Health: `self.online: bool | None = None` (None = not checked yet), `self.last_seen: float | None` (wall clock `time.time()`), `self._misses = 0`. Methods `mark_seen()` (online True, misses 0, notify if changed) and `mark_missed(threshold)` (misses += 1; online False once misses ≥ threshold; notify if changed).

`async_identify()`: flash the whole light white 3× (0.4 s on / 0.3 s off) then restore exactly: if it was off, turn off again; if in show mode, go back to show mode; else `_render()`. While flashing, ignore sequencer frames. A second call while one runs does nothing.

### twinkly.py
- `async_discover(timeout=2.5) -> dict[ip, name]` (UDP 5555 broadcast, non-blocking socket with loop.sock_recvfrom like `protocol.async_scan`).
- `async_gestalt(session, ip) -> dict` (raises on failure; 3 s timeout).
- `TwinklyClient(session, host)`: `async_login()`, `async_request(method, path, json=None)` (auto login, re-login once on 401, refresh token 60 s before expiry), `async_set_mode(mode)`, `async_get_mode()`, `async_layout() -> list[[x, -y]] | None` (y flipped so the card draws top-down; None if `source == "linear"` or error), `token_bytes`.
- `TwinklyController(C9Controller)`: constructor `(hass, host, bulbs, profile="RGB")`; uses `async_get_clientsession(hass)`; overrides `_make_link` (no Govee socket; open own UDP socket for 7777), `_hw_power(on)` (on → nothing until the first frame arms rt; off → schedule `set_mode("off")`), `_hw_frame(frame)` (if not armed: schedule an async arm task = login + brightness 100 + set_mode("rt"); drop frames until armed; then send v3 fragments), `_hw_close()`. Keepalive: while on and armed, resend the last frame every 2 s (loop.call_later) so static colours/patterns hold in rt mode. `async_check_mode()` used by health: if we expect rt (on and armed) but device reports another mode (someone used the Twinkly app), clear armed so the next frame/keepalive re-arms. `self.layout` = cached layout (fetched once after setup, background task). Always `segmented = True`. All HTTP errors are logged at debug and never raise out of callbacks.

### health.py — `HealthMonitor(hass)`, started in `async_setup`, every 60 s (first run 10 s after start)
- Govee entries: one `protocol.async_scan(2.0)`. Make `async_scan` return `None` when it can't bind/send (port held) — then don't change anything. Track `scan_ever_worked`; only mark missed if a scan has ever found at least one device (multicast may be blocked). Threshold 3 misses. Found host → `mark_seen()`; if entry has no `CONF_DEVICE_ID` and the reply has `device`, store it (`async_update_entry(data=...)`). Not found at host but its device id answers at another IP → update `CONF_HOST` in entry data (the update listener reloads it) and log info "moved from A to B".
- Twinkly entries: `async_gestalt` per light (threshold 2); on success `mark_seen()` and `await ctl.async_check_mode()`. On a miss, run `async_discover`, gestalt each found IP, and if its mac matches the entry's `CONF_DEVICE_ID`, move the host as above.
- Never raise; log at debug.

### __init__.py
Entry types: player (unchanged), twinkly (`TwinklyController`), else Govee. Twinkly setup: gestalt check first → `ConfigEntryNotReady` if unreachable. Receiver subscribe the same way for both. Start/stop the HealthMonitor once (hass.data key). Entity/device unique ids must stay stable when the IP changes: use `entry.unique_id` (existing Govee entries have unique_id = their original host, so ids don't change).

### light.py
- Unique ids / device identifiers from `entry.unique_id or entry.data[CONF_HOST]`.
- `available` → `ctl.online is not False`.
- DeviceInfo: manufacturer "Twinkly" or "Govee", model = sku/product_code, plus `connections={(CONNECTION_NETWORK_MAC, mac)}` when the device id is a MAC (Twinkly).
- String light extra attributes add: `ip_address`, `start_pixel`, `pixels` ("51-100"), `channels` ("151-300").
- Twinkly default `CONF_BULB_ENTITIES` False.

### config_flow.py
Menu: `light` ("Add a Govee light"), `twinkly` ("Add a Twinkly light"), `player`. Govee scan list labels: `"<SKU> at <ip>"`; store the scan's `device` id as `CONF_DEVICE_ID`. Twinkly: discover → select "<name> (<product_code>) at <ip>" + "Enter an IP address" → gestalt (error `cannot_connect`) → unique_id = mac (lowercase) → details step with name = device_name, bulbs = number_of_led, start pixel = next free, bulb_entities False, and no `segmented` field. Options flow for Twinkly: same fields without `segmented`. Update strings.json + translations/en.json (identical).

### websocket.py — new commands (the frontend depends on these exact shapes)
- `govee_light_show/strands` → `{"strands": [{entry_id, title, kind: "govee"|"twinkly", host, model, start_pixel, bulbs, online: bool|null, last_seen: ISO string|null, loaded: bool, entity_id: str|null}], "ddp_port": 4048, "e131_port": 5568, "max_pixels": 2040}`. Includes not-loaded light entries (loaded false, online null). Excludes the player.
- `govee_light_show/identify` `{entry_id}` → `{}` (starts `async_identify` as a task). Error `not_found` if not loaded.
- `govee_light_show/strand/update` `{entry_id, title?: str, start_pixel?: int, bulbs?: int}` admin only → updates title and options (reloads) → `{}`.
- `govee_light_show/subscribe` event: add `"layout": [[x, y], ...]` ONLY in the first event, and only when the controller has a layout (Twinkly).

### tests/test_twinkly.py
Pure unit tests (no HA needed, like tests/test_library.py): v3 packet building/fragmentation for 400 RGB LEDs (2 packets, sizes 900 and 300 + 12-byte header), RGBW conversion, layout y-flip, discovery reply parsing. Put the pure helpers in twinkly.py as module functions so they're testable without HA. Run with the pytest at `../pt` (see tests/test_library.py for how it imports).

## Frontend

### Panel "Lights" tab → Strands list on top, live cards below
- Fetch `govee_light_show/strands` on open, every 15 s while the Lights tab is visible, and after any action.
- One row per strand (responsive: wraps to a 2-line block under 600 px, no horizontal scroll at 320 px):
  status dot (green online / red offline + "last seen 5 min ago" / grey "checking…"), name, kind+model ("Twinkly TWS400STV", "Govee R6861"), IP (monospace, tap to copy), "Pixels 51–100 · Channels 151–300".
  Buttons: **Identify** (calls identify, shows "Flashing…" 3 s), **Edit** (admin only: inline name/start pixel/bulbs inputs, Save/Cancel → strand/update).
- Overlap check: if two strands' pixel ranges overlap, show a warning on both rows ("Overlaps Porch: pixels 40–50"). Suggest the next free start pixel in the Edit form.
- Footer box "For xLights / EZPlayer": `DDP · <location.hostname>:4048 · <total pixels> pixels (<×3> channels)`, plus a **Copy** button copying a short multi-line setup text (controller type Ethernet/DDP, IP, port, start channel 1, total channels; one line per strand with its channel range).
- "Add a light" button (existing INTEGRATION_PATH).
- Light cards: show offline strands' cards greyed with "Offline" (their entity state is `unavailable`).

### Card
If the config has no `layout` and a subscribe event carries `layout`, use it (rebuild once). Unavailable entity: show "Offline" status and disable painting (already partly there via `_canPaint`).

### Testing
Mock page with a fake `hass` (states + callWS + connection.subscribeMessage) in your scratch dir, served locally, checked in the in-app browser (`mcp__Claude_Browser__*`) at 375 px and desktop, including one offline strand, one Twinkly with a 400-point tree layout, and an overlap.
