import csv
import logging
import re
import time
import random
import socket
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)


class PeopleSearchScraper:
    def __init__(self, *args, **kwargs):
        self.timeout = int(kwargs.get("timeout", 60000))
        self.wait_time = float(kwargs.get("wait_time", 2.0))
        self.base_url = "https://www.peoplesearchnow.com"

        self.cdp_host = kwargs.get("cdp_host", "127.0.0.1")
        self.cdp_port = int(kwargs.get("cdp_port", 9222))
        self.cdp_url = kwargs.get("cdp_url", f"http://{self.cdp_host}:{self.cdp_port}")

        # 自动拉起 Chrome 调试实例
        self.chrome_path = kwargs.get(
            "chrome_path",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        )
        self.chrome_user_data_dir = kwargs.get(
            "chrome_user_data_dir",
            r"C:\chrome-debug-profile"
        )
        self.auto_start_chrome = bool(kwargs.get("auto_start_chrome", True))
        self.chrome_start_wait_sec = int(kwargs.get("chrome_start_wait_sec", 20))

        self.max_manual_wait = int(kwargs.get("max_manual_wait", 0))  # 0=无限等待
        self.max_pages = int(kwargs.get("max_pages", 30))

        # 放慢速度
        self.per_action_min = float(kwargs.get("per_action_min", 2.0))
        self.per_action_max = float(kwargs.get("per_action_max", 4.5))
        self.per_detail_min = float(kwargs.get("per_detail_min", 4.0))
        self.per_detail_max = float(kwargs.get("per_detail_max", 7.0))

        # 即时保存文件（目录下 results 文件夹）
        self.results_dir = Path(kwargs.get("results_dir", "results"))
        self.results_file = self.results_dir / kwargs.get("results_file", "phones.csv")
        self._seen_phones = set()
        self._load_existing_phones()

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    # ---------------- system helpers ----------------
    def _is_port_open(self, host: str, port: int, timeout: float = 0.8) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False

    def _ensure_debug_chrome(self):
        """
        如果 9222 没开，则自动启动 Chrome(remote-debugging)。
        """
        if self._is_port_open(self.cdp_host, self.cdp_port):
            logger.info(f"[CDP] port already open: {self.cdp_host}:{self.cdp_port}")
            return

        if not self.auto_start_chrome:
            raise RuntimeError(
                f"CDP端口未开启: {self.cdp_host}:{self.cdp_port}，且 auto_start_chrome=False"
            )

        chrome_exe = Path(self.chrome_path)
        if not chrome_exe.exists():
            raise FileNotFoundError(
                f"找不到 Chrome: {self.chrome_path}，请在 scraper.py 里改 chrome_path"
            )

        Path(self.chrome_user_data_dir).mkdir(parents=True, exist_ok=True)

        cmd = [
            str(chrome_exe),
            f"--remote-debugging-port={self.cdp_port}",
            f"--user-data-dir={self.chrome_user_data_dir}",
        ]

        logger.info("[CDP] port not open, starting Chrome debug instance...")
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 等待端口起来
        start = time.time()
        while time.time() - start < self.chrome_start_wait_sec:
            if self._is_port_open(self.cdp_host, self.cdp_port):
                logger.info(f"[CDP] port ready: {self.cdp_host}:{self.cdp_port}")
                return
            time.sleep(0.5)

        raise RuntimeError(
            f"Chrome已尝试启动，但 {self.cdp_host}:{self.cdp_port} 在 {self.chrome_start_wait_sec}s 内未就绪"
        )

    # ---------------- basic ----------------
    def _sleep_rand(self, a: float, b: float, label: str = ""):
        s = random.uniform(a, b)
        if label:
            logger.info(f"[SLOW] {label} sleep {s:.1f}s")
        time.sleep(s)

    def _ensure_session(self):
        if self._page is not None:
            return

        self._ensure_debug_chrome()

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)

        contexts = self._browser.contexts
        self._context = contexts[0] if contexts else self._browser.new_context()

        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()

        logger.info(f"[CDP] connected to {self.cdp_url}")

    def close(self):
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def _wait_ready(self, page):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=self.timeout)
        except Exception:
            pass
        time.sleep(self.wait_time)

    def _slugify_name(self, name: str) -> str:
        s = (name or "").strip().lower()
        s = re.sub(r"[^a-z\s\-]", " ", s)
        s = re.sub(r"\s+", "-", s).strip("-")
        return s

    # ---------------- challenge ----------------
    def _is_challenge_page(self, page) -> bool:
        u = (page.url or "").lower()
        t = ""
        h = ""
        try:
            t = (page.title() or "").lower()
        except Exception:
            pass
        try:
            h = (page.content() or "").lower()
        except Exception:
            pass

        markers = [
            "__cf_chl_rt_tk", "/cdn-cgi/", "just a moment", "attention required",
            "verify you are human", "checking your browser before accessing",
            "performing security verification", "正在进行安全验证", "请验证您是真人", "ray id"
        ]
        blob = f"{u}\n{t}\n{h}"
        return any(m in blob for m in markers)

    def _wait_until_challenge_passed(self, page, tag: str) -> bool:
        logger.warning(f"[{tag}] 命中验证页，脚本暂停等待手动验证。")
        print("\n" + "=" * 72)
        print("[MANUAL] 请在浏览器中完成 Cloudflare 验证。")
        print("[MANUAL] 完成后回终端按回车，脚本继续当前名字。")
        print("[MANUAL] 未通过前不会进入下一个名字。Ctrl+C 可退出。")
        print("=" * 72 + "\n")

        start = time.time()
        while True:
            try:
                input(">>> 验证完成后按回车继续：")
            except KeyboardInterrupt:
                logger.warning(f"[{tag}] 用户中断")
                return False
            except EOFError:
                time.sleep(2)

            self._wait_ready(page)

            if not self._is_challenge_page(page):
                logger.info(f"[{tag}] ✓ 验证通过")
                return True

            elapsed = int(time.time() - start)
            logger.warning(f"[{tag}] 仍在验证页（已等待 {elapsed}s）")
            if self.max_manual_wait > 0 and elapsed >= self.max_manual_wait:
                logger.warning(f"[{tag}] 超过最大等待 {self.max_manual_wait}s")
                return False

    def _guard_challenge(self, page, tag: str, return_url: str = None) -> bool:
        if not self._is_challenge_page(page):
            return True

        ok = self._wait_until_challenge_passed(page, tag=tag)
        if not ok:
            return False

        if return_url:
            try:
                page.goto(return_url, wait_until="domcontentloaded", timeout=self.timeout)
                self._wait_ready(page)
            except Exception:
                return False
        return True

    # ---------------- parse ----------------
    def _extract_phone_candidates(self, text: str):
        phones = set()
        for m in re.findall(r"(?:\+?1[\s\-.]*)?(?:\(\d{3}\)|\d{3})[\s\-.]*\d{3}[\s\-.]*\d{4}", text or ""):
            d = re.sub(r"\D", "", m)
            if len(d) == 11 and d.startswith("1"):
                d = d[1:]
            if len(d) == 10:
                phones.add(f"({d[0:3]}) {d[3:6]}-{d[6:10]}")
        return sorted(phones)

    def _extract_age(self, text: str):
        for p in [r"Approximate Age:\s*(\d{1,3})", r"\bAge[:\s]+(\d{1,3})\b", r"\b(\d{1,3})\s*years?\s*old\b"]:
            m = re.search(p, text or "", flags=re.I)
            if m:
                try:
                    a = int(m.group(1))
                    if 0 < a < 120:
                        return a
                except Exception:
                    pass
        return None

    def _detect_total_pages(self, page):
        nums = []
        loc = page.locator("a")
        n = min(loc.count(), 400)
        for i in range(n):
            try:
                t = (loc.nth(i).inner_text() or "").strip()
                if t.isdigit():
                    nums.append(int(t))
            except Exception:
                pass
        return max(nums) if nums else 1

    def _goto_page_num(self, page, list_url: str, page_num: int) -> bool:
        if page_num == 1:
            try:
                page.goto(list_url, wait_until="domcontentloaded", timeout=self.timeout)
                self._wait_ready(page)
                return True
            except Exception:
                return False

        try:
            page.goto(list_url, wait_until="domcontentloaded", timeout=self.timeout)
            self._wait_ready(page)
        except Exception:
            return False

        # 优先点击数字页码
        try:
            link = page.locator(f"a:has-text('{page_num}')").first
            if link.count() > 0:
                link.click(timeout=5000)
                self._wait_ready(page)
                return True
        except Exception:
            pass

        # 兜底 Next 翻页
        current = 1
        while current < page_num:
            nxt = page.locator("a:has-text('Next')")
            if nxt.count() == 0:
                return False
            try:
                nxt.first.click(timeout=5000)
                self._wait_ready(page)
                current += 1
            except Exception:
                return False
        return True

    def _collect_cards_current_page(self, page):
        """
        当前页仅收集年龄 55-75 且有详情链接的卡片
        """
        cards_data = []
        cards = page.locator("div").filter(has_text=re.compile(r"Approximate Age:", re.I))
        cnt = cards.count()

        for i in range(cnt):
            c = cards.nth(i)
            try:
                txt = c.inner_text(timeout=3000)
            except Exception:
                continue

            age = self._extract_age(txt)
            if age is None or not (55 <= age <= 75):
                continue  # 不符合年龄直接跳过，不进详情

            href = None
            for sel in ["a:has-text('View Details')", "a:has-text('View All Info')"]:
                btn = c.locator(sel)
                if btn.count() > 0:
                    try:
                        href = btn.first.get_attribute("href")
                        if href:
                            break
                    except Exception:
                        pass

            if not href:
                continue
            if not href.startswith("http"):
                href = self.base_url + href

            cards_data.append({
                "age": age,
                "detail_url": href
            })

        # 去重
        uniq = []
        seen = set()
        for x in cards_data:
            key = (x["detail_url"], x["age"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(x)
        return uniq

    # ---------------- immediate save ----------------
    def _load_existing_phones(self):
        self.results_dir.mkdir(parents=True, exist_ok=True)
        if not self.results_file.exists():
            return
        try:
            with self.results_file.open("r", newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    p = (row.get("phone") or "").strip()
                    if p:
                        self._seen_phones.add(p)
        except Exception:
            logger.exception("[init] failed loading existing phones")

    def _save_phone_immediately(self, phone: str):
        phone = (phone or "").strip()
        if not phone:
            return False
        if phone in self._seen_phones:
            return False

        self.results_dir.mkdir(parents=True, exist_ok=True)
        new_file = not self.results_file.exists() or self.results_file.stat().st_size == 0

        with self.results_file.open("a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["phone"])
            if new_file:
                writer.writeheader()
            writer.writerow({"phone": phone})

        self._seen_phones.add(phone)
        logger.info(f"[SAVE] {phone} -> {self.results_file}")
        return True

    # ---------------- public ----------------
    def search_by_name(self, name: str, *args, **kwargs):
        self._ensure_session()
        page = self._page

        slug = self._slugify_name(name)
        if not slug:
            return []

        list_url = f"{self.base_url}/person/{slug}"
        logger.info(f"[search_by_name] {name} -> {list_url}")

        collected = []

        try:
            page.goto(list_url, wait_until="domcontentloaded", timeout=self.timeout)
            self._wait_ready(page)

            if not self._guard_challenge(page, tag=name, return_url=list_url):
                return []

            if "/person/" not in (page.url or ""):
                return []

            total_pages = self._detect_total_pages(page)
            total_pages = max(1, min(total_pages, self.max_pages))
            logger.info(f"[{name}] total_pages={total_pages}")

            for pnum in range(1, total_pages + 1):
                logger.info(f"[{name}] page {pnum}/{total_pages}")

                ok = self._goto_page_num(page, list_url, pnum)
                if not ok:
                    logger.warning(f"[{name}] page {pnum} 打开失败")
                    continue

                if not self._guard_challenge(page, tag=name, return_url=None):
                    return collected

                # 当前页先筛年龄，只留 55-75
                candidates = self._collect_cards_current_page(page)
                logger.info(f"[{name}] page {pnum} candidates(55-75)={len(candidates)}")

                self._sleep_rand(self.per_action_min, self.per_action_max, f"{name} page-cooldown")

                for idx, c in enumerate(candidates, 1):
                    detail_url = c["detail_url"]
                    age = c["age"]

                    logger.info(f"[{name}] -> detail {idx}/{len(candidates)} age={age}")

                    try:
                        page.goto(detail_url, wait_until="domcontentloaded", timeout=self.timeout)
                        self._wait_ready(page)

                        if not self._guard_challenge(page, tag=name, return_url=detail_url):
                            return collected

                        body = ""
                        try:
                            body = page.inner_text("body")
                        except Exception:
                            body = ""

                        phones = self._extract_phone_candidates(body)
                        for ph in phones:
                            saved = self._save_phone_immediately(ph)
                            if saved:
                                collected.append({"phone": ph})

                    except Exception:
                        logger.exception(f"[{name}] detail parse failed: {detail_url}")

                    self._sleep_rand(self.per_detail_min, self.per_detail_max, f"{name} detail-cooldown")

                self._sleep_rand(self.per_action_min, self.per_action_max, f"{name} page-end-cooldown")

            return collected

        except PlaywrightTimeoutError:
            logger.warning(f"[{name}] timeout")
            return collected
        except Exception:
            logger.exception(f"[{name}] search failed")
            return collected

    def save_results(self, records, output_file=None):
        """
        兼容 main.py 调用。实际保存已在抓取时即时写入 results/phones.csv
        """
        rows = records or []
        if not rows:
            logger.info("[save_results] no records")
            return 0

        n = 0
        for r in rows:
            if isinstance(r, dict):
                ph = (r.get("phone") or "").strip()
                if ph and self._save_phone_immediately(ph):
                    n += 1

        logger.info(f"[save_results] merged {n} new phones into {self.results_file}")
        return n

    def search(self, name: str, *args, **kwargs):
        return self.search_by_name(name, *args, **kwargs)