# Copyright (c) 2026 Hao Yin. All rights reserved.

"""LibriVox 爬虫 - 公共领域有声书，提供免费 API，无反爬"""

import aiohttp

from anti_crawler import build_headers, random_delay, RateLimiter
from config import SPIDER_CONFIGS
from spiders.base import BaseSpider
from storage import AudioRecord


class LibriVoxSpider(BaseSpider):
    name = "librivox"

    def __init__(self):
        super().__init__()
        cfg = SPIDER_CONFIGS[self.name]
        self.api_url = cfg["api_url"]
        self.max_items = cfg["max_items"]
        self.limiter = RateLimiter(rate=1.0, burst=3)

    async def crawl(self) -> list[AudioRecord]:
        self.logger.info("开始爬取 LibriVox...")
        records = []
        async with aiohttp.ClientSession() as session:
            books = await self._fetch_books(session)
            for book in books:
                await self.limiter.acquire()
                tracks = await self._fetch_book_tracks(session, book)
                records.extend(tracks)
                await random_delay(0.5, 1.0)
        self.logger.info(f"LibriVox 共发现 {len(records)} 个音频文件")
        return records

    async def _fetch_books(self, session: aiohttp.ClientSession) -> list[dict]:
        params = {
            "format": "json",
            "limit": str(self.max_items),
            "offset": "0",
        }
        try:
            async with session.get(self.api_url, params=params,
                                   headers=build_headers("https://librivox.org"),
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
                data = await resp.json(content_type=None)
                books = data.get("books", [])
                self.logger.info(f"LibriVox: 找到 {len(books)} 本有声书")
                return books
        except Exception as e:
            self.logger.error(f"获取 LibriVox 书籍列表失败: {e}")
            return []

    async def _fetch_book_tracks(self, session: aiohttp.ClientSession,
                                  book: dict) -> list[AudioRecord]:
        records = []
        rss_url = book.get("url_rss", "")
        if not rss_url:
            return records

        try:
            async with session.get(rss_url,
                                   headers=build_headers("https://librivox.org"),
                                   timeout=aiohttp.ClientTimeout(total=20)) as resp:
                from bs4 import BeautifulSoup
                text = await resp.text()
                soup = BeautifulSoup(text, "lxml-xml")
                for item in soup.find_all("item"):
                    enclosure = item.find("enclosure")
                    if enclosure and enclosure.get("url"):
                        audio_url = enclosure["url"]
                        title_tag = item.find("title")
                        title = title_tag.text.strip() if title_tag else ""
                        record = self._make_record(
                            url=audio_url,
                            title=f"{book.get('title', '')} - {title}",
                            file_format="mp3",
                        )
                        size = enclosure.get("length", "0")
                        record.file_size = int(size) if size.isdigit() else 0
                        records.append(record)
        except Exception as e:
            self.logger.error(f"解析 LibriVox RSS 失败 {rss_url}: {e}")
        return records
