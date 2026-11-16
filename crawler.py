"""网页爬虫模块。

职责：
1. 规范化并校验 URL（拒绝非 http(s)、内网地址等）；
2. 读取并遵守 robots.txt（含 Crawl-delay），对同一站点做请求间隔限速；
3. 抓取网页（超时、重定向、体积上限、编码识别）；
4. 解析正文，剔除脚本、导航、广告、评论等噪声；
5. 将正文以 .txt 落盘。
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup, Comment, Tag

import config


class CrawlError(Exception):
    """爬取阶段的业务异常，code 用于前端识别错误类型。"""

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


@dataclass
class CrawlResult:
    """一次成功爬取的结果。"""

    url: str
    final_url: str
    title: str
    text: str
    html_bytes: int
    charset: str
    text_file: str
    elapsed: float
    truncated: bool = False


ProgressCb = Optional[Callable[[int, str], None]]

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([\w\-]+)", re.I)
_META_CHARSET_RE = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?([\w\-]+)", re.I)

# 直接删除的噪声标签
_NOISE_TAGS = {
    "script", "style", "noscript", "iframe", "svg", "canvas", "form",
    "input", "button", "select", "textarea", "nav", "header", "footer",
    "aside", "video", "audio", "object", "embed", "template", "dialog",
    "link", "meta",
}

# 命中这些 class / id 片段的容器视为噪声
_NOISE_TOKENS = {
    "ad", "ads", "adv", "advert", "advertisement", "banner", "popup", "modal",
    "overlay", "share", "social", "comment", "comments", "related",
    "recommend", "recommended", "sidebar", "breadcrumb", "copyright",
    "subscribe", "newsletter", "cookie", "qrcode", "footer", "nav", "navbar",
    "menu", "pagination", "pager", "login", "signup", "toolbar", "promo",
    "sponsor", "download", "guide", "float", "fixed", "bottom", "bottomdiv",
    "friendlink", "links", "linklist", "sitelink", "aboutus", "contactus",
    "feedback", "statement", "disclaimer",
}

_CONTENT_TOKENS = re.compile(r"article|content|post|entry|detail|body|text|main|story", re.I)
_NEGATIVE_TOKENS = re.compile(r"comment|list|side|nav|footer|header|menu|recommend|related", re.I)

# 页面尾部常见模板文字
_BOILERPLATE_RES = [
    re.compile(r"^(责任编辑|编辑|来源|声明|版权声明|免责声明|版权所有|未经授权|转载请注明)"),
    re.compile(r"(扫码|扫一扫|二维码|点击查看|阅读原文|关注我们|分享到|相关阅读|热门推荐)"),
    re.compile(r"^(上一篇|下一篇|返回首页|返回顶部|登录|注册|搜索|首页)"),
    re.compile(r"^(广告|推广|赞助|举报|纠错|收藏|点赞|评论)\b"),
    re.compile(r"^(友情链接|合作伙伴|关于我们|联系我们|广告服务|网站地图|招聘信息|服务条款|隐私政策)"),
    re.compile(r"(ICP备|公网安备|增值电信业务经营许可证|互联网新闻信息服务许可证|营业执照|违法和不良信息举报)"),
    re.compile(r"^(查看更多|更多>>|下载客户端|客户端下载)"),
    re.compile(r"^(Copyright|All rights reserved|Powered by|©)", re.I),
    re.compile(r"^https?://\S+$", re.I),
]


# --------------------------------------------------------------------------
# URL 处理
# --------------------------------------------------------------------------
def normalize_url(raw: str) -> str:
    """规范化用户输入的网址，并做基础安全校验。"""
    if not raw or not raw.strip():
        raise CrawlError("INVALID_URL", "请输入需要抓取的网页地址。")

    url = raw.strip()
    if url.startswith("//"):
        url = "https:" + url
    if not _SCHEME_RE.match(url):
        url = "https://" + url

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise CrawlError("INVALID_URL", "仅支持以 http:// 或 https:// 开头的网页地址。")

    host = parts.hostname
    if not host:
        raise CrawlError("INVALID_URL", "网址格式不正确，缺少域名部分。")
    if parts.username or parts.password:
        raise CrawlError("INVALID_URL", "网址中不支持携带用户名或密码。")

    try:
        port = parts.port
    except ValueError as exc:
        raise CrawlError("INVALID_URL", "网址中的端口号不合法。") from exc

    if host != "localhost" and "." not in host:
        raise CrawlError("INVALID_URL", "网址格式不正确，请填写完整域名，例如 https://www.example.com。")
    if " " in host or not re.match(r"^[A-Za-z0-9\u4e00-\u9fff.\-]+$", host):
        raise CrawlError("INVALID_URL", "域名中包含无法识别的字符，请检查网址。")

    if host.isascii():
        ascii_host = host
    else:
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise CrawlError("INVALID_URL", "域名包含无法识别的字符，请检查网址。") from exc

    if not config.ALLOW_PRIVATE_HOSTS:
        _ensure_public_host(ascii_host)

    netloc = ascii_host if port is None else f"{ascii_host}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _ensure_public_host(host: str) -> None:
    """拒绝仅解析到内网 / 环回 / 保留地址的域名，降低 SSRF 风险。

    部分网络环境下 DNS 会同时返回公网地址与异常地址（如 DNS 污染），
    只要存在可用的公网地址就放行，由后续连接阶段决定成败。
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise CrawlError(
            "INVALID_URL", f"无法解析域名 {host}，请检查网址是否正确或网络是否可用。"
        ) from exc

    has_public = False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            has_public = True
            break

    if not has_public:
        raise CrawlError(
            "PRIVATE_HOST", "出于安全考虑，暂不支持抓取内网、本机或保留地址。"
        )


