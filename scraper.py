import logging
import random
import re
import time
import csv
from typing import List, Dict, Optional
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

try:
    import cloudscraper
    CLOUDSCRAPER_AVAILABLE = True
except ImportError:
    CLOUDSCRAPER_AVAILABLE = False

import config

logger = logging.getLogger(__name__)

# ─── 去重键 ──────────────────────────────────────────────────────────────────
DEDUP_FIELDS = ("name", "age", "location", "phone")


def _dedup_key(record: Dict) -> tuple:
    return tuple(str(record.get(f, "")).strip() for f in DEDUP_FIELDS)


# ─── 统一重试包装 ─────────────────────────────────────────────────────────────

def retry_with_backoff(func, *, max_retries: int = None, base_delay: float = None,
                       label: str = "操作"):
    """
    对 func() 做指数退避重试（含随机抖动）。

    参数:
        func        – 无参可调用，直接调用执行目标动作
        max_retries – 最大重试次数，默认读 config.MAX_RETRIES
        base_delay  – 起始退避秒数，默认读 config.RETRY_BASE_DELAY
        label       – 日志描述
    返回:
        func() 的返回值
    抛出:
        最后一次异常（所有重试耗尽后）
    """
    if max_retries is None:
        max_retries = config.MAX_RETRIES
    if base_delay is None:
        base_delay = config.RETRY_BASE_DELAY

    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as exc:
            if attempt >= max_retries:
                logger.error(
                    f"[{label}] 全部 {max_retries} 次重试失败，最终错误: {exc}"
                )
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            logger.warning(
                f"[{label}] 第 {attempt + 1}/{max_retries} 次重试，等待 {delay:.1f}s，错误: {exc}"
            )
            time.sleep(delay)


