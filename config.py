# -*- coding: utf-8 -*-
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

INPUT_FILE = str(DATA_DIR / "names.txt")
OUTPUT_FILE = str(DATA_DIR / "results.csv")
LOG_FILE = str(BASE_DIR / "run.log")

BASE_URL = "https://www.peoplesearchnow.com"
SEARCH_URL = f"{BASE_URL}/person"

HEADLESS = False
TIMEOUT = 30000
WAIT_TIME = 1.0
MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.2

# 当前 scraper.py 为“检测到验证即跳过并记录 failed_queue.csv”的模式
CF_MODE = "skip"
CF_MANUAL_MAX_WAIT = 1800

MIN_AGE = 55
MAX_AGE = 75
ONLY_WIRELESS = False

MAX_CANDIDATES_PER_QUERY = 30
MAX_PAGES = 0  # 0 = unlimited

# 失败队列输出（可选，scraper.py 会读取）
FAILED_QUEUE_PATH = str(DATA_DIR / "failed_queue.csv")

# 以下参数在当前“无痕 chromium”实现中不生效，保留不影响
USE_PERSISTENT_PROFILE = False
CHROME_USER_DATA_DIR = r"C:\Users\maoni\AppData\Local\Google\Chrome\User Data"
CHROME_PROFILE_DIRECTORY = "Profile 1"
CHROME_CHANNEL = "chrome"

BROWSER_CHANNEL = "chromium"
BROWSER_EXECUTABLE_CANDIDATES = []

LOG_LEVEL = "INFO"