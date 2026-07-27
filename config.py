# -*- coding: utf-8 -*-
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

INPUT_FILE = str(DATA_DIR / "names.txt")
OUTPUT_FILE = str(DATA_DIR / "results.csv")
LOG_FILE = str(BASE_DIR / "run.log")

BASE_URL = "https://www.peoplesearchnow.com"
SEARCH_URL = f"{BASE_URL}/person"

HEADLESS = True
TIMEOUT = 30000
WAIT_TIME = 1.5
MAX_RETRIES = 1
RETRY_BASE_DELAY = 1.2

CF_MODE = "skip"
CF_MANUAL_MAX_WAIT = 20

MIN_AGE = 55
MAX_AGE = 75
ONLY_WIRELESS = False

MAX_CANDIDATES_PER_QUERY = 20
MAX_PAGES = 1

USE_PERSISTENT_PROFILE = False
CHROME_USER_DATA_DIR = r"C:\Users\maoni\AppData\Local\Google\Chrome\User Data"
CHROME_PROFILE_DIRECTORY = "Profile 1"
CHROME_CHANNEL = "chrome"

LOG_LEVEL = "INFO"
