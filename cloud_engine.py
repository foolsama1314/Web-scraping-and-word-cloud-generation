"""词云生成引擎。

支持矩形、圆形、心形、五角星、云朵、菱形六种形状；
内置渐变 / 莫兰迪 / 多套色板，支持自定义画布尺寸与中文字体。
"""

from __future__ import annotations

import math
import random
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from wordcloud import WordCloud

import config

_RGB_RE = re.compile(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")


class CloudError(Exception):
    """词云渲染阶段的业务异常。"""

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


@dataclass
class CloudResult:
    image_file: str
    width: int
    height: int
    used_words: int
    top_words: list
    palette: str
    shape: str
    font: str
    layout: list | None = None


SHAPES = {
    "rectangle": {"label": "矩形", "desc": "经典满版"},
    "circle": {"label": "圆形", "desc": "聚焦中心"},
    "heart": {"label": "心形", "desc": "浪漫表达"},
    "star": {"label": "星形", "desc": "突出焦点"},
    "cloud": {"label": "云朵", "desc": "轻盈柔和"},
    "diamond": {"label": "菱形", "desc": "利落几何"},
}

PALETTES = {
    "coral": {
        "label": "珊瑚暖阳",
        "type": "gradient",
        "colors": ["#ff6a3d", "#ffb703"],
    },
    "ocean": {
        "label": "深海蓝",
        "type": "gradient",
        "colors": ["#0b3d91", "#41c9e2"],
    },
    "violet": {
        "label": "紫罗兰",
        "type": "gradient",
        "colors": ["#5b21b6", "#f472b6"],
    },
    "forest": {
        "label": "森林绿",
        "type": "gradient",
        "colors": ["#0b6e4f", "#a3e635"],
    },
    "sky": {
        "label": "晴空",
        "type": "gradient",
        "colors": ["#1d6fa5", "#a6e3ff"],
    },
    "sakura": {
        "label": "樱花",
        "type": "gradient",
        "colors": ["#d94f74", "#ffc2d1"],
    },
    "morandi": {
        "label": "莫兰迪",
        "type": "custom",
        "colors": ["#8a9a8b", "#b8a89a", "#9aa5b1", "#c4a69a", "#7d8ca3", "#a8b5a0"],
    },
    "viridis": {"label": "翡翠", "type": "colormap", "colormap": "viridis"},
    "rainbow": {"label": "彩虹", "type": "colormap", "colormap": "rainbow"},
    "magma": {"label": "熔岩", "type": "colormap", "colormap": "magma"},
    "cool": {"label": "清凉", "type": "colormap", "colormap": "cool"},
    "mono": {
        "label": "水墨",
        "type": "custom",
        "colors": ["#111827", "#374151", "#4b5563", "#6b7280", "#1f2937"],
    },
}


# --------------------------------------------------------------------------
# 形状蒙版
# --------------------------------------------------------------------------
def _fit_points(points: list[tuple[float, float]], width: int, height: int, margin: float):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-6)
    span_y = max(max_y - min_y, 1e-6)
    scale = min((width - 2 * margin) / span_x, (height - 2 * margin) / span_y)
    cx = (min_x + max_x) / 2
    cy = (min_y + max_y) / 2
    return [
        ((x - cx) * scale + width / 2, (y - cy) * scale + height / 2)
        for x, y in points
    ]


def _heart_points() -> list[tuple[float, float]]:
    points = []
    for step in range(720):
        t = 2 * math.pi * step / 720
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        points.append((x, -y))  # 图像坐标 y 轴向下
    return points


def _star_points() -> list[tuple[float, float]]:
    points = []
    for step in range(10):
        radius = 1.0 if step % 2 == 0 else 0.42
        angle = -math.pi / 2 + step * math.pi / 5
        points.append((radius * math.cos(angle), radius * math.sin(angle)))
    return points


def build_mask(shape: str, width: int, height: int) -> np.ndarray | None:
    """生成黑白蒙版：黑色区域可绘制文字，白色区域留空。"""
    if shape == "rectangle":
        return None

    supersample = 3
    w, h = width * supersample, height * supersample
    margin = min(w, h) * 0.06
    img = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(img)

    if shape == "circle":
        draw.ellipse([margin, margin, w - margin, h - margin], fill=0)
    elif shape == "heart":
        points = _fit_points(_heart_points(), w, h, margin * 1.1)
        draw.polygon(points, fill=0)
    elif shape == "star":
        points = _fit_points(_star_points(), w, h, margin * 1.2)
        draw.polygon(points, fill=0)
    elif shape == "diamond":
        cx, cy = w / 2, h / 2
        draw.polygon(
            [(cx, margin), (w - margin, cy), (cx, h - margin), (margin, cy)], fill=0
        )
    elif shape == "cloud":
        draw.rounded_rectangle(
            [0.10 * w, 0.44 * h, 0.90 * w, 0.84 * h],
            radius=0.20 * h,
            fill=0,
        )
        draw.ellipse([0.14 * w, 0.26 * h, 0.50 * w, 0.68 * h], fill=0)
        draw.ellipse([0.36 * w, 0.14 * h, 0.72 * w, 0.58 * h], fill=0)
        draw.ellipse([0.58 * w, 0.28 * h, 0.88 * w, 0.62 * h], fill=0)
    else:
        return None

    img = img.resize((width, height), Image.LANCZOS)
    array = np.array(img)
    # 二值化，保证边缘干净
    array = np.where(array >= 250, 255, 0).astype(np.uint8)

    # 蒙版留空过多时（例如形状过小）回退为矩形，避免生成失败
    if np.count_nonzero(array == 0) < width * height * 0.08:
        return None
    return array


