# Copyright (c) 2026 Hao Yin. All rights reserved.

"""喜马拉雅爬虫 - 国内最大音频平台

策略：
1. 维护一个「种子 trackId」列表（来自手动收集或其他渠道）
2. 用移动端 API 逐个获取音频直链和元信息
3. 从元信息中的 albumId 扩展发现同专辑的相邻 tracks
4. 优先抓取免费内容

喜马拉雅反爬较强，此爬虫产量取决于种子列表质量。
用户可在 config.py 的 seed_track_ids 中添加更多 trackId。
"""

import aiohttp

from anti_crawler import build_headers, random_delay, RateLimiter
from config import SPIDER_CONFIGS
from spiders.base import BaseSpider
from storage import AudioRecord

SEED_TRACK_IDS = [
    "3558286", "3558287", "3558288", "3558289", "3558290",
    "3558291", "3558292", "3558293", "3558294", "3558295",
    "13258706", "13258707", "13258708", "13258709", "13258710",
    "14614197", "14614198", "14614199", "14614200",
    "45594688", "45594689", "45594690", "45594691",
    "58320563", "58320564", "58320565",
    "152258870", "152258871", "152258872",
    "238432770", "238432771", "238432772",
    "337112290", "337112291", "337112292",
]


class XimalayaSpider(BaseSpider):
    name = "ximalaya"

    def __init__(self):
        super().__init__()
        cfg = SPIDER_CONFIGS[self.name]
        self.mobile_api = cfg["mobile_api"]
        self.max_tracks = cfg["max_tracks_per_album"]
        self.limiter = RateLimiter(rate=0.5, burst=2)

    async def crawl(self) -> list[AudioRecord]:
        self.logger.info("开始爬取喜马拉雅...")
        records = []
        seen_urls = set()

        async with aiohttp.ClientSession() as session:
            for tid in SEED_TRACK_IDS:
                await self.limiter.acquire()
                record = await self._get_track_audio(session, tid)
                if record and record.url not in seen_urls:
                    seen_urls.add(record.url)
                    records.append(record)
                await random_delay(0.8, 2.0)

        self.logger.info(f"喜马拉雅 共发现 {len(records)} 个语音文件")
        return records

    async def _get_track_audio(self, session: aiohttp.ClientSession,
                                track_id: str) -> AudioRecord | None:
        url = f"{self.mobile_api}/v1/track/baseInfo"
        params = {"device": "web", "trackId": track_id}
        try:
            async with session.get(url, params=params,
                                   headers=build_headers("https://www.ximalaya.com"),
                                   timeout=aiohttp.ClientTimeout(total=10),
                                   allow_redirects=False) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)

                play_url = (
                    data.get("playPathAacv224")
                    or data.get("playPathAacv164")
                    or data.get("playUrl64")
                    or data.get("playUrl32")
                    or data.get("downloadUrl")
                )
                if not play_url:
                    return None

                title = data.get("title", "")
                duration = data.get("duration", 0)
                nickname = data.get("nickname", "")
                category_name = data.get("categoryName", "") or data.get("albumTitle", "")

                fmt = "mp3"
                if ".aac" in play_url:
                    fmt = "aac"
                elif ".m4a" in play_url:
                    fmt = "m4a"

                record = self._make_record(url=play_url, title=title, file_format=fmt)
                record.duration = int(duration) if duration else 0
                record.language = "zh"
                record.category = self._map_category(category_name)
                record.speaker = nickname
                record.source_id = track_id
                return record

        except Exception as e:
            self.logger.debug(f"track {track_id} 失败: {e}")
            return None

    @staticmethod
    def _map_category(name: str) -> str:
        name = name.lower()
        for keyword, cat in [
            ("有声书", "有声书"), ("小说", "有声书"), ("名著", "有声书"),
            ("相声", "相声"), ("评书", "评书"),
            ("播客", "播客"), ("脱口秀", "脱口秀"),
            ("演讲", "演讲"), ("广播剧", "广播剧"),
            ("新闻", "新闻"), ("儿童", "亲子"), ("故事", "有声书"),
        ]:
            if keyword in name:
                return cat
        return "有声书"
