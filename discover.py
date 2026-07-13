#!/usr/bin/env python3
# Copyright (c) 2026 Hao Yin. All rights reserved.

"""自动发现新的语音源 — 搜索播客目录，自动扩充 URL 池（仅采集中英文）

两个数据源：
1. Apple Podcasts — 关键词搜索 + 88个分类遍历（免费，无需注册）
2. Podcast Index — 400万+播客，关键词搜索 + 热门 + 分页遍历（免费，需注册 API Key）

用法：
    python discover.py                           # 两个源全部启用（默认）
    python discover.py --source apple             # 只用 Apple
    python discover.py --source podcastindex       # 只用 Podcast Index
    python discover.py --keywords 脱口秀 TED       # 自定义关键词
    python discover.py --top 200                  # Apple关键词搜索每词取前200
    python discover.py --pi-max-pages 50          # Podcast Index 最近feeds翻页数
    python discover.py --loop --interval 86400    # 每天自动搜索一次
    python discover.py --stats                    # 查看统计
"""

import argparse
import asyncio
import hashlib
import logging
import sqlite3
import sys
import time
from datetime import datetime

import re

import aiohttp

from anti_crawler import build_headers, random_delay
from config import LOG_DIR, DB_PATH, PODCAST_INDEX_KEY, PODCAST_INDEX_SECRET
from storage import Storage, AudioRecord

logger = logging.getLogger("discover")

DEFAULT_KEYWORDS = [
    # ── 曲艺 / 表演 ──
    "相声", "评书", "单口相声", "小品", "快板", "说书",
    # ── 有声书 / 文学 ──
    "有声书", "有声小说", "网络小说", "名著", "文学", "散文",
    "小说连播", "长篇小说", "武侠小说", "言情小说",
    # ── 播客 / 脱口秀 ──
    "播客", "中文播客", "脱口秀", "单口喜剧", "吐槽",
    # ── 广播剧 / 配音 ──
    "广播剧", "声优", "配音", "有声剧",
    # ── 朗读 / 诵读 ──
    "朗读", "诵读", "朗诵", "诗歌朗诵", "经典诵读", "美文朗读",
    # ── 演讲 / 辩论 ──
    "演讲", "公开演讲", "辩论", "奇葩说",
    # ── 新闻 / 时事 ──
    "新闻播报", "新闻评论", "时事", "深度报道", "财经新闻",
    # ── 访谈 / 对话 ──
    "访谈", "人物访谈", "对话", "圆桌", "深度对谈",
    # ── 故事 / 纪实 ──
    "故事", "真实故事", "悬疑故事", "恐怖故事", "灵异", "纪实",
    "口述历史", "人物传记",
    # ── 历史 / 人文 ──
    "历史", "中国历史", "世界历史", "人文", "哲学", "社科",
    # ── 教育 / 知识 ──
    "读书", "科普", "知识", "学习", "教育",
    "心理学", "经济学", "法律", "医学科普",
    # ── 亲子 / 儿童 ──
    "睡前故事", "儿童故事", "童话", "育儿", "亲子",
    # ── 情感 / 生活 ──
    "情感电台", "深夜电台", "心理", "成长", "职场",
    # ── 商业 / 创业 ──
    "创业", "投资", "商业", "财经",
    # ── 方言 ──
    "粤语", "四川话", "上海话", "方言",
    # ── 语言学习 ──
    "学英语", "英语口语", "日语学习",
    # ── 国学 / 经典 ──
    "国学", "论语", "道德经", "古诗词", "三字经",
    # ── English ──
    "podcast", "audiobook", "speech", "storytelling",
    "TED talks", "interview", "news podcast",
    "true crime", "comedy podcast", "history podcast",
    "science podcast", "business podcast", "self help",
    "parenting", "documentary", "narrative podcast",
    "talk show", "radio drama", "motivational",
    "debate", "philosophy podcast", "psychology",
    "language learning", "educational podcast",
]

APPLE_SEARCH_URL = "https://itunes.apple.com/search"

