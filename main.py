#!/usr/bin/env python3
# Copyright (c) 2026 Hao Yin. All rights reserved.

"""音频下载器 — 从数据库取待下载 URL 并批量下载

用法:
    python main.py                           # 下载 50 条
    python main.py --limit 200               # 下载 200 条
    python main.py --source xiaoyuzhou       # 仅下载指定来源
    python main.py --category 播客           # 仅下载指定分类
    python main.py --per-source --limit 20   # 每个来源各下载 20 条
    python main.py --per-category --limit 10 # 每个分类各下载 10 条
    python main.py --workers 10              # 10 并发下载
    python main.py --loop                    # 持续消费下载
    python main.py stats                     # 查看统计 + 已下载文件
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime

from config import LOG_DIR
from storage import Storage
from downloader import Downloader


def setup_logging():
    log_file = f"{LOG_DIR}/download_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def main():
    parser = argparse.ArgumentParser(description="AudioSpider 音频下载器")
    parser.add_argument("action", nargs="?", default="download",
                        choices=["download", "stats", "fix-meta"],
                        help="download=下载(默认), stats=统计, fix-meta=补生成元信息JSON")
    parser.add_argument("--source", default=None, help="仅下载指定来源 (如 podcast_rss, bilibili)")
    parser.add_argument("--category", default=None, help="仅下载指定分类 (如 播客, 有声书)")
    parser.add_argument("--per-source", action="store_true", help="每个来源各下载 --limit 条")
    parser.add_argument("--per-category", action="store_true", help="每个分类各下载 --limit 条")
    parser.add_argument("--limit", type=int, default=50, help="单批下载数量(默认50)")
    parser.add_argument("--workers", type=int, default=None, help="并发下载数(默认5)")
    parser.add_argument("--loop", action="store_true", help="持续循环消费下载")
    parser.add_argument("--interval", type=int, default=60, help="循环间隔秒数(默认60)")

    args = parser.parse_args()
    setup_logging()
    storage = Storage()

    if args.action == "stats":
        storage.show_stats()
        return

    if args.action == "fix-meta":
        from downloader import fix_meta
        fix_meta(storage)
        return

    dl_kwargs = dict(
        limit=args.limit,
        source=args.source,
        category=args.category,
        per_source=args.per_source,
        per_category=args.per_category,
    )

    async def download_once():
        dl = Downloader(storage, max_workers=args.workers)
        return await dl.download_all(**dl_kwargs)

    async def download_loop():
        logger = logging.getLogger("download")
        round_num = 0
        while True:
            round_num += 1
            logger.info(f"\n=== 下载轮次 {round_num} ===")
            dl = Downloader(storage, max_workers=args.workers)
            stats = await dl.download_all(**dl_kwargs)
            storage.show_stats()
            if stats["success"] == 0 and stats["failed"] == 0:
                logger.info(f"本轮无新下载, 等待 {args.interval} 秒...")
            await asyncio.sleep(args.interval)

    if args.loop:
        asyncio.run(download_loop())
    else:
        asyncio.run(download_once())
        storage.show_stats()


if __name__ == "__main__":
    main()
