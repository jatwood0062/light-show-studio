# Govee device research

Which Govee lights take the LAN API (UDP 4003) and the undocumented `razer` per-segment realtime
frame (`BB .. B0 <mode> <count> <rgb...> <xor>`, see `protocol.py`). `unknown` means no source says.

## Twinkly

Tested on a Twinkly TWS400STV (400 RGB LEDs, firmware 2.9.1, Gen II). RGBW models are supported
but untested. Twinkly lights use their own local API, not the Govee LAN API.

## Key findings

- **Real SKU of the Govee C9 string lights: H6860** (50 bulbs, 49 ft) and H6861 (100 bulbs, 98 ft),
  per Govee's product pages. They are NOT H70C1/H70C2 (those are the non-C9 "Christmas String
  Lights" 10 m / 20 m). No `R6861` was found anywhere. H6860/H6861 do not appear in the openHAB
  LAN list; LAN support is confirmed here only by this project's own device (50 bulbs, razer works;
  owner-verified, not a published source).
- Confirmed razer per-segment from published sources: **H618A, 15 segments** (DMXCore.Plugin.Govee,
  firmware 1.02.11). Govee's WiFi RGBIC family (H6159, H616x, H618x, H619x, H61A0) is said to share the
  packet format (via OpenRGB references) but is only verified on H618A.
- Other reports: LeoSko/GoveeDreamView streams 10 zones to an unnamed strip (repo topic `h6609`, not
  confirmed); govee-rgbic-bridge tested on H6047 using an RGBIC packet mode, 10 virtual segments per
  bar by config, warns not every model/firmware supports it. Neither is recorded as confirmed.
- The segment count is supplied by the sender in each frame (byte after the mode flag); the frame
  length field is 2 + 3N. Whether a device honours a count other than its hardware count is not
  documented; length dependence of counts (strips by metre) is unknown.
- Ordinary Govee A19 bulbs do not support the LAN API (DMXCore page). LAN control must be enabled
  per device in the Govee Home app.

## Table

