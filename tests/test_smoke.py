"""全流程功能测试。

覆盖：URL 校验、robots 协议、限速、正文提取与噪声过滤、
文件落盘、中文分词、自定义词、词云生成、Web API 全链路。

运行：
    python tests/test_smoke.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP_DIR = tempfile.mkdtemp(prefix="wcs_test_")
os.environ["WCS_DATA_DIR"] = _TMP_DIR
os.environ["WCS_ALLOW_PRIVATE"] = "1"
os.environ["WCS_MIN_INTERVAL"] = "0"

import analyzer  # noqa: E402
import app as web_app  # noqa: E402
import cloud_engine  # noqa: E402
import config  # noqa: E402
import crawler  # noqa: E402
import db  # noqa: E402
import history  # noqa: E402
from collections import Counter  # noqa: E402
from PIL import Image  # noqa: E402


ARTICLE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>人工智能与未来社会 - 测试站点</title>
  <script>var hiddenScriptText = "脚本文本不应出现";</script>
  <style>.hidden { display: none; }</style>
</head>
<body>
  <header class="site-header"><nav><a href="/">首页</a><a href="/news">新闻</a></nav></header>
  <div class="ad-banner">广告：立即购买超值课程</div>
  <main>
    <article class="article-content">
      <h1>人工智能与未来社会</h1>
      <p>人工智能正在深刻改变我们的生产与生活方式，机器学习算法在数据中心大规模运行。</p>
      <p>从自然语言处理到计算机视觉，深度学习模型不断刷新纪录，机器人技术也随之进步。</p>
      <p>研究者关注算法公平、数据安全与隐私保护，产业界则推动智能应用落地。</p>
      <p>未来社会需要技术创新与伦理规范并行，让智能技术真正服务于人。</p>
    </article>
    <div class="comment-list"><p>评论区内容不应该出现在词云中</p></div>
  </main>
  <aside class="sidebar"><p>侧边栏推荐阅读</p></aside>
  <footer><p>版权所有 © 测试站点</p></footer>
</body>
</html>
"""

