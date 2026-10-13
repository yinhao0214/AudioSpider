"""下载阶段的平台识别与增量过滤。"""

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import unquote

from storage import Storage


logger = logging.getLogger(__name__)

PLATFORM_DISPLAY_NAMES = {
    "ximalaya": "喜马拉雅",
    "lizhi": "荔枝FM",
    "xiaoyuzhou": "小宇宙",
    "qingting": "蜻蜓FM",
    "bilibili": "Bilibili",
}

SUPPORTED_PLATFORMS = tuple(PLATFORM_DISPLAY_NAMES)

_SOURCE_ALIASES = {
    "ximalaya": {"ximalaya"},
    "lizhi": {"lizhi", "lizhi_fm", "lizhifm"},
    "xiaoyuzhou": {"xiaoyuzhou", "xiaoyuzhoufm"},
    "qingting": {"qingting", "qingting_fm", "qingtingfm"},
    "bilibili": {"bilibili", "b站"},
}

_PLATFORM_DOMAINS = {
    "ximalaya": ("ximalaya.com", "xmcdn.com"),
    "lizhi": ("lizhi.fm",),
    "xiaoyuzhou": ("xiaoyuzhoufm.com", "xyzcdn.net"),
    "qingting": ("qtfm.cn", "qingting.fm"),
    "bilibili": ("bilibili.com", "bilivideo.com", "bilivideo.cn"),
}

_DOMAIN_PATTERNS = {
    platform: tuple(
        re.compile(
            rf"(?<![a-z0-9.-])(?:[a-z0-9-]+\.)*{re.escape(domain)}(?=[:/?&#]|$)",
            re.IGNORECASE,
        )
        for domain in domains
    )
    for platform, domains in _PLATFORM_DOMAINS.items()
}


@dataclass
class FilteredPendingBatch:
    items: list[dict]
    cursor: int
    scanned: int
    excluded_counts: dict[str, int]
    reached_end: bool


def detect_platform(source: str, url: str) -> str | None:
    """识别需要支持排除的音频平台；无法确定时返回 None。"""
    normalized_source = (source or "").strip().lower()
    for platform, aliases in _SOURCE_ALIASES.items():
        if normalized_source in aliases:
            return platform

    decoded_url = (url or "").lower()
    for _ in range(3):
        next_value = unquote(decoded_url)
        if next_value == decoded_url:
            break
        decoded_url = next_value

    for platform, patterns in _DOMAIN_PATTERNS.items():
        if any(pattern.search(decoded_url) for pattern in patterns):
            return platform
    return None


def build_filter_checkpoint_key(
    *,
    excluded_platforms: set[str],
    source: str | None,
    category: str | None,
    language: str | None,
    published_since: str | None,
    published_before: str | None,
) -> str:
    """为一组下载过滤条件生成稳定、互不干扰的游标键。"""
    profile = {
        "version": 1,
        "excluded_platforms": sorted(excluded_platforms),
        "source": source or "",
        "category": category or "",
        "language": language or "",
        "published_since": published_since or "",
        "published_before": published_before or "",
    }
    payload = json.dumps(profile, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"platform_filter_cursor_v1_{digest}"


def scan_filtered_pending(
    storage: Storage,
    *,
    after_id: int,
    limit: int,
    excluded_platforms: set[str],
    source: str | None = None,
    category: str | None = None,
    language: str | None = None,
    published_since: str | None = None,
    published_before: str | None = None,
) -> FilteredPendingBatch:
    """从 ID 游标后扫描 pending 记录，返回至多 limit 条未被排除的记录。"""
    selected: list[dict] = []
    excluded_counts: Counter[str] = Counter()
    cursor = max(after_id, 0)
    scan_upper_bound = max(cursor, storage.get_max_audio_id())
    scanned = 0
    reached_end = False
    scan_batch_size = max(1000, min(50000, limit * 10))
    next_progress_log = 100000

    while len(selected) < limit:
        rows = storage.get_pending_after_id(
            after_id=cursor,
            through_id=scan_upper_bound,
            limit=scan_batch_size,
            source=source,
            category=category,
            language=language,
            published_since=published_since,
            published_before=published_before,
        )
        if not rows:
            cursor = scan_upper_bound
            reached_end = True
            break

        for item in rows:
            cursor = item["id"]
            scanned += 1
            platform = detect_platform(item.get("source", ""), item.get("url", ""))
            if platform in excluded_platforms:
                excluded_counts[platform] += 1
            else:
                selected.append(item)
                if len(selected) >= limit:
                    break

        if scanned >= next_progress_log and len(selected) < limit:
            logger.info(
                "过滤扫描进度: 已扫描 %d 条，选中 %d 条，排除 %d 条",
                scanned,
                len(selected),
                sum(excluded_counts.values()),
            )
            next_progress_log += 100000

        if len(rows) < scan_batch_size and len(selected) < limit:
            cursor = scan_upper_bound
            reached_end = True
            break

    return FilteredPendingBatch(
        items=selected,
        cursor=cursor,
        scanned=scanned,
        excluded_counts=dict(excluded_counts),
        reached_end=reached_end,
    )
