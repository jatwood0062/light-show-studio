DOMAIN = "govee_c9"

CONF_BULBS = "bulbs"
CONF_BULB_ENTITIES = "bulb_entities"

DEFAULT_BULBS = 50
FPS = 25  # the C9 kept up with 25 fps in testing
EXTERNAL_MAX_FPS = 40  # cap for frames arriving from xLights / E1.31
CONF_START_PIXEL = "start_pixel"  # where this string's slice begins in the xLights output
FLUSH_DELAY = 0.05  # coalesce many bulb changes (e.g. a scene) into one frame
