# Govee C9 Pixels

Local, per-bulb control of Govee C9 string lights (H6860 / reports as R6861) for Home Assistant.

- One light for the whole string, with effects (Candy Cane Spin, Halloween Chase, Witches Brew, …)
- One light per bulb, and a `govee_c9.set_pixels` action
- **xLights / Light-O-Rama receiver**: the string shows up to a sequencer as a 50-pixel controller,
  so you can sequence it to music

Everything stays on your LAN (Govee LAN API, UDP 4003). In the Govee Home app, turn on
**LAN Control** for the light first.

## Install (HACS)

1. HACS → ⋮ → **Custom repositories** → add this repository's URL, type **Integration**.
2. Download **Govee C9 Pixels**, then restart Home Assistant.
3. Settings → Devices & services → **Add integration** → Govee C9 Pixels → enter the light's IP.

## Sequencing to music with xLights

Home Assistant listens for pixel data on **DDP (UDP 4048)** and **E1.31/sACN (UDP 5568, universe 1)**.

1. In xLights → **Controllers** → **Add Ethernet**:
   - Protocol **DDP**, IP address = your Home Assistant IP
   - Channels **150** (50 RGB pixels)
2. **Layout**: add a **Single Line** model with 50 nodes, start channel 1 on that controller.
3. Sequence a song (File → New Sequence → Musical, pick the audio file; the sequencer
   can mark beats for you). Turn on **Output to Lights** and press Play.

Light-O-Rama S6: add an E1.31 universe 1, 150 channels, unicast to the Home Assistant IP.

While frames arrive the string follows the sequencer; normal Home Assistant control takes
over again when you pick a colour or effect.
