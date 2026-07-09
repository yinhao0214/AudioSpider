# AudioSpider

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

面向语音大模型训练的全网语音音频搜集和批量下载系统。自动从播客、有声书、B站等来源发现语音 URL，支持增量爬取、内容去重、并发下载。

## 架构

```
AudioSpider/
├── discover.py         # 自动发现新源（Apple Podcasts + Podcast Index，两源全覆盖）
├── collect.py          # URL 搜集器（从已有固定源抓取最新音频链接）
├── main.py             # 音频下载器（从数据库取 URL 并下载）
├── config.py           # 全局配置（目录、超时、爬虫参数、Podcast Index API Key）
├── storage.py          # SQLite 存储（URL 去重、状态追踪、指纹去重）
├── downloader.py       # 异步下载引擎（并发、断点续传、元信息生成）
├── anti_crawler.py     # 反爬工具（UA 轮换、延时、代理、限速）
├── convert_audio.py    # 批量音频格式转换（→ Opus 24kHz mono 32kbps）
├── db_viewer.py        # 数据库交互式查看器
├── spiders/            # 各来源爬虫
│   ├── base.py         # 爬虫基类
│   ├── xiaoyuzhou.py   # 小宇宙播客（CDN 直链）
│   ├── ximalaya.py     # 喜马拉雅（移动端 API）
│   ├── podcast_rss.py  # 通用播客 RSS
│   ├── librivox.py     # LibriVox 有声书
│   └── bilibili.py     # B站（DASH 音频流）
└── requirements.txt    # Python 依赖
```

运行后自动生成：

```
├── downloads/          # 下载的音频文件（按 来源/分类 分目录）
├── logs/               # 运行日志
└── audiospider.db      # SQLite 数据库
```

## 工作流

**三个独立程序，按需运行：**

1. **`discover.py`** — 自动发现新源。通过两个数据源（Apple Podcasts、Podcast Index）全网发现播客，拿到 RSS 地址并解析全部剧集的音频 URL 入库。仅采集中英文播客。相当于"开拓新领地"。
2. **`collect.py`** — 从已有固定源抓取。只跑 `config.py` 里配置好的来源（小宇宙、喜马拉雅等），抓取最新音频。相当于"巡逻老地盘"。
3. **`main.py`** — 消费下载。从数据库取待下载 URL，批量下载到本地。

```
discover.py → [搜索全网播客] ─┐
                              ├→ audiospider.db → [取URL] → main.py → [下载+转码] → downloads/ (*.opus)
collect.py  → [抓已有来源]  ──┘
```

## 快速开始

### 安装系统依赖

下载器会自动将音频转为 Opus 格式，需要系统安装 ffmpeg：

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y ffmpeg

# CentOS / RHEL
sudo yum install -y epel-release && sudo yum install -y ffmpeg

# Arch Linux
sudo pacman -S ffmpeg

# 验证安装
ffmpeg -version
```

### 安装项目

```bash
# 1. 克隆项目
git clone https://github.com/your-username/AudioSpider.git
cd AudioSpider

# 2. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 3. 安装 Python 依赖
pip install -r requirements.txt

# 4. 自动发现播客源（首次运行，大量扩充 URL 池）
python discover.py                         # 两个源全开（需配置 Podcast Index API Key，否则自动跳过）
python discover.py --source apple          # 或只用 Apple 源（无需注册）

# 5. 下载音频
python main.py --limit 100
```

## 用法

### 自动发现新源（discover.py）

通过两个数据源全网发现播客 RSS，自动解析音频 URL 入库。仅采集中英文播客。

**两个数据源：**

| 源 | 说明 | 覆盖量 |
|---|---|---|
| **Apple Podcasts**（`--source apple`） | 关键词搜索（80+ 中英文关键词）+ 分类排行榜遍历（102 个分类 × 2 地区，Top Charts API），中国区+美国区 | 去重后预计数万个播客 |
| **Podcast Index**（`--source podcastindex`） | 开源播客目录，400 万+ 播客。关键词搜索 + 热门 + 分页遍历最近更新 | 理论上无上限 |

```bash
# 两个源全部启用（默认，最大覆盖）
python discover.py

# 只用 Apple（无需注册，立刻能跑）
python discover.py --source apple

# 只用 Apple 关键词搜索
python discover.py --source apple_keyword

