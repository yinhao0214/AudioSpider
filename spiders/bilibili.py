# Copyright (c) 2026 Hao Yin. All rights reserved.

"""B站爬虫 — 提取视频中的音频流（有声书、评书、相声、演讲等）

策略：
1. 搜索 API 按关键词找语音类长视频
2. 获取视频分P列表
3. 通过 playurl API 提取 DASH 音频流地址（M4A 格式）

注意：B站音频流 URL 有时效性，需要带 Referer 下载。
"""

import re
import aiohttp

from anti_crawler import random_delay, RateLimiter
from config import SPIDER_CONFIGS
from spiders.base import BaseSpider
from storage import AudioRecord

SEARCH_KEYWORDS = [
    "有声书 合集", "评书 单田芳", "评书 袁阔成",
    "相声 郭德纲", "相声 合集",
    "演讲 TED 中文", "脱口秀 合集",
    "广播剧 全集", "朗读 名著",
]

BILIBILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
}

SEARCH_URL = "https://api.bilibili.com/x/web-interface/search/all/v2"
PAGELIST_URL = "https://api.bilibili.com/x/player/pagelist"
PLAYURL_URL = "https://api.bilibili.com/x/player/playurl"


class BilibiliSpider(BaseSpider):
    name = "bilibili"

    def __init__(self):
        super().__init__()
        cfg = SPIDER_CONFIGS.get(self.name, {})
        self.keywords = cfg.get("search_keywords", SEARCH_KEYWORDS)
        self.max_videos_per_keyword = cfg.get("max_videos_per_keyword", 10)
        self.max_pages_per_video = cfg.get("max_pages_per_video", 20)
        self.limiter = RateLimiter(rate=0.5, burst=3)

    async def crawl(self) -> list[AudioRecord]:
        self.logger.info(f"开始爬取B站, 关键词: {len(self.keywords)} 个")
        records = []
        seen_urls = set()

        async with aiohttp.ClientSession(headers=BILIBILI_HEADERS) as session:
            # 先访问首页获取 cookie
            await session.get("https://www.bilibili.com", timeout=aiohttp.ClientTimeout(total=10))

            for keyword in self.keywords:
                await self.limiter.acquire()
                bvids = await self._search_videos(session, keyword)
                self.logger.info(f"搜索 \"{keyword}\": {len(bvids)} 个视频")

                for bvid, title, duration_str in bvids[:self.max_videos_per_keyword]:
                    await self.limiter.acquire()
                    page_records = await self._extract_audio(session, bvid, title, keyword)
                    for r in page_records:
                        if r.url not in seen_urls:
                            seen_urls.add(r.url)
                            records.append(r)
                    await random_delay(1.0, 2.0)

                await random_delay(2.0, 4.0)

        self.logger.info(f"B站 共发现 {len(records)} 个音频")
        return records

    async def _search_videos(self, session: aiohttp.ClientSession,
                              keyword: str) -> list[tuple[str, str, str]]:
        """搜索视频，返回 [(bvid, title, duration), ...]"""
        results = []
        try:
            params = {"keyword": keyword, "page": 1, "duration": 4}
            async with session.get(SEARCH_URL, params=params,
                                   timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
                for rt in data.get("data", {}).get("result", []):
                    if rt.get("result_type") != "video":
                        continue
                    for v in rt.get("data", []):
                        bvid = v.get("bvid", "")
                        if not bvid:
                            continue
                        title = re.sub(r"<[^>]+>", "", v.get("title", ""))
                        duration = v.get("duration", "")
                        results.append((bvid, title, str(duration)))
        except Exception as e:
            self.logger.warning(f"B站搜索失败 \"{keyword}\": {e}")
        return results

    async def _extract_audio(self, session: aiohttp.ClientSession,
                              bvid: str, video_title: str,
                              keyword: str) -> list[AudioRecord]:
        """从视频的每个分P提取音频流"""
        records = []
        try:
            async with session.get(PAGELIST_URL, params={"bvid": bvid},
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
                pages = data.get("data", [])

            for page in pages[:self.max_pages_per_video]:
                cid = page.get("cid")
                part_title = page.get("part", "")
                page_num = page.get("page", 1)
                duration = page.get("duration", 0)

                if not cid:
                    continue

                await self.limiter.acquire()
                audio_url = await self._get_audio_url(session, bvid, cid)
                if not audio_url:
                    continue

                title = f"{video_title} P{page_num}" if not part_title else part_title
                category = self._guess_category(keyword)

                record = self._make_record(url=audio_url, title=title, file_format="m4a")
                record.duration = duration
                record.category = category
                record.language = "zh"
                record.speaker = video_title[:30]
                record.source_id = f"{bvid}_p{page_num}"
                records.append(record)

                await random_delay(0.5, 1.0)

        except Exception as e:
            self.logger.warning(f"B站视频解析失败 {bvid}: {e}")
        return records

    async def _get_audio_url(self, session: aiohttp.ClientSession,
                              bvid: str, cid: int) -> str:
        """获取单个分P的最高品质音频流 URL"""
        try:
            params = {"bvid": bvid, "cid": cid, "fnval": 16}
            async with session.get(PLAYURL_URL, params=params,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json(content_type=None)
                audios = data.get("data", {}).get("dash", {}).get("audio", [])
                if not audios:
                    return ""
                best = max(audios, key=lambda a: a.get("bandwidth", 0))
                return best.get("baseUrl", "") or best.get("base_url", "")
        except Exception as e:
            self.logger.debug(f"获取音频流失败 {bvid} cid={cid}: {e}")
            return ""

    @staticmethod
    def _guess_category(keyword: str) -> str:
        if "有声书" in keyword:
            return "有声书"
        if "评书" in keyword:
            return "评书"
        if "相声" in keyword:
            return "相声"
        if "演讲" in keyword or "TED" in keyword:
            return "演讲"
        if "脱口秀" in keyword:
            return "脱口秀"
        if "广播剧" in keyword:
            return "广播剧"
        if "朗读" in keyword:
            return "朗读"
        return "播客"
