#!/usr/bin/env python3
# Copyright (c) 2026 Hao Yin. All rights reserved.

"""音频下载器 — 从数据库取待下载 URL 并批量下载

用法:
    python main.py                           # 下载 50 条
    python main.py --limit 200               # 下载 200 条
    python main.py --source xiaoyuzhou       # 仅下载指定来源
    python main.py --category 播客           # 仅下载指定分类
    python main.py --language zh             # 仅下载中文音频
    python main.py --language zh --exclude-platform ximalaya --loop
                                             # 持续下载中文，并排除指定实际平台
    python main.py --language en             # 仅下载英文音频
    python main.py --per-source --limit 20   # 每个来源各下载 20 条
    python main.py --per-category --limit 10 # 每个分类各下载 10 条
    python main.py --workers 10              # 10 并发下载
    python main.py --format original         # 保留原始格式，跳过 ffmpeg 转 opus
    python main.py --loop                    # 持续消费下载
    python main.py --retry-failed            # 重试一批失败的 URL
    python main.py --retry-failed --source bilibili  # 只重试指定来源的失败 URL
    python main.py stats                     # 查看统计 + 已下载文件
"""

import argparse
import asyncio
import logging
import sys
from collections import Counter
from datetime import datetime

from config import LOG_DIR
from downloader import Downloader
from platform_filter import (
    PLATFORM_DISPLAY_NAMES,
    SUPPORTED_PLATFORMS,
    build_filter_checkpoint_key,
    scan_filtered_pending,
)
from storage import Storage


