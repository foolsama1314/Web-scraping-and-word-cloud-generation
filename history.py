"""历史记录与本地文件管理。

历史记录保存在 data/history.json，图片与正文文件按相对路径引用，
删除记录时可一并清理磁盘文件。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import config

_lock = threading.RLock()
MAX_RECORDS = 200


def _read() -> list[dict]:
    if not config.HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(config.HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _write(records: list[dict]) -> None:
    tmp = config.HISTORY_FILE.parent / (config.HISTORY_FILE.name + ".tmp")
    tmp.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, config.HISTORY_FILE)


def resolve_asset(relative_path: str | None) -> Path | None:
    """把相对路径解析为 DATA_DIR 内的真实路径，阻止路径穿越。"""
    if not relative_path:
        return None
    base = config.DATA_DIR.resolve()
    path = (base / relative_path).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path


def add_record(record: dict) -> dict:
    with _lock:
        records = _read()
        records = [item for item in records if item.get("id") != record.get("id")]
        records.insert(0, record)
        _write(records[:MAX_RECORDS])
    return record


def list_records(limit: int = 40) -> list[dict]:
    with _lock:
        records = _read()
    return records[: max(1, min(limit, MAX_RECORDS))]


def get_record(task_id: str) -> dict | None:
    with _lock:
        for record in _read():
            if record.get("id") == task_id:
                return record
    return None


def delete_record(task_id: str) -> bool:
    with _lock:
        records = _read()
        target = next((item for item in records if item.get("id") == task_id), None)
        if target is None:
            return False
        remaining = [item for item in records if item.get("id") != task_id]
        _write(remaining)

    for key in ("image_file", "text_file"):
        path = resolve_asset(target.get(key))
        if path and path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
    return True


def clear_records() -> int:
    with _lock:
        records = _read()
        if not records:
            return 0
        _write([])

    removed = 0
    for record in records:
        for key in ("image_file", "text_file"):
            path = resolve_asset(record.get(key))
            if path and path.is_file():
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
    return len(records)
