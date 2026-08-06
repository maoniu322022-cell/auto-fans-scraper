# -*- coding: utf-8 -*-
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RESULTS_DIR = BASE_DIR / "results"

INPUT_FILE = str(DATA_DIR / "names.txt")
RESULTS_FILE = str(RESULTS_DIR / "phones.csv")
OUTPUT_FILE = RESULTS_FILE  # 兼容旧代码
LOG_FILE = str(BASE_DIR / "run.log")

BASE_URL = "https://www.peoplesearchnow.com"
SEARCH_URL = f"{BASE_URL}/person"

# 浏览器与连接
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_USER_DATA_DIR = r"C:\chrome-debug-profile"
CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
AUTO_START_CHROME = True
CHROME_START_WAIT_SEC = 20

# 页面等待与重试
TIMEOUT = 30000
WAIT_TIME = 0.2
MAX_RETRIES = 1
RETRY_BASE_DELAY = 1.0

# 抓取范围
MIN_AGE = 55
MAX_AGE = 75
MAX_CANDIDATES_PER_QUERY = 30
MAX_PAGES = 0  # 0 = 不限页数

# 速度与节流（稳中求快）
WAIT_TIME = 0.8

PAGE_COOLDOWN_MIN = 1.2
PAGE_COOLDOWN_MAX = 2.2

DETAIL_COOLDOWN_MIN = 2.0
DETAIL_COOLDOWN_MAX = 3.5

NAME_COOLDOWN_MIN = 4.0
NAME_COOLDOWN_MAX = 7.0

RESULT_FLUSH_SIZE = 10
MAX_RETRIES = 2
# 降低无关资源加载；不会阻止 JavaScript、样式或 API 请求。
BLOCK_HEAVY_RESOURCES = True
BLOCKED_RESOURCE_TYPES = ("image", "media", "font")

# 人工验证：检测到后暂停，需在浏览器完成验证并回终端按回车。
MANUAL_CHALLENGE_TIMEOUT = 1800

# 输出。每累计 30 个新号码写入一次；正常结束或 Ctrl+C 时也会写入。
RESULT_FLUSH_SIZE = 30

# 每次启动前清空上次结果（results/phones.csv）
CLEAR_RESULTS_ON_START = True

LOG_LEVEL = "INFO"