# Apple Podcasts 全部分类 Genre ID（用于分类遍历）
# 1302 = Podcasts 根分类；下面列出所有子分类
APPLE_PODCAST_GENRES = {
    # 主分类
    1301: "Arts",
    1303: "Comedy",
    1304: "Education",
    1305: "Kids & Family",
    1307: "Health & Fitness",
    1309: "TV & Film",
    1310: "Music",
    1311: "News",
    1314: "Religion & Spirituality",
    1315: "Science",
    1316: "Sports",
    1318: "Technology",
    1321: "Business",
    1323: "Games & Hobbies",      # 一些地区可用
    1324: "Society & Culture",
    1325: "Government",
    1401: "Fiction",
    1402: "True Crime",
    1404: "History",
    1405: "Leisure",
    # 子分类（部分重要的）
    1406: "Animation & Manga",
    1459: "Documentary",
    1461: "Entertainment News",
    1462: "Sports News",
    1463: "Tech News",
    1464: "Business News",
    1465: "Daily News",
    1466: "News Commentary",
    1467: "Politics",
    1468: "Investing",
    1469: "Management",
    1470: "Marketing",
    1471: "Non-Profit",
    1472: "Parenting",
    1473: "Pets & Animals",
    1474: "Places & Travel",
    1475: "Relationships",
    1477: "Philosophy",
    1478: "Spirituality",
    1480: "Baseball",
    1481: "Basketball",
    1482: "Cricket",
    1483: "Fantasy Sports",
    1484: "Football",
    1485: "Golf",
    1486: "Hockey",
    1487: "Rugby",
    1488: "Soccer",
    1489: "Swimming",
    1490: "Tennis",
    1491: "Wilderness",
    1492: "Wrestling",
    1493: "Astronomy",
    1494: "Chemistry",
    1495: "Earth Sciences",
    1496: "Life Sciences",
    1497: "Mathematics",
    1498: "Natural Sciences",
    1499: "Nature",
    1500: "Physics",
    1501: "Social Sciences",
    1502: "Comedy Fiction",
    1503: "Drama",
    1504: "Science Fiction",
    1543: "Self-Improvement",
    1544: "Mental Health",
    1545: "Nutrition",
    1546: "Fitness",
    1547: "Medicine",
    1548: "Alternative Health",
    1549: "Sexuality",
    1550: "Education for Kids",
    1551: "Stories for Kids",
    1553: "Design",
    1554: "Fashion & Beauty",
    1555: "Food",
    1556: "Performing Arts",
    1557: "Visual Arts",
    1558: "Books",
    1559: "Music Commentary",
    1560: "Music History",
    1561: "Music Interviews",
    1602: "Careers",
    1603: "Entrepreneurship",
    1604: "Improv",
    1605: "Stand-Up",
    1606: "Courses",
    1607: "How To",
    1608: "Language Learning",
    1609: "Self-Improvement (Education)",
    1610: "Comedy Interviews",
    1611: "After Shows",
    1612: "Film History",
    1613: "Film Interviews",
    1614: "Film Reviews",
    1615: "TV Reviews",
    1616: "Automotive",
    1617: "Aviation",
    1618: "Crafts",
    1619: "Games",
    1620: "Home & Garden",
    1621: "Video Games",
}

APPLE_SEARCH_COUNTRIES = ["cn", "us"]

# Podcast Index API
PODCAST_INDEX_BASE = "https://api.podcastindex.org/api/1.0"

GENRE_MAP = {
    # 英文 genre（美国区返回）
    "Comedy": "脱口秀",
    "Society & Culture": "访谈",
    "News": "新闻",
    "Education": "教育",
    "Business": "商业",
    "Technology": "科技",
    "Health & Fitness": "健康",
    "Arts": "文艺",
    "History": "历史",
    "True Crime": "纪实",
    "Sports": "体育",
    "Music": "音乐",
    "Fiction": "有声书",
    "Kids & Family": "亲子",
    "Religion & Spirituality": "宗教",
    "Science": "科学",
    "TV & Film": "影视",
    "Leisure": "休闲",
    "Government": "时政",
    # 中文 genre（中国区返回）
    "社会与文化": "访谈",
    "喜剧": "脱口秀",
    "新闻": "新闻",
    "教育": "教育",
    "商业": "商业",
    "科技": "科技",
    "健康与健身": "健康",
    "艺术": "文艺",
    "历史": "历史",
    "纪实犯罪": "纪实",
    "体育": "体育",
    "音乐": "音乐",
    "虚构": "有声书",
    "儿童与家庭": "亲子",
    "宗教与灵修": "宗教",
    "科学": "科学",
    "电视与电影": "影视",
    "休闲": "休闲",
    "政府": "时政",
    "个人日记": "访谈",
    "情感与人际关系": "情感",
}


