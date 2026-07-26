import logging
import time
from pathlib import Path

import config
from scraper import PeopleSearchScraper


def setup_logging():
    Path("logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
            logging.StreamHandler()
        ],
    )


def load_names(input_file: str):
    p = Path(input_file)
    if not p.exists():
        return []
    names = []
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s:
            names.append(s)
    return names


def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    names = load_names(config.INPUT_FILE)
    if not names:
        logger.error(f"输入文件为空或不存在: {config.INPUT_FILE}")
        return

    logger.info("=" * 60)
    logger.info(f"开始运行，总名字数: {len(names)}")
    logger.info("=" * 60)

    scraper = PeopleSearchScraper()

    start_time = time.time()
    success = 0
    failed = 0
    all_results = []

    try:
        for i, name in enumerate(names, 1):
            logger.info("")
            logger.info(f"[进度 {i}/{len(names)}] 正在处理: {name}")

            try:
                results = scraper.search_by_name(name)

                if results:
                    logger.info(f"[✓] 找到 {len(results)} 条记录")
                    all_results.extend(results)

                    # 关键：每个名字处理完立即增量保存
                    scraper.save_results(results, config.OUTPUT_CSV)
                else:
                    logger.info("[✗] 无符合条件的结果")

                success += 1

            except KeyboardInterrupt:
                logger.warning("检测到中断，正在安全退出...")
                raise
            except Exception as e:
                failed += 1
                logger.error(f"[✗] 处理失败: {name} | 错误: {e}")

    except KeyboardInterrupt:
        logger.warning("用户中断运行（Ctrl+C）")

    finally:
        try:
            scraper.close()
        except Exception:
            pass

    elapsed = time.time() - start_time

    logger.info("=" * 60)
    if all_results:
        logger.info(f"共找到 {len(all_results)} 条符合条件的记录")
    else:
        logger.info("未找到符合条件的结果")
    logger.info("=" * 60)
    logger.info("── 运行摘要 ──────────────────────────────────")
    logger.info(f"  总名字数 : {len(names)}")
    logger.info(f"  成功处理 : {success}")
    logger.info(f"  失败数   : {failed}")
    logger.info(f"  结果记录 : {len(all_results)}")
    logger.info(f"  总耗时   : {elapsed:.1f}s")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()