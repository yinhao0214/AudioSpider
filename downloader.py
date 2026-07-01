# Copyright (c) 2026 Hao Yin. All rights reserved.

"""批量下载器：连接池复用、并发可调、断点续传、进度条、内容指纹去重、自动格式转换"""

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
from urllib.parse import urlparse, unquote

import aiohttp
import aiofiles

from anti_crawler import build_headers, random_delay, get_aio_proxy
from config import DOWNLOAD_DIR, MAX_CONCURRENT_DOWNLOADS, DOWNLOAD_TIMEOUT, CHUNK_SIZE, MAX_RETRIES, RETRY_BACKOFF
from storage import Storage

logger = logging.getLogger(__name__)

REFERER_MAP = {
    "xyzcdn.net": "https://www.xiaoyuzhoufm.com/",
    "xmcdn.com": "https://www.ximalaya.com/",
    "ximalaya.com": "https://www.ximalaya.com/",
    "cos.tx.xmcdn.com": "https://www.ximalaya.com/",
    "archive.org": "https://archive.org/",
    "bilivideo.cn": "https://www.bilibili.com/",
    "bilivideo.com": "https://www.bilibili.com/",
    "akamaized.net": "https://www.bilibili.com/",
}

BILIBILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
}


def safe_filename(url: str, title: str = "", fmt: str = "", source_id: str = "") -> str:
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]

    if title:
        name = re.sub(r'[^\w\s\-.\u4e00-\u9fff]', '_', title)
        name = re.sub(r'_+', '_', name).strip('_ ')[:80]
        safe_id = re.sub(r'[^\w\-]', '', source_id)[:20] if source_id else url_hash
        name = f"{name}_{safe_id}"
    else:
        path = urlparse(url).path
        name = unquote(path.rsplit("/", 1)[-1]) or f"audio_{url_hash}"
        if fmt and name.endswith(f".{fmt}"):
            name = name[: -len(fmt) - 1]
        name = f"{name}_{url_hash}"

    if fmt and not name.endswith(f".{fmt}"):
        name = f"{name}.{fmt}"
    elif "." not in name.rsplit("/", 1)[-1]:
        name = f"{name}.mp3"
    return name


