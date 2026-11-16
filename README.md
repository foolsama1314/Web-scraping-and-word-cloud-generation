# 词云工坊 CloudLet Studio

一个功能完整的网页词云生成网站：输入任意公开网页地址，自动爬取正文、过滤广告与导航、完成中文分词，生成可下载的词云图片。爬取正文与分词结果统一存入本地 SQLite 数据库，并提供数据管理后台。

## 功能特性

- **网页爬虫**：URL 校验、robots.txt 合规检查、同站请求间隔限速、重定向逐跳校验、超时与体积上限、编码自动识别（UTF-8 / GBK / Big5）
- **正文解析**：基于文本密度与链接密度评分定位正文，自动剔除脚本、样式、导航、侧栏、评论、广告、页脚等噪声
- **中文分词**：jieba 分词 + 700+ 停用词过滤 + 词频统计，支持自定义词汇（持久化到本地词库）
- **词云引擎**：6 种形状（矩形 / 圆形 / 心形 / 五角星 / 云朵 / 菱形）、12 套配色（渐变、莫兰迪、多套 colormap）、5 种中文字体、自定义画布尺寸、最大词数、最短词长、背景色与透明背景
- **Web 界面**：参数面板、实时进度条与任务日志、结果预览、一键下载 PNG / 正文 TXT、历史记录复用与删除
- **异常处理**：无效网址、内网地址、robots 拒绝、超时、HTTP 错误、非网页内容、正文为空、正文过少、有效词汇不足、渲染失败等均有明确提示
- **SQLite 数据持久化**：爬取 → 数据库存储 → 数据库读取 → 词云生成的全链路，分词结果结构化存储，支持条件查询、分页、更新与逻辑 / 物理删除
- **数据管理后台**：数据列表（分页 / 搜索 / 来源与状态筛选）、单条编辑、新增、逻辑与物理删除、批量删除、重新分词，以及数据量、来源占比、分词频次、近 7 天抓取趋势看板

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.10+、Flask |
| 数据库 | Python 内置 sqlite3（WAL 模式、事务、批量写入） |
| 爬虫 | requests、BeautifulSoup4、lxml |
| 分词 | jieba |
| 词云 | wordcloud、Pillow、numpy |
| 前端 | 原生 HTML / CSS / JavaScript（无外部 CDN 依赖） |

## 目录结构

```
wordcloud_studio/
├── app.py                  # Flask 应用入口、页面路由、词云任务 API、登录鉴权、CORS
├── admin_api.py            # 数据管理后台 API（增删改查、分页、统计）
├── db.py                   # SQLite 数据访问层（建表、CRUD、事务、批量写入）
├── init_db.py              # 数据库初始化脚本
├── config.py               # 路径、字体、爬虫策略等配置
├── crawler.py              # 爬虫：robots、限速、抓取、正文提取
├── analyzer.py             # 中文分词、停用词、自定义词、词频统计
├── cloud_engine.py         # 形状蒙版、配色、词云渲染
├── task_manager.py         # 后台任务：爬取 → 入库 → 读库 → 渲染
├── history.py              # 历史记录与图片资源管理
├── stopwords.txt           # 内置停用词表
├── requirements.txt
├── templates/index.html    # 词云生成界面
├── templates/login.html    # 登录页
├── templates/admin.html    # 数据管理后台
├── static/css/style.css
├── static/js/app.js
├── static/js/editor.js
├── data/                   # 运行时生成
│   ├── wordcloud.db        # SQLite 数据库（pages / word_stats）
│   ├── clouds/             # 词云 PNG
│   ├── layouts/            # 编辑后的词云布局
│   ├── history.json
│   ├── custom_words.txt
│   └── custom_stopwords.txt
└── tests/test_smoke.py     # 全流程测试
```
## 环境要求

- Python 3.10 及以上（已在 Python 3.12 验证）
- 可访问目标网页的网络
- 一个中文字体文件（Windows 自带微软雅黑、黑体、楷体等，通常无需额外配置）

## 快速开始

```bash
# 1. 进入项目目录
cd wordcloud_studio

# 2. 创建虚拟环境（推荐）
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 初始化数据库（幂等，可重复执行）
python init_db.py            # 仅建表；加 --seed 写入示例数据
# 5. 启动服务
python app.py
```

打开 <http://127.0.0.1:5000>，先用演示账号 `admin / 123456` 登录，再输入网址生成词云；管理后台地址为 <http://127.0.0.1:5000/admin>。

