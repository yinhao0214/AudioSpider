"""AudioSpider 数据库查看器 —— 查看 audiospider.db 中每条记录的完整细节"""

import sqlite3
import sys
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audiospider.db")


def fmt_size(n: int) -> str:
    if n <= 0:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_duration(sec: int) -> str:
    if sec <= 0:
        return "-"
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def connect():
    if not os.path.exists(DB_PATH):
        print(f"数据库不存在: {DB_PATH}")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def show_overview(conn: sqlite3.Connection):
    print("=" * 70)
    print("  AudioSpider 数据库概览")
    print("=" * 70)

    total = conn.execute("SELECT COUNT(*) FROM audio_urls").fetchone()[0]
    print(f"\n  总记录数: {total}")

    rows = conn.execute(
        "SELECT status, COUNT(*) AS cnt FROM audio_urls GROUP BY status ORDER BY cnt DESC"
    ).fetchall()
    if rows:
        print("\n  按状态统计:")
        for r in rows:
            print(f"    {r['status']:12s}  {r['cnt']}")

    rows = conn.execute(
        "SELECT source, COUNT(*) AS cnt FROM audio_urls GROUP BY source ORDER BY cnt DESC"
    ).fetchall()
    if rows:
        print("\n  按来源统计:")
        for r in rows:
            print(f"    {r['source']:16s}  {r['cnt']}")

    rows = conn.execute(
        "SELECT category, COUNT(*) AS cnt FROM audio_urls WHERE category != '' GROUP BY category ORDER BY cnt DESC"
    ).fetchall()
    if rows:
        print("\n  按分类统计:")
        for r in rows:
            print(f"    {r['category']:12s}  {r['cnt']}")

    show_duration_stats(conn)

    cp = conn.execute("SELECT COUNT(*) FROM crawl_checkpoints").fetchone()[0]
    print(f"\n  爬取检查点数: {cp}")
    print()


def show_all_records(conn: sqlite3.Connection, source_filter: str = "", status_filter: str = "", limit: int = 0):
    where = "WHERE 1=1"
    filter_params: list = []
    if source_filter:
        where += " AND source = ?"
        filter_params.append(source_filter)
    if status_filter:
        where += " AND status = ?"
        filter_params.append(status_filter)

    total = conn.execute(f"SELECT COUNT(*) FROM audio_urls {where}", filter_params).fetchone()[0]

    query = f"SELECT * FROM audio_urls {where} ORDER BY id"
    params = list(filter_params)
    if limit > 0:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("  没有匹配的记录。")
        return

    if limit > 0 and total > limit:
        print(f"\n  共 {total} 条记录, 显示前 {limit} 条\n")
    else:
        print(f"\n  共 {total} 条记录\n")
    for i, r in enumerate(rows, 1):
        print(f"── 记录 #{i} (ID={r['id']}) {'─' * 48}")
        print(f"  标题:       {r['title'] or '-'}")
        print(f"  来源:       {r['source']}")
        print(f"  状态:       {r['status']}")
        print(f"  URL:        {r['url']}")
        print(f"  格式:       {r['file_format'] or '-'}")
        print(f"  大小:       {fmt_size(r['file_size'])}")
        print(f"  时长:       {fmt_duration(r['duration'])}")
        print(f"  语言:       {r['language'] or '-'}")
        print(f"  分类:       {r['category'] or '-'}")
        print(f"  说话人:     {r['speaker'] or '-'}")
        print(f"  本地路径:   {r['local_path'] or '-'}")
        print(f"  内容哈希:   {r['content_hash'] or '-'}")
        print(f"  源站 ID:    {r['source_id'] or '-'}")
        print(f"  发现时间:   {r['discovered_at'] or '-'}")
        print(f"  下载时间:   {r['downloaded_at'] or '-'}")
        print()


def show_single_record(conn: sqlite3.Connection, record_id: int):
    r = conn.execute("SELECT * FROM audio_urls WHERE id = ?", (record_id,)).fetchone()
    if not r:
        print(f"  未找到 ID={record_id} 的记录。")
        return
    print(f"\n{'=' * 60}")
    print(f"  记录详情  (ID={r['id']})")
    print(f"{'=' * 60}")
    print(f"  标题:       {r['title'] or '-'}")
    print(f"  来源:       {r['source']}")
    print(f"  状态:       {r['status']}")
    print(f"  URL:        {r['url']}")
    print(f"  格式:       {r['file_format'] or '-'}")
    print(f"  大小:       {fmt_size(r['file_size'])}")
    print(f"  时长:       {fmt_duration(r['duration'])}")
    print(f"  语言:       {r['language'] or '-'}")
    print(f"  分类:       {r['category'] or '-'}")
    print(f"  说话人:     {r['speaker'] or '-'}")
    print(f"  本地路径:   {r['local_path'] or '-'}")
    print(f"  内容哈希:   {r['content_hash'] or '-'}")
    print(f"  源站 ID:    {r['source_id'] or '-'}")
    print(f"  发现时间:   {r['discovered_at'] or '-'}")
    print(f"  下载时间:   {r['downloaded_at'] or '-'}")
    print()


