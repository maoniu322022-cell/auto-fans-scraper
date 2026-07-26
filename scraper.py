import logging
import random
import re
import time
import csv
from typing import List, Dict, Optional
from pathlib import Path
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

try:
    import cloudscraper
    CLOUDSCRAPER_AVAILABLE = True
except ImportError:
    CLOUDSCRAPER_AVAILABLE = False

import config

logger = logging.getLogger(__name__)

DEDUP_FIELDS = ("name", "age", "location", "phone")
PHONE_PATTERN = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def _dedup_key(record: Dict) -> tuple:
    return tuple(str(record.get(f, "")).strip() for f in DEDUP_FIELDS)


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return (raw or "").strip()


def retry_with_backoff(func, *, max_retries: int = None, base_delay: float = None, label: str = "operation"):
    if max_retries is None:
        max_retries = config.MAX_RETRIES
    if base_delay is None:
        base_delay = config.RETRY_BASE_DELAY

    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as exc:
            if attempt >= max_retries:
                logger.error(f"[{label}] all {max_retries} retries failed: {exc}")
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            logger.warning(f"[{label}] retry {attempt + 1}/{max_retries} after {delay:.1f}s, error: {exc}")
            time.sleep(delay)


class PeopleSearchScraper:
    def __init__(self):
        self.browser = None
        self.page = None
        self.playwright = None
        self.context = None
        self.scraper = None

        if CLOUDSCRAPER_AVAILABLE:
            try:
                self.scraper = cloudscraper.create_scraper()
                logger.info("✓ cloudscraper initialized")
            except Exception as e:
                logger.warning(f"cloudscraper init failed: {e}")

    def init_browser(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=config.HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        self.context = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(config.TIMEOUT)
        logger.info("✓ browser started")

    def close(self):
        try:
            if self.page:
                try:
                    self.page.close()
                except Exception:
                    pass
                self.page = None
            if self.context:
                try:
                    self.context.close()
                except Exception:
                    pass
                self.context = None
            if self.browser:
                try:
                    self.browser.close()
                except Exception:
                    pass
                self.browser = None
        finally:
            if self.playwright:
                try:
                    self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None
            logger.info("✓ browser closed")

    def _has_search_results(self, html: str) -> bool:
        t = (html or "")
        return ("Approximate Age" in t) or ("Current Location" in t) or ("people in" in t.lower())

    def _is_cloudflare_challenge(self, page) -> bool:
        try:
            content = page.content()
            lower = content.lower()
            return (
                "cloudflare" in lower
                and (
                    "challenge" in lower
                    or "security check" in lower
                    or "please verify you are human" in lower
                    or "请验证您是真人" in content
                    or "正在进行安全验证" in content
                    or "checking your browser" in lower
                )
            )
        except Exception:
            return False

    def _handle_cloudflare_challenge(self, page) -> bool:
        cf_mode = getattr(config, "CF_MODE", "skip")
        logger.info(f"cloudflare challenge detected, CF_MODE={cf_mode}")

        if cf_mode == "skip":
            logger.warning("CF_MODE=skip, skip this record")
            return False

        if cf_mode == "manual":
            max_wait = int(getattr(config, "CF_MANUAL_MAX_WAIT", 1200) or 1200)  # 默认20分钟
            check_interval = 2.0
            start = time.time()
            logger.warning(
                f"检测到 Cloudflare 验证，请在浏览器中完成验证。程序将最多等待 {max_wait}s，不会提前关闭页面。"
            )

            while True:
                elapsed = time.time() - start
                if elapsed > max_wait:
                    logger.warning("等待人工验证超时，跳过当前记录")
                    return False

                try:
                    if not self._is_cloudflare_challenge(page):
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=8000)
                        except Exception:
                            pass
                        logger.info("✓ Cloudflare 验证通过，继续")
                        return True
                except Exception as e:
                    logger.debug(f"检查 Cloudflare 状态异常: {e}")

                if int(elapsed) % 10 == 0:
                    logger.info(f"仍在等待人工验证... {int(elapsed)}s/{max_wait}s")

                time.sleep(check_interval)

        # retry/other
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
            if not self._is_cloudflare_challenge(page):
                logger.info("✓ cloudflare auto-resolved")
                return True
        except Exception:
            pass

        time.sleep(5)
        if self._is_cloudflare_challenge(page):
            logger.warning("cloudflare still present")
            return False
        return True

    def search_by_name(self, name: str) -> List[Dict]:
        try:
            slug = name.replace(" ", "-").lower()
            search_url = f"{config.SEARCH_URL}/{slug}"
            logger.info(f"searching: {name}")
            logger.info(f"url: {search_url}")

            if self.scraper:
                logger.info("using cloudscraper...")
                try:
                    html = retry_with_backoff(
                        lambda: self._fetch_with_cloudscraper(search_url),
                        label=f"cloudscraper/{name}",
                    )
                    if html and self._has_search_results(html):
                        rows = self._extract_results_from_html(html, name)
                        if rows:
                            return rows
                except Exception:
                    logger.info("cloudscraper failed, fallback to playwright")

            if not self.page:
                self.init_browser()

            retry_with_backoff(
                lambda: self.page.goto(search_url, wait_until="load"),
                max_retries=3,
                base_delay=2.0,
                label=f"page.goto/{name}",
            )
            time.sleep(max(0.2, float(config.WAIT_TIME)))

            if self._is_cloudflare_challenge(self.page):
                ok = self._handle_cloudflare_challenge(self.page)
                if not ok:
                    return []

            try:
                self.page.wait_for_selector(
                    'a:has-text("View All Info"), div:has-text("Approximate Age"), div:has-text("Current Location")',
                    timeout=5000,
                )
            except Exception:
                pass

            return self._extract_results_from_dom(name)

        except Exception as e:
            logger.error(f"search failed: {e}")
            return []

    def _fetch_with_cloudscraper(self, url: str) -> Optional[str]:
        resp = self.scraper.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.text
        raise RuntimeError(f"HTTP {resp.status_code}")

    def _extract_results_from_html(self, html: str, search_name: str) -> List[Dict]:
        results = []
        if not self._has_search_results(html):
            return results

        pattern = r"Approximate Age[:=\s]+(\d+)"
        for m in re.findall(pattern, html, re.IGNORECASE):
            try:
                age = int(m)
            except Exception:
                continue
            if config.MIN_AGE <= age <= config.MAX_AGE:
                results.append({"name": "", "age": age, "location": "Unknown", "phone": ""})
        return results

    def _extract_results_from_dom(self, search_name: str) -> List[Dict]:
        logger.info("start extracting candidates from list page...")
        candidates = self._extract_candidates_from_dom(search_name)

        max_candidates = int(getattr(config, "MAX_CANDIDATES_PER_QUERY", 30) or 30)
        if max_candidates > 0:
            candidates = candidates[:max_candidates]

        logger.info(f"✓ list page candidates: {len(candidates)}, start detail phone extraction...")

        if not candidates:
            return []

        results: List[Dict] = []
        seen_keys = set()

        for c in candidates:
            detail_url = c.get("detail_url", "")
            age = c.get("age", -1)
            location = c.get("location", "Unknown")

            phones = self._get_phones_from_detail_page(detail_url, "")
            if not phones:
                phones = [""]

            for phone in phones:
                row = {"name": "", "age": age, "location": location, "phone": phone}
                key = _dedup_key(row)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                results.append(row)

        logger.info(f"✓ extracted {len(results)} records")
        return results

    def _extract_candidates_from_dom(self, search_name: str) -> List[Dict]:
        candidates: List[Dict] = []
        seen_urls = set()

        def _collect_current_page() -> List[Dict]:
            try:
                raw = self.page.evaluate(
                    """
() => {
  function txt(el){ return (el && (el.innerText || el.textContent) || '').trim(); }
  function pickCard(a){
    let n = a;
    for(let i=0;i<12 && n;i++){
      const t = txt(n);
      if(/(Approximate\\s+Age|Current\\s+Location|Lives\\s+in|\\bAge\\b)/i.test(t)) return n;
      n = n.parentElement;
    }
    return a.closest('article,li,section,div,tr') || a.parentElement;
  }

  const anchors = Array.from(document.querySelectorAll('a[href]')).filter(a => {
    const t = txt(a);
    const h = a.getAttribute('href') || '';
    return /view\\s+all\\s+info/i.test(t) || /\\/name\\/|\\/person\\/|\\/details\\//i.test(h);
  });

  return anchors.map(a => {
    const card = pickCard(a);
    const t = txt(card);

    const ageM = t.match(/Approximate\\s+Age[:\\s]*([0-9]{1,3})/i) || t.match(/\\bAge[:\\s]*([0-9]{1,3})/i);
    const locM = t.match(/Current\\s+Location[:\\s]*([^\\n\\r]+)/i) || t.match(/Lives\\s+in[:\\s]*([^\\n\\r]+)/i);

    return {
      age: ageM ? ageM[1] : '',
      location: locM ? locM[1].trim() : '',
      detail_url: a.href || ''
    };
  });
}
                    """
                )
                return raw or []
            except Exception as e:
                logger.debug(f"collect current page failed: {e}")
                return []

        def _go_next_page() -> bool:
            try:
                next_exists = self.page.evaluate(
                    """
() => {
  function txt(el){ return (el && (el.innerText || el.textContent) || '').trim().toLowerCase(); }

  let cands = Array.from(document.querySelectorAll('a,button,[role="button"]')).filter(el => {
    const t = txt(el);
    return t === 'next' || t.includes('next') || t === '>' || t === '›' || t === '→';
  });

  const relNext = document.querySelector('a[rel="next"]');
  if (relNext) cands.unshift(relNext);

  for (const el of cands) {
    const ariaDisabled = (el.getAttribute('aria-disabled') || '').toLowerCase();
    const cls = (el.className || '').toString().toLowerCase();
    const disabled = el.disabled || ariaDisabled === 'true' || cls.includes('disabled');
    if (disabled) continue;

    const rect = el.getBoundingClientRect();
    const visible = rect.width > 0 && rect.height > 0;
    if (!visible) continue;

    return true;
  }
  return false;
}
                    """
                )
                if not next_exists:
                    return False

                clicked = self.page.evaluate(
                    """
() => {
  function txt(el){ return (el && (el.innerText || el.textContent) || '').trim().toLowerCase(); }

  let cands = Array.from(document.querySelectorAll('a,button,[role="button"]')).filter(el => {
    const t = txt(el);
    return t === 'next' || t.includes('next') || t === '>' || t === '›' || t === '→';
  });

  const relNext = document.querySelector('a[rel="next"]');
  if (relNext) cands.unshift(relNext);

  for (const el of cands) {
    const ariaDisabled = (el.getAttribute('aria-disabled') || '').toLowerCase();
    const cls = (el.className || '').toString().toLowerCase();
    const disabled = el.disabled || ariaDisabled === 'true' || cls.includes('disabled');
    if (disabled) continue;

    const rect = el.getBoundingClientRect();
    const visible = rect.width > 0 && rect.height > 0;
    if (!visible) continue;

    el.scrollIntoView({block:'center'});
    el.click();
    return true;
  }
  return false;
}
                    """
                )
                if not clicked:
                    return False

                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass

                time.sleep(max(0.4, float(getattr(config, "WAIT_TIME", 1.0))))
                return True

            except Exception as e:
                logger.debug(f"go next page failed: {e}")
                return False

        page_no = 1
        max_pages = int(getattr(config, "MAX_PAGES", 0) or 0)  # 0 = until last page

        while True:
            logger.info(f"collecting candidates from page {page_no}...")
            raw_items = _collect_current_page()

            added_this_page = 0
            for item in raw_items:
                age_raw = str(item.get("age", "")).strip()
                age = int(age_raw) if age_raw.isdigit() else -1
                if age != -1 and not (config.MIN_AGE <= age <= config.MAX_AGE):
                    continue

                detail_url = (item.get("detail_url") or "").strip()
                if detail_url:
                    detail_url = urljoin(config.BASE_URL, detail_url)
                if not detail_url or detail_url in seen_urls:
                    continue

                location = (item.get("location") or "").strip() or "Unknown"
                candidates.append({"name": "", "age": age, "location": location, "detail_url": detail_url})
                seen_urls.add(detail_url)
                added_this_page += 1

            logger.info(f"page {page_no} added {added_this_page}, total candidates {len(candidates)}")

            if max_pages > 0 and page_no >= max_pages:
                logger.info(f"reached MAX_PAGES={max_pages}, stop paging")
                break

            moved = _go_next_page()
            if not moved:
                logger.info("reached last page (or no usable Next button)")
                break

            page_no += 1

        return candidates

    def _get_phones_from_detail_page(self, detail_url: str, person_name: str = "") -> List[str]:
        if not detail_url:
            return []

        phones: List[str] = []
        detail_page = None
        try:
            detail_page = self.context.new_page()
            retry_with_backoff(
                lambda: detail_page.goto(detail_url, wait_until="domcontentloaded"),
                label=f"detail.goto/{detail_url}",
            )
            time.sleep(max(0.2, float(config.WAIT_TIME)))

            if self._is_cloudflare_challenge(detail_page):
                ok = self._handle_cloudflare_challenge(detail_page)
                if not ok:
                    return []

            try:
                detail_page.wait_for_selector(
                    "span:has-text('Wireless'), span:has-text('Mobile'), a[href^='tel:']",
                    timeout=3000,
                )
            except Exception:
                pass

            phones = self._extract_phones_from_page(detail_page)

        except Exception as e:
            logger.warning(f"detail extraction failed: {detail_url} | error: {e}")
        finally:
            if detail_page:
                try:
                    detail_page.close()
                except Exception:
                    pass

        return phones

    def _extract_phones_from_page(self, page) -> List[str]:
        phones: List[str] = []
        seen = set()

        try:
            elems = page.query_selector_all("a[href^='tel:'], li, div, span, td, p")
            for elem in elems:
                try:
                    txt = (elem.inner_text() or "").strip()
                except Exception:
                    continue
                if not txt:
                    continue

                low = txt.lower()
                if config.ONLY_WIRELESS and ("wireless" not in low and "mobile" not in low):
                    continue

                m = PHONE_PATTERN.search(txt)
                if not m:
                    continue

                phone = _normalize_phone(m.group(0))
                if phone and phone not in seen:
                    seen.add(phone)
                    phones.append(phone)

            if not phones:
                full = page.evaluate("document.body.innerText || ''")
                for line in full.splitlines():
                    s = line.strip()
                    if not s:
                        continue
                    low = s.lower()
                    if config.ONLY_WIRELESS and ("wireless" not in low and "mobile" not in low):
                        continue
                    m = PHONE_PATTERN.search(s)
                    if not m:
                        continue
                    phone = _normalize_phone(m.group(0))
                    if phone and phone not in seen:
                        seen.add(phone)
                        phones.append(phone)

        except Exception as e:
            logger.debug(f"phone extraction failed: {e}")

        return phones

    def save_results(self, results: List[Dict], filename: str):
        if not results:
            logger.warning("no results to save")
            return

        output_path = Path(filename)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        fieldnames = ["name", "age", "location", "phone"]
        existing_records: List[Dict] = []
        existing_keys = set()

        if output_path.exists():
            try:
                with open(output_path, "r", newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        existing_records.append(row)
                        existing_keys.add(_dedup_key(row))
                logger.info(f"existing records: {len(existing_records)}")
            except Exception as e:
                logger.warning(f"read existing csv failed, overwrite mode: {e}")

        new_records = []
        for r in results:
            key = _dedup_key(r)
            if key not in existing_keys:
                existing_keys.add(key)
                new_records.append(r)

        if not new_records:
            logger.info(f"all {len(results)} records already exist, no write")
            return

        all_records = existing_records + new_records
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_records)

        logger.info(f"✓ added {len(new_records)} rows, total {len(all_records)} rows, saved to {filename}")