# 只用 Apple 分类排行榜遍历（Top Charts API，不依赖搜索词，覆盖面更广）
python discover.py --source apple_genre

# 只用 Podcast Index（需配置 API Key）
python discover.py --source podcastindex

# 组合使用（如：Apple 分类遍历 + Podcast Index）
python discover.py --source apple_genre podcastindex

# 自定义关键词
python discover.py --keywords 脱口秀 TED 历史故事

# Apple 关键词搜索每词取前 200 个播客（默认 200，上限 200）
python discover.py --top 200

# Podcast Index 翻页更深（100 页 × 1000 条 = 最多 10 万条 feeds）
python discover.py --pi-max-pages 100

# 每天自动搜索一次
python discover.py --loop --interval 86400

# 查看已发现的所有播客源
python discover.py --list-feeds

# 查看统计（总数、已解析/未解析、按来源分布）
python discover.py --stats
```

**使用 Podcast Index 前的准备：**

Podcast Index 是免费开源的播客目录。使用前需注册获取 API Key：

1. 访问 https://api.podcastindex.org/ 免费注册
2. 获取 API Key 和 Secret
3. 设置环境变量：

```bash
export PODCAST_INDEX_KEY="你的key"
export PODCAST_INDEX_SECRET="你的secret"
```

或在 `config.py` 中直接配置。未配置时程序会自动跳过该源，不影响 Apple 源的正常运行。

### 从固定源搜集 URL（collect.py）

`collect.py` 从 `config.py` 中配置好的**固定来源**抓取音频链接，包含以下 5 个爬虫：

这些源是**有限的**，跑几次就会全部入库。它的主要价值是**定期抓增量更新**（比如播客出了新一期）。

#### 1. 小宇宙播客（xiaoyuzhou）

解析播客页面 HTML，提取 M4A CDN 直链。配置在 `config.py` → `discover_urls`，当前包含 6 个播客，每个最多抓 30 集：

- 大内密谈、日谈公园、忽左忽右、故事FM、无人知晓、不合时宜

> 想加更多小宇宙播客？把播客页面 URL 中的 `/podcast/xxx` 路径添加到 `config.py` 的 `discover_urls` 列表即可。

#### 2. 通用播客 RSS（podcast_rss）

解析 RSS XML 中的 `<enclosure>` 标签获取音频直链。配置在 `config.py` → `feeds`，当前包含 4 个 RSS 地址，每个最多取 50 集。

> 想加更多 RSS？把播客的 RSS 地址添加到 `config.py` 的 `feeds` 列表即可。

#### 3. 喜马拉雅（ximalaya）

通过移动端 API 逐个获取音频直链。种子 track ID 配置在 `spiders/ximalaya.py` 的 `SEED_TRACK_IDS` 列表中。

> 喜马拉雅反爬较强，产量取决于种子列表质量。可在 `spiders/ximalaya.py` 中添加更多 track ID。

#### 4. LibriVox 有声书（librivox）

调用 LibriVox 公开 API 获取公版有声书章节链接。音频托管在 Archive.org 上。

#### 5. B站（bilibili）

通过 B站搜索 API 按关键词搜索语音类长视频，提取每个视频的 DASH 音频流（M4A 格式）。

> 想加更多搜索词？修改 `config.py` 中 `bilibili` → `search_keywords` 列表即可。

```bash
# 运行全部爬虫
python collect.py

# 只运行指定爬虫
python collect.py --spiders xiaoyuzhou podcast_rss

# 每小时自动搜集一次（适合抓播客更新）
python collect.py --loop --interval 3600
```

### discover.py vs collect.py

| | `discover.py` | `collect.py` |
|---|---|---|
| **做什么** | 搜索全网，发现新的播客源 | 从已有固定源抓取音频链接 |
| **数据来源** | Apple Podcasts（关键词搜索+分类排行榜） + Podcast Index API | `config.py` 中配置的 5 个固定爬虫 |
| **URL 数量** | 无上限，两源组合覆盖数十万播客 | 单次约 1500 条 |
| **语言** | 仅中英文 | 取决于配置 |
| **适合场景** | 大量扩充 URL 池 | 定期检查已有播客的更新 |
| **类比** | "去新书店找书" | "去常去的书店看有没有上新" |

### 下载音频（main.py）

```bash
# 下载 50 条（默认）
python main.py

# 下载 200 条
python main.py --limit 200

# 指定来源
python main.py --source xiaoyuzhou
python main.py --source podcast_rss --limit 100

