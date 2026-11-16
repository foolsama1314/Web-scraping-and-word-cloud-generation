"""SQLite 数据访问层。

设计说明
--------
* 使用 Python 内置 ``sqlite3``，无需单独部署数据库服务；
* SQL 语句只出现在本模块内部，业务代码（爬虫、任务、Web 接口）全部通过
  Python 方法调用数据库，不手写 SQL；
* 每次写操作都在事务中完成，异常自动回滚，保证原子性；
* 连接按线程复用（``threading.local``），开启 WAL 模式以支持并发读写。

表结构
------
pages       爬取到的页面：来源、正文、时间、状态等
word_stats  每个页面的分词结果：词 + 词频（与 pages 一对多，级联删除）
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterable
from urllib.parse import urlparse

import config

# --------------------------------------------------------------------------
# 常量与异常
# --------------------------------------------------------------------------
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
STATUS_DELETED = "deleted"
VALID_STATUS = {STATUS_ACTIVE, STATUS_ARCHIVED, STATUS_DELETED}

SORT_FIELDS = {"created_at", "updated_at", "char_count", "id", "title"}
MAX_PAGE_SIZE = 100
EXCERPT_LENGTH = 120

_local = threading.local()
_init_lock = threading.Lock()
_initialized = False

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS pages (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id      TEXT UNIQUE,
        url          TEXT NOT NULL,
        final_url    TEXT NOT NULL DEFAULT '',
        host         TEXT NOT NULL DEFAULT '',
        title        TEXT NOT NULL DEFAULT '',
        content      TEXT NOT NULL,
        content_hash TEXT NOT NULL DEFAULT '',
        charset      TEXT NOT NULL DEFAULT '',
        html_bytes   INTEGER NOT NULL DEFAULT 0,
        char_count   INTEGER NOT NULL DEFAULT 0,
        status       TEXT NOT NULL DEFAULT 'active',
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pages_host ON pages(host)",
    "CREATE INDEX IF NOT EXISTS idx_pages_status ON pages(status)",
    "CREATE INDEX IF NOT EXISTS idx_pages_created ON pages(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pages_hash ON pages(content_hash)",
    """
    CREATE TABLE IF NOT EXISTS word_stats (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        page_id    INTEGER NOT NULL,
        word       TEXT NOT NULL,
        frequency  INTEGER NOT NULL DEFAULT 1,
        min_len    INTEGER NOT NULL DEFAULT 2,
        created_at TEXT NOT NULL,
        FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE,
        UNIQUE (page_id, word)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_words_page ON word_stats(page_id)",
    "CREATE INDEX IF NOT EXISTS idx_words_word ON word_stats(word)",
    "CREATE INDEX IF NOT EXISTS idx_words_freq ON word_stats(frequency DESC)",
)


class DatabaseError(Exception):
    """数据库层业务异常。"""

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


# --------------------------------------------------------------------------
# 连接管理
# --------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    """获取当前线程的数据库连接。"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    config.DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DATABASE_FILE), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _local.conn = conn
    return conn


@contextmanager
def transaction():
    """写操作事务：正常结束提交，异常回滚。"""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db(reset: bool = False, seed: bool = False) -> dict:
    """建表建索引（幂等），可选清空 / 写入示例数据。"""
    global _initialized
    with _init_lock:
        conn = _connect()
        if reset:
            conn.executescript("DROP TABLE IF EXISTS word_stats; DROP TABLE IF EXISTS pages;")
            conn.commit()
        for statement in _SCHEMA:
            conn.execute(statement)
        conn.commit()
        _initialized = True
        result = {"tables": ["pages", "word_stats"], "total": count_pages(None)}
        if seed:
            result["seeded"] = _seed_demo_data()
            result["total"] = count_pages(None)
        return result


def _ensure_ready() -> None:
    if _initialized:
        return
    init_db()


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------
def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:32]