class FeedStore:
    """管理已发现的 RSS feeds，避免重复搜索"""

    def __init__(self, db_path: str = DB_PATH):
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA temp_store = MEMORY")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS discovered_feeds (
                feed_url TEXT PRIMARY KEY,
                podcast_name TEXT DEFAULT '',
                artist TEXT DEFAULT '',
                genre TEXT DEFAULT '',
                language TEXT DEFAULT '',
                discovered_via TEXT DEFAULT '',
                discovered_at TEXT NOT NULL,
                last_crawled TEXT DEFAULT '',
                episode_count INTEGER DEFAULT 0
            );
        """)
        self.conn.commit()

    def feed_exists(self, feed_url: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM discovered_feeds WHERE feed_url=?", (feed_url,)
        ).fetchone()
        return row is not None

    def add_feed(self, feed_url: str, name: str = "", artist: str = "",
                 genre: str = "", language: str = "", via: str = ""):
        self.conn.execute(
            "INSERT OR IGNORE INTO discovered_feeds "
            "(feed_url, podcast_name, artist, genre, language, discovered_via, discovered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (feed_url, name, artist, genre, language, via, datetime.now().isoformat()),
        )
        self.conn.commit()

    def mark_crawled(self, feed_url: str, episode_count: int):
        self.conn.execute(
            "UPDATE discovered_feeds SET last_crawled=?, episode_count=? WHERE feed_url=?",
            (datetime.now().isoformat(), episode_count, feed_url),
        )
        self.conn.commit()

    def get_uncrawled(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM discovered_feeds WHERE last_crawled='' ORDER BY discovered_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM discovered_feeds ORDER BY discovered_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM discovered_feeds").fetchone()
        return row[0]


async def search_apple_podcasts(session: aiohttp.ClientSession,
                                 keyword: str, limit: int = 50,
                                 country: str = "cn") -> list[dict]:
    """通过 Apple Podcasts Search API 搜索播客，返回播客信息列表"""
    params = {
        "term": keyword,
        "media": "podcast",
        "limit": min(limit, 200),
        "country": country,
    }
    try:
        async with session.get(APPLE_SEARCH_URL, params=params,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning(f"Apple搜索 '{keyword}' (country={country}) 返回 {resp.status}")
                return []
            data = await resp.json(content_type=None)
            results = data.get("results", [])
            podcasts = []
            for r in results:
                feed_url = r.get("feedUrl", "")
                if not feed_url:
                    continue
                genres = r.get("genres", [])
                podcasts.append({
                    "feed_url": feed_url,
                    "name": r.get("collectionName", ""),
                    "artist": r.get("artistName", ""),
                    "genre": ", ".join(genres),
                    "genres": genres,
                    "language": "zh" if country == "cn" else "en",
                })
            logger.info(f"Apple搜索 '{keyword}' (country={country}): 找到 {len(podcasts)} 个播客")
            return podcasts
    except Exception as e:
        logger.error(f"Apple搜索失败 '{keyword}': {e}")
        return []


def _map_genre(genres: list[str]) -> str:
    """将 Apple Podcasts genre 映射为中文分类"""
    for g in genres:
        if g in GENRE_MAP:
            return GENRE_MAP[g]
    return "播客"


APPLE_TOP_CHARTS_URL = "https://itunes.apple.com/{country}/rss/toppodcasts/limit={limit}/genre={genre_id}/json"
APPLE_LOOKUP_URL = "https://itunes.apple.com/lookup"


async def search_apple_by_genre(session: aiohttp.ClientSession,
                                genre_id: int, genre_name: str,
                                country: str = "cn") -> list[dict]:
    """通过 Apple Top Charts API 获取分类下的热门播客排行榜，再用 Lookup API 批量获取 feed URL"""
    top_url = APPLE_TOP_CHARTS_URL.format(country=country, limit=200, genre_id=genre_id)
    try:
        # 第一步：从 Top Charts 获取播客 ID 列表
        async with session.get(top_url, timeout=aiohttp.ClientTimeout(total=15),
                               allow_redirects=True) as resp:
            if resp.status != 200:
                logger.debug(f"Apple Top Charts genre={genre_id} country={country} 返回 {resp.status}")
                return []
            data = await resp.json(content_type=None)
            entries = data.get("feed", {}).get("entry", [])
            if not entries:
                return []
            podcast_ids = []
            for entry in entries:
                pid = entry.get("id", {}).get("attributes", {}).get("im:id", "")
                if pid:
                    podcast_ids.append(pid)
            if not podcast_ids:
                return []

        # 第二步：用 Lookup API 批量获取 feed URL（每批最多 200 个）
        podcasts = []
        for batch_start in range(0, len(podcast_ids), 200):
            batch_ids = podcast_ids[batch_start:batch_start + 200]
            ids_str = ",".join(batch_ids)
            params = {"id": ids_str, "entity": "podcast", "country": country}
            await random_delay(0.3, 0.6)
            async with session.get(APPLE_LOOKUP_URL, params=params,
                                   timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    continue
                lookup_data = await resp.json(content_type=None)
                for r in lookup_data.get("results", []):
                    feed_url = r.get("feedUrl", "")
                    if not feed_url:
                        continue
                    genres = r.get("genres", [])
                    podcasts.append({
                        "feed_url": feed_url,
                        "name": r.get("collectionName", ""),
                        "artist": r.get("artistName", ""),
                        "genre": ", ".join(genres),
                        "genres": genres,
                        "language": "zh" if country == "cn" else "en",
                    })

        logger.info(f"Apple分类 [{genre_name}] (country={country}): 排行榜 {len(entries)} 个, 获取 feedUrl {len(podcasts)} 个")
        return podcasts
    except Exception as e:
        logger.error(f"Apple分类搜索失败 genre={genre_id}: {e}")
        return []


# ── Podcast Index API ──────────────────────────────────────────────────────

def _pi_auth_headers() -> dict:
    """生成 Podcast Index API 的认证 headers"""
    ts = str(int(time.time()))
    auth_hash = hashlib.sha1(
        (PODCAST_INDEX_KEY + PODCAST_INDEX_SECRET + ts).encode()
    ).hexdigest()
    return {
        "User-Agent": "AudioSpider/1.0",
        "X-Auth-Key": PODCAST_INDEX_KEY,
        "X-Auth-Date": ts,
        "Authorization": auth_hash,
    }


async def pi_search(session: aiohttp.ClientSession,
                     keyword: str, max_results: int = 200) -> list[dict]:
    """Podcast Index: 按关键词搜索播客"""
    url = f"{PODCAST_INDEX_BASE}/search/byterm"
    params = {"q": keyword, "max": min(max_results, 1000)}
    try:
        async with session.get(url, params=params, headers=_pi_auth_headers(),
                               timeout=aiohttp.ClientTimeout(total=20)) as resp:
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
                lang_raw = (f.get("language") or "").lower()
                language = "zh" if lang_raw.startswith("zh") else lang_raw[:2] if lang_raw else ""
                if language and language not in ("zh", "en"):
                    continue
                categories = f.get("categories", {}) or {}
                genre_str = ", ".join(categories.values()) if isinstance(categories, dict) else ""
                podcasts.append({
                    "feed_url": feed_url,
                    "name": f.get("title", ""),
                    "artist": f.get("author", ""),
                    "genre": genre_str,
                    "genres": list(categories.values()) if isinstance(categories, dict) else [],
                    "language": language,
                })
            logger.info(f"PodcastIndex搜索 '{keyword}': 找到 {len(podcasts)} 个播客")
            return podcasts
    except Exception as e:
        logger.error(f"PodcastIndex搜索失败 '{keyword}': {e}")
        return []


async def pi_recent_feeds(session: aiohttp.ClientSession,
                          max_pages: int = 20,
                          per_page: int = 1000) -> list[dict]:
    """Podcast Index: 遍历最近更新的 feeds（支持分页，每页最多 1000 条）"""
    url = f"{PODCAST_INDEX_BASE}/recent/feeds"
    all_podcasts = []
    since = None

    for page in range(max_pages):
        params = {"max": per_page, "lang": "zh,en"}
        if since:
            params["since"] = since
        try:
            await random_delay(0.3, 0.8)
            async with session.get(url, params=params, headers=_pi_auth_headers(),
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning(f"PodcastIndex recent feeds 第{page+1}页 返回 {resp.status}")
                    break
                data = await resp.json(content_type=None)
                feeds = data.get("feeds", [])
                if not feeds:
                    logger.info(f"PodcastIndex recent feeds: 第{page+1}页无数据，结束翻页")
                    break

                page_podcasts = []
                for f in feeds:
                    feed_url = f.get("url", "") or f.get("originalUrl", "")
                    if not feed_url:
                        continue
                    lang_raw = (f.get("language") or "").lower()
                    language = "zh" if lang_raw.startswith("zh") else lang_raw[:2] if lang_raw else ""
                    if language and language not in ("zh", "en"):
                        continue
                    categories = f.get("categories", {}) or {}
                    genre_str = ", ".join(categories.values()) if isinstance(categories, dict) else ""
                    page_podcasts.append({
                        "feed_url": feed_url,
                        "name": f.get("title", ""),
                        "artist": f.get("author", ""),
                        "genre": genre_str,
                        "genres": list(categories.values()) if isinstance(categories, dict) else [],
                        "language": language,
                    })

                all_podcasts.extend(page_podcasts)
                oldest_ts = feeds[-1].get("newestItemPublishTime", 0)
                if oldest_ts:
                    since = oldest_ts - 1
                else:
                    break

                logger.info(f"PodcastIndex recent feeds 第{page+1}页: +{len(page_podcasts)} 个, 累计 {len(all_podcasts)}")

                if len(feeds) < per_page:
                    break
        except Exception as e:
            logger.error(f"PodcastIndex recent feeds 第{page+1}页失败: {e}")
            break

    logger.info(f"PodcastIndex recent feeds 总计: {len(all_podcasts)} 个播客")
    return all_podcasts


async def pi_trending(session: aiohttp.ClientSession,
                      max_results: int = 100) -> list[dict]:
    """Podcast Index: 获取热门播客"""
    url = f"{PODCAST_INDEX_BASE}/podcasts/trending"
    params = {"max": min(max_results, 1000), "lang": "zh,en"}
    try:
        async with session.get(url, params=params, headers=_pi_auth_headers(),
                               timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                logger.warning(f"PodcastIndex trending 返回 {resp.status}")
                return []
            data = await resp.json(content_type=None)
            feeds = data.get("feeds", [])
            podcasts = []
            for f in feeds:
                feed_url = f.get("url", "") or f.get("originalUrl", "")
                if not feed_url:
                    continue
                lang_raw = (f.get("language") or "").lower()
                language = "zh" if lang_raw.startswith("zh") else lang_raw[:2] if lang_raw else ""
                if language and language not in ("zh", "en"):
                    continue
                categories = f.get("categories", {}) or {}
                genre_str = ", ".join(categories.values()) if isinstance(categories, dict) else ""
                podcasts.append({
                    "feed_url": feed_url,
                    "name": f.get("title", ""),
                    "artist": f.get("author", ""),
                    "genre": genre_str,
                    "genres": list(categories.values()) if isinstance(categories, dict) else [],
                    "language": language,
                })
            logger.info(f"PodcastIndex trending: 找到 {len(podcasts)} 个播客")
            return podcasts
    except Exception as e:
        logger.error(f"PodcastIndex trending 失败: {e}")
        return []


async def pi_categories(session: aiohttp.ClientSession) -> list[dict]:
    """Podcast Index: 获取所有分类列表"""
    url = f"{PODCAST_INDEX_BASE}/categories/list"
    try:
        async with session.get(url, headers=_pi_auth_headers(),
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
            return data.get("feeds", [])
    except Exception as e:
        logger.error(f"PodcastIndex categories 失败: {e}")
        return []


MAX_RSS_SIZE = 20 * 1024 * 1024  # 20MB，超过此大小的 RSS 跳过

# 纯正则提取 RSS 内容，不依赖任何 XML 解析器，彻底避免 segfault
_RE_CHANNEL_TITLE = re.compile(r"<channel[^>]*>.*?<title[^>]*>(.*?)</title>", re.S)
_RE_LANGUAGE = re.compile(r"<language[^>]*>(.*?)</language>", re.S | re.I)
_RE_ITEM = re.compile(r"<item[\s>].*?</item>", re.S)
_RE_ENCLOSURE = re.compile(r'<enclosure\s[^>]*?url=["\']([^"\']+)["\'][^>]*/?\s*>', re.S)
_RE_ENCLOSURE_TYPE = re.compile(r'<enclosure\s[^>]*?type=["\']([^"\']+)["\']', re.S)
_RE_ENCLOSURE_LENGTH = re.compile(r'<enclosure\s[^>]*?length=["\'](\d+)["\']', re.S)
_RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S)
_RE_GUID = re.compile(r"<guid[^>]*>(.*?)</guid>", re.S)
_RE_DURATION = re.compile(r"<(?:itunes:)?duration[^>]*>(.*?)</(?:itunes:)?duration>", re.S)
_RE_PUBDATE = re.compile(r"<pubDate[^>]*>(.*?)</pubDate>", re.S | re.I)
_RE_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)


def _strip_cdata(text: str) -> str:
    m = _RE_CDATA.search(text)
    return m.group(1).strip() if m else text.strip()


async def parse_rss_feed(session: aiohttp.ClientSession,
                          feed_url: str, max_eps: int = 0,
                          genres: list[str] | None = None) -> list[AudioRecord]:
    """解析单个 RSS feed，返回音频记录。使用正则提取，不依赖 XML 解析器。"""
    records = []
    try:
        async with session.get(feed_url, headers=build_headers(),
                               timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                return []
            content_length = resp.content_length or 0
            if content_length > MAX_RSS_SIZE:
                logger.debug(f"RSS 过大({content_length // 1024 // 1024}MB), 跳过: {feed_url}")
                return []
            # 限量读取，避免超大响应导致崩溃
            chunks = []
            total_read = 0
            async for chunk in resp.content.iter_chunked(65536):
                total_read += len(chunk)
                if total_read > MAX_RSS_SIZE:
                    logger.debug(f"RSS 读取超限({total_read // 1024 // 1024}MB), 跳过: {feed_url}")
                    return []
                chunks.append(chunk)
            raw = b"".join(chunks)

            text = raw.decode("utf-8", errors="replace")

            # 提取播客标题
            podcast_title = ""
            m = _RE_CHANNEL_TITLE.search(text)
            if m:
                podcast_title = _strip_cdata(m.group(1))

            # 提取语言
            language = ""
            m = _RE_LANGUAGE.search(text)
            if m:
                lang_raw = m.group(1).strip().lower()
                language = "zh" if lang_raw.startswith("zh") else lang_raw[:2]

            # 提取所有 <item>
            items = _RE_ITEM.findall(text)
            if max_eps > 0:
                items = items[:max_eps]

            for item_text in items:
                m_enc = _RE_ENCLOSURE.search(item_text)
                if not m_enc:
                    continue
                audio_url = m_enc.group(1).strip()
                if not audio_url:
                    continue

                # 检查 MIME 类型
                m_type = _RE_ENCLOSURE_TYPE.search(item_text)
                mime = m_type.group(1) if m_type else ""
                if "audio" not in mime:
                    ext = audio_url.rsplit(".", 1)[-1].split("?")[0].lower()
                    if ext not in ("mp3", "m4a", "ogg", "aac", "wav", "opus"):
                        continue

                # HTML 实体还原（部分 RSS 会对 URL 中的 & 转义）
                audio_url = audio_url.replace("&amp;", "&")

                m_title = _RE_TITLE.search(item_text)
                title = _strip_cdata(m_title.group(1)) if m_title else ""

                m_guid = _RE_GUID.search(item_text)
                guid = _strip_cdata(m_guid.group(1)) if m_guid else audio_url

                m_dur = _RE_DURATION.search(item_text)
                duration = _parse_duration(m_dur.group(1).strip()) if m_dur else 0

                m_len = _RE_ENCLOSURE_LENGTH.search(item_text)
                size = int(m_len.group(1)) if m_len else 0

                m_pub = _RE_PUBDATE.search(item_text)
                published_at = _parse_pubdate(m_pub.group(1).strip()) if m_pub else ""

                ext = audio_url.rsplit(".", 1)[-1].split("?")[0].lower()
                fmt = ext if ext in ("m4a", "ogg", "aac", "wav", "opus") else "mp3"

                category = _map_genre(genres or []) if genres else "播客"

                record = AudioRecord(
                    url=audio_url, source="podcast_rss",
                    title=title, file_format=fmt,
                    file_size=size, duration=duration,
                    category=category, language=language,
                    speaker=podcast_title, source_id=guid,
                    published_at=published_at,
                )
                records.append(record)

    except Exception as e:
        logger.debug(f"RSS 解析失败 {feed_url}: {e}")
    return records


def _parse_pubdate(text: str) -> str:
    """解析 RSS pubDate 为 ISO 8601"""
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(text)
        return dt.isoformat()
    except Exception:
        return ""


def _parse_duration(text: str) -> int:
    text = text.strip()
    if ":" in text:
        parts = [int(p) for p in text.split(":") if p.isdigit()]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        elif len(parts) == 2:
            return parts[0] * 60 + parts[1]
    if text.isdigit():
        return int(text)
    return 0


def _store_podcasts(feed_store: FeedStore, podcasts: list[dict], via: str) -> int:
    """将播客列表写入 feed_store，返回新增数量"""
    new = 0
    for p in podcasts:
        if not feed_store.feed_exists(p["feed_url"]):
            feed_store.add_feed(
                feed_url=p["feed_url"],
                name=p["name"], artist=p["artist"],
                genre=p["genre"], language=p.get("language", ""),
                via=via,
            )
            new += 1
    return new


async def discover_and_collect(keywords: list[str], top: int = 200,
                                sources: list[str] | None = None,
                                pi_max_pages: int = 20,
                                parse_only: bool = False):
    """主流程：搜索 → 发现 feeds → 解析 → 入库

    Args:
        sources: 启用的数据源，可选 "apple", "podcastindex"，默认全部启用
        pi_max_pages: Podcast Index recent feeds 最大翻页数
        parse_only: 若为 True，跳过发现阶段，只解析未解析的 feeds
    """
    active = set(sources or ["apple", "podcastindex"])
    if "apple" in active:
        active.update(["apple_keyword", "apple_genre"])

    feed_store = FeedStore()
    storage = Storage()

    new_feeds = 0

    if parse_only:
        logger.info("跳过发现阶段，直接解析未解析的 feeds...")
    else:
        logger.info(f"开始自动发现, 启用源: {active}")
        async with aiohttp.ClientSession() as session:

            # ── Apple Podcasts 关键词搜索 ──
            if "apple_keyword" in active:
                logger.info(f"\n{'='*50}")
                logger.info(f"[Apple] 关键词搜索: {len(keywords)} 个关键词, 每词最多 {top} 个")
                logger.info(f"{'='*50}")
                for i, keyword in enumerate(keywords, 1):
                    for country in APPLE_SEARCH_COUNTRIES:
                        await random_delay(0.5, 1.5)
                        podcasts = await search_apple_podcasts(session, keyword, limit=top, country=country)
                        added = _store_podcasts(feed_store, podcasts, f"apple_kw:{keyword}:{country}")
                        new_feeds += added
                    if i % 10 == 0:
                        logger.info(f"  关键词进度: {i}/{len(keywords)}, 累计新增 {new_feeds} 个 feeds")

            # ── Apple Podcasts 分类遍历 ──
            if "apple_genre" in active:
                logger.info(f"\n{'='*50}")
                logger.info(f"[Apple] 分类遍历: {len(APPLE_PODCAST_GENRES)} 个分类 × {len(APPLE_SEARCH_COUNTRIES)} 个地区")
                logger.info(f"{'='*50}")
                genre_count = 0
                for genre_id, genre_name in APPLE_PODCAST_GENRES.items():
                    for country in APPLE_SEARCH_COUNTRIES:
                        await random_delay(0.5, 1.5)
                        podcasts = await search_apple_by_genre(session, genre_id, genre_name, country)
                        added = _store_podcasts(feed_store, podcasts, f"apple_genre:{genre_id}:{country}")
                        new_feeds += added
                        genre_count += 1
                    if genre_count % 20 == 0:
                        logger.info(f"  分类进度: {genre_count}/{len(APPLE_PODCAST_GENRES) * len(APPLE_SEARCH_COUNTRIES)}, "
                                    f"累计新增 {new_feeds} 个 feeds")

            # ── Podcast Index（关键词搜索 + 热门 + 最近更新分页遍历）──
            if "podcastindex" in active:
                if not PODCAST_INDEX_KEY or not PODCAST_INDEX_SECRET:
                    logger.warning(
                        "[PodcastIndex] 未配置 API Key，跳过。"
                        "请设置环境变量 PODCAST_INDEX_KEY 和 PODCAST_INDEX_SECRET，"
                        "或在 config.py 中配置。免费注册: https://api.podcastindex.org/"
                    )
                else:
                    logger.info(f"\n{'='*50}")
                    logger.info(f"[PodcastIndex] 开始搜索")
                    logger.info(f"{'='*50}")

                    logger.info("[PodcastIndex] 关键词搜索...")
                    for keyword in keywords:
                        await random_delay(0.3, 0.8)
                        podcasts = await pi_search(session, keyword)
                        added = _store_podcasts(feed_store, podcasts, f"pi_search:{keyword}")
                        new_feeds += added

                    logger.info("[PodcastIndex] 获取热门播客...")
                    podcasts = await pi_trending(session, max_results=1000)
                    added = _store_podcasts(feed_store, podcasts, "pi_trending")
                    new_feeds += added

                    logger.info(f"[PodcastIndex] 遍历最近更新的 feeds (最多 {pi_max_pages} 页)...")
                    podcasts = await pi_recent_feeds(session, max_pages=pi_max_pages)
                    added = _store_podcasts(feed_store, podcasts, "pi_recent")
                    new_feeds += added

        logger.info(f"\n发现阶段完成: 新增 {new_feeds} 个 RSS feeds, 总计 {feed_store.count()} 个")

    # ── 解析未爬取过的 feeds ──
    uncrawled = feed_store.get_uncrawled()
    if not uncrawled:
        logger.info("没有新的 feed 需要解析")
        storage.show_stats()
        return

    logger.info(f"\n开始解析 {len(uncrawled)} 个新 RSS feeds...")
    total_new = 0
    async with aiohttp.ClientSession() as session:
        for i, feed in enumerate(uncrawled, 1):
            feed_url = feed["feed_url"]
            feed_name = feed["podcast_name"] or feed_url[:60]
            await random_delay(0.3, 0.8)

            print(f"  >> 解析中 [{i}/{len(uncrawled)}] {feed_name} | {feed_url}", flush=True)

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
    log_file = f"{LOG_DIR}/discover_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def main():
    parser = argparse.ArgumentParser(description="AudioSpider 自动发现新的语音源")
    parser.add_argument("--keywords", nargs="*", default=None,
                        help="自定义搜索关键词（默认使用内置关键词列表）")
    parser.add_argument("--top", type=int, default=200,
                        help="Apple关键词搜索每词最多发现多少个播客(默认200, 上限200)")
    parser.add_argument("--source", nargs="*", default=None,
                        choices=["apple", "apple_keyword", "apple_genre", "podcastindex", "all"],
                        help="选择数据源: apple(关键词+分类), apple_keyword(仅关键词), "
                             "apple_genre(仅分类), podcastindex, all(全部,默认)")
    parser.add_argument("--pi-max-pages", type=int, default=20,
                        help="Podcast Index recent feeds 最大翻页数(默认20, 每页1000条)")
    parser.add_argument("--loop", action="store_true", help="持续循环发现")
    parser.add_argument("--interval", type=int, default=86400, help="循环间隔秒数(默认1天)")
    parser.add_argument("--parse-only", action="store_true",
                        help="跳过发现阶段，只解析数据库中未解析的 feeds")
    parser.add_argument("--list-feeds", action="store_true", help="列出所有已发现的 feeds")
    parser.add_argument("--stats", action="store_true", help="显示已发现 feeds 的统计信息")

    args = parser.parse_args()
    setup_logging()

    if args.list_feeds:
        feed_store = FeedStore()
        feeds = feed_store.get_all()
        print(f"\n已发现 {len(feeds)} 个 RSS feeds:\n")
        for f in feeds:
            status = f"({f['episode_count']}集)" if f["last_crawled"] else "(未解析)"
            print(f"  {f['podcast_name'][:30]:<32} {status:<12} {f['feed_url'][:70]}")
        return

    if args.stats:
        feed_store = FeedStore()
        feeds = feed_store.get_all()
        total = len(feeds)
        crawled = sum(1 for f in feeds if f["last_crawled"])
        uncrawled = total - crawled
        by_via = {}
        for f in feeds:
            source = (f.get("discovered_via") or "unknown").split(":")[0]
            by_via[source] = by_via.get(source, 0) + 1
        print(f"\n已发现 feeds 统计:")
        print(f"  总计: {total}")
        print(f"  已解析: {crawled}")
        print(f"  未解析: {uncrawled}")
        print(f"  按来源:")
        for src, cnt in sorted(by_via.items(), key=lambda x: -x[1]):
            print(f"    {src}: {cnt}")
        return

    # 解析 --source 参数
    sources = None
    if args.source:
        if "all" in args.source:
            sources = None
        else:
            sources = list(set(args.source))

    keywords = args.keywords or DEFAULT_KEYWORDS

    if args.loop:
        async def loop():
            round_num = 0
            while True:
                round_num += 1
                logger.info(f"\n{'#' * 60}")
                logger.info(f"# 第 {round_num} 轮自动发现 — {datetime.now():%Y-%m-%d %H:%M:%S}")
                logger.info(f"{'#' * 60}")
                await discover_and_collect(keywords, args.top,
                                            sources=sources,
                                            pi_max_pages=args.pi_max_pages,
                                            parse_only=args.parse_only)
                logger.info(f"等待 {args.interval} 秒...")
                await asyncio.sleep(args.interval)
        asyncio.run(loop())
    else:
        asyncio.run(discover_and_collect(keywords, args.top,
                                          sources=sources,
                                          pi_max_pages=args.pi_max_pages,
                                          parse_only=args.parse_only))


if __name__ == "__main__":
    main()
