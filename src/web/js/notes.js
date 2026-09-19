/* ========== 简记模式：细长条笔记窗口 ==========
   只记「文字 + 图片」，没有照片墙、没有连线，
   但破译台照旧可以用（结果可以直接插进简记里）。

   文字：点一下就能写，支持一点 Markdown 写法
        # 标题 / ## 小标题 / **粗体** / *斜体* / `代码`
        - 列表 / 1. 编号 / > 引用 / --- 分隔线 / [文字](网址)
   图片：点「＋图片」、Ctrl+V 粘贴、或者直接把图片拖进窗口。 */
(function () {
  "use strict";

  const STORE_KEY = "bubblework.notes.v1";
  const DB_NAME = "linkchart-assets";
  const DB_STORE = "photos";

  const el = (id) => document.getElementById(id);

  const state = {
    blocks: [],
    font: 100,          // 正文字号百分比
    title: "未命名事件"
  };

  const db = { handle: null };
  let ui = null;
  let saveTimer = null;
  let editingId = null;
  let dbReady = null;          // 打开图片库的 Promise（导入时等它）

  function uid() {
    return "n" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  /* ---------------- 本地存储 ---------------- */

  function load() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (!data || typeof data !== "object") return;
      if (Array.isArray(data.blocks)) {
        state.blocks = data.blocks.filter((b) => b && (b.type === "text" || b.type === "image"));
      }
      if (typeof data.font === "number") state.font = clamp(data.font, 70, 200);
      if (typeof data.title === "string" && data.title.trim()) state.title = data.title;
    } catch (err) { /* 数据坏了就当新笔记 */ }
  }

  function saveSoon() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveNow, 300);
  }

  function saveNow() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        blocks: state.blocks.map((b) => (b.type === "image"
          ? { id: b.id, type: "image", key: b.key, name: b.name }
          : { id: b.id, type: "text", text: b.text })),
        font: state.font,
        title: state.title
      }));
    } catch (err) { /* 隐私模式忽略 */ }
  }

  /* ---------------- 图片放 IndexedDB ---------------- */

  function openDB() {
    return new Promise((resolve) => {
      let req;
      try { req = indexedDB.open(DB_NAME, 1); } catch (err) { return resolve(null); }
      req.onupgradeneeded = () => {
        const database = req.result;
        if (!database.objectStoreNames.contains(DB_STORE)) database.createObjectStore(DB_STORE);
      };
      req.onsuccess = () => { db.handle = req.result; resolve(db.handle); };
      req.onerror = () => resolve(null);
    });
  }

  function dbPut(key, blob) {
    return new Promise((resolve) => {
      if (!db.handle) return resolve(false);
      try {
        const tx = db.handle.transaction(DB_STORE, "readwrite");
        tx.objectStore(DB_STORE).put(blob, key);
        tx.oncomplete = () => resolve(true);
        tx.onerror = () => resolve(false);
        tx.onabort = () => resolve(false);
      } catch (err) { resolve(false); }
    });
  }

  function dbGet(key) {
    return new Promise((resolve) => {
      if (!db.handle) return resolve(null);
      try {
        const tx = db.handle.transaction(DB_STORE, "readonly");
        const req = tx.objectStore(DB_STORE).get(key);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => resolve(null);
      } catch (err) { resolve(null); }
    });
  }

  function dbDel(key) {
    if (!db.handle) return Promise.resolve(false);
    return new Promise((resolve) => {
      try {
        const tx = db.handle.transaction(DB_STORE, "readwrite");
        tx.objectStore(DB_STORE).delete(key);
        tx.oncomplete = () => resolve(true);
        tx.onerror = () => resolve(false);
      } catch (err) { resolve(false); }
    });
  }

  /* ---------------- 一点点 Markdown ---------------- */

  function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[ch]);
  }

  function inline(src) {
    let out = esc(src);
    out = out.replace(/`([^`]+)`/g, (m, code) => "<code>" + code + "</code>");
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
    out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, text, href) => {
      const safe = /^https?:\/\//i.test(href) ? href : "#";
      return '<a href="' + esc(safe) + '" target="_blank" rel="noreferrer">' + text + "</a>";
    });
    return out;
  }

  function markdown(src) {
    const lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let list = null;      // "ul" | "ol"
    let fence = null;     // 代码块内容

    function closeList() {
      if (list) { out.push("</" + list + ">"); list = null; }
    }

    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];

      if (/^\s*```/.test(line)) {
        if (fence === null) { closeList(); fence = []; }
        else { out.push("<pre><code>" + esc(fence.join("\n")) + "</code></pre>"); fence = null; }
        continue;
      }
      if (fence !== null) { fence.push(line); continue; }

      if (!line.trim()) { closeList(); continue; }

      if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) { closeList(); out.push("<hr />"); continue; }

      const head = /^\s*(#{1,3})\s+(.*)$/.exec(line);
      if (head) {
        closeList();
        const level = head[1].length;
        out.push("<h" + level + ">" + inline(head[2]) + "</h" + level + ">");
        continue;
      }

      if (/^\s*>\s?/.test(line)) {
        closeList();
        const quoted = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          quoted.push(inline(lines[i].replace(/^\s*>\s?/, "")));
          i += 1;
        }
        i -= 1;
        out.push("<blockquote>" + quoted.join("<br />") + "</blockquote>");
        continue;
      }

      const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
      if (bullet) {
        if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
        out.push("<li>" + inline(bullet[1]) + "</li>");
        continue;
      }

      const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      if (numbered) {
        if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
        out.push("<li>" + inline(numbered[1]) + "</li>");
        continue;
      }

      closeList();
      out.push("<p>" + inline(line) + "</p>");
    }
    if (fence !== null) out.push("<pre><code>" + esc(fence.join("\n")) + "</code></pre>");
    closeList();
    return out.join("");
  }

  /* ---------------- 渲染 ---------------- */

  function render() {
    if (!ui) return;
    ui.sheet.innerHTML = "";
    ui.sheet.style.setProperty("--nb-font", state.font + "%");

    if (!state.blocks.length) {
      const empty = document.createElement("div");
      empty.className = "nb-empty";
      empty.innerHTML =
        "点这里就能开始写。<br />" +
        "也能直接 <b>Ctrl+V</b> 粘图片、或者把图片拖进来。";
      ui.sheet.appendChild(empty);
      updateFoot();
      return;
    }

    state.blocks.forEach((block, index) => {
      ui.sheet.appendChild(blockNode(block, index));
    });
    updateFoot();
  }

  function blockNode(block, index) {
    const wrap = document.createElement("div");
    wrap.className = "nb-block" + (block.id === editingId ? " editing" : "");
    wrap.dataset.id = block.id;

    if (block.type === "image") {
      // 只有图片保留删除按钮（文字段落靠"清空内容"自然消失，不留空框也不留小叉）
      const toWall = document.createElement("button");
      toWall.className = "nb-pin";
      toWall.type = "button";
      toWall.title = "把这张图片贴到照片墙";
      toWall.innerHTML =
        '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.4" y="3" width="11.2" height="9" rx="1.4"/>' +
        '<path d="M3.2 10.6 6.4 7.4l2 2 1.6-1.6 2.6 2.6"/></svg>';
      toWall.addEventListener("click", (e) => {
        e.stopPropagation();
        pinToWall(block);
      });
      wrap.appendChild(toWall);

      const kill = document.createElement("button");
      kill.className = "nb-kill";
      kill.type = "button";
      kill.title = "删掉这张图片";
      kill.textContent = "✕";
      kill.addEventListener("click", (e) => {
        e.stopPropagation();
        removeBlock(block, index);
      });
      wrap.appendChild(kill);
      wrap.appendChild(imageNode(block));
    } else {
      wrap.appendChild(textNode(block, index));
    }
    return wrap;
  }

  function textNode(block, index) {
    if (block.id === editingId) {
      const area = document.createElement("textarea");
      area.className = "nb-edit";
      area.value = block.text || "";
      area.spellcheck = false;
      area.rows = 1;
      area.addEventListener("input", () => autosize(area));
      area.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { e.preventDefault(); endEdit(true); }
        else if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); endEdit(true); }
        else if ((e.ctrlKey || e.metaKey) && e.altKey && (e.key === "Backspace" || e.key === "Delete")) {
          e.preventDefault();
          block.text = "";
        }
      });
      area.addEventListener("blur", () => endEdit(true));
      // 图片直接粘进文字块里
      area.addEventListener("paste", (e) => {
        const items = e.clipboardData && e.clipboardData.items;
        if (!items) return;
        const files = [];
        for (const it of items) {
          if (it.kind === "file" && it.type.startsWith("image/")) files.push(it.getAsFile());
        }
        if (files.length) { e.preventDefault(); addImages(files); }
      });
      requestAnimationFrame(() => {
        area.focus();
        const len = area.value.length;
        try { area.setSelectionRange(len, len); } catch (err) { /* 忽略 */ }
        autosize(area);
      });
      return area;
    }

    const view = document.createElement("div");
    view.className = "nb-view";
    view.dataset.action = "edit";
    if (block.text && block.text.trim()) view.innerHTML = markdown(block.text);
    else view.innerHTML = "";
    return view;
  }

  function imageNode(block) {
    const box = document.createElement("div");
    box.className = "nb-imgbox";
    if (block.url) {
      const img = document.createElement("img");
      img.className = "nb-img";
      img.src = block.url;
      img.alt = block.name || "图片";
      img.draggable = false;
      // 以前是 window.open(blob:…)，WebView2 会把它交给系统 → 弹"获取打开此 blob 链接的应用"。
      // 现在改成应用内直接放大查看。
      img.addEventListener("click", () => showBigImage(block.url));
      box.appendChild(img);
    } else {
      const lost = document.createElement("div");
      lost.className = "nb-lost";
      lost.textContent = "这张图片没能读出来（可能被清理掉了）";
      box.appendChild(lost);
    }
    return box;
  }

  function autosize(area) {
    area.style.height = "auto";
    // 一行的高度就够，别留一个空框（Typora 那种直接写在纸上的感觉）
    area.style.height = Math.max(30, area.scrollHeight + 2) + "px";
  }

  /* ---------------- 点图片放大（应用内查看，不交给系统） ---------------- */

  function showBigImage(url) {
    if (!url) return;
    const box = document.createElement("div");
    box.className = "nb-lightbox";
    box.innerHTML = '<img alt="图片" /><div class="nb-light-hint">点一下关闭（Esc 也行）</div>';
    box.querySelector("img").src = url;
    const close = () => {
      box.remove();
      window.removeEventListener("keydown", onKey);
    };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    box.addEventListener("click", close);
    window.addEventListener("keydown", onKey);
    document.body.appendChild(box);
  }

  function updateFoot() {
    if (!ui) return;
    ui.fontVal.textContent = state.font + "%";
    const texts = state.blocks.filter((b) => b.type === "text" && b.text && b.text.trim()).length;
    const imgs = state.blocks.filter((b) => b.type === "image").length;
    ui.count.textContent = texts || imgs ? "文字 " + texts + " · 图片 " + imgs : "";
  }

  /* ---------------- 增删改 ---------------- */

  function insertAt() {
    const editingIndex = state.blocks.findIndex((b) => b.id === editingId);
    return editingIndex >= 0 ? editingIndex + 1 : state.blocks.length;
  }

  function addText(text) {
    const block = { id: uid(), type: "text", text: text || "" };
    state.blocks.splice(insertAt(), 0, block);
    editingId = text ? null : block.id;
    saveSoon();
    render();
    if (!text) focusLast();
    return block;
  }

  function focusLast() {
    const area = ui.sheet.querySelector("textarea.nb-edit");
    if (area) { area.focus(); const n = area.value.length; try { area.setSelectionRange(n, n); } catch (e) {} }
  }

  function beginEdit(block) {
    if (editingId === block.id) return;
    editingId = block.id;
    render();
    const area = ui.sheet.querySelector(".nb-block.editing textarea");
    if (area) autosize(area);
  }

  function endEdit(commit) {
    const block = state.blocks.find((b) => b.id === editingId);
    if (!block) { editingId = null; return; }
    if (commit) {
      const area = ui.sheet.querySelector(".nb-block.editing textarea");
      if (area) block.text = area.value;
    }
    editingId = null;
    // 空段落不留着（不然纸上会剩一个空框）：内容清空就整段消失
    if (block.type === "text" && !String(block.text || "").trim()) {
      const at = state.blocks.findIndex((b) => b.id === block.id);
      if (at >= 0) state.blocks.splice(at, 1);
    }
    saveSoon();
    render();
  }

  function removeBlock(block, index) {
    if (editingId === block.id) editingId = null;
    const at = state.blocks.findIndex((b) => b.id === block.id);
    if (at >= 0) state.blocks.splice(at, 1);
    if (block.type === "image" && block.key) dbDel(block.key);
    if (block.url) { try { URL.revokeObjectURL(block.url); } catch (err) { /* 忽略 */ } }
    saveSoon();
    render();
    if (window.LinkChart && LinkChart.toast) LinkChart.toast("已删掉这一段");
  }

  async function addImages(files) {
    const imgs = Array.from(files || []).filter((f) => f && f.type && f.type.startsWith("image/"));
    if (!imgs.length) return;
    let at = insertAt();
    for (const file of imgs) {
      const id = uid();
      const key = "note-" + id;
      const ok = await dbPut(key, file);
      if (!ok) { toast("图片没能存下来（本地存储不可用）"); continue; }
      const block = { id: id, type: "image", key: key, name: file.name || "图片" };
      state.blocks.splice(at, 0, block);
      at += 1;
      hydrateImage(block);
    }
    saveNow();
    render();
  }

  async function hydrateImage(block) {
    if (block.url || !block.key) return;
    const blob = await dbGet(block.key);
    if (!blob) return;
    block.url = URL.createObjectURL(blob);
    if (document.body.classList.contains("mode-note")) render();
  }

  async function hydrateAll() {
    await Promise.all(state.blocks.filter((b) => b.type === "image").map(hydrateImage));
    render();
  }

  /* 简记里的这张图片 → 照片墙 */
  async function pinToWall(block) {
    if (!block || !block.key) return;
    if (!window.LinkChart || typeof LinkChart.addPhotoBlob !== "function") {
      toast("照片墙还没准备好，稍后再试");
      return;
    }
    const blob = await dbGet(block.key);
    if (!blob) {
      toast("这张图片读不出来了");
      return;
    }
    const id = await LinkChart.addPhotoBlob(blob, block.name || "简记图片");
    if (id) toast("已贴到照片墙（点「推理墙」就能看到）");
  }

  /* 墙上那张便条 / 破译台的结果 → 简记（给 app.js 调用） */
  async function appendImageBlob(blob, name) {
    if (!blob) return false;
    if (dbReady) {
      try { await dbReady; } catch (err) { /* 打不开就按没有图片库处理 */ }
    }
    const id = uid();
    const key = "note-" + id;
    const stored = await dbPut(key, blob);
    if (!stored) {
      toast("图片没能存下来（本地存储不可用）");
      return false;
    }
    const block = { id: id, type: "image", key: key, name: name || "图片" };
    state.blocks.splice(insertAt(), 0, block);
    hydrateImage(block);
    saveNow();
    render();
    return true;
  }

  /* ---------------- 导出 / 导入：和照片墙装在同一个 .lc 文件里 ----------------
     简记的图片平时只存在本机图片库里（IndexedDB），换台电脑就没了。
     导出时把它们转成 base64 文字，跟着 .lc 一起走；导入时再写回本机图片库。 */

  function blobToDataUrl(blob) {
    return new Promise((resolve) => {
      try {
        const reader = new FileReader();
        reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : null);
        reader.onerror = () => resolve(null);
        reader.readAsDataURL(blob);
      } catch (err) { resolve(null); }
    });
  }

  function dataUrlToBlob(dataUrl) {
    try {
      const text = String(dataUrl);
      const comma = text.indexOf(",");
      if (comma < 0) return null;
      const meta = text.slice(0, comma);
      const mime = (meta.match(/data:([^;]+)/) || [])[1] || "image/png";
      const bin = atob(text.slice(comma + 1));
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
      return new Blob([bytes], { type: mime });
    } catch (err) { return null; }
  }

  /* 导出：返回纯数据（图片是 base64），可以整块 JSON.stringify 进 .lc */
  async function exportPayload() {
    const assets = {};
    const blocks = [];
    let missing = 0;
    for (const block of state.blocks) {
      if (!block) continue;
      if (block.type !== "image") {
        blocks.push({ id: block.id, type: "text", text: block.text || "" });
        continue;
      }
      const key = block.key || ("note-" + block.id);
      const blob = block.key ? await dbGet(block.key) : null;
      const dataUrl = blob ? await blobToDataUrl(blob) : null;
      if (!dataUrl) { missing += 1; continue; }    // 读不出来的图就跳过，别塞个空壳进去
      assets[key] = dataUrl;
      blocks.push({ id: block.id, type: "image", key: key, name: block.name || "图片" });
    }
    return { title: state.title, font: state.font, blocks: blocks, assets: assets, missing: missing };
  }

  function hasContent() {
    return state.blocks.some((b) => (b.type === "text" && b.text && b.text.trim()) || b.type === "image");
  }

  /* 给「关闭前确认」用：把当前样子压成一串好比较的数据 */
  function stateForDiff() {
    return {
      title: state.title,
      font: state.font,
      blocks: state.blocks.map((b) => [
        b.id, b.type, b.text || "", b.key || "", b.name || ""
      ])
    };
  }

  /* 新一轮开始：把简记清空（连带删掉它的图片） */
  function reset() {
    state.blocks.forEach((b) => {
      if (b.key) dbDel(b.key);
      if (b.url) { try { URL.revokeObjectURL(b.url); } catch (err) { /* 忽略 */ } }
    });
    state.blocks = [];
    state.title = "未命名事件";
    state.font = 100;
    editingId = null;
    saveNow();
    const titleEl = el("nbTitle");
    if (titleEl) titleEl.value = state.title;
    if (ui && ui.sheet) ui.sheet.style.setProperty("--nb-font", state.font + "%");
    render();
    updateFoot();
  }

  /* 导入：整条简记换成文件里的那份（图片重新写进本机图片库） */
  async function importPayload(payload) {
    if (!payload || typeof payload !== "object" || !Array.isArray(payload.blocks)) {
      return { ok: false, error: "文件里没有简记数据" };
    }
    // 刚启动就双击 .lc 时，图片库可能还在打开 —— 等它一下，否则简记图片会写不进去
    if (dbReady) {
      try { await dbReady; } catch (err) { /* 打不开就按没有图片库处理 */ }
    }
    const oldKeys = state.blocks.filter((b) => b.type === "image" && b.key).map((b) => b.key);
    state.blocks.forEach((b) => {
      if (b.url) { try { URL.revokeObjectURL(b.url); } catch (err) { /* 忽略 */ } }
    });

    const assets = payload.assets && typeof payload.assets === "object" ? payload.assets : {};
    const blocks = [];
    let missing = 0;
    for (const raw of payload.blocks) {
      if (!raw || typeof raw !== "object") continue;
      if (raw.type === "image") {
        const dataUrl = assets[raw.key];
        const blob = dataUrl ? dataUrlToBlob(dataUrl) : null;
        if (!blob) { missing += 1; continue; }
        // 换一把新钥匙：老图可能还被别处用着，别覆盖掉
        const key = "note-" + uid();
        const stored = await dbPut(key, blob);
        if (!stored) { missing += 1; continue; }
        const block = { id: raw.id || uid(), type: "image", key: key, name: raw.name || "图片" };
        blocks.push(block);
        hydrateImage(block);
      } else {
        blocks.push({ id: raw.id || uid(), type: "text", text: String(raw.text || "") });
      }
    }

    state.blocks = blocks;
    if (typeof payload.font === "number") state.font = clamp(payload.font, 70, 200);
    if (typeof payload.title === "string" && payload.title.trim()) state.title = payload.title;
    editingId = null;

    for (const key of oldKeys) {                 // 被换掉的老图清掉，不占地方
      if (!blocks.some((b) => b.key === key)) await dbDel(key);
    }

    saveNow();
    const titleEl = el("nbTitle");
    if (titleEl) titleEl.value = state.title;
    if (ui && ui.sheet) ui.sheet.style.setProperty("--nb-font", state.font + "%");
    render();
    return { ok: true, missing: missing, blocks: blocks.length,
             images: blocks.filter((b) => b.type === "image").length };
  }

  /* ---------------- 右键菜单：复制 / 粘贴 / 全选 / 张贴到墙上 ----------------
     点空白处右键：只有「全选」亮着（剪切板里真有东西时「粘贴」也亮）；
     点在文字上右键：「复制」「张贴到墙上」才可用。 */

  let menuEl = null;

  function closeMenu() {
    if (menuEl) { menuEl.remove(); menuEl = null; }
  }

  function pickedText() {
    try {
      const sel = window.getSelection && window.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount || !ui || !ui.sheet) return "";
      const node = sel.getRangeAt(0).commonAncestorContainer;
      if (!ui.sheet.contains(node.nodeType === 1 ? node : node.parentNode)) return "";
      return sel.toString();
    } catch (err) { return ""; }
  }

  function blockFromEvent(e) {
    const wrap = e.target && e.target.closest ? e.target.closest(".nb-block") : null;
    if (!wrap) return null;
    return state.blocks.find((b) => b.id === wrap.dataset.id) || null;
  }

  async function readClipboard() {
    try {
      if (navigator.clipboard && navigator.clipboard.readText) {
        const text = await navigator.clipboard.readText();
        return typeof text === "string" ? text : "";
      }
    } catch (err) { /* 不给读就当空的 */ }
    return "";
  }

  function selectAllText() {
    if (!ui || !ui.sheet) return;
    try {
      const range = document.createRange();
      range.selectNodeContents(ui.sheet);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    } catch (err) { /* 忽略 */ }
  }

  async function copyText(text) {
    if (!text) { toast("没有可复制的内容"); return; }
    try {
      await navigator.clipboard.writeText(text);
      toast("已复制");
    } catch (err) {
      const tmp = document.createElement("textarea");
      tmp.value = text;
      document.body.appendChild(tmp);
      tmp.select();
      try { document.execCommand("copy"); toast("已复制"); } catch (e2) { toast("复制失败"); }
      tmp.remove();
    }
  }

  async function pasteText(block) {
    const text = await readClipboard();
    if (!text) { toast("剪切板里没有文字（也可以直接按 Ctrl+V）"); return; }
    // ⚠️ 右键那一刻拿到的那一段，中间可能已经被「空段落自动消失」删掉了
    //    （简记空着的时候，右键会让正在编辑的空段落失焦 → endEdit 把它删掉）。
    //    所以这里必须在 state.blocks 里重新找一遍，找不到就新开一段 ——
    //    否则就会出现「提示已粘贴、纸上却没有」的情况。
    const live = block ? state.blocks.find((b) => b.id === block.id) : null;
    if (live && live.type === "text") {
      live.text = (live.text || "") + text;
      saveNow();
      render();
    } else {
      addText(text);
      saveNow();
    }
    toast("已粘贴");
  }

  function postToWall(text) {
    if (!text || !text.trim()) { toast("没有可以贴到墙上的文字"); return; }
    if (!window.LinkChart || typeof LinkChart.addNote !== "function") {
      toast("照片墙还没准备好，稍后再试");
      return;
    }
    LinkChart.addNote(text, 240);
    toast("已贴到照片墙（点「推理墙」就能看到）");
  }

  function buildMenu(e, block, hasClip, text) {
    const picked = pickedText();
    const items = [
      { key: "copy", label: "复制", on: !!(picked || (block && block.type === "text" && (block.text || "").trim())), act: () => copyText(picked || (block ? block.text : "")) },
      { key: "paste", label: "粘贴", on: !!hasClip, act: () => pasteText(block) },
      { key: "selectAll", label: "全选", on: true, act: selectAllText },
      { key: "toWall", label: "张贴到墙上", on: !!(picked || (block && block.type === "text" && (block.text || "").trim())), act: () => postToWall(picked || (block ? block.text : "")) }
    ];

    const box = document.createElement("div");
    box.className = "nb-menu";
    items.forEach((it) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.dataset.act = it.key;
      btn.textContent = it.label;
      if (!it.on) btn.disabled = true;
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        closeMenu();
        if (it.on) it.act();
      });
      box.appendChild(btn);
    });
    document.body.appendChild(box);

    // 贴着鼠标放，别跑出窗口（简记窗口很窄，右边尤其要注意）
    const w = box.offsetWidth || 132;
    const h = box.offsetHeight || 120;
    const x = Math.max(4, Math.min(e.clientX, window.innerWidth - w - 4));
    const y = Math.max(4, Math.min(e.clientY, window.innerHeight - h - 4));
    box.style.left = x + "px";
    box.style.top = y + "px";
    menuEl = box;
  }

  async function openMenu(e) {
    if (!active()) return;
    e.preventDefault();
    e.stopPropagation();
    closeMenu();
    const block = blockFromEvent(e);
    const hasClip = !!(await readClipboard());      // 剪切板里有东西才让「粘贴」亮
    buildMenu(e, block, hasClip);
  }

  /* ---------------- 模式切换 ---------------- */

  function active() {
    return document.body.classList.contains("mode-note");
  }

  function setMode(mode) {
    const note = mode === "note";
    const wasNote = active();
    document.body.classList.toggle("mode-note", note);
    if (window.WindowChrome) window.WindowChrome.setMode(note ? "note" : "wall");

    if (note) {
      // 进来就是「一张能直接写的纸」：没有内容就先开好一段，光标也放好
      if (!state.blocks.length) {
        addText("");
      } else if (!ui.sheet.childElementCount) {
        render();
      }
      setTimeout(() => { if (ui.sheet) ui.sheet.scrollTop = ui.sheet.scrollHeight; }, 60);
    } else {
      editingId = null;
      saveNow();
      // 回到推理墙：让画布重新量一次尺寸（它在简记模式里被藏起来了）
      setTimeout(() => window.dispatchEvent(new Event("resize")), 30);
    }
    return wasNote;
  }

  function toast(msg) {
    if (window.LinkChart && typeof LinkChart.toast === "function") LinkChart.toast(msg);
  }

  /* ---------------- 事件绑定 ---------------- */

  function bindEvents() {
    const titleEl = el("nbTitle");
    if (titleEl) {
      titleEl.value = state.title;
      titleEl.addEventListener("input", () => {
        state.title = titleEl.value.trim() || "未命名事件";
        saveSoon();
      });
    }
    el("nbBack").addEventListener("click", () => setMode("wall"));

    // 右键菜单（只在简记模式里）
    ui.sheet.addEventListener("contextmenu", openMenu);
    window.addEventListener("pointerdown", (e) => {
      if (!menuEl) return;
      if (e.target && e.target.closest && e.target.closest(".nb-menu")) return;
      closeMenu();
    }, true);
    window.addEventListener("blur", closeMenu);

    // 推理墙工具栏上的「📝 简记」按钮
    const wallBtn = el("btnNoteMode");
    if (wallBtn) wallBtn.addEventListener("click", () => setMode("note"));

    el("nbDecipher").addEventListener("click", () => {
      const panel = el("decipherPanel");
      if (panel) panel.classList.toggle("open");
      el("nbDecipher").classList.toggle("on", !!(panel && panel.classList.contains("open")));
    });

    ui.file.addEventListener("change", () => {
      if (ui.file.files && ui.file.files.length) addImages(ui.file.files);
      ui.file.value = "";
    });

    el("nbFontUp").addEventListener("click", () => {
      state.font = clamp(state.font + 10, 70, 200);
      saveSoon();
      updateFoot();
      ui.sheet.style.setProperty("--nb-font", state.font + "%");
    });
    el("nbFontDown").addEventListener("click", () => {
      state.font = clamp(state.font - 10, 70, 200);
      saveSoon();
      updateFoot();
      ui.sheet.style.setProperty("--nb-font", state.font + "%");
    });

    el("nbCopyAll").addEventListener("click", copyAll);
    el("nbClearAll").addEventListener("click", async () => {
      if (!state.blocks.length) { toast("简记本来就是空的"); return; }
      // 用应用内确认框：简记窗口很窄，系统的 confirm 会把"确定"按钮切到看不见的地方
      const yes = window.appConfirm
        ? await window.appConfirm("清空这条简记吗？\n文字和图片都会被删掉。", "清空")
        : window.confirm("清空这条简记吗？文字和图片都会被删掉。");
      if (!yes) return;
      state.blocks.forEach((b) => { if (b.key) dbDel(b.key); });
      state.blocks = [];
      editingId = null;
      saveNow();
      render();
      toast("简记已清空");
    });

    // 在简记模式下：粘贴图片 → 进简记（不给推理墙）
    window.addEventListener("paste", (e) => {
      if (!active()) return;
      const items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      const files = [];
      for (const it of items) {
        if (it.kind === "file" && it.type.startsWith("image/")) files.push(it.getAsFile());
      }
      if (files.length) { e.preventDefault(); e.stopPropagation(); addImages(files); }
    }, true);

    // 在简记模式下：拖进来的图片 → 进简记
    window.addEventListener("dragover", (e) => {
      if (!active()) return;
      e.preventDefault();
      document.body.classList.add("nb-drag-over");
    });
    window.addEventListener("dragleave", (e) => {
      if (!active()) return;
      if (!e.relatedTarget) document.body.classList.remove("nb-drag-over");
    });
    window.addEventListener("drop", (e) => {
      if (!active()) return;
      e.preventDefault();
      e.stopPropagation();
      document.body.classList.remove("nb-drag-over");
      const files = e.dataTransfer ? e.dataTransfer.files : null;
      if (files && files.length) addImages(files);
    }, true);

    // Esc：先退出正在编辑的段落
    window.addEventListener("keydown", (e) => {
      if (!active()) return;
      if (e.key === "Escape" && menuEl) { e.preventDefault(); closeMenu(); return; }
      if (e.key === "Escape" && editingId) { e.preventDefault(); endEdit(true); }
      else if (e.key === "Escape" && el("decipherPanel").classList.contains("open")) {
        el("decipherPanel").classList.remove("open");
      }
    }, true);

    // 点一段文字就进入编辑；点空白处就直接在末尾开一段新的（可以马上打字）
    ui.sheet.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".nb-kill")) return;              // 删除按钮自己处理
      if (e.target.closest(".nb-block.editing")) return;     // 正在编辑这一段

      const wrap = e.target.closest(".nb-block");
      const view = e.target.closest(".nb-view");
      const wasEditing = !!editingId;
      if (wasEditing) endEdit(true);

      if (wrap && view && !e.target.closest("a")) {
        // 点已有的文字 → 改它
        const block = state.blocks.find((b) => b.id === wrap.dataset.id);
        if (!block) return;
        e.preventDefault();
        beginEdit(block);
        return;
      }
      if (wrap || e.target.closest("a")) return;             // 点图片、链接：别乱插

      // 点空白处：直接继续写
      e.preventDefault();
      const last = state.blocks[state.blocks.length - 1];
      if (last && last.type === "text" && !String(last.text || "").trim()) beginEdit(last);
      else addText("");
    }, true);
  }

  async function copyAll() {
    const parts = state.blocks.map((b) => (b.type === "image" ? "（图片：" + (b.name || "图片") + "）" : (b.text || "")));
    const text = parts.join("\n\n").trim();
    if (!text) { toast("简记还是空的"); return; }
    try {
      await navigator.clipboard.writeText(text);
    } catch (err) {
      const tmp = document.createElement("textarea");
      tmp.value = text;
      document.body.appendChild(tmp);
      tmp.select();
      document.execCommand("copy");
      tmp.remove();
    }
    toast("已经把简记全文复制到剪贴板");
  }

  /* ---------------- 初始化 ---------------- */

  function init() {
    if (!el("noteApp")) return;
    ui = {
      sheet: el("nbSheet"),
      file: el("nbFileInput"),
      fontVal: el("nbFontVal"),
      count: el("nbCount")
    };
    load();
    bindEvents();
    render();
    dbReady = openDB();
    dbReady.then(hydrateAll);
    if (window.__pageStep) window.__pageStep("简记模块就绪");
  }

  window.NoteApp = {
    active: active,
    setMode: setMode,
    open: function () { setMode("note"); },
    appendText: function (text) { addText(text); },
    appendImageBlob: appendImageBlob,      // 墙上的照片 / 破译结果 → 简记
    count: function () { return state.blocks.length; },
    // 导出 / 导入：让 .lc 文件把简记（含图片）一起带走
    exportPayload: exportPayload,
    importPayload: importPayload,
    hasContent: hasContent,
    stateForDiff: stateForDiff,
    reset: reset,
    toast: toast
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