# --------------------------------------------------------------------------
# 配色
# --------------------------------------------------------------------------
def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _interpolate(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float):
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))


def _gradient_color_func(start: str, end: str, min_size: int, max_size: int):
    c1, c2 = _hex_to_rgb(start), _hex_to_rgb(end)
    span = max(1, max_size - min_size)

    def color_func(word=None, font_size=None, position=None, orientation=None,
                   font_path=None, random_state=None, **kwargs):
        t = ((font_size or min_size) - min_size) / span
        return _interpolate(c1, c2, t)

    return color_func


def _custom_color_func(colors: list[str]):
    rgb = [_hex_to_rgb(item) for item in colors]

    def color_func(word=None, font_size=None, position=None, orientation=None,
                   font_path=None, random_state=None, **kwargs):
        rng = random_state if random_state is not None else random
        return rgb[rng.randint(0, len(rgb) - 1)]

    return color_func


def palette_preview(key: str, count: int = 4) -> list[str]:
    """给前端色板缩略图使用的颜色列表。"""
    palette = PALETTES.get(key) or PALETTES["coral"]
    if palette["type"] == "gradient":
        start, end = palette["colors"]
        return [
            "#%02x%02x%02x" % _interpolate(_hex_to_rgb(start), _hex_to_rgb(end), i / (count - 1))
            for i in range(count)
        ]
    if palette["type"] == "custom":
        return palette["colors"][:count]
    try:
        import matplotlib

        cmap = matplotlib.colormaps[palette["colormap"]]
        return [
            "#%02x%02x%02x" % tuple(int(c * 255) for c in cmap(i / (count - 1))[:3])
            for i in range(count)
        ]
    except Exception:
        return ["#4c6ef5", "#15aabf", "#fab005", "#fa5252"]


def palette_options() -> list[dict]:
    return [
        {"key": key, "label": item["label"], "preview": palette_preview(key)}
        for key, item in PALETTES.items()
    ]


# --------------------------------------------------------------------------
# 生成
# --------------------------------------------------------------------------
def _clamp_int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _parse_color(color_str: str) -> tuple[int, int, int]:
    """把 wordcloud 输出的 'rgb(R, G, B)' 字符串解析为 (R, G, B)。"""
    match = _RGB_RE.search(color_str) if color_str else None
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return (51, 51, 51)


def extract_layout(cloud: WordCloud, font_path: str) -> list[dict]:
    """从 WordCloud 实例提取每个词的位置/尺寸/颜色数据，供前端编辑画布使用。

    返回列表每项：
    {
        "word": str,        词汇文本
        "weight": float,    相对权重 0-1
        "font_size": int,   字号 px
        "x": float,         中心点 x（原图像素坐标）
        "y": float,         中心点 y（原图像素坐标）
        "width": float,     词的渲染宽度 px
        "height": float,    词的渲染高度 px
        "rotation": float,  旋转角度（度），0 表示水平
        "color": [R, G, B], 颜色 RGB 三元组
    }
    """
    layout = getattr(cloud, "layout_", None) or []
    if not layout:
        return []

    try:
        font = ImageFont.truetype(font_path, 16)  # 先加载字体，后续动态换 size
    except Exception:
        return []

    result: list[dict] = []
    for item in layout:
        try:
            (word, weight), font_size, (cx, cy), orientation, color_str = item
        except (ValueError, TypeError):
            continue

        try:
            render_font = ImageFont.truetype(font_path, int(font_size))
        except Exception:
            render_font = font

        # 测量渲染尺寸
        try:
            bbox = render_font.getbbox(word)
            tw = float(bbox[2] - bbox[0])
            th = float(bbox[3] - bbox[1])
        except Exception:
            try:
                tw = float(render_font.getlength(word))
                th = float(font_size) * 1.2
            except Exception:
                tw = float(font_size) * len(word) * 0.6
                th = float(font_size) * 1.2

        rotation = 0.0
        if orientation:
            try:
                rotation = float(orientation)
            except (TypeError, ValueError):
                rotation = 0.0

        r, g, b = _parse_color(str(color_str or ""))

        result.append(
            {
                "word": str(word),
                "weight": float(weight or 0.0),
                "font_size": int(font_size),
                "x": float(cx),
                "y": float(cy),
                "width": tw,
                "height": th,
                "rotation": rotation,
                "color": [r, g, b],
            }
        )

    return result