# --------------------------------------------------------------------------
# robots.txt 与限速
# --------------------------------------------------------------------------
class RobotsChecker:
    """带缓存的 robots.txt 检查器。"""

    def __init__(self, timeout: float = 6.0) -> None:
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._timeout = timeout

    def check(self, url: str) -> tuple[bool, float, str]:
        """返回 (是否允许, Crawl-delay 秒, 说明)。"""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        entry = self._get(origin)
        parser: RobotFileParser | None = entry["parser"]
        if parser is None:
            return True, 0.0, entry["reason"]

        allowed = parser.can_fetch(config.ROBOTS_USER_AGENT, url)
        delay = parser.crawl_delay(config.ROBOTS_USER_AGENT) or parser.crawl_delay("*") or 0
        return allowed, float(delay), entry["reason"]

    def _get(self, origin: str) -> dict:
        with self._lock:
            cached = self._cache.get(origin)
            if cached and time.time() - cached["time"] < 600:
                return cached

        entry = self._load(origin)
        entry["time"] = time.time()
        with self._lock:
            self._cache[origin] = entry
        return entry

    def _load(self, origin: str) -> dict:
        robots_url = origin + "/robots.txt"
        try:
            resp = requests.get(
                robots_url,
                headers={"User-Agent": config.USER_AGENT},
                timeout=self._timeout,
            )
        except requests.RequestException:
            # 无法获取 robots.txt 时按惯例视为允许
            return {"parser": None, "reason": "robots.txt 获取失败，按允许处理"}

        if resp.status_code in (401, 403):
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(["User-agent: *", "Disallow: /"])
            return {"parser": parser, "reason": "robots.txt 拒绝访问，禁止抓取"}

        if resp.status_code >= 400:
            return {"parser": None, "reason": "robots.txt 不存在，按允许处理"}

        content = resp.text[:512 * 1024]
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(content.splitlines())
        return {"parser": parser, "reason": "已读取 robots.txt"}


class HostRateLimiter:
    """按域名维护最小请求间隔。"""

    def __init__(self, default_interval: float) -> None:
        self._default = max(0.0, default_interval)
        self._next_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str, extra_delay: float = 0.0) -> float:
        interval = max(self._default, extra_delay or 0.0)
        with self._lock:
            now = time.monotonic()
            next_at = self._next_at.get(host, 0.0)
            delay = max(0.0, next_at - now)
            self._next_at[host] = max(now, next_at) + interval
        if delay > 0:
            time.sleep(min(delay, 10.0))
        return delay


ROBOTS = RobotsChecker()
LIMITER = HostRateLimiter(config.MIN_REQUEST_INTERVAL)


