# Copyright (c) 2026 Hao Yin. All rights reserved.

"""SQLite 存储：URL 去重、下载状态追踪、增量爬取标记、内容指纹"""

import hashlib
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime

from config import DB_PATH, DOWNLOAD_DIR


@dataclass
class AudioRecord:
    url: str
    source: str
    title: str = ""
    file_format: str = ""
    file_size: int = 0
    duration: int = 0
    language: str = ""
    category: str = ""
    speaker: str = ""
    status: str = "pending"
    local_path: str = ""
    content_hash: str = ""
    source_id: str = ""
    discovered_at: str = ""
    downloaded_at: str = ""


class Storage:
    _local = threading.local()

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS audio_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                source TEXT NOT NULL,
                title TEXT DEFAULT '',
                file_format TEXT DEFAULT '',
                file_size INTEGER DEFAULT 0,
                duration INTEGER DEFAULT 0,
                language TEXT DEFAULT '',
                category TEXT DEFAULT '',
                speaker TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                local_path TEXT DEFAULT '',
                content_hash TEXT DEFAULT '',
                source_id TEXT DEFAULT '',
                discovered_at TEXT NOT NULL,
                downloaded_at TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_status ON audio_urls(status);
            CREATE INDEX IF NOT EXISTS idx_source ON audio_urls(source);
            CREATE INDEX IF NOT EXISTS idx_source_id ON audio_urls(source, source_id);
            CREATE INDEX IF NOT EXISTS idx_content_hash ON audio_urls(content_hash);

            CREATE TABLE IF NOT EXISTS crawl_checkpoints (
                source TEXT NOT NULL,
                checkpoint_key TEXT NOT NULL,
                checkpoint_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source, checkpoint_key)
            );
        """)
        conn.commit()

    # ── 增量爬取标记 ──

    def get_checkpoint(self, source: str, key: str) -> str | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT checkpoint_value FROM crawl_checkpoints WHERE source=? AND checkpoint_key=?",
            (source, key),
        ).fetchone()
        return row["checkpoint_value"] if row else None

    def set_checkpoint(self, source: str, key: str, value: str):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO crawl_checkpoints "
            "(source, checkpoint_key, checkpoint_value, updated_at) VALUES (?, ?, ?, ?)",
            (source, key, value, datetime.now().isoformat()),
        )
        conn.commit()

    # ── URL 管理 ──

    def add_url(self, record: AudioRecord) -> bool:
        conn = self._get_conn()
        try:
            record.discovered_at = record.discovered_at or datetime.now().isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO audio_urls "
                "(url, source, title, file_format, file_size, duration, language, "
                "category, speaker, status, source_id, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record.url, record.source, record.title, record.file_format,
                 record.file_size, record.duration, record.language,
                 record.category, record.speaker, record.status,
                 record.source_id, record.discovered_at),
            )
            conn.commit()
            return conn.total_changes > 0
        except sqlite3.Error:
            return False

    def add_urls_batch(self, records: list[AudioRecord]) -> int:
        conn = self._get_conn()
        now = datetime.now().isoformat()
        rows = [
            (r.url, r.source, r.title, r.file_format, r.file_size,
             r.duration, r.language, r.category, r.speaker,
             r.status, r.source_id, r.discovered_at or now)
            for r in records
        ]
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO audio_urls "
            "(url, source, title, file_format, file_size, duration, language, "
            "category, speaker, status, source_id, discovered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        return conn.total_changes - before

    def get_pending(self, limit: int = 50, source: str | None = None) -> list[dict]:
        conn = self._get_conn()
        if source:
            rows = conn.execute(
                "SELECT * FROM audio_urls WHERE status='pending' AND source=? ORDER BY id LIMIT ?",
                (source, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audio_urls WHERE status='pending' ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_status(self, url: str, status: str, local_path: str = ""):
        conn = self._get_conn()
        if status == "done":
            conn.execute(
                "UPDATE audio_urls SET status=?, local_path=?, downloaded_at=? WHERE url=?",
                (status, local_path, datetime.now().isoformat(), url),
            )
        else:
            conn.execute("UPDATE audio_urls SET status=? WHERE url=?", (status, url))
        conn.commit()

    def set_content_hash(self, url: str, content_hash: str):
        conn = self._get_conn()
        conn.execute("UPDATE audio_urls SET content_hash=? WHERE url=?", (content_hash, url))
        conn.commit()

    def hash_exists(self, content_hash: str) -> bool:
        if not content_hash:
            return False
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM audio_urls WHERE content_hash=? AND status='done'",
            (content_hash,),
        ).fetchone()
        return row is not None

    def source_id_exists(self, source: str, source_id: str) -> bool:
        if not source_id:
            return False
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM audio_urls WHERE source=? AND source_id=?",
            (source, source_id),
        ).fetchone()
        return row is not None

    def url_exists(self, url: str) -> bool:
        conn = self._get_conn()
        row = conn.execute("SELECT 1 FROM audio_urls WHERE url=?", (url,)).fetchone()
        return row is not None

    def get_stats(self) -> dict:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM audio_urls GROUP BY status"
        ).fetchall()
        stats = {r["status"]: r["cnt"] for r in rows}
        stats["total"] = sum(stats.values())

        source_rows = conn.execute(
            "SELECT source, status, COUNT(*) as cnt FROM audio_urls GROUP BY source, status"
        ).fetchall()
        by_source: dict[str, dict[str, int]] = {}
        for r in source_rows:
            by_source.setdefault(r["source"], {})[r["status"]] = r["cnt"]
        stats["by_source"] = by_source
        return stats

    def get_downloaded_files(self, limit: int = 20) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT source, title, speaker, category, local_path, downloaded_at "
            "FROM audio_urls WHERE status='done' AND local_path NOT LIKE 'dup:%' "
            "ORDER BY downloaded_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def show_stats(self):
        stats = self.get_stats()
        print("\n" + "=" * 62)
        print("  AudioSpider 语音数据统计")
        print("=" * 62)
        print(f"  {'总计 URL:':<12} {stats.get('total', 0)}")
        print(f"  {'待下载:':<12} {stats.get('pending', 0)}")
        print(f"  {'下载中:':<12} {stats.get('downloading', 0)}")
        print(f"  {'已完成:':<12} {stats.get('done', 0)}")
        print(f"  {'失败:':<12} {stats.get('failed', 0)}")

        by_source = stats.get("by_source", {})
        if by_source:
            print("  " + "─" * 58)
            print(f"  {'来源':<16} {'待下载':>6} {'已完成':>6} {'失败':>6}")
            print("  " + "─" * 58)
            for src in sorted(by_source):
                s = by_source[src]
                pending = s.get("pending", 0)
                done = s.get("done", 0)
                failed = s.get("failed", 0)
                print(f"  {src:<16} {pending:>6} {done:>6} {failed:>6}")

        downloaded = self.get_downloaded_files(10)
        if downloaded:
            print("  " + "─" * 58)
            print("  最近下载:")
            for f in downloaded:
                path = f["local_path"]
                if path and os.path.exists(path):
                    size_mb = os.path.getsize(path) / 1024 / 1024
                    rel = os.path.relpath(path, DOWNLOAD_DIR)
                    print(f"    {rel}")
                    print(f"      {f['title'][:40]} | {size_mb:.1f}MB | {f['downloaded_at'][:16]}")

        disk_total = 0
        file_count = 0
        for root, _, files in os.walk(DOWNLOAD_DIR):
            for fname in files:
                fp = os.path.join(root, fname)
                disk_total += os.path.getsize(fp)
                file_count += 1
        print("  " + "─" * 58)
        print(f"  磁盘: {file_count} 个文件, {disk_total / 1024 / 1024:.1f} MB")
        print("=" * 62 + "\n")

    @staticmethod
    def compute_file_hash(filepath: str) -> str:
        h = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
