import logging
import sys
import os
from pathlib import Path
from scraper import PeopleSearchScraper
import config

# 设置 UTF-8 编码
if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 配置日志
log_dir = Path(config.LOG_FILE).parent
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


def load_names(filename: str) -> list:
    """加载名字列表"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            names = [line.strip() for line in f if line.strip()]
        logger.info(f"✓ 加载了 {len(names)} 个名字")
        return names
    except FileNotFoundError:
        logger.error(f"文件不存在: {filename}")
        return []
    except Exception as e:
        logger.error(f"加载名字失败: {e}")
        return []


def main():
    """主程序"""
    logger.info("=" * 60)
    logger.info("开始按名字搜索")
    logger.info("=" * 60)
    
    # 加载名字
    names = load_names(config.INPUT_FILE)
    if not names:
        logger.error("未加载到名字，退出")
        return
    
    logger.info(f"✓ 筛选条件: 年龄 {config.MIN_AGE}-{config.MAX_AGE} 岁")
    if config.ONLY_WIRELESS:
        logger.info("✓ 仅保留 Wireless 电话")
    logger.info("")
    
    # 初始化爬虫
    scraper = PeopleSearchScraper()
    all_results = []
    
    try:
        for idx, name in enumerate(names, 1):
            logger.info(f"[进度 {idx}/{len(names)}] 正在处理: {name}")
            
            try:
                results = scraper.search_by_name(name)
                
                if results:
                    all_results.extend(results)
                    logger.info(f"[✓] 找到 {len(results)} 条记录")
                else:
                    logger.info(f"[✗] 无符合条件的结果")
                
            except Exception as e:
                logger.error(f"处理 {name} 时出错: {e}")
                continue
            
            logger.info("")
    
    finally:
        scraper.close()
    
    # 保存结果
    logger.info("=" * 60)
    if all_results:
        logger.info(f"共找到 {len(all_results)} 条符合条件的记录")
        scraper.save_results(all_results, config.OUTPUT_FILE)
    else:
        logger.info("未找到符合条件的结果")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