# 指定分类
python main.py --category 播客 --limit 100
python main.py --category 有声书 --limit 50

# 指定语种
python main.py --language zh --limit 1000    # 仅下载中文
python main.py --language en --limit 500     # 仅下载英文

# 每个来源各下载 N 条
python main.py --per-source --limit 20

# 每个分类各下载 N 条
python main.py --per-category --limit 10

# 组合过滤（来源 + 分类 + 语种）
python main.py --source bilibili --category 有声书 --limit 50
python main.py --language zh --source podcast_rss --limit 200

# 20 并发下载（默认 20）
python main.py --workers 20

# 持续消费下载（每 60 秒检查一次，每轮下载 --limit 条）
python main.py --loop

# 全量下载数据库中所有待下载 URL
python main.py --limit 999999999

# 中断后重启会自动恢复：downloading 状态自动重置为 pending，不会丢失进度

# 重试之前下载失败的 URL（只下载 failed 状态的，成功改 done，仍失败保持 failed）
python main.py --retry-failed
python main.py --retry-failed --limit 1000

# 查看统计
python main.py stats

# 为已下载文件补生成元信息 JSON
python main.py fix-meta
```

### 数据库查看（db_viewer.py）

```bash
# 交互式查看
python db_viewer.py

# 数据库概览
python db_viewer.py overview

# 按来源/状态筛选
python db_viewer.py source bilibili -n 10
python db_viewer.py status done -n 20

# 按语种筛选
python db_viewer.py language zh -n 10
python db_viewer.py language en -n 20

# 查看指定记录
python db_viewer.py id 42

# 查看时长统计
python db_viewer.py duration
```

示例输出（`python db_viewer.py overview`）：

```
======================================================================
  AudioSpider 数据库概览
======================================================================

  总记录数: 2209434

  按状态统计:
    pending       2175179
    done          30672
    failed        3544
    downloading   39

  按来源统计:
    podcast_rss       2204192
    bilibili          3516
    librivox          1695
    xiaoyuzhou        17
    ximalaya          14

  按分类统计:
    文艺            261867
    教育            250717
    新闻            241006
    脱口秀           234178
    播客            193640
    商业            162159
    访谈            127408
    音乐            110020
    影视            103075
    亲子            100012
    宗教            78471
    科技            75885
    有声书           68204
    历史            62545
    休闲            34994
    体育            24580
    健康            23818
    纪实            21520
    科学            18384
    情感            11708
    评书            929
    时政            831
    相声            767
    朗读            366
    广播剧           350
    演讲            305

  按语种统计:
    en            1227810
    zh            806578
    es            22516
    ja            21858
    ko            12607
    fr            10976
    de            9722
    ...

  时长统计:
    待下载 (pending)    2175177 条  总时长 1381752:16:26
    已完成 (done)       30674 条  总时长 14497:12:00
    本地保留 (去重后)   30660 条  总时长 14490:05:10
    全部记录             2209434 条  总时长 1398673:14:50
```

### 格式转换（convert_audio.py）

下载器会自动将音频转为统一格式（见下方「音频格式」章节）。对于早期下载的、尚未转换的文件，可用批量转换脚本补转：

```bash
# 预览哪些文件需要转换
python convert_audio.py --dry-run

# 正式转换（4 并发，默认）
python convert_audio.py

# 8 并发加速
python convert_audio.py --workers 8

# 指定目录
python convert_audio.py --dir downloads/podcast_rss
```

### 日常使用

```bash
# 首次：大量发现新源
python discover.py

# 日常：从已有源抓增量 + 下载
python collect.py && python main.py --limit 100

# 偶尔：用新关键词扩充源
python discover.py --keywords 英语学习 科技播客

