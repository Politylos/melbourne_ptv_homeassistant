"""Constants for the PTV Transport integration."""

DOMAIN = "ptv_transport"

CONF_DEVID = "devid"
CONF_API_KEY = "api_key"
CONF_TOPIC_PREFIX = "topic_prefix"
CONF_ROUTES = "routes"
CONF_FROM = "from"
CONF_TO = "to"
CONF_MODES = "modes"
CONF_MAX_WALK = "max_walk"
CONF_ONLY_RELEVANT = "only_relevant_stops"
CONF_MAX_JOURNEYS = "max_journeys"
CONF_STOPS_PER_MODE = "stops_per_mode"

DEFAULT_TOPIC_PREFIX = "transport"
DEFAULT_SCAN_INTERVAL = 120  # seconds
DEFAULT_MAX_JOURNEYS = 5
DEFAULT_STOPS_PER_MODE = 4

# PTV route_type numbers
MODES = {"train": 0, "tram": 1, "bus": 2, "vline": 3, "night_bus": 4}
MODE_NAMES = {v: k for k, v in MODES.items()}

# How far you're willing to walk to each mode (metres)
DEFAULT_MAX_WALK = {"train": 1500, "tram": 800, "bus": 600, "vline": 3000, "night_bus": 600}

WALK_SPEED_MPS = 1.3
CANDIDATES_TRIED = 8    # stop/route pairs checked per refresh
RUNS_TRIED = 3          # runs checked per pair (one pattern call each)
STOP_CACHE_SECONDS = 24 * 3600

SERVICE_REFRESH = "refresh"

# Config entry / UI
CONF_SCAN_SECONDS = "scan_interval"     # options: seconds between refreshes
SUBENTRY_ROUTE = "route"
CONF_FROM_ENTITY = "from_entity"        # route form fields
CONF_FROM_LOCATION = "from_location"
CONF_TO_ENTITY = "to_entity"
CONF_TO_LOCATION = "to_location"
CONF_TO_NAME = "to_name"
SECTION_WALKING = "walking"
SECTION_ADVANCED = "advanced"
