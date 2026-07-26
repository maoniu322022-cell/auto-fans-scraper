# config.py
LOG_FILE = "logs/app.log"
LOG_LEVEL = "INFO"

INPUT_FILE = "data/names.txt"
OUTPUT_CSV = "data/results.csv"

BASE_URL = "https://www.peoplesearchnow.com"
SEARCH_URL = f"{BASE_URL}/person"

HEADLESS = False
TIMEOUT = 60000
WAIT_TIME = 1.0

MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.2

MIN_AGE = 55
MAX_AGE = 75
ONLY_WIRELESS = False

# Cloudflare: skip / manual / retry
CF_MODE = "manual"

# manual 模式下，最长等待人工验证秒数（20分钟）
CF_MANUAL_MAX_WAIT = 1200

DETAIL_CONCURRENCY = 1
MAX_CANDIDATES_PER_QUERY = 30

# 0 = 一直翻到最后一页
MAX_PAGES = 0