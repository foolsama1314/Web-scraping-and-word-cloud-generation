"""全局配置：本地存储路径、可用字体、爬虫策略与生成参数限制。

所有路径均支持通过环境变量覆盖，便于测试与服务器部署。
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ---------- 本地存储 ----------
DATA_DIR = Path(os.environ.get("WCS_DATA_DIR") or (BASE_DIR / "data")).resolve()
PAGE_DIR = DATA_DIR / "pages"          # 爬取到的正文 .txt
CLOUD_DIR = DATA_DIR / "clouds"        # 生成的词云 PNG
LAYOUT_DIR = DATA_DIR / "layouts"       # 用户编辑后的词云布局 JSON
HISTORY_FILE = DATA_DIR / "history.json"
DATABASE_FILE = DATA_DIR / "wordcloud.db"
CUSTOM_WORDS_FILE = DATA_DIR / "custom_words.txt"
CUSTOM_STOPWORDS_FILE = DATA_DIR / "custom_stopwords.txt"
STOPWORDS_FILE = BASE_DIR / "stopwords.txt"


def ensure_dirs() -> None:
    """确保数据目录存在。"""
    for directory in (DATA_DIR, PAGE_DIR, CLOUD_DIR, LAYOUT_DIR):
        directory.mkdir(parents=True, exist_ok=True)


ensure_dirs()

# ---------- 服务 ----------
HOST = os.environ.get("WCS_HOST", "127.0.0.1")
PORT = int(os.environ.get("WCS_PORT", "5000"))
DEBUG = os.environ.get("WCS_DEBUG", "0") == "1"

# ---------- 爬虫策略 ----------
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "WordCloudStudioBot/1.0 (+https://example.com/wordcloud-studio)"
)
ROBOTS_USER_AGENT = "WordCloudStudioBot"
# 同一站点两次请求的最小间隔（秒），可通过环境变量调整
MIN_REQUEST_INTERVAL = float(os.environ.get("WCS_MIN_INTERVAL", "1.5"))
REQUEST_TIMEOUT = (8, 25)              # (连接超时, 读取超时)
MAX_REDIRECTS = 5
MAX_PAGE_BYTES = 5 * 1024 * 1024       # 单页最多读取 5MB
MAX_TEXT_CHARS = 200_000               # 参与分词的最大字符数
# 默认拒绝内网 / 本机地址，避免 SSRF；本地测试可设置 WCS_ALLOW_PRIVATE=1
ALLOW_PRIVATE_HOSTS = os.environ.get("WCS_ALLOW_PRIVATE", "0") == "1"

# ---------- 登录鉴权 ----------
SECRET_KEY = os.environ.get("WCS_SECRET_KEY") or "wcs-dev-secret-key-please-change"


def _build_accounts() -> dict[str, str]:
    """读取演示账号，默认 admin / 123456。

    可通过 WCS_AUTH_ACCOUNTS="admin:123456,test:abc123" 覆盖。
    """
    raw = os.environ.get("WCS_AUTH_ACCOUNTS", "").strip()
    if raw:
        accounts: dict[str, str] = {}
        for item in raw.split(","):
            if ":" not in item:
                continue
            name, password = item.split(":", 1)
            name, password = name.strip(), password.strip()
            if name and password:
                accounts[name] = password
        if accounts:
            return accounts
    return {"admin": "123456"}


AUTH_ACCOUNTS = _build_accounts()

# ---------- 内容与词云限制 ----------
MIN_TEXT_CHARS = 80                    # 正文少于此值直接告警
MIN_UNIQUE_WORDS = 5                   # 唯一词少于此值无法生成
WARN_UNIQUE_WORDS = 15                 # 少于此值给出提示
SIZE_LIMITS = (480, 2400)              # 画布宽高范围
MAX_WORDS_LIMITS = (20, 300)

# ---------- 中文字体 ----------
# 每个字体给出多个候选路径，取第一个存在的；也可用 WCS_FONT_PATH 指定默认字体
_FONT_DEFS = [
    (
        "msyh",
        "微软雅黑",
        [
            "C:/Windows/Fonts/msyh.ttc",
            "/usr/share/fonts/truetype/msttcorefonts/msyh.ttc",
        ],
    ),
    (
        "simhei",
        "黑体",
        [
            "C:/Windows/Fonts/simhei.ttf",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
        ],
    ),
    (
        "simkai",
        "楷体",
        [
            "C:/Windows/Fonts/simkai.ttf",
            "/usr/share/fonts/truetype/arphic/ukai.ttc",
        ],
    ),
    (
        "deng",
        "等线",
        [
            "C:/Windows/Fonts/Deng.ttf",
        ],
    ),
    (
        "simsun",
        "宋体",
        [
            "C:/Windows/Fonts/simsun.ttc",
        ],
    ),
    (
        "noto",
        "思源黑体",
        [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/System/Library/Fonts/PingFang.ttc",
        ],
    ),
]


def _build_fonts() -> dict[str, dict]:
    custom_path = os.environ.get("WCS_FONT_PATH", "").strip()
    fonts: dict[str, dict] = {}
    for key, label, candidates in _FONT_DEFS:
        for candidate in candidates:
            if Path(candidate).is_file():
                fonts[key] = {"key": key, "label": label, "path": candidate}
                break
    if custom_path and Path(custom_path).is_file():
        fonts["custom"] = {"key": "custom", "label": "自定义字体", "path": custom_path}
    return fonts


FONTS = _build_fonts()
DEFAULT_FONT = "msyh" if "msyh" in FONTS else next(iter(FONTS), "")


def font_options() -> list[dict]:
    """返回给前端的字体列表（不含物理路径）。"""
    return [{"key": item["key"], "label": item["label"]} for item in FONTS.values()]


def resolve_font(key: str | None) -> str | None:
    """根据字体键返回字体文件路径；找不到时回退到默认字体。"""
    if key and key in FONTS:
        return FONTS[key]["path"]
    if DEFAULT_FONT in FONTS:
        return FONTS[DEFAULT_FONT]["path"]
    return None


# ---------- 词云背景色 ----------
BACKGROUNDS = {
    "white": {"label": "纯白", "value": "#ffffff"},
    "ink": {"label": "深空", "value": "#10131f"},
    "cream": {"label": "米白", "value": "#f6f1e7"},
    "transparent": {"label": "透明", "value": None},
}


def background_options() -> list[dict]:
    return [
        {"key": key, "label": item["label"], "value": item["value"]}
        for key, item in BACKGROUNDS.items()
    ]
