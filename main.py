import logging
import time
import random
from pathlib import Path

import config
from scraper import PeopleSearchScraper


def setup_logging():
    """
    精简日志输出：
    - 终端只显示 __main__ 的进度信息
    - scraper 的细节日志（如 [SAVE] / [SLOW]）不在终端显示
    - 全量日志仍写入文件，便于排错
    """
    Path("logs").mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, str(getattr(config, "LOG_LEVEL", "INFO")).upper(), logging.INFO)
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    root = logging.getLogger()
    root.setLevel(log_level)

    # 清空旧 handler，避免重复打印
    for h in list(root.handlers):
        root.removeHandler(h)

    # 文件日志：保留全部
    file_handler = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(log_format))
    root.addHandler(file_handler)

    # 终端日志：仅显示 __main__（进度）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(log_format))

    class MainOnlyFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return record.name == "__main__"

    console_handler.addFilter(MainOnlyFilter())
    root.addHandler(console_handler)


def clean_previous_results():
    """
    每次运行前清空 results/phones.csv
    """
    p = Path("results/phones.csv")
    p.parent.mkdir(parents=True, exist_ok=True)  # 确保 results 目录存在
    if p.exists():
        p.unlink(missing_ok=True)
        print("✅ 已清空 results/phones.csv，开始新任务。")
    else:
        print("ℹ️ results/phones.csv 不存在，已跳过清空。")


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

    # 仅保留“每个名字后”节流
    per_name_min = 8
    per_name_max = 15

    try:
        for i, name in enumerate(names, 1):
            logger.info("")
            logger.info(f"[进度 {i}/{len(names)}] 正在处理: {name}")

            try:
                results = scraper.search_by_name(name)

                # scraper 内部已“抓到即保存到 results/phones.csv”
                if results:
                    logger.info(f"[✓] 本轮新增号码: {len(results)}")
                    all_results.extend(results)
                    scraper.save_results(results, getattr(config, "OUTPUT_CSV", "results.csv"))
                else:
                    logger.info("[✗] 本轮无新增号码")

                success += 1

            except KeyboardInterrupt:
                logger.warning("检测到中断，正在安全退出...")
                raise
            except Exception as e:
                failed += 1
                logger.error(f"[✗] 处理失败: {name} | 错误: {e}")

            finally:
                sleep_s = random.uniform(per_name_min, per_name_max)
                logger.info(f"节流等待 {sleep_s:.1f}s ...")
                time.sleep(sleep_s)

    except KeyboardInterrupt:
        logger.warning("用户中断运行（Ctrl+C）")

    finally:
        try:
            scraper.close()
        except Exception:
            pass

    elapsed = time.time() - start_time

    logger.info("=" * 60)
    logger.info("── 运行摘要 ──────────────────────────────────")
    logger.info(f"  总名字数 : {len(names)}")
    logger.info(f"  成功处理 : {success}")
    logger.info(f"  失败数   : {failed}")
    logger.info(f"  新增号码 : {len(all_results)}")
    logger.info(f"  总耗时   : {elapsed:.1f}s")
    logger.info("  实时文件 : results/phones.csv")
    logger.info("=" * 60)


if __name__ == "__main__":
    clean_previous_results()  # ← 只新增这一行：每次启动先清空 results/phones.csv
    main()