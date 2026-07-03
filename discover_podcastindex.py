#!/usr/bin/env python3
# Copyright (c) 2026 Hao Yin. All rights reserved.

"""通过 Podcast Index API 发现播客源

Podcast Index 是最大的开放播客目录（400 万+ 播客），提供搜索和趋势榜单。
需要免费注册 API Key: https://podcastindex.org

用法：
    # 设置 API Key（二选一）
    export PODCAST_INDEX_KEY="your_key"
    export PODCAST_INDEX_SECRET="your_secret"
    # 或
    python discover_podcastindex.py --api-key YOUR_KEY --api-secret YOUR_SECRET

    # 按关键词搜索
    python discover_podcastindex.py

    # 自定义关键词
    python discover_podcastindex.py --keywords 粤语 相声

    # 拉取趋势榜单（不需要关键词，按分类遍历）
    python discover_podcastindex.py --trending

    # 同时搜索 + 趋势
    python discover_podcastindex.py --trending --keywords 粤语
"""

import argparse
import asyncio
import hashlib
import logging
import os
import sys
import time
from datetime import datetime

import aiohttp

from config import LOG_DIR
from discover import (
    FeedStore, parse_rss_feed, GENRE_MAP, DEFAULT_KEYWORDS,
    _map_genre,
)
from storage import Storage

logger = logging.getLogger("discover.podcastindex")

PODCASTINDEX_BASE = "https://api.podcastindex.org/api/1.0"

PODCASTINDEX_CATEGORIES_FOR_SPEECH = [
    "Arts", "Books", "Comedy", "Education", "Fiction",
    "History", "Kids-Family", "News", "Religion-Spirituality",
    "Science", "Society-Culture", "True-Crime", "Health-Fitness",
    "Sports", "Business", "Technology", "Government",
    "TV-Film", "Leisure",
]


def _build_podcastindex_headers(api_key: str, api_secret: str) -> dict:
    epoch = str(int(time.time()))
    auth_hash = hashlib.sha1(
        (api_key + api_secret + epoch).encode()
    ).hexdigest()
    return {
        "User-Agent": "AudioSpider/1.0",
        "X-Auth-Key": api_key,
        "X-Auth-Date": epoch,
        "Authorization": auth_hash,
    }


def _extract_genres(categories: dict | None) -> list[str]:
    """从 Podcast Index 的 categories 字段提取 genre 列表"""
    if not categories:
        return []
    return list(categories.values())


