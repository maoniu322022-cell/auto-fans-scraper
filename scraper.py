# -*- coding: utf-8 -*-
import csv
import logging
import random
import re
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

logger = logging.getLogger(__name__)


class PeopleSearchScraper:
    def __init__(self, *args, **kwargs):
        self.base_url = kwargs.get(
            "base_url",
            "https://www.peoplesearchnow.com",
        ).rstrip("/")

        self.timeout = int(kwargs.get("timeout", 30000))
        self.wait_time = float(kwargs.get("wait_time", 0.5))
        self.max_retries = max(0, int(kwargs.get("max_retries", 1)))
        self.retry_base_delay = float(kwargs.get("retry_base_delay", 1.0))

        self.cdp_host = kwargs.get("cdp_host", "127.0.0.1")
        self.cdp_port = int(kwargs.get("cdp_port", 9222))
        self.cdp_url = kwargs.get(
            "cdp_url",
            f"http://{self.cdp_host}:{self.cdp_port}",
        )

        self.chrome_path = kwargs.get(
            "chrome_path",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        )
        self.chrome_user_data_dir = kwargs.get(
            "chrome_user_data_dir",
            r"C:\chrome-debug-profile",
        )
        self.auto_start_chrome = bool(kwargs.get("auto_start_chrome", True))
        self.chrome_start_wait_sec = int(
            kwargs.get("chrome_start_wait_sec", 20)
        )

        # 0 = 无限等待人工完成验证
        self.max_manual_wait = int(kwargs.get("max_manual_wait", 0))

        self.min_age = int(kwargs.get("min_age", 55))
        self.max_age = int(kwargs.get("max_age", 75))

        configured_max_pages = int(kwargs.get("max_pages", 0))
        self.max_pages = configured_max_pages if configured_max_pages > 0 else None

        configured_max_candidates = int(
            kwargs.get("max_candidates_per_query", 30)
        )
        self.max_candidates_per_query = (
            configured_max_candidates
            if configured_max_candidates > 0
            else None
        )

        self.per_action_min = float(kwargs.get("per_action_min", 0.8))
        self.per_action_max = float(kwargs.get("per_action_max", 1.5))
        self.per_detail_min = float(kwargs.get("per_detail_min", 1.5))
        self.per_detail_max = float(kwargs.get("per_detail_max", 2.5))

        self.block_heavy_resources = bool(
            kwargs.get("block_heavy_resources", True)
        )
        self.blocked_resource_types = set(
            kwargs.get("blocked_resource_types", ("image", "media", "font"))
        )

        result_file = kwargs.get("results_file", "results/phones.csv")
        self.results_file = Path(result_file)
        self.results_dir = self.results_file.parent
        self.result_flush_size = max(
            1,
            int(kwargs.get("result_flush_size", 10)),
        )

        self._seen_phones = set()
        self._pending_phones = []
        self._load_existing_phones()

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    # ---------------- browser ----------------

    def _is_port_open(
        self,
        host: str,
        port: int,
        timeout: float = 0.8,
    ) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _ensure_debug_chrome(self):
        if self._is_port_open(self.cdp_host, self.cdp_port):
            logger.info(
                f"[CDP] port already open: "
                f"{self.cdp_host}:{self.cdp_port}"
            )
            return

        if not self.auto_start_chrome:
            raise RuntimeError(
                f"CDP 端口未开启：{self.cdp_host}:{self.cdp_port}"
            )

        chrome_exe = Path(self.chrome_path)
        if not chrome_exe.exists():
            raise FileNotFoundError(f"找不到 Chrome：{chrome_exe}")

        Path(self.chrome_user_data_dir).mkdir(parents=True, exist_ok=True)

        command = [
            str(chrome_exe),
            f"--remote-debugging-port={self.cdp_port}",
            f"--user-data-dir={self.chrome_user_data_dir}",
        ]

        logger.info("[CDP] starting Chrome debug instance...")
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        started_at = time.time()

        while time.time() - started_at < self.chrome_start_wait_sec:
            if self._is_port_open(self.cdp_host, self.cdp_port):
                logger.info(
                    f"[CDP] port ready: "
                    f"{self.cdp_host}:{self.cdp_port}"
                )
                return
            time.sleep(0.5)

        raise RuntimeError(
            "Chrome 已尝试启动，但 CDP 端口在 "
            f"{self.chrome_start_wait_sec} 秒内未就绪"
        )

    def _route_request(self, route):
        try:
            resource_type = route.request.resource_type

            if resource_type in self.blocked_resource_types:
                route.abort()
            else:
                route.continue_()

        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    def _install_resource_blocker(self, page):
        if not self.block_heavy_resources:
            return

        try:
            page.route("**/*", self._route_request)
        except Exception:
            logger.debug("资源拦截规则未启用", exc_info=True)

    def _ensure_session(self):
        if self._page is not None:
            return

        self._ensure_debug_chrome()

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)

        contexts = self._browser.contexts
        self._context = contexts[0] if contexts else self._browser.new_context()
        self._context.set_default_timeout(self.timeout)

        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()

        self._install_resource_blocker(self._page)

        logger.info(
            f"[CDP] connected to {self.cdp_url} "
            "(single-tab mode)"
        )

    def close(self):
        try:
            self.flush_results()
        except Exception:
            logger.exception("最终保存结果失败")

        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    # ---------------- timing ----------------

    def _sleep_rand(
        self,
        minimum: float,
        maximum: float,
        label: str = "",
    ):
        if maximum <= 0:
            return

        delay = random.uniform(minimum, max(minimum, maximum))

        if label:
            logger.debug(f"[WAIT] {label}: {delay:.1f}s")

        time.sleep(delay)

    def _wait_ready(self, page):
        try:
            page.wait_for_load_state(
                "domcontentloaded",
                timeout=self.timeout,
            )
        except PlaywrightTimeoutError:
            pass

        if self.wait_time > 0:
            time.sleep(self.wait_time)

    def _navigate(self, page, url: str, label: str) -> bool:
        for attempt in range(self.max_retries + 1):
            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout,
                )
                self._wait_ready(page)
                return True

            except PlaywrightTimeoutError:
                logger.warning(
                    f"[{label}] 页面超时 "
                    f"({attempt + 1}/{self.max_retries + 1})"
                )

            except Exception as error:
                logger.warning(
                    f"[{label}] 页面打开失败 "
                    f"({attempt + 1}/{self.max_retries + 1}): {error}"
                )

            if attempt < self.max_retries:
                delay = self.retry_base_delay * (2 ** attempt)
                time.sleep(delay)

        return False

    # ---------------- challenge ----------------

    def _is_challenge_page(self, page) -> bool:
        markers = (
            "__cf_chl_rt_tk",
            "/cdn-cgi/",
            "just a moment",
            "attention required",
            "verify you are human",
            "checking your browser before accessing",
            "performing security verification",
            "ray id",
            "why have i been blocked",
            "cloudflare",
            "正在进行安全验证",
            "请验证您是真人",
        )

        try:
            metadata = f"{page.url}\n{page.title()}".lower()
        except Exception:
            metadata = ""

        if any(marker in metadata for marker in markers):
            return True

        try:
            body_text = page.locator("body").inner_text(
                timeout=3000
            ).lower()

            return any(marker in body_text[:12000] for marker in markers)

        except Exception:
            return False

    def _wait_until_challenge_passed(self, page, tag: str) -> bool:
        logger.warning(f"[{tag}] 检测到验证页，暂停等待人工完成验证。")

        print("\n" + "=" * 72)
        print("[MANUAL] 请在浏览器中完成验证。")
        print("[MANUAL] 完成后回终端按回车，脚本会继续当前任务。")
        print("[MANUAL] Ctrl+C 可安全退出并保存已抓到的结果。")
        print("=" * 72 + "\n")

        started_at = time.time()

        while True:
            try:
                input(">>> 验证完成后按回车继续：")
            except KeyboardInterrupt:
                logger.warning(f"[{tag}] 用户中断")
                return False
            except EOFError:
                time.sleep(2)
                continue

            poll_start = time.time()
            auto_wait_sec = 60
            poll_interval = 2

            while time.time() - poll_start < auto_wait_sec:
                self._wait_ready(page)

                if not self._is_challenge_page(page):
                    logger.info(f"[{tag}] 验证通过，已继续运行。")
                    return True

                time.sleep(poll_interval)

            elapsed = int(time.time() - started_at)
            logger.warning(
                f"[{tag}] 回车后已等待 {auto_wait_sec}s，"
                "仍在验证页；请完成验证后再次按回车。"
            )

            if (
                self.max_manual_wait > 0
                and elapsed >= self.max_manual_wait
            ):
                logger.warning(
                    f"[{tag}] 超过人工验证最大等待时间："
                    f"{self.max_manual_wait}s"
                )
                return False

    @staticmethod
    def _same_page(current_url: str, expected_url: str) -> bool:
        current = (current_url or "").split("#", 1)[0].rstrip("/")
        expected = (expected_url or "").split("#", 1)[0].rstrip("/")
        return current == expected

    def _guard_challenge(
        self,
        page,
        tag: str,
        return_url: str | None = None,
    ) -> bool:
        if not self._is_challenge_page(page):
            return True

        if not self._wait_until_challenge_passed(page, tag):
            return False

        if return_url and not self._same_page(page.url, return_url):
            return self._navigate(page, return_url, f"{tag} return")

        return True

    # ---------------- extraction ----------------

    @staticmethod
    def _slugify_name(name: str) -> str:
        slug = (name or "").strip().lower()
        slug = re.sub(r"[^a-z\s-]", " ", slug)
        slug = re.sub(r"\s+", "-", slug).strip("-")
        return slug

    @staticmethod
    def _extract_phone_candidates(text: str) -> list[str]:
        phones = set()

        pattern = (
            r"(?:\+?1[\s\-.]*)?"
            r"(?:\(\d{3}\)|\d{3})[\s\-.]*\d{3}[\s\-.]*\d{4}"
        )

        for match in re.findall(pattern, text or ""):
            digits = re.sub(r"\D", "", match)

            if len(digits) == 11 and digits.startswith("1"):
                digits = digits[1:]

            if len(digits) == 10:
                phones.add(
                    f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
                )

        return sorted(phones)

    @staticmethod
    def _extract_age(text: str) -> int | None:
        patterns = (
            r"Approximate Age:\s*(\d{1,3})",
            r"\bAge[:\s]+(\d{1,3})\b",
            r"\b(\d{1,3})\s*years?\s*old\b",
        )

        for pattern in patterns:
            match = re.search(
                pattern,
                text or "",
                flags=re.IGNORECASE,
            )

            if not match:
                continue

            try:
                age = int(match.group(1))

                if 0 < age < 120:
                    return age

            except ValueError:
                pass

        return None

    def _collect_cards_current_page(self, page) -> list[dict]:
        selector = (
            "a:has-text('View Details'), "
            "a:has-text('View All Info')"
        )

        try:
            raw_cards = page.locator(selector).evaluate_all(
                """
                links => links.map(link => {
                    let node = link;
                    let cardText = "";

                    while (node && node !== document.body) {
                        const text = node.innerText || "";

                        if (/Approximate\\s+Age\\s*:/i.test(text)) {
                            cardText = text;
                            break;
                        }

                        node = node.parentElement;
                    }

                    return {
                        href: link.href || "",
                        text: cardText
                    };
                })
                """
            )

        except Exception:
            logger.exception("读取列表候选项失败")
            return []

        candidates = []
        seen_urls = set()

        for item in raw_cards:
            detail_url = (item.get("href") or "").strip()
            age = self._extract_age(item.get("text") or "")

            if not detail_url or age is None:
                continue

            if not self.min_age <= age <= self.max_age:
                continue

            if detail_url in seen_urls:
                continue

            seen_urls.add(detail_url)
            candidates.append(
                {
                    "age": age,
                    "detail_url": detail_url,
                }
            )

            if (
                self.max_candidates_per_query is not None
                and len(candidates) >= self.max_candidates_per_query
            ):
                break

        return candidates

    def _advance_to_next_page(self, page, tag: str) -> bool:
        try:
            candidates = page.locator(
                ".pagination a:has-text('Next'), "
                "[class*='pagination'] a:has-text('Next'), "
                "nav[aria-label*='pagination' i] a:has-text('Next')"
            )

            if candidates.count() == 0:
                candidates = page.locator("a:has-text('Next')")

            if candidates.count() == 0:
                return False

            current_url = page.url

            for index in range(candidates.count()):
                link = candidates.nth(index)

                aria_disabled = (
                    link.get_attribute("aria-disabled") or ""
                ).lower()

                class_name = (
                    link.get_attribute("class") or ""
                ).lower()

                if aria_disabled == "true" or "disabled" in class_name:
                    continue

                text = (
                    link.inner_text(timeout=1000) or ""
                ).strip().lower()

                if text not in ("next", ">"):
                    continue

                href = (link.get_attribute("href") or "").strip()

                if href and not href.lower().startswith("javascript:"):
                    next_url = urljoin(current_url, href)
                    lower_url = next_url.lower()

                    if "/people/" in lower_url or "/phone/" in lower_url:
                        continue

                    if self._same_page(current_url, next_url):
                        continue

                    if self._navigate(
                        page,
                        next_url,
                        f"{tag} next-page",
                    ):
                        return True

                    continue

                try:
                    link.click(timeout=5000)
                    self._wait_ready(page)

                    if not self._same_page(page.url, current_url):
                        return True

                except Exception:
                    continue

            return False

        except Exception:
            logger.debug(
                f"[{tag}] 无法进入下一页",
                exc_info=True,
            )
            return False

    def _return_to_list_page(
        self,
        page,
        list_url: str,
        tag: str,
    ) -> bool:
        """
        优先使用浏览器后退回列表页，避免重新打开第一页再逐页翻回。
        若历史记录不可用，再直接打开当前列表页 URL。
        """
        try:
            page.go_back(
                wait_until="domcontentloaded",
                timeout=self.timeout,
            )
            self._wait_ready(page)

            if self._same_page(page.url, list_url):
                return True

        except Exception:
            pass

        return self._navigate(page, list_url, f"{tag} back-to-list")

    # ---------------- output ----------------

    def _load_existing_phones(self):
        self.results_dir.mkdir(parents=True, exist_ok=True)

        if not self.results_file.exists():
            return

        try:
            with self.results_file.open(
                "r",
                newline="",
                encoding="utf-8-sig",
            ) as file:
                for row in csv.DictReader(file):
                    phone = (row.get("phone") or "").strip()

                    if phone:
                        self._seen_phones.add(phone)

        except Exception:
            logger.exception("读取已有号码失败")

    def _queue_phone(self, phone: str) -> bool:
        phone = (phone or "").strip()

        if not phone or phone in self._seen_phones:
            return False

        self._seen_phones.add(phone)
        self._pending_phones.append(phone)

        if len(self._pending_phones) >= self.result_flush_size:
            self.flush_results()

        return True

    def flush_results(self) -> int:
        if not self._pending_phones:
            return 0

        self.results_dir.mkdir(parents=True, exist_ok=True)

        is_new_file = (
            not self.results_file.exists()
            or self.results_file.stat().st_size == 0
        )

        pending = list(self._pending_phones)

        try:
            with self.results_file.open(
                "a",
                newline="",
                encoding="utf-8-sig",
            ) as file:
                writer = csv.DictWriter(file, fieldnames=["phone"])

                if is_new_file:
                    writer.writeheader()

                writer.writerows(
                    {"phone": phone}
                    for phone in pending
                )

            self._pending_phones.clear()

            logger.info(
                f"[SAVE] 写入 {len(pending)} 个号码 -> "
                f"{self.results_file}"
            )

            return len(pending)

        except Exception:
            logger.exception("保存号码失败")
            return 0

    # ---------------- public API ----------------

    def search_by_name(self, name: str, *args, **kwargs) -> list[dict]:
        self._ensure_session()
        page = self._page

        slug = self._slugify_name(name)

        if not slug:
            return []

        initial_list_url = f"{self.base_url}/person/{slug}"

        logger.info(f"[search_by_name] {name} -> {initial_list_url}")

        if not self._navigate(page, initial_list_url, f"{name} list"):
            return []

        if not self._guard_challenge(
            page,
            tag=f"{name} list",
            return_url=initial_list_url,
        ):
            return []

        collected = []
        page_number = 1
        current_list_url = page.url
        visited_list_urls = {current_list_url}

        while True:
            logger.info(
                f"[{name}] 正在处理列表第 {page_number} 页"
            )

            candidates = self._collect_cards_current_page(page)

            logger.info(
                f"[{name}] 候选详情数 "
                f"({self.min_age}-{self.max_age} 岁)："
                f"{len(candidates)}"
            )

            if candidates:
                self._sleep_rand(
                    self.per_action_min,
                    self.per_action_max,
                    f"{name} page {page_number}",
                )

            for index, candidate in enumerate(candidates, start=1):
                detail_url = candidate["detail_url"]
                age = candidate["age"]

                logger.info(
                    f"[{name}] 详情 {index}/{len(candidates)}，"
                    f"年龄 {age}"
                )

                if not self._navigate(
                    page,
                    detail_url,
                    f"{name} detail {index}",
                ):
                    continue

                if not self._guard_challenge(
                    page,
                    tag=f"{name} detail {index}",
                    return_url=detail_url,
                ):
                    return collected

                try:
                    body_text = page.locator("body").inner_text(
                        timeout=self.timeout
                    )
                except Exception:
                    logger.warning(
                        f"[{name}] 读取详情文本失败：{detail_url}"
                    )
                    body_text = ""

                for phone in self._extract_phone_candidates(body_text):
                    if self._queue_phone(phone):
                        collected.append({"phone": phone})

                if not self._return_to_list_page(
                    page,
                    current_list_url,
                    f"{name} detail {index}",
                ):
                    logger.warning(
                        f"[{name}] 无法返回当前列表页，"
                        "停止该姓名。"
                    )
                    return collected

                if index < len(candidates):
                    self._sleep_rand(
                        self.per_detail_min,
                        self.per_detail_max,
                        f"{name} detail cooldown",
                    )

            if (
                self.max_pages is not None
                and page_number >= self.max_pages
            ):
                break

            self._sleep_rand(
                self.per_action_min,
                self.per_action_max,
                f"{name} page transition",
            )

            if not self._advance_to_next_page(page, name):
                break

            if not self._guard_challenge(
                page,
                tag=f"{name} next page",
            ):
                return collected

            current_list_url = page.url

            if current_list_url in visited_list_urls:
                logger.warning(
                    f"[{name}] 检测到分页循环，停止该姓名。"
                )
                break

            visited_list_urls.add(current_list_url)
            page_number += 1

        self.flush_results()
        return collected

    def save_results(self, records, output_file=None) -> int:
        """兼容旧调用；新流程会自动保存。"""
        for record in records or []:
            if isinstance(record, dict):
                self._queue_phone(record.get("phone") or "")

        return self.flush_results()

    def search(self, name: str, *args, **kwargs) -> list[dict]:
        return self.search_by_name(name, *args, **kwargs)