# --------------------------------------------------------------------------
# 抓取
# --------------------------------------------------------------------------
def _detect_encoding(resp: requests.Response, raw: bytes) -> str:
    declared = ""
    match = _CHARSET_RE.search(resp.headers.get("Content-Type", ""))
    if match:
        declared = match.group(1)
    if not declared:
        match2 = _META_CHARSET_RE.search(raw[:4096])
        if match2:
            declared = match2.group(1).decode("ascii", "ignore")

    candidates: list[str] = []
    if declared:
        name = declared.lower()
        if name in ("gb2312", "gbk", "gb18030"):
            name = "gb18030"
        candidates.append(name)
    candidates.extend(["utf-8", "gb18030", "big5"])

    for encoding in candidates:
        try:
            raw.decode(encoding)
            return encoding
        except (LookupError, UnicodeDecodeError):
            continue
    return "utf-8"


def _http_error(status: int) -> CrawlError:
    if status in (401, 403):
        return CrawlError(
            "HTTP_ERROR",
            f"目标网站拒绝访问（HTTP {status}），可能启用了反爬虫限制。",
            f"status={status}",
        )
    if status in (404, 410):
        return CrawlError(
            "HTTP_ERROR", f"页面不存在（HTTP {status}），请检查网址是否正确。"
        )
    if status == 429:
        return CrawlError(
            "HTTP_ERROR", "目标网站提示请求过于频繁（HTTP 429），请稍后再试。"
        )
    if 500 <= status < 600:
        return CrawlError(
            "HTTP_ERROR", f"目标网站服务器异常（HTTP {status}），请稍后重试。"
        )
    return CrawlError("HTTP_ERROR", f"抓取失败，目标网站返回 HTTP {status}。")


def _fetch_once(url: str, robots: RobotsChecker, limiter: HostRateLimiter):
    allowed, crawl_delay, _ = robots.check(url)
    if not allowed:
        raise CrawlError(
            "ROBOTS_DENIED",
            "目标网站的 robots.txt 禁止抓取该页面，已自动停止。",
            url,
        )
    limiter.wait(urlsplit(url).netloc, crawl_delay)

    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": config.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            },
            timeout=config.REQUEST_TIMEOUT,
            stream=True,
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise CrawlError("FETCH_TIMEOUT", "抓取超时，目标网站响应过慢，请稍后重试。") from exc
    except requests.exceptions.SSLError as exc:
        raise CrawlError("FETCH_FAILED", "目标网站的 HTTPS 证书校验失败，无法安全访问。") from exc
    except requests.ConnectionError as exc:
        raise CrawlError("FETCH_FAILED", "无法连接到目标网站，请确认网址是否可以正常访问。") from exc
    except requests.RequestException as exc:
        raise CrawlError("FETCH_FAILED", "抓取网页失败，请稍后重试。", str(exc)) from exc

    return resp


def fetch_html(
    url: str,
    robots: RobotsChecker | None = None,
    limiter: HostRateLimiter | None = None,
) -> tuple[str, str, str, int, bool]:
    """抓取网页，返回 (html, 最终地址, 编码, 字节数, 是否截断)。

    手动处理重定向，每一跳都重新校验 robots.txt 与地址安全性。
    """
    robots = robots or ROBOTS
    limiter = limiter or LIMITER

    current = url
    for _ in range(config.MAX_REDIRECTS + 1):
        resp = _fetch_once(current, robots, limiter)
        try:
            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    raise CrawlError("FETCH_FAILED", "目标页面重定向缺少跳转地址，无法继续抓取。")
                nxt = normalize_url(urljoin(resp.url, location))
                if urlsplit(nxt).scheme not in ("http", "https"):
                    raise CrawlError("FETCH_FAILED", "目标页面重定向到了不支持的地址。")
                current = nxt
                continue

            if resp.status_code >= 400:
                raise _http_error(resp.status_code)

            content_type = (resp.headers.get("Content-Type") or "").lower()
            if content_type and not re.search(
                r"text/html|application/xhtml|text/plain|application/xml|text/xml",
                content_type,
            ):
                mime = content_type.split(";")[0].strip()
                raise CrawlError(
                    "UNSUPPORTED_CONTENT",
                    f"该地址返回的不是网页内容（{mime}），无法提取正文。",
                )

            chunks: list[bytes] = []
            total = 0
            truncated = False
            for chunk in resp.iter_content(65536):
                if not chunk:
                    continue
                remain = config.MAX_PAGE_BYTES - total
                if len(chunk) > remain:
                    chunks.append(chunk[:remain])
                    total += remain
                    truncated = True
                    break
                chunks.append(chunk)
                total += len(chunk)

            raw = b"".join(chunks)
            if not raw:
                raise CrawlError("EMPTY_CONTENT", "目标页面没有返回任何内容。")

            encoding = _detect_encoding(resp, raw)
            html = raw.decode(encoding, errors="replace")
            final_url = normalize_url(resp.url)
            return html, final_url, encoding, total, truncated
        finally:
            resp.close()

    raise CrawlError("FETCH_FAILED", "页面重定向次数过多，无法访问目标页面。")


