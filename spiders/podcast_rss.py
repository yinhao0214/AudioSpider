# Copyright (c) 2026 Hao Yin. All rights reserved.

"""通用播客 RSS 爬虫 - 任意播客 RSS 源

RSS 是最干净的播客分发协议，<enclosure> 直接包含音频直链。
支持增量爬取：记录每个 feed 最后一次看到的 episode guid。
"""

import aiohttp
from bs4 import BeautifulSoup

from anti_crawler import build_headers, random_delay, RateLimiter
from config import SPIDER_CONFIGS
from spiders.base import BaseSpider
from storage import AudioRecord


class PodcastRSSSpider(BaseSpider):
    name = "podcast_rss"

    def __init__(self):
        super().__init__()
        cfg = SPIDER_CONFIGS[self.name]
        self.feeds = cfg["feeds"]
        self.max_eps = cfg["max_episodes_per_feed"]
        self.limiter = RateLimiter(rate=0.5, burst=3)

    async def crawl(self) -> list[AudioRecord]:
        self.logger.info(f"开始爬取 {len(self.feeds)} 个播客 RSS...")
        records = []
        async with aiohttp.ClientSession() as session:
            for feed_url in self.feeds:
                await self.limiter.acquire()
                feed_records = await self._parse_feed(session, feed_url)
                records.extend(feed_records)
                await random_delay(1.0, 2.0)
        self.logger.info(f"Podcast RSS 共发现 {len(records)} 个语音文件")
        return records

    async def _parse_feed(self, session: aiohttp.ClientSession,
                           feed_url: str) -> list[AudioRecord]:
        records = []
        try:
            async with session.get(feed_url,
                                   headers=build_headers(),
                                   timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    self.logger.warning(f"RSS {feed_url} 返回 {resp.status}")
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
                    if lang_raw.startswith("zh"):
                        language = "zh"
                    elif lang_raw.startswith("en"):
                        language = "en"
                    else:
                        language = lang_raw[:2]

                for item in list(soup.find_all("item"))[:self.max_eps]:
                    enclosure = item.find("enclosure")
                    if not enclosure:
                        continue
                    audio_url = enclosure.get("url", "")
                    mime = enclosure.get("type", "")
                    if not audio_url or "audio" not in mime:
                        if audio_url and audio_url.rsplit(".", 1)[-1].lower() in ("mp3", "m4a", "ogg", "aac"):
                            pass
                        else:
                            continue

                    title_tag = item.find("title")
                    title = title_tag.get_text(strip=True) if title_tag else ""

                    guid_tag = item.find("guid")
                    guid = guid_tag.get_text(strip=True) if guid_tag else audio_url

                    duration_tag = item.find("itunes:duration") or item.find("duration")
                    duration = 0
                    if duration_tag:
                        dur_text = duration_tag.get_text(strip=True)
                        duration = self._parse_duration(dur_text)

                    fmt = "mp3"
                    ext = audio_url.rsplit(".", 1)[-1].split("?")[0].lower()
                    if ext in ("m4a", "ogg", "aac", "wav", "opus"):
                        fmt = ext

                    size = int(enclosure.get("length", 0) or 0)

                    record = self._make_record(url=audio_url, title=title, file_format=fmt)
                    record.file_size = size
                    record.duration = duration
                    record.category = "播客"
                    record.language = language
                    record.speaker = podcast_title
                    record.source_id = guid
                    records.append(record)

        except Exception as e:
            self.logger.error(f"RSS 解析失败 {feed_url}: {e}")
        return records

    @staticmethod
    def _parse_duration(text: str) -> int:
        text = text.strip()
        if ":" in text:
            parts = text.split(":")
            parts = [int(p) for p in parts if p.isdigit()]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            elif len(parts) == 2:
                return parts[0] * 60 + parts[1]
        if text.isdigit():
            return int(text)
        return 0
