# 爬虫配置

# 搜索参数
MIN_AGE = 53
MAX_AGE = 75
ONLY_WIRELESS = True  # 仅保留 Wireless 电话

# URL 配置
BASE_URL = "https://www.peoplesearchnow.com"
SEARCH_URL = "https://www.peoplesearchnow.com/person"

# 浏览器配置
HEADLESS = False  # 是否无头模式
TIMEOUT = 30000  # 页面加载超时 (ms)
WAIT_TIME = 2    # 页面加载后等待时间 (s)

# 日志配置
LOG_LEVEL = "INFO"
LOG_FILE = "logs/app.log"

# 输入输出
INPUT_FILE = "data/names.txt"
OUTPUT_FILE = "data/results.csv"