> 本机若无法直接使用 `python` 命令，请换成 Python 安装目录下的完整路径，或把 Python 加入 PATH。

## 字体配置

程序启动时会自动探测以下字体：

| 平台 | 字体 |
| --- | --- |
| Windows | `msyh.ttc`（微软雅黑）、`simhei.ttf`（黑体）、`simkai.ttf`（楷体）、`Deng.ttf`（等线）、`simsun.ttc`（宋体） |
| Linux | Noto Sans CJK、文泉驿正黑 |
| macOS | PingFang |

如需指定其他字体，设置环境变量：

```bash
# Windows PowerShell
$env:WCS_FONT_PATH = "D:\fonts\SourceHanSansSC-Regular.otf"
# Linux / macOS
export WCS_FONT_PATH=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
```

字体必须包含中文字形，否则词云中文字会显示为方框或生成失败。

## 配置项

所有配置均可通过环境变量覆盖：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WCS_HOST` | `127.0.0.1` | 监听地址，局域网访问可设为 `0.0.0.0` |
| `WCS_PORT` | `5000` | 监听端口 |
| `WCS_DEBUG` | `0` | 设为 `1` 开启 Flask 调试 |
| `WCS_MIN_INTERVAL` | `1.5` | 同站两次请求的最小间隔（秒） |
| `WCS_ALLOW_PRIVATE` | `0` | 设为 `1` 时允许抓取本机 / 内网地址（仅用于本地测试） |
| `WCS_DATA_DIR` | `./data` | 数据目录（数据库、图片、历史、自定义词库） |
| `WCS_FONT_PATH` | 空 | 额外指定默认中文字体文件 |
| `WCS_SECRET_KEY` | 内置开发密钥 | Flask session 签名密钥，生产环境务必修改 |
| `WCS_AUTH_ACCOUNTS` | `admin:123456` | 登录账号，格式 `账号:密码,账号:密码` |

示例：

```bash
# Windows PowerShell
$env:WCS_MIN_INTERVAL = "3"; python app.py

# Linux
WCS_HOST=0.0.0.0 WCS_PORT=8000 python app.py
```

## 使用说明

0. 打开首页会先跳转到 `/login` 登录页，默认演示账号 `admin` / `123456`（密码直接展示在登录页底部），登录成功后进入词云工作台，右上角可退出登录。
1. 在“目标网址”中粘贴一个公开可访问的网页地址，可省略 `https://`。
2. 选择形状、配色、字体、画布尺寸、背景、最大词数与最短词长。
3. 如有专业术语被切碎，在“自定义词汇”中添加（例如“大语言模型”）。
4. 点击“开始生成词云”，右侧会显示抓取、分词、渲染各阶段的实时进度与日志。
5. 生成完成后可下载 PNG 图片或正文 TXT（正文从数据库读取）；结果会自动进入历史记录。
6. 历史记录支持下载、复用参数与删除（删除时会同步清理本地图片）。
7. 登录页点击「进入数据管理后台」或顶栏「管理后台」，可查看与维护所有爬取数据。

## 数据库设计

使用 Python 内置 `sqlite3`，数据库文件为 `data/wordcloud.db`（WAL 模式）。所有 SQL 均封装在 `db.py` 内，业务代码只调用 Python 方法。

**pages（爬取页面）**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | 自增主键 |
| `task_id` | TEXT UNIQUE | 对应生成任务 ID，便于与历史记录关联 |
| `url` / `final_url` | TEXT | 原始地址与重定向后的最终地址 |
| `host` | TEXT | 来源域名（建索引，用于来源占比统计） |
| `title` | TEXT | 页面标题 |
| `content` | TEXT | 正文全文 |
| `content_hash` | TEXT | 正文 SHA-256 摘要，用于去重与变更检测 |
| `charset` / `html_bytes` | TEXT / INTEGER | 编码与原始 HTML 体积 |
| `char_count` | INTEGER | 正文字符数 |
| `status` | TEXT | `active` / `archived` / `deleted`（逻辑删除标记） |
| `created_at` / `updated_at` | TEXT | 抓取时间与更新时间 |

**word_stats（分词结果，与 pages 一对多）**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | 自增主键 |
| `page_id` | INTEGER FK | 关联 `pages.id`，级联删除 |
| `word` | TEXT | 词汇 |
| `frequency` | INTEGER | 词频 |
| `min_len` | INTEGER | 分词时使用的最短词长 |
| `created_at` | TEXT | 写入时间 |

