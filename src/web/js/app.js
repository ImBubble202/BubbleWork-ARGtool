/* LinkChart · 侦探推理墙
   无第三方依赖，纯前端实现：
   - 软木板质感 + 暗角灯光
   - 上传照片 / 文字便签，均用一枚工字钉钉在墙上
   - 点击工字钉以当前线色连接线索
   - 拖动照片 / 便签，连线用 Verlet 绳索物理模拟，产生真实晃动、下垂与回弹
   - localStorage + IndexedDB 自动保存案件内容 */
(function () {
  "use strict";

  /* ---------------- 常量与全局状态 ---------------- */
  const STORAGE_KEY = "linkchart.state.v1";
  const DB_NAME = "linkchart-assets";
  const DB_STORE = "photos";

  const COLORS = [
    { name: "红线", value: "#e23b3b" },
    { name: "蓝线", value: "#3d6fe0" },
    { name: "绿线", value: "#3a9d4a" },
    { name: "黄线", value: "#e4b34a" },
    { name: "紫线", value: "#9a55c9" },
    { name: "橙线", value: "#e07c2f" },
    { name: "粉线", value: "#e255a1" },
    { name: "黑线", value: "#413e3a" }
  ];

  const state = {
    caseTitle: "未命名事件",
    tool: "select", // select | cut
    color: COLORS[0].value,
    items: [],
    links: []
  };

  const byId = new Map();   // item.id -> item
  const ropes = new Map();  // link.id -> rope（物理粒子）

  const runtime = {
    db: null,
    dbReady: null,          // 打开图片库的 Promise（刚启动就双击 .lc 时要等它）
    noDB: false,
    storageWarned: false,
    armedId: null,          // 已点亮的图钉（连线起点）
    editingId: null,        // 正在编辑文字的便签
    drag: null,
    pendingDrag: null,
    cutArmed: null,          // 剪线模式里已经点蓝的那枚工字钉
    mouse: { x: -9999, y: -9999 },
    saveTimer: null,
    toastTimer: null,
    demoCounter: 0,
    hintClosed: false
  };

  const HINT_KEY = "linkchart.hintClosed";
  // 「这次运行」的编号：程序每次启动都会换一个新的，页面发现变了就把上一轮清空
  const RUN_KEY = "bubblework.run.v1";

  let els = {};
  let lastFrameTs = performance.now();
  let frameBusy = false;

  /* ---------------- 画布视图（世界坐标） ----------------
     墙是一块固定大小的大画布，屏幕看到的是它的一个视窗：
       world.x / world.y  = 视窗左上角在世界里的位置
       view.scale         = 缩放比例
     照片、便签都存世界坐标，所以平移缩放不会改变数据。      */
  // 软木板是一块**固定大小**的画板（世界坐标写死，和缩放无关）：
  // 所以不管缩放到多少，边界都在同一个地方 —— 拖到头会顶住、拖到这边就停，
  // 但这条边界永远不画出来（外面看起来还是同样的软木纹，只是拖不出去）。
  const BOARD = { halfW: 2600, halfH: 1800 };  // 木板半径（世界坐标，固定）
  const MAX_ITEM_SIZE = 4000;                  // 单个照片/便签的尺寸上限
  const MIN_ZOOM = 0.25;                       // 允许的最小缩放（还会按窗口大小兜底，见 minZoom()）
  const MAX_ZOOM = 3;
  const view = { x: 0, y: 0, scale: 1 };
  // 画布（#scene）在窗口里的原点。自绘标题栏占了顶部一条，所以它不是 (0,0)。
  // 所有"屏幕坐标 ↔ 世界坐标"的换算都要减掉它，否则缩放锚点、连线端点都会偏。
  let sceneOrigin = { x: 0, y: 0 };

  function measureScene() {
    if (!els.scene) return;
    const r = els.scene.getBoundingClientRect();
    if (r.width > 1 && r.height > 1) sceneOrigin = { x: r.left, y: r.top };
  }

  /* 画布（#scene）的尺寸。画布一定按这个尺寸来设，否则会被拉伸、坐标就偏了。 */
  function sceneSize() {
    if (!els.scene) return { w: window.innerWidth, h: window.innerHeight };
    const w = els.scene.clientWidth || window.innerWidth;
    const h = els.scene.clientHeight || window.innerHeight;
    return { w: w, h: h };
  }

  function worldToScreen(p) {
    // 返回的是"画布内部"的坐标（连线和图钉都画在画布层里）
    return { x: p.x * view.scale + view.x, y: p.y * view.scale + view.y };
  }

  function screenToWorld(p) {
    return {
      x: (p.x - sceneOrigin.x - view.x) / view.scale,
      y: (p.y - sceneOrigin.y - view.y) / view.scale
    };
  }

  function visibleWorldRect() {
    const a = screenToWorld({ x: sceneOrigin.x, y: sceneOrigin.y });
    const b = screenToWorld({ x: window.innerWidth, y: window.innerHeight });
    return { x: a.x, y: a.y, w: b.x - a.x, h: b.y - a.y };
  }

  function clampView() {
    const vis = visibleWorldRect();
    // 木板大小是固定的，所以不管缩放到多少，边界都在同一个位置
    const limitX = Math.max(0, BOARD.halfW - vis.w / 2);
    const limitY = Math.max(0, BOARD.halfH - vis.h / 2);
    const cx = vis.x + vis.w / 2;
    const cy = vis.y + vis.h / 2;
    const dx = clamp(cx, -limitX, limitX) - cx;
    const dy = clamp(cy, -limitY, limitY) - cy;
    if (dx || dy) {
      // 视窗中心要往 +dx 走，等价于把世界往 -dx 平移
      view.x -= dx * view.scale;
      view.y -= dy * view.scale;
    }
  }

  /* 木板的世界坐标范围。
     固定大小、和缩放无关：边界真实存在（拖到头会顶住），但永远不画出来。 */
  function boardRect() {
    return { left: -BOARD.halfW, top: -BOARD.halfH, right: BOARD.halfW, bottom: BOARD.halfH };
  }

  /* 允许的最小缩放。
     缩得太小就会看见木板以外的区域（那片区域没法放东西），所以按窗口大小兜底：
     保证「看得见的地方一定在木板里」。 */
  function minZoom() {
    const w = window.innerWidth || 1;
    const h = window.innerHeight || 1;
    return Math.max(MIN_ZOOM, w / (2 * BOARD.halfW), h / (2 * BOARD.halfH));
  }

  /* 把「照片 / 便签」夹在木板范围内：贴到边就停住，拖不出去 */
  function clampItemToBoard(item, x, y) {
    const board = boardRect();
    const w = item.w || (item.el && item.el.offsetWidth) || 0;
    const h = item.h || (item.el && item.el.offsetHeight) || 0;
    return {
      x: clamp(x, board.left, Math.max(board.left, board.right - w)),
      y: clamp(y, board.top, Math.max(board.top, board.bottom - h))
    };
  }

  function updateZoomUi() {
    if (!els.zoomRange) return;
    const low = Math.round(minZoom() * 100);
    const pct = Math.round(view.scale * 100);
    // 缩放轴的下限跟着窗口大小走（保证缩到底也不会看见木板外面）
    els.zoomRange.min = String(low);
    els.zoomRange.max = String(Math.round(MAX_ZOOM * 100));
    els.zoomRange.value = clamp(pct, low, MAX_ZOOM * 100);
    els.zoomLabel.textContent = pct + "%";
  }

  let rasterScale = 1;        // 上一次「已经画清楚」的倍率（见 refreshRaster）
  let rasterTimer = null;

  function applyView() {
    clampView();
    const tf = "translate3d(" + view.x + "px," + view.y + "px,0) scale(" + view.scale + ")";
    if (els.world) {
      els.world.style.transform = tf;
      // 控件（✕ / A－ / A＋ / 缩放手柄 / 图钉）跟着画布一起缩放：
      // 放大时按钮变大、缩小时按钮变小（和便签/照片本身保持一样的比例）。
      els.world.style.setProperty("--ui-scale", "1");
    }
    if (els.pins) {
      // 图钉层是独立的一层（为了能压在线条上面），所以要自己跟着画布平移缩放
      els.pins.style.transform = tf;
      els.pins.style.setProperty("--ui-scale", "1");
    }
    if (view.scale !== rasterScale) {
      rasterScale = view.scale;
      refreshRasterSoon();
    }
    updateZoomUi();
    scheduleCork();
  }

  /* 缩放停下来之后，让照片 / 便签（特别是上面的字）按新倍率重新画一遍。
     不这么做的话，浏览器会拿缩放前那一张位图直接放大 → 字看着发虚，
     点一下（触发了重绘）才变清楚。
     两边一起使劲，保证一定重画：
       ① 临时把它提升成独立图层（新图层必然重新光栅化）；
       ② 用一个「几乎看不出差别」的倍率，等两帧稳定后再回到准确倍率。 */
  function refreshRaster() {
    if (!els.world) return;
    const at = (k) => "translate3d(" + view.x + "px," + view.y + "px,0) scale(" + k + ")";
    const s = view.scale;
    els.world.style.willChange = "transform";
    if (els.pins) els.pins.style.willChange = "transform";
    if (els.items) els.items.style.willChange = "transform";   // 让整面墙的内容一起重画
    els.world.style.transform = at(s * 1.0012);
    if (els.pins) els.pins.style.transform = at(s * 1.0012);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      els.world.style.transform = at(s);
      if (els.pins) els.pins.style.transform = at(s);
      requestAnimationFrame(() => {
        els.world.style.willChange = "";
        if (els.pins) els.pins.style.willChange = "";
        if (els.items) els.items.style.willChange = "";
      });
    }));
  }
  function refreshRasterSoon() {
    clearTimeout(rasterTimer);
    rasterTimer = setTimeout(refreshRaster, 130);
  }

  // 背景纹理重绘比较贵，用一帧一次的节流，拖动时也不会卡
  let corkPending = false;
  function scheduleCork() {
    if (corkPending) return;
    corkPending = true;
    requestAnimationFrame(() => {
      corkPending = false;
      drawCork();
    });
  }

  // 以屏幕上某一点为锚点缩放（滚轮缩放时鼠标下的内容保持不动）
  function zoomAt(screenX, screenY, nextScale) {
    const s = clamp(nextScale, minZoom(), MAX_ZOOM);
    const k = s / view.scale;
    view.x = screenX - (screenX - view.x) * k;
    view.y = screenY - (screenY - view.y) * k;
    view.scale = s;
    applyView();
  }

  function setZoomCentered(nextScale) {
    // 以画布中心为锚点（画布原点可能不是 (0,0)，比如有自绘标题栏时）
    zoomAt((window.innerWidth - sceneOrigin.x) / 2,
           (window.innerHeight - sceneOrigin.y) / 2, nextScale);
  }

  function resetView() {
    view.x = 0;
    view.y = 0;
    view.scale = 1;
    applyView();
  }

  /* ---------------- 小工具 ---------------- */
  function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

  function rand(min, max) { return min + Math.random() * (max - min); }

  function uid() {
    return (crypto && crypto.randomUUID)
      ? crypto.randomUUID().slice(0, 8)
      : "n" + Math.random().toString(36).slice(2, 10);
  }

  function dist(ax, ay, bx, by) {
    return Math.hypot(bx - ax, by - ay);
  }

  function itemKey(a, b) {
    return a < b ? a + "|" + b : b + "|" + a;
  }

  function deg2rad(deg) { return deg * Math.PI / 180; }

  function colorName(value) {
    const c = COLORS.find((c) => c.value === value);
    return c ? c.name : String(value || "").toUpperCase();
  }

  /* ---------------- DOM 引用 ---------------- */
  function cacheEls() {
    els = {
      cork: document.getElementById("cork"),
      strings: document.getElementById("strings"),
      world: document.getElementById("world"),
      items: document.getElementById("items"),
      pins: document.getElementById("pins"),
      hint: document.getElementById("hint"),
      hintText: document.getElementById("hintText"),
      hintClose: document.getElementById("hintClose"),
      hintRestore: document.getElementById("hintRestore"),
      toast: document.getElementById("toast"),
      tools: document.getElementById("tools"),
      colorBtn: document.getElementById("colorBtn"),
      colorSwatch: document.getElementById("colorSwatch"),
      colorPop: document.getElementById("colorPop"),
      cpRecent: document.getElementById("cpRecent"),
      cpPreset: document.getElementById("cpPreset"),
      cpCustom: document.getElementById("cpCustom"),
      fileInput: document.getElementById("fileInput"),
      caseName: document.getElementById("caseName"),
      stats: document.getElementById("stats"),
      toolSelect: document.getElementById("toolSelect"),
      toolCut: document.getElementById("toolCut"),
      btnUpload: document.getElementById("btnUpload"),
      btnNote: document.getElementById("btnNote"),
      btnClear: document.getElementById("btnClear"),
      btnExport: document.getElementById("btnExport"),
      btnImport: document.getElementById("btnImport"),
      wallFileInput: document.getElementById("wallFileInput"),
      zoomRange: document.getElementById("zoomRange"),
      zoomLabel: document.getElementById("zoomLabel"),
      zoomIn: document.getElementById("zoomIn"),
      zoomOut: document.getElementById("zoomOut"),
      zoomReset: document.getElementById("zoomReset"),
      scene: document.getElementById("scene")
    };
  }

  /* ---------------- 存储：localStorage + IndexedDB ---------------- */
  function readSavedRaw() {
    try { return localStorage.getItem(STORAGE_KEY); } catch (e) { return null; }
  }

  function readSaved() {
    const raw = readSavedRaw();
    if (raw == null) return null;
    try {
      const data = JSON.parse(raw);
      return (data && typeof data === "object") ? data : null;
    } catch (e) {
      console.warn("案件数据解析失败", e);
      return null;
    }
  }

  function snapshot() {
    return {
      v: 1,
      caseTitle: state.caseTitle,
      color: state.color,
      items: state.items.map((it) => {
        const plain = {
          id: it.id,
          type: it.type,
          x: it.x,
          y: it.y,
          angle: it.angle
        };
        if (it.type === "photo") {
          plain.w = it.w;
          plain.h = it.h;
          if (it.dataUrl) plain.dataUrl = it.dataUrl;
        } else {
          plain.w = it.w;
          plain.text = it.text;
        }
        return plain;
      }),
      links: state.links.map((l) => ({ id: l.id, from: l.from, to: l.to, color: l.color }))
    };
  }

  function saveLater() {
    clearTimeout(runtime.saveTimer);
    runtime.saveTimer = setTimeout(saveNow, 500);
  }

  function saveNow() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot()));
    } catch (e) {
      if (!runtime.storageWarned) {
        runtime.storageWarned = true;
        toast("浏览器没有允许本地保存，刷新后内容可能丢失");
      }
    }
  }

  function openDB() {
    return new Promise((resolve) => {
      let req;
      try { req = indexedDB.open(DB_NAME, 1); } catch (e) { runtime.noDB = true; return resolve(null); }
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(DB_STORE)) db.createObjectStore(DB_STORE);
      };
      req.onsuccess = () => {
        runtime.db = req.result;
        runtime.noDB = false;
        resolve(runtime.db);
      };
      req.onerror = () => {
        runtime.noDB = true;
        resolve(null);
      };
    });
  }

  function dbPut(key, blob) {
    return new Promise((resolve) => {
      if (!runtime.db) return resolve(false);
      try {
        const tx = runtime.db.transaction(DB_STORE, "readwrite");
        tx.objectStore(DB_STORE).put(blob, key);
        tx.oncomplete = () => resolve(true);
        tx.onerror = () => resolve(false);
        tx.onabort = () => resolve(false);
      } catch (e) { resolve(false); }
    });
  }

  function dbGet(key) {
    return new Promise((resolve) => {
      if (!runtime.db) return resolve(null);
      try {
        const tx = runtime.db.transaction(DB_STORE, "readonly");
        const req = tx.objectStore(DB_STORE).get(key);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => resolve(null);
      } catch (e) { resolve(null); }
    });
  }

  function dbDel(key) {
    if (!runtime.db) return Promise.resolve(false);
    return new Promise((resolve) => {
      try {
        const tx = runtime.db.transaction(DB_STORE, "readwrite");
        tx.objectStore(DB_STORE).delete(key);
        tx.oncomplete = () => resolve(true);
        tx.onerror = () => resolve(false);
      } catch (e) { resolve(false); }
    });
  }

  function dbClear() {
    if (!runtime.db) return Promise.resolve(false);
    return new Promise((resolve) => {
      try {
        const tx = runtime.db.transaction(DB_STORE, "readwrite");
        const store = tx.objectStore(DB_STORE);
        const keysReq = store.getAllKeys();
        keysReq.onsuccess = () => {
          keysReq.result.forEach((k) => store.delete(k));
        };
        tx.oncomplete = () => resolve(true);
        tx.onerror = () => resolve(false);
      } catch (e) { resolve(false); }
    });
  }

  /* 换一轮（重启后清空）专用：把图片库清干净，但**留着**「上次用过的目录」这条记录，
     否则用户刚养成的「导入导出默认打开上次那个文件夹」就白设了。 */
  function dbClearExcept(keepKeys) {
    const keep = new Set((keepKeys || []).filter(Boolean));
    if (!runtime.db) return Promise.resolve(false);
    return new Promise((resolve) => {
      try {
        const tx = runtime.db.transaction(DB_STORE, "readwrite");
        const store = tx.objectStore(DB_STORE);
        const keysReq = store.getAllKeys();
        keysReq.onsuccess = () => {
          keysReq.result.forEach((k) => { if (!keep.has(k)) store.delete(k); });
        };
        tx.oncomplete = () => resolve(true);
        tx.onerror = () => resolve(false);
        tx.onabort = () => resolve(false);
      } catch (e) { resolve(false); }
    });
  }

  /* 只删「照片墙自己的」图片。
     简记（笔记模式）里的图片也存在同一个对象仓库里（key 前缀是 note-），
     所以清空照片墙 / 导入照片墙时**必须**用这个、不能用 dbClear()，
     否则会把简记里的图片一起删掉 —— 表现就是「图片导出/导入后就看不到了」。 */
  function dbDelMany(keys) {
    const list = (keys || []).filter(Boolean);
    if (!runtime.db || !list.length) return Promise.resolve(0);
    return new Promise((resolve) => {
      try {
        const tx = runtime.db.transaction(DB_STORE, "readwrite");
        const store = tx.objectStore(DB_STORE);
        list.forEach((key) => store.delete(key));
        tx.oncomplete = () => resolve(list.length);
        tx.onerror = () => resolve(0);
        tx.onabort = () => resolve(0);
      } catch (e) { resolve(0); }
    });
  }

  function wallPhotoIds(items) {
    return (items || []).filter((it) => it && it.type === "photo").map((it) => it.id);
  }

  /* ---------------- 记住上次「导入 / 导出」用的目录 ----------------
     用 File System Access API 的目录句柄（Chromium 支持把它存进 IndexedDB），
     下次打开文件框时直接定位到那个目录；第一次用（或拿不到句柄）→ 桌面。 */
  const LAST_DIR_KEY = "__lastDir__";

  async function rememberDirFrom(handle) {
    try {
      if (!handle || typeof handle.getParent !== "function") return;
      const dir = await handle.getParent();
      if (dir) await dbPut(LAST_DIR_KEY, dir);
    } catch (e) { /* 拿不到就算了 */ }
  }

  async function pickerOptions(extra) {
    const opts = Object.assign({}, extra);
    let start = "desktop";                 // 第一次使用 → 桌面
    try {
      const dir = await dbGet(LAST_DIR_KEY);
      if (dir) start = dir;                // 上次用过的目录
    } catch (e) { /* 忽略 */ }
    opts.startIn = start;
    return opts;
  }

  // 传统 <input type=file> 的退路：点完文件后把选中的文件交回给调用方
  let pendingImportFile = null;

  async function pickWallFile() {
    if (window.showOpenFilePicker) {
      try {
        const [handle] = await window.showOpenFilePicker(await pickerOptions({
          multiple: false,
          types: [{ description: "照片墙 / 简记文件", accept: { "application/json": [".lc", ".linkchart", ".json"] } }]
        }));
        if (!handle) return null;
        const file = await handle.getFile();
        await rememberDirFrom(handle);          // 记住这次是从哪个目录打开的
        return file;
      } catch (err) {
        if (err && err.name === "AbortError") return null;
        // 其它情况（不支持这个 API）→ 退回传统 input
      }
    }
    return new Promise((resolve) => {
      pendingImportFile = resolve;
      els.wallFileInput.click();
    });
  }

  function fileToDataUrl(file) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(fr.result);
      fr.onerror = () => reject(new Error("图片读取失败"));
      fr.readAsDataURL(file);
    });
  }

  function dataUrlToBlob(dataUrl) {
    const comma = dataUrl.indexOf(",");
    const meta = comma >= 0 ? dataUrl.slice(0, comma) : "";
    const base64 = comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl;
    const mimeMatch = meta.match(/^data:([^;]+);/);
    const mime = mimeMatch ? mimeMatch[1] : "image/png";
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return new Blob([bytes], { type: mime });
  }

  async function assetDataUrl(item) {
    if (item.dataUrl) return item.dataUrl;
    if (runtime.db) {
      const blob = await dbGet(item.id);
      if (blob) return fileToDataUrl(blob);
    }
    if (item._url) {
      try {
        const resp = await fetch(item._url);
        if (resp.ok) return fileToDataUrl(await resp.blob());
      } catch (e) { /* 下面返回空 */ }
    }
    return null;
  }

  function plainItemDef(item) {
    const def = { id: item.id, type: item.type, x: item.x, y: item.y, angle: item.angle };
    if (item.type === "photo") {
      def.w = item.w;
      def.h = item.h;
    } else {
      def.w = item.w;
      def.text = item.text;
      if (item.fontSize) def.fontSize = item.fontSize;
      if (item.hFixed) {          // 手动定过高度的便签才记高度
        def.h = item.h;
        def.hFixed = true;
      }
    }
    return def;
  }

  /* ---------------- 软木板背景 ---------------- */
  let corkTile = null;

  // 生成一次可平铺的软木纹理（固定随机种子，平移缩放时纹理不会闪）
  function buildCorkTile() {
    if (corkTile) return corkTile;
    const size = 512;
    const tile = document.createElement("canvas");
    tile.width = size;
    tile.height = size;
    const t = tile.getContext("2d");

    let seed = 20240916;
    const rnd = () => {
      seed = (seed * 1664525 + 1013904223) % 4294967296;
      return seed / 4294967296;
    };

    t.fillStyle = "#c99960";
    t.fillRect(0, 0, size, size);

    const noise = document.createElement("canvas");
    noise.width = size;
    noise.height = size;
    const nctx = noise.getContext("2d");
    const img = nctx.createImageData(size, size);
    for (let i = 0; i < img.data.length; i += 4) {
      const v = 118 + Math.floor(rnd() * 74);
      img.data[i] = v;
      img.data[i + 1] = v - 5;
      img.data[i + 2] = v - 14;
      img.data[i + 3] = 34;
    }
    nctx.putImageData(img, 0, 0);
    t.drawImage(noise, 0, 0);

    // 软木孔洞与深浅斑点（画 9 遍保证平铺接缝处不出现断口）
    for (let i = 0; i < 1500; i++) {
      const x = rnd() * size;
      const y = rnd() * size;
      const r = 0.5 + rnd() * 1.9;
      const dark = rnd() < 0.55;
      t.fillStyle = dark
        ? "rgba(74,43,17," + (0.08 + rnd() * 0.18) + ")"
        : "rgba(255,219,162," + (0.04 + rnd() * 0.12) + ")";
      for (const ox of [-size, 0, size]) {
        for (const oy of [-size, 0, size]) {
          t.beginPath();
          t.arc(x + ox, y + oy, r, 0, Math.PI * 2);
          t.fill();
        }
      }
    }

    // 长条木纹
    t.globalAlpha = 0.045;
    t.strokeStyle = "#5e3b1c";
    for (let i = 0; i < 14; i++) {
      const y = rnd() * size;
      const width = 5 + rnd() * 18;
      const wave1 = (rnd() - 0.5) * 60;
      const wave2 = (rnd() - 0.5) * 60;
      t.lineWidth = width;
      for (const oy of [-size, 0, size]) {
        t.beginPath();
        t.moveTo(-20, y + oy);
        t.bezierCurveTo(size * 0.3, y + oy + wave1,
          size * 0.7, y + oy + wave2, size + 20, y + oy + (wave1 + wave2) * 0.3);
        t.stroke();
      }
    }
    t.globalAlpha = 1;

    corkTile = tile;
    return tile;
  }

  function drawCork() {
    const canvas = els.cork;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const box = sceneSize();          // 同样按 #scene 的尺寸来，不然纹理也会被拉伸
    const W = box.w;
    const H = box.h;
    canvas.width = Math.max(1, Math.round(W * dpr));
    canvas.height = Math.max(1, Math.round(H * dpr));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // 无限软木板：纹理铺满整个视野，拖到哪儿都还是墙的样子
    const pattern = ctx.createPattern(buildCorkTile(), "repeat");
    if (pattern.setTransform && typeof DOMMatrix === "function") {
      const m = new DOMMatrix();
      m.a = view.scale;
      m.d = view.scale;
      m.e = view.x;
      m.f = view.y;
      pattern.setTransform(m);
    }
    ctx.fillStyle = pattern;
    ctx.fillRect(0, 0, W, H);

    // 不画任何边界线：背景就是一块完整、无边界的软木板
    // （可漫游范围由 clampView 控制，肉眼看不到）
  }

  /* ---------------- 照片与便签的创建、渲染 ---------------- */
  function posTransform(item) {
    return "translate3d(" + item.x + "px," + item.y + "px,0) rotate(" + item.angle + "deg)";
  }

  function anchorOf(item) {
    const rad = deg2rad(item.angle);
    const cos = Math.cos(rad);
    const sin = Math.sin(rad);
    const lx = item.w / 2;   // 图钉位于卡片上缘正中
    const ly = 1.5;          // 图钉头圆心略低于卡片上边缘
    return {
      x: item.x + lx * cos - ly * sin,
      y: item.y + lx * sin + ly * cos
    };
  }

  function ctrlHtml(item) {
    const fontBtns = item && item.type === "note"
      ? '<button class="icon-btn font" data-action="font-down" title="便签字号变小">A－</button>' +
        '<button class="icon-btn font" data-action="font-up" title="便签字号变大">A＋</button>'
      : "";
    const toNoteTitle = item && item.type === "photo"
      ? "把这张图片放进简记"
      : "把这张便条写进简记";
    return '<div class="item-ctrl">' + fontBtns +
      '<button class="icon-btn to-note" data-action="to-note" title="' + toNoteTitle + '">' +
        '<svg viewBox="0 0 16 16" aria-hidden="true">' +
        '<path d="M10.8 2.3 13.7 5.2 6.1 12.8 2.6 13.4l.6-3.5z"/><path d="M9.2 3.9l2.9 2.9"/></svg>' +
      "</button>" +
      '<button class="icon-btn" data-action="delete" title="删除此线索">✕</button></div>' +
      '<div class="item-resize" data-action="resize" title="拖动改变大小"></div>';
  }

  const NOTE_FONT_DEFAULT = 15;
  const NOTE_FONT_MIN = 10;
  const NOTE_FONT_MAX = 48;

  function noteFontSize(item) {
    return Math.round(clamp(Number(item.fontSize) || NOTE_FONT_DEFAULT, NOTE_FONT_MIN, NOTE_FONT_MAX));
  }

  // 便签字号：A－ / A＋ 每点一下改 1px，自动高度会跟着重排
  function setNoteFontSize(item, size) {
    if (!item || item.type !== "note") return;
    item.fontSize = Math.round(clamp(size, NOTE_FONT_MIN, NOTE_FONT_MAX));
    if (item.textEl) {
      item.textEl.style.fontSize = item.fontSize + "px";
      applyItemSize(item);
    }
    saveLater();
    toast("便签字号 " + item.fontSize + "px");
  }

  /* 缩放：照片按原比例，便签只改宽度（高度随文字自动伸缩） */
  const MIN_ITEM_W = 96;
  const MIN_ITEM_H = 56;
  const NOTE_PAD_Y = 43;          // 便签上下内边距之和（27 + 16）

  function maxItemW(item) {
    return MAX_ITEM_SIZE;
  }

  function maxItemH(item) {
    return MAX_ITEM_SIZE;
  }

  function applyItemSize(item) {
    if (!item.el) return;
    item.el.style.width = Math.round(item.w) + "px";
    if (item.type === "photo") item.el.style.height = Math.round(item.h) + "px";
    if (item.type === "note") {
      if (item.hFixed) {
        // 手动定过高度：文字超了就内部滚动
        item.h = Math.max(MIN_ITEM_H, Math.min(item.h, maxItemH(item)));
        item.el.style.height = Math.round(item.h) + "px";
        if (item.textEl) {
          item.textEl.style.height = Math.max(30, item.h - NOTE_PAD_Y) + "px";
          item.textEl.style.overflowY = "auto";
        }
      } else {
        item.el.style.height = "";
        if (item.textEl) item.textEl.style.overflowY = "hidden";
        autosizeNote(item);
      }
    }
    syncPinPosition(item);
  }

  function startResize(item, e) {
    if (state.tool !== "select") return;
    const startX = e.clientX;
    const startY = e.clientY;
    const startW = item.w;
    const startH = item.type === "photo" ? item.h : (item.el ? item.el.offsetHeight : 150);
    const ratio = startH / Math.max(1, startW);

    selectItem(item);
    item.el.classList.add("resizing");
    document.body.classList.add("resizing");
    try { item.el.setPointerCapture(e.pointerId); } catch (err) { /* 个别浏览器不支持 */ }

    function onMove(ev) {
      // 自由改宽高；按住 Shift 锁住原来的横纵比（和 Office 调整图片一样）
      const dx = (ev.clientX - startX) / view.scale;
      const dy = (ev.clientY - startY) / view.scale;
      const w = clamp(startW + dx, MIN_ITEM_W, maxItemW(item));
      let h = clamp(startH + dy, MIN_ITEM_H, maxItemH(item));
      if (ev.shiftKey) h = clamp(w * ratio, MIN_ITEM_H, maxItemH(item));

      item.w = Math.round(w);
      if (item.type === "photo") {
        item.h = Math.round(h);
      } else if (ev.shiftKey || Math.abs(dy) > 6) {
        // 斜着拖（或按住 Shift）才固定便签高度，纯左右拖保持"高度随文字"
        item.h = Math.round(h);
        item.hFixed = true;
      }
      applyItemSize(item);
      ev.preventDefault();
    }

    function onUp() {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      item.el.classList.remove("resizing");
      document.body.classList.remove("resizing");
      saveLater();
    }

    window.addEventListener("pointermove", onMove, { passive: false });
    window.addEventListener("pointerup", onUp, { passive: false });
    e.preventDefault();
    e.stopPropagation();
  }

  function buildItemElement(item) {
    const el = document.createElement("div");
    el.className = "item " + item.type;
    el.dataset.id = item.id;
    el.style.width = Math.round(item.w) + "px";
    if (item.type === "photo") el.style.height = Math.round(item.h) + "px";
    el.style.transform = posTransform(item);

    if (item.type === "photo") {
      el.innerHTML =
        '<div class="photo-surface"><img class="photo-img" alt="线索照片" draggable="false"></div>' +
        ctrlHtml(item);
      item.imgEl = el.querySelector(".photo-img");
      item.surfaceEl = el.querySelector(".photo-surface");
    } else {
      el.innerHTML = ctrlHtml(item);
      const ta = document.createElement("textarea");
      ta.className = "note-text";
      ta.placeholder = "双击输入线索……";
      ta.readOnly = true;
      ta.spellcheck = false;
      ta.value = item.text || "";
      ta.style.fontSize = noteFontSize(item) + "px";
      item.textEl = ta;
      el.insertBefore(ta, el.firstChild);
      applyItemSize(item);          // 便签：按文字自适应，或按保存的固定高度
      // 失焦时直接提交编辑内容（不依赖 focusout 冒泡，兼容性更好）
      item.textEl.addEventListener("blur", () => {
        if (runtime.editingId === item.id) endEdit(item);
      });
      item.textEl.addEventListener("input", () => {
        item.draft = item.textEl.value;
        item.draftChanged = true;
        autosizeNote(item);
      });
    }
    item.el = el;
    els.items.appendChild(el);
    byId.set(item.id, item);
    createPinOverlay(item);
    return item;
  }

  function autosizeNote(item) {
    if (!item.textEl) return;
    if (item.hFixed) return;          // 手动定过大小就不再自动撑高
    const ta = item.textEl;
    ta.style.height = "auto";
    ta.style.height = Math.max(104, (ta.scrollHeight || 104) + 2) + "px";
  }

  /* 把所有「自动高度」的便签重新撑一遍。
     创建那一刻字体/排版可能还没就绪，量出来的高度会偏小 → 文字被切掉；
     等字体加载完、以及加载后再补两次，保证一打开就能看全。 */
  function autosizeAllNotes() {
    state.items.forEach((it) => {
      if (it.type === "note" && !it.hFixed && it.textEl) autosizeNote(it);
    });
  }

  function createPinOverlay(item) {
    const pin = document.createElement("button");
    pin.type = "button";
    pin.className = "pin-overlay";
    pin.dataset.id = item.id;
    pin.title = "点击此图钉开始连接线索";
    pin.innerHTML =
      '<span class="pin-head"></span>' +
      '<span class="pin-cone" aria-hidden="true"></span>' +
      '<span class="pin-shade" aria-hidden="true"></span>';
    els.pins.appendChild(pin);
    item.pinEl = pin;
    syncPinPosition(item);
  }

  function syncPinPosition(item) {
    if (!item.pinEl) return;
    const a = anchorOf(item);
    item.pinEl.style.left = Math.round((a.x - 9) * 10) / 10 + "px";
    item.pinEl.style.top = Math.round((a.y - 8) * 10) / 10 + "px";
  }

  function applyPhotoSource(item) {
    if (!item.imgEl) return;
    if (item._url) {
      item.imgEl.src = item._url;
    } else if (item.dataUrl) {
      item.imgEl.src = item.dataUrl;
    }
  }

  function randomAngle() {
    return rand(-3.4, 3.4);
  }

  function freeSpot(w, h, preferX, preferY) {
    // 在当前可见的世界区域里找位置
    const vis = visibleWorldRect();
    const left = vis.x + 24;
    const top = vis.y + 86;
    const right = Math.max(left, vis.x + vis.w - w - 24);
    const bottom = Math.max(top, vis.y + vis.h - h - 58);

    if (typeof preferX === "number" && typeof preferY === "number") {
      const p = screenToWorld({ x: preferX, y: preferY });
      return {
        x: clamp(p.x - w / 2, left, right),
        y: clamp(p.y - h / 2, top, bottom)
      };
    }

    const start = {
      x: left + (right - left) * 0.42 + runtime.demoCounter * 14,
      y: top + (bottom - top) * 0.30 + runtime.demoCounter * 16
    };
    for (let tryN = 0; tryN < 90; tryN++) {
      let x, y;
      if (tryN === 0) { x = start.x; y = start.y; }
      else {
        x = rand(left, right);
        y = rand(top, bottom);
      }
      let ok = true;
      for (const it of state.items) {
        if (it.x - 22 < x + w && x < it.x + it.w + 22 &&
            it.y - 22 < y + (it.h || 140) && y < it.y + (it.h || 140) + 22) {
          ok = false;
          break;
        }
      }
      if (ok) return { x, y };
    }
    return { x: rand(left, right), y: rand(top, bottom) };
  }

  function readImageMeta(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => resolve({ w: img.naturalWidth, h: img.naturalHeight, url });
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("无法读取图片")); };
      img.src = url;
    });
  }

  async function addPhotoFromFile(file, dropX, dropY) {
    if (!file.type.startsWith("image/")) {
      toast("只支持图片文件");
      return;
    }
    let meta;
    try { meta = await readImageMeta(file); }
    catch (e) { toast("图片读取失败：" + file.name); return; }

    const scale = Math.min(1, 320 / meta.w, 360 / meta.h);
    const dispW = Math.max(24, Math.round(meta.w * scale));
    const dispH = Math.max(24, Math.round(meta.h * scale));
    const frame = 18; // 拍立得白边

    const item = {
      id: uid(),
      type: "photo",
      x: 0,
      y: 0,
      angle: randomAngle(),
      w: dispW + frame,
      h: dispH + frame,
      _url: meta.url,
      blobReady: false
    };
    const spot = freeSpot(item.w, item.h, dropX, dropY);
    item.x = spot.x;
    item.y = spot.y;

    state.items.push(item);
    buildItemElement(item);
    applyPhotoSource(item);
    updateStats();
    updateHint();

    // 图片存入 IndexedDB；若不可用则退回 base64
    let stored = false;
    if (runtime.db) {
      stored = await dbPut(item.id, file);
    }
    if (!stored && !runtime.noDB) {
      stored = await dbPut(item.id, file);
    }
    if (!stored) {
      try {
        const dataUrl = await fileToDataUrl(file);
        item.dataUrl = dataUrl;
        if (item._url) { URL.revokeObjectURL(item._url); item._url = null; }
        applyPhotoSource(item);
      } catch (e) {
        deleteItem(item, false);
        toast("图片保存失败，已取消添加");
        return;
      }
    }
    item.blobReady = true;
    saveLater();
  }

  function addNote(text, w, preferX, preferY, focusIt) {
    const width = w || 220;
    const spot = freeSpot(width, 130, preferX, preferY);
    const item = {
      id: uid(),
      type: "note",
      text: text || "",
      x: spot.x,
      y: spot.y,
      angle: randomAngle(),
      w: width
    };
    state.items.push(item);
    buildItemElement(item);
    if (focusIt) beginEdit(item);
    updateStats();
    updateHint();
    saveLater();
    return item;
  }

  function beginEdit(item) {
    if (!item || item.type !== "note" || !item.textEl) return;
    if (runtime.editingId === item.id) {
      item.textEl.focus();
      try { item.textEl.setSelectionRange(item.textEl.value.length, item.textEl.value.length); } catch (e) { /* ignore */ }
      return;
    }
    if (runtime.editingId) {
      const prev = byId.get(runtime.editingId);
      if (prev) endEdit(prev);
    }
    item.draft = item.text;
    item.draftChanged = false;
    runtime.editingId = item.id;
    selectItem(item);
    item.textEl.readOnly = false;
    item.textEl.classList.add("editing");
    item.textEl.focus();
    try {
      item.textEl.setSelectionRange(item.textEl.value.length, item.textEl.value.length);
    } catch (e) { /* ignore */ }
  }

  function endEdit(item, commit = true) {
    if (!item || !item.textEl) return;
    if (runtime.editingId === item.id) runtime.editingId = null;
    if (commit) {
      // textarea 的值不会在失焦时被浏览器清空，直接读取即可
      item.text = item.textEl.value;
    } else {
      item.text = item.text || "";
    }
    item.textEl.value = item.text;
    autosizeNote(item);
    item.textEl.readOnly = true;
    item.textEl.classList.remove("editing");
    item.draftChanged = false;
    saveLater();
  }

  function deleteItem(item, immediateSave) {
    if (!item || !item.el || !item.el.parentNode) return;
    if (runtime.armedId === item.id) disarmPin();
    if (runtime.editingId === item.id) runtime.editingId = null;
    if (item._url) { URL.revokeObjectURL(item._url); item._url = null; }
    if (item.pinEl) {
      item.pinEl.remove();
      item.pinEl = null;
    }

    // 删除与此物件的所有连线
    const dead = state.links.filter((l) => l.from === item.id || l.to === item.id);
    dead.forEach((l) => {
      ropes.delete(l.id);
    });
    state.links = state.links.filter((l) => l.from !== item.id && l.to !== item.id);
    item.el.remove();
    state.items = state.items.filter((it) => it.id !== item.id);
    byId.delete(item.id);
    if (runtime.db) dbDel(item.id);
    updateStats();
    updateHint();
    renderStrings(); // 立即把画布上的残留连线清掉，不等下一帧
    if (immediateSave !== false) saveLater();
  }

  function selectItem(item) {
    state.items.forEach((it) => {
      if (it.el) it.el.classList.toggle("selected", it.id === item.id);
    });
  }

  function clearSelection() {
    state.items.forEach((it) => {
      if (it.el) it.el.classList.remove("selected");
    });
  }

  /* ---------------- 图钉与连线 ---------------- */
  function disarmPin() {
    if (!runtime.armedId) return;
    const from = byId.get(runtime.armedId);
    if (from && from.pinEl) from.pinEl.classList.remove("armed");
    runtime.armedId = null;
    updateHint();
  }

  function armPin(item) {
    disarmPin();
    runtime.armedId = item.id;
    item.pinEl.classList.add("armed");
    selectItem(item);
    updateHint();
  }

  /* ---------------- 剪线模式：点两个「连着的」工字钉 = 取消它们之间那根线 ----------------
     第一个点中的工字钉会变成蓝色（提醒「已经抓住一个了」），点第二个就把线删掉，
     然后把两个工字钉都恢复原色。 */
  function disarmCut() {
    if (!runtime.cutArmed) return;
    const item = byId.get(runtime.cutArmed);
    if (item && item.pinEl) item.pinEl.classList.remove("cut-armed");
    runtime.cutArmed = null;
    updateHint();
  }

  function armCut(item) {
    disarmCut();
    runtime.cutArmed = item.id;
    if (item.pinEl) item.pinEl.classList.add("cut-armed");
    updateHint();
  }

  function linkBetween(idA, idB) {
    return state.links.find((l) =>
      (l.from === idA && l.to === idB) || (l.from === idB && l.to === idA)) || null;
  }

  function handleCutPinClick(e, item) {
    e.preventDefault();
    if (!runtime.cutArmed) { armCut(item); return; }
    if (runtime.cutArmed === item.id) { disarmCut(); return; }
    const link = linkBetween(runtime.cutArmed, item.id);
    if (!link) {
      toast("这两个工字钉之间没有连线");
      disarmCut();
      return;
    }
    deleteLinkById(link.id);
    disarmCut();
    toast("已取消这两枚工字钉之间的连线");
  }

  function handlePinClick(e, item) {
    if (state.tool === "cut") { handleCutPinClick(e, item); return; }
    e.preventDefault();
    if (!runtime.armedId) {
      armPin(item);
      return;
    }
    if (runtime.armedId === item.id) {
      disarmPin();
      return;
    }
    const from = runtime.armedId;
    const duplicate = state.links.some((l) =>
      (l.from === from && l.to === item.id) || (l.from === item.id && l.to === from)
    );
    if (duplicate) {
      toast("这两个图钉之间已经连了一根线");
      return;
    }
    const link = {
      id: "L" + uid(),
      from: from,
      to: item.id,
      color: state.color
    };
    state.links.push(link);
    buildRope(link);
    if (!e.shiftKey) disarmPin();
    updateStats();
    updateHint();
    saveLater();
  }

  function deleteLinkById(id) {
    const idx = state.links.findIndex((l) => l.id === id);
    if (idx < 0) return false;
    state.links.splice(idx, 1);
    ropes.delete(id);
    updateStats();
    updateHint();
    saveLater();
    return true;
  }

  function exitCut() {
    if (state.tool !== "cut") return;
    disarmCut();                 // 退出剪线模式时，把蓝色的那枚工字钉恢复原色
    state.tool = "select";
    updateToolbar();
    updateHint();
  }

  /* ---------------- Verlet 绳线物理 ---------------- */
  function buildRope(link) {
    const aItem = byId.get(link.from);
    const bItem = byId.get(link.to);
    if (!aItem || !bItem) return null;
    const a = anchorOf(aItem);
    const b = anchorOf(bItem);
    const d = Math.max(2, dist(a.x, a.y, b.x, b.y));

    // 粒子数随跨度增加，保证曲线平滑
    const M = clamp(Math.round(d / 17) + 2, 6, 46);
    // 预留一小段下垂/拉扯的余量（像真实棉线那样有一点松量）
    const extra = Math.min(200, 26 + d * 0.10);
    const seg = (d + extra) / (M - 1);

    const p = [];
    const prev = [];
    for (let i = 0; i < M; i++) {
      const t = i / (M - 1);
      p.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
      prev.push({ x: p[i].x, y: p[i].y });
    }
    const rope = { linkId: link.id, p, prev, M, seg, len: d + extra };
    ropes.set(link.id, rope);
    // 新绳子也要「活」起来：不然它会一直是一条笔直线，等你拖动别的东西才突然垂下来。
    // （导入 / 载入时重建的绳子同理 —— 打开就是自然垂着的。）
    wakeRopes();
    return rope;
  }

  /* 绳子存的是**世界坐标**（和照片/便签同一套坐标系），画的时候才换算到屏幕。
     这样缩放、平移画布时，线条只是跟着一起被「看」到，形状一点都不会变；
     物理也只在图钉动过之后跑一小会儿（见 wakeRopes）。 */
  let ropeWakeUntil = 0;

  function wakeRopes(ms) {
    const until = performance.now() + (ms || 1400);
    if (until > ropeWakeUntil) ropeWakeUntil = until;
    if (!ropeAwakeSince) ropeAwakeSince = performance.now();
  }

  let ropeAwakeSince = 0;          // 这一轮物理是什么时候被唤醒的
  const ROPE_MAX_AWAKE = 2500;     // 最多连续跑 2.5 秒：之后一律停下（下垂早就稳了，剩下的是肉眼看不见的微动）

  function ropesSleeping() {
    return performance.now() >= ropeWakeUntil;
  }

  /* 连续多少帧几乎没动 → 直接睡（不用等满唤醒时间） */
  let ropeStillFrames = 0;
  function noteRopeMotion(move) {
    // 0.5px/帧 以下肉眼看不出来，就算「不动了」
    if (move > 0.5) {
      ropeStillFrames = 0;
      wakeRopes(300);                 // 还在晃 → 再多跑一会儿
      return;
    }
    ropeStillFrames += 1;
    if (ropeStillFrames >= 8) ropeSleep();
  }

  function ropeSleep() {
    ropeWakeUntil = 0;
    ropeStillFrames = 0;
    ropeAwakeSince = 0;
  }

  /* 图钉动没动？动过就唤醒绳子物理。
     拖动照片/便签、改大小、改字号、旋转、增删物件都会让图钉位置变化，全都能覆盖到。 */
  function anchorsMoved() {
    let moved = false;
    for (const item of state.items) {
      const a = anchorOf(item);
      const last = item._anchorCache;
      if (!last || Math.abs(a.x - last.x) > 0.01 || Math.abs(a.y - last.y) > 0.01) moved = true;
      item._anchorCache = a;
    }
    return moved;
  }

  function stepRopes(dtNorm) {
    const G = 0.55 * dtNorm;
    let maxMove = 0;
    for (const link of state.links) {
      const rope = ropes.get(link.id);
      const aItem = byId.get(link.from);
      const bItem = byId.get(link.to);
      if (!rope || !aItem || !bItem) continue;

      const A = anchorOf(aItem);          // 世界坐标（不跟缩放走）
      const B = anchorOf(bItem);
      rope.p[0].x = A.x; rope.p[0].y = A.y;
      rope.p[rope.M - 1].x = B.x; rope.p[rope.M - 1].y = B.y;

      // 绳子的「自然长度」：比两枚图钉之间的距离长一截（才有垂度）。
      // 关键：这个长度不是瞬移过去的 —— 被拉长时很快跟上，收回来时慢慢松下去，
      // 所以快速拉扯时它不会突然绷成一条直线（那看着就像卡住了）。
      const span = Math.max(2, Math.hypot(B.x - A.x, B.y - A.y));
      const want = span * 1.10 + 24;
      if (!rope.len) rope.len = want;
      rope.len += (want - rope.len) * (want > rope.len ? 0.45 : 0.12) * Math.min(2, dtNorm);
      const seg = clamp(rope.len / (rope.M - 1), 0.8, 90);

      // Verlet：惯性 + 阻尼 + 重力
      for (let i = 1; i < rope.M - 1; i++) {
        const pt = rope.p[i];
        const pv = rope.prev[i];
        const vx = (pt.x - pv.x) * 0.982;
        const vy = (pt.y - pv.y) * 0.982;
        pv.x = pt.x;
        pv.y = pt.y;
        pt.x += vx;
        pt.y += vy + G;
      }

      // 迭代求解“线段长度不可变”约束（迭代多一点，甩起来才不会一卡一卡的）
      for (let iter = 0; iter < 14; iter++) {
        for (let i = 0; i < rope.M - 1; i++) {
          const p1 = rope.p[i];
          const p2 = rope.p[i + 1];
          let dx = p2.x - p1.x;
          let dy = p2.y - p1.y;
          let d = Math.hypot(dx, dy);
          if (d < 0.001) {
            const ang = Math.random() * Math.PI * 2;
            dx = Math.cos(ang);
            dy = Math.sin(ang);
            d = 1;
          }
          const target = seg;
          if (i === 0) {
            // 起点钉死在图钉上，只修正第 1 个粒子
            p2.x = p1.x + (dx / d) * target;
            p2.y = p1.y + (dy / d) * target;
          } else if (i === rope.M - 2) {
            // 终点钉死，只修正倒数第 2 个粒子
            p1.x = p2.x - (dx / d) * target;
            p1.y = p2.y - (dy / d) * target;
          } else {
            const half = ((d - target) / d) * 0.5;
            const ox = dx * half;
            const oy = dy * half;
            p1.x += ox; p1.y += oy;
            p2.x -= ox; p2.y -= oy;
          }
        }
      }

      // 这一步到底动了多少？用「约束解完之后的位置」和「这一步之前的位置」比，
      // 才是真实的位移（直接看速度的话，静止的绳子每帧都会被重力记成 0.55，
      // 于是永远「还在动」，物理就停不下来了）
      for (let i = 1; i < rope.M - 1; i++) {
        const pt = rope.p[i];
        const pv = rope.prev[i];
        const moved = Math.abs(pt.x - pv.x) + Math.abs(pt.y - pv.y);
        if (moved > maxMove) maxMove = moved;
      }
    }
    return maxMove;      // 给外面判断「还在晃吗」
  }

  /* ---------------- 渲染连线 ---------------- */

  /* 把绳子的两端强制钉回「图钉中心」。
     物理模拟里两端本来就钉住了，但约束求解、缩放、拖动都可能让它偏出几个像素，
     所以绘制前再钉一次，保证线头永远压在图钉上。 */
  function pinRopeEnds(rope, link) {
    if (!rope || !rope.p || !rope.p.length) return;
    const last = rope.p.length - 1;
    const aItem = byId.get(link.from);
    const bItem = byId.get(link.to);
    if (aItem) {
      const a = anchorOf(aItem);          // 世界坐标：绳子本身就存在这个世界里
      rope.p[0].x = a.x;
      rope.p[0].y = a.y;
    }
    if (bItem) {
      const b = anchorOf(bItem);
      rope.p[last].x = b.x;
      rope.p[last].y = b.y;
    }
  }

  /* 绳子是世界坐标，画到画布上要换算成屏幕坐标；
     每根线复用同一份数组，免得每帧新建对象。 */
  function screenPoints(rope) {
    if (!rope.sp || rope.sp.length !== rope.p.length) {
      rope.sp = rope.p.map(() => ({ x: 0, y: 0 }));
    }
    for (let i = 0; i < rope.p.length; i++) {
      const s = worldToScreen(rope.p[i]);
      rope.sp[i].x = s.x;
      rope.sp[i].y = s.y;
    }
    return rope.sp;
  }

  function tracePath(ctx, pts) {
    if (!pts || pts.length < 2) return;
    ctx.moveTo(pts[0].x, pts[0].y);
    if (pts.length === 2) {
      ctx.lineTo(pts[1].x, pts[1].y);
      return;
    }
    for (let i = 1; i < pts.length - 1; i++) {
      const mx = (pts[i].x + pts[i + 1].x) / 2;
      const my = (pts[i].y + pts[i + 1].y) / 2;
      ctx.quadraticCurveTo(pts[i].x, pts[i].y, mx, my);
    }
    ctx.lineTo(pts[pts.length - 1].x, pts[pts.length - 1].y);
  }

  function strokeString(ctx, pts, color, emphasized) {
    ctx.save();
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    // 绳下的阴影
    ctx.strokeStyle = "rgba(15, 6, 2, 0.32)";
    ctx.lineWidth = emphasized ? 6 : 5;
    ctx.beginPath();
    tracePath(ctx, pts);
    ctx.stroke();

    // 主线
    ctx.strokeStyle = color;
    ctx.lineWidth = emphasized ? 3.2 : 2.4;
    ctx.beginPath();
    tracePath(ctx, pts);
    ctx.stroke();

    // 棉线绞合的暗纹
    ctx.strokeStyle = "rgba(0,0,0,0.12)";
    ctx.lineWidth = emphasized ? 1.6 : 1.1;
    ctx.setLineDash([1, 7]);
    ctx.beginPath();
    tracePath(ctx, pts);
    ctx.stroke();

    // 棉线绞合的高光纹理
    ctx.strokeStyle = "rgba(255,255,255,0.16)";
    ctx.lineWidth = 0.9;
    ctx.setLineDash([3, 8]);
    ctx.beginPath();
    tracePath(ctx, pts);
    ctx.stroke();

    ctx.restore();
  }

  function ghostPath(a, b) {
    // 预览虚线：从图钉笔直拉到光标，不留垂度、不留多余长度
    const n = 10;
    const pts = [];
    for (let i = 0; i <= n; i++) {
      const t = i / n;
      const x = a.x + (b.x - a.x) * t;
      const y = a.y + (b.y - a.y) * t;
      pts.push({ x, y });
    }
    return pts;
  }

  function renderStrings() {
    const cv = els.strings;
    const ctx = cv.getContext("2d");
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const box = sceneSize();
    ctx.clearRect(0, 0, box.w, box.h);

    // 已创建的连线
    for (const link of state.links) {
      const rope = ropes.get(link.id);
      if (!rope) continue;
      // 画之前把两端重新钉回图钉中心：物理约束偶尔会把端点拽偏一点，
      // 不重新钉的话，绳头就会和图钉差开几个像素（看着像没连上）。
      pinRopeEnds(rope, link);
      strokeString(ctx, screenPoints(rope), link.color, false);
    }

    // 连线预览（已点亮一枚图钉后跟随鼠标）
    if (runtime.armedId) {
      const fromItem = byId.get(runtime.armedId);
      if (fromItem && runtime.mouse.x > -9000) {
        // 图钉锚点是世界坐标，画在屏幕画布上要先换算
        const a = worldToScreen(anchorOf(fromItem));
        const pts = ghostPath(a, runtime.mouse);
        ctx.save();
        ctx.globalAlpha = 0.65;
        ctx.strokeStyle = state.color;
        ctx.lineWidth = 2.2;
        ctx.setLineDash([7, 7]);
        ctx.lineCap = "round";
        ctx.beginPath();
        tracePath(ctx, pts);
        ctx.stroke();
        ctx.restore();
      }
    }
  }

  /* ---------------- 点线命中检测（剪断模式） ---------------- */
  function pointSegDist(px, py, ax, ay, bx, by) {
    const dx = bx - ax;
    const dy = by - ay;
    const len2 = dx * dx + dy * dy;
    let t = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
    t = clamp(t, 0, 1);
    return dist(px, py, ax + dx * t, ay + dy * t);
  }

  /* ---------------- 线色：一个色块按钮 + 弹出调色板 ---------------- */

  const RECENT_KEY = "linkchart.recentColors.v1";
  const RECENT_MAX = 5;

  function readRecentColors() {
    try {
      const raw = localStorage.getItem(RECENT_KEY);
      const list = raw ? JSON.parse(raw) : [];
      return Array.isArray(list)
        ? list.filter((v) => typeof v === "string" && v).slice(0, RECENT_MAX)
        : [];
    } catch (e) {
      return [];
    }
  }

  function rememberColor(value) {
    const rest = readRecentColors().filter((v) => v.toLowerCase() !== String(value).toLowerCase());
    const list = [value, ...rest].slice(0, RECENT_MAX);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch (e) { /* 隐私模式忽略 */ }
  }

  function ballStyle(value) {
    return "radial-gradient(circle at 34% 30%, rgba(255,255,255,0.35), "
      + value + " 58%, rgba(0,0,0,0.25) 130%)";
  }

  function colorBall(value, title, onClick, extraClass) {
    const ball = document.createElement("button");
    ball.type = "button";
    ball.className = "cp-dot" + (extraClass ? " " + extraClass : "");
    ball.style.background = ballStyle(value);
    ball.title = title || value;
    ball.dataset.color = value;
    ball.setAttribute("aria-label", title || value);
    ball.addEventListener("click", (e) => {
      e.stopPropagation();
      onClick(value);
    });
    return ball;
  }

  function renderColorPick() {
    if (!els.colorSwatch) return;
    els.colorSwatch.style.background = ballStyle(state.color);
    els.colorBtn.title = "当前线色：" + colorName(state.color) + "（点这里换颜色）";

    els.cpRecent.innerHTML = "";
    const recent = readRecentColors();
    if (!recent.length) {
      const tip = document.createElement("span");
      tip.className = "cp-empty";
      tip.textContent = "还没用过颜色";
      els.cpRecent.appendChild(tip);
    } else {
      recent.forEach((value) => {
        els.cpRecent.appendChild(colorBall(
          value, value, (v) => chooseColor(v, true),
          state.color.toLowerCase() === value.toLowerCase() ? "sel" : ""));
      });
    }

    els.cpPreset.innerHTML = "";
    COLORS.forEach((c) => {
      els.cpPreset.appendChild(colorBall(
        c.value, c.name, (v) => chooseColor(v, true),
        state.color.toLowerCase() === c.value.toLowerCase() ? "sel" : ""));
    });

    els.cpCustom.value = /^#[0-9a-f]{6}$/i.test(state.color) ? state.color : "#e23b3b";
  }

  function chooseColor(value, remember) {
    state.color = value;
    if (remember) rememberColor(value);
    renderColorPick();
    saveLater();
    updateHint();
  }

  function openColorPop() {
    renderColorPick();
    const box = els.colorPop;
    box.hidden = false;
    els.colorBtn.classList.add("open");
    // 贴着按钮下方弹出，右边不会跑出屏幕
    const btn = els.colorBtn.getBoundingClientRect();
    const width = box.offsetWidth || 240;
    const left = clamp(btn.left, 8, Math.max(8, window.innerWidth - width - 8));
    box.style.left = left + "px";
    box.style.top = (btn.bottom + 6) + "px";
  }

  function closeColorPop() {
    if (!els.colorPop || els.colorPop.hidden) return;
    els.colorPop.hidden = true;
    els.colorBtn.classList.remove("open");
  }

  function toggleColorPop() {
    if (els.colorPop.hidden) openColorPop();
    else closeColorPop();
  }

  /* ---------------- 工具栏自适应 ----------------
     窗口不够宽的时候不再出现横向滚动条，而是把按钮收成"只有图标"的小方块
     （和简记窗口里那排按钮一个样式）。 */
  function fitToolbar() {
    if (!els.tools) return;
    const fits = () => els.tools.scrollWidth <= els.tools.clientWidth + 2;
    document.body.classList.remove("tools-compact", "tools-tight");
    if (fits()) return;
    document.body.classList.add("tools-compact");
    if (fits()) return;
    document.body.classList.add("tools-tight");
  }

  function updateToolbar() {
    els.toolSelect.classList.toggle("active", state.tool === "select");
    els.toolCut.classList.toggle("active", state.tool === "cut");
    document.body.classList.toggle("mode-cut", state.tool === "cut");
  }

  function updateStats() {
    const photos = state.items.filter((i) => i.type === "photo").length;
    const notes = state.items.filter((i) => i.type === "note").length;
    els.stats.textContent =
      "照片 " + photos + " · 便签 " + notes + " · 连线 " + state.links.length;
  }

  function updateHint() {
    let text;
    if (state.tool === "cut") {
      text = runtime.cutArmed
        ? "✂ 已抓住一枚工字钉（蓝色）—— 再点跟它连着的那一枚，就能取消这根连线"
        : "✂ 剪线模式已开启 —— 点两个连着的工字钉就能取消它们之间的连线（点空白处退出）";
    } else if (runtime.armedId) {
      const item = byId.get(runtime.armedId);
      text =
        (item ? "已选中图钉 " : "图钉 ") +
        "· 线色「" + colorName(state.color) + "」。点击另一枚图钉完成连线；按住 Shift 可连续引出多根线；Esc 取消。";
    } else {
      text =
        "拖空白处平移画布 · 滚轮或右下角缩放轴缩放 · 拖动照片/便签摆位 · 拖右下角小圆点改大小（按住 Shift 锁比例）";
    }
    els.hintText.textContent = text;
    els.hint.title = text;
  }

  // 收起 / 展开底部提示条（收起后留一个小问号可以叫回来）
  function setHintClosed(closed) {
    els.hint.classList.toggle("closed", closed);
    els.hintRestore.classList.toggle("show", closed);
    runtime.hintClosed = closed;
    try { localStorage.setItem(HINT_KEY, closed ? "1" : "0"); } catch (e) { /* 隐私模式忽略 */ }
  }

  function toast(msg, ms) {
    els.toast.textContent = msg;
    els.toast.classList.add("show");
    clearTimeout(runtime.toastTimer);
    runtime.toastTimer = setTimeout(() => els.toast.classList.remove("show"), ms || 2600);
  }

  /* ---------------- 应用内确认框 ----------------
     不用系统弹窗（window.confirm）：简记窗口只有 360px 宽，
     系统那个确认框会被切掉，"确定"按钮根本点不到。
     这里自己画一个，永远跟着窗口大小走。 */
  function appConfirm(message, okText, opts) {
    return new Promise((resolve) => {
      const box = document.createElement("div");
      box.className = "app-confirm";
      box.innerHTML =
        '<div class="ac-panel">' +
        '<div class="ac-text"></div>' +
        '<div class="ac-btns">' +
        '<button class="ac-btn" data-act="cancel" type="button">取消</button>' +
        '<button class="ac-btn danger" data-act="ok" type="button"></button>' +
        "</div></div>";
      box.querySelector(".ac-text").textContent = message;
      const okBtn = box.querySelector('[data-act="ok"]');
      okBtn.textContent = okText || "确定";
      if (opts && opts.hideCancel) box.querySelector('[data-act="cancel"]').hidden = true;

      function finish(val) {
        box.remove();
        window.removeEventListener("keydown", onKey, true);
        resolve(val);
      }
      function onKey(e) {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
        else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); finish(true); }
      }
      okBtn.addEventListener("click", () => finish(true));
      box.querySelector('[data-act="cancel"]').addEventListener("click", () => finish(false));
      box.addEventListener("click", (e) => { if (e.target === box) finish(false); });
      window.addEventListener("keydown", onKey, true);
      document.body.appendChild(box);
      requestAnimationFrame(() => okBtn.focus());
    });
  }
  window.appConfirm = appConfirm;

  /* 只报个信的弹窗（只有一个按钮）——同样是自己画的，窄窗口里也点得到 */
  function appNotify(message, okText) {
    return appConfirm(message, okText || "知道了", { hideCancel: true });
  }
  window.appNotify = appNotify;

  /* 三选一的弹窗：返回 "ok" / "alt" / "cancel"（Esc 或点背景 = cancel） */
  function appAsk(opts) {
    const o = opts || {};
    return new Promise((resolve) => {
      const box = document.createElement("div");
      box.className = "app-confirm";
      box.innerHTML =
        '<div class="ac-panel">' +
        '<div class="ac-text"></div>' +
        '<div class="ac-btns">' +
        '<button class="ac-btn" data-act="cancel" type="button"></button>' +
        '<button class="ac-btn ghost" data-act="alt" type="button"></button>' +
        '<button class="ac-btn danger" data-act="ok" type="button"></button>' +
        "</div></div>";
      box.querySelector(".ac-text").textContent = o.text || "";
      box.querySelector('[data-act="cancel"]').textContent = o.cancel || "取消";
      box.querySelector('[data-act="alt"]').textContent = o.alt || "确定";
      box.querySelector('[data-act="ok"]').textContent = o.ok || "确定";

      let done = false;
      function finish(val) {
        if (done) return;
        done = true;
        box.remove();
        window.removeEventListener("keydown", onKey, true);
        resolve(val);
      }
      function onKey(e) {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish("cancel"); }
      }
      Array.prototype.forEach.call(box.querySelectorAll(".ac-btn"), (b) => {
        b.addEventListener("click", () => finish(b.getAttribute("data-act")));
      });
      box.addEventListener("click", (e) => { if (e.target === box) finish("cancel"); });
      window.addEventListener("keydown", onKey, true);
      document.body.appendChild(box);
      requestAnimationFrame(() => box.querySelector('[data-act="cancel"]').focus());
    });
  }

  /* ---------------- 「一轮 = 一张白纸」+ 关闭前确认 ---------------- */

  let savedBaseline = null;      // 「已经存过」的样子（启动 / 导出 / 导入之后）
  let closeAsking = false;       // 正在问「要不要关」，别问第二遍

  function stateSnapshot() {
    const noteState = (window.NoteApp && typeof NoteApp.stateForDiff === "function")
      ? NoteApp.stateForDiff() : null;
    return JSON.stringify({
      title: state.caseTitle,
      color: state.color,
      items: state.items.map((it) => [
        it.id, it.type,
        Math.round(it.x), Math.round(it.y), Math.round((it.angle || 0) * 100),
        Math.round(it.w),
        // 便签的高度是会自己长的（字体加载完自动撑高），那不算「用户改过」；
        // 只有手动定过高度的便签（hFixed）才把它算进去
        Math.round(it.hFixed ? (it.h || 0) : (it.type === "photo" ? (it.h || 0) : 0)),
        it.text || "", it.fontSize || 0
      ]),
      links: state.links.map((l) => [l.id, l.from, l.to, l.color]),
      notes: noteState
    });
  }

  function hasAnything() {
    if (state.items.length || state.links.length) return true;
    return !!(window.NoteApp && typeof NoteApp.hasContent === "function" && NoteApp.hasContent());
  }

  function markClean() { savedBaseline = stateSnapshot(); }

  function isDirty() {
    if (!hasAnything()) return false;        // 空白的墙没什么可保存的
    return stateSnapshot() !== savedBaseline;
  }

  async function sessionInfo() {
    try {
      const res = await fetch("/api/session", { cache: "no-store" });
      if (res.ok) return await res.json();
    } catch (e) { /* 没有本地服务（直接打开 html）→ 当普通网页用 */ }
    return null;
  }

  /* 上次关掉程序时留下的东西：整包备份到数据目录，再把这一轮清成白纸 */
  /* 备份用的打包：不看内存里的 state，直接从「上次存下来的那份数据」打包 ——
     启动这一刻内存里还是空的，用 state 会包出一份空文件。 */
  async function buildStoredPayload() {
    let wall = null;
    let notes = null;
    try { wall = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); } catch (e) { wall = null; }
    try { notes = JSON.parse(localStorage.getItem("bubblework.notes.v1") || "null"); } catch (e) { notes = null; }
    if (!wall && !notes) return null;

    const assets = {};
    for (const it of ((wall && wall.items) || [])) {
      if (!it || it.type !== "photo") continue;
      try {
        const blob = await dbGet(it.id);
        const url = blob ? await fileToDataUrl(blob) : (it.dataUrl || null);
        if (url) assets[it.id] = url;
      } catch (e) { /* 这一张读不出来就跳过，别把整份备份弄没 */ }
    }
    const blocks = (notes && Array.isArray(notes.blocks)) ? notes.blocks : [];
    const noteAssets = {};
    for (const b of blocks) {
      if (!b || b.type !== "image" || !b.key) continue;
      try {
        const blob = await dbGet(b.key);
        const url = blob ? await fileToDataUrl(blob) : null;
        if (url) noteAssets[b.key] = url;
      } catch (e) { /* 同上 */ }
    }

    const payload = {
      app: "linkchart-wall",
      format: 2,
      savedAt: new Date().toISOString(),
      caseTitle: (wall && wall.caseTitle) || "未命名事件",
      color: (wall && wall.color) || "#e23b3b",
      items: (wall && wall.items) || [],
      links: (wall && wall.links) || [],
      assets
    };
    if (notes) {
      payload.notes = {
        title: notes.title || "未命名事件",
        font: typeof notes.font === "number" ? notes.font : 100,
        blocks, assets: noteAssets
      };
    }
    return payload;
  }

  async function startFreshRun(session) {
    try {
      const payload = await buildStoredPayload();
      if (payload) {
        const res = await fetch("/api/backup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        if (res && res.ok) {
          const info = await res.json().catch(() => null);
          if (info && info.file) console.info("上一轮没保存的内容已备份到：" + info.file);
        }
      }
    } catch (e) { /* 备份失败也不能拦着清空 */ }

    try { localStorage.removeItem(STORAGE_KEY); } catch (e) { /* 忽略 */ }
    try { localStorage.removeItem("bubblework.notes.v1"); } catch (e) { /* 忽略 */ }
    await dbClearExcept([LAST_DIR_KEY]);
    if (window.NoteApp && typeof NoteApp.reset === "function") NoteApp.reset();
    try { localStorage.setItem(RUN_KEY, session); } catch (e) { /* 忽略 */ }
  }

  /* 关窗口：还有没保存的东西就先问一句 */
  function closeApp(force) {
    const api = bridgeApi("close");
    if (api) {
      try { api(!!force); } catch (e) { /* 桥坏了就走下面 */ }
      return;
    }
    window.close();
  }

  async function requestClose() {
    if (closeAsking) return;
    const dirty = isDirty();
    if (window.__pageStep) window.__pageStep("收到关闭请求：未保存=" + (dirty ? "有" : "没有"));
    if (!dirty) { closeApp(true); return; }
    closeAsking = true;
    try {
      const pick = await appAsk({
        text: "未保存内容将丢失，确定退出吗？",
        cancel: "取消",
        alt: "不保存",
        ok: "另存为"
      });
      if (pick === "cancel") return;
      if (pick === "ok") {
        const done = await exportWall();
        if (!done) return;                    // 用户在选择保存位置时点了取消
      }
      closeApp(true);
    } finally {
      closeAsking = false;
    }
  }
  window.AppClose = { request: requestClose, isDirty: isDirty, markClean: markClean };

  /* ---------------- 对外接口（供破译面板调用） ---------------- */
  window.LinkChart = {
    // 把一段文字作为便签钉到墙上（自动找一个空位）
    addNote: function (text, width) {
      const note = addNote(text, width || 236, null, null, false);
      return note ? note.id : null;
    },
    // 在墙中央附近钉便签，并把它选中，方便立刻拖动
    addNoteCentered: function (text, width) {
      const note = addNote(text, width || 236, window.innerWidth * 0.42, window.innerHeight * 0.45, false);
      if (note) {
        selectItem(note);
        toast("已把破译结果钉到墙上，拖动它即可摆位");
      }
      return note ? note.id : null;
    },
    // 把一张图片（简记里的图片 / 别处拿到的 blob）贴到墙上，返回新照片的 id
    addPhotoBlob: async function (blob, name) {
      if (!blob) return null;
      let file = blob;
      try {
        if (typeof File === "function" && !(blob instanceof File)) {
          file = new File([blob], name || "图片.png", { type: blob.type || "image/png" });
        }
      } catch (e) { file = blob; }
      const before = state.items.length;
      await addPhotoFromFile(file, window.innerWidth * 0.4, window.innerHeight * 0.42);
      return state.items.length > before ? state.items[state.items.length - 1].id : null;
    },
    toast: toast,
    version: "1.0"
  };

  /* ---------------- 拖动 ---------------- */
  function setItemPos(item, x, y) {
    item.x = x;
    item.y = y;
    item.el.style.transform = posTransform(item);
    syncPinPosition(item);
  }

  function startDrag(item, e) {
    if (state.tool !== "select") return;
    if (runtime.editingId === item.id) return;
    const startX = e.clientX;
    const startY = e.clientY;
    const baseX = item.x;
    const baseY = item.y;
    const pending = { item, started: false };
    runtime.pendingDrag = pending;

    function onMove(ev) {
      runtime.mouse.x = ev.clientX - sceneOrigin.x;      // 存画布内部坐标（虚线要跟光标对齐）
      runtime.mouse.y = ev.clientY - sceneOrigin.y;
      if (runtime.pendingDrag !== pending) return;
      if (!pending.started) {
        if (dist(ev.clientX, ev.clientY, startX, startY) < 4) return;
        pending.started = true;
        runtime.drag = { item };
        try { item.el.setPointerCapture(ev.pointerId); } catch (err) { /* 个别浏览器不支持 */ }
        document.body.classList.add("dragging");
        item.el.classList.add("dragging");
      }
      // 屏幕位移换算回"世界"位移（缩放后 1px 屏幕 ≠ 1px 世界）
      const nx = baseX + (ev.clientX - startX) / view.scale;
      const ny = baseY + (ev.clientY - startY) / view.scale;
      // 贴到墙边就停住：不让照片/便签被拖出木板
      const spot = clampItemToBoard(item, nx, ny);
      setItemPos(item, spot.x, spot.y);
      ev.preventDefault();
    }

    function onUp(ev) {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      if (runtime.pendingDrag === pending) runtime.pendingDrag = null;
      if (runtime.drag && runtime.drag.item === item) {
        runtime.drag = null;
        document.body.classList.remove("dragging");
        item.el.classList.remove("dragging");
        saveLater();
      }
    }

    window.addEventListener("pointermove", onMove, { passive: false });
    window.addEventListener("pointerup", onUp, { passive: false });
  }

  /* ---------------- 事件绑定 ---------------- */
  function onItemsPointerDown(e) {
    if (state.tool === "cut") return;
    const itemEl = e.target.closest(".item");
    if (!itemEl) return;
    const item = byId.get(itemEl.dataset.id);
    if (!item) return;
    if (e.target.closest(".icon-btn")) return;
    if (e.button !== 0) return;
    if (e.target.closest(".item-resize")) { startResize(item, e); return; }
    if (runtime.editingId === item.id) return;
    selectItem(item);
    startDrag(item, e);
  }

  function onItemsClick(e) {
    const itemEl = e.target.closest(".item");
    if (!itemEl) return;
    const item = byId.get(itemEl.dataset.id);
    if (!item) return;

    const delBtn = e.target.closest('[data-action="delete"]');
    if (delBtn) {
      deleteItem(item);
      toast("已删除此线索");
      return;
    }

    if (e.target.closest('[data-action="to-note"]')) {
      toNote(item);
      return;
    }

    if (e.target.closest('[data-action="font-up"]')) {
      setNoteFontSize(item, noteFontSize(item) + 1);
      return;
    }
    if (e.target.closest('[data-action="font-down"]')) {
      setNoteFontSize(item, noteFontSize(item) - 1);
      return;
    }
  }

  /* ---------------- 「放进简记」：墙上的照片 / 便条 → 简记 ---------------- */

  // 取出墙上某张照片的原始数据（优先本机图片库，其次内嵌 dataURL / blob 地址）
  async function wallPhotoBlob(item) {
    if (!item) return null;
    if (runtime.db) {
      const blob = await dbGet(item.id);
      if (blob) return blob;
    }
    if (item.dataUrl) {
      try { return dataUrlToBlob(item.dataUrl); } catch (e) { /* 试试下一个 */ }
    }
    if (item._url) {
      try {
        const resp = await fetch(item._url);
        if (resp.ok) return await resp.blob();
      } catch (e) { /* 读不到就算了 */ }
    }
    return null;
  }

  async function toNote(item) {
    const noteApp = window.NoteApp;
    if (!noteApp || typeof noteApp.appendText !== "function") {
      toast("简记还没准备好，稍后再试");
      return;
    }
    if (item.type === "note") {
      if (!String(item.text || "").trim()) {
        toast("这张便条是空的，没什么可写的");
        return;
      }
      noteApp.appendText(item.text);
    } else {
      const blob = await wallPhotoBlob(item);
      if (!blob) {
        toast("这张图片读不出来了，没能放进简记");
        return;
      }
      if (typeof noteApp.appendImageBlob !== "function") {
        toast("简记还没准备好，稍后再试");
        return;
      }
      await noteApp.appendImageBlob(blob, "墙上的照片");
    }
    toast("已放进简记，切过去看看～");
    if (typeof noteApp.open === "function") noteApp.open();   // 切到简记，立刻能看到
  }

  function onItemsDblClick(e) {
    if (state.tool === "cut") return;
    const noteText = e.target.closest(".note-text");
    if (!noteText) return;
    const item = byId.get(noteText.closest(".item").dataset.id);
    if (item) beginEdit(item);
  }

  function applyPhotoTilt(e) {
    if (runtime.drag || e.buttons) return;
    const photoEl = e.target.closest && e.target.closest(".item.photo");
    if (!photoEl) return;
    const r = photoEl.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    const px = (e.clientX - r.left) / r.width - 0.5;
    const py = (e.clientY - r.top) / r.height - 0.5;
    const rx = clamp(-py * 8, -4.5, 4.5);
    const ry = clamp(px * 10, -6, 6);
    photoEl.style.setProperty("--rx", rx.toFixed(2) + "deg");
    photoEl.style.setProperty("--ry", ry.toFixed(2) + "deg");
  }

  function resetPhotoTilt(e) {
    const photoEl = e.target.closest && e.target.closest(".item.photo");
    if (!photoEl) return;
    if (e.relatedTarget && photoEl.contains(e.relatedTarget)) return;
    photoEl.style.setProperty("--rx", "0deg");
    photoEl.style.setProperty("--ry", "0deg");
  }

  function onWindowPointerDown(e) {
    if (e.button !== 0) return;
    const inTopbar = e.target.closest && e.target.closest("#topbar");
    const inItem = !!(e.target.closest && (e.target.closest(".item") || e.target.closest(".pin-overlay")));

    // 剪断模式：优先命中连线并剪断
    if (state.tool === "cut" && !inTopbar) {
      // 剪线模式：只认工字钉（点两个连着的工字钉 = 取消它们之间那根线），
      // 点空白处／顶栏以外的地方就退出这个模式
      if (!inItem) exitCut();
      return;
    }

    // 注意：window 上的 pointerdown，target 可能是 document / window 本身
    // （它们没有 .closest），这里必须自己兜一下，不然会抛异常。
    const hasClosest = !!(e.target && typeof e.target.closest === "function");
    if (!inTopbar && !inItem && !(hasClosest && e.target.closest("#toast")) &&
        !(hasClosest && e.target.closest("#hint"))) {
      clearSelection();
      disarmPin();
    }
  }

  function onWindowPointerMove(e) {
    runtime.mouse.x = e.clientX - sceneOrigin.x;        // 画布内部坐标
    runtime.mouse.y = e.clientY - sceneOrigin.y;
  }

  function onKeyDown(e) {
    if (e.key === "Escape") {
      if (runtime.editingId) {
        const item = byId.get(runtime.editingId);
        if (item) endEdit(item);
        return;
      }
      disarmPin();
      exitCut();
      clearSelection();
    }
  }

  function bindEvents() {
    els.items.addEventListener("pointerdown", onItemsPointerDown);
    els.items.addEventListener("click", onItemsClick);
    els.items.addEventListener("dblclick", onItemsDblClick);
    els.items.addEventListener("pointermove", applyPhotoTilt, { passive: true });
    els.items.addEventListener("pointerleave", resetPhotoTilt, { passive: true });
    els.pins.addEventListener("click", (e) => {
      const pinEl = e.target.closest(".pin-overlay");
      if (!pinEl) return;
      const item = byId.get(pinEl.dataset.id);
      if (item) handlePinClick(e, item);
    });
    window.addEventListener("pointerdown", onWindowPointerDown, { passive: false });
    window.addEventListener("pointermove", onWindowPointerMove, { passive: true });
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", onResize);

    // 空白处拖动平移画布；滚轮缩放；右下角缩放轴
    els.scene.addEventListener("pointerdown", onScenePointerDown);
    els.scene.addEventListener("wheel", onSceneWheel, { passive: false });
    els.zoomRange.addEventListener("input", () => {
      setZoomCentered(parseFloat(els.zoomRange.value) / 100);
    });
    els.zoomIn.addEventListener("click", () => setZoomCentered(view.scale * 1.25));
    els.zoomOut.addEventListener("click", () => setZoomCentered(view.scale / 1.25));
    els.zoomReset.addEventListener("click", resetView);

    // 线色：一个色块按钮，点开是调色板（最近用过的颜色 + 预设 + 自己调色）
    if (els.colorBtn) {
      els.colorBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleColorPop();
      });
      els.colorPop.addEventListener("click", (e) => e.stopPropagation());
      els.cpCustom.addEventListener("input", () => chooseColor(els.cpCustom.value, false));
      els.cpCustom.addEventListener("change", () => chooseColor(els.cpCustom.value, true));
      window.addEventListener("pointerdown", (e) => {
        if (!els.colorPop || els.colorPop.hidden) return;
        if (e.target.closest && (e.target.closest("#colorPop") || e.target.closest("#colorBtn"))) return;
        closeColorPop();
      });
      window.addEventListener("resize", closeColorPop);
    }

    // 底部提示条的收起 / 展开
    els.hintClose.addEventListener("click", (e) => {
      e.stopPropagation();
      setHintClosed(true);
    });
    els.hintRestore.addEventListener("click", (e) => {
      e.stopPropagation();
      setHintClosed(false);
      updateHint();
    });

    els.toolSelect.addEventListener("click", () => {
      state.tool = "select";
      disarmPin();
      updateToolbar();
      updateHint();
    });
    els.toolCut.addEventListener("click", () => {
      state.tool = "cut";
      disarmPin();
      updateToolbar();
      updateHint();
    });

    els.btnUpload.addEventListener("click", () => els.fileInput.click());
    els.fileInput.addEventListener("change", () => {
      if (els.fileInput.files && els.fileInput.files.length) {
        addFiles(els.fileInput.files);
      }
      els.fileInput.value = "";
    });

    els.btnNote.addEventListener("click", () => {
      const item = addNote("", 220, undefined, undefined, true);
      runtime.demoCounter++;
    });

    els.btnClear.addEventListener("click", () => {
      window.appConfirm("确定清空整面墙吗？\n所有照片、便签和连线都会被删除。", "清空").then((ok) => {
        if (ok) clearAll();
      });
    });
    els.btnExport.addEventListener("click", exportWall);
    els.btnImport.addEventListener("click", async () => {
      const file = await pickWallFile();
      if (file) importWall(file);
    });
    els.wallFileInput.addEventListener("change", () => {
      const file = els.wallFileInput.files && els.wallFileInput.files[0];
      els.wallFileInput.value = "";
      if (pendingImportFile) {
        const done = pendingImportFile;
        pendingImportFile = null;
        done(file || null);
      } else if (file) {
        importWall(file);
      }
    });

    els.caseName.value = state.caseTitle;
    els.caseName.addEventListener("input", () => {
      state.caseTitle = els.caseName.value || "未命名事件";
      saveLater();
    });

    // 拖文件到墙上
    window.addEventListener("dragover", (e) => {
      if (document.body.classList.contains("mode-note")) return;   // 简记模式里交给简记
      e.preventDefault();
      if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files")) {
        document.body.classList.add("drag-over");
      }
    });
    window.addEventListener("dragleave", (e) => {
      if (!e.relatedTarget) document.body.classList.remove("drag-over");
    });
    window.addEventListener("drop", (e) => {
      if (document.body.classList.contains("mode-note")) return;   // 简记模式里交给简记
      e.preventDefault();
      document.body.classList.remove("drag-over");
      const files = e.dataTransfer ? e.dataTransfer.files : null;
      if (files && files.length) addFiles(files, e.clientX, e.clientY);
    });

    // 直接 Ctrl+V / ⌘+V 粘贴截图
    window.addEventListener("paste", (e) => {
      if (document.body.classList.contains("mode-note")) return;   // 简记模式里交给简记
      const items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      const files = [];
      for (const it of items) {
        if (it.kind === "file" && it.type.startsWith("image/")) files.push(it.getAsFile());
      }
      if (files.length) addFiles(files, undefined, undefined);
    });

  }

  function onResize() {
    fitToolbar();         // 窗口变窄就把工具栏收成图标（不出现滚动条）
    measureScene();       // 自绘标题栏 / 窗口尺寸变了，重新量画布原点
    autosizeAllNotes();   // 宽度变了，自动高度的便签跟着重新排版
    // 窗口变大以后，原先的缩放可能已经"缩过头"（会看见木板外面）→ 拉回下限
    const low = minZoom();
    if (view.scale < low) view.scale = low;
    if (view.scale > MAX_ZOOM) view.scale = MAX_ZOOM;
    applyView();          // 重新算墙的位置并重绘背景
    resizeStringCanvas();
  }

  /* ---------------- 画布平移 / 缩放 ---------------- */
  function onScenePointerDown(e) {
    const overItem = !!(e.target.closest && (e.target.closest(".item") || e.target.closest(".pin-overlay")));
    if (e.button === 1) {
      // 中键：任何位置都平移
    } else if (overItem || e.button !== 0 || state.tool === "cut" || runtime.editingId) {
      return;
    }
    const startX = e.clientX;
    const startY = e.clientY;
    const baseX = view.x;
    const baseY = view.y;
    let moved = false;

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      if (!moved) {
        if (Math.hypot(dx, dy) < 4) return;
        moved = true;
        document.body.classList.add("panning");
      }
      view.x = baseX + dx;
      view.y = baseY + dy;
      applyView();
      ev.preventDefault();
    }

    function onUp() {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.classList.remove("panning");
      if (moved) updateHint();
    }

    window.addEventListener("pointermove", onMove, { passive: false });
    window.addEventListener("pointerup", onUp, { passive: false });
    e.preventDefault();
  }

  function onSceneWheel(e) {
    // 鼠标停在「已选中的便签」上时，滚轮用来滚便签里的文字（不缩放画布）。
    // 点空白处或图片会取消选中，滚轮就恢复成缩放 —— 和用户要的手感一致。
    const selNote = e.target.closest && e.target.closest(".item.note.selected");
    if (selNote) {
      const textEl = selNote.querySelector(".note-text");
      if (textEl && textEl.scrollHeight > textEl.clientHeight + 1) {
        // 自己滚：行为确定，不会"既没滚也没缩放"
        const step = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
        textEl.scrollTop += step;
        e.preventDefault();
        return;
      }
    }
    e.preventDefault();
    const factor = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0016));
    zoomAt(e.clientX - sceneOrigin.x, e.clientY - sceneOrigin.y, view.scale * factor);
  }

  function resizeStringCanvas() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    // 注意：画布装在 #scene 里，尺寸必须按 #scene 来算。
    // 之前用 window.innerHeight（把自绘标题栏那 34px 也算进去了）→ 画布被轻微拉伸，
    // 于是绳子端点会随"位置 + 缩放"整体偏移，看着就是线头没钉在钉子上。
    const box = sceneSize();
    els.strings.width = Math.max(1, Math.round(box.w * dpr));
    els.strings.height = Math.max(1, Math.round(box.h * dpr));
  }

  async function addFiles(fileList, dropX, dropY) {
    const files = Array.from(fileList).filter((f) => f.type.startsWith("image/"));
    if (!files.length) {
      toast("没有检测到可用的图片");
      return;
    }
    for (const file of files) {
      runtime.demoCounter++;
      await addPhotoFromFile(file, dropX, dropY);
    }
  }

  /* ---------------- 清空 / 恢复 ---------------- */
  function clearAll() {
    disarmPin();
    const oldPhotoIds = wallPhotoIds(state.items);      // 先记下要删的是哪几张（别碰简记的图）
    state.items.forEach((it) => {
      if (it._url) URL.revokeObjectURL(it._url);
    });
    state.items = [];
    state.links = [];
    byId.clear();
    ropes.clear();
    els.items.innerHTML = "";
    els.pins.innerHTML = "";
    renderStrings();
    dbDelMany(oldPhotoIds);
    updateStats();
    updateHint();
    saveNow();
    toast("整面墙已清空");
  }

  // 启动自愈：剔除指向不存在物件的孤立连线，避免旧存档残留“悬浮线”
  function pruneOrphanLines() {
    const liveIds = new Set(state.items.map((it) => it.id));
    state.links = state.links.filter(
      (l) => liveIds.has(l.from) && liveIds.has(l.to)
    );
    for (const key of ropes.keys()) {
      if (!state.links.some((l) => l.id === key)) ropes.delete(key);
    }
  }

  /* ---------------- 双击 .lc 用本程序打开 ----------------
     Windows 把 .lc 交给本程序时，Python 那头会把文件登记进队列（/api/open-wait）；
     这里长轮询把它接过来，然后走和「导入」一模一样的那条路。 */
  let openWatchOn = true;

  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  async function takePendingOpen() {
    let res;
    try {
      res = await fetch("/api/open-wait?timeout=20", { cache: "no-store" });
    } catch (e) {
      await sleep(3000);            // 没有本地服务（比如直接打开 html）→ 慢点再试
      return;
    }
    if (!res.ok) { await sleep(3000); return; }
    let item = null;
    try { item = await res.json(); } catch (e) { return; }
    if (item && item.close) {
      // 有人点了 ✕ / Alt+F4：先让用户确认一下「还没保存」的东西
      await requestClose();
      return;
    }
    if (!item || !item.token) return;
    toast("正在打开：" + (item.name || "文件") + "……");
    let text = null;
    try {
      const got = await fetch("/api/open-file?token=" + encodeURIComponent(item.token), { cache: "no-store" });
      if (got.ok) text = await got.text();
    } catch (e) { /* 下面统一报错 */ }
    if (text == null) { toast("打不开这个文件：" + (item.name || "")); return; }
    await importWall(new File([text], item.name || "打开的文件.lc", { type: "application/json" }));
  }

  async function watchOpenFiles() {
    while (openWatchOn) {
      await takePendingOpen();
      await sleep(200);
    }
  }

  /* ---------------- 数据目录：照片和文字到底存在哪个文件夹 ----------------
     文字（照片墙、简记、线色）和图片（墙上照片、简记图片）都由窗口外壳
     （WebView2）落盘，存哪个文件夹由程序（Python 那一头）说了算。
     这里只负责：问一下现在存在哪、弹系统「选择文件夹」换一个。 */
  let dataDirBusy = false;

  function setDataDirTitle(dir) {
    if (!els.btnDataDir || !dir) return;
    els.btnDataDir.title = "照片和文字现在存在：\n" + dir + "\n（点一下可以换到别的文件夹）";
  }

  function refreshDataDirTitle() {
    currentDataDir().then((info) => { if (info) setDataDirTitle(info.dir); });
  }

  function bridgeApi(name) {
    const api = window.pywebview && window.pywebview.api;
    return api && typeof api[name] === "function" ? api[name].bind(api) : null;
  }

  async function currentDataDir() {
    const fn = bridgeApi("data_dir_info");
    if (fn) {
      try { return await fn(); } catch (e) { /* 桥没起来 → 走下面的 HTTP 问 */ }
    }
    try {
      const res = await fetch("/api/data-dir");
      if (res.ok) return await res.json();
    } catch (e) { /* 直接双击 html 打开时没有本地服务，问不到就算了 */ }
    return null;
  }

  async function showDataDir() {
    const choose = bridgeApi("choose_data_dir");
    if (!choose) {
      const info = await currentDataDir();
      await window.appNotify(
        "这个界面是在浏览器里打开的，照片和文字由浏览器自己保管（存在浏览器的数据目录里），这里改不了。" +
        (info && info.dir ? "\n\n用程序窗口打开时会用：\n" + info.dir : "") +
        "\n\n用 ARGtool.exe 打开就能自己挑文件夹。", "知道了");
      return;
    }
    if (dataDirBusy) return;
    dataDirBusy = true;
    try {
      const res = await choose();
      if (!res || res.cancel) return;
      if (!res.ok) {
        await window.appNotify("换不了：" + (res.error || "未知原因"), "知道了");
        return;
      }
      const movedText = (res.moved && res.moved.length)
        ? "原来的照片和文字已经一起搬过去了。"
        : "这个文件夹原来是空的，重启后会在这里新建。";
      setDataDirTitle(res.dir);
      await window.appNotify(
        "以后照片和文字都存在：\n" + res.dir + "\n\n" + movedText +
        "\n\n关闭本程序再打开一次就生效。", "好");
    } catch (e) {
      await window.appNotify("换目录出错：" + (e && e.message ? e.message : e), "知道了");
    } finally {
      dataDirBusy = false;
    }
  }

  /* 把「照片墙 + 简记」打包成 .lc 的内容（导出 和「上一轮自动备份」共用这一段） */
  async function buildWallPayload() {
    const notes = (window.NoteApp && typeof NoteApp.exportPayload === "function" && NoteApp.hasContent())
      ? await NoteApp.exportPayload()
      : null;
    const noteImgs = notes ? Object.keys(notes.assets || {}).length : 0;
    if (!state.items.length && !state.links.length && !notes) {
      return null;
    }
    const assets = {};
    for (const item of state.items) {
      if (item.type !== "photo") continue;
      const dataUrl = await assetDataUrl(item);
      if (!dataUrl) {
        return { error: "有一张照片无法读取" };
      }
      assets[item.id] = dataUrl;
    }
    if (notes && notes.missing) {
      toast("有 " + notes.missing + " 张简记图片读不出来，这些图没能导出");
    }

    const payload = {
      app: "linkchart-wall",
      format: 2,
      savedAt: new Date().toISOString(),
      caseTitle: state.caseTitle,
      color: state.color,
      items: state.items.map(plainItemDef),
      links: state.links.map((l) => ({ id: l.id, from: l.from, to: l.to, color: l.color })),
      assets
    };
    if (notes) {
      payload.notes = { title: notes.title, font: notes.font, blocks: notes.blocks, assets: notes.assets };
      payload.noteImages = noteImgs;
    }
    return payload;
  }

  /* 导出：返回 true = 真的写出去了（「导出后再关闭」要看这个结果） */
  async function exportWall() {
    toast("正在打包……");
    let payload;
    try {
      payload = await buildWallPayload();
    } catch (e) {
      toast("导出失败：" + (e && e.message ? e.message : e));
      return false;
    }
    if (!payload) { toast("照片墙和简记都是空的，没有东西可以导出"); return false; }
    if (payload.error) { toast(payload.error + "，导出已取消"); return false; }
    try {
      const json = JSON.stringify(payload);
      const blob = new Blob([json], { type: "application/json" });
      const safeName = (state.caseTitle || "未命名事件").replace(/[\\/:*?"<>|]/g, "_");
      const fileName = safeName + ".lc";

      // ① 先请系统弹出「另存为」，让用户自己挑保存位置（Chromium 的文件系统访问 API）
      if (window.showSaveFilePicker) {
        try {
          const handle = await window.showSaveFilePicker(await pickerOptions({
            suggestedName: fileName,
            types: [{ description: "照片墙 / 简记文件", accept: { "application/json": [".lc", ".linkchart", ".json"] } }]
          }));
          const writable = await handle.createWritable();
          await writable.write(blob);
          await writable.close();
          await rememberDirFrom(handle);        // 记住这次存到哪个目录了
          toast("已导出到：" + handle.name);
          markClean();                          // 存过了 → 关闭时不再拦
          return true;
        } catch (err) {
          if (err && err.name === "AbortError") return false;   // 用户自己点了取消
          // 其它情况（浏览器不支持这个能力）→ 走下面的老办法
        }
      }

      // ② 退路：走浏览器默认下载
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 3000);
      toast("已保存到浏览器默认位置（一般是「下载」文件夹）");
      markClean();
      return true;
    } catch (e) {
      toast("导出失败：" + (e.message || "未知错误"));
      return false;
    }
  }

  async function importWall(file) {
    let text;
    try { text = await file.text(); }
    catch (e) { toast("无法读取这个文件"); return; }

    let data;
    try { data = JSON.parse(text); }
    catch (e) { toast("文件格式不正确，不是有效的照片墙文件"); return; }

    if (!data || data.app !== "linkchart-wall" || !Array.isArray(data.items) || !Array.isArray(data.links)) {
      toast("这不是 LinkChart 照片墙文件");
      return;
    }

    // 文件里有什么：墙（.lc 老版本只有墙）和简记（新版本才有）是分开的两块
    const noteData = (data.notes && Array.isArray(data.notes.blocks)) ? data.notes : null;
    const fileHasWall = data.items.length > 0 || data.links.length > 0;
    const noteApp = window.NoteApp && typeof NoteApp.importPayload === "function" ? NoteApp : null;
    const noteThere = !!(noteApp && typeof NoteApp.hasContent === "function" && NoteApp.hasContent());

    const willReplace = [];
    if (fileHasWall && (state.items.length || state.links.length)) willReplace.push("照片墙");
    if (noteData && noteThere) willReplace.push("简记");
    if (willReplace.length) {
      const ok = await window.appConfirm(
        "导入会替换当前的" + willReplace.join("和") + "，确定继续吗？", "继续导入");
      if (!ok) return;
    }

    // ① 先还原简记（文字 + 图片），图片会写进本机图片库
    let noteResult = null;
    if (noteData && noteApp) {
      noteResult = await noteApp.importPayload(noteData);
    }

    // ② 还原照片墙。文件里要是只有简记（墙是空的），就别动用户现在这面墙。
    if (!fileHasWall) {
    if (noteResult && noteResult.ok) markClean();
    const noteImgs = noteResult ? noteResult.images : 0;
      toast(noteResult && noteResult.ok
        ? "导入成功：简记 " + noteResult.blocks + " 段（图片 " + noteImgs + " 张），照片墙保持原样"
        : "照片墙文件里没有任何内容");
      setTimeout(() => location.reload(), 900);
      return;
    }

    // 写入图片资源：优先存入 IndexedDB，避免 localStorage 放不下
    // （刚启动就双击 .lc 时，图片库可能还在打开 —— 等它一下，别退化成把图片塞进 localStorage）
    if (runtime.dbReady) {
      try { await runtime.dbReady; } catch (e) { /* 打不开就按没有图片库处理 */ }
    }
    disarmPin();
    const defs = data.items.map((it) => ({
      id: String(it.id),
      type: it.type === "photo" ? "photo" : "note",
      x: Number(it.x) || 30,
      y: Number(it.y) || 90,
      angle: Number(it.angle) || 0,
      w: Number(it.w) || (it.type === "photo" ? 260 : 220),
      h: it.type === "photo" ? Number(it.h) || 200 : undefined,
      text: it.type === "note" ? String(it.text || "") : undefined,
      dataUrl: undefined
    }));
    // 只清掉*当前墙上*那几张照片的图片，简记里的图片（key 前缀 note-）必须原样保留
    if (runtime.db) await dbDelMany(wallPhotoIds(state.items));
    for (const def of defs) {
      if (def.type !== "photo") continue;
      const asset = data.assets && data.assets[def.id];
      if (!asset) continue;
      if (runtime.db) {
        try { await dbPut(def.id, dataUrlToBlob(asset)); }
        catch (e) { /* 下面的兜底会保留 dataUrl */ }
        def.dataUrl = undefined;
      } else {
        def.dataUrl = asset;
      }
    }

    const keptIds = new Set(defs.map((it) => it.id));
    const links = (data.links || [])
      .filter((l) => l && keptIds.has(String(l.from)) && keptIds.has(String(l.to)))
      .map((l) => ({
        id: String(l.id || "L" + uid()),
        from: String(l.from),
        to: String(l.to),
        color: COLORS.some((c) => c.value === l.color) ? l.color : data.color
      }));

    const wall = {
      v: 1,
      caseTitle: typeof data.caseTitle === "string" ? data.caseTitle : "未命名事件",
      color: COLORS.some((c) => c.value === data.color) ? data.color : COLORS[0].value,
      items: defs,
      links
    };
    const putWall = (w) => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(w));
        return true;
      } catch (e) { return false; }
    };
    if (!putWall(wall)) {
      // 塞不下通常是因为图片没能进图片库（dataUrl 留在墙数据里了）→ 再搬一次，然后重试
      let moved = false;
      for (const def of defs) {
        if (!def.dataUrl || !runtime.db) continue;
        try {
          const blob = dataUrlToBlob(def.dataUrl);
          if (blob && await dbPut(def.id, blob)) { def.dataUrl = undefined; moved = true; }
        } catch (e) { /* 这一张搬不动就算了 */ }
      }
      if (!moved || !putWall(wall)) {
        toast("浏览器空间不足，导入的文件太大");
        return;
      }
    }
    const noteMsg = (noteResult && noteResult.ok)
      ? "，简记 " + noteResult.blocks + " 段（图片 " + noteResult.images + " 张）"
      : "";
    toast("导入成功" + noteMsg + "，正在重建……");
    markClean();                 // 内容来自文件，等于「已保存」
    setTimeout(() => location.reload(), 900);
  }

  function restoreState(saved) {
    if (!saved || !Array.isArray(saved.items)) return false;
    state.caseTitle = typeof saved.caseTitle === "string" && saved.caseTitle
      ? saved.caseTitle
        : "未命名事件";
    if (typeof saved.color === "string" && COLORS.some((c) => c.value === saved.color)) {
      state.color = saved.color;
    }

    saved.items.forEach((def) => {
      if (!def || typeof def.id !== "string") return;
      const item = {
        id: def.id,
        type: def.type === "photo" ? "photo" : "note",
        x: Number(def.x) || 30,
        y: Number(def.y) || 90,
        angle: Number(def.angle) || 0,
        w: Number(def.w) || (def.type === "photo" ? 260 : 220)
      };
      if (item.type === "photo") {
        item.h = Number(def.h) || 200;
        if (typeof def.dataUrl === "string") item.dataUrl = def.dataUrl;
      } else {
        item.text = typeof def.text === "string" ? def.text : "";
        if (def.fontSize) item.fontSize = Number(def.fontSize) || NOTE_FONT_DEFAULT;
        if (def.hFixed && Number(def.h)) {
          item.hFixed = true;
          item.h = Number(def.h);
        }
      }
      state.items.push(item);
      buildItemElement(item);
    });

    if (Array.isArray(saved.links)) {
      saved.links.forEach((ldef) => {
        if (!ldef || !byId.has(ldef.from) || !byId.has(ldef.to)) return;
        const link = {
          id: ldef.id || "L" + uid(),
          from: ldef.from,
          to: ldef.to,
          color: COLORS.some((c) => c.value === ldef.color) ? ldef.color : state.color
        };
        state.links.push(link);
        buildRope(link);
      });
    }
    return true;
  }

  async function hydratePhotos() {
    const tasks = state.items.filter((it) => it.type === "photo" && !it.dataUrl).map(async (item) => {
      const blob = await dbGet(item.id);
      if (!blob) {
        deleteItem(item, false);
        toast("有一张照片无法恢复，已从墙上移除");
        return;
      }
      item._url = URL.createObjectURL(blob);
      applyPhotoSource(item);
    });
    await Promise.all(tasks);
    renderColorPick();
    updateStats();
    updateHint();
    saveLater();
  }

  async function init() {
    cacheEls();
    measureScene();              // 先量一次画布原点（有没有标题栏都靠它）
    renderColorPick();
    fitToolbar();                // 窄窗口：工具栏直接收成图标，不出现滚动条
    applyView();                 // 设定世界层变换并绘制软木板
    resizeStringCanvas();
    bindEvents();
    if (window.__pageStep) window.__pageStep("界面骨架已搭好");

    runtime.dbReady = openDB();
    await runtime.dbReady;
    if (window.__pageStep) window.__pageStep("本地数据库已打开");

    // 「一次运行 = 一张白纸」：程序每次启动都会换一个会话号。
    // 会话号变了 = 上一次的痕迹要清掉（清之前先把上一轮自动备份到数据目录）。
    // 同一次运行里刷新页面（比如导入后自动重载）会话号不变，所以内容照旧。
    let freshRun = false;
    try {
      const ses = await sessionInfo();
      if (ses && ses.session && localStorage.getItem(RUN_KEY) !== ses.session) {
        if (window.__pageStep) window.__pageStep("检测到新的一次运行，清空上一轮");
        await startFreshRun(ses.session);
        freshRun = true;
      }
    } catch (e) { /* 出问题就按老样子恢复，别把界面卡住 */ }

    // freshRun：这一次就是要空白。上面 await 的这点时间里，页面别的地方可能已经
    // 往 localStorage 里写过一份「空墙」，所以这里不看它，直接按空白走。
    const saved = freshRun ? null : readSaved();
    const restored = restoreState(saved);
    pruneOrphanLines();

    if (restored && !saved.items.length) {
      // 用户曾保存过空墙，不重复添加引导便签
    } else if (!restored) {
      const msg =
        "欢迎使用 BubbleWork ARG 辅助工具！\n\n" +
        "· 点上方「上传照片」把图片钉到墙上；也可直接把图片拖进来或 Ctrl+V 粘贴。\n" +
        "· 把鼠标移到照片或便签上，拖右下角的小圆点即可改大小；按住 Shift 拖动可以锁定原来的横纵比。\n" +
        "· 便签上还有 A－ / A＋ 两个小按钮，可以单独调这张便签的字号。\n" +
        "· 拖动空白处可以平移整面墙，滚轮或右下角的缩放轴可以放大缩小，右下角「复位」一键回到原位。\n" +
        "· 双击便签编辑文字。\n" +
        "· 点击一枚工字钉，再点击另一枚，就能用当前线色连起两个线索。\n\n" +
        "· 右上角「破译台」可以粘贴密文自动尝试各种解密方式，结果能一键钉到墙上。\n" +
        "· 想备份时，点「导出」生成照片墙文件，之后用「导入」打开即可。\n" +
        "· 点击右上角的 ✕ 即可删除这条便签。";
      // 便签给足宽度，配合下面的自动撑高，一打开就能看全所有文字，不用先双击
      const note = addNote(msg, 330, window.innerWidth * 0.58, window.innerHeight * 0.30, false);
      note.angle = -1.8;
      note.el.style.transform = posTransform(note);
      saveNow();
    }

    els.caseName.value = state.caseTitle;
    updateToolbar();
    updateStats();
    updateHint();
    renderColorPick();
    if (window.__pageStep) window.__pageStep("推理墙就绪");

    // 上次收起过提示条的话，这次也保持收起（右下角小问号可以叫回来）
    try {
      if (localStorage.getItem(HINT_KEY) === "1") setHintClosed(true);
    } catch (e) { /* 隐私模式忽略 */ }

    // 恢复 IndexedDB 中的照片
    hydratePhotos();

    // 到这一刻为止都算「启动状态」：把当前样子记成基准，
    // 用户接下来改动才叫「还没保存」。
    markClean();

    // 有人双击 .lc 就把它接过来（走「导入」那条路）。
    // 必须等上面这些都摆好再开始问 —— 否则会和「初始加载」抢着写同一份数据。
    watchOpenFiles();

    // 便签自动撑高：创建时字体可能还没就绪，这里补几次，保证文字不被切掉
    setTimeout(autosizeAllNotes, 260);
    setTimeout(autosizeAllNotes, 1000);
    try {
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(autosizeAllNotes);
    } catch (e) { /* 忽略 */ }

    // 主循环：每帧推进绳线物理并重绘
    lastFrameTs = performance.now();
    function frame(ts) {
      requestAnimationFrame(frame);
      if (frameBusy) return;
      const dt = Math.min(ts - lastFrameTs || 16, 60);
      lastFrameTs = ts;
      // 简记模式下推理墙是收起来的，不用每帧算绳子（回到墙上会重画）
      if (document.body.classList.contains("mode-note")) return;
      const dtNorm = clamp(dt / 16.667, 0.2, 3);
      state.items.forEach(syncPinPosition);
      // 只有「图钉动过」才跑物理；缩放/平移画布完全不碰它
      if (anchorsMoved()) wakeRopes();
      if (!ropesSleeping()) {
        noteRopeMotion(stepRopes(dtNorm));
        // 跑够久了就强制睡：剩下的都是肉眼看不出的微动，
        // 继续跑只会让线条永远「活」着（缩放时看着就像被牵着走）
        if (ropeAwakeSince && performance.now() - ropeAwakeSince > ROPE_MAX_AWAKE) ropeSleep();
      }
      if (state.links.length || runtime.armedId || state.tool === "cut") {
        renderStrings();
      }
    }
    requestAnimationFrame(frame);
  }

  init();
})();
