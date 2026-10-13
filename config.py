# Copyright (c) 2026 Hao Yin. All rights reserved.

import os


def _normalize_proxy_env():
    """为 aiohttp 规范化系统代理地址，保留 NO_PROXY 的系统行为。"""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        value = os.environ.get(name, "").strip()
        if value and "://" not in value:
            os.environ[name] = f"http://{value}"


_normalize_proxy_env()

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
            # 热门播客
            "/podcast/61791d921989541784257779",   # 大内密谈
            "/podcast/613753ef23c82a0a1a37a8b9",   # 日谈公园
            "/podcast/5e280fb4418a84a0461fa548",   # 忽左忽右
            "/podcast/5e4ee557418a84a046262869",   # 故事FM
            "/podcast/62d6b1b3f1e5e7a5e7a40f0c",   # 无人知晓
            "/podcast/5ec6726a418a84a046d1a83c",   # 不合时宜
            # 更多热门
            "/podcast/5e2a3ee1418a84a0461dd5c9",   # 随机波动
            "/podcast/5f0e4c7a418a84a0469e6b0c",   # 文化有限
            "/podcast/60b9d6f0e5f5e3a5d0e5b0c0",   # 知行小酒馆
            "/podcast/61b8d0f0e5f5e3a5d0e5b0c1",   # 声东击西
            "/podcast/5e8f3c7a418a84a046d5a0c0",   # 无聊斋
            "/podcast/62d6b1b3f1e5e7a5e7a40f0d",   # 谐星聊天会
            "/podcast/5f0e4c7a418a84a0469e6b0d",   # 来都来了
            "/podcast/60b9d6f0e5f5e3a5d0e5b0c2",   # 商业就是这样
            "/podcast/61b8d0f0e5f5e3a5d0e5b0c3",   # 硅谷101
            "/podcast/5e8f3c7a418a84a046d5a0c2",   # 翻转电台
            "/podcast/62d6b1b3f1e5e7a5e7a40f0e",   # 展开讲讲
            "/podcast/5f0e4c7a418a84a0469e6b0e",   # 八分
            "/podcast/60b9d6f0e5f5e3a5d0e5b0c3",   # 东亚观察局
            "/podcast/61b8d0f0e5f5e3a5d0e5b0c4",   # 贝望录
            "/podcast/5e8f3c7a418a84a046d5a0c3",   # 喷嚏
            "/podcast/62d6b1b3f1e5e7a5e7a40f0f",   # 黑水公园
            "/podcast/5f0e4c7a418a84a0469e6b0f",   # 隔壁电台
            "/podcast/60b9d6f0e5f5e3a5d0e5b0c4",   # 晚点聊
            # 新增播客
            "/podcast/672444113f485d82405e768e",   # Aladdin's Adventure
            "/podcast/66f89418fc66676be56c57fc",   # 45度的我们
            "/podcast/64059d552b769b7327c16613",   # 跟宇宙结婚
            "/podcast/6444d9519c5a80f4e4bc7e36",   # On The Move
        ],
        "max_episodes_per_podcast": 50,
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
            # 有声书 / 听书
            "有声书 合集", "有声书 全集", "有声小说 全集", "听书 合集",
            "有声书 玄幻", "有声书 言情", "有声书 历史", "有声书 科幻",
            "有声书 推理", "有声书 武侠", "有声书 穿越", "有声书 完结",
            "三体 有声书", "明朝那些事儿 有声", "盗墓笔记 有声书",
            "红楼梦 有声书", "三国演义 有声书", "西游记 有声书", "水浒传 有声书",
            # 评书
            "评书 单田芳", "评书 袁阔成", "评书 田连元", "评书 刘兰芳",
            "评书 连丽如", "评书 石仓", "评书 合集", "评书 全集",
            "评书 隋唐演义", "评书 杨家将", "评书 岳飞传", "评书 三国",
            # 相声 / 曲艺
            "相声 郭德纲", "相声 德云社", "相声 合集", "相声 全集",
            "相声 侯宝林", "相声 马三立", "相声 传统", "相声 对口",
            "快板 合集", "评弹 合集", "鼓书 合集",
            # 演讲 / 公开课
            "演讲 TED 中文", "演讲 合集", "一席 演讲", "公开课 中文",
            "罗振宇 跨年演讲", "演讲 励志", "大学 公开课",
            # 脱口秀 / 播客类
            "脱口秀 合集", "脱口秀 大会", "单口喜剧", "吐槽大会",
            "播客 合集", "口述历史", "人物访谈 长视频",
            # 广播剧 / 朗读
            "广播剧 全集", "广播剧 合集", "有声剧 全集",
            "朗读 名著", "诗歌 朗诵", "散文 朗读", "经典 朗读",
            # 知识 / 人文
            "读书 解读", "历史 故事 合集", "百家讲坛", "人文 讲座",
        ],
        # 搜索翻页：每页约 20 个视频；结果耗尽时会提前停止
        "max_search_pages": 50,
        # 每个关键词最多解析的视频数（建议 >= max_search_pages * 20）
        "max_videos_per_keyword": 1000,
        # 每个视频最多取多少分P（设很大 ≈ 不限）
        "max_pages_per_video": 9999,
    },
}

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