`UNIQUE(page_id, word)` 保证同一页面内词汇不重复；`word` 与 `frequency` 建有索引，支撑全局高频词聚合查询。

**流程变化**：生成任务现在执行「爬取 → 写入 pages + 批量写入 word_stats（单事务）→ 从 word_stats 读回词频 → 渲染词云」，不再依赖 `.txt` 中间文件。

## API 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/login` | 登录页（已登录会跳回首页） |
| `POST` | `/api/login` | 登录，body：`{"account": "...", "password": "..."}` |
| `POST` | `/api/logout` | 退出登录，清空 session |
| `GET` | `/` | Web 界面（需登录） |
| `GET` | `/admin` | 数据管理后台（需登录） |
| `POST` | `/api/tasks` | 创建生成任务，body：`{"url": "...", "options": {...}}` |
| `GET` | `/api/tasks/<id>` | 查询任务状态、进度、日志与结果 |
| `GET` | `/api/preview/<id>` | 内联预览词云 PNG |
| `GET` | `/api/download/<id>/image` | 下载词云 PNG |
| `GET` | `/api/download/<id>/text` | 下载正文 TXT（从数据库读取） |
| `GET` | `/api/history?limit=40` | 历史记录列表 |
| `DELETE` | `/api/history/<id>` | 删除单条记录及本地文件 |
| `DELETE` | `/api/history` | 清空全部历史与本地文件 |
| `GET` | `/api/custom-words` | 自定义词库列表 |
| `POST` | `/api/custom-words` | 添加词汇，body：`{"word": "知识图谱"}` |
| `DELETE` | `/api/custom-words/<word>` | 移除词汇 |

### 管理后台接口（`/api/admin`，需登录）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/admin/stats` | 看板数据：总量、状态分布、字数、词条数、来源占比、高频词、近 7 天趋势 |
| `GET` | `/api/admin/pages` | 分页 + 条件查询，参数：`page`、`size`、`q`、`host`、`status`、`include_deleted`、`sort`、`order` |
| `GET` | `/api/admin/pages/<id>` | 单条详情（含正文与分词结果） |
| `POST` | `/api/admin/pages` | 新增单条（自动分词） |
| `POST` | `/api/admin/pages/batch` | 批量新增，body：`{"items": [...]}`，单事务写入 |
| `PUT` | `/api/admin/pages/<id>` | 更新标题 / 状态 / 网址 / 正文（正文变更自动重算分词） |
| `DELETE` | `/api/admin/pages/<id>` | 逻辑删除，加 `?hard=1` 为物理删除 |
| `DELETE` | `/api/admin/pages` | 批量删除，body：`{"ids": [1,2], "hard": false}` |
| `POST` | `/api/admin/pages/<id>/restore` | 恢复逻辑删除的数据 |
| `POST` | `/api/admin/pages/<id>/analyze` | 重新分词并覆盖写入 `word_stats` |

所有 `/api/*` 响应都会带上 `Access-Control-Allow-Origin / Methods / Headers`，并支持 `OPTIONS` 预检（204），前端可跨域调用。

`options` 支持字段：

```json
{
  "shape": "heart",          // rectangle/circle/heart/star/cloud/diamond
  "palette": "coral",        // coral/ocean/violet/forest/sky/sakura/morandi/viridis/rainbow/magma/cool/mono
  "font": "msyh",            // 见页面字体下拉框
  "background": "white",     // white/ink/cream/transparent
  "width": 1200,             // 480-2400
  "height": 800,             // 480-2400
  "max_words": 150,          // 20-300
  "min_len": 2               // 1-6，中文词最小长度
}
```

## 爬虫合规与安全

- 抓取前读取目标站点 `robots.txt`，`Disallow` 的页面直接拒绝，并遵循 `Crawl-delay`
- 同一站点默认最小请求间隔 1.5 秒，可通过 `WCS_MIN_INTERVAL` 调大
- 关闭自动重定向，逐跳校验目标地址与 robots 规则，最多 5 跳
- 单页最多读取 5MB，避免异常大页面拖垮服务
- 默认拒绝解析到内网、本机、保留地址的域名；若域名同时解析出公网地址则放行（兼容部分 DNS 污染环境）
- 本项目仅用于抓取公开内容，请遵守目标网站使用条款与相关法律法规

## 异常码说明

