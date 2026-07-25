# 爬虫配置
# 优先读取 .env 文件中的环境变量，缺失时使用以下默认值。

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv 未安装时退回纯环境变量读取


def _bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes")


def _int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


# 搜索参数
MIN_AGE: int = _int("MIN_AGE", 53)
MAX_AGE: int = _int("MAX_AGE", 75)
ONLY_WIRELESS: bool = _bool("ONLY_WIRELESS", True)

# URL 配置
BASE_URL: str = os.environ.get("BASE_URL", "https://www.peoplesearchnow.com")
SEARCH_URL: str = os.environ.get("SEARCH_URL", "https://www.peoplesearchnow.com/person")

# 浏览器配置
HEADLESS: bool = _bool("HEADLESS", False)
TIMEOUT: int = _int("TIMEOUT", 30000)   # 页面加载超时 (ms)
WAIT_TIME: int = _int("WAIT_TIME", 2)   # 页面加载后等待时间 (s)

# 重试配置
MAX_RETRIES: int = _int("MAX_RETRIES", 3)       # 最大重试次数
RETRY_BASE_DELAY: float = float(os.environ.get("RETRY_BASE_DELAY", "1.0"))  # 初始退避秒数

# Cloudflare 处理策略: manual | skip | retry
CF_MODE: str = os.environ.get("CF_MODE", "skip").strip().lower()
# manual  – 记录日志并等待有限时间，不阻塞 STDIN
# skip    – 跳过该条记录（默认，适合无人值守）
# retry   – 按 MAX_RETRIES 次重试后放弃

# 日志配置
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_FILE: str = os.environ.get("LOG_FILE", "logs/app.log")

# 输入输出
INPUT_FILE: str = os.environ.get("INPUT_FILE", "data/names.txt")
OUTPUT_FILE: str = os.environ.get("OUTPUT_FILE", "data/results.csv")