def generate(frequencies, options: dict | None = None) -> CloudResult:
    """根据词频生成词云 PNG，返回生成结果。"""
    options = options or {}
    width = _clamp_int(options.get("width"), 1200, *config.SIZE_LIMITS)
    height = _clamp_int(options.get("height"), 800, *config.SIZE_LIMITS)
    max_words = _clamp_int(options.get("max_words"), 150, *config.MAX_WORDS_LIMITS)

    shape = options.get("shape") if options.get("shape") in SHAPES else "rectangle"
    palette_key = options.get("palette") if options.get("palette") in PALETTES else "coral"
    background_key = options.get("background")
    if background_key not in config.BACKGROUNDS:
        background_key = "white"
    font_key = options.get("font") or config.DEFAULT_FONT

    font_path = config.resolve_font(font_key)
    if not font_path:
        raise CloudError(
            "FONT_NOT_FOUND",
            "未找到可用的中文字体，请安装中文字体或通过 WCS_FONT_PATH 指定字体文件。",
        )
    if font_key not in config.FONTS:
        font_key = config.DEFAULT_FONT

    items = sorted(frequencies.items(), key=lambda kv: (-kv[1], kv[0]))[:max_words]
    if len(items) < 3:
        raise CloudError(
            "INSUFFICIENT_WORDS", "有效词汇过少（少于 3 个），无法生成词云。"
        )
    freq_dict = {word: float(count) for word, count in items}

    background = config.BACKGROUNDS[background_key]["value"]
    transparent = background is None
    mask = build_mask(shape, width, height)

    max_font_size = max(24, int(min(width, height) * 0.26))
    min_font_size = max(8, int(min(width, height) * 0.012))

    palette = PALETTES[palette_key]
    color_func = None
    colormap = "viridis"
    if palette["type"] == "gradient":
        color_func = _gradient_color_func(
            palette["colors"][0], palette["colors"][1], min_font_size, max_font_size
        )
    elif palette["type"] == "custom":
        color_func = _custom_color_func(palette["colors"])
    else:
        colormap = palette["colormap"]

    light_bg = background in (None, "#ffffff", "#f6f1e7")
    contour_color = "#d9d4c8" if light_bg else "#3b4256"

    kwargs = dict(
        font_path=font_path,
        mask=mask,
        width=width,
        height=height,
        background_color=background,
        max_words=max_words,
        max_font_size=max_font_size,
        min_font_size=min_font_size,
        prefer_horizontal=0.9,
        relative_scaling=0.5,
        margin=6,
        random_state=42,
        repeat=False,
        mode="RGBA" if transparent else "RGB",
    )
    if color_func is not None:
        kwargs["color_func"] = color_func
    else:
        kwargs["colormap"] = colormap
    # 透明背景与 wordcloud 的描边实现存在 RGBA 兼容问题，跳过描边
    if mask is not None and not transparent:
        kwargs["contour_width"] = 3
        kwargs["contour_color"] = contour_color

    try:
        cloud = WordCloud(**kwargs)
        cloud.generate_from_frequencies(freq_dict)
        image = cloud.to_image()
    except ValueError as exc:
        raise CloudError(
            "GENERATE_FAILED",
            "词云渲染失败，请尝试调整画布尺寸、最大词数或更换形状。",
            str(exc),
        ) from exc
    except OSError as exc:
        raise CloudError("FONT_NOT_FOUND", "字体文件无法加载，请更换字体后重试。", str(exc)) from exc
    except Exception as exc:  # pragma: no cover - 兜底
        raise CloudError("GENERATE_FAILED", "词云生成出现未知错误。", str(exc)) from exc

    filename = f"{uuid.uuid4().hex}.png"
    path = config.CLOUD_DIR / filename
    image.save(path, format="PNG", optimize=True)

    if not path.exists() or path.stat().st_size < 1024:
        raise CloudError("GENERATE_FAILED", "词云图片写入失败，请检查磁盘空间。")

    used_words = len(getattr(cloud, "layout_", [])) or len(freq_dict)
    # 提取 layout 数据供前端编辑画布使用
    try:
        layout_data = extract_layout(cloud, font_path)
    except Exception:
        layout_data = []

    return CloudResult(
        image_file=f"clouds/{filename}",
        width=image.width,
        height=image.height,
        used_words=used_words,
        top_words=items[:12],
        palette=palette_key,
        shape=shape,
        font=font_key,
        layout=layout_data,
    )