# 后台持续运行（discover + main 同时跑，discover 写入新 URL，main 实时下载）
python discover.py --source apple & # 终端1: 发现新源
python main.py --loop &             # 终端2: 持续下载（默认 20 并发）
```

## 数据来源

| 来源 | 类型 | 下载方式 | 说明 |
|------|------|----------|------|
| 小宇宙 | 播客 | CDN 直链 | 最稳定，M4A 格式 |
| 通用 RSS | 播客 | 直链 | 标准协议，覆盖面广 |
| 喜马拉雅 | 有声书/播客 | 移动端 API | MP3/AAC/M4A 格式 |
| LibriVox | 有声书 | Archive.org | 英文公版有声书 |
| B站 | 有声书/相声/评书/演讲 | DASH 音频流 | M4A 格式，内容量大 |

### 添加新来源

在 `spiders/` 目录新建爬虫类继承 `BaseSpider`，实现 `crawl()` 方法返回 `AudioRecord` 列表，然后在 `collect.py` 的 `ALL_SPIDERS` 中注册即可。

## 音频格式

所有音频统一转为面向语音模型训练的标准格式：

| 参数 | 值 |
|------|-----|
| **编码** | Opus (libopus) |
| **容器** | `.opus` |
| **采样率** | 24kHz |
| **通道** | 单声道 (mono) |
| **码率** | 32kbps |

选型依据：

- **Opus 32kbps** — 同码率下音质优于 MP3/AAC，是 WenetSpeech 等大规模语音数据集的标准选择
- **24kHz** — 现代 TTS 模型（VALL-E、ChatTTS 等）的主流训练采样率，完整覆盖人声频率范围
- **单声道** — 语音合成只需单通道
- **存储效率** — 1 万小时约 140GB，相比 WAV (1.6TB) 节省约 91%

训练时用 `torchaudio.load()` 直接加载，自动解码为 24kHz PCM：

```python
import torchaudio
wav, sr = torchaudio.load("audio.opus")  # sr=24000, wav.shape=[1, N]
```

下载流程中自动完成格式转换（需要系统安装 `ffmpeg`），也可用 `convert_audio.py` 对已有文件批量补转。

## 去重机制

三层去重，避免重复下载：

1. **URL 去重** — 数据库 UNIQUE 约束
2. **source_id 去重** — 用源站唯一 ID 做增量爬取判断
3. **内容指纹去重** — 下载后计算 MD5，跨源去重

## 下载目录结构

```
downloads/
├── xiaoyuzhou/
│   └── 播客/
│       ├── 不开玩笑 Jokes Aside_xxx.opus
│       └── 不开玩笑 Jokes Aside_xxx.json   ← 元信息
├── podcast_rss/
│   └── 播客/
│       └── ...
├── ximalaya/
│   ├── 有声书/
│   └── 亲子/
├── bilibili/
│   ├── 有声书/
│   ├── 相声/
│   └── 评书/
└── librivox/
    └── ...
```

每个音频文件旁生成同名 `.json` 元信息文件，包含 title、source、category、language、duration 等字段。

## 反爬策略

- 随机 User-Agent 轮换
- 请求间随机延时
- 指数退避重试
- 令牌桶限速
- Referer 头伪装
- 代理支持（在 `config.py` 中配置 `PROXY_LIST`）

## 统计

运行 `python main.py stats` 查看：

- URL 总数、各状态数量
- 按来源分类的待下载/已完成/失败数
- 最近下载的文件列表
- 磁盘总用量

## TODO

### 待接入的发现源

当前 `discover.py` 已集成两个数据源（Apple Podcasts、Podcast Index API），计划接入更多渠道：

- [x] **Podcast Index API** — 最大的开放播客目录（400 万+ 播客），已集成到 `discover.py`，支持关键词搜索/热门播客/分页遍历最近更新的 feeds
- [x] **Apple Genre 分类遍历** — 遍历 Apple Podcasts 88 个分类 × 2 个地区，已集成到 `discover.py`
- [ ] **gpodder.net API** — 开源播客目录，提供热门排行和订阅数排序，免费无需认证
- [ ] **Listen Notes API** — 播客搜索引擎（300 万+ 播客），支持按语言/地区过滤，免费额度 300 次/月
- [ ] **荔枝FM** — 国内 UGC 音频平台，大量中文有声内容
- [ ] **蜻蜓FM** — 国内音频平台，评书/有声书资源丰富

## 免责声明

本项目仅供学习和研究使用。使用者应遵守相关网站的服务条款和版权法律。请勿将爬取的数据用于未经授权的商业用途。对因使用本工具产生的任何法律问题，项目作者不承担任何责任。

## Star History

<a href="https://www.star-history.com/?repos=yinhao0214%2FAudioSpider&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/image?repos=yinhao0214/AudioSpider&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/image?repos=yinhao0214/AudioSpider&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/image?repos=yinhao0214/AudioSpider&type=date&legend=top-left" />
 </picture>
</a>

## License

本项目基于 [MIT License](LICENSE) 开源。
