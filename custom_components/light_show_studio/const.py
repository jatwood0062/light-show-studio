DOMAIN = "light_show_studio"
SEQUENCER_KEY = f"{DOMAIN}_sequencer"  # hass.data key of the one shared .fseq SequencePlayer (made in async_setup)

CONF_BULBS = "bulbs"
CONF_BULB_ENTITIES = "bulb_entities"

DEFAULT_BULBS = 50
FPS = 25  # the C9 kept up with 25 fps in testing
WHOLE_LIGHT_FPS = 8  # lights without per-segment control get JSON colour commands, so fewer
EXTERNAL_MAX_FPS = 40  # cap for frames arriving from xLights / E1.31
CONF_START_PIXEL = "start_pixel"
CONF_SEGMENTED = "segmented"  # light takes per-segment ("razer") frames
CONF_SKU = "sku"
CONF_TYPE = "type"  # "light" (default) or "player"
TYPE_PLAYER = "player"
CONF_SPEAKERS = "speakers"  # ordered list of media players; the first leads
CONF_AUDIO_OFFSET = "audio_offset"
CONF_ALLOWED_SOURCES = "allowed_sources"  # player option: comma separated IPs / networks allowed to send DDP / E1.31 ("" = any LAN address)
CONF_GAP = "gap"  # where this string's slice begins in the xLights output
FLUSH_DELAY = 0.05  # coalesce many bulb changes (e.g. a scene) into one frame
TYPE_TWINKLY = "twinkly"
TYPE_WLED = "wled"
CONF_DEVICE_ID = "device_id"  # Govee device id / Twinkly or WLED MAC: finds the light again when its IP changes
CONF_LED_PROFILE = "led_profile"  # Twinkly: "RGB" or "RGBW"
CONF_TRANSITION = "transition"  # between shows: none, fade, crossfade or effect
CONF_TRANSITION_EFFECT = "transition_effect"
SCHEDULER_KEY = f"{DOMAIN}_scheduler"  # hass.data key of the Scheduler (playlists + schedules), made in async_setup
SCHEDULE_STORE_KEY = f"{DOMAIN}.schedule"
EVENT_SCHEDULE_STARTED = f"{DOMAIN}_schedule_started"
EVENT_SCHEDULE_ENDED = f"{DOMAIN}_schedule_ended"