| 错误码 | 含义 |
| --- | --- |
| `INVALID_URL` | 网址格式错误或域名无法解析 |
| `PRIVATE_HOST` | 内网 / 本机 / 保留地址被拦截 |
| `ROBOTS_DENIED` | robots.txt 禁止抓取 |
| `FETCH_TIMEOUT` | 抓取超时 |
| `FETCH_FAILED` | 连接失败或重定向异常 |
| `HTTP_ERROR` | 目标站点返回 4xx / 5xx |
| `UNSUPPORTED_CONTENT` | 返回内容不是网页（PDF、图片等） |
| `EMPTY_CONTENT` | 未提取到有效正文（常见于纯 JS 渲染页面） |
| `CONTENT_TOO_SMALL` | 正文少于 80 字 |
| `INSUFFICIENT_WORDS` | 有效词汇过少，无法成图 |
| `GENERATE_FAILED` | 词云渲染失败，可调整参数重试 |
| `FONT_NOT_FOUND` | 未找到可用中文字体 |
| `UNAUTHORIZED` | 未登录访问受保护接口 |
| `LOGIN_FAILED` | 账号或密码不正确 |
| `INTERNAL` | 服务端未知异常 |

## 测试

```bash
python tests/test_smoke.py
```

测试会启动一个本地 HTTP 服务，覆盖 39 个用例：

- URL 规范化、协议校验、内网地址拦截
- robots.txt 拒绝、同站限速
- 正文提取与广告 / 评论 / 脚本 / 页脚过滤
- 正文落盘与读取、正文过少、非网页内容
- 停用词过滤、词频统计、自定义词增删
- 6 种形状生成、透明背景、词汇不足
- Web API 全链路：创建任务 → 轮询进度 → 预览 → 下载图片 / 正文 → 历史记录增删
- 自定义词 API、非法网址 API、首页渲染
- 登录鉴权：未登录跳转 / 401、错误密码、登录成功、退出登录
- 数据库层：单条 / 批量写入、分词读写、分页与条件查询、更新重算、逻辑与物理删除、级联删除、事务回滚、看板统计
- 管理后台 API：看板、CORS 与预检、增删改查、批量新增 / 删除、重新分词、非法入参校验、后台页面渲染

实测公开站点（2026-11）：

| 站点 | 结果 |
| --- | --- |
| example.com | 成功提取正文 |
| 人民网首页 | 成功，提取约 2400 字 |
| 中国新闻网首页 | 成功，提取约 6100 字 |
| 中国新闻网文章页 | 成功，提取约 470 字 |
| 维基百科“人工智能”词条 | 成功，提取约 5.1 万字 |
| 澎湃新闻首页 | 页面依赖 JS 渲染，返回“未提取到正文”友好提示 |
| IT之家 / 36氪 | 反爬或 JS 渲染，返回正文过少提示 |

## 生产部署

开发服务器（`python app.py`）仅适合本地使用。生产环境建议用 WAITRESS（Windows）或 Gunicorn（Linux）托管：

```bash
# Windows
pip install waitress
waitress-serve --host=0.0.0.0 --port=8000 app:app

# Linux
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:8000 app:app
```

注意事项：

1. **多进程下任务状态不共享**。任务进度保存在进程内存，若使用多 worker，请把轮询请求固定到同一进程（例如 Nginx `ip_hash`），或改为单 worker + 多线程。
2. 建议使用 Nginx 反向代理并限制请求体大小、配置超时。
3. `data/` 目录需要持久化与写权限；定期清理旧记录可避免磁盘增长。
4. 对外开放时请在应用前增加鉴权与访问频率限制，避免被滥用为通用爬虫代理。

## 常见问题

**Q：提示“未能提取到有效正文”？**
目标页面可能完全由 JavaScript 渲染（如澎湃、部分 SPA），服务器端 HTML 中没有正文。可换用该站点的文章详情页或静态页面。

**Q：生成的词云没有中文字？**
服务器缺少中文字体。安装 Noto Sans CJK / 文泉驿，或通过 `WCS_FONT_PATH` 指定字体文件。

**Q：提示 403 或抓取失败？**
部分站点有反爬策略。请降低频率、确认页面可公开访问，或更换页面。程序不会绕过 robots.txt。

**Q：想抓本机测试页面？**
设置 `WCS_ALLOW_PRIVATE=1` 后再启动，仅建议在本地测试时使用。

**Q：如何清空数据？**
页面“历史记录 → 清空记录”会删除记录与对应文件；也可直接删除 `data/` 下的 `pages/`、`clouds/`、`history.json`（保留 `custom_words.txt` 可保留自定义词）。
