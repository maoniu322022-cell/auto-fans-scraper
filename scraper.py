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
                    '--disable-dev-shm-usage'
                ]
            )
            
            self.context = self.browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
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
    
    def _is_verification_page(self, html: str) -> bool:
        """检查是否是验证页面"""
        if self._has_search_results(html):
            return False
        
        verification_keywords = [
            "Performing security verification",
            "Incompatible browser",
            "security verification",
            "challenges.cloudflare"
        ]
        
        html_lower = html.lower()
        for keyword in verification_keywords:
            if keyword.lower() in html_lower:
                return True
        
        return len(html) < 3000
    
    def search_by_name(self, name: str) -> List[Dict]:
        """按名字搜索"""
        results = []
        
        try:
            search_url = f"{config.SEARCH_URL}/{name.replace(' ', '-').lower()}"
            logger.info(f"正在搜索: {name}")
            logger.info(f"访问 URL: {search_url}")
            
            # 尝试用 cloudscraper
            if self.scraper:
                logger.info("使用 cloudscraper 请求...")
                html = self._fetch_with_cloudscraper(search_url)
                if html and not self._is_verification_page(html):
                    logger.info("✓ 获取到搜索结果")
                    results = self._extract_results_from_html(html, name)
                    if results:
                        return results
            
            # 降级到浏览器
            logger.info("使用 Playwright 请求...")
            if not self.page:
                self.init_browser()
            
            self.page.goto(search_url, wait_until="networkidle")
            time.sleep(config.WAIT_TIME)
            
            page_html = self.page.content()
            if self._is_verification_page(page_html):
                logger.warning("⚠️ 需要手动完成验证")
                logger.info("按任何键继续...")
                input()
                time.sleep(2)
                page_html = self.page.content()
            
            # 从 DOM 提取详细结果
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
        pattern = r'([A-Z][a-z]+ [A-Z][a-z]+).*?Approximate Age[:\s]+(\d+)'
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
        """从 DOM 提取结果并获取电话"""
        results = []
        
        try:
            logger.info("开始从 DOM 提取结果...")
            
            # 查找所有结果卡片
            result_cards = self.page.query_selector_all("div")
            
            for card in result_cards:
                try:
                    card_text = card.inner_text()
                    
                    if "Approximate Age" not in card_text:
                        continue
                    
                    # 提取名字
                    name_elem = card.query_selector("h3, .name, [class*='name']")
                    if name_elem:
                        person_name = name_elem.inner_text().strip()
                    else:
                        lines = card_text.split('\n')
                        person_name = None
                        for line in lines:
                            line = line.strip()
                            if line and len(line) > 3 and re.match(r'^[A-Z][a-z]+ [A-Z]', line):
                                person_name = line
                                break
                        if not person_name:
                            continue
                    
                    # 提取年龄
                    age_match = re.search(r'Approximate Age[:\s]+(\d+)', card_text, re.IGNORECASE)
                    if not age_match:
                        continue
                    
                    age = int(age_match.group(1))
                    if not (config.MIN_AGE <= age <= config.MAX_AGE):
                        continue
                    
                    logger.info(f"✓ 找到: {person_name} (年龄: {age})")
                    
                    # 提取位置
                    location_match = re.search(r'Current Location[:\s]+([^\n]+)', card_text, re.IGNORECASE)
                    location = location_match.group(1).strip() if location_match else "Unknown"
                    
                    # 查找详情按钮
                    button = card.query_selector("button:has-text('View All Info'), a:has-text('View All Info'), button, a")
                    if not button:
                        continue
                    
                    # 点击按钮获取电话
                    phones = self._get_phones_from_detail_page(button)
                    
                    if phones:
                        for phone in phones:
                            results.append({
                                "name": person_name,
                                "age": age,
                                "location": location,
                                "phone": phone
                            })
                            logger.info(f"✓ 保存: {person_name} - {phone}")
                    
                except Exception as e:
                    logger.debug(f"处理卡片失败: {e}")
                    continue
            
            return results
            
        except Exception as e:
            logger.error(f"提取结果失败: {e}")
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
            pattern = f"{name}.*?Current Location[:\\s]+([^<\\n]+)"
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
