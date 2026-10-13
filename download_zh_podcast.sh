#!/usr/bin/env bash
#
# 持续下载 podcast_rss 中文音频
# 用法：
#   bash download_zh_podcast.sh
#   WORKERS=30 LIMIT=500 bash download_zh_podcast.sh
#   FORMAT=original bash download_zh_podcast.sh   # 保留原始格式，跳过 ffmpeg 转 opus
#   RETRY_FAILED=1 bash download_zh_podcast.sh    # 重试一批 failed，再持续下载 pending
#   RETRY_FAILED=all bash download_zh_podcast.sh  # 分批重试全部 failed，再持续下载 pending
#
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python}"
SOURCE="${SOURCE:-podcast_rss}"
LANGUAGE="${LANGUAGE:-zh}"
WORKERS="${WORKERS:-20}"
LIMIT="${LIMIT:-5000}"
INTERVAL="${INTERVAL:-60}"
FORMAT="${FORMAT:-opus}"   # opus | original
RETRY_FAILED="${RETRY_FAILED:-0}"
HOST_MAX_SHARE="${HOST_MAX_SHARE:-0.20}"

if [[ "$FORMAT" != "opus" && "$FORMAT" != "original" ]]; then
  echo "错误: FORMAT 只能是 opus 或 original，当前值: $FORMAT" >&2
  exit 1
fi

if [[ "$RETRY_FAILED" != "0" && "$RETRY_FAILED" != "1" && "$RETRY_FAILED" != "all" ]]; then
  echo "错误: RETRY_FAILED 只能是 0、1 或 all，当前值: $RETRY_FAILED" >&2
  exit 1
fi

failed_count() {
  SOURCE="$SOURCE" LANGUAGE="$LANGUAGE" "$PYTHON_BIN" - <<'PY'
import os
import sqlite3

from config import DB_PATH

conn = sqlite3.connect(DB_PATH, timeout=60)
row = conn.execute(
    "SELECT COUNT(*) FROM audio_urls "
    "WHERE status='failed' AND source=? AND language=?",
    (os.environ["SOURCE"], os.environ["LANGUAGE"]),
).fetchone()
print(row[0])
PY
}

retry_failed_batch() {
  "$PYTHON_BIN" main.py \
    --source "$SOURCE" \
    --language "$LANGUAGE" \
    --retry-failed \
    --workers "$WORKERS" \
    --limit "$LIMIT" \
    --format "$FORMAT"
}

echo "[$(date '+%F %T')] 启动持续下载"
echo "  source=$SOURCE language=$LANGUAGE workers=$WORKERS limit=$LIMIT interval=$INTERVAL format=$FORMAT retry_failed=$RETRY_FAILED host_max_share=$HOST_MAX_SHARE"

if [[ "$RETRY_FAILED" == "1" ]]; then
  retry_failed_batch
elif [[ "$RETRY_FAILED" == "all" ]]; then
  batch=0
  while true; do
    before="$(failed_count)"
    if (( before == 0 )); then
      echo "[$(date '+%F %T')] 所有 failed 已重试完毕，开始持续下载 pending"
      break
    fi

    batch=$((batch + 1))
    echo "[$(date '+%F %T')] failed 全量重试第 $batch 批，剩余 $before 条"
    retry_failed_batch

    after="$(failed_count)"
    if (( after >= before )); then
      echo "错误: failed 数量未减少（重试前 $before，重试后 $after），停止以避免死循环" >&2
      exit 1
    fi
  done
fi

exec "$PYTHON_BIN" main.py \
  --source "$SOURCE" \
  --language "$LANGUAGE" \
  --loop \
  --workers "$WORKERS" \
  --limit "$LIMIT" \
  --interval "$INTERVAL" \
  --host-max-share "$HOST_MAX_SHARE" \
  --format "$FORMAT"
