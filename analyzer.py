"""中文分词与词频统计模块。

基于 jieba 分词，提供停用词过滤、词频统计与自定义词汇管理。
自定义词汇会写入本地词库文件，并在后续分词中生效。
"""

from __future__ import annotations

import re
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import jieba

import config


class AnalysisError(Exception):
    """分词 / 统计阶段的业务异常。"""

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


@dataclass
class AnalysisResult:
    frequencies: Counter
    stats: dict = field(default_factory=dict)
    top_words: list = field(default_factory=list)
    warning: str = ""


_CN_RE = re.compile(r"[\u4e00-\u9fff]")
_TOKEN_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fffA-Za-z0-9'\-·]*$")
_EN_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]*$")
_PUNCT_RE = re.compile(r"^[^\w\u4e00-\u9fff]+$", re.UNICODE)
_CUSTOM_WORD_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fffA-Za-z0-9'\-· ]*$")

_lock = threading.RLock()
_stopwords: set[str] | None = None
_custom_words: list[str] = []
_jieba_ready = False


def _read_word_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    words: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        word = line.strip()
        if not word or word.startswith("#"):
            continue
        words.append(word)
    return words


def load_stopwords(force: bool = False) -> set[str]:
    """加载内置停用词与用户自定义停用词。"""
    global _stopwords
    with _lock:
        if _stopwords is not None and not force:
            return _stopwords
        words: set[str] = set()
        for path in (config.STOPWORDS_FILE, config.CUSTOM_STOPWORDS_FILE):
            for word in _read_word_file(path):
                words.add(word.lower())
        _stopwords = words
        return _stopwords


def load_custom_words(force: bool = False) -> list[str]:
    """加载自定义词库，并注册到 jieba。"""
    global _custom_words
    with _lock:
        if _custom_words and not force:
            return list(_custom_words)
        words = _read_word_file(config.CUSTOM_WORDS_FILE)
        seen: set[str] = set()
        result: list[str] = []
        for word in words:
            if word in seen:
                continue
            seen.add(word)
            result.append(word)
            jieba.add_word(word, freq=20000, tag="nz")
        _custom_words = result
        return list(_custom_words)


def warm_up() -> None:
    """提前加载词典与停用词，避免首次请求等待。"""
    global _jieba_ready
    load_stopwords()
    load_custom_words()
    if not _jieba_ready:
        jieba.initialize()
        _jieba_ready = True


def get_custom_words() -> list[str]:
    return load_custom_words()


def add_custom_word(raw_word: str) -> str:
    """新增自定义词汇并持久化。"""
    word = unicodedata.normalize("NFKC", (raw_word or "").strip())
    word = re.sub(r"\s+", " ", word)
    if not word:
        raise AnalysisError("INVALID_WORD", "请输入要添加的词汇。")
    if len(word) > 20:
        raise AnalysisError("INVALID_WORD", "词汇长度不能超过 20 个字符。")
    if not _CUSTOM_WORD_RE.match(word):
        raise AnalysisError("INVALID_WORD", "词汇只能包含中文、字母、数字及常见连接符。")
    if word.lower() in load_stopwords():
        raise AnalysisError("INVALID_WORD", "该词属于停用词，无需添加。")

    with _lock:
        words = load_custom_words()
        if word in words:
            raise AnalysisError("WORD_EXISTS", "该词汇已在自定义词库中。")
        with config.CUSTOM_WORDS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(word + "\n")
        _custom_words.append(word)
        jieba.add_word(word, freq=20000, tag="nz")
    return word


def remove_custom_word(raw_word: str) -> None:
    """删除自定义词汇并同步词典文件。"""
    word = unicodedata.normalize("NFKC", (raw_word or "").strip())
    with _lock:
        words = load_custom_words()
        if word not in words:
            raise AnalysisError("WORD_NOT_FOUND", "自定义词库中没有该词汇。")
        remaining = [item for item in words if item != word]
        config.CUSTOM_WORDS_FILE.write_text(
            "\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8"
        )
        try:
            jieba.del_word(word)
        except Exception:
            pass
        _custom_words[:] = remaining


def analyze(text: str, min_len: int = 2, top_n: int = 200) -> AnalysisResult:
    """对正文分词并统计词频。

    min_len: 中文词语的最小长度（1-6）
    top_n: 返回的高频词数量
    """
    if not text or not text.strip():
        raise AnalysisError("EMPTY_CONTENT", "正文内容为空，无法进行分词。")

    min_len = max(1, min(6, int(min_len)))
    stopwords = load_stopwords()
    load_custom_words()
    warm_up()

    normalized = unicodedata.normalize("NFKC", text)[: config.MAX_TEXT_CHARS]

    counter: Counter[str] = Counter()
    raw_tokens = 0
    for raw in jieba.lcut(normalized, cut_all=False):
        raw_tokens += 1
        token = raw.strip().strip("·…—-_'\"“”‘’")
        if len(token) < 2:
            continue
        if token.isdigit():
            continue
        if _PUNCT_RE.match(token):
            continue

        lowered = token.lower()
        if lowered in stopwords:
            continue

        if _CN_RE.search(token):
            if len(token) < min_len:
                continue
            if not _TOKEN_RE.match(token):
                continue
            counter[token] += 1
        elif _EN_RE.match(token):
            counter[lowered] += 1
        # 其余（纯符号、混合符号串）直接忽略

    if not counter:
        raise AnalysisError(
            "INSUFFICIENT_WORDS",
            "没有提取到有效词汇，正文可能以符号、数字或单字为主。",
        )
    if len(counter) < config.MIN_UNIQUE_WORDS:
        raise AnalysisError(
            "INSUFFICIENT_WORDS",
            f"有效词汇过少（仅 {len(counter)} 个），无法生成有意义的词云。",
        )

    warning = ""
    if len(counter) < config.WARN_UNIQUE_WORDS:
        warning = f"可用词汇较少（{len(counter)} 个），词云可能比较稀疏，建议更换内容更丰富的页面。"

    top_words = counter.most_common(max(1, min(top_n, 300)))
    stats = {
        "chars": len(normalized),
        "raw_tokens": raw_tokens,
        "tokens": sum(counter.values()),
        "unique": len(counter),
    }
    return AnalysisResult(
        frequencies=counter,
        stats=stats,
        top_words=top_words,
        warning=warning,
    )