LAN column: yes = listed in openHAB Govee LAN-API binding (which follows Govee's supported list);
? = not in that list.

| SKU | Name | Kind | LAN | Razer | Segments |
|---|---|---|---|---|---|
| H6042 | Govee TV Light Bar #2 | tv_backlight | yes | unknown | unknown |
| H6043 | Govee TV Light Bars #2 | tv_backlight | yes | unknown | unknown |
| H6046 | RGBIC TV Light Bars | tv_backlight | yes | unknown | unknown |
| H6047 | RGBIC Gaming Light Bars with Smart Controller | tv_backlight | yes | unknown | unknown |
| H6051 | Aura Smart Table Lamp | table_lamp | yes | unknown | unknown |
| H6052 | Govee Table Lamp | table_lamp | yes | unknown | unknown |
| H6056 | Flow Plus | table_lamp | yes | unknown | unknown |
| H6059 | RGBWW Night Light for Kids | table_lamp | yes | unknown | unknown |
| H6061 | Glide Hexa LED Panels | panel | yes | unknown | unknown |
| H6062 | Glide Wall Light | panel | yes | unknown | unknown |
| H6063 | Gaming Wall Light | panel | yes | unknown | unknown |
| H6065 | Glide RGBIC Y Lights | panel | yes | unknown | unknown |
| H6066 | Glide Hexa Pro LED Panel | panel | yes | unknown | unknown |
| H6067 | Glide Triangle Light Panels | panel | yes | unknown | unknown |
| H606A | Glide Hexa Light Panel Ultra | panel | yes | unknown | unknown |
| H6072 | RGBICWW Corner Floor Lamp | floor_lamp | yes | unknown | unknown |
| H6073 | LED Floor Lamp | floor_lamp | yes | unknown | unknown |
| H6076 | RGBICW Smart Corner Floor Lamp | floor_lamp | yes | unknown | unknown |
| H6078 | Cylinder Floor Lamp | floor_lamp | yes | unknown | unknown |
| H607C | Floor Lamp #2 | floor_lamp | yes | unknown | unknown |
| H6087 | RGBIC Smart Wall Sconces | panel | yes | unknown | unknown |
| H6088 | RGBIC Cube Wall Sconces | panel | yes | unknown | unknown |
| H608A | String Downlights 5M | string | yes | unknown | unknown |
| H608B | String Downlights 3M | string | yes | unknown | unknown |
| H608C | String Downlights 2M | string | yes | unknown | unknown |
| H608D | String Downlights 10M | string | yes | unknown | unknown |
| H60A0 | Ceiling Light | ceiling | yes | unknown | unknown |
| H60A1 | Smart Ceiling Light | ceiling | yes | unknown | unknown |
| H610A | Glide Lively Wall Lights | panel | yes | unknown | unknown |
| H610B | Music Wall Lights | panel | yes | unknown | unknown |
| H6110 | 2x5M Multicolor Strip with Alexa | strip | yes | unknown | unknown |
| H6117 | Dream Color LED Strip Light 10M | strip | yes | unknown | unknown |
| H6141 | 5M Smart Multicolor Strip Light | strip | yes | unknown | unknown |
| H6143 | 5M Strip Light | strip | yes | unknown | unknown |
| H6144 | 2x5M Strip Light | strip | yes | unknown | unknown |
| H6159 | RGB Light Strip | strip | yes | unknown | unknown |
| H615A | 5M Light Strip with Alexa | strip | yes | unknown | unknown |
| H615B | 10M Light Strip with Alexa | strip | yes | unknown | unknown |
| H615C | 15M Light Strip with Alexa | strip | yes | unknown | unknown |
| H615D | 20M Light Strip with Alexa | strip | yes | unknown | unknown |
| H615E | 30M Light Strip with Alexa | strip | yes | unknown | unknown |
| H6163 | Dreamcolor LED Strip Light 5M | strip | yes | unknown | unknown |
| H6167 | TV Backlight 2.4M | tv_backlight | yes | unknown | unknown |
| H6168 | TV Backlight 2x0.7M+2x1.2M | tv_backlight | yes | unknown | unknown |
| H616C | Outdoor Strip 10M | strip | yes | unknown | unknown |
| H616D | Outdoor Strip 2x7.5M | strip | yes | unknown | unknown |
| H616E | Outdoor Strip 2x10M | strip | yes | unknown | unknown |
| H6172 | Outdoor LED Strip 10M | strip | yes | unknown | unknown |
| H6173 | RGBIC Outdoor Strip Lights | strip | yes | unknown | unknown |
| H6175 | RGBIC Outdoor Strip Lights 10M | strip | yes | unknown | unknown |
| H6176 | RGBIC Outdoor Strip Lights 30M | strip | yes | unknown | unknown |
| H6182 | WiFi Multicolor TV Strip Light | tv_backlight | yes | unknown | unknown |
| H618A | RGBIC Basic LED Strip Lights 5M | strip | yes | yes | 15 |
| H618C | RGBIC Basic LED Strip Lights 5M | strip | yes | unknown | unknown |
| H618E | LED Strip Lights 22M | strip | yes | unknown | unknown |
| H618F | RGBIC LED Strip Lights | strip | yes | unknown | unknown |
| H619A | Strip Lights with Protective Coating 5M | strip | yes | unknown | unknown |
| H619B | Strip Lights with Protective Coating 7.5M | strip | yes | unknown | unknown |
| H619C | Strip Lights with Protective Coating 10M | strip | yes | unknown | unknown |
| H619D | Pro LED Strip Lights 2x7.5M | strip | yes | unknown | unknown |
| H619E | Strip Lights with Protective Coating 2x10M | strip | yes | unknown | unknown |
| H619Z | Pro LED Strip Lights 3M | strip | yes | unknown | unknown |
| H61A0 | RGBIC Neon Rope Light 3M | neon_rope | yes | unknown | unknown |
| H61A1 | RGBIC Neon Rope Light 2M | neon_rope | yes | unknown | unknown |
| H61A2 | RGBIC Neon Rope Light 5M | neon_rope | yes | unknown | unknown |
| H61A3 | RGBIC Neon Rope Light 4M | neon_rope | yes | unknown | unknown |
| H61A5 | Neon LED Strip Light 10M | neon_rope | yes | unknown | unknown |
| H61A8 | Neon Rope Light 10M/20M | neon_rope | yes | unknown | unknown |
| H61B1 | Strip Light with Cover 5M | strip | yes | unknown | unknown |
| H61B2 | RGBIC Neon TV Backlight 3M | tv_backlight | yes | unknown | unknown |
| H61BA | LED Strip Light 5M | strip | yes | unknown | unknown |
| H61BC | LED Strip Light 10M | strip | yes | unknown | unknown |
| H61BE | LED Strip Light 2x10M | strip | yes | unknown | unknown |
| H61C2 | Neon LED Strip Light 2M/3M/5M | neon_rope | yes | unknown | unknown |
| H61D3 | Neon Rope Light 2 3M | neon_rope | yes | unknown | unknown |
| H61D5 | Neon Rope Light 2 5M | neon_rope | yes | unknown | unknown |
| H61E0 | LED Strip Light M1 | strip | yes | unknown | unknown |
| H61E1 | LED Strip Light M1 | strip | yes | unknown | unknown |
| H6199 | DreamView T1 TV Backlight | tv_backlight | ? | unknown | unknown |
| H7012 | Warm White Outdoor String Lights | string | yes | unknown | unknown |
| H7013 | Warm White Outdoor String Lights | string | yes | unknown | unknown |
| H7021 | RGBIC Warm White Smart Outdoor String | string | yes | unknown | unknown |
| H7028 | Lynx Dream LED-Bulb String | string | yes | unknown | unknown |
| H7033 | LED-Bulb String Lights | string | yes | unknown | unknown |
| H7041 | LED Outdoor Bulb String Lights | string | yes | unknown | unknown |
| H7042 | LED Outdoor Bulb String Lights | string | yes | unknown | unknown |
| H7050 | Outdoor Ground Lights 11M | outdoor_fixture | yes | unknown | unknown |
| H7051 | Outdoor Ground Lights 15M | outdoor_fixture | yes | unknown | unknown |
| H7052 | Outdoor Ground Lights 15M/30M | outdoor_fixture | yes | unknown | unknown |
| H7055 | Pathway Light | outdoor_fixture | yes | unknown | unknown |
| H705A | Permanent Outdoor Lights 30M | permanent_outdoor | yes | unknown | unknown |
| H705B | Permanent Outdoor Lights 15M | permanent_outdoor | yes | unknown | unknown |
| H705C | Permanent Outdoor Lights 45M | permanent_outdoor | yes | unknown | unknown |
| H705D | Permanent Outdoor Lights #2 15M | permanent_outdoor | yes | unknown | unknown |
| H705E | Permanent Outdoor Lights #2 30M | permanent_outdoor | yes | unknown | unknown |
| H705F | Permanent Outdoor Lights #2 45M | permanent_outdoor | yes | unknown | unknown |
| H7060 | LED Flood Lights (2-Pack) | outdoor_fixture | yes | unknown | unknown |
| H7061 | LED Flood Lights (4-Pack) | outdoor_fixture | yes | unknown | unknown |
| H7062 | LED Flood Lights (6-Pack) | outdoor_fixture | yes | unknown | unknown |
| H7063 | Outdoor Flood Lights | outdoor_fixture | yes | unknown | unknown |
| H7065 | Outdoor Spot Lights | outdoor_fixture | yes | unknown | unknown |
| H7066 | Outdoor Spot Lights | outdoor_fixture | yes | unknown | unknown |
| H706A | Permanent Outdoor Lights Pro 30M | permanent_outdoor | yes | unknown | unknown |
| H706B | Permanent Outdoor Lights Pro 45M | permanent_outdoor | yes | unknown | unknown |
| H706C | Permanent Outdoor Lights Pro 60M | permanent_outdoor | yes | unknown | unknown |
| H7070 | Outdoor Projector Light | outdoor_fixture | yes | unknown | unknown |
| H7075 | Outdoor Wall Light | panel | yes | unknown | unknown |
| H70B1 | 520 LED Curtain Lights | curtain | yes | unknown | unknown |
| H70BC | 400 LED Curtain Lights | curtain | yes | unknown | unknown |
| H70C1 | RGBIC String Light 10M (Christmas String Lights) | string | yes | unknown | unknown |
| H70C2 | RGBIC String Light 20M (Christmas String Lights) | string | yes | unknown | unknown |
| H6860 | C9 Christmas String Lights 50 bulbs (49ft) | c9_string | ? | yes | 50 |
| H6861 | C9 Christmas String Lights 100 bulbs (98ft) | c9_string | ? | unknown | unknown |
| H805A | Permanent Outdoor Lights Elite 30M | permanent_outdoor | yes | unknown | unknown |
| H805B | Permanent Outdoor Lights Elite 15M | permanent_outdoor | yes | unknown | unknown |
| H805C | Permanent Outdoor Lights Elite 45M | permanent_outdoor | yes | unknown | unknown |

Notes: H61A8 (10 m/20 m), H61C2 (2/3/5 m), H7052 (15/30 m) are one SKU shared by several names in the
source. H6199 (DreamView T1) is from the ioBroker govee-smart device list only.

## Sources

- openHAB Govee LAN-API binding (117 models): https://www.openhab.org/addons/bindings/govee/
- DMXCore.Plugin.Govee (H618A, 15 segments, razer family notes): https://www.nuget.org/packages/DMXCore.Plugin.Govee
- LeoSko/GoveeDreamView (frame layout, 10 zones): https://github.com/LeoSko/GoveeDreamView
- romeolazar/govee-rgbic-bridge (H6047): https://github.com/romeolazar/govee-rgbic-bridge
- ioBroker.govee-smart devices wiki (H6199): https://github.com/krobipd/ioBroker.govee-smart/wiki/Devices
- govee2mqtt LAN notes: https://github.com/wez/govee2mqtt/blob/main/docs/LAN.md
- Govee C9 product page (H6860/H6861): https://us.govee.com/products/govee-c9-christmas-string-lights
- Govee H70C1/H70C2 Christmas String Lights: https://us.govee.com/products/govee-christmas-string-lights
- OpenRGB Govee draft MR (not checked in detail): https://gitlab.com/CalcProgrammer1/OpenRGB/-/merge_requests/2172
- Govee LAN API user manual (official list, page not retrievable here): https://app-h5.govee.com/user-manual/wlan-guide
