# Copyright (c) 2026 Hao Yin. All rights reserved.

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
LOG_DIR = os.path.join(BASE_DIR, "logs")
DB_PATH = os.path.join(BASE_DIR, "audiospider.db")
TMP_DIR = os.path.join(BASE_DIR, "tmp")

# SQLite 临时文件放在数据盘，避免系统根分区空间不足
os.makedirs(TMP_DIR, exist_ok=True)
os.environ.setdefault("TMPDIR", TMP_DIR)

MAX_CONCURRENT_DOWNLOADS = 20
MAX_CONCURRENT_SPIDERS = 3
DOWNLOAD_TIMEOUT = 600
REQUEST_TIMEOUT = 30

MIN_DELAY = 1.0
MAX_DELAY = 3.0

MAX_RETRIES = 3
RETRY_BACKOFF = 2.0

PROXY_LIST = []

# Podcast Index API — 免费注册: https://api.podcastindex.org/
PODCAST_INDEX_KEY = os.environ.get("PODCAST_INDEX_KEY", "")
PODCAST_INDEX_SECRET = os.environ.get("PODCAST_INDEX_SECRET", "")

CHUNK_SIZE = 8192

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a", ".wma", ".opus"}

SPEECH_CATEGORIES = [
    "有声书", "播客", "相声", "评书", "演讲",
    "脱口秀", "广播剧", "新闻", "访谈", "朗读",
]

SPIDER_CONFIGS = {
    "xiaoyuzhou": {
        "enabled": True,
        "base_url": "https://www.xiaoyuzhoufm.com",
        "cdn_domain": "media.xyzcdn.net",
        "discover_urls": [
            "/podcast/61791d921989541784257779",   # 大内密谈
            "/podcast/613753ef23c82a0a1a37a8b9",   # 日谈公园
            "/podcast/5e280fb4418a84a0461fa548",   # 忽左忽右
            "/podcast/5e4ee557418a84a046262869",   # 故事FM
            "/podcast/62d6b1b3f1e5e7a5e7a40f0c",   # 无人知晓
            "/podcast/5ec6726a418a84a046d1a83c",   # 不合时宜
        ],
        "max_episodes_per_podcast": 30,
    },
    "ximalaya": {
        "enabled": True,
        "base_url": "https://www.ximalaya.com",
        "mobile_api": "https://mobile.ximalaya.com",
        "search_keywords": ["相声", "评书", "有声书", "播客", "演讲", "脱口秀", "广播剧", "朗读"],
        "max_tracks_per_album": 50,
    },
    "librivox": {
        "enabled": True,
        "base_url": "https://librivox.org",
        "api_url": "https://librivox.org/api/feed/audiobooks",
        "max_items": 50,
        "languages": ["Chinese", "English"],
    },
    "podcast_rss": {
        "enabled": True,
        "feeds": [
            # 中文播客 RSS（已验证可用）
            "https://crazy.capital/feed",                       # 疯投圈 (140+集)
            # 英文播客/有声书 RSS（已验证可用）
            "https://feeds.npr.org/500005/podcast.xml",         # NPR News Now
            "https://rss.art19.com/apology-line",               # Apology Line
            "https://librivox.org/rss/5765",                    # LibriVox 有声书
        ],
        "max_episodes_per_feed": 50,
    },
    "bilibili": {
        "enabled": True,
        "search_keywords": [
            "有声书 合集", "评书 单田芳", "评书 袁阔成",
            "相声 郭德纲", "相声 合集",
            "演讲 TED 中文", "脱口秀 合集",
            "广播剧 全集", "朗读 名著",
        ],
        "max_videos_per_keyword": 10,
        "max_pages_per_video": 20,
    },
}

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