def _row_to_page(row: sqlite3.Row, with_content: bool = False) -> dict:
    data = {
        "id": row["id"],
        "task_id": row["task_id"],
        "url": row["url"],
        "final_url": row["final_url"],
        "host": row["host"],
        "title": row["title"] or "未命名页面",
        "charset": row["charset"],
        "html_bytes": row["html_bytes"],
        "char_count": row["char_count"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if with_content:
        data["content"] = row["content"]
    else:
        row_content = row["content"] or ""
        data["excerpt"] = row_content[:EXCERPT_LENGTH]
    return data


def _build_where(keyword: str, host: str, status: str, include_deleted: bool):
    clauses: list[str] = []
    params: list[Any] = []
    if include_deleted:
        pass
    elif status:
        clauses.append("status = ?")
        params.append(status)
    else:
        clauses.append("status != ?")
        params.append(STATUS_DELETED)
    if host:
        clauses.append("host = ?")
        params.append(host.lower())
    if keyword:
        clauses.append("(title LIKE ? OR url LIKE ? OR content LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


# --------------------------------------------------------------------------
# 新增
# --------------------------------------------------------------------------
def create_page(
    *,
    url: str,
    content: str,
    title: str = "",
    final_url: str = "",
    host: str = "",
    charset: str = "",
    html_bytes: int = 0,
    status: str = STATUS_ACTIVE,
    task_id: str | None = None,
    words: Counter | dict | None = None,
    min_len: int = 2,
) -> int:
    """新增单条页面（含分词结果），整体在一个事务内完成。"""
    _ensure_ready()
    ids = create_pages(
        [
            {
                "url": url,
                "content": content,
                "title": title,
                "final_url": final_url,
                "host": host,
                "charset": charset,
                "html_bytes": html_bytes,
                "status": status,
                "task_id": task_id,
                "words": words,
                "min_len": min_len,
            }
        ]
    )
    return ids[0]


def create_pages(records: Iterable[dict]) -> list[int]:
    """批量新增页面，单事务批量写入，任一失败整批回滚。"""
    _ensure_ready()
    rows = list(records)
    if not rows:
        return []

    created: list[int] = []
    now = _now()
    with transaction() as conn:
        for item in rows:
            content = (item.get("content") or "").strip()
            if not content:
                raise DatabaseError("EMPTY_CONTENT", "正文内容不能为空。")
            url = (item.get("url") or "").strip()
            if not url:
                raise DatabaseError("INVALID_URL", "来源网址不能为空。")
            status = item.get("status") if item.get("status") in VALID_STATUS else STATUS_ACTIVE
            final_url = item.get("final_url") or url
            host = (item.get("host") or _host_of(final_url)).lower()
            cursor = conn.execute(
                """
                INSERT INTO pages
                    (task_id, url, final_url, host, title, content, content_hash,
                     charset, html_bytes, char_count, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("task_id"),
                    url,
                    final_url,
                    host,
                    (item.get("title") or "").strip(),
                    content,
                    _hash(content),
                    item.get("charset") or "",
                    int(item.get("html_bytes") or 0),
                    len(content),
                    status,
                    item.get("created_at") or now,
                    now,
                ),
            )
            page_id = int(cursor.lastrowid)
            created.append(page_id)
            _write_words(conn, page_id, item.get("words"), int(item.get("min_len") or 2), now)
    return created


def _write_words(conn: sqlite3.Connection, page_id: int, words, min_len: int, now: str) -> int:
    """批量写入分词结果（先清空该页旧数据）。"""
    if not words:
        return 0
    items = words.items() if isinstance(words, dict) else iter(words)
    payload = [(page_id, str(word), int(count), min_len, now) for word, count in items if word]
    if not payload:
        return 0
    conn.execute("DELETE FROM word_stats WHERE page_id = ?", (page_id,))
    conn.executemany(
        """
        INSERT INTO word_stats (page_id, word, frequency, min_len, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def save_word_stats(page_id: int, words, min_len: int = 2) -> int:
    """覆盖式更新某页面的分词结果。"""
    _ensure_ready()
    with transaction() as conn:
        return _write_words(conn, page_id, words, min_len, _now())


# --------------------------------------------------------------------------
# 查询
# --------------------------------------------------------------------------
def get_page(page_id: int, with_content: bool = True) -> dict | None:
    _ensure_ready()
    row = _connect().execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
    return _row_to_page(row, with_content) if row else None


def get_page_by_task(task_id: str, with_content: bool = False) -> dict | None:
    _ensure_ready()
    row = _connect().execute("SELECT * FROM pages WHERE task_id = ?", (task_id,)).fetchone()
    return _row_to_page(row, with_content) if row else None


def list_pages(
    page: int = 1,
    size: int = 20,
    keyword: str = "",
    host: str = "",
    status: str = "",
    include_deleted: bool = False,
    sort: str = "created_at",
    order: str = "desc",
) -> dict:
    """分页 + 条件查询，返回列表与分页元信息。"""
    _ensure_ready()
    page = max(1, int(page or 1))
    size = max(1, min(MAX_PAGE_SIZE, int(size or 20)))
    sort = sort if sort in SORT_FIELDS else "created_at"
    order = "ASC" if str(order).lower() == "asc" else "DESC"

    where, params = _build_where(keyword.strip(), host.strip(), status, include_deleted)
    conn = _connect()
    total = conn.execute(f"SELECT COUNT(*) AS c FROM pages{where}", params).fetchone()["c"]
    rows = conn.execute(
        f"SELECT * FROM pages{where} ORDER BY {sort} {order}, id DESC LIMIT ? OFFSET ?",
        [*params, size, (page - 1) * size],
    ).fetchall()

    items = []
    for row in rows:
        item = _row_to_page(row)
        item["word_count"] = conn.execute(
            "SELECT COUNT(*) AS c FROM word_stats WHERE page_id = ?", (row["id"],)
        ).fetchone()["c"]
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "pages": max(1, (total + size - 1) // size),
    }


def get_word_frequencies(page_id: int) -> Counter:
    """从数据库读取某页面的分词结果，供词云引擎使用。"""
    _ensure_ready()
    rows = _connect().execute(
        "SELECT word, frequency FROM word_stats WHERE page_id = ? ORDER BY frequency DESC",
        (page_id,),
    ).fetchall()
    return Counter({row["word"]: row["frequency"] for row in rows})


def get_word_stats(page_id: int, limit: int = 200) -> list[dict]:
    _ensure_ready()
    rows = _connect().execute(
        """
        SELECT word, frequency FROM word_stats
        WHERE page_id = ? ORDER BY frequency DESC, word ASC LIMIT ?
        """,
        (page_id, max(1, min(1000, limit))),
    ).fetchall()
    return [{"word": row["word"], "frequency": row["frequency"]} for row in rows]


def count_pages(status: str | None = None) -> int:
    _ensure_ready()
    conn = _connect()
    if status is None:
        return conn.execute("SELECT COUNT(*) AS c FROM pages").fetchone()["c"]
    return conn.execute("SELECT COUNT(*) AS c FROM pages WHERE status = ?", (status,)).fetchone()["c"]


def list_hosts(limit: int = 20) -> list[dict]:
    """来源域名分布（仅统计未删除数据）。"""
    _ensure_ready()
    rows = _connect().execute(
        """
        SELECT host, COUNT(*) AS count FROM pages
        WHERE status != ? GROUP BY host ORDER BY count DESC, host ASC LIMIT ?
        """,
        (STATUS_DELETED, max(1, min(100, limit))),
    ).fetchall()
    return [{"host": row["host"] or "未知来源", "count": row["count"]} for row in rows]


def top_words(limit: int = 15) -> list[dict]:
    """全局高频词（跨页面聚合，只统计未删除页面）。"""
    _ensure_ready()
    rows = _connect().execute(
        """
        SELECT w.word AS word,
               SUM(w.frequency) AS total,
               COUNT(DISTINCT w.page_id) AS pages
        FROM word_stats w
        JOIN pages p ON p.id = w.page_id
        WHERE p.status != ?
        GROUP BY w.word
        ORDER BY total DESC, word ASC
        LIMIT ?
        """,
        (STATUS_DELETED, max(1, min(100, limit))),
    ).fetchall()
    return [
        {"word": row["word"], "frequency": row["total"], "pages": row["pages"]}
        for row in rows
    ]


def daily_counts(days: int = 7) -> list[dict]:
    """最近 N 天每日新增数量。"""
    _ensure_ready()
    days = max(1, min(30, days))
    start = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = _connect().execute(
        """
        SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS count
        FROM pages WHERE created_at >= ? GROUP BY day
        """,
        (start,),
    ).fetchall()
    mapping = {row["day"]: row["count"] for row in rows}
    result = []
    for offset in range(days):
        day = (datetime.now() - timedelta(days=days - 1 - offset)).strftime("%Y-%m-%d")
        result.append({"date": day[5:], "count": mapping.get(day, 0)})
    return result


def stats_overview() -> dict:
    """管理后台看板指标。"""
    _ensure_ready()
    conn = _connect()
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END), 0) AS active,
               COALESCE(SUM(CASE WHEN status = 'archived' THEN 1 ELSE 0 END), 0) AS archived,
               COALESCE(SUM(CASE WHEN status = 'deleted' THEN 1 ELSE 0 END), 0) AS deleted,
               COALESCE(SUM(char_count), 0) AS total_chars
        FROM pages
        """
    ).fetchone()
    word_row = conn.execute(
        "SELECT COUNT(*) AS rows, COUNT(DISTINCT word) AS unique_words FROM word_stats"
    ).fetchone()

    hosts = list_hosts(8)
    base = row["active"] or 0
    for item in hosts:
        item["percent"] = round(item["count"] * 100 / base, 1) if base else 0.0

    return {
        "total": row["total"],
        "active": row["active"],
        "archived": row["archived"],
        "deleted": row["deleted"],
        "total_chars": row["total_chars"],
        "avg_chars": round(row["total_chars"] / row["total"], 1) if row["total"] else 0,
        "word_rows": word_row["rows"],
        "unique_words": word_row["unique_words"],
        "avg_words": round(word_row["unique_words"] / row["total"], 1) if row["total"] else 0,
        "hosts": hosts,
        "top_words": top_words(12),
        "daily": daily_counts(7),
    }


# --------------------------------------------------------------------------
# 更新与删除
# --------------------------------------------------------------------------
def update_page(page_id: int, words: Counter | dict | None = None, **fields) -> dict | None:
    """更新页面字段；若正文变化则同步重算分词结果（同一事务）。"""
    _ensure_ready()
    page = get_page(page_id, with_content=False)
    if not page:
        return None

    allowed = {"title", "url", "final_url", "host", "status", "content", "charset"}
    updates = {key: value for key, value in fields.items() if key in allowed and value is not None}
    if not updates:
        return page

    if "status" in updates and updates["status"] not in VALID_STATUS:
        raise DatabaseError("INVALID_STATUS", "数据状态不合法。")

    content_changed = False
    if "content" in updates:
        content = str(updates["content"]).strip()
        if not content:
            raise DatabaseError("EMPTY_CONTENT", "正文内容不能为空。")
        updates["content"] = content
        updates["char_count"] = len(content)
        updates["content_hash"] = _hash(content)
        content_changed = True

    if "final_url" in updates and "host" not in updates:
        updates["host"] = _host_of(str(updates["final_url"]))
    if "url" in updates and "host" not in updates and "final_url" not in updates:
        updates["host"] = _host_of(str(updates["url"]))
    if "host" in updates:
        updates["host"] = str(updates["host"]).lower()

    updates["updated_at"] = _now()
    assignment = ", ".join(f"{key} = ?" for key in updates)
    now = updates["updated_at"]

    with transaction() as conn:
        conn.execute(
            f"UPDATE pages SET {assignment} WHERE id = ?",
            [*updates.values(), page_id],
        )
        if content_changed or words is not None:
            if words is None:
                content = updates.get("content") or get_page(page_id)["content"]
                import analyzer  # 延迟导入，避免循环依赖

                words = analyzer.analyze(content).frequencies
            _write_words(conn, page_id, words, 2, now)

    return get_page(page_id)


def delete_page(page_id: int, hard: bool = False) -> bool:
    """逻辑删除（默认）或物理删除。"""
    _ensure_ready()
    with transaction() as conn:
        if hard:
            cursor = conn.execute("DELETE FROM pages WHERE id = ?", (page_id,))
        else:
            cursor = conn.execute(
                "UPDATE pages SET status = ?, updated_at = ? WHERE id = ? AND status != ?",
                (STATUS_DELETED, _now(), page_id, STATUS_DELETED),
            )
    return cursor.rowcount > 0


def delete_pages(page_ids: Iterable[int], hard: bool = False) -> int:
    """批量删除，返回受影响行数。"""
    _ensure_ready()
    ids = [int(item) for item in page_ids]
    if not ids:
        return 0
    placeholders = ", ".join("?" for _ in ids)
    with transaction() as conn:
        if hard:
            cursor = conn.execute(f"DELETE FROM pages WHERE id IN ({placeholders})", ids)
        else:
            cursor = conn.execute(
                f"UPDATE pages SET status = ?, updated_at = ? "
                f"WHERE id IN ({placeholders}) AND status != ?",
                [STATUS_DELETED, _now(), *ids, STATUS_DELETED],
            )
    return cursor.rowcount


def restore_page(page_id: int) -> bool:
    """把逻辑删除的数据恢复为可用状态。"""
    _ensure_ready()
    with transaction() as conn:
        cursor = conn.execute(
            "UPDATE pages SET status = ?, updated_at = ? WHERE id = ?",
            (STATUS_ACTIVE, _now(), page_id),
        )
    return cursor.rowcount > 0


# --------------------------------------------------------------------------
# 示例数据（初始化脚本用）
# --------------------------------------------------------------------------
_SEED = [
    (
        "https://zh.wikipedia.org/wiki/数据可视化",
        "数据可视化",
        "数据可视化是指以图形化方式呈现数据的技术与方法，借助图表与色彩帮助人们理解数据背后的规律，"
        "常见形式包括折线图、柱状图、散点图与热力图，被广泛应用于数据分析、商业智能与科学研究。",
        {"数据": 6, "可视化": 5, "图表": 4, "分析": 3, "图形": 2},
    ),
    (
        "https://www.example.com/article",
        "示例文章 · 人工智能",
        "人工智能正在改变生产与生活方式，机器学习算法在大规模数据中心运行，深度学习推动自然语言处理"
        "与计算机视觉不断突破，研究者同时关注算法公平、数据安全与隐私保护。",
        {"人工智能": 3, "机器学习": 2, "深度学习": 2, "数据": 2, "算法": 2},
    ),
    (
        "https://www.ruc.edu.cn/news",
        "校园新闻 · 数据科学",
        "数据科学融合统计学、计算机科学与领域知识，通过数据采集、清洗、建模与可视化支撑决策，"
        "课程覆盖数据库、数据挖掘与机器学习实践。",
        {"数据": 5, "科学": 3, "可视化": 2, "统计": 2, "模型": 2},
    ),
]


def _seed_demo_data() -> int:
    records = [
        {
            "url": url,
            "final_url": url,
            "title": title,
            "content": content,
            "words": Counter(words),
            "min_len": 2,
        }
        for url, title, content, words in _SEED
    ]
    return len(create_pages(records))


def reset_connection() -> None:
    """关闭当前线程连接（测试或删除数据库文件后调用）。"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        _local.conn = None
