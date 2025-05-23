# config.py
import requests
import logging # ### Added import logging ###

# --- File Names and Paths ---
LOG_FILE = "anem_app.log"
DATA_FILE = "members_data.json"
STYLESHEET_FILE = "styles_dark.txt"
SETTINGS_FILE = "app_settings.json" # File to store settings

# --- API Configuration ---
BASE_API_URL = "https://ac-controle.anem.dz/AllocationChomage/api"
MAIN_SITE_CHECK_URL = "https://ac-controle.anem.dz/" # For checking general site availability

# --- Session Object (shared across API clients if needed) ---
SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'ar-DZ,ar;q=0.9,fr-FR;q=0.8,fr;q=0.7,en-US;q=0.6,en;q=0.5',
    'Origin': 'https://minha.anem.dz',
    'Referer': 'https://minha.anem.dz/',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-site',
    'Cache-Control': 'no-cache', # Ensure fresh data
    'Pragma': 'no-cache' # For older HTTP/1.0 servers
})

# --- Settings Keys (used for consistency in accessing settings dict) ---
SETTING_MIN_MEMBER_DELAY = "min_member_delay"
SETTING_MAX_MEMBER_DELAY = "max_member_delay"
SETTING_MONITORING_INTERVAL = "monitoring_interval"
SETTING_BACKOFF_429 = "backoff_429"           # Delay after HTTP 429
SETTING_BACKOFF_GENERAL = "backoff_general"   # General retry delay
SETTING_REQUEST_TIMEOUT = "request_timeout"   # Timeout for API requests

# --- Default Settings (if settings file is missing or corrupted) ---
DEFAULT_SETTINGS = {
    SETTING_MIN_MEMBER_DELAY: 30,      # seconds (e.g., 30 seconds)
    SETTING_MAX_MEMBER_DELAY: 60,     # seconds (e.g., 60 seconds)
    SETTING_MONITORING_INTERVAL: 1,  # minutes (e.g., 1 minute)
    SETTING_BACKOFF_429: 60,          # seconds (e.g., 60 seconds)
    SETTING_BACKOFF_GENERAL: 5,       # seconds (e.g., 5 seconds)
    SETTING_REQUEST_TIMEOUT: 30       # seconds (e.g., 30 seconds)
}

# --- Retry Mechanism Constants (used by AnemAPIClient) ---
MAX_RETRIES = 3  # Max number of retries for a single API call (excluding initial attempt)
MAX_BACKOFF_DELAY = 120  # Maximum delay (in seconds) for exponential backoff

# --- Other Application Constants ---
MAX_ERROR_DISPLAY_LENGTH = 70 # Max length for truncated error messages in the table
APP_ID_FALLBACK = 'anem-booking-app-pyqt14-refactored' # Fallback if __app_id is not defined

# Attempt to get __app_id, provide a fallback if not defined (e.g., when running outside specific env)
# This part is for environments where __app_id might be injected.
# If running as a standalone script without __app_id, it will use the fallback.
try:
    # Attempt to access __app_id. If it's not defined, a NameError will occur.
    # This variable is typically injected by the execution environment.
    APP_ID = __app_id
except NameError:
    # If __app_id is not defined (e.g., running script directly), use the fallback.
    # Get a logger instance. Note: The main application logger is usually set up
    # by logger_setup.py and imported in main_app.py. This logger is specific
    # to this fallback scenario in config.py.
    config_logger = logging.getLogger(__name__ + ".config_fallback")
    config_logger.info("Global variable __app_id not found, using fallback APP_ID.")
    APP_ID = APP_ID_FALLBACK