async def search_podcastindex(session: aiohttp.ClientSession,
                               keyword: str, api_key: str, api_secret: str,
                               max_results: int = 60) -> list[dict]:
    """通过 Podcast Index 搜索播客"""
    url = f"{PODCASTINDEX_BASE}/search/byterm"
    params = {"q": keyword, "max": min(max_results, 60), "clean": "true"}
    headers = _build_podcastindex_headers(api_key, api_secret)

    try:
        async with session.get(url, params=params, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning(f"PodcastIndex搜索 '{keyword}' 返回 {resp.status}")
                return []
            data = await resp.json(content_type=None)
            feeds = data.get("feeds", [])
            podcasts = []
            for f in feeds:
                feed_url = f.get("url", "") or f.get("originalUrl", "")
                if not feed_url:
                    continue
                genres = _extract_genres(f.get("categories"))
                lang = (f.get("language") or "")[:2].lower()
                podcasts.append({
                    "feed_url": feed_url,
                    "name": f.get("title", ""),
                    "artist": f.get("author", ""),
                    "genre": ", ".join(genres),
                    "genres": genres,
                    "language": lang,
                })
            logger.info(f"PodcastIndex搜索 '{keyword}': 找到 {len(podcasts)} 个播客")
            return podcasts
    except Exception as e:
        logger.error(f"PodcastIndex搜索失败 '{keyword}': {e}")
        return []


async def trending_podcastindex(session: aiohttp.ClientSession,
                                 api_key: str, api_secret: str,
                                 lang: str = "", cat: str = "",
                                 notcat: str = "Music",
                                 max_results: int = 50) -> list[dict]:
    """获取 Podcast Index 趋势榜单"""
    url = f"{PODCASTINDEX_BASE}/podcasts/trending"
    params = {"max": min(max_results, 1000), "notcat": notcat}
    if lang:
        params["lang"] = lang
    if cat:
        params["cat"] = cat
    headers = _build_podcastindex_headers(api_key, api_secret)

    try:
        async with session.get(url, params=params, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning(f"PodcastIndex趋势 (lang={lang}, cat={cat}) 返回 {resp.status}")
                return []
            data = await resp.json(content_type=None)
            feeds = data.get("feeds", [])
            podcasts = []
            for f in feeds:
                feed_url = f.get("url", "") or f.get("originalUrl", "")
                if not feed_url:
                    continue
                genres = _extract_genres(f.get("categories"))
                feed_lang = (f.get("language") or "")[:2].lower()
                podcasts.append({
                    "feed_url": feed_url,
                    "name": f.get("title", ""),
                    "artist": f.get("author", ""),
                    "genre": ", ".join(genres),
                    "genres": genres,
                    "language": feed_lang,
                })
            label = f"lang={lang}" if lang else "all"
            if cat:
                label += f", cat={cat}"
            logger.info(f"PodcastIndex趋势 ({label}): 找到 {len(podcasts)} 个播客")
            return podcasts
    except Exception as e:
        logger.error(f"PodcastIndex趋势获取失败: {e}")
        return []


def _store_podcasts(feed_store: FeedStore, podcasts: list[dict], via: str) -> int:
    """将播客列表存入 FeedStore，返回新增数"""
    new = 0
    for p in podcasts:
        if not feed_store.feed_exists(p["feed_url"]):
            feed_store.add_feed(
                feed_url=p["feed_url"],
                name=p["name"], artist=p["artist"],
                genre=p["genre"], language=p["language"],
                via=via,
            )
            new += 1
    return new


async def discover_via_podcastindex(
    api_key: str, api_secret: str,
    keywords: list[str] | None = None,
    do_trending: bool = False,
):
    """主流程：通过 Podcast Index 搜索 + 趋势发现 feeds，然后解析 RSS 入库"""
    feed_store = FeedStore()
    storage = Storage()

    new_feeds = 0
    async with aiohttp.ClientSession() as session:
        # ── 关键词搜索 ──
        if keywords:
            logger.info(f"[PodcastIndex] 开始关键词搜索, {len(keywords)} 个关键词")
            for keyword in keywords:
                await asyncio.sleep(0.3)
                podcasts = await search_podcastindex(
                    session, keyword, api_key, api_secret,
                )
                added = _store_podcasts(feed_store, podcasts, f"podcastindex:{keyword}")
                new_feeds += added

        # ── 趋势榜单 ──
        if do_trending:
            logger.info(f"[PodcastIndex] 开始拉取趋势榜单")
            for lang in ("zh", "en"):
                await asyncio.sleep(0.3)
                podcasts = await trending_podcastindex(
                    session, api_key, api_secret,
                    lang=lang, notcat="Music", max_results=100,
                )
                added = _store_podcasts(feed_store, podcasts, f"podcastindex:trending:{lang}")
                new_feeds += added

            for cat in PODCASTINDEX_CATEGORIES_FOR_SPEECH:
                await asyncio.sleep(0.3)
                podcasts = await trending_podcastindex(
                    session, api_key, api_secret,
                    cat=cat, notcat="Music", max_results=50,
                )
                added = _store_podcasts(feed_store, podcasts, f"podcastindex:trending:{cat}")
                new_feeds += added

    logger.info(f"[PodcastIndex] 发现阶段完成: 新增 {new_feeds} 个 RSS feeds, 总计 {feed_store.count()} 个")

    # ── 解析未爬取过的 feeds ──
    uncrawled = feed_store.get_uncrawled()
    if not uncrawled:
        logger.info("没有新的 feed 需要解析")
        storage.show_stats()
        return

    logger.info(f"开始解析 {len(uncrawled)} 个新 RSS feeds...")
    total_new = 0
    async with aiohttp.ClientSession() as session:
        for i, feed in enumerate(uncrawled, 1):
            feed_url = feed["feed_url"]
            feed_name = feed["podcast_name"] or feed_url[:60]
            await asyncio.sleep(0.3)

            feed_genres = [g.strip() for g in (feed.get("genre") or "").split(",") if g.strip()]
            records = await parse_rss_feed(session, feed_url, genres=feed_genres)
            feed_store.mark_crawled(feed_url, len(records))

            if records:
                new_records = [r for r in records if not storage.url_exists(r.url)]
                if new_records:
                    added = storage.add_urls_batch(new_records)
                    total_new += added
                    logger.info(f"  [{i}/{len(uncrawled)}] {feed_name}: +{added} 条")
                else:
                    logger.debug(f"  [{i}/{len(uncrawled)}] {feed_name}: 全部已存在")

            if i % 20 == 0:
                logger.info(f"  进度: {i}/{len(uncrawled)}, 累计新增 {total_new} 条 URL")

    logger.info(f"\n解析完成, 新增 {total_new} 条音频 URL")
    storage.show_stats()


def setup_logging():
    log_file = f"{LOG_DIR}/discover_podcastindex_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def main():
    parser = argparse.ArgumentParser(
        description="AudioSpider — 通过 Podcast Index 发现播客源",
    )
    parser.add_argument("--api-key", default=os.environ.get("PODCAST_INDEX_KEY", ""),
                        help="Podcast Index API Key (或设环境变量 PODCAST_INDEX_KEY)")
    parser.add_argument("--api-secret", default=os.environ.get("PODCAST_INDEX_SECRET", ""),
                        help="Podcast Index API Secret (或设环境变量 PODCAST_INDEX_SECRET)")
    parser.add_argument("--keywords", nargs="*", default=None,
                        help="搜索关键词（默认使用内置列表）")
    parser.add_argument("--trending", action="store_true",
                        help="拉取趋势榜单（按分类遍历，不需要关键词）")
    parser.add_argument("--no-search", action="store_true",
                        help="跳过关键词搜索（仅与 --trending 配合使用）")

    args = parser.parse_args()
    setup_logging()

    if not args.api_key or not args.api_secret:
        logger.error(
            "需要 Podcast Index API 凭据。请设置环境变量:\n"
            "  export PODCAST_INDEX_KEY='your_key'\n"
            "  export PODCAST_INDEX_SECRET='your_secret'\n"
            "或使用命令行参数 --api-key / --api-secret\n"
            "免费注册: https://podcastindex.org"
        )
        sys.exit(1)

    keywords = None if args.no_search else (args.keywords or DEFAULT_KEYWORDS)

    asyncio.run(discover_via_podcastindex(
        api_key=args.api_key,
        api_secret=args.api_secret,
        keywords=keywords,
        do_trending=args.trending,
    ))


if __name__ == "__main__":
    main()