FILTER_CHECKPOINT_SOURCE = "downloader"


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
    parser.add_argument("--language", default=None, help="仅下载指定语种 (如 zh, en)")
    parser.add_argument(
        "--exclude-platform",
        action="append",
        choices=SUPPORTED_PLATFORMS,
        default=[],
        help="排除实际音频平台，可重复指定: ximalaya/lizhi/xiaoyuzhou/qingting/bilibili",
    )
    parser.add_argument(
        "--reset-filter-cursor",
        action="store_true",
        help="将当前平台过滤条件的扫描游标重置到数据库开头",
    )
    parser.add_argument("--per-source", action="store_true", help="每个来源各下载 --limit 条")
    parser.add_argument("--per-category", action="store_true", help="每个分类各下载 --limit 条")
    parser.add_argument("--limit", type=int, default=50, help="单批下载数量(默认50)")
    parser.add_argument("--workers", type=int, default=None, help="并发下载数(默认5)")
    parser.add_argument("--format", choices=["opus", "original"], default="opus",
                        help="保存格式: opus=ffmpeg转opus(默认), original=保留原始格式(省CPU)")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="重试最多 --limit 条 failed；再次失败时标记为 retry_failed",
    )
    parser.add_argument("--loop", action="store_true", help="持续循环消费下载")
    parser.add_argument("--interval", type=int, default=60,
                        help="无待下载任务时的检查间隔秒数(默认60)")
    parser.add_argument("--host-max-share", type=float, default=0.0,
                        help="单批任一域名最大占比，如 0.2 表示约20%%")
    parser.add_argument("--since", default=None,
                        help="仅下载发布时间 >= 此日期的 (如 2024-01-01)")
    parser.add_argument("--before", default=None,
                        help="仅下载发布时间 <= 此日期的 (如 2024-12-31)")

    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit 必须大于 0")
    if not 0 <= args.host_max_share <= 1:
        parser.error("--host-max-share 必须在 0 到 1 之间")
    if args.host_max_share and (args.per_source or args.per_category or args.exclude_platform):
        parser.error("--host-max-share 不能与分组下载或平台排除同时使用")
    if args.exclude_platform and (args.per_source or args.per_category):
        parser.error("--exclude-platform 不能与 --per-source/--per-category 同时使用")
    if args.exclude_platform and args.retry_failed:
        parser.error("--exclude-platform 暂不支持与 --retry-failed 同时使用")
    if args.reset_filter_cursor and not args.exclude_platform:
        parser.error("--reset-filter-cursor 必须与 --exclude-platform 一起使用")

    setup_logging()
    storage = Storage()

    if args.action == "stats":
        storage.show_stats()
        return

    if args.action == "fix-meta":
        from downloader import fix_meta
        fix_meta(storage)
        return

    logger = logging.getLogger("download")
    recovered = storage.recover_interrupted_downloads()
    if recovered:
        logger.info(f"已恢复 {recovered} 条上次中断的下载任务")

    if args.retry_failed:
        failed_items = storage.get_failed(
            limit=args.limit,
            source=args.source,
            language=args.language,
        )
        scope_parts = []
        if args.source:
            scope_parts.append(f"来源={args.source}")
        if args.language:
            scope_parts.append(f"语言={args.language}")
        scope = "，".join(scope_parts) or "全部来源"
        if not failed_items:
            logger.info(f"没有失败的 URL 需要重试（{scope}）")
            return
        logger.info(f"准备重试 {len(failed_items)} 条失败的 URL（{scope}）")

        async def retry():
            dl = Downloader(storage, max_workers=args.workers,
                            convert=args.format == "opus",
                            failure_status="retry_failed")
            return await dl.download_all(items=failed_items)
        asyncio.run(retry())
        storage.show_stats()
        return

    dl_kwargs = dict(
        limit=args.limit,
        source=args.source,
        category=args.category,
        language=args.language,
        per_source=args.per_source,
        per_category=args.per_category,
        published_since=args.since,
        published_before=args.before,
        host_max_share=args.host_max_share,
    )

    convert = args.format == "opus"
    excluded_platforms = set(args.exclude_platform)
    checkpoint_key = None
    if excluded_platforms:
        checkpoint_key = build_filter_checkpoint_key(
            excluded_platforms=excluded_platforms,
            source=args.source,
            category=args.category,
            language=args.language,
            published_since=args.since,
            published_before=args.before,
        )
        if args.reset_filter_cursor:
            storage.set_checkpoint(FILTER_CHECKPOINT_SOURCE, checkpoint_key, "0")
            logger.info("已将当前过滤配置的扫描游标重置到数据库开头")

        excluded_names = "、".join(
            PLATFORM_DISPLAY_NAMES[name] for name in sorted(excluded_platforms)
        )
        logger.info(f"启用实时平台过滤，排除: {excluded_names}")

    async def download_once():
        if excluded_platforms:
            cursor_value = storage.get_checkpoint(FILTER_CHECKPOINT_SOURCE, checkpoint_key)
            try:
                cursor = int(cursor_value or 0)
            except ValueError:
                cursor = 0

            batch = scan_filtered_pending(
                storage,
                after_id=cursor,
                limit=args.limit,
                excluded_platforms=excluded_platforms,
                source=args.source,
                category=args.category,
                language=args.language,
                published_since=args.since,
                published_before=args.before,
            )
            excluded_total = sum(batch.excluded_counts.values())
            excluded_summary = "、".join(
                f"{PLATFORM_DISPLAY_NAMES[name]}={count}"
                for name, count in Counter(batch.excluded_counts).most_common()
            )
            logger.info(
                "本轮过滤: 游标 %d → %d，扫描 %d 条，选中 %d 条，排除 %d 条%s",
                cursor,
                batch.cursor,
                batch.scanned,
                len(batch.items),
                excluded_total,
                f"（{excluded_summary}）" if excluded_summary else "",
            )

            if not batch.items:
                if batch.cursor != cursor:
                    storage.set_checkpoint(
                        FILTER_CHECKPOINT_SOURCE, checkpoint_key, str(batch.cursor)
                    )
                logger.info("当前游标后没有符合条件的待下载音频")
                return {"success": 0, "failed": 0, "skipped": 0, "dup": 0}

            dl = Downloader(storage, max_workers=args.workers, convert=convert)
            stats = await dl.download_all(items=batch.items)
            storage.set_checkpoint(FILTER_CHECKPOINT_SOURCE, checkpoint_key, str(batch.cursor))
            return stats

        dl = Downloader(storage, max_workers=args.workers, convert=convert)
        return await dl.download_all(**dl_kwargs)

    async def download_loop():
        round_num = 0
        while True:
            round_num += 1
            logger.info(f"\n=== 下载轮次 {round_num} ===")
            stats = await download_once()
            processed = sum(stats.values())
            if processed == 0:
                storage.show_stats()
                logger.info(f"本轮无新下载, 等待 {args.interval} 秒...")
                await asyncio.sleep(args.interval)
            else:
                logger.info(f"本轮处理 {processed} 条，立即开始下一轮")

    if args.loop:
        asyncio.run(download_loop())
    else:
        asyncio.run(download_once())
        storage.show_stats()


if __name__ == "__main__":
    main()
