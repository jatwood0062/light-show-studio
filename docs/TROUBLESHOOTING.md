# Troubleshooting

Find your problem below and try the fixes in order. Most problems come down to three things:
LAN Control is off, Home Assistant and the light can't see each other, or a setting in
**Configure** doesn't match the light.

## Setup

### My light is not in the list

- In the Govee Home app, turn on **LAN Control** for that light.
- Put Home Assistant and the light on the same network. Discovery may not reach across separate
  VLANs or some guest Wi-Fi networks.
- The official Govee integration may hold the reply port (UDP 4002). Choose
  **Enter an IP address** and type the light's IP. Your router's device list shows it.
- Allow UDP ports 4001 to 4003 between Home Assistant and the light.

### The IP address is rejected

Type a plain IPv4 address, like `192.168.1.50`. Names such as `govee-porch` are not accepted.

### The bulb count is wrong

Choose **Configure** and set **Number of bulbs / segments**. A C9 50-bulb string is 50. Save.

## Colours and bulbs

### The light only shows one colour

Some lights take only one colour for the whole light. In **Configure**, turn **off**
**Per-bulb control (turn off if the light only takes one colour)**. The show then plays as one
blended colour, which is slower and less detailed.

If the light should show every bulb, turn **on** per-bulb control and check LAN Control. A
100-bulb C9 (H6861) is not confirmed for per-bulb control in `docs/DEVICES.md`, so the one-colour
setting may be the only option for it.

### Single bulbs are not separate lights

Turn on **Create a light for every bulb** in **Configure**. Per-bulb control must be on too.

## Live shows from xLights, EZPlayer or Light-O-Rama

### Nothing happens when the sequence plays

- In xLights, add a **DDP** controller with Home Assistant's IP, 150 channels per 50-bulb string.
  Or use **E1.31** on **universe 1**, unicast to Home Assistant's IP. Light-O-Rama S6 works the same way.
- On each light, set the first effect to **Light show**. Any other effect ignores the sequencer.
- The light must be **on**. Frames are ignored while it is off.
- Check each string's **First pixel** against the channels in your layout.
- Look in the Home Assistant log for `port 4048 in use` or `port 5568` warnings. Another program
  holds the port; stop it and restart Home Assistant.

### The show looks choppy

Home Assistant takes up to about 40 frames per second from a sequencer and skips the rest. Try a
lower frame rate in xLights, and check the light's Wi-Fi signal.

### The lights stop when I pick a colour

That's expected. A colour or effect takes the string away from the sequencer. Pick **Light show**
again, or play a show from the Light Shows app, to hand control back.

## Playing shows from Home Assistant

### A show doesn't appear

- The `.fseq` must be in `/media/xlights/`, or in a folder inside it.
- Name the song the same as the `.fseq`, like `Thriller.fseq` and `Thriller.m4a`. Songs can be
  `.mp3`, `.m4a`, `.aac`, `.wav`, `.ogg` or `.flac`.
- Refresh the Light Shows page after copying files in.

### The upload fails

- Only **administrators** can upload.
- Upload only `.fseq` and song files, up to **300 MB** each.

### Lights and music are out of sync

- In the player options, set **Light delay**. Positive values run the lights later, negative
  values earlier. If the lights run ahead of the sound, try `0.1` to `0.3`.
- Slow speakers, such as Music Assistant or Sonos, can take 1 to 3 seconds to start. The
  integration follows the speaker's position, so only fine-tune the delay after that.
- Drift is eased out automatically. A jump, such as a seek, is followed straight away.

### The speaker plays nothing

- Set **Speaker for the music** in the player options. Lights still run without one.
- Home Assistant must reach the speaker, and the speaker must be able to fetch the song from
  Home Assistant.
- Put the song in the same folder as the `.fseq`.
- With `play_sequence`, music plays only when both `media_player` and `audio` are set.

## Other problems

### The dashboard or sidebar still shows the old version

Refresh the browser page. In the Companion app, close it completely and open it again.

### The lights changed IP address

The integration is tied to the light's IP address. Reserve a fixed address for the light in your
router (a DHCP reservation), remove the old entry, then add the light again with
**Enter an IP address**.
