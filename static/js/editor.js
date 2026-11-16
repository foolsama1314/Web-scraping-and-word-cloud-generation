/* 词云编辑器 —— 词汇元素的拖拽、缩放、层级与持久化 */
(function () {
  "use strict";

  // ---------------------------------------------------------------------------
  // 内部状态
  // ---------------------------------------------------------------------------
  var state = {
    taskId: null,
    layout: [],          // 当前编辑中的词汇数组（可变）
    originalLayout: [],  // 原始自动布局（用于恢复）
    canvas: { width: 800, height: 600 },
    selected: new Set(), // 选中元素的 index
    viewScale: 1,        // 画布整体缩放
    dirty: false,
    saveTimer: null,
  };

  // DOM 引用
  var el = {};

  // Pointer 操作上下文
  var ctx = {
    mode: null,           // null | 'drag' | 'resize' | 'pan'
    activeIndex: -1,
    startX: 0,
    startY: 0,
    origX: 0,
    origY: 0,
    origFontSize: 0,
    origWidth: 0,
    origHeight: 0,
    origLeft: 0,          // 相对元素自身坐标系的起点
    origTop: 0,
    direction: null,      // resize 的方向
    pointerId: null,
    pinchCenter: null,
    pinchOrigSize: 0,
  };

  // 常量
  var MIN_FONT_SIZE = 12;
  var MAX_FONT_SIZE = 400;
  var MIN_CANVAS_SCALE = 0.2;
  var MAX_CANVAS_SCALE = 3.0;
  var AUTO_SAVE_DELAY = 600;

  // ---------------------------------------------------------------------------
  // 初始化
  // ---------------------------------------------------------------------------
  function initDom() {
    el.overlay = document.getElementById("editorOverlay");
    el.stage = document.getElementById("editorStage");
    el.viewport = document.getElementById("editorViewport");
    el.canvas = document.getElementById("editorCanvas");
    el.zoomLabel = document.getElementById("editorZoomLabel");
    el.backBtn = document.getElementById("editorBackBtn");
    el.fitBtn = document.getElementById("editorFitBtn");
    el.resetBtn = document.getElementById("editorResetBtn");
    el.saveBtn = document.getElementById("editorSaveBtn");
    el.statusInfo = document.getElementById("editorStatusInfo");

    if (!el.overlay) return false;

    el.backBtn.addEventListener("click", close);
    el.fitBtn.addEventListener("click", fitToViewport);
    el.resetBtn.addEventListener("click", resetLayout);
    el.saveBtn.addEventListener("click", saveLayout);

    // 画布滚动平移（在空白区域按住拖动）
    el.canvas.addEventListener("pointerdown", onCanvasPointerDown);
    el.viewport.addEventListener("wheel", onViewportWheel, { passive: false });
    document.addEventListener("pointermove", onPointerMove);
    document.addEventListener("pointerup", onPointerUp);
    document.addEventListener("pointercancel", onPointerUp);

    // 阻止浏览器的原生拖拽
    el.canvas.addEventListener("dragstart", function (e) { e.preventDefault(); });

    // 键盘快捷键
    document.addEventListener("keydown", onKeydown);

    return true;
  }

  // ---------------------------------------------------------------------------
  // 公开 API：打开编辑器
  // ---------------------------------------------------------------------------
  window.WordCloudEditor = {
    open: function (taskId, layout, canvas) {
      if (!initDom()) return;
      state.taskId = taskId;
      state.originalLayout = JSON.parse(JSON.stringify(layout || []));
      state.canvas = { width: canvas.width, height: canvas.height };

      // 尝试加载本地持久化布局
      var saved = loadFromStorage(taskId);
      state.layout = saved || JSON.parse(JSON.stringify(layout || []));

      // 给每个元素分配唯一 index
      state.layout.forEach(function (item, i) { item.__idx = i; });

      // 清除选中
      state.selected.clear();
      state.viewScale = 1;
      state.dirty = false;

      renderCanvas();
      fitToViewport();

      el.overlay.hidden = false;
      document.body.style.overflow = "hidden";
      updateStatus();
    },

    close: close,

    isOpen: function () { return el.overlay && !el.overlay.hidden; },
  };

  function close() {
    if (!el.overlay) return;
    // 离开编辑器前自动保存
    if (state.dirty && state.taskId) saveToStorage(state.taskId);
    el.overlay.hidden = true;
    document.body.style.overflow = "";
  }

  // ---------------------------------------------------------------------------
  // 渲染画布
  // ---------------------------------------------------------------------------
  function renderCanvas() {
    el.canvas.style.width = state.canvas.width + "px";
    el.canvas.style.height = state.canvas.height + "px";
    el.canvas.dataset.width = String(state.canvas.width);
    el.canvas.dataset.height = String(state.canvas.height);
    el.canvas.innerHTML = "";

    // 按 z_index 排序渲染
    var sorted = state.layout.slice().sort(function (a, b) {
      return (a.z_index || 0) - (b.z_index || 0);
    });

    for (var i = 0; i < sorted.length; i++) {
      var item = sorted[i];
      var div = document.createElement("div");
      div.className = "editor-word";
      div.dataset.idx = String(item.__idx);
      div.style.zIndex = String(item.z_index || 0);
      div.style.fontSize = item.font_size + "px";
      div.style.color = "rgb(" + item.color.join(",") + ")";
      div.style.fontFamily = '"Microsoft YaHei", "PingFang SC", sans-serif';
      div.style.left = item.x + "px";
      div.style.top = item.y + "px";
      div.dataset.sizeHint = Math.round(item.font_size) + "px";

      var textSpan = document.createElement("span");
      textSpan.textContent = item.word;
      div.appendChild(textSpan);

      // pointerdown 监听
      div.addEventListener("pointerdown", onWordPointerDown);

      if (state.selected.has(item.__idx)) {
        div.classList.add("is-selected");
        addResizeHandles(div);
      }

      el.canvas.appendChild(div);
    }

    updateZoomLabel();
  }

  function addResizeHandles(wordEl) {
    // 先清除旧的
    wordEl.querySelectorAll(".resize-handle").forEach(function (h) { h.remove(); });

    var dirs = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
    dirs.forEach(function (dir) {
      var handle = document.createElement("div");
      handle.className = "resize-handle";
      handle.dataset.dir = dir;
      handle.addEventListener("pointerdown", onResizeHandleDown);
      wordEl.appendChild(handle);
    });

    // 尺寸 tooltip
    var tip = document.createElement("div");
    tip.className = "size-tooltip";
    var idx = parseInt(wordEl.dataset.idx, 10);
    var item = state.layout[idx];
    tip.textContent = item.word + " · " + Math.round(item.font_size) + "px";
    wordEl.appendChild(tip);
  }

  // ---------------------------------------------------------------------------
  // 画布整体缩放适配
  // ---------------------------------------------------------------------------
  function fitToViewport() {
    var vw = el.viewport.clientWidth - 60;
    var vh = el.viewport.clientHeight - 60;
    var sx = vw / state.canvas.width;
    var sy = vh / state.canvas.height;
    state.viewScale = Math.min(sx, sy, 1);
    if (state.viewScale < MIN_CANVAS_SCALE) state.viewScale = MIN_CANVAS_SCALE;
    applyViewScale();
    // 居中到 viewport
    el.viewport.scrollLeft = Math.max(0, (el.canvas.offsetWidth - el.viewport.clientWidth) / 2);
    el.viewport.scrollTop = Math.max(0, (el.canvas.offsetHeight - el.viewport.clientHeight) / 2);
  }

  function applyViewScale() {
    el.canvas.style.transform = "scale(" + state.viewScale + ")";
    el.canvas.style.transformOrigin = "center center";
    updateZoomLabel();
  }

  function updateZoomLabel() {
    el.zoomLabel.textContent = Math.round(state.viewScale * 100) + "%";
  }

  // ---------------------------------------------------------------------------
  // 事件处理
  // ---------------------------------------------------------------------------
  function onCanvasPointerDown(e) {
    // 只在点击 canvas 空白区域时触发
    if (e.target !== el.canvas && e.target !== el.viewport) {
      // 检查是不是点在了 word 元素上
      var wordEl = e.target.closest(".editor-word");
      if (wordEl) return;
    }

    // 点击空白处：取消所有选中
    if (e.button === 0) {
      state.selected.clear();
      renderCanvas();
      updateStatus();
    }
  }

  function onViewportWheel(e) {
    // 在画布空白区域滚轮 → 整体缩放
    // 在选中元素上滚轮 → 切换层级
    var selArr = Array.from(state.selected);
    if (selArr.length > 0) {
      // 检查鼠标位置是否在选中元素上
      var canvasPoint = screenToCanvas(e.clientX, e.clientY);
      var hit = false;
      for (var i = 0; i < selArr.length; i++) {
        var item = state.layout[selArr[i]];
        if (hitTest(canvasPoint, item)) { hit = true; break; }
      }
      if (hit) {
        e.preventDefault();
        layerShift(e.deltaY < 0 ? 1 : -1);
        return;
      }
    }

    e.preventDefault();
    var delta = -e.deltaY * 0.0015;
    var newScale = state.viewScale * (1 + delta);
    state.viewScale = Math.max(MIN_CANVAS_SCALE, Math.min(MAX_CANVAS_SCALE, newScale));
    applyViewScale();
  }

  function onWordPointerDown(e) {
    e.stopPropagation();

    var idx = parseInt(this.dataset.idx, 10);
    var item = state.layout[idx];

    // 选择逻辑
    if (e.shiftKey) {
      if (state.selected.has(idx)) state.selected.delete(idx);
      else state.selected.add(idx);
    } else if (!state.selected.has(idx)) {
      state.selected.clear();
      state.selected.add(idx);
    }

    // 提升 z-index 让拖动不被遮挡
    item.z_index = getMaxZ() + 1;

    renderCanvas();
    updateStatus();

    // renderCanvas 重建了 DOM，需要获取新元素引用来 setPointerCapture
    var newEl = el.canvas.querySelector('[data-idx="' + idx + '"]');
    if (!newEl) return;

    // 开始拖拽
    ctx.mode = "drag";
    ctx.activeIndex = idx;
    ctx.pointerId = e.pointerId;
    ctx.startX = e.clientX;
    ctx.startY = e.clientY;
    ctx.origX = item.x;
    ctx.origY = item.y;

    try {
      newEl.setPointerCapture(e.pointerId);
    } catch (err) { /* 某些浏览器在 render 后会抛 InvalidStateError */ }
    newEl.classList.add("is-dragging");
  }

  function onResizeHandleDown(e) {
    e.stopPropagation();
    e.preventDefault();

    var wordEl = e.target.closest(".editor-word");
    var idx = parseInt(wordEl.dataset.idx, 10);
    var item = state.layout[idx];

    // 确保被选中
    state.selected.clear();
    state.selected.add(idx);
    item.z_index = getMaxZ() + 1;
    renderCanvas();

    // renderCanvas 重建了 DOM，获取新元素引用
    var newEl = el.canvas.querySelector('[data-idx="' + idx + '"]');
    if (!newEl) return;

    ctx.mode = "resize";
    ctx.activeIndex = idx;
    ctx.pointerId = e.pointerId;
    ctx.direction = e.target.dataset.dir;
    ctx.startX = e.clientX;
    ctx.startY = e.clientY;
    ctx.origFontSize = item.font_size;
    ctx.origWidth = item.width;
    ctx.origHeight = item.height;

    try {
      newEl.setPointerCapture(e.pointerId);
    } catch (err) {}
    newEl.classList.add("is-resizing");
  }

  function onPointerMove(e) {
    if (!ctx.mode || ctx.pointerId !== e.pointerId) return;

    var idx = ctx.activeIndex;
    if (idx < 0 || idx >= state.layout.length) return;
    var item = state.layout[idx];
    var canvasPoint = screenToCanvas(e.clientX, e.clientY);

    if (ctx.mode === "drag") {
      var dx = canvasPoint.x - screenToCanvas(ctx.startX, ctx.startY).x;
      var dy = canvasPoint.y - screenToCanvas(ctx.startX, ctx.startY).y;
      item.x = Math.round(clamp(ctx.origX + dx, item.width / 2, state.canvas.width - item.width / 2));
      item.y = Math.round(clamp(ctx.origY + dy, item.height / 2, state.canvas.height - item.height / 2));
      updateWordElement(idx);
    } else if (ctx.mode === "resize") {
      // 根据方向计算缩放系数
      var factor = computeResizeFactor(
        ctx.direction,
        ctx.startX, ctx.startY,
        e.clientX, e.clientY,
        ctx.origWidth, ctx.origHeight
      );

      // 字号缩放，限制范围
      var newSize = clamp(
        Math.round(ctx.origFontSize * factor),
        MIN_FONT_SIZE, MAX_FONT_SIZE
      );
      if (newSize !== item.font_size) {
        // 同时更新 width / height（近似比例）
        var sizeRatio = newSize / ctx.origFontSize;
        item.font_size = newSize;
        item.width = Math.round(ctx.origWidth * sizeRatio);
        item.height = Math.round(ctx.origHeight * sizeRatio);
        updateWordElement(idx);
      }
    }
  }

  function onPointerUp(e) {
    if (ctx.pointerId !== null && e.pointerId !== ctx.pointerId) return;
    if (!ctx.mode) return;

    var idx = ctx.activeIndex;
    if (idx >= 0) {
      var wordEl = el.canvas.querySelector('[data-idx="' + idx + '"]');
      if (wordEl) {
        wordEl.classList.remove("is-dragging", "is-resizing");
        // 更新 tooltip
        var item = state.layout[idx];
        var tip = wordEl.querySelector(".size-tooltip");
        if (tip) tip.textContent = item.word + " · " + Math.round(item.font_size) + "px";
        wordEl.dataset.sizeHint = Math.round(item.font_size) + "px";
      }
    }

    ctx.mode = null;
    ctx.activeIndex = -1;
    ctx.pointerId = null;
    ctx.direction = null;
    state.dirty = true;

    // 标记脏状态 + debounce 自动保存
    scheduleAutoSave();
    updateStatus();
  }

  // ---------------------------------------------------------------------------
  // 渲染更新单个元素
  // ---------------------------------------------------------------------------
  function updateWordElement(idx) {
    var item = state.layout[idx];
    var elWord = el.canvas.querySelector('[data-idx="' + idx + '"]');
    if (!elWord) return;
    elWord.style.left = item.x + "px";
    elWord.style.top = item.y + "px";
    elWord.style.fontSize = item.font_size + "px";
    elWord.dataset.sizeHint = Math.round(item.font_size) + "px";
    var tip = elWord.querySelector(".size-tooltip");
    if (tip) tip.textContent = item.word + " · " + Math.round(item.font_size) + "px";
  }

  // ---------------------------------------------------------------------------
  // 层级切换
  // ---------------------------------------------------------------------------
  function layerShift(delta) {
    var maxZ = getMaxZ();
    Array.from(state.selected).forEach(function (idx) {
      var item = state.layout[idx];
      item.z_index = Math.max(0, Math.min(maxZ + 5, (item.z_index || 0) + delta));
      var elWord = el.canvas.querySelector('[data-idx="' + idx + '"]');
      if (elWord) elWord.style.zIndex = String(item.z_index);
    });
    state.dirty = true;
    scheduleAutoSave();
  }

  function getMaxZ() {
    var max = 0;
    for (var i = 0; i < state.layout.length; i++) {
      var z = state.layout[i].z_index || 0;
      if (z > max) max = z;
    }
    return max;
  }

  // ---------------------------------------------------------------------------
  // 工具函数
  // ---------------------------------------------------------------------------
  function clamp(v, low, high) {
    return Math.max(low, Math.min(high, v));
  }

  function screenToCanvas(clientX, clientY) {
    var rect = el.canvas.getBoundingClientRect();
    // 计算相对于 canvas 的坐标（考虑 viewScale）
    var x = (clientX - rect.left) / state.viewScale;
    var y = (clientY - rect.top) / state.viewScale;
    return { x: x, y: y };
  }

  function hitTest(point, item) {
    var hw = item.width / 2;
    var hh = item.height / 2;
    return point.x >= item.x - hw && point.x <= item.x + hw &&
           point.y >= item.y - hh && point.y <= item.y + hh;
  }

  function computeResizeFactor(dir, startX, startY, curX, curY, origW, origH) {
    var dx = curX - startX;
    var dy = curY - startY;
    var factor = 1;

    // 把屏幕 dx/dy 转换为 canvas 坐标
    var canvasDx = dx / state.viewScale;
    var canvasDy = dy / state.viewScale;

    // 每种方向决定主要缩放轴
    if (dir === "e") factor = (origW + canvasDx) / origW;
    else if (dir === "w") factor = (origW - canvasDx) / origW;
    else if (dir === "s") factor = (origH + canvasDy) / origH;
    else if (dir === "n") factor = (origH - canvasDy) / origH;
    else if (dir === "se") factor = Math.min((origW + canvasDx) / origW, (origH + canvasDy) / origH);
    else if (dir === "sw") factor = Math.min((origW - canvasDx) / origW, (origH + canvasDy) / origH);
    else if (dir === "ne") factor = Math.min((origW + canvasDx) / origW, (origH - canvasDy) / origH);
    else if (dir === "nw") factor = Math.min((origW - canvasDx) / origW, (origH - canvasDy) / origH);

    return clamp(factor, 0.3, 4.0);
  }

  // ---------------------------------------------------------------------------
  // 持久化
  // ---------------------------------------------------------------------------
  function localStorageKey(taskId) {
    return "wcs_editor_" + taskId;
  }

  function saveToStorage(taskId) {
    try {
      var data = {
        layout: state.layout.map(function (item) {
          return {
            word: item.word,
            x: item.x,
            y: item.y,
            width: item.width,
            height: item.height,
            font_size: item.font_size,
            rotation: item.rotation || 0,
            color: item.color,
            z_index: item.z_index || 0,
          };
        }),
        canvas: state.canvas,
        savedAt: Date.now(),
      };
      localStorage.setItem(localStorageKey(taskId), JSON.stringify(data));
    } catch (e) { /* 忽略 */ }
  }

  function loadFromStorage(taskId) {
    try {
      var raw = localStorage.getItem(localStorageKey(taskId));
      if (!raw) return null;
      var data = JSON.parse(raw);
      if (!data || !Array.isArray(data.layout)) return null;
      var layout = data.layout.map(function (item, i) {
        return {
          word: item.word,
          x: item.x,
          y: item.y,
          width: item.width,
          height: item.height,
          font_size: item.font_size,
          rotation: item.rotation || 0,
          color: item.color || [51, 51, 51],
          weight: 0,
          z_index: item.z_index || 0,
          __idx: i,
        };
      });
      state.canvas = data.canvas || state.canvas;
      return layout;
    } catch (e) { return null; }
  }

  function scheduleAutoSave() {
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(function () {
      if (state.dirty && state.taskId) {
        saveToStorage(state.taskId);
      }
    }, AUTO_SAVE_DELAY);
  }

  async function saveLayout() {
    if (!state.taskId) return;
    if (!state.dirty) {
      toast("没有需要保存的更改");
      return;
    }

    // 先存本地（同步）
    saveToStorage(state.taskId);

    try {
      var resp = await fetch("/api/layout/" + state.taskId, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          layout: state.layout.map(function (item) {
            return {
              word: item.word,
              x: item.x,
              y: item.y,
              width: item.width,
              height: item.height,
              font_size: item.font_size,
              rotation: item.rotation || 0,
              color: item.color,
              z_index: item.z_index || 0,
            };
          }),
          canvas: state.canvas,
        }),
      });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      state.dirty = false;
      updateStatus();
      toast("布局已保存", "success");
    } catch (err) {
      toast("云端保存失败，但本地已同步（刷新后可恢复）", "error");
    }
  }

  function resetLayout() {
    if (!state.originalLayout.length) return;
    if (!confirm("恢复自动布局？你当前的调整将会丢失。")) return;
    state.layout = JSON.parse(JSON.stringify(state.originalLayout));
    state.layout.forEach(function (item, i) { item.__idx = i; });
    state.selected.clear();
    state.dirty = true;
    // 清除本地存储
    try { localStorage.removeItem(localStorageKey(state.taskId)); } catch (e) {}
    renderCanvas();
    fitToViewport();
    scheduleAutoSave();
    updateStatus();
    toast("已恢复自动布局");
  }

  // ---------------------------------------------------------------------------
  // 键盘快捷键
  // ---------------------------------------------------------------------------
  function onKeydown(e) {
    if (!WordCloudEditor.isOpen()) return;

    if (e.key === "Escape") {
      close();
      return;
    }

    if (e.ctrlKey || e.metaKey) {
      if (e.key.toLowerCase() === "s") {
        e.preventDefault();
        saveLayout();
      } else if (e.key.toLowerCase() === "a") {
        e.preventDefault();
        state.selected = new Set(state.layout.map(function (_, i) { return i; }));
        renderCanvas();
        updateStatus();
      }
      return;
    }

    if (e.key === "Delete" || e.key === "Backspace") {
      if (state.selected.size > 0 && state.layout.length > state.selected.size) {
        // 删除选中的词汇（注意：从后往前删避免索引偏移）
        var toRemove = Array.from(state.selected).sort(function (a, b) { return b - a; });
        toRemove.forEach(function (idx) {
          state.layout.splice(idx, 1);
        });
        // 重新分配 __idx
        state.layout.forEach(function (item, i) { item.__idx = i; });
        state.selected.clear();
        state.dirty = true;
        renderCanvas();
        scheduleAutoSave();
        updateStatus();
      }
    }
  }

  // ---------------------------------------------------------------------------
  // 状态栏 & Toast
  // ---------------------------------------------------------------------------
  function updateStatus() {
    if (!el.statusInfo) return;
    var selCount = state.selected.size;
    var parts = [];
    parts.push("就绪");
    parts.push("· 共 " + state.layout.length + " 个词汇");
    if (selCount > 0) parts.push("· 已选 " + selCount);
    if (state.dirty) parts.push("· 有未保存更改");
    el.statusInfo.textContent = parts.join(" ");
  }

  function toast(msg, type) {
    var node = document.getElementById("toast");
    if (!node) return;
    node.textContent = msg;
    node.className = "toast show" + (type ? " " + type : "");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(function () { node.className = "toast"; }, 2800);
  }

  // ---------------------------------------------------------------------------
  // 初始化
  // ---------------------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", function () {
    initDom();
  });
})();
