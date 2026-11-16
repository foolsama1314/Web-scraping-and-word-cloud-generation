"""WordCloud Studio —— Flask Web 应用入口。

启动方式：
    python app.py
"""

from __future__ import annotations

import hmac
import io
import re
from datetime import datetime

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

import admin_api
import analyzer
import cloud_engine
import config
import crawler
import db
import history
import task_manager

app = Flask(__name__)
app.json.ensure_ascii = False
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.secret_key = config.SECRET_KEY

app.register_blueprint(admin_api.admin_bp)

# 未登录也允许访问的端点
PUBLIC_ENDPOINTS = {"login_page", "login_api", "logout_api", "static"}


# --------------------------------------------------------------------------
# 登录鉴权
# --------------------------------------------------------------------------
@app.before_request
def require_login():
    """未登录时：页面跳转到登录页，接口返回 401。"""
    if request.method == "OPTIONS":
        return ("", 204)
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if session.get("user"):
        return None
    if request.path.startswith("/api/"):
        return _error(401, "UNAUTHORIZED", "请先登录后再操作。")
    return redirect(url_for("login_page", next=request.path))


@app.get("/login")
def login_page():
    if session.get("user"):
        return redirect(url_for("index"))
    next_url = request.args.get("next") or "/"
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"
    demo_accounts = [
        {"account": name, "password": password}
        for name, password in config.AUTH_ACCOUNTS.items()
    ]
    return render_template(
        "login.html", next_url=next_url, demo_accounts=demo_accounts
    )


@app.post("/api/login")
def login_api():
    payload = request.get_json(silent=True) or {}
    account = str(payload.get("account") or "").strip()
    password = str(payload.get("password") or "")

    if not account or not password:
        return _error(400, "INVALID_CREDENTIALS", "请输入账号和密码。")

    expected = config.AUTH_ACCOUNTS.get(account)
    if not expected or not hmac.compare_digest(expected, password):
        return _error(401, "LOGIN_FAILED", "账号或密码不正确，请使用页面上的演示账号。")

    session.clear()
    session["user"] = account
    return jsonify({"ok": True, "user": account})


@app.post("/api/logout")
def logout_api():
    session.clear()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# 页面
# --------------------------------------------------------------------------
@app.get("/")
def index():
    meta = {
        "fonts": config.font_options(),
        "shapes": [
            {"key": key, "label": item["label"], "desc": item["desc"]}
            for key, item in cloud_engine.SHAPES.items()
        ],
        "palettes": cloud_engine.palette_options(),
        "backgrounds": config.background_options(),
        "sizes": [
            {"label": "横版 1200 × 800", "width": 1200, "height": 800},
            {"label": "高清 1600 × 1000", "width": 1600, "height": 1000},
            {"label": "方形 1000 × 1000", "width": 1000, "height": 1000},
            {"label": "竖版 800 × 1200", "width": 800, "height": 1200},
        ],
        "defaults": {
            "shape": "rectangle",
            "palette": "coral",
            "font": config.DEFAULT_FONT,
            "background": "white",
            "width": 1200,
            "height": 800,
            "max_words": 150,
            "min_len": 2,
        },
        "limits": {
            "width": list(config.SIZE_LIMITS),
            "height": list(config.SIZE_LIMITS),
            "max_words": list(config.MAX_WORDS_LIMITS),
            "min_len": [1, 6],
        },
        "custom_words": analyzer.get_custom_words(),
        "stopword_count": len(analyzer.load_stopwords()),
    }
    return render_template("index.html", meta=meta, user=session.get("user", ""))


@app.get("/admin")
def admin_page():
    """数据库管理后台。"""
    return render_template("admin.html", user=session.get("user", ""))


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def _error(status: int, code: str, message: str, detail: str = ""):
    return (
        jsonify({"ok": False, "error": {"code": code, "message": message, "detail": detail}}),
        status,
    )


def _find_result(task_id: str) -> dict | None:
    record = history.get_record(task_id)
    if record:
        return record
    task = task_manager.get_task(task_id)
    if task and task.get("result"):
        return task["result"]
    return None


def _download_name(title: str, suffix: str) -> str:
    base = re.sub(r"[\\/:*?\"<>|\r\n]+", " ", title or "").strip()
    base = re.sub(r"\s+", " ", base)[:50] or "网页词云"
    return f"{base}_{datetime.now().strftime('%Y%m%d')}{suffix}"


