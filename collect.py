#!/usr/bin/env python3
# Copyright (c) 2026 Hao Yin. All rights reserved.

"""URL 搜集器 — 负责发现和扩充待下载的语音 URL 池

每次需要扩充时直接运行：
    python collect.py                         # 运行全部爬虫
    python collect.py --spiders xiaoyuzhou    # 仅指定爬虫
    python collect.py --loop --interval 3600  # 每小时自动搜集一次
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime

from config import LOG_DIR
from storage import Storage

from spiders.xiaoyuzhou import XiaoyuzhouSpider
from spiders.ximalaya import XimalayaSpider
from spiders.librivox import LibriVoxSpider
from spiders.podcast_rss import PodcastRSSSpider
from spiders.bilibili import BilibiliSpider

ALL_SPIDERS = [
    XiaoyuzhouSpider,
    PodcastRSSSpider,
    XimalayaSpider,
    LibriVoxSpider,
    BilibiliSpider,
]


def setup_logging():
    log_file = f"{LOG_DIR}/collect_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


async def do_crawl(storage: Storage, spider_names: list[str] | None = None):
    logger = logging.getLogger("collect")
    logger.info("=" * 60)
    logger.info("开始搜集语音 URL...")
    logger.info("=" * 60)

    total_new = 0
    for SpiderClass in ALL_SPIDERS:
        spider = SpiderClass()
        if spider_names and spider.name not in spider_names:
            continue

        logger.info(f"\n>>> 启动爬虫: {spider.name}")
        try:
            records = await spider.crawl()
            if records:
                new_records = []
                for r in records:
                    if r.source_id and storage.source_id_exists(r.source, r.source_id):
                        continue
                    if storage.url_exists(r.url):
                        continue
                    new_records.append(r)

                if new_records:
                    added = storage.add_urls_batch(new_records)
                    total_new += added
                    logger.info(f"<<< {spider.name}: 发现 {len(records)} 个, 新增 {added} 个")
                else:
                    logger.info(f"<<< {spider.name}: 发现 {len(records)} 个, 全部已存在")
            else:
                logger.info(f"<<< {spider.name}: 未发现音频")
        except Exception as e:
            logger.error(f"<<< {spider.name} 异常: {e}", exc_info=True)

    logger.info(f"\n搜集完成, 本轮新增 {total_new} 条 URL")
    storage.show_stats()
    return total_new


def main():
    parser = argparse.ArgumentParser(description="AudioSpider URL 搜集器")
    parser.add_argument("--spiders", nargs="*", default=None,
                        help="指定爬虫: xiaoyuzhou ximalaya librivox podcast_rss")
    parser.add_argument("--loop", action="store_true", help="持续循环搜集")
    parser.add_argument("--interval", type=int, default=3600, help="循环间隔(秒)")

    args = parser.parse_args()
    setup_logging()
    storage = Storage()
    logger = logging.getLogger("collect")

    if args.loop:
        async def loop():
            round_num = 0
            while True:
                round_num += 1
                logger.info(f"\n{'#' * 60}")
                logger.info(f"# 第 {round_num} 轮搜集 — {datetime.now():%Y-%m-%d %H:%M:%S}")
                logger.info(f"{'#' * 60}")
                await do_crawl(storage, args.spiders)
                logger.info(f"等待 {args.interval} 秒...")
                await asyncio.sleep(args.interval)
        asyncio.run(loop())
    else:
        asyncio.run(do_crawl(storage, args.spiders))


if __name__ == "__main__":
    main()