SHORT_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>短页面</title></head>
<body><article><p>这是一个内容非常简短的页面，用来测试正文不足的告警逻辑。</p></article></body></html>
"""


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body, content_type="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/robots.txt":
            self._send(200, "User-agent: *\nDisallow: /private\nCrawl-delay: 0\n", "text/plain")
        elif self.path in ("/", "/article"):
            self._send(200, ARTICLE_HTML)
        elif self.path == "/short":
            self._send(200, SHORT_HTML)
        elif self.path == "/private/page":
            self._send(200, ARTICLE_HTML)
        elif self.path == "/binary":
            self._send(200, b"%PDF-1.4 fake pdf content", "application/pdf")
        else:
            self._send(404, "<html><body>not found</body></html>")


class WordCloudTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()


class CrawlerTests(WordCloudTestBase):
    def test_normalize_url(self):
        self.assertEqual(crawler.normalize_url("example.com"), "https://example.com/")
        self.assertEqual(
            crawler.normalize_url("http://example.com/a?b=1#frag"),
            "http://example.com/a?b=1",
        )
        with self.assertRaises(crawler.CrawlError) as ctx:
            crawler.normalize_url("ftp://example.com/file")
        self.assertEqual(ctx.exception.code, "INVALID_URL")

        with self.assertRaises(crawler.CrawlError):
            crawler.normalize_url("")

    def test_private_host_blocked(self):
        original = config.ALLOW_PRIVATE_HOSTS
        config.ALLOW_PRIVATE_HOSTS = False
        try:
            with self.assertRaises(crawler.CrawlError) as ctx:
                crawler.normalize_url(self.base + "/article")
            self.assertEqual(ctx.exception.code, "PRIVATE_HOST")
        finally:
            config.ALLOW_PRIVATE_HOSTS = original

    def test_rate_limiter_waits(self):
        limiter = crawler.HostRateLimiter(0.2)
        started = time.monotonic()
        limiter.wait("demo.test")
        limiter.wait("demo.test")
        self.assertGreaterEqual(time.monotonic() - started, 0.18)

    def test_robots_denied(self):
        with self.assertRaises(crawler.CrawlError) as ctx:
            crawler.crawl_page(self.base + "/private/page")
        self.assertEqual(ctx.exception.code, "ROBOTS_DENIED")

    def test_crawl_extract_and_save(self):
        result = crawler.crawl_page(self.base + "/article")
        self.assertEqual(result.title, "人工智能与未来社会")
        self.assertIn("机器学习算法", result.text)
        self.assertNotIn("广告：立即购买", result.text)
        self.assertNotIn("评论区内容", result.text)
        self.assertNotIn("脚本文本不应出现", result.text)
        self.assertNotIn("版权所有", result.text)

        saved = config.DATA_DIR / result.text_file
        self.assertTrue(saved.is_file())
        content = saved.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("# 标题:"))
        self.assertIn("人工智能", crawler.read_saved_text(result.text_file))

    def test_content_too_small(self):
        with self.assertRaises(crawler.CrawlError) as ctx:
            crawler.crawl_page(self.base + "/short")
        self.assertEqual(ctx.exception.code, "CONTENT_TOO_SMALL")

    def test_unsupported_content(self):
        with self.assertRaises(crawler.CrawlError) as ctx:
            crawler.crawl_page(self.base + "/binary")
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_CONTENT")


class AnalyzerTests(unittest.TestCase):
    TEXT = (
        "人工智能正在改变世界，人工智能推动机器学习与深度学习发展。"
        "机器学习算法依赖数据中心，算法优化提升模型能力。"
        "自然语言处理与计算机视觉是重要方向，机器人技术持续进步。"
    )

    def test_frequencies_and_stopwords(self):
        result = analyzer.analyze(self.TEXT, min_len=2, top_n=50)
        self.assertIn("人工智能", result.frequencies)
        self.assertGreaterEqual(result.frequencies["人工智能"], 2)
        self.assertNotIn("我们", result.frequencies)
        self.assertNotIn("的", result.frequencies)
        self.assertGreater(result.stats["unique"], 5)

    def test_custom_word_lifecycle(self):
        word = "生成式人工智能"
        try:
            analyzer.add_custom_word(word)
        except analyzer.AnalysisError as exc:
            if exc.code != "WORD_EXISTS":
                raise
        self.assertIn(word, analyzer.get_custom_words())

        result = analyzer.analyze(
            "生成式人工智能正在快速发展，生成式人工智能应用不断落地，"
            "知识图谱与向量检索成为重要方向，算法工程师持续优化模型。"
        )
        self.assertIn(word, result.frequencies)

        analyzer.remove_custom_word(word)
        self.assertNotIn(word, analyzer.get_custom_words())

    def test_insufficient_words(self):
        with self.assertRaises(analyzer.AnalysisError) as ctx:
            analyzer.analyze("的了呢吗，。！")
        self.assertEqual(ctx.exception.code, "INSUFFICIENT_WORDS")


class CloudTests(unittest.TestCase):
    FREQ = Counter(
        {
            "人工智能": 40,
            "机器学习": 32,
            "大模型": 28,
            "数据中心": 24,
            "算法": 22,
            "芯片": 20,
            "机器人": 18,
            "自然语言": 16,
            "推理": 14,
            "训练": 12,
            "神经网络": 11,
            "云计算": 10,
            "开源": 9,
            "安全": 8,
            "应用": 7,
            "创新": 6,
        }
    )

    def test_shapes_and_sizes(self):
        for shape in ("rectangle", "circle", "heart", "star", "cloud", "diamond"):
            result = cloud_engine.generate(
                self.FREQ,
                {
                    "shape": shape,
                    "palette": "ocean",
                    "font": config.DEFAULT_FONT,
                    "background": "white",
                    "width": 720,
                    "height": 560,
                    "max_words": 16,
                },
            )
            path = config.DATA_DIR / result.image_file
            self.assertTrue(path.is_file())
            with Image.open(path) as image:
                self.assertEqual(image.size, (720, 560))
            self.assertGreater(result.used_words, 0)

    def test_transparent_background(self):
        result = cloud_engine.generate(
            self.FREQ,
            {
                "shape": "heart",
                "palette": "morandi",
                "font": config.DEFAULT_FONT,
                "background": "transparent",
                "width": 640,
                "height": 640,
                "max_words": 12,
            },
        )
        with Image.open(config.DATA_DIR / result.image_file) as image:
            self.assertEqual(image.mode, "RGBA")

    def test_insufficient_frequencies(self):
        with self.assertRaises(cloud_engine.CloudError) as ctx:
            cloud_engine.generate(Counter({"人工智能": 3, "算法": 2}))
        self.assertEqual(ctx.exception.code, "INSUFFICIENT_WORDS")


class ApiTests(WordCloudTestBase):
    def setUp(self):
        self.client = web_app.app.test_client()
        # 接口已加登录保护，测试前先登录
        account, password = next(iter(config.AUTH_ACCOUNTS.items()))
        login = self.client.post(
            "/api/login", json={"account": account, "password": password}
        )
        self.assertEqual(login.status_code, 200)

    def test_full_generation_flow(self):
        response = self.client.post(
            "/api/tasks",
            json={"url": self.base + "/article", "options": {"shape": "circle", "max_words": 30}},
        )
        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        task_id = payload["task"]["id"]

        task = None
        deadline = time.time() + 60
        while time.time() < deadline:
            status = self.client.get(f"/api/tasks/{task_id}").get_json()["task"]
            if status["status"] in ("success", "error"):
                task = status
                break
            time.sleep(0.25)

        self.assertIsNotNone(task, "任务在 60 秒内未结束")
        self.assertEqual(task["status"], "success", task.get("error"))
        self.assertEqual(task["progress"], 100)
        self.assertTrue(task["logs"])

        result = task["result"]
        self.assertIn("人工智能", result["title"])
        self.assertGreater(result["stats"]["unique"], 5)

        preview = self.client.get(result["preview_url"])
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.mimetype, "image/png")
        self.assertTrue(preview.data.startswith(b"\x89PNG"))
        preview.close()

        image_download = self.client.get(result["image_download_url"])
        self.assertEqual(image_download.status_code, 200)
        self.assertIn("attachment", image_download.headers.get("Content-Disposition", ""))
        image_download.close()

        text_download = self.client.get(result["text_download_url"])
        self.assertEqual(text_download.status_code, 200)
        self.assertIn("人工智能", text_download.data.decode("utf-8"))
        text_download.close()

        records = self.client.get("/api/history").get_json()["records"]
        self.assertTrue(any(item["id"] == task_id for item in records))

        delete = self.client.delete(f"/api/history/{task_id}")
        self.assertEqual(delete.status_code, 200)
        after_delete = self.client.get(result["preview_url"])
        self.assertEqual(after_delete.status_code, 404)
        after_delete.close()

    def test_invalid_url(self):
        response = self.client.post("/api/tasks", json={"url": "ftp://example.com/a"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "INVALID_URL")

    def test_custom_words_api(self):
        response = self.client.post("/api/custom-words", json={"word": "知识图谱"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("知识图谱", response.get_json()["words"])

        listing = self.client.get("/api/custom-words").get_json()["words"]
        self.assertIn("知识图谱", listing)

        removed = self.client.delete("/api/custom-words/%E7%9F%A5%E8%AF%86%E5%9B%BE%E8%B0%B1")
        self.assertEqual(removed.status_code, 200)
        self.assertNotIn("知识图谱", removed.get_json()["words"])

    def test_task_progress_endpoint(self):
        response = self.client.get("/api/tasks/not-exists")
        self.assertEqual(response.status_code, 404)

    def test_index_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("词云工坊", response.get_data(as_text=True))


class AuthTests(WordCloudTestBase):
    def setUp(self):
        self.client = web_app.app.test_client()

    def test_login_page_accessible(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("欢迎回来", response.get_data(as_text=True))

    def test_index_requires_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_api_requires_login(self):
        response = self.client.get("/api/history")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "UNAUTHORIZED")

    def test_login_failure(self):
        response = self.client.post(
            "/api/login", json={"account": "admin", "password": "wrong-password"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "LOGIN_FAILED")

    def test_login_empty_fields(self):
        response = self.client.post("/api/login", json={"account": "", "password": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "INVALID_CREDENTIALS")

    def test_login_success_and_logout(self):
        account, password = next(iter(config.AUTH_ACCOUNTS.items()))

        login = self.client.post(
            "/api/login", json={"account": account, "password": password}
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.get_json()["user"], account)

        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("词云工坊", page.get_data(as_text=True))

        logout = self.client.post("/api/logout")
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 302)


class DatabaseTests(WordCloudTestBase):
    """数据库层 CRUD、分页、批量写入与事务回滚。"""

    def _make(self, url="https://example.com/a", title="测试页面", content=None):
        content = content or (
            "数据可视化帮助人们理解数据规律，图表与色彩是常见表达方式，数据分析与商业智能广泛应用。"
        )
        return db.create_page(
            url=url,
            title=title,
            content=content,
            words={"数据": 5, "可视化": 3, "图表": 2},
        )

    def test_create_and_read(self):
        page_id = self._make(title="唯一标题-A")
        page = db.get_page(page_id)
        self.assertIsNotNone(page)
        self.assertEqual(page["title"], "唯一标题-A")
        self.assertEqual(page["host"], "example.com")
        self.assertEqual(page["status"], db.STATUS_ACTIVE)
        self.assertGreater(page["char_count"], 10)
        self.assertTrue(page["content"])

    def test_word_stats_roundtrip(self):
        page_id = self._make(title="分词读写")
        frequencies = db.get_word_frequencies(page_id)
        self.assertEqual(frequencies["数据"], 5)
        self.assertEqual(frequencies["可视化"], 3)
        words = db.get_word_stats(page_id)
        self.assertEqual(words[0]["word"], "数据")

    def test_pagination_and_search(self):
        for index in range(3):
            self._make(url=f"https://demo{index}.com/p", title=f"分页测试-{index}")
        result = db.list_pages(page=1, size=2, keyword="分页测试", include_deleted=True)
        self.assertGreaterEqual(result["total"], 3)
        self.assertEqual(result["size"], 2)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["pages"], (result["total"] + 1) // 2)

        by_host = db.list_pages(host="demo1.com", include_deleted=True)
        self.assertEqual(by_host["total"], 1)

    def test_update_content_recomputes_words(self):
        page_id = self._make(title="更新前")
        updated = db.update_page(page_id, title="更新后", words={"新词": 9})
        self.assertEqual(updated["title"], "更新后")
        self.assertEqual(db.get_word_frequencies(page_id)["新词"], 9)

        # 只改正文时自动重新分词
        db.update_page(page_id, content="人工智能推动计算机视觉与自然语言处理不断进步，机器学习算法广泛应用。")
        frequencies = db.get_word_frequencies(page_id)
        self.assertTrue(len(frequencies) > 0)
        self.assertNotIn("新词", frequencies)

    def test_logical_and_physical_delete(self):
        page_id = self._make(title="待删除")
        self.assertTrue(db.delete_page(page_id, hard=False))
        self.assertEqual(db.get_page(page_id)["status"], db.STATUS_DELETED)
        self.assertFalse(db.delete_page(page_id, hard=False))

        self.assertTrue(db.restore_page(page_id))
        self.assertEqual(db.get_page(page_id)["status"], db.STATUS_ACTIVE)

        self.assertTrue(db.delete_page(page_id, hard=True))
        self.assertIsNone(db.get_page(page_id))

    def test_cascade_delete_words(self):
        page_id = self._make(title="级联删除")
        self.assertGreater(len(db.get_word_stats(page_id)), 0)
        db.delete_page(page_id, hard=True)
        self.assertEqual(len(db.get_word_stats(page_id)), 0)

    def test_batch_insert_transaction_rollback(self):
        before = db.count_pages()
        with self.assertRaises(db.DatabaseError):
            db.create_pages(
                [
                    {"url": "https://ok.com/1", "content": "这是一条合法正文内容，用于验证事务回滚能力。"},
                    {"url": "https://ok.com/2", "content": ""},  # 非法：正文为空
                ]
            )
        self.assertEqual(db.count_pages(), before, "批量写入失败时应整批回滚")

    def test_stats_overview(self):
        self._make(url="https://stats-demo.com/x", title="统计测试")
        stats = db.stats_overview()
        self.assertGreaterEqual(stats["total"], 1)
        self.assertIsInstance(stats["hosts"], list)
        self.assertIsInstance(stats["top_words"], list)
        self.assertEqual(len(stats["daily"]), 7)


class AdminApiTests(WordCloudTestBase):
    """管理后台接口：列表、分页、增删改查、CORS。"""

    def setUp(self):
        self.client = web_app.app.test_client()
        account, password = next(iter(config.AUTH_ACCOUNTS.items()))
        self.client.post("/api/login", json={"account": account, "password": password})

    def _create(self, title="后台新增"):
        response = self.client.post(
            "/api/admin/pages",
            json={
                "url": "https://admin-demo.com/news",
                "title": title,
                "content": "后台新增的测试正文，包含数据、图表与可视化等关键词，用于验证分词与入库流程。",
            },
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["page"]["id"]

    def test_stats_endpoint(self):
        data = self.client.get("/api/admin/stats").get_json()
        self.assertTrue(data["ok"])
        self.assertIn("total", data["stats"])
        self.assertIn("hosts", data["stats"])
        self.assertIn("top_words", data["stats"])

    def test_cors_headers(self):
        response = self.client.get("/api/admin/stats", headers={"Origin": "http://localhost:3000"})
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "http://localhost:3000")
        self.assertIn("GET", response.headers.get("Access-Control-Allow-Methods", ""))

        preflight = self.client.options(
            "/api/admin/pages",
            headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
        )
        self.assertEqual(preflight.status_code, 204)
        self.assertEqual(
            preflight.headers.get("Access-Control-Allow-Origin"), "http://localhost:3000"
        )

    def test_create_read_update_delete(self):
        page_id = self._create(title="接口新增-1")

        detail = self.client.get(f"/api/admin/pages/{page_id}").get_json()["page"]
        self.assertEqual(detail["title"], "接口新增-1")
        self.assertTrue(detail["content"])
        self.assertTrue(detail["words"])

        updated = self.client.put(
            f"/api/admin/pages/{page_id}", json={"title": "接口改名", "status": "archived"}
        ).get_json()["page"]
        self.assertEqual(updated["title"], "接口改名")
        self.assertEqual(updated["status"], "archived")

        listing = self.client.get("/api/admin/pages?q=接口改名").get_json()
        self.assertEqual(listing["total"], 1)

        self.assertEqual(self.client.delete(f"/api/admin/pages/{page_id}").status_code, 200)
        self.assertEqual(
            self.client.get(f"/api/admin/pages/{page_id}").get_json()["page"]["status"], "deleted"
        )

        restored = self.client.post(f"/api/admin/pages/{page_id}/restore").get_json()
        self.assertEqual(restored["page"]["status"], "active")

        self.assertEqual(
            self.client.delete(f"/api/admin/pages/{page_id}?hard=1").status_code, 200
        )
        self.assertEqual(self.client.get(f"/api/admin/pages/{page_id}").status_code, 404)

    def test_batch_create_and_delete(self):
        payload = [
            {
                "url": f"https://batch-demo.com/{index}",
                "title": f"批量-{index}",
                "content": "批量新增的测试正文内容，包含数据与可视化，用于验证单事务批量写入。",
            }
            for index in range(2)
        ]
        created = self.client.post("/api/admin/pages/batch", json={"items": payload}).get_json()
        self.assertEqual(created["created"], 2)
        self.assertEqual(len(created["ids"]), 2)

        removed = self.client.delete(
            "/api/admin/pages", json={"ids": created["ids"], "hard": True}
        ).get_json()
        self.assertEqual(removed["deleted"], 2)

    def test_reanalyze(self):
        page_id = self._create(title="重新分词")
        data = self.client.post(f"/api/admin/pages/{page_id}/analyze").get_json()
        self.assertGreater(data["words"], 0)

    def test_invalid_payload(self):
        response = self.client.post(
            "/api/admin/pages", json={"url": "ftp://bad", "content": "内容足够长的测试正文内容。"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "INVALID_URL")

        short = self.client.post(
            "/api/admin/pages", json={"url": "https://a.com", "content": "太短"}
        )
        self.assertEqual(short.status_code, 400)
        self.assertEqual(short.get_json()["error"]["code"], "CONTENT_TOO_SMALL")

    def test_admin_page_renders(self):
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn("数据管理后台", response.get_data(as_text=True))


def tearDownModule():
    analyzer.remove_custom_word("知识图谱") if "知识图谱" in analyzer.get_custom_words() else None
    db.reset_connection()
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
