import logging
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
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
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
        except:
            return False
    
    def _handle_cloudflare_challenge(self, page) -> bool:
        """处理 Cloudflare 挑战"""
        try:
            logger.info("⏳ 检测到 Cloudflare 挑战页面，等待处理...")
            
            # 方法1: 等待 Cloudflare 自动处理
            logger.info("正在等待 Cloudflare 自动处理 (30秒)...")
            try:
                page.wait_for_navigation(timeout=30000, wait_until="networkidle")
                logger.info("✓ 自动处理完成")
                return True
            except:
                pass
            
            # 方法2: 查找并点击验证复选框
            logger.info("尝试点击验证框...")
            
            # 尝试多个选择器
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
                    if elements:
                        logger.info(f"找到元素: {selector}")
                        for elem in elements:
                            try:
                                # 滚动到元素
                                elem.scroll_into_view_if_needed()
                                time.sleep(0.5)
                                # 点击
                                elem.click()
                                logger.info("✓ 已点击验证框")
                                time.sleep(2)
                                break
                            except Exception as e:
                                logger.debug(f"点击失败: {e}")
                except:
                    pass
            
            # 等待验证完成
            logger.info("⏳ 等待验证完成 (15秒)...")
            time.sleep(15)
            
            # 检查是否仍在验证页面
            if self._is_cloudflare_challenge(page):
                logger.warning("⚠️ 验证可能未完成，请手动完成验证后按任何键继续...")
                input()
            else:
                logger.info("✓ Cloudflare 验证已完成")
            
            return True
                
        except Exception as e:
            logger.error(f"处理 Cloudflare 失败: {e}")
            return False
    
    def search_by_name(self, name: str) -> List[Dict]:
        """按名字搜索"""
        results = []
        
        try:
            search_url = f"{config.SEARCH_URL}/{name.replace(' ', '-').lower()}"
            logger.info(f"正在搜索: {name}")
            logger.info(f"访问 URL: {search_url}")
            
            # 优先使用 cloudscraper
            if self.scraper:
                logger.info("使用 cloudscraper 请求...")
                html = self._fetch_with_cloudscraper(search_url)
                if html and self._has_search_results(html):
                    logger.info("✓ 获取到搜索结果")
                    results = self._extract_results_from_html(html, name)
                    if results:
                        return results
            
            # 降级到浏览器
            logger.info("使用 Playwright 请求...")
            if not self.page:
                self.init_browser()
            
            # 访问页面
            self.page.goto(search_url, wait_until="domcontentloaded")
            time.sleep(2)
            
            # 处理 Cloudflare 挑战
            if self._is_cloudflare_challenge(self.page):
                self._handle_cloudflare_challenge(self.page)
                time.sleep(2)
            
            # 等待搜索结果加载
            try:
                self.page.wait_for_selector(
                    'div:has-text("Approximate Age"), div:has-text("Current Location")',
                    timeout=10000
                )
            except:
                logger.debug("未找到结果选择器，继续处理...")
            
            time.sleep(config.WAIT_TIME)
            
            # 从 DOM 提取结果
            results = self._extract_results_from_dom(name)
            
            return results
            
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return []
    
    def _fetch_with_cloudscraper(self, url: str) -> Optional[str]:
        """使用 cloudscraper 获取页面"""
        try:
            response = self.scraper.get(url, timeout=30)
            if response.status_code == 200:
                logger.info(f"✓ 请求成功 (状态码: {response.status_code})")
                return response.text
        except Exception as e:
            logger.warning(f"cloudscraper 失败: {e}")
        return None
    
    def _extract_results_from_html(self, html: str, search_name: str) -> List[Dict]:
        """从 HTML 提取结果"""
        results = []
        
        if not self._has_search_results(html):
            return []
        
        # 查找符合年龄范围的人物
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
            except:
                continue
        
        return results
    
    def _extract_results_from_dom(self, search_name: str) -> List[Dict]:
        """从 DOM 提取结果 - 保存所有符合条件的人员"""
        results = []
        
        try:
            logger.info("开始从页面提取所有符合条件的人员...")
            
            # 获取页面内容
            page_content = self.page.content()
            
            # 提取所有人员信息
            # 寻找包含 "Approximate Age" 的文本块
            age_pattern = r'([A-Z][a-z]+ [A-Z][a-z]+).*?Approximate Age[:=\s]*(\d+)'
            location_pattern = r'Current Location[:=\s]*([^\n<]+)'
            
            # 获取所有文本内容
            page_text = self.page.evaluate('document.body.innerText')
            
            # 查找所有符合条件的人员
            lines = page_text.split('\n')
            current_person = None
            
            for i, line in enumerate(lines):
                line = line.strip()
                
                # 检查是否是年龄信息
                age_match = re.search(r'Approximate Age[:=\s]*(\d+)', line, re.IGNORECASE)
                if age_match:
                    age = int(age_match.group(1))
                    
                    # 检查年龄范围
                    if not (config.MIN_AGE <= age <= config.MAX_AGE):
                        current_person = None
                        continue
                    
                    # 回溯找名字
                    person_name = None
                    for j in range(i - 1, max(0, i - 5), -1):
                        prev_line = lines[j].strip()
                        if prev_line and re.match(r'^[A-Z][a-z]+ [A-Z][a-z]+', prev_line):
                            person_name = prev_line
                            break
                    
                    if not person_name:
                        person_name = "Unknown"
                    
                    # 查找位置信息
                    location = "Unknown"
                    for j in range(i + 1, min(len(lines), i + 5)):
                        next_line = lines[j].strip()
                        if "Current Location" in next_line:
                            loc_match = re.search(r'Current Location[:=\s]*([^\n]+)', next_line, re.IGNORECASE)
                            if loc_match:
                                location = loc_match.group(1).strip()
                            break
                    
                    # 查找电话信息
                    phone = "未获取"
                    if config.ONLY_WIRELESS:
                        for j in range(i + 1, min(len(lines), i + 10)):
                            next_line = lines[j].strip()
                            if "Wireless" in next_line or "Mobile" in next_line:
                                phone_match = re.search(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', next_line)
                                if phone_match:
                                    phone = phone_match.group(0).strip()
                                    break
                    
                    # 保存结果
                    result = {
                        "name": person_name,
                        "age": age,
                        "location": location,
                        "phone": phone
                    }
                    
                    # 避免重复
                    if result not in results:
                        results.append(result)
                        logger.info(f"✓ 保存: {person_name} | 年龄: {age} | 位置: {location} | 电话: {phone}")
            
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
            
            # 处理详情页的 Cloudflare
            if self._is_cloudflare_challenge(detail_page):
                logger.info("⚠️ 详情页需要 Cloudflare 验证")
                self._handle_cloudflare_challenge(detail_page)
                time.sleep(2)
            
            # 等待电话元素
            try:
                detail_page.wait_for_selector(
                    "span:has-text('Wireless'), span:has-text('Mobile')",
                    timeout=10000
                )
            except:
                pass
            
            # 提取电话
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
                    
                    # 提取电话
                    phone_match = re.search(
                        r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}',
                        elem_text
                    )
                    if phone_match:
                        phone = phone_match.group(0).strip()
                        if phone not in phones:
                            phones.append(phone)
                            logger.info(f"  ✓ 找到: {phone}")
                
                except:
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
        except:
            pass
        return "Unknown"
    
    def save_results(self, results: List[Dict], filename: str):
        """保存结果到 CSV"""
        try:
            Path(filename).parent.mkdir(parents=True, exist_ok=True)
            
            if not results:
                logger.warning("无结果保存")
                return
            
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=['name', 'age', 'location', 'phone'])
                writer.writeheader()
                writer.writerows(results)
            
            logger.info(f"✓ 保存 {len(results)} 条记录到 {filename}")
        except Exception as e:
            logger.error(f"保存失败: {e}")
