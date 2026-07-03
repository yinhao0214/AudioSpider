#!/usr/bin/env python3
# Copyright (c) 2026 Hao Yin. All rights reserved.

"""自动发现新的语音源 — 搜索播客目录，自动扩充 URL 池

核心策略：
1. Apple Podcasts Search API（免费，无需认证）按关键词搜索中英文播客
2. 提取每个播客的 RSS 地址
3. 解析 RSS 获取音频 URL，写入数据库

用法：
    python discover.py                        # 用默认关键词搜索
    python discover.py --keywords 脱口秀 TED   # 自定义关键词
    python discover.py --top 100              # 每个关键词取前100个播客
    python discover.py --loop --interval 86400 # 每天自动搜索一次
"""

import argparse
import asyncio
import logging
import sqlite3
import sys
from datetime import datetime

import aiohttp
from bs4 import BeautifulSoup

from anti_crawler import build_headers, random_delay
from config import LOG_DIR, DB_PATH
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
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
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


async def parse_rss_feed(session: aiohttp.ClientSession,
                          feed_url: str, max_eps: int = 50,
                          genres: list[str] | None = None) -> list[AudioRecord]:
    """解析单个 RSS feed，返回音频记录"""
    records = []
    try:
        async with session.get(feed_url, headers=build_headers(),
                               timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return []
            text = await resp.text()
            soup = BeautifulSoup(text, "lxml-xml")

            podcast_title = ""
            channel = soup.find("channel")
            if channel:
                t = channel.find("title", recursive=False)
                if t:
                    podcast_title = t.get_text(strip=True)

            language = ""
            lang_tag = soup.find("language")
            if lang_tag:
                lang_raw = lang_tag.get_text(strip=True).lower()
                language = "zh" if lang_raw.startswith("zh") else lang_raw[:2]

            for item in list(soup.find_all("item"))[:max_eps]:
                enclosure = item.find("enclosure")
                if not enclosure:
                    continue
                audio_url = enclosure.get("url", "")
                mime = enclosure.get("type", "")
                if not audio_url:
                    continue
                if "audio" not in mime:
                    ext = audio_url.rsplit(".", 1)[-1].split("?")[0].lower()
                    if ext not in ("mp3", "m4a", "ogg", "aac", "wav", "opus"):
                        continue

                title_tag = item.find("title")
                title = title_tag.get_text(strip=True) if title_tag else ""

                guid_tag = item.find("guid")
                guid = guid_tag.get_text(strip=True) if guid_tag else audio_url

                duration_tag = item.find("itunes:duration") or item.find("duration")
                duration = 0
                if duration_tag:
                    duration = _parse_duration(duration_tag.get_text(strip=True))

                ext = audio_url.rsplit(".", 1)[-1].split("?")[0].lower()
                fmt = ext if ext in ("m4a", "ogg", "aac", "wav", "opus") else "mp3"
                size = int(enclosure.get("length", 0) or 0)

                category = _map_genre(genres or []) if genres else "播客"

                record = AudioRecord(
                    url=audio_url, source="podcast_rss",
                    title=title, file_format=fmt,
                    file_size=size, duration=duration,
                    category=category, language=language,
                    speaker=podcast_title, source_id=guid,
                )
                records.append(record)

    except Exception as e:
        logger.debug(f"RSS 解析失败 {feed_url}: {e}")
    return records


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


async def discover_and_collect(keywords: list[str], top: int = 50):
    """主流程：搜索 → 发现 feeds → 解析 → 入库"""
    feed_store = FeedStore()
    storage = Storage()

    logger.info(f"开始自动发现, 关键词: {len(keywords)} 个, 每词最多 {top} 个播客")

    new_feeds = 0
    async with aiohttp.ClientSession() as session:
        # 搜索 Apple Podcasts
        for keyword in keywords:
            for country in ("cn", "us"):
                await random_delay(0.5, 1.5)
                podcasts = await search_apple_podcasts(session, keyword, limit=top, country=country)
                for p in podcasts:
                    if not feed_store.feed_exists(p["feed_url"]):
                        feed_store.add_feed(
                            feed_url=p["feed_url"],
                            name=p["name"], artist=p["artist"],
                            genre=p["genre"], language=p["language"],
                            via=f"apple:{keyword}:{country}",
                        )
                        new_feeds += 1

    logger.info(f"发现阶段完成: 新增 {new_feeds} 个 RSS feeds, 总计 {feed_store.count()} 个")

    # 解析未爬取过的 feeds
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
            await random_delay(0.3, 0.8)

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
    parser.add_argument("--top", type=int, default=50,
                        help="每个关键词最多发现多少个播客(默认50)")
    parser.add_argument("--loop", action="store_true", help="持续循环发现")
    parser.add_argument("--interval", type=int, default=86400, help="循环间隔秒数(默认1天)")
    parser.add_argument("--list-feeds", action="store_true", help="列出所有已发现的 feeds")

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

    keywords = args.keywords or DEFAULT_KEYWORDS

    if args.loop:
        async def loop():
            round_num = 0
            while True:
                round_num += 1
                logger.info(f"\n{'#' * 60}")
                logger.info(f"# 第 {round_num} 轮自动发现 — {datetime.now():%Y-%m-%d %H:%M:%S}")
                logger.info(f"{'#' * 60}")
                await discover_and_collect(keywords, args.top)
                logger.info(f"等待 {args.interval} 秒...")
                await asyncio.sleep(args.interval)
        asyncio.run(loop())
    else:
        asyncio.run(discover_and_collect(keywords, args.top))


if __name__ == "__main__":
    main()