def show_checkpoints(conn: sqlite3.Connection):
    rows = conn.execute(
        "SELECT * FROM crawl_checkpoints ORDER BY source, checkpoint_key"
    ).fetchall()
    if not rows:
        print("  没有爬取检查点记录。")
        return
    print(f"\n  共 {len(rows)} 条检查点\n")
    for r in rows:
        print(f"  [{r['source']}]  {r['checkpoint_key']} = {r['checkpoint_value']}  (更新于 {r['updated_at']})")
    print()


def show_duration_stats(conn: sqlite3.Connection):
    print("\n  时长统计:")

    row = conn.execute(
        "SELECT COALESCE(SUM(duration), 0) AS sec, COUNT(*) AS cnt "
        "FROM audio_urls WHERE status = 'pending'"
    ).fetchone()
    print(f"    待下载 (pending)    {row['cnt']:>5} 条  总时长 {fmt_duration(row['sec'])}")

    row = conn.execute(
        "SELECT COALESCE(SUM(duration), 0) AS sec, COUNT(*) AS cnt "
        "FROM audio_urls WHERE status = 'done'"
    ).fetchone()
    print(f"    已完成 (done)       {row['cnt']:>5} 条  总时长 {fmt_duration(row['sec'])}")

    row = conn.execute(
        "SELECT COALESCE(SUM(duration), 0) AS sec, COUNT(*) AS cnt "
        "FROM audio_urls WHERE status = 'done' AND local_path != '' AND local_path NOT LIKE 'dup:%'"
    ).fetchone()
    print(f"    本地保留 (去重后)   {row['cnt']:>5} 条  总时长 {fmt_duration(row['sec'])}")

    row = conn.execute(
        "SELECT COALESCE(SUM(duration), 0) AS sec, COUNT(*) AS cnt "
        "FROM audio_urls"
    ).fetchone()
    print(f"    全部记录             {row['cnt']:>5} 条  总时长 {fmt_duration(row['sec'])}")


def interactive(conn: sqlite3.Connection):
    show_overview(conn)
    while True:
        print("─" * 50)
        print("  操作菜单:")
        print("    1  查看记录详情 (可指定条数)")
        print("    2  按来源筛选记录")
        print("    3  按状态筛选记录")
        print("    4  查看单条记录 (输入 ID)")
        print("    5  查看爬取检查点")
        print("    6  查看时长统计")
        print("    7  重新显示概览")
        print("    q  退出")
        print("─" * 50)
        choice = input("  请选择> ").strip().lower()

        if choice == "1":
            n = input("  显示前 N 条 (直接回车显示全部): ").strip()
            limit = int(n) if n.isdigit() else 0
            show_all_records(conn, limit=limit)
        elif choice == "2":
            sources = conn.execute(
                "SELECT DISTINCT source FROM audio_urls ORDER BY source"
            ).fetchall()
            source_names = " / ".join(r["source"] for r in sources)
            src = input(f"  输入来源名称 ({source_names}): ").strip()
            n = input("  显示前 N 条 (直接回车显示全部): ").strip()
            limit = int(n) if n.isdigit() else 0
            show_all_records(conn, source_filter=src, limit=limit)
        elif choice == "3":
            st = input("  输入状态 (pending / downloading / done / failed): ").strip()
            n = input("  显示前 N 条 (直接回车显示全部): ").strip()
            limit = int(n) if n.isdigit() else 0
            show_all_records(conn, status_filter=st, limit=limit)
        elif choice == "4":
            try:
                rid = int(input("  输入记录 ID: ").strip())
                show_single_record(conn, rid)
            except ValueError:
                print("  无效 ID")
        elif choice == "5":
            show_checkpoints(conn)
        elif choice == "6":
            show_duration_stats(conn)
        elif choice == "7":
            show_overview(conn)
        elif choice == "q":
            print("  再见！")
            break
        else:
            print("  无效选择，请重试。")


def _parse_limit(args: list[str]) -> int:
    """从参数列表中提取 -n NUM，返回 limit 值"""
    for i, a in enumerate(args):
        if a == "-n" and i + 1 < len(args) and args[i + 1].isdigit():
            return int(args[i + 1])
    return 0


def main():
    args = sys.argv[1:]
    limit = _parse_limit(args)

    conn = connect()
    try:
        if not args:
            interactive(conn)
        elif args[0] == "overview":
            show_overview(conn)
        elif args[0] == "all":
            show_all_records(conn, limit=limit)
        elif args[0] == "source" and len(args) > 1:
            show_all_records(conn, source_filter=args[1], limit=limit)
        elif args[0] == "status" and len(args) > 1:
            show_all_records(conn, status_filter=args[1], limit=limit)
        elif args[0] == "id" and len(args) > 1:
            show_single_record(conn, int(args[1]))
        elif args[0] == "checkpoints":
            show_checkpoints(conn)
        elif args[0] == "duration":
            show_duration_stats(conn)
        else:
            print("用法:")
            print("  python db_viewer.py                    交互式查看")
            print("  python db_viewer.py overview           数据库概览")
            print("  python db_viewer.py all [-n NUM]       所有记录详情")
            print("  python db_viewer.py source NAME [-n N] 按来源筛选")
            print("  python db_viewer.py status NAME [-n N] 按状态筛选")
            print("  python db_viewer.py id NUM             查看指定 ID")
            print("  python db_viewer.py checkpoints        查看爬取检查点")
            print("  python db_viewer.py duration           查看时长统计")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