def _public_record(record: dict) -> dict:
    task_id = record.get("id", "")
    return {
        "id": task_id,
        "title": record.get("title", "未命名页面"),
        "url": record.get("url", ""),
        "created_at": record.get("created_at", ""),
        "params": record.get("params", {}),
        "stats": record.get("stats", {}),
        "top_words": record.get("top_words", []),
        "elapsed": record.get("elapsed", 0),
        "preview_url": f"/api/preview/{task_id}",
        "image_download_url": f"/api/download/{task_id}/image",
        "text_download_url": f"/api/download/{task_id}/text",
    }


# --------------------------------------------------------------------------
# 任务 API
# --------------------------------------------------------------------------
@app.post("/api/tasks")
def create_task():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error(400, "INVALID_REQUEST", "请求格式不正确，应为 JSON。")

    url = (payload.get("url") or "").strip()
    try:
        crawler.normalize_url(url)
    except crawler.CrawlError as exc:
        return _error(400, exc.code, exc.message, exc.detail)

    options = task_manager.normalize_options(payload.get("options"))
    task = task_manager.submit(url, options)
    return jsonify({"ok": True, "task": task}), 202


@app.get("/api/tasks/<task_id>")
def get_task(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        return _error(404, "TASK_NOT_FOUND", "任务不存在或已过期。")
    return jsonify({"ok": True, "task": task})


# --------------------------------------------------------------------------
# 结果预览与下载
# --------------------------------------------------------------------------
@app.get("/api/preview/<task_id>")
def preview(task_id: str):
    result = _find_result(task_id)
    path = history.resolve_asset(result.get("image_file")) if result else None
    if not path or not path.is_file():
        abort(404)
    response = send_file(path, mimetype="image/png", as_attachment=False, max_age=0)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/download/<task_id>/image")
def download_image(task_id: str):
    result = _find_result(task_id)
    path = history.resolve_asset(result.get("image_file")) if result else None
    if not path or not path.is_file():
        abort(404)
    return send_file(
        path,
        mimetype="image/png",
        as_attachment=True,
        download_name=_download_name(result.get("title", ""), ".png"),
        max_age=0,
    )


@app.get("/api/download/<task_id>/text")
def download_text(task_id: str):
    """正文下载：优先从数据库读取，兼容历史版本落盘的 txt 文件。"""
    result = _find_result(task_id)
    if not result:
        abort(404)

    page = None
    page_id = result.get("page_id")
    if page_id:
        try:
            page = db.get_page(int(page_id))
        except (TypeError, ValueError):
            page = None
    if page is None:
        page = db.get_page_by_task(task_id, with_content=True)

    if page and page.get("content"):
        buffer = io.BytesIO(page["content"].encode("utf-8"))
        return send_file(
            buffer,
            mimetype="text/plain; charset=utf-8",
            as_attachment=True,
            download_name=_download_name(result.get("title", ""), ".txt"),
            max_age=0,
        )

    path = history.resolve_asset(result.get("text_file")) if result else None
    if not path or not path.is_file():
        abort(404)
    response = send_file(
        path,
        as_attachment=True,
        download_name=_download_name(result.get("title", ""), ".txt"),
        max_age=0,
    )
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    return response


# --------------------------------------------------------------------------
# 历史记录
# --------------------------------------------------------------------------
@app.get("/api/history")
def list_history():
    try:
        limit = int(request.args.get("limit", 40))
    except (TypeError, ValueError):
        limit = 40
    records = history.list_records(limit)
    return jsonify({"ok": True, "records": [_public_record(item) for item in records]})


@app.delete("/api/history/<task_id>")
def delete_history(task_id: str):
    removed = history.delete_record(task_id)
    if not removed:
        return _error(404, "RECORD_NOT_FOUND", "历史记录不存在。")
    return jsonify({"ok": True})


@app.delete("/api/history")
def clear_history():
    count = history.clear_records()
    return jsonify({"ok": True, "removed": count})


# --------------------------------------------------------------------------
# 自定义词库
# --------------------------------------------------------------------------
@app.get("/api/custom-words")
def list_custom_words():
    return jsonify({"ok": True, "words": analyzer.get_custom_words()})


@app.post("/api/custom-words")
def add_custom_word():
    payload = request.get_json(silent=True) or {}
    word = payload.get("word", "")
    try:
        added = analyzer.add_custom_word(word)
    except analyzer.AnalysisError as exc:
        return _error(400, exc.code, exc.message)
    return jsonify({"ok": True, "word": added, "words": analyzer.get_custom_words()})


@app.delete("/api/custom-words/<path:word>")
def remove_custom_word(word: str):
    try:
        analyzer.remove_custom_word(word)
    except analyzer.AnalysisError as exc:
        return _error(404, exc.code, exc.message)
    return jsonify({"ok": True, "words": analyzer.get_custom_words()})


# --------------------------------------------------------------------------
# 编辑器布局持久化
# --------------------------------------------------------------------------
def _layout_path(task_id: str) -> Path:
    """构造布局 JSON 路径，task_id 仅允许字母数字。"""
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", task_id)[:32]
    return config.LAYOUT_DIR / f"{safe_id}.json"


@app.get("/api/layout/<task_id>")
def get_layout(task_id: str):
    """获取用户之前编辑保存的布局。"""
    path = _layout_path(task_id)
    if not path.is_file():
        return jsonify({"ok": True, "layout": None})
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _error(500, "LAYOUT_READ_FAILED", "布局文件读取失败。")
    return jsonify({"ok": True, "layout": data})


@app.put("/api/layout/<task_id>")
def save_layout(task_id: str):
    """保存用户编辑后的词云布局。

    payload:
        layout: list[dict]   词汇元素列表（前端编辑后回传）
        canvas: {width, height} 画布尺寸
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error(400, "INVALID_REQUEST", "请求格式不正确。")

    layout = payload.get("layout")
    canvas = payload.get("canvas") or {}
    if not isinstance(layout, list):
        return _error(400, "INVALID_LAYOUT", "layout 必须为数组。")

    # 基本校验和清理
    cleaned: list[dict] = []
    for item in layout:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word", ""))[:80]
        if not word:
            continue
        try:
            x = float(item.get("x", 0))
            y = float(item.get("y", 0))
            width = float(item.get("width", 0))
            height = float(item.get("height", 0))
            font_size = int(item.get("font_size", 16))
            rotation = float(item.get("rotation", 0))
            color = item.get("color", [51, 51, 51])
            z_index = int(item.get("z_index", 0))
        except (TypeError, ValueError):
            continue

        if width <= 0 or height <= 0:
            continue
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            color = [51, 51, 51]
        color = [max(0, min(255, int(c))) for c in color]

        cleaned.append(
            {
                "word": word,
                "x": round(x, 2),
                "y": round(y, 2),
                "width": round(width, 2),
                "height": round(height, 2),
                "font_size": max(8, min(400, font_size)),
                "rotation": round(rotation, 2),
                "color": color,
                "z_index": z_index,
            }
        )

    if len(cleaned) > 500:
        cleaned = cleaned[:500]

    try:
        import json
        tmp_path = _layout_path(task_id).with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(
                {"layout": cleaned, "canvas": canvas, "version": 1},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp_path.replace(_layout_path(task_id))
    except OSError as exc:
        return _error(500, "LAYOUT_SAVE_FAILED", "布局保存失败。", str(exc))

    return jsonify({"ok": True, "count": len(cleaned)})


@app.delete("/api/layout/<task_id>")
def delete_layout(task_id: str):
    """清除用户保存的布局（恢复为自动布局）。"""
    path = _layout_path(task_id)
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# 错误处理
# --------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(_exc):
    if request.path.startswith("/api/"):
        return _error(404, "NOT_FOUND", "请求的资源不存在。")
    return render_template("index.html", meta={
        "fonts": config.font_options(),
        "shapes": [],
        "palettes": [],
        "backgrounds": [],
        "sizes": [],
        "defaults": {},
        "limits": {},
        "custom_words": [],
        "stopword_count": 0,
    }), 404


@app.errorhandler(500)
def internal_error(_exc):
    if request.path.startswith("/api/"):
        return _error(500, "INTERNAL", "服务端出现异常，请查看控制台日志。")
    return "服务端异常", 500


@app.after_request
def api_headers(response):
    """统一为 /api/* 注入 CORS 头，并禁止接口被缓存。"""
    if request.path.startswith("/api/"):
        origin = request.headers.get("Origin")
        response.headers["Access-Control-Allow-Origin"] = origin or "*"
        if origin:
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        )
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, X-Requested-With"
        )
        response.headers["Access-Control-Max-Age"] = "600"
        response.headers["Cache-Control"] = "no-store"
    return response


# 应用启动时确保表结构就绪
db.init_db()


if __name__ == "__main__":
    analyzer.warm_up()
    print("\n  WordCloud Studio 已启动")
    print(f"  访问地址: http://{config.HOST}:{config.PORT}\n")
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG, threaded=True, use_reloader=False)