def _guess_referer(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for domain, ref in REFERER_MAP.items():
        if domain in host:
            return ref
    return urlparse(url).scheme + "://" + urlparse(url).netloc + "/"


async def _resolve_bilibili_url(session: aiohttp.ClientSession, source_id: str) -> str:
    """B站音频流 URL 有时效性，下载时实时获取新的"""
    m = re.match(r"(BV[\w]+)_p(\d+)", source_id)
    if not m:
        return ""
    bvid, page_num = m.group(1), int(m.group(2))

    try:
        async with session.get(
            "https://api.bilibili.com/x/player/pagelist",
            params={"bvid": bvid},
            headers=BILIBILI_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json(content_type=None)
            pages = data.get("data", [])
            if page_num > len(pages):
                return ""
            cid = pages[page_num - 1]["cid"]

        async with session.get(
            "https://api.bilibili.com/x/player/playurl",
            params={"bvid": bvid, "cid": cid, "fnval": 16},
            headers=BILIBILI_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json(content_type=None)
            audios = data.get("data", {}).get("dash", {}).get("audio", [])
            if not audios:
                return ""
            best = max(audios, key=lambda a: a.get("bandwidth", 0))
            return best.get("baseUrl", "") or best.get("base_url", "")
    except Exception as e:
        logger.debug(f"B站URL刷新失败 {source_id}: {e}")
        return ""


TARGET_FORMAT = {
    "ext": ".opus",
    "ffmpeg_args": [
        "-vn", "-ar", "24000", "-ac", "1",
        "-c:a", "libopus", "-b:a", "32k",
    ],
}


def convert_to_target(filepath: str) -> str | None:
    """将音频文件转换为目标格式 (Opus 24kHz mono 32kbps)，返回新路径；失败返回 None"""
    target_ext = TARGET_FORMAT["ext"]
    base = os.path.splitext(filepath)[0]
    tmp_output = base + ".tmp_conv" + target_ext
    final_output = base + target_ext

    try:
        cmd = ["ffmpeg", "-y", "-i", filepath, *TARGET_FORMAT["ffmpeg_args"], tmp_output]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if proc.returncode != 0 or not os.path.exists(tmp_output) or os.path.getsize(tmp_output) == 0:
            if os.path.exists(tmp_output):
                os.remove(tmp_output)
            return None

        if filepath.lower() != final_output.lower():
            os.remove(filepath)
        if os.path.exists(final_output) and final_output != filepath:
            os.remove(final_output)
        os.rename(tmp_output, final_output)
        return final_output

    except Exception as e:
        logger.warning(f"格式转换失败 {filepath}: {e}")
        if os.path.exists(tmp_output):
            os.remove(tmp_output)
        return None


class Downloader:
    def __init__(self, storage: Storage, max_workers: int | None = None):
        self.storage = storage
        self.max_workers = max_workers or MAX_CONCURRENT_DOWNLOADS
        self.semaphore = asyncio.Semaphore(self.max_workers)
        self.stats = {"success": 0, "failed": 0, "skipped": 0, "dup": 0}
        self._connector = None

    async def download_all(self, limit: int = 50, source: str | None = None):
        pending = self.storage.get_pending(limit=limit, source=source)
        if not pending:
            logger.info("没有待下载的音频")
            return self.stats

        total = len(pending)
        logger.info(f"开始下载 {total} 个音频文件 (并发={self.max_workers})...")

        self._connector = aiohttp.TCPConnector(limit=self.max_workers, limit_per_host=3)
        async with aiohttp.ClientSession(connector=self._connector) as session:
            # B站需要先获取 cookie
            has_bilibili = any(item.get("source") == "bilibili" for item in pending)
            if has_bilibili:
                await session.get("https://www.bilibili.com",
                                  headers=BILIBILI_HEADERS,
                                  timeout=aiohttp.ClientTimeout(total=10))

            tasks = [self._download_one(session, item, i + 1, total) for i, item in enumerate(pending)]
            await asyncio.gather(*tasks)

        done = self.stats["success"] + self.stats["skipped"] + self.stats["dup"]
        logger.info(
            f"下载完成 — 成功: {self.stats['success']}, 跳过: {self.stats['skipped']}, "
            f"去重: {self.stats['dup']}, 失败: {self.stats['failed']} | 总计: {done}/{total}"
        )
        return self.stats

    async def _download_one(self, session: aiohttp.ClientSession, item: dict,
                             idx: int, total: int):
        async with self.semaphore:
            url = item["url"]
            title = item.get("title", "")
            fmt = item.get("file_format", "")
            source = item.get("source", "")
            source_id = item.get("source_id", "")
            category = item.get("category", "")

            # B站：实时刷新音频流 URL
            if source == "bilibili" and source_id:
                fresh_url = await _resolve_bilibili_url(session, source_id)
                if fresh_url:
                    url = fresh_url
                else:
                    logger.warning(f"[{idx}/{total}] B站URL刷新失败, 跳过: {title}")
                    self.storage.update_status(item["url"], "failed")
                    self.stats["failed"] += 1
                    return
                await random_delay(0.5, 1.0)

            subdir = self._build_subdir(source, category)
            filename = safe_filename(item["url"], title, fmt, source_id)
            filepath = os.path.join(subdir, filename)

            progress = f"[{idx}/{total}]"

            if os.path.exists(filepath):
                existing_size = os.path.getsize(filepath)
                expected_size = item.get("file_size", 0)
                if expected_size > 0 and existing_size >= expected_size:
                    self.storage.update_status(item["url"], "done", filepath)
                    self.stats["skipped"] += 1
                    logger.info(f"{progress} - 跳过(已存在): {filename}")
                    return

            self.storage.update_status(item["url"], "downloading")

            for attempt in range(MAX_RETRIES):
                try:
                    await self._do_download(session, url, filepath)

                    content_hash = Storage.compute_file_hash(filepath)
                    if self.storage.hash_exists(content_hash):
                        os.remove(filepath)
                        self.storage.update_status(item["url"], "done", f"dup:{content_hash}")
                        self.stats["dup"] += 1
                        return

                    self.storage.set_content_hash(item["url"], content_hash)

                    converted_path = await asyncio.to_thread(convert_to_target, filepath)
                    if converted_path:
                        filepath = converted_path
                        filename = os.path.basename(filepath)
                    else:
                        logger.warning(f"{progress} 格式转换失败, 保留原始文件: {filename}")

                    self.storage.update_status(item["url"], "done", filepath)
                    self.stats["success"] += 1
                    size_mb = os.path.getsize(filepath) / 1024 / 1024
                    _save_meta(filepath, item, content_hash)
                    logger.info(f"{progress} ✓ {filename} ({size_mb:.1f}MB)")
                    return
                except Exception as e:
                    wait = RETRY_BACKOFF ** attempt
                    logger.warning(f"{progress} 下载失败 [{attempt+1}/{MAX_RETRIES}] {filename}: {e}")
                    if source == "bilibili" and attempt < MAX_RETRIES - 1:
                        fresh_url = await _resolve_bilibili_url(session, source_id)
                        if fresh_url:
                            url = fresh_url
                    await asyncio.sleep(wait)

            self.storage.update_status(item["url"], "failed")
            self.stats["failed"] += 1
            logger.error(f"{progress} ✗ {filename}")

    def _build_subdir(self, source: str, category: str) -> str:
        parts = [DOWNLOAD_DIR]
        if source:
            parts.append(source)
        if category:
            safe_cat = re.sub(r'[^\w\u4e00-\u9fff]', '_', category).strip('_')
            if safe_cat:
                parts.append(safe_cat)
        subdir = os.path.join(*parts)
        os.makedirs(subdir, exist_ok=True)
        return subdir

    async def _do_download(self, session: aiohttp.ClientSession, url: str, filepath: str):
        referer = _guess_referer(url)
        headers = build_headers(referer)

        existing_size = 0
        if os.path.exists(filepath):
            existing_size = os.path.getsize(filepath)
            headers["Range"] = f"bytes={existing_size}-"

        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
        proxy = get_aio_proxy()

        async with session.get(url, headers=headers, timeout=timeout,
                               proxy=proxy, allow_redirects=True) as resp:
            if resp.status == 416:
                return
            if resp.status not in (200, 206):
                raise Exception(f"HTTP {resp.status}")

            mode = "ab" if resp.status == 206 else "wb"
            async with aiofiles.open(filepath, mode) as f:
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    await f.write(chunk)

        await random_delay(0.2, 0.5)


def _save_meta(filepath: str, item: dict, content_hash: str = ""):
    """在音频文件旁生成同名 .json 元信息文件"""
    meta_path = os.path.splitext(filepath)[0] + ".json"
    meta = {
        "title": item.get("title", ""),
        "source": item.get("source", ""),
        "source_id": item.get("source_id", ""),
        "file_format": item.get("file_format", ""),
        "file_size": os.path.getsize(filepath) if os.path.exists(filepath) else 0,
        "duration": item.get("duration", 0),
        "language": item.get("language", ""),
        "category": item.get("category", ""),
        "speaker": item.get("speaker", ""),
        "content_hash": content_hash or item.get("content_hash", ""),
    }
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"元信息写入失败 {meta_path}: {e}")


def fix_meta(storage: Storage):
    """为已下载但没有 .json 的音频文件补生成元信息"""
    conn = storage._get_conn()
    rows = conn.execute(
        "SELECT * FROM audio_urls WHERE status='done' AND local_path != '' AND local_path NOT LIKE 'dup:%'"
    ).fetchall()

    fixed = 0
    for r in rows:
        filepath = r["local_path"]
        if not os.path.exists(filepath):
            continue
        meta_path = os.path.splitext(filepath)[0] + ".json"
        if os.path.exists(meta_path):
            continue
        item = dict(r)
        _save_meta(filepath, item)
        fixed += 1

    logger.info(f"补生成元信息: {fixed} 个文件")
    return fixed