# --------------------------------------------------------------------------
# 正文提取
# --------------------------------------------------------------------------
def _link_density(node: Tag) -> float:
    total = len(node.get_text(" ", strip=True))
    if total == 0:
        return 1.0
    link_len = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
    return link_len / total


def _is_noise_node(node: Tag) -> bool:
    if not isinstance(node, Tag):
        return False
    if node.name in ("html", "body"):
        return False
    if node.name in _NOISE_TAGS:
        return True
    if node.get("aria-hidden") == "true":
        return True
    ident = " ".join(
        filter(
            None,
            [
                node.get("id") or "",
                " ".join(node.get("class") or []),
            ],
        )
    ).lower()
    if not ident:
        return False
    tokens = {token for token in re.split(r"[^a-z0-9]+", ident) if token}
    return bool(tokens & _NOISE_TOKENS)


def _prune_noise(soup: BeautifulSoup) -> None:
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    for node in soup.find_all(True):
        if node.parent is None:
            continue
        if not _is_noise_node(node):
            continue
        # 大段且链接很少的节点大概率是正文，宁可保留
        text_len = len(node.get_text(" ", strip=True))
        if text_len > 800 and _link_density(node) < 0.25:
            continue
        node.decompose()


def _candidate_score(node: Tag) -> float:
    text = node.get_text(" ", strip=True)
    length = len(text)
    if length < 50:
        return 0.0
    density = _link_density(node)
    if density > 0.6:
        return 0.0

    score = length * (1.0 - density)
    if node.name in ("article", "main"):
        score += 800
    ident = " ".join(filter(None, [node.get("id") or "", " ".join(node.get("class") or [])]))
    if ident:
        if _CONTENT_TOKENS.search(ident):
            score += 400
        if _NEGATIVE_TOKENS.search(ident):
            score -= 500
    return score


def _pick_main_node(soup: BeautifulSoup) -> Tag:
    body = soup.body or soup
    best: Tag | None = None
    best_score = 0.0
    for node in soup.find_all(["article", "main", "div", "section"]):
        score = _candidate_score(node)
        if score > best_score:
            best = node
            best_score = score

    if best is None:
        return body

    # 候选区域过短时，回退到整页正文（门户首页等链接密集页面）
    best_len = len(best.get_text(" ", strip=True))
    body_len = len(body.get_text(" ", strip=True))
    if best_len < 400 and body_len > max(best_len * 1.8, 600):
        return body
    return best


def _clean_lines(raw_text: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for line in raw_text.split("\n"):
        line = re.sub(r"[\t\r\u00a0\u3000 ]+", " ", line).strip()
        if len(line) < 2:
            continue
        if any(pattern.search(line) for pattern in _BOILERPLATE_RES):
            continue
        # 纯数字 / 标点占比过高的行（表格、编号）直接丢弃
        visible = re.sub(r"\s", "", line)
        if visible and sum(ch.isdigit() for ch in visible) / len(visible) > 0.7:
            continue
        # 形如 “A | B | C | D” 的友情链接 / 导航行
        if line.count("|") + line.count("｜") >= 4:
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)


