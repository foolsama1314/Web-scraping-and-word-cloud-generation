/* 词云工坊前端交互 */
(function () {
  "use strict";

  var META = {};
  try {
    META = JSON.parse(document.getElementById("app-meta").textContent || "{}");
  } catch (err) {
    META = {};
  }

  var $ = function (selector, root) {
    return (root || document).querySelector(selector);
  };

  var state = {
    shape: (META.defaults && META.defaults.shape) || "rectangle",
    palette: (META.defaults && META.defaults.palette) || "coral",
    font: (META.defaults && META.defaults.font) || "",
    background: (META.defaults && META.defaults.background) || "white",
    width: (META.defaults && META.defaults.width) || 1200,
    height: (META.defaults && META.defaults.height) || 800,
    maxWords: (META.defaults && META.defaults.max_words) || 150,
    minLen: (META.defaults && META.defaults.min_len) || 2,
    customWords: (META.custom_words || []).slice(),
    taskId: null,
    timer: null,
    polling: false,
    renderedLogs: 0,
    failCount: 0,
  };

  var records = new Map();
  var currentResult = null;

  var el = {
    urlInput: $("#urlInput"),
    clearUrl: $("#clearUrl"),
    generateBtn: $("#generateBtn"),
    shapeGrid: $("#shapeGrid"),
    paletteGrid: $("#paletteGrid"),
    fontSelect: $("#fontSelect"),
    sizeSelect: $("#sizeSelect"),
    customSize: $("#customSize"),
    widthInput: $("#widthInput"),
    heightInput: $("#heightInput"),
    backgroundSelect: $("#backgroundSelect"),
    minLenSelect: $("#minLenSelect"),
    maxWords: $("#maxWords"),
    maxWordsValue: $("#maxWordsValue"),
    customWordsBox: $("#customWordsBox"),
    customWordForm: $("#customWordForm"),
    customWordInput: $("#customWordInput"),
    statusPill: $("#statusPill"),
    idleView: $("#idleView"),
    progressView: $("#progressView"),
    resultView: $("#resultView"),
    errorView: $("#errorView"),
    progressFill: $("#progressFill"),
    progressPercent: $("#progressPercent"),
    progressStage: $("#progressStage"),
    progressBar: $("#progressBar"),
    logList: $("#logList"),
    logCount: $("#logCount"),
    resultImg: $("#resultImg"),
    imageLoading: $("#imageLoading"),
    resultTitle: $("#resultTitle"),
    resultSource: $("#resultSource"),
    resultStats: $("#resultStats"),
    resultWords: $("#resultWords"),
    warningBox: $("#warningBox"),
    downloadImage: $("#downloadImage"),
    downloadText: $("#downloadText"),
    regenerateBtn: $("#regenerateBtn"),
    enterEditorBtn: $("#enterEditorBtn"),
    errorMessage: $("#errorMessage"),
    errorHint: $("#errorHint"),
    errorCode: $("#errorCode"),
    retryBtn: $("#retryBtn"),
    backBtn: $("#backBtn"),
    historyGrid: $("#historyGrid"),
    historyCount: $("#historyCount"),
    refreshHistoryBtn: $("#refreshHistoryBtn"),
    clearHistoryBtn: $("#clearHistoryBtn"),
    toast: $("#toast"),
  };

  var ERROR_HINTS = {
    INVALID_URL: "请检查网址是否完整，例如 https://www.example.com。",
    PRIVATE_HOST: "仅支持公开可访问的网址，内网与本地地址已被拦截。",
    ROBOTS_DENIED: "目标站点的 robots.txt 不允许抓取该页面，请更换其他公开网页。",
    FETCH_TIMEOUT: "目标网站响应较慢，请稍后重试或更换网页。",
    FETCH_FAILED: "无法连接目标网站，请确认网址可正常打开、网络通畅。",
    HTTP_ERROR: "目标网站返回了错误状态码，请更换页面或稍后再试。",
    UNSUPPORTED_CONTENT: "该链接不是 HTML 网页（可能是 PDF、图片或下载文件），无法提取正文。",
    EMPTY_CONTENT: "没有提取到正文，页面可能是纯图片、视频或依赖 JavaScript 渲染。",
    CONTENT_TOO_SMALL: "页面正文太少，换一篇内容更丰富的文章试试。",
    INSUFFICIENT_WORDS: "有效词汇不足，可尝试降低最短词长或更换页面。",
    GENERATE_FAILED: "词云渲染失败，请调整画布尺寸、形状或最大词数后重试。",
    FONT_NOT_FOUND: "服务器缺少可用中文字体，请检查字体安装或 WCS_FONT_PATH 配置。",
    TASK_NOT_FOUND: "任务已过期，请重新点击生成。",
    NETWORK: "无法连接服务器，请确认服务是否正在运行。",
    INTERNAL: "服务端出现异常，请查看运行终端中的日志。",
  };

  var SHAPE_ICONS = {
    rectangle:
      '<rect x="5" y="8" width="30" height="22" rx="4" fill="none" stroke="currentColor" stroke-width="2"/>',
    circle:
      '<circle cx="20" cy="19" r="13" fill="none" stroke="currentColor" stroke-width="2"/>',
    heart:
      '<path d="M20 31s-11-6.8-11-13.8A5.8 5.8 0 0 1 20 13a5.8 5.8 0 0 1 11 4.2C31 24.2 20 31 20 31z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
    star:
      '<polygon points="20,4 24.6,14.2 35.5,15.5 27.4,22.8 29.7,33.6 20,28 10.3,33.6 12.6,22.8 4.5,15.5 15.4,14.2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    cloud:
      '<path d="M12 29a6 6 0 0 1 .6-12 9 9 0 0 1 17 2.2A5.6 5.6 0 0 1 30 29z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
    diamond:
      '<polygon points="20,5 34,19 20,33 6,19" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
  };

  /* ---------------------------------------------------------------------- */
  /* 工具                                                                   */
  /* ---------------------------------------------------------------------- */
  function escapeHtml(value) {
    return String(value === undefined || value === null ? "" : value).replace(
      /[&<>"']/g,
      function (char) {
        return {
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        }[char];
      }
    );
  }

  function toast(message, type) {
    var node = el.toast;
    if (!node) return;
    node.textContent = message;
    node.className = "toast show" + (type ? " " + type : "");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(function () {
      node.className = "toast";
    }, 2800);
  }

  function shake(node) {
    if (!node) return;
    node.classList.remove("shake");
    void node.offsetWidth;
    node.classList.add("shake");
  }

  function hostOf(url) {
    try {
      return new URL(url).host;
    } catch (err) {
      return url || "";
    }
  }

  function formatDate(iso) {
    if (!iso) return "";
    var date = new Date(iso);
    if (isNaN(date.getTime())) return iso;
    var pad = function (n) {
      return String(n).padStart(2, "0");
    };
    return (
      pad(date.getMonth() + 1) +
      "-" +
      pad(date.getDate()) +
      " " +
      pad(date.getHours()) +
      ":" +
      pad(date.getMinutes())
    );
  }

  function clamp(value, low, high) {
    var number = parseInt(value, 10);
    if (isNaN(number)) number = low;
    return Math.max(low, Math.min(high, number));
  }

  async function api(url, options) {
    var response = await fetch(url, options || {});
    var data = null;
    try {
      data = await response.json();
    } catch (err) {
      data = null;
    }
    if (!response.ok || !data || data.ok === false) {
      var error =
        (data && data.error) || {
          code: "HTTP_" + response.status,
          message: "请求失败（HTTP " + response.status + "）",
        };
      throw error;
    }
    return data;
  }

  function showView(name) {
    var views = {
      idle: el.idleView,
      progress: el.progressView,
      result: el.resultView,
      error: el.errorView,
    };
    Object.keys(views).forEach(function (key) {
      if (views[key]) views[key].hidden = key !== name;
    });
  }

  function setStatus(kind, text) {
    if (!el.statusPill) return;
    el.statusPill.className = "status-pill" + (kind ? " is-" + kind : "");
    el.statusPill.textContent = text;
  }

  function setBusy(busy) {
    el.generateBtn.disabled = busy;
    el.generateBtn.classList.toggle("loading", busy);
  }

  /* ---------------------------------------------------------------------- */
  /* 初始化表单                                                              */
  /* ---------------------------------------------------------------------- */
  function renderShapes() {
    var shapes = META.shapes || [];
    el.shapeGrid.innerHTML = shapes
      .map(function (shape) {
        var active = shape.key === state.shape;
        return (
          '<button type="button" class="shape-tile' +
          (active ? " is-active" : "") +
          '" data-shape="' +
          escapeHtml(shape.key) +
          '" aria-pressed="' +
          (active ? "true" : "false") +
          '" title="' +
          escapeHtml(shape.desc || shape.label) +
          '">' +
          '<svg viewBox="0 0 40 38" aria-hidden="true">' +
          (SHAPE_ICONS[shape.key] || SHAPE_ICONS.rectangle) +
          "</svg><span>" +
          escapeHtml(shape.label) +
          "</span></button>"
        );
      })
      .join("");
  }

  function renderPalettes() {
    var palettes = META.palettes || [];
    el.paletteGrid.innerHTML = palettes
      .map(function (palette) {
        var active = palette.key === state.palette;
        var swatch = (palette.preview || [])
          .map(function (color) {
            return '<i style="background:' + escapeHtml(color) + '"></i>';
          })
          .join("");
        return (
          '<button type="button" class="palette-tile' +
          (active ? " is-active" : "") +
          '" data-palette="' +
          escapeHtml(palette.key) +
          '" aria-pressed="' +
          (active ? "true" : "false") +
          '"><span class="palette-swatch">' +
          swatch +
          '</span><span class="palette-name">' +
          escapeHtml(palette.label) +
          "</span></button>"
        );
      })
      .join("");
  }

  function renderSelects() {
    el.fontSelect.innerHTML = (META.fonts || [])
      .map(function (font) {
        return (
          '<option value="' +
          escapeHtml(font.key) +
          '">' +
          escapeHtml(font.label) +
          "</option>"
        );
      })
      .join("");

    var sizes = (META.sizes || []).slice();
    sizes.push({ label: "自定义尺寸", width: 0, height: 0, custom: true });
    el.sizeSelect.innerHTML = sizes
      .map(function (size) {
        if (size.custom) return '<option value="custom">自定义尺寸</option>';
        return (
          '<option value="' +
          size.width +
          "x" +
          size.height +
          '">' +
          escapeHtml(size.label) +
          "</option>"
        );
      })
      .join("");

    el.backgroundSelect.innerHTML = (META.backgrounds || [])
      .map(function (bg) {
        return (
          '<option value="' +
          escapeHtml(bg.key) +
          '">' +
          escapeHtml(bg.label) +
          "</option>"
        );
      })
      .join("");

    syncForm();
  }

  function syncForm() {
    el.fontSelect.value = state.font;
    el.backgroundSelect.value = state.background;
    el.minLenSelect.value = String(state.minLen);
    el.maxWords.value = String(state.maxWords);
    el.maxWordsValue.textContent = String(state.maxWords);

    var preset = state.width + "x" + state.height;
    var hasPreset = Array.prototype.some.call(el.sizeSelect.options, function (option) {
      return option.value === preset;
    });
    if (hasPreset) {
      el.sizeSelect.value = preset;
      el.customSize.hidden = true;
    } else {
      el.sizeSelect.value = "custom";
      el.customSize.hidden = false;
    }
    el.widthInput.value = String(state.width);
    el.heightInput.value = String(state.height);

    Array.prototype.forEach.call(el.shapeGrid.children, function (button) {
      var active = button.dataset.shape === state.shape;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    Array.prototype.forEach.call(el.paletteGrid.children, function (button) {
      var active = button.dataset.palette === state.palette;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  /* ---------------------------------------------------------------------- */
  /* 自定义词库                                                              */
  /* ---------------------------------------------------------------------- */
  function renderCustomWords(words) {
    state.customWords = (words || []).slice();
    if (!el.customWordsBox) return;
    if (!state.customWords.length) {
      el.customWordsBox.innerHTML =
        '<span class="placeholder">暂无自定义词汇</span>';
      return;
    }
    el.customWordsBox.innerHTML = state.customWords
      .map(function (word) {
        var encoded = encodeURIComponent(word);
        return (
          '<span class="word-chip">' +
          escapeHtml(word) +
          '<button type="button" data-remove-word="' +
          escapeHtml(encoded) +
          '" aria-label="删除 ' +
          escapeHtml(word) +
          '">×</button></span>'
        );
      })
      .join("");
  }

  async function refreshCustomWords() {
    try {
      var data = await api("/api/custom-words");
      renderCustomWords(data.words);
    } catch (err) {
      /* 静默失败，保留初始数据 */
    }
  }

  /* ---------------------------------------------------------------------- */
  /* 生成流程                                                                */
  /* ---------------------------------------------------------------------- */
  function currentOptions() {
    return {
      shape: state.shape,
      palette: state.palette,
      font: state.font,
      background: state.background,
      width: state.width,
      height: state.height,
      max_words: state.maxWords,
      min_len: state.minLen,
    };
  }

  function resetProgress() {
    el.progressFill.style.width = "0%";
    el.progressPercent.textContent = "0";
    el.progressBar.setAttribute("aria-valuenow", "0");
    el.progressStage.textContent = "准备中…";
    el.logList.innerHTML = "";
    el.logCount.textContent = "0 条";
    state.renderedLogs = 0;
    state.failCount = 0;
  }

  function stopPolling() {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
    state.polling = false;
  }

  async function startGenerate() {
    var url = el.urlInput.value.trim();
    if (!url) {
      toast("请先输入要生成词云的网页地址", "error");
      shake(el.urlInput.closest(".url-field"));
      el.urlInput.focus();
      return;
    }

    stopPolling();
    state.taskId = null;
    resetProgress();
    showView("progress");
    setStatus("running", "生成中");
    setBusy(true);

    try {
      var data = await api("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url, options: currentOptions() }),
      });
      state.taskId = data.task.id;
      pollTask();
    } catch (err) {
      setBusy(false);
      showError(err);
    }
  }

  function pollTask() {
    stopPolling();
    state.timer = setInterval(async function () {
      if (state.polling || !state.taskId) return;
      state.polling = true;
      try {
        var response = await fetch("/api/tasks/" + state.taskId, {
          cache: "no-store",
        });
        if (!response.ok) {
          throw { code: "TASK_NOT_FOUND", message: "任务状态获取失败。" };
        }
        var data = await response.json();
        var task = data.task;
        if (!task) throw { code: "TASK_NOT_FOUND", message: "任务不存在。" };

        state.failCount = 0;
        renderProgress(task);
        if (task.status === "success") {
          stopPolling();
          setBusy(false);
          setStatus("success", "已完成");
          showResult(task.result);
          loadHistory();
          toast("词云生成成功", "success");
        } else if (task.status === "error") {
          stopPolling();
          setBusy(false);
          showError(task.error || { code: "INTERNAL", message: "生成失败。" });
        }
      } catch (err) {
        state.failCount += 1;
        if (state.failCount >= 5) {
          stopPolling();
          setBusy(false);
          showError({
            code: err.code || "NETWORK",
            message: err.message || "无法获取任务进度，请检查服务是否运行。",
          });
        }
      } finally {
        state.polling = false;
      }
    }, 650);
  }

  function renderProgress(task) {
    var percent = Math.max(0, Math.min(100, Number(task.progress) || 0));
    el.progressFill.style.width = percent + "%";
    el.progressPercent.textContent = String(percent);
    el.progressBar.setAttribute("aria-valuenow", String(percent));
    el.progressStage.textContent = task.stage || "处理中…";
    renderLogs(task.logs || [], task.status === "running");
  }

  function renderLogs(logs, running) {
    if (logs.length < state.renderedLogs) {
      el.logList.innerHTML = "";
      state.renderedLogs = 0;
    }
    var fragment = document.createDocumentFragment();
    for (var i = state.renderedLogs; i < logs.length; i += 1) {
      var log = logs[i];
      var item = document.createElement("li");
      if (log.level === "error") item.classList.add("is-error");
      item.innerHTML =
        '<span class="log-dot"></span>' +
        '<span class="log-time">' +
        escapeHtml(log.time || "") +
        "</span>" +
        '<span class="log-msg"><b>' +
        escapeHtml(log.stage || "") +
        "</b>" +
        escapeHtml(log.message || "") +
        "</span>";
      fragment.appendChild(item);
    }
    if (fragment.childNodes.length) {
      el.logList.appendChild(fragment);
      state.renderedLogs = logs.length;
      el.logList.scrollTop = el.logList.scrollHeight;
    }
    el.logCount.textContent = logs.length + " 条";

    var previousCurrent = el.logList.querySelector(".is-current");
    if (previousCurrent) previousCurrent.classList.remove("is-current");
    if (running) {
      var last = el.logList.lastElementChild;
      if (last) last.classList.add("is-current");
    }
  }

  function showResult(result) {
    if (!result) {
      showError({ code: "INTERNAL", message: "任务已完成，但没有返回结果。" });
      return;
    }
    currentResult = result;
    showView("result");
    el.resultImg.classList.remove("is-ready");
    el.imageLoading.hidden = false;
    el.resultImg.onload = function () {
      el.imageLoading.hidden = true;
      el.resultImg.classList.add("is-ready");
    };
    el.resultImg.onerror = function () {
      el.imageLoading.hidden = true;
      toast("词云图片加载失败，请尝试重新生成", "error");
    };
    el.resultImg.src = result.preview_url + "?v=" + Date.now();
    el.resultImg.alt = (result.title || "词云") + " 的词云图片";

    el.resultTitle.textContent = result.title || "未命名页面";
    el.resultSource.href = result.url || "#";
    el.resultSource.textContent = hostOf(result.url);
    el.resultSource.title = result.url || "";

    var stats = result.stats || {};
    var chips = [
      ["正文", (stats.chars || 0) + " 字"],
      ["有效词", stats.unique || 0],
      ["上屏", stats.used_words || 0],
      ["耗时", (result.elapsed || 0) + " s"],
    ];
    el.resultStats.innerHTML = chips
      .map(function (item) {
        return (
          '<span class="meta-chip">' +
          item[0] +
          " <b>" +
          escapeHtml(item[1]) +
          "</b></span>"
        );
      })
      .join("");

    el.warningBox.hidden = !result.warning;
    el.warningBox.textContent = result.warning || "";

    el.downloadImage.href = result.image_download_url;
    el.downloadText.href = result.text_download_url;

    el.resultWords.innerHTML = (result.top_words || [])
      .map(function (pair) {
        return (
          '<span class="word-chip">' +
          escapeHtml(pair[0]) +
          "<b>" +
          escapeHtml(pair[1]) +
          "</b></span>"
        );
      })
      .join("");
  }

  function showError(error) {
    stopPolling();
    showView("error");
    setStatus("error", "生成失败");
    var message = (error && error.message) || "生成失败，请稍后重试。";
    var code = (error && error.code) || "UNKNOWN";
    el.errorMessage.textContent = message;
    el.errorHint.textContent = ERROR_HINTS[code] || "";
    el.errorCode.textContent = code;
    setBusy(false);
  }

  /* ---------------------------------------------------------------------- */
  /* 历史记录                                                                */
  /* ---------------------------------------------------------------------- */
  async function loadHistory() {
    if (!el.historyGrid) return;
    try {
      var data = await api("/api/history?limit=40");
      renderHistory(data.records || []);
    } catch (err) {
      el.historyGrid.innerHTML =
        '<div class="history-empty">历史记录加载失败：' +
        escapeHtml(err.message || "未知错误") +
        "</div>";
    }
  }

  function renderHistory(items) {
    records = new Map();
    el.historyCount.textContent = items.length ? items.length + " 条" : "";
    el.clearHistoryBtn.disabled = !items.length;

    if (!items.length) {
      el.historyGrid.innerHTML =
        '<div class="history-empty">还没有生成记录。完成第一张词云后，这里会保存图片与正文文件。</div>';
      return;
    }

    el.historyGrid.innerHTML = items
      .map(function (record) {
        records.set(record.id, record);
        return (
          '<article class="history-card" data-id="' +
          escapeHtml(record.id) +
          '"><div class="history-thumb"><img loading="lazy" src="' +
          escapeHtml(record.preview_url) +
          '" alt="' +
          escapeHtml(record.title) +
          '"></div><div class="history-body"><h3 title="' +
          escapeHtml(record.title) +
          '">' +
          escapeHtml(record.title) +
          '</h3><p class="history-url">' +
          escapeHtml(hostOf(record.url)) +
          " · " +
          escapeHtml(formatDate(record.created_at)) +
          '</p><div class="history-actions"><a class="mini-btn" href="' +
          escapeHtml(record.image_download_url) +
          '">下载</a><button type="button" class="mini-btn" data-action="reuse">复用</button><button type="button" class="mini-btn danger" data-action="delete">删除</button></div></div></article>'
        );
      })
      .join("");
  }

  function applyRecord(record) {
    var params = record.params || {};
    state.shape = params.shape || state.shape;
    state.palette = params.palette || state.palette;
    state.font = params.font || state.font;
    state.background = params.background || state.background;
    state.width = params.width || state.width;
    state.height = params.height || state.height;
    state.maxWords = params.max_words || state.maxWords;
    state.minLen = params.min_len || state.minLen;
    el.urlInput.value = record.url || "";
    el.clearUrl.hidden = !el.urlInput.value;
    syncForm();
    document.getElementById("generator").scrollIntoView({ behavior: "smooth", block: "start" });
    toast("已载入历史参数，可再次生成");
  }

  /* ---------------------------------------------------------------------- */
  /* 事件绑定                                                                */
  /* ---------------------------------------------------------------------- */
  el.shapeGrid.addEventListener("click", function (event) {
    var button = event.target.closest("[data-shape]");
    if (!button) return;
    state.shape = button.dataset.shape;
    syncForm();
  });

  el.paletteGrid.addEventListener("click", function (event) {
    var button = event.target.closest("[data-palette]");
    if (!button) return;
    state.palette = button.dataset.palette;
    syncForm();
  });

  el.fontSelect.addEventListener("change", function () {
    state.font = el.fontSelect.value;
  });

  el.backgroundSelect.addEventListener("change", function () {
    state.background = el.backgroundSelect.value;
  });

  el.minLenSelect.addEventListener("change", function () {
    state.minLen = parseInt(el.minLenSelect.value, 10) || 2;
  });

  el.sizeSelect.addEventListener("change", function () {
    var value = el.sizeSelect.value;
    if (value === "custom") {
      el.customSize.hidden = false;
      state.width = clamp(el.widthInput.value, 480, 2400);
      state.height = clamp(el.heightInput.value, 480, 2400);
      el.widthInput.value = state.width;
      el.heightInput.value = state.height;
      return;
    }
    var parts = value.split("x");
    state.width = parseInt(parts[0], 10) || 1200;
    state.height = parseInt(parts[1], 10) || 800;
    el.customSize.hidden = true;
    el.widthInput.value = state.width;
    el.heightInput.value = state.height;
  });

  function commitSize() {
    state.width = clamp(el.widthInput.value, 480, 2400);
    state.height = clamp(el.heightInput.value, 480, 2400);
    el.widthInput.value = state.width;
    el.heightInput.value = state.height;
  }

  el.widthInput.addEventListener("change", commitSize);
  el.heightInput.addEventListener("change", commitSize);

  el.maxWords.addEventListener("input", function () {
    state.maxWords = clamp(el.maxWords.value, 30, 300);
    el.maxWordsValue.textContent = String(state.maxWords);
  });

  el.customWordForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    var word = el.customWordInput.value.trim();
    if (!word) return;
    try {
      var data = await api("/api/custom-words", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ word: word }),
      });
      renderCustomWords(data.words);
      el.customWordInput.value = "";
      toast("已加入自定义词库：" + data.word, "success");
    } catch (err) {
      toast(err.message || "添加失败", "error");
    }
  });

  el.customWordsBox.addEventListener("click", async function (event) {
    var button = event.target.closest("[data-remove-word]");
    if (!button) return;
    var word = decodeURIComponent(button.dataset.removeWord);
    try {
      var data = await api("/api/custom-words/" + encodeURIComponent(word), {
        method: "DELETE",
      });
      renderCustomWords(data.words);
      toast("已从词库移除：" + word);
    } catch (err) {
      toast(err.message || "删除失败", "error");
    }
  });

  el.urlInput.addEventListener("input", function () {
    el.clearUrl.hidden = !el.urlInput.value;
  });

  el.urlInput.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      startGenerate();
    }
  });

  el.clearUrl.addEventListener("click", function () {
    el.urlInput.value = "";
    el.clearUrl.hidden = true;
    el.urlInput.focus();
  });

  document.querySelectorAll(".example-url").forEach(function (button) {
    button.addEventListener("click", function () {
      el.urlInput.value = button.dataset.url || "";
      el.clearUrl.hidden = !el.urlInput.value;
      el.urlInput.focus();
    });
  });

  el.generateBtn.addEventListener("click", startGenerate);
  el.regenerateBtn.addEventListener("click", startGenerate);
  el.retryBtn.addEventListener("click", startGenerate);

  var logoutBtn = $("#logoutBtn");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", async function () {
      if (!window.confirm("确定退出登录吗？")) return;
      try {
        await fetch("/api/logout", { method: "POST" });
      } catch (err) {
        /* 忽略网络异常，直接跳转登录页 */
      }
      window.location.href = "/login";
    });
  }

  if (el.enterEditorBtn) {
    el.enterEditorBtn.addEventListener("click", function () {
      if (!currentResult || !currentResult.layout || !currentResult.layout.length) {
        toast("该结果不支持编辑，可能是旧版本生成的", "error");
        return;
      }
      if (!window.WordCloudEditor) {
        toast("编辑器模块未加载，请刷新页面", "error");
        return;
      }
      var canvas = currentResult.canvas || {
        width: currentResult.params.width,
        height: currentResult.params.height,
      };
      WordCloudEditor.open(state.taskId, currentResult.layout, canvas);
    });
  }
  el.backBtn.addEventListener("click", function () {
    setStatus("", "待开始");
    showView("idle");
    el.urlInput.focus();
  });

  el.refreshHistoryBtn.addEventListener("click", function () {
    loadHistory();
    toast("历史记录已刷新");
  });

  el.clearHistoryBtn.addEventListener("click", async function () {
    if (!records.size) return;
    if (!window.confirm("确定清空全部历史记录吗？本地图片与正文文件也会一并删除。")) {
      return;
    }
    try {
      await api("/api/history", { method: "DELETE" });
      toast("历史记录已清空", "success");
      loadHistory();
    } catch (err) {
      toast(err.message || "清空失败", "error");
    }
  });

  el.historyGrid.addEventListener("click", async function (event) {
    var card = event.target.closest(".history-card");
    if (!card) return;
    var record = records.get(card.dataset.id);
    if (!record) return;

    var actionBtn = event.target.closest("[data-action]");
    if (!actionBtn) return;

    if (actionBtn.dataset.action === "reuse") {
      applyRecord(record);
      return;
    }

    if (actionBtn.dataset.action === "delete") {
      if (!window.confirm("删除这条记录？对应的图片与正文文件会被移除。")) return;
      try {
        await api("/api/history/" + encodeURIComponent(record.id), { method: "DELETE" });
        toast("记录已删除");
        loadHistory();
      } catch (err) {
        toast(err.message || "删除失败", "error");
      }
    }
  });

  /* ---------------------------------------------------------------------- */
  /* 启动                                                                    */
  /* ---------------------------------------------------------------------- */
  renderShapes();
  renderPalettes();
  renderSelects();
  renderCustomWords(state.customWords);
  refreshCustomWords();
  loadHistory();

  if (META.defaults && META.defaults.font === "") {
    toast("未检测到可用中文字体，请检查服务器字体配置", "error");
  }
})();