class PeopleSearchScraper:
    """人物搜索爬虫"""

    def __init__(self):
        self.browser = None
        self.page = None
        self.playwright = None
        self.context = None
        self.scraper = None

        if CLOUDSCRAPER_AVAILABLE:
            try:
                self.scraper = cloudscraper.create_scraper()
                logger.info("✓ cloudscraper 已初始化")
            except Exception as e:
                logger.warning(f"⚠️ cloudscraper 初始化失败: {e}")

    def init_browser(self):
        """初始化浏览器"""
        try:
            self.playwright = sync_playwright().start()

            self.browser = self.playwright.chromium.launch(
                headless=config.HEADLESS,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox'
                ]
            )

            self.context = self.browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )

            self.page = self.context.new_page()
            self.page.set_default_timeout(config.TIMEOUT)

            logger.info("✓ 浏览器已启动")
        except Exception as e:
            logger.error(f"浏览器初始化失败: {e}")
            raise

    def close(self):
        """关闭浏览器"""
        try:
            if self.page:
                self.page.close()
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
            logger.info("✓ 浏览器已关闭")
        except Exception as e:
            logger.debug(f"关闭浏览器时出错: {e}")

    def _has_search_results(self, html: str) -> bool:
        """检查是否有搜索结果"""
        return (
            "Approximate Age" in html or
            "Current Location" in html or
            "people in" in html.lower()
        )

    def _is_cloudflare_challenge(self, page) -> bool:
        """检查是否是 Cloudflare 挑战页面"""
        try:
            content = page.content()
            return (
                "Cloudflare" in content and
                ("challenge" in content.lower() or
                 "security check" in content.lower() or
                 "正在进行安全验证" in content)
            )
        except Exception:
            return False

    def _handle_cloudflare_challenge(self, page) -> bool:
        """
        根据 CF_MODE 处理 Cloudflare 挑战，不再阻塞 STDIN。

        CF_MODE=manual : 等待有限时间让浏览器自动通过，超时后继续（不调用 input()）
        CF_MODE=skip   : 记录日志后直接返回 False，由调用方跳过该记录
        CF_MODE=retry  : 由调用方的 retry_with_backoff 处理重试；此处仅等待一次
        """
        cf_mode = config.CF_MODE

        logger.info(f"⏳ 检测到 Cloudflare 挑战，CF_MODE={cf_mode}")

        if cf_mode == "skip":
            logger.warning("⚠️ CF_MODE=skip，跳过此记录")
            return False

        # manual / retry: 先等待自动通过
        logger.info("正在等待 Cloudflare 自动处理 (30秒)...")
        try:
            page.wait_for_navigation(timeout=30000, wait_until="networkidle")
            logger.info("✓ Cloudflare 自动处理完成")
            return True
        except Exception:
            pass

        # 尝试点击验证复选框
        logger.info("尝试点击验证框...")
        selectors = [
            'input[type="checkbox"]',
            'label input[type="checkbox"]',
            '[aria-label*="checkbox"]',
            '.cf-checkbox',
            '#challenge-form input'
        ]
        for selector in selectors:
            try:
                elements = page.query_selector_all(selector)
                for elem in elements:
                    try:
                        elem.scroll_into_view_if_needed()
                        time.sleep(0.5)
                        elem.click()
                        logger.info("✓ 已点击验证框")
                        time.sleep(2)
                        break
                    except Exception as e:
                        logger.debug(f"点击失败: {e}")
            except Exception:
                pass

        # 等待验证完成（有限时间）
        wait_sec = 15
        logger.info(f"⏳ 等待验证完成 ({wait_sec}秒)...")
        time.sleep(wait_sec)

        if self._is_cloudflare_challenge(page):
            logger.warning(
                f"⚠️ Cloudflare 验证未完成 (CF_MODE={cf_mode})，继续处理（可能失败）"
            )
            return False

        logger.info("✓ Cloudflare 验证已完成")
        return True

    def search_by_name(self, name: str) -> List[Dict]:
        """按名字搜索"""
        results = []

        try:
            search_url = f"{config.SEARCH_URL}/{name.replace(' ', '-').lower()}"
            logger.info(f"正在搜索: {name}")
            logger.info(f"访问 URL: {search_url}")

            # 优先使用 cloudscraper（带重试）
            if self.scraper:
                logger.info("使用 cloudscraper 请求...")
                try:
                    html = retry_with_backoff(
                        lambda: self._fetch_with_cloudscraper(search_url),
                        label=f"cloudscraper/{name}"
                    )
                    if html and self._has_search_results(html):
                        logger.info("✓ 获取到搜索结果")
                        results = self._extract_results_from_html(html, name)
                        if results:
                            return results
                except Exception:
                    logger.info("cloudscraper 全部重试失败，降级到 Playwright")

            # 降级到浏览器（带重试）
            logger.info("使用 Playwright 请求...")
            if not self.page:
                self.init_browser()

            # 访问页面（带重试）
            try:
                retry_with_backoff(
                    lambda: self.page.goto(search_url, wait_until="domcontentloaded"),
                    label=f"page.goto/{name}"
                )
            except Exception as e:
                logger.error(f"页面加载失败（已重试）: {e}")
                return []

            time.sleep(2)

            # 处理 Cloudflare 挑战
            if self._is_cloudflare_challenge(self.page):
                cf_ok = self._handle_cloudflare_challenge(self.page)
                if not cf_ok and config.CF_MODE == "skip":
                    return []
                time.sleep(2)

            # 等待搜索结果加载
            try:
                self.page.wait_for_selector(
                    'div:has-text("Approximate Age"), div:has-text("Current Location")',
                    timeout=10000
                )
            except Exception:
                logger.debug("未找到结果选择器，继续处理...")

            time.sleep(config.WAIT_TIME)

            # 从 DOM 提取结果
            results = self._extract_results_from_dom(name)

            return results

        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return []

    def _fetch_with_cloudscraper(self, url: str) -> Optional[str]:
        """使用 cloudscraper 获取页面（失败时抛出异常供 retry 捕获）"""
        response = self.scraper.get(url, timeout=30)
        if response.status_code == 200:
            logger.info(f"✓ 请求成功 (状态码: {response.status_code})")
            return response.text
        raise RuntimeError(f"HTTP {response.status_code}")

    def _extract_results_from_html(self, html: str, search_name: str) -> List[Dict]:
        """从 HTML 提取结果"""
        results = []

        if not self._has_search_results(html):
            return []

        pattern = r'([A-Z][a-z]+ [A-Z][a-z]+).*?Approximate Age[:=\s]+(\d+)'
        matches = re.findall(pattern, html, re.IGNORECASE)

        for name, age_str in matches:
            try:
                age = int(age_str)
                if config.MIN_AGE <= age <= config.MAX_AGE:
                    location = self._extract_location(html, name)
                    results.append({
                        "name": name.strip(),
                        "age": age,
                        "location": location,
                        "phone": "待获取"
                    })
            except Exception:
                continue

        return results

    def _extract_results_from_dom(self, search_name: str) -> List[Dict]:
        """从 DOM 提取结果"""
        results = []

        try:
            logger.info("开始从页面提取所有符合条件的人员...")

            page_text = self.page.evaluate('document.body.innerText')
            lines = page_text.split('\n')
            seen_keys: set = set()

            for i, line in enumerate(lines):
                line = line.strip()

                age_match = re.search(r'Approximate Age[:=\s]*(\d+)', line, re.IGNORECASE)
                if not age_match:
                    continue

                age = int(age_match.group(1))
                if not (config.MIN_AGE <= age <= config.MAX_AGE):
                    continue

                # 回溯找名字
                person_name = "Unknown"
                for j in range(i - 1, max(0, i - 5), -1):
                    prev_line = lines[j].strip()
                    if prev_line and re.match(r'^[A-Z][a-z]+ [A-Z][a-z]+', prev_line):
                        person_name = prev_line
                        break

                # 查找位置信息
                location = "Unknown"
                for j in range(i + 1, min(len(lines), i + 5)):
                    next_line = lines[j].strip()
                    if "Current Location" in next_line:
                        loc_match = re.search(
                            r'Current Location[:=\s]*([^\n]+)', next_line, re.IGNORECASE
                        )
                        if loc_match:
                            location = loc_match.group(1).strip()
                        break

                # 查找电话信息
                phone = "未获取"
                if config.ONLY_WIRELESS:
                    for j in range(i + 1, min(len(lines), i + 10)):
                        next_line = lines[j].strip()
                        if "Wireless" in next_line or "Mobile" in next_line:
                            phone_match = re.search(
                                r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', next_line
                            )
                            if phone_match:
                                phone = phone_match.group(0).strip()
                                break

                result = {
                    "name": person_name,
                    "age": age,
                    "location": location,
                    "phone": phone
                }

                key = _dedup_key(result)
                if key not in seen_keys:
                    seen_keys.add(key)
                    results.append(result)
                    logger.info(
                        f"✓ 保存: {person_name} | 年龄: {age} | 位置: {location} | 电话: {phone}"
                    )

            logger.info(f"✓ 共从页面提取 {len(results)} 条符合条件的记录")
            return results

        except Exception as e:
            logger.error(f"提取结果失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return []

    def _get_phones_from_detail_page(self, button) -> List[str]:
        """点击按钮获取详情页电话"""
        phones = []

        try:
            logger.debug("点击 View All Info 按钮...")

            with self.page.context.expect_page() as new_page_info:
                button.click()

            detail_page = new_page_info.value
            time.sleep(config.WAIT_TIME)

            if self._is_cloudflare_challenge(detail_page):
                logger.info("⚠️ 详情页需要 Cloudflare 验证")
                self._handle_cloudflare_challenge(detail_page)
                time.sleep(2)

            try:
                detail_page.wait_for_selector(
                    "span:has-text('Wireless'), span:has-text('Mobile')",
                    timeout=10000
                )
            except Exception:
                pass

            phones = self._extract_phones_from_page(detail_page)
            detail_page.close()

        except Exception as e:
            logger.debug(f"获取详情页失败: {e}")

        return phones

    def _extract_phones_from_page(self, page) -> List[str]:
        """从页面提取电话号码"""
        phones = []

        try:
            phone_elements = page.query_selector_all("span, div, td")

            for elem in phone_elements:
                try:
                    elem_text = elem.inner_text()

                    if "Wireless" not in elem_text and "Mobile" not in elem_text:
                        continue

                    phone_match = re.search(
                        r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}',
                        elem_text
                    )
                    if phone_match:
                        phone = phone_match.group(0).strip()
                        if phone not in phones:
                            phones.append(phone)
                            logger.info(f"  ✓ 找到: {phone}")

                except Exception:
                    continue

            return phones
        except Exception as e:
            logger.debug(f"提取电话失败: {e}")
            return []

    def _extract_location(self, html: str, name: str) -> str:
        """提取位置"""
        try:
            pattern = f"{name}.*?Current Location[:=\\s]+([^<\\n]+)"
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                return re.sub(r'<[^>]+>', '', match.group(1)).strip()
        except Exception:
            pass
        return "Unknown"

    def save_results(self, results: List[Dict], filename: str):
        """
        增量写入 CSV，并对全局结果去重。

        去重键：(name, age, location, phone)
        策略：
          1. 若文件已存在，读取已有记录提取去重键集合。
          2. 合并新旧记录，仅追加尚未出现的新记录。
          3. 以安全的读-去重-写回方式更新文件（覆盖写，但内容已去重合并）。
        """
        if not results:
            logger.warning("无结果保存")
            return

        try:
            output_path = Path(filename)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            fieldnames = ['name', 'age', 'location', 'phone']
            existing_records: List[Dict] = []
            existing_keys: set = set()

            # 读取已有记录
            if output_path.exists():
                try:
                    with open(output_path, 'r', newline='', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            existing_records.append(row)
                            existing_keys.add(_dedup_key(row))
                    logger.info(f"已有记录: {len(existing_records)} 条")
                except Exception as e:
                    logger.warning(f"读取已有文件失败，将覆盖写入: {e}")

            # 筛选新增（未重复）记录
            new_records = []
            for r in results:
                key = _dedup_key(r)
                if key not in existing_keys:
                    existing_keys.add(key)
                    new_records.append(r)

            if not new_records:
                logger.info(f"全部 {len(results)} 条记录已存在，无需写入")
                return

            # 合并写回
            all_records = existing_records + new_records
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(all_records)

            logger.info(
                f"✓ 新增 {len(new_records)} 条，总计 {len(all_records)} 条，已保存到 {filename}"
            )
        except Exception as e:
            logger.error(f"保存失败: {e}")
