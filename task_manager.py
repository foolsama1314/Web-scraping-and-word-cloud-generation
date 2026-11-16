"""后台任务管理。

每个生成请求对应一个后台线程，前端通过轮询任务状态获取
实时进度、阶段日志与最终结果。
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import analyzer
import cloud_engine
import config
import crawler
import db
import history

_lock = threading.RLock()
_tasks: "OrderedDict[str, dict]" = OrderedDict()
MAX_MEMORY_TASKS = 40


def _now_time() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clamp(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def normalize_options(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    shape = raw.get("shape")
    palette = raw.get("palette")
    font = raw.get("font")
    background = raw.get("background")
    return {
        "shape": shape if shape in cloud_engine.SHAPES else "rectangle",
        "palette": palette if palette in cloud_engine.PALETTES else "coral",
        "font": font if font in config.FONTS else config.DEFAULT_FONT,
        "background": background if background in config.BACKGROUNDS else "white",
        "width": _clamp(raw.get("width"), 1200, *config.SIZE_LIMITS),
        "height": _clamp(raw.get("height"), 800, *config.SIZE_LIMITS),
        "max_words": _clamp(raw.get("max_words"), 150, *config.MAX_WORDS_LIMITS),
        "min_len": _clamp(raw.get("min_len"), 2, 1, 6),
    }


def _set(task_id: str, **fields) -> None:
    with _lock:
        task = _tasks.get(task_id)
        if task is not None:
            task.update(fields)


def _log(task_id: str, stage: str, message: str, level: str = "info") -> None:
    with _lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        task["logs"].append(
            {"time": _now_time(), "stage": stage, "message": message, "level": level}
        )
        if len(task["logs"]) > 60:
            task["logs"] = task["logs"][-60:]


def _fail(task_id: str, code: str, message: str, detail: str = "") -> None:
    with _lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        task.update(
            status="error",
            stage="生成失败",
            message=message,
            error={"code": code, "message": message, "detail": detail},
            finished_at=_now_iso(),
        )
        task["logs"].append(
            {"time": _now_time(), "stage": "失败", "message": message, "level": "error"}
        )


def _snapshot(task: dict) -> dict:
    data = dict(task)
    data["logs"] = list(task["logs"])
    data["result"] = dict(task["result"]) if task.get("result") else None
    data["error"] = dict(task["error"]) if task.get("error") else None
    return data


def _cleanup() -> None:
    finished = [
        key
        for key, item in _tasks.items()
        if item.get("status") in ("success", "error")
    ]
    while len(_tasks) > MAX_MEMORY_TASKS and finished:
        _tasks.pop(finished.pop(0), None)


def submit(url: str, options: dict) -> dict:
    """创建并启动生成任务，立即返回任务快照。"""
    task_id = uuid.uuid4().hex[:12]
    task = {
        "id": task_id,
        "url": url,
        "status": "queued",
        "progress": 0,
        "stage": "排队中",
        "message": "任务已创建",
        "logs": [],
        "result": None,
        "error": None,
        "created_at": _now_iso(),
        "finished_at": None,
    }
    with _lock:
        _tasks[task_id] = task
        _cleanup()

    thread = threading.Thread(
        target=_run, args=(task_id, url, options), name=f"wc-{task_id}", daemon=True
    )
    thread.start()
    return get_task(task_id)


def get_task(task_id: str) -> dict | None:
    with _lock:
        task = _tasks.get(task_id)
        return _snapshot(task) if task else None


def _run(task_id: str, url: str, options: dict) -> None:
    started = time.time()
    try:
        _set(task_id, status="running", progress=2, stage="启动任务")
        _log(task_id, "系统", "任务已启动，开始校验目标网址")

        def report(percent: int, message: str) -> None:
            _set(
                task_id,
                progress=min(60, 4 + int(percent * 0.86)),
                stage=message,
                message=message,
            )
            _log(task_id, "爬虫", message)

        crawl = crawler.crawl_page(url, progress=report, persist_text=False)
        _log(task_id, "爬虫", f"正文 {len(crawl.text)} 字，已提取完成")

        _set(task_id, progress=66, stage="中文分词与词频统计")
        _log(task_id, "分词", "正在分词并过滤停用词")
        analysis = analyzer.analyze(
            crawl.text, min_len=options["min_len"], top_n=options["max_words"]
        )
        _log(
            task_id,
            "分词",
            f"有效词汇 {analysis.stats['unique']} 个，词频总数 {analysis.stats['tokens']}",
        )
        if analysis.warning:
            _log(task_id, "提醒", analysis.warning, "warn")

        _set(task_id, progress=72, stage="写入数据库")
        try:
            page_id = db.create_page(
                task_id=task_id,
                url=crawl.url,
                final_url=crawl.final_url,
                title=crawl.title,
                content=crawl.text,
                charset=crawl.charset,
                html_bytes=crawl.html_bytes,
                words=analysis.frequencies,
                min_len=options["min_len"],
            )
        except db.DatabaseError as exc:
            raise db.DatabaseError("DB_WRITE_FAILED", "正文与分词结果写入数据库失败。", exc.detail) from exc
        _log(
            task_id,
            "数据库",
            f"已写入 pages(id={page_id}) 与 {analysis.stats['unique']} 条分词记录",
        )

        # 从数据库读回分词结果，驱动词云渲染
        _set(task_id, progress=78, stage="从数据库读取词频")
        db_frequencies = db.get_word_frequencies(page_id)
        _log(task_id, "数据库", f"从 word_stats 读取 {len(db_frequencies)} 个词汇")
        frequencies = db_frequencies or analysis.frequencies

        _set(task_id, progress=82, stage="生成词云图像")
        _log(task_id, "渲染", "正在按所选形状、配色与字体渲染")
        cloud = cloud_engine.generate(frequencies, options)
        _log(
            task_id,
            "渲染",
            f"已放置 {cloud.used_words} 个词，输出尺寸 {cloud.width}×{cloud.height}",
        )

        elapsed = round(time.time() - started, 2)
        stats = {
            **analysis.stats,
            "chars": len(crawl.text),
            "used_words": cloud.used_words,
            "html_bytes": crawl.html_bytes,
        }
        result = {
            "title": crawl.title,
            "url": crawl.final_url,
            "source_url": crawl.url,
            "image_file": cloud.image_file,
            "page_id": page_id,
            "preview_url": f"/api/preview/{task_id}",
            "image_download_url": f"/api/download/{task_id}/image",
            "text_download_url": f"/api/download/{task_id}/text",
            "stats": stats,
            "top_words": analysis.top_words[:24],
            "params": options,
            "warning": analysis.warning,
            "elapsed": elapsed,
            "charset": crawl.charset,
            "layout": cloud.layout or [],
            "canvas": {"width": cloud.width, "height": cloud.height},
        }

        _set(task_id, progress=94, stage="保存历史记录")
        try:
            history.add_record(
                {
                    "id": task_id,
                    "title": crawl.title,
                    "url": crawl.final_url,
                    "source_url": crawl.url,
                    "created_at": _now_iso(),
                    "image_file": cloud.image_file,
                    "page_id": page_id,
                    "params": options,
                    "stats": stats,
                    "top_words": analysis.top_words[:12],
                    "elapsed": elapsed,
                }
            )
        except Exception as exc:  # 历史记录失败不影响主流程
            _log(task_id, "提醒", f"历史记录写入失败：{exc}", "warn")

        _set(
            task_id,
            status="success",
            progress=100,
            stage="完成",
            message="词云生成成功",
            result=result,
            finished_at=_now_iso(),
        )
        _log(task_id, "完成", "词云生成成功，可下载图片或正文")
    except (
        crawler.CrawlError,
        analyzer.AnalysisError,
        cloud_engine.CloudError,
        db.DatabaseError,
    ) as exc:
        _fail(task_id, exc.code, exc.message, exc.detail)
    except Exception as exc:  # pragma: no cover - 兜底
        traceback.print_exc()
        _fail(
            task_id,
            "INTERNAL",
            "生成过程中出现未预期的错误，请查看服务端日志。",
            str(exc),
        )
