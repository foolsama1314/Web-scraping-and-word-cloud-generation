"""管理后台 API。

所有接口位于 ``/api/admin`` 下，需登录后访问；
统一返回 JSON，CORS 响应头由应用层统一注入。
"""

from __future__ import annotations

import analyzer
import crawler
import db

from flask import Blueprint, jsonify, request

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


# --------------------------------------------------------------------------
# 响应工具
# --------------------------------------------------------------------------
def _ok(**payload):
    payload.setdefault("ok", True)
    return jsonify(payload)


def _fail(status: int, code: str, message: str, detail: str = ""):
    return (
        jsonify({"ok": False, "error": {"code": code, "message": message, "detail": detail}}),
        status,
    )


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _analyze(content: str):
    """尝试分词，失败时返回空 Counter（不影响正文入库）。"""
    try:
        return analyzer.analyze(content).frequencies
    except analyzer.AnalysisError:
        return {}


def _normalize_url(raw: str) -> str:
    try:
        return crawler.normalize_url(raw)
    except crawler.CrawlError as exc:
        raise ValueError(exc.message) from exc


# --------------------------------------------------------------------------
# 看板统计
# --------------------------------------------------------------------------
@admin_bp.get("/stats")
def stats():
    data = db.stats_overview()
    data["sources"] = db.list_hosts(50)
    return _ok(stats=data)


# --------------------------------------------------------------------------
# 列表 / 详情
# --------------------------------------------------------------------------
@admin_bp.get("/pages")
def list_pages():
    args = request.args
    data = db.list_pages(
        page=_int(args.get("page"), 1),
        size=_int(args.get("size"), 20),
        keyword=args.get("q", ""),
        host=args.get("host", ""),
        status=args.get("status", ""),
        include_deleted=args.get("include_deleted") == "1",
        sort=args.get("sort", "created_at"),
        order=args.get("order", "desc"),
    )
    data["sources"] = db.list_hosts(50)
    return _ok(**data)


@admin_bp.get("/pages/<int:page_id>")
def get_page(page_id: int):
    page = db.get_page(page_id)
    if not page:
        return _fail(404, "PAGE_NOT_FOUND", "数据不存在或已被删除。")
    page["words"] = db.get_word_stats(page_id, 60)
    return _ok(page=page)


# --------------------------------------------------------------------------
# 新增
# --------------------------------------------------------------------------
@admin_bp.post("/pages")
def create_page():
    payload = request.get_json(silent=True) or {}
    content = str(payload.get("content") or "").strip()
    raw_url = str(payload.get("url") or "").strip()

    if not content:
        return _fail(400, "EMPTY_CONTENT", "正文内容不能为空。")
    if len(content) < 10:
        return _fail(400, "CONTENT_TOO_SMALL", "正文至少需要 10 个字符。")
    try:
        url = _normalize_url(raw_url)
    except ValueError as exc:
        return _fail(400, "INVALID_URL", str(exc))

    words = payload.get("words") or _analyze(content)
    try:
        page_id = db.create_page(
            url=url,
            final_url=str(payload.get("final_url") or url),
            title=str(payload.get("title") or "").strip(),
            content=content,
            host=str(payload.get("host") or "").strip(),
            charset=str(payload.get("charset") or ""),
            html_bytes=_int(payload.get("html_bytes"), 0),
            status=payload.get("status") if payload.get("status") in db.VALID_STATUS else db.STATUS_ACTIVE,
            words=words,
            min_len=_int(payload.get("min_len"), 2),
        )
    except db.DatabaseError as exc:
        return _fail(400, exc.code, exc.message, exc.detail)

    return _ok(page=db.get_page(page_id), created=1)


