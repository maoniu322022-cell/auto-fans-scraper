# ===== 你原有配置保留，这里给出一份可直接使用的完整示例 =====

# 基础
BASE_URL = "https://www.fastpeoplesearch.com"
SEARCH_URL = f"{BASE_URL}/name"

# 浏览器
HEADLESS = True
TIMEOUT = 30000
WAIT_TIME = 1.0

# 重试
MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.2

# 年龄过滤
MIN_AGE = 45
MAX_AGE = 55

# 电话过滤
ONLY_WIRELESS = True

# Cloudflare 处理策略: manual / skip / retry
CF_MODE = "manual"

# 输出
OUTPUT_CSV = "data/results.csv"

# 性能优化
DETAIL_CONCURRENCY = 4          # 详情页并发数（建议 3~5）
MAX_CANDIDATES_PER_QUERY = 30   # 每次查询最多处理多少候选