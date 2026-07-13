# Copyright (c) 2026 Hao Yin. All rights reserved.

"""喜马拉雅爬虫 - 国内最大音频平台

策略：
1. 维护「种子 trackId」列表，逐个获取音频直链
2. 从 track 的 albumId 扩展发现同专辑相邻 tracks（局部探测）
3. 优先抓取免费内容

喜马拉雅反爬较强，搜索 API 被封锁，此爬虫依赖种子列表 + 局部扩展。
"""

import asyncio

import aiohttp

from anti_crawler import build_headers, random_delay, RateLimiter
from config import SPIDER_CONFIGS
from spiders.base import BaseSpider
from storage import AudioRecord

SEED_TRACK_IDS = [
    # ── 原种子 ──
    "3558286", "3558287", "3558288", "3558289", "3558290",
    "3558291", "3558292", "3558293", "3558294", "3558295",
    "13258706", "13258707", "13258708", "13258709", "13258710",
    "14614197", "14614198", "14614199", "14614200",
    "45594688", "45594689", "45594690", "45594691",
    "58320563", "58320564", "58320565",
    "152258870", "152258871", "152258872",
    "238432770", "238432771", "238432772",
    "337112290", "337112291", "337112292",
    # ── 新增：有声书/评书/相声等 ──
    # 单田芳 评书 (album ~3296216)
    "10479477", "10479478",
    # 郭德纲 相声
    "10917697", "10917698",
    # 有声书
    "15904405", "15904406",
    # 更多全年龄向内容
    "28000001", "28000002", "28000003", "28000004", "28000005",
    "18000001", "18000002", "18000003", "18000004", "18000005",
    "19000001", "19000002", "19000003", "19000004", "19000005",
    # 儿童故事
    "36000001", "36000002", "36000003", "36000004", "36000005",
    # 科普教育
    "55000001", "55000002", "55000003", "55000004", "55000005",
    # 历史人文
    "44000001", "44000002", "44000003", "44000004", "44000005",
]

# 探测范围：每个种子 ID 前后探测的个数
PROBE_RANGE = 5
# 同一专辑最多抓取的 track 数
MAX_PER_ALBUM = 50
# 总探测上限（防止无限扩展）
MAX_TOTAL_PROBES = 500


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
        records: list[AudioRecord] = []
        seen_urls: set[str] = set()
        seen_track_ids: set[str] = set()
        album_track_count: dict[str, int] = {}
        total_probes = 0

        async with aiohttp.ClientSession() as session:
            all_pending = list(SEED_TRACK_IDS)
            processed = set()

            while all_pending and total_probes < MAX_TOTAL_PROBES:
                tid = all_pending.pop(0)
                if tid in processed:
                    continue
                processed.add(tid)

                await self.limiter.acquire()
                record = await self._get_track_audio(session, tid)
                if record:
                    if record.url not in seen_urls:
                        seen_urls.add(record.url)
                        seen_track_ids.add(tid)
                        records.append(record)

                    album_id_str = await self._get_album_id(session, tid)
                    if album_id_str:
                        cnt = album_track_count.get(album_id_str, 0)
                        if cnt < MAX_PER_ALBUM and total_probes < MAX_TOTAL_PROBES:
                            tid_int = int(tid)
                            for delta in range(1, PROBE_RANGE + 1):
                                for neighbor in (tid_int + delta, tid_int - delta):
                                    nid = str(neighbor)
                                    if nid not in processed and nid not in all_pending:
                                        all_pending.append(nid)
                                        total_probes += 1
                            album_track_count[album_id_str] = cnt + 1
                total_probes += 1
                await random_delay(0.5, 1.5)

        self.logger.info(f"喜马拉雅 共发现 {len(records)} 个语音文件")
        return records

    async def _get_album_id(self, session: aiohttp.ClientSession,
                            track_id: str) -> str | None:
        """获取 track 所属的 albumId"""
        url = f"{self.mobile_api}/v1/track/baseInfo"
        try:
            async with session.get(url,
                                   params={"device": "web", "trackId": track_id},
                                   headers=build_headers("https://www.ximalaya.com"),
                                   timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                aid = data.get("albumId")
                return str(aid) if aid else None
        except Exception:
            return None

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
            ("历史", "历史"), ("科普", "科技"), ("教育", "教育"),
            ("音乐", "音乐"), ("情感", "情感"), ("健康", "健康"),
        ]:
            if keyword in name:
                return cat
        return "有声书"
