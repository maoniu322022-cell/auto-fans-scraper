# -*- coding: utf-8 -*-
import csv
import logging
import random
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import cloudscraper
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import config

logger = logging.getLogger("scraper")


class PeopleSearchScraper:
    def __init__(self):
        self.base_url = config.BASE_URL.rstrip("/")
        self.search_url = config.SEARCH_URL
        self.timeout = getattr(config, "TIMEOUT", 30000)
        self.wait_time = getattr(config, "WAIT_TIME", 1.0)
        self.max_retries = getattr(config, "MAX_RETRIES", 2)
        self.retry_base_delay = getattr(config, "RETRY_BASE_DELAY", 1.2)

        self.min_age = getattr(config, "MIN_AGE", 55)
        self.max_age = getattr(config, "MAX_AGE", 75)
        self.only_wireless = getattr(config, "ONLY_WIRELESS", False)

        self.cf_mode = getattr(config, "CF_MODE", "skip")  # skip/manual/retry
        self.cf_manual_max_wait = getattr(config, "CF_MANUAL_MAX_WAIT", 1200)

        self.max_candidates = getattr(config, "MAX_CANDIDATES_PER_QUERY", 30)
        self.max_pages = getattr(config, "MAX_PAGES", 0)  # 0 = unlimited

        self.browser = None
        self.context = None
        self.page = None
        self._pw = None

    # -----------------------
    # Browser lifecycle
    # -----------------------
    def start(self):
        if self._pw and self.context and self.page and not self.page.is_closed():
            return

        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=getattr(config, "HEADLESS", False))
        self.context = self.browser.new_context(
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        self.page = self.context.new_page()
        logger.info("browser started")

    def _ensure_page(self):
        if not self._pw or not self.context or not self.page or self.page.is_closed():
            self.start()
        self._cleanup_extra_pages()

    def _cleanup_extra_pages(self):
        if not self.context:
            return
        try:
            pages = self.context.pages
            # keep self.page only; close others to prevent about:blank accumulation
            for p in list(pages):
                if p is not self.page:
                    try:
                        p.close()
                    except Exception:
                        pass
        except Exception:
            pass

    def close(self):
        try:
            if self.context:
                for p in list(self.context.pages):
                    try:
                        p.close()
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            if self.context:
                self.context.close()
        except Exception:
            pass

        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass

        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

        self.browser = None
        self.context = None
        self.page = None
        self._pw = None
        logger.info("browser closed")

    # -----------------------
    # Public API
    # -----------------------
    def search_person(self, full_name: str) -> List[Dict]:
        full_name = (full_name or "").strip()
        if not full_name:
            return []

        logger.info(f"searching: {full_name}")
        person_url = f"{self.search_url}/{self._slug_name(full_name)}"
        logger.info(f"url: {person_url}")

        # 1) Try cloudscraper first
        html = self._fetch_with_cloudscraper(person_url, full_name)

        # 2) Parse with regex quickly
        if html:
            results = self._extract_candidates_from_html(html, full_name, person_url)
            if results:
                logger.info(f"[cloudscraper/{full_name}] candidates={len(results)}")
                return results

        # 3) Fallback to Playwright
        logger.info("cloudscraper failed, fallback to playwright")
        results = self._fetch_with_playwright(full_name, person_url)
        return results

    # 兼容 main.py 旧调用
    def search_by_name(self, full_name: str) -> List[Dict]:
        return self.search_person(full_name)

    # -----------------------
    # HTTP path: cloudscraper
    # -----------------------
    def _fetch_with_cloudscraper(self, url: str, full_name: str) -> Optional[str]:
        logger.info("using cloudscraper...")
        s = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
        for i in range(1, self.max_retries + 1):
            try:
                r = s.get(url, timeout=20)
                if r.status_code == 200 and r.text:
                    return r.text
                raise RuntimeError(f"HTTP {r.status_code}")
            except Exception as e:
                if i >= self.max_retries:
                    logger.error(f"[cloudscraper/{full_name}] all {self.max_retries} retries failed: {e}")
                    return None
                delay = self.retry_base_delay * (2 ** (i - 1)) + random.uniform(0.2, 0.9)
                logger.warning(f"[cloudscraper/{full_name}] retry {i}/{self.max_retries} after {delay:.1f}s, error: {e}")
                time.sleep(delay)
        return None

    # -----------------------
    # Browser path: playwright
    # -----------------------
    def _fetch_with_playwright(self, full_name: str, url: str) -> List[Dict]:
        self._ensure_page()

        # Robust goto with retry
        ok = self._goto_with_retry(self.page, url, f"list/{full_name}")
        if not ok:
            self._cleanup_extra_pages()
            return []

        # If blocked by Cloudflare or 1015, handle it
        if self._is_cloudflare_challenge(self.page) or self._is_rate_limited_1015(self.page):
            logger.info("cloudflare/1015 detected")
            if not self._handle_blocking_challenge(self.page, f"list/{full_name}"):
                logger.warning("blocking page unresolved, skip this record")
                self._cleanup_extra_pages()
                return []

        # parse first page + pagination
        candidates = self._extract_candidates_from_page(self.page, full_name)
        all_candidates = list(candidates)
        seen_keys = {self._candidate_key(x) for x in all_candidates}

        page_num = 1
        while True:
            if self.max_pages > 0 and page_num >= self.max_pages:
                break

            next_btn = self._find_next_button(self.page)
            if not next_btn:
                break

            try:
                next_btn.click(timeout=5000)
                self.page.wait_for_load_state("domcontentloaded", timeout=self.timeout)
                time.sleep(self.wait_time)
            except Exception:
                break

            if self._is_cloudflare_challenge(self.page) or self._is_rate_limited_1015(self.page):
                logger.info("cloudflare/1015 detected on next page")
                if not self._handle_blocking_challenge(self.page, f"list-page{page_num+1}/{full_name}"):
                    logger.warning("blocking unresolved on paginated page, stop pagination")
                    break

            page_items = self._extract_candidates_from_page(self.page, full_name)
            for item in page_items:
                k = self._candidate_key(item)
                if k not in seen_keys:
                    seen_keys.add(k)
                    all_candidates.append(item)

            page_num += 1

        if self.max_candidates and len(all_candidates) > self.max_candidates:
            all_candidates = all_candidates[: self.max_candidates]

        # 低风控模式：不抓详情页电话（最容易触发风控）
        enriched = all_candidates

        # filter by age/phone type if needed
        filtered = [x for x in enriched if self._age_ok(x.get("age"))]
        if self.only_wireless:
            filtered = [x for x in filtered if self._looks_wireless(x.get("phone", ""))]

        self._cleanup_extra_pages()
        logger.info(f"[✓] matched {len(filtered)} result(s)")
        return filtered

    # -----------------------
    # Challenge handling
    # -----------------------
    def _handle_blocking_challenge(self, page, tag: str) -> bool:
        mode = (self.cf_mode or "skip").lower().strip()

        if mode == "skip":
            logger.warning("CF_MODE=skip, skip this record")
            return False

        if mode == "retry":
            logger.info("CF_MODE=retry, sleeping then retry once")
            time.sleep(8)
            try:
                page.reload(wait_until="domcontentloaded", timeout=self.timeout)
                time.sleep(self.wait_time)
            except Exception:
                pass
            return not (self._is_cloudflare_challenge(page) or self._is_rate_limited_1015(page))

        # manual
        logger.warning(
            f"[{tag}] 检测到 Cloudflare/1015。请现在手动处理（可切换VPN、过验证），"
            f"完成后程序会自动继续。最长等待 {self.cf_manual_max_wait}s"
        )
        start = time.time()
        last_log_sec = -1

        while True:
            elapsed = int(time.time() - start)
            if elapsed > int(self.cf_manual_max_wait):
                logger.warning(f"[{tag}] 等待人工处理超时（{self.cf_manual_max_wait}s），跳过")
                return False

            blocked = self._is_cloudflare_challenge(page) or self._is_rate_limited_1015(page)
            if not blocked:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
                logger.info(f"[{tag}] ✓ 阻断已解除，继续")
                return True

            # 每10秒打一条日志，避免刷屏
            if elapsed % 10 == 0 and elapsed != last_log_sec:
                last_log_sec = elapsed
                logger.info(f"[{tag}] 仍在等待人工处理（可切换VPN后重试）... {elapsed}s/{self.cf_manual_max_wait}s")

            time.sleep(2)

    def _is_cloudflare_challenge(self, page) -> bool:
        try:
            html = page.content().lower()
            title = (page.title() or "").lower()
            url = (page.url or "").lower()
            keys = [
                "checking your browser",
                "cf-challenge",
                "cloudflare",
                "attention required",
                "/cdn-cgi/challenge-platform/",
                "just a moment",
            ]
            if any(k in html for k in keys):
                return True
            if any(k in title for k in keys):
                return True
            if "/cdn-cgi/" in url:
                return True
            return False
        except Exception:
            return False

    def _is_rate_limited_1015(self, page) -> bool:
        try:
            content = page.content()
            low = content.lower()
            return (
                "error 1015" in low
                or "you are being rate limited" in low
                or "rate limited" in low
                or "访问受限" in content
                or "请求过多" in content
            )
        except Exception:
            return False

    # -----------------------
    # Parsing candidates
    # -----------------------
    def _extract_candidates_from_html(self, html: str, full_name: str, base_url: str) -> List[Dict]:
        cards = re.findall(r'href="(/person/[^"]+)"', html, flags=re.I)
        uniq = []
        seen = set()
        for p in cards:
            if p not in seen:
                seen.add(p)
                uniq.append(p)

        out = []
        for p in uniq[: self.max_candidates]:
            detail = self.base_url + p
            out.append(
                {
                    "query_name": full_name,
                    "name": self._name_from_slug(p),
                    "age": None,
                    "location": "",
                    "phone": "",
                    "detail_url": detail,
                    "source_url": base_url,
                }
            )
        return out

    def _extract_candidates_from_page(self, page, full_name: str) -> List[Dict]:
        out: List[Dict] = []

        links = page.locator('a[href^="/person/"], a[href*="/person/"]').all()
        seen: Set[str] = set()

        for a in links:
            try:
                href = a.get_attribute("href") or ""
                if not href or "/person/" not in href:
                    continue

                if href.startswith("/"):
                    detail_url = self.base_url + href
                elif href.startswith("http"):
                    detail_url = href
                else:
                    detail_url = f"{self.base_url}/{href.lstrip('/')}"

                if detail_url in seen:
                    continue
                seen.add(detail_url)

                card_text = ""
                try:
                    card_text = a.locator("xpath=ancestor::*[self::div or self::li][1]").inner_text(timeout=500)
                except Exception:
                    try:
                        card_text = a.inner_text(timeout=500)
                    except Exception:
                        card_text = ""

                name = self._extract_name_from_text_or_url(card_text, detail_url)
                age = self._extract_age(card_text)
                loc = self._extract_location(card_text)

                out.append(
                    {
                        "query_name": full_name,
                        "name": name,
                        "age": age,
                        "location": loc,
                        "phone": "",
                        "detail_url": detail_url,
                        "source_url": page.url,
                    }
                )
            except Exception:
                continue

        return out[: self.max_candidates]

    # -----------------------
    # Helpers
    # -----------------------
    def _goto_with_retry(self, page, url: str, tag: str) -> bool:
        for i in range(1, self.max_retries + 1):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
                time.sleep(self.wait_time)
                return True
            except PlaywrightTimeoutError as e:
                if i >= self.max_retries:
                    logger.error(f"[{tag}] goto timeout after retries: {e}")
                    return False
                delay = self.retry_base_delay * (2 ** (i - 1)) + random.uniform(0.3, 1.0)
                logger.warning(f"[{tag}] goto retry {i}/{self.max_retries} after {delay:.1f}s")
                time.sleep(delay)
            except Exception as e:
                if i >= self.max_retries:
                    logger.error(f"[{tag}] goto failed: {e}")
                    return False
                delay = self.retry_base_delay * (2 ** (i - 1)) + random.uniform(0.3, 1.0)
                logger.warning(f"[{tag}] goto retry {i}/{self.max_retries} after {delay:.1f}s, err={e}")
                time.sleep(delay)
        return False

    def _find_next_button(self, page):
        selectors = [
            'a[rel="next"]',
            'a:has-text("Next")',
            'button:has-text("Next")',
            'a:has-text("›")',
            'a:has-text(">")',
        ]
        for s in selectors:
            try:
                el = page.locator(s).first
                if el.count() > 0 and el.is_visible():
                    return el
            except Exception:
                continue
        return None

    def _extract_name_from_text_or_url(self, text: str, detail_url: str) -> str:
        txt = (text or "").strip()
        m = re.search(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})", txt)
        if m:
            return m.group(1).strip()
        return self._name_from_slug(detail_url)

    def _name_from_slug(self, url_or_path: str) -> str:
        s = url_or_path.split("/person/")[-1].strip("/")
        s = s.split("?")[0]
        s = s.replace("-", " ")
        return " ".join([w.capitalize() for w in s.split() if w])

    def _slug_name(self, name: str) -> str:
        name = re.sub(r"\s+", "-", name.strip().lower())
        name = re.sub(r"[^a-z0-9\-]", "", name)
        return name

    def _extract_age(self, text: str) -> Optional[int]:
        if not text:
            return None
        m = re.search(r"\b(?:age)\s*[:\-]?\s*(\d{1,3})\b", text, re.I)
        if not m:
            m = re.search(r"\b(\d{2})\s*(?:years old|yrs old|yo)\b", text, re.I)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None
        return None

    def _extract_location(self, text: str) -> str:
        if not text:
            return ""
        m = re.search(r"\b([A-Z][a-z]+,\s*[A-Z]{2})\b", text)
        return m.group(1) if m else ""

    def _age_ok(self, age: Optional[int]) -> bool:
        if age is None:
            return True
        return self.min_age <= age <= self.max_age

    def _looks_wireless(self, phone: str) -> bool:
        return bool(phone)

    def _candidate_key(self, x: Dict) -> Tuple[str, str, str, str]:
        return (
            (x.get("name") or "").strip().lower(),
            str(x.get("age") or "").strip().lower(),
            (x.get("location") or "").strip().lower(),
            (x.get("phone") or "").strip().lower(),
        )


def append_results_dedup(csv_path: str, rows: List[Dict]):
    if not rows:
        return
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["query_name", "name", "age", "location", "phone", "detail_url", "source_url"]

    existed = set()
    if path.exists():
        try:
            with path.open("r", newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    k = (
                        (r.get("name") or "").strip().lower(),
                        str(r.get("age") or "").strip().lower(),
                        (r.get("location") or "").strip().lower(),
                        (r.get("phone") or "").strip().lower(),
                    )
                    existed.add(k)
        except Exception:
            pass

    to_write = []
    for r in rows:
        k = (
            (r.get("name") or "").strip().lower(),
            str(r.get("age") or "").strip().lower(),
            (r.get("location") or "").strip().lower(),
            (r.get("phone") or "").strip().lower(),
        )
        if k in existed:
            continue
        existed.add(k)
        to_write.append(r)

    if not to_write:
        return

    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()
        for r in to_write:
            w.writerow({k: r.get(k, "") for k in fieldnames})