def _extract_title(soup: BeautifulSoup) -> str:
    for attrs in (
        {"property": "og:title"},
        {"name": "twitter:title"},
        {"name": "title"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return tag["content"].strip()

    h1 = soup.find("h1")
    if h1:
        text = h1.get_text(" ", strip=True)
        if text:
            return text

    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        # 去掉 “文章标题 - 网站名” 这类后缀
        for sep in (" - ", " | ", " — ", " – ", "_"):
            if sep in title:
                head, tail = title.rsplit(sep, 1)
                if len(head) >= 6 and 0 < len(tail) <= 20:
                    title = head
                break
        return title
    return "未命名页面"


def extract_article(html: str, url: str = "") -> tuple[str, str]:
    """从 HTML 中提取标题与正文，返回 (title, text)。"""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    title = _extract_title(soup)
    _prune_noise(soup)

    main = _pick_main_node(soup)

    # 去掉正文内部链接密度过高的列表 / 推荐位。
    # 整页都是链接的门户首页除外，否则会只剩下页脚文字。
    link_heavy_page = main is soup.body and _link_density(main) > 0.6
    if not link_heavy_page:
        for block in main.find_all(["div", "li", "ul", "ol", "section", "table"]):
            if block is main or block.parent is None:
                continue
            text_len = len(block.get_text(" ", strip=True))
            if text_len >= 40 and _link_density(block) > 0.65:
                block.decompose()

    for br in main.find_all("br"):
        br.replace_with("\n")

    text = _clean_lines(main.get_text("\n", strip=True))
    return title, text[: config.MAX_TEXT_CHARS]


# --------------------------------------------------------------------------
# 本地存储
# --------------------------------------------------------------------------
def _safe_slug(text: str, limit: int = 40) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", text).strip("-")
    return cleaned[:limit] or "page"


def save_text(title: str, url: str, text: str) -> str:
    """把正文保存为 .txt，返回相对 DATA_DIR 的路径。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    host = urlsplit(url).netloc.replace(":", "_") or "site"
    digest = hashlib.md5((url + text[:200]).encode("utf-8")).hexdigest()[:8]
    filename = f"{stamp}_{_safe_slug(host, 24)}_{digest}.txt"
    path = config.PAGE_DIR / filename

    header = (
        f"# 标题: {title}\n"
        f"# 来源: {url}\n"
        f"# 抓取时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# 正文字数: {len(text)}\n"
        "# " + "-" * 40 + "\n"
    )
    path.write_text(header + text, encoding="utf-8")
    return str(path.relative_to(config.DATA_DIR)).replace("\\", "/")


def read_saved_text(relative_path: str) -> str:
    """读取本地保存的正文，跳过文件头注释行。"""
    path = config.DATA_DIR / relative_path
    content = path.read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if not line.startswith("#")]
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------
def crawl_page(url: str, progress: ProgressCb = None, persist_text: bool = True) -> CrawlResult:
    """执行完整爬取流程，失败时抛出 CrawlError。

    persist_text=False 时不落盘 .txt，正文由调用方写入数据库。
    """
    started = time.time()

    def report(percent: int, message: str) -> None:
        if progress:
            progress(percent, message)

    report(4, "校验网址格式")
    normalized = normalize_url(url)

    report(12, "检查 robots.txt 协议")
    allowed, _, _ = ROBOTS.check(normalized)
    if not allowed:
        raise CrawlError(
            "ROBOTS_DENIED",
            "目标网站的 robots.txt 禁止抓取该页面，已自动停止。",
            normalized,
        )

    report(24, "正在获取网页内容")
    html, final_url, charset, nbytes, truncated = fetch_html(normalized)

    report(46, "解析正文并过滤广告")
    title, text = extract_article(html, final_url)

    if len(text.strip()) < 20:
        raise CrawlError(
            "EMPTY_CONTENT",
            "未能从页面中提取到有效正文，该页面可能依赖 JavaScript 渲染，或内容是图片/视频。",
            final_url,
        )
    if len(text) < config.MIN_TEXT_CHARS:
        raise CrawlError(
            "CONTENT_TOO_SMALL",
            f"页面正文过少（仅 {len(text)} 字，至少需要 {config.MIN_TEXT_CHARS} 字），无法生成有意义的词云。",
            final_url,
        )

    report(58, "保存正文到本地")
    text_file = save_text(title, final_url, text) if persist_text else ""
    report(64, "正文已保存")

    return CrawlResult(
        url=normalized,
        final_url=final_url,
        title=title,
        text=text,
        html_bytes=nbytes,
        charset=charset,
        text_file=text_file,
        elapsed=round(time.time() - started, 2),
        truncated=truncated,
    )