@admin_bp.post("/pages/batch")
def create_pages():
    payload = request.get_json(silent=True) or {}
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return _fail(400, "INVALID_REQUEST", "请提供 items 数组。")
    if len(items) > 100:
        return _fail(400, "TOO_MANY_ITEMS", "单次最多批量新增 100 条。")

    records = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            return _fail(400, "INVALID_REQUEST", f"第 {index} 条数据格式不正确。")
        content = str(item.get("content") or "").strip()
        if len(content) < 10:
            return _fail(400, "CONTENT_TOO_SMALL", f"第 {index} 条正文过短（至少 10 字）。")
        try:
            url = _normalize_url(str(item.get("url") or ""))
        except ValueError as exc:
            return _fail(400, "INVALID_URL", f"第 {index} 条网址无效：{exc}")
        records.append(
            {
                "url": url,
                "final_url": str(item.get("final_url") or url),
                "title": str(item.get("title") or "").strip(),
                "content": content,
                "host": str(item.get("host") or "").strip(),
                "charset": str(item.get("charset") or ""),
                "html_bytes": _int(item.get("html_bytes"), 0),
                "status": item.get("status") if item.get("status") in db.VALID_STATUS else db.STATUS_ACTIVE,
                "words": item.get("words") or _analyze(content),
                "min_len": _int(item.get("min_len"), 2),
            }
        )

    try:
        ids = db.create_pages(records)
    except db.DatabaseError as exc:
        return _fail(400, exc.code, exc.message, exc.detail)
    return _ok(created=len(ids), ids=ids)


# --------------------------------------------------------------------------
# 更新
# --------------------------------------------------------------------------
@admin_bp.put("/pages/<int:page_id>")
def update_page(page_id: int):
    payload = request.get_json(silent=True) or {}
    current = db.get_page(page_id)
    if not current:
        return _fail(404, "PAGE_NOT_FOUND", "数据不存在或已被删除。")

    fields: dict = {}
    if "title" in payload:
        fields["title"] = str(payload.get("title") or "").strip()
    if "status" in payload:
        if payload.get("status") not in db.VALID_STATUS:
            return _fail(400, "INVALID_STATUS", "数据状态不合法。")
        fields["status"] = payload["status"]
    if "url" in payload:
        try:
            fields["url"] = _normalize_url(str(payload.get("url") or ""))
        except ValueError as exc:
            return _fail(400, "INVALID_URL", str(exc))
    if "host" in payload:
        fields["host"] = str(payload.get("host") or "").strip()

    words = None
    if "content" in payload:
        content = str(payload.get("content") or "").strip()
        if len(content) < 10:
            return _fail(400, "CONTENT_TOO_SMALL", "正文至少需要 10 个字符。")
        if content != current.get("content"):
            fields["content"] = content
            words = _analyze(content)

    try:
        page = db.update_page(page_id, words=words, **fields)
    except db.DatabaseError as exc:
        return _fail(400, exc.code, exc.message, exc.detail)
    if not page:
        return _fail(404, "PAGE_NOT_FOUND", "数据不存在或已被删除。")
    return _ok(page=page)


@admin_bp.post("/pages/<int:page_id>/analyze")
def reanalyze(page_id: int):
    page = db.get_page(page_id)
    if not page:
        return _fail(404, "PAGE_NOT_FOUND", "数据不存在或已被删除。")
    try:
        result = analyzer.analyze(page["content"])
    except analyzer.AnalysisError as exc:
        return _fail(400, exc.code, exc.message)
    count = db.save_word_stats(page_id, result.frequencies, 2)
    return _ok(page_id=page_id, words=count, top_words=result.top_words[:12])


# --------------------------------------------------------------------------
# 删除 / 恢复（支持逻辑删除与物理删除）
# --------------------------------------------------------------------------
@admin_bp.delete("/pages/<int:page_id>")
def delete_page(page_id: int):
    hard = request.args.get("hard") == "1"
    if not db.delete_page(page_id, hard=hard):
        return _fail(404, "PAGE_NOT_FOUND", "数据不存在或已是删除状态。")
    return _ok(deleted=1, hard=hard)


@admin_bp.delete("/pages")
def batch_delete():
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids") or request.args.get("ids", "")
    if isinstance(ids, str):
        ids = [item for item in ids.split(",") if item.strip().isdigit()]
    if not isinstance(ids, list) or not ids:
        return _fail(400, "INVALID_REQUEST", "请提供要删除的 id 列表。")
    hard = payload.get("hard") is True or request.args.get("hard") == "1"
    removed = db.delete_pages(ids, hard=hard)
    return _ok(deleted=removed, hard=hard)


@admin_bp.post("/pages/<int:page_id>/restore")
def restore_page(page_id: int):
    if not db.restore_page(page_id):
        return _fail(404, "PAGE_NOT_FOUND", "数据不存在。")
    return _ok(page=db.get_page(page_id))
