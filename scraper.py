import logging
import random
import re
import time
import csv
from typing import List, Dict, Optional
from pathlib import Path
from urllib.parse import urljoin, urlparse
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
PHONE_PATTERN = re.compile(r'(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}')


def _dedup_key(record: Dict) -> tuple:
    return tuple(str(record.get(f, "")).strip() for f in DEDUP_FIELDS)


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return (raw or "").strip()


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
            candidates = self._extract_candidates_from_dom(search_name)
            logger.info(f"✓ 列表页命中 {len(candidates)} 条候选记录，开始详情页提取电话...")

            seen_keys: set = set()
            for candidate in candidates:
                person_name = candidate["name"]
                age = candidate["age"]
                location = candidate["location"]
                detail_url = candidate.get("detail_url", "")

                # 强制从详情页纠正姓名，避免 View All Info / Uxxxx slug
                fixed_name = self._get_name_from_detail_page(detail_url)
                if fixed_name:
                    person_name = fixed_name

                phones = self._get_phones_from_detail_page(detail_url, person_name)
                logger.info(f"详情提取: {person_name} | 电话数量: {len(phones)}")

                if not phones:
                    phones = ["未获取"]

                for phone in phones:
                    result = {
                        "name": person_name,
                        "age": age,
                        "location": location,
                        "phone": phone
                    }
                    key = _dedup_key(result)
                    if key in seen_keys:
                        continue
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

    def _extract_candidates_from_dom(self, search_name: str) -> List[Dict]:
        """从列表页提取候选人员（含详情链接）"""
        candidates: List[Dict] = []
        seen_urls: set = set()

        try:
            raw_candidates = self.page.evaluate("""
() => {
  const links = Array.from(document.querySelectorAll('a'))
    .filter(a => /view\\s+all\\s+info/i.test((a.textContent || '').trim()));

  return links.map((link) => {
    let card = link.closest('article, li, section, div');
    let probe = card;
    for (let i = 0; i < 6 && probe; i++) {
      const txt = (probe.innerText || '').trim();
      if (/Approximate\\s+Age/i.test(txt) || /Current\\s+Location/i.test(txt)) {
        card = probe;
        break;
      }
      probe = probe.parentElement;
    }

    const cardText = card ? (card.innerText || '') : '';
    const ageMatch = cardText.match(/Approximate\\s+Age[:=\\s]*(\\d+)/i);
    const locationMatch = cardText.match(/Current\\s+Location[:=\\s]*([^\\n]+)/i);
    const nameNodes = card
      ? Array.from(card.querySelectorAll('h1, h2, h3, h4, [data-testid*="name"], a[href*="/person/"]'))
      : [];

    let name = '';
    for (const node of nameNodes) {
      const txt = (node.textContent || '').trim();
      if (!txt) continue;
      if (/view\\s+all\\s+info/i.test(txt)) continue;
      if (/approximate\\s+age|current\\s+location/i.test(txt)) continue;
      name = txt;
      break;
    }

    return {
      name,
      age: ageMatch ? ageMatch[1] : '',
      location: locationMatch ? locationMatch[1].trim() : '',
      detail_url: link.href || ''
    };
  });
}
            """)
        except Exception as e:
            logger.debug(f"列表页 DOM 候选提取失败: {e}")
            raw_candidates = []

        for item in raw_candidates or []:
            try:
                age = int(str(item.get("age", "")).strip())
            except Exception:
                continue

            if not (config.MIN_AGE <= age <= config.MAX_AGE):
                continue

            detail_url = item.get("detail_url", "") or ""
            if detail_url:
                detail_url = urljoin(config.BASE_URL, detail_url)
            if detail_url in seen_urls:
                continue

            person_name = (item.get("name", "") or "").strip()
            # 列表页姓名无效时，用搜索名占位，后续会被详情页强制覆盖
            if (
                not person_name
                or re.search(r'view\s+all', person_name, re.IGNORECASE)
                or re.fullmatch(r"U[a-z0-9]{12,}", person_name or "")
            ):
                person_name = search_name or "Unknown"

            location = (item.get("location", "") or "").strip() or "Unknown"

            candidates.append({
                "name": person_name,
                "age": age,
                "location": location,
                "detail_url": detail_url
            })
            if detail_url:
                seen_urls.add(detail_url)

        return candidates

    def _guess_name_from_detail_url(self, detail_url: str) -> str:
        """从详情页 URL 猜测姓名"""
        if not detail_url:
            return ""
        try:
            path = urlparse(detail_url).path.strip("/")
            slug = path.split("/")[-1]
            if not slug:
                return ""
            slug = re.sub(r'-\d+$', '', slug)
            words = [w for w in slug.split("-") if w and w.lower() not in {"person"}]
            return " ".join(w.capitalize() for w in words)
        except Exception:
            return ""

    def _get_name_from_detail_page(self, detail_url: str) -> str:
        """访问详情页提取姓名（强制用于覆盖列表页错误姓名）"""
        detail_page = None
        try:
            if not detail_url:
                return ""

            detail_page = self.context.new_page()
            retry_with_backoff(
                lambda: detail_page.goto(detail_url, wait_until="domcontentloaded"),
                label=f"name.detail.goto/{detail_url}"
            )
            time.sleep(max(1, config.WAIT_TIME))

            if self._is_cloudflare_challenge(detail_page):
                self._handle_cloudflare_challenge(detail_page)
                time.sleep(2)

            for sel in ["h1", "h2", ".person-name", ".profile-name", ".name"]:
                try:
                    el = detail_page.query_selector(sel)
                    if el:
                        txt = (el.inner_text() or "").strip()
                        txt = re.sub(r"\s+", " ", txt)
                        if (
                            txt
                            and not re.search(r'view\s+all', txt, re.IGNORECASE)
                            and not re.fullmatch(r"U[a-z0-9]{12,}", txt)
                        ):
                            return txt
                except Exception:
                    pass

            try:
                t = (detail_page.title() or "").strip()
                t = re.sub(r"\s*[-|–].*$", "", t).strip()
                t = re.sub(r"\s+", " ", t)
                if (
                    t
                    and not re.search(r'view\s+all', t, re.IGNORECASE)
                    and not re.fullmatch(r"U[a-z0-9]{12,}", t)
                ):
                    return t
            except Exception:
                pass

            return ""
        except Exception:
            return ""
        finally:
            try:
                if detail_page:
                    detail_page.close()
            except Exception:
                pass

    def _get_phones_from_detail_page(self, detail_url: str, person_name: str = "") -> List[str]:
        """访问详情页获取电话"""
        phones = []
        detail_page = None

        try:
            if not detail_url:
                return []
            detail_page = self.context.new_page()
            logger.info(f"  ↳ 访问详情页: {person_name or detail_url}")
            retry_with_backoff(
                lambda: detail_page.goto(detail_url, wait_until="domcontentloaded"),
                label=f"detail.goto/{person_name or detail_url}"
            )
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
                logger.debug("详情页未命中 Wireless/Mobile 显式节点，使用回退提取")

            phones = self._extract_phones_from_page(detail_page)

        except Exception as e:
            logger.warning(f"详情页提取失败: {person_name or detail_url} | 错误: {e}")
        finally:
            try:
                if detail_page:
                    detail_page.close()
            except Exception:
                pass

        return phones

    def _extract_phones_from_page(self, page) -> List[str]:
        """从页面提取电话号码"""
        phones: List[str] = []

        try:
            phone_elements = page.query_selector_all(
                "a[href^='tel:'], li, div, span, td, p"
            )
            seen = set()

            for elem in phone_elements:
                try:
                    elem_text = (elem.inner_text() or "").strip()
                    if not elem_text:
                        continue
                    lowered = elem_text.lower()

                    if config.ONLY_WIRELESS and ("wireless" not in lowered and "mobile" not in lowered):
                        continue

                    phone_match = PHONE_PATTERN.search(elem_text)
                    if not phone_match:
                        continue

                    phone = _normalize_phone(phone_match.group(0))
                    if phone and phone not in seen:
                        seen.add(phone)
                        phones.append(phone)
                        logger.info(f"  ✓ 找到: {phone}")

                except Exception:
                    continue

            # 回退：全文正则（避免节点结构变化时全部漏掉）
            if not phones:
                full_text = page.evaluate("document.body.innerText || ''")
                for line in full_text.splitlines():
                    line_strip = line.strip()
                    if not line_strip:
                        continue
                    lowered = line_strip.lower()
                    if config.ONLY_WIRELESS and ("wireless" not in lowered and "mobile" not in lowered):
                        continue
                    phone_match = PHONE_PATTERN.search(line_strip)
                    if not phone_match:
                        continue
                    phone = _normalize_phone(phone_match.group(0))
                    if phone and phone not in phones:
                        phones.append(phone)

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