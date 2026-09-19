/* ========== 破译台：调用本地破译服务，把结果钉到推理墙上 ========== */
(function () {
  "use strict";

  const API = "/api/decipher";
  const METHODS_API = "/api/methods";
  const PING = "/api/ping";

  const el = (id) => document.getElementById(id);
  let ui = null;
  let online = null;          // null=未检测 / true / false
  let lastResult = null;
  let mode = "smart";         // smart=智能模式 / manual=常规模式
  let methods = null;         // 手法清单（常规模式用，第一次切过去才拉）

  /* ---------------- 两种模式 ---------------- */

  function setMode(next) {
    mode = next === "manual" ? "manual" : "smart";
    const manual = mode === "manual";
    if (ui.modeSmart) ui.modeSmart.classList.toggle("active", !manual);
    if (ui.modeManual) ui.modeManual.classList.toggle("active", manual);
    if (ui.depthField) ui.depthField.hidden = manual;
    if (ui.methodField) ui.methodField.hidden = !manual;
    if (ui.rule) {
      ui.rule.hidden = manual;
      ui.rule.textContent = "智能模式：按初步判断的类型依次给结果，每种手法最多 3 条、总共最多 15 条。";
    }
    if (manual) {
      loadMethods().then(() => {
        if (lastResult) runDecipher();          // 已经解过一次就顺手重解
      });
    } else if (lastResult) {
      runDecipher();
    }
  }

  // 拉手法清单（只拉一次），按分类分组塞进下拉框
  async function loadMethods() {
    if (methods) return methods;
    if (!(await probeService())) return null;
    try {
      const res = await fetch(METHODS_API, { cache: "no-store" });
      if (!res.ok) throw new Error("服务返回 " + res.status);
      const data = await res.json();
      methods = Array.isArray(data.methods) ? data.methods : [];
    } catch (err) {
      setStatus("拿不到破译方式列表：" + err.message, "error");
      return null;
    }
    if (!ui.method) return methods;
    ui.method.innerHTML = "";
    const byCat = new Map();
    methods.forEach((m) => {
      if (!byCat.has(m.category)) byCat.set(m.category, []);
      byCat.get(m.category).push(m);
    });
    byCat.forEach((items, cat) => {
      const group = document.createElement("optgroup");
      group.label = cat;
      items.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.name;
        opt.textContent = m.name;
        if (m.description) opt.title = m.description;
        group.appendChild(opt);
      });
      ui.method.appendChild(group);
    });
    return methods;
  }

  // 给本地服务报个进度（日志里能看到页面走到哪一步，卡住时一眼定位）
  function pageStep(step) {
    try {
      fetch(`/api/page?step=${encodeURIComponent(step)}`, { cache: "no-store" }).catch(() => {});
    } catch (err) { /* 忽略 */ }
  }
  window.__pageStep = pageStep;

  /* ---------------- 面板开合 ---------------- */

  function openPanel() {
    el("decipherPanel").classList.add("open");
    el("btnDecipher").classList.add("active");
    probeService();
    setTimeout(() => ui.input.focus(), 260);
  }

  function closePanel() {
    el("decipherPanel").classList.remove("open");
    el("btnDecipher").classList.remove("active");
  }

  function togglePanel() {
    if (el("decipherPanel").classList.contains("open")) closePanel();
    else openPanel();
  }

  /* ---------------- 与破译服务通信 ---------------- */

  // 心跳：网页开着 → 本地服务就知道"有人在用"；网页关掉 → 服务自动退出。
  const SID = "s" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  // 告诉本地服务「这一页是在程序窗口里，还是在浏览器里」——出问题时日志里一眼能看出来
  const SHELL = (function () {
    try {
      if (window.chrome && window.chrome.webview) return "window";
    } catch (err) { /* 忽略 */ }
    return "browser";
  })();

  function heartbeat() {
    fetch(`/api/hello?sid=${SID}&shell=${SHELL}`, { cache: "no-store", keepalive: true }).catch(() => {});
  }

  function sayBye() {
    try {
      navigator.sendBeacon(`/api/bye?sid=${SID}`);
    } catch (err) {
      fetch(`/api/bye?sid=${SID}`, { keepalive: true }).catch(() => {});
    }
  }

  // 手动退出 ARGtool（关掉本地服务）
  async function probeService() {
    if (online === true) return true;
    try {
      const res = await fetch(PING, { cache: "no-store" });
      online = res.ok;
    } catch (err) {
      online = false;
    }
    if (!online) {
      setStatus(
        "破译服务未启动：请双击 ARGtool.exe 打开本站，或在项目目录执行 " +
          "python src/argtool.py 后再访问本页。",
        "error"
      );
    }
    return online;
  }

  function setStatus(text, kind) {
    ui.status.textContent = text || "";
    ui.status.className = "dc-status" + (kind ? " " + kind : "");
  }

  async function runDecipher() {
    const text = ui.input.value;
    if (!text.trim()) {
      setStatus("先把密文粘进来。", "error");
      return;
    }
    if (!(await probeService())) return;

    ui.run.disabled = true;
    const manual = mode === "manual";
    const method = manual ? (ui.method.value || "") : "";
    if (manual && !method) {
      ui.run.disabled = false;
      setStatus("先选一种破译方式。", "error");
      return;
    }
    setStatus(manual ? "正在用「" + method + "」解…" : "正在尝试各种解密方式…", "busy");
    const payload = {
      text,
      mode,
      method,
      depth: ui.depth ? (parseInt(ui.depth.value, 10) || 1) : 1,
      keys: ui.key.value
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean)
    };
    try {
      const res = await fetch(API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error("服务返回 " + res.status);
      const data = await res.json();
      lastResult = data;
      render(data);
      const head = data.mode === "manual" ? "常规模式（" + (data.method || method) + "）：" : "智能模式：";
      setStatus(head + `共 ${data.candidates.length} 条结果，用时 ${data.elapsed.toFixed(2)} 秒。`
        + (data.candidates.length ? "" : (data.mode === "manual"
            ? " 这个手法没解出东西，换一种试试。"
            : " 试试换成「可读性优先」。")));
    } catch (err) {
      setStatus("破译失败：" + err.message, "error");
    } finally {
      ui.run.disabled = false;
    }
  }

  /* ---------------- 结果渲染 ---------------- */

  function accentFor(popularity) {
    if (popularity >= 90) return "#e03333";     // 图钉红：最常见
    if (popularity >= 70) return "#eabe6e";     // 铜黄
    if (popularity >= 50) return "#8fb3c9";     // 冷蓝
    return "#8d8071";                           // 灰：冷门手法
  }

  function render(data) {
    // 「该段密文可能是 xx」：智能模式下才显示
    const likely = data.mode === "manual" ? null : data.likely;
    if (ui.likely) {
      if (likely && likely.name) {
        ui.likely.hidden = false;
        ui.likely.innerHTML = "该段密文可能是<b>" + escapeHtml(shortName(likely.name)) + "</b>";
      } else {
        ui.likely.hidden = true;
        ui.likely.textContent = "";
      }
    }

    ui.hints.innerHTML = "";
    if (data.hints && data.hints.length) {
      data.hints.slice(0, 6).forEach((h) => {
        const chip = document.createElement("span");
        chip.className = "dc-chip";
        chip.textContent = `${h.name} ${h.score}%`;
        ui.hints.appendChild(chip);
      });
    }

    ui.notes.innerHTML = "";
    (data.notes || []).slice(0, 3).forEach((n) => {
      const li = document.createElement("li");
      li.textContent = n;
      ui.notes.appendChild(li);
    });

    ui.list.innerHTML = "";
    if (!data.candidates.length) {
      const empty = document.createElement("div");
      empty.className = "dc-empty";
      empty.textContent = data.mode === "manual"
        ? "这个手法没解出东西。换一种破译方式，或切回智能模式试试。"
        : "没有找到任何候选结果。";
      ui.list.appendChild(empty);
      return;
    }

    data.candidates.forEach((cand, index) => {
      const card = document.createElement("div");
      card.className = "dc-card" + (index === 0 ? " top" : "");
      card.style.setProperty("--dc-accent", accentFor(cand.popularity));

      const head = document.createElement("div");
      head.className = "dc-card-head";
      head.innerHTML =
        `<span class="dc-rank">${index + 1}</span>` +
        `<span class="dc-method" title="${escapeAttr(cand.method)}">${escapeHtml(cand.method)}</span>`;
      card.appendChild(head);

      const body = document.createElement("pre");
      body.className = "dc-text";
      body.textContent = cand.text;
      card.appendChild(body);

      if (cand.note) {
        const note = document.createElement("p");
        note.className = "dc-card-note";
        note.textContent = cand.note;
        card.appendChild(note);
      }

      const btns = document.createElement("div");
      btns.className = "dc-card-btns";
      btns.appendChild(miniBtn("复制", "", () => copyText(cand.text)));
      btns.appendChild(miniBtn("钉到墙上", "pin", () => pinToWall(cand)));
      btns.appendChild(miniBtn("填入输入框", "", () => {
        ui.input.value = cand.text;
        ui.input.focus();
      }));
      card.appendChild(btns);

      ui.list.appendChild(card);
    });
  }

  function miniBtn(label, extra, onClick) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dc-mini" + (extra ? " " + extra : "");
    btn.textContent = label;
    btn.addEventListener("click", onClick);
    return btn;
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      setStatus("已复制到剪贴板。");
    } catch (err) {
      const tmp = document.createElement("textarea");
      tmp.value = text;
      document.body.appendChild(tmp);
      tmp.select();
      document.execCommand("copy");
      tmp.remove();
      setStatus("已复制到剪贴板。");
    }
  }

  function pinToWall(cand) {
    // 简记模式下没有照片墙：破译结果直接插进简记里
    if (window.NoteApp && window.NoteApp.active()) {
      window.NoteApp.appendText(`【${cand.method}】\n${cand.text}`);
      // 插完就把破译台收起来，直接回到笔记界面
      el("decipherPanel").classList.remove("open");
      const nbBtn = el("nbDecipher");
      if (nbBtn) nbBtn.classList.remove("on");
      setStatus("已插入简记。");
      return;
    }
    if (!window.LinkChart || typeof window.LinkChart.addNoteCentered !== "function") {
      setStatus("推理墙还没准备好，稍后再试一次。", "error");
      return;
    }
    const text = `【${cand.method}】\n${cand.text}`;
    window.LinkChart.addNoteCentered(text, 240);
    setStatus("已钉到推理墙上，可以拖动它并连线。");
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    })[ch]);
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/\n/g, " ");
  }

  /* 「摩斯电码（Morse）」→「摩斯电码」：句子里的括号英文读着累 */
  function shortName(name) {
    return String(name || "").replace(/[（(][^）)]*[）)]\s*$/, "").trim() || String(name || "");
  }

  /* ---------------- 初始化 ---------------- */

  function init() {
    if (window.__pageStep) window.__pageStep("网页脚本开始跑");
      ui = {
      panel: el("decipherPanel"),
      input: el("dcInput"),
      depth: el("dcDepth"),
      key: el("dcKey"),
      run: el("dcRun"),
      clear: el("dcClear"),
      status: el("dcStatus"),
      likely: el("dcLikely"),
      hints: el("dcHints"),
      notes: el("dcNotes"),
      list: el("dcList"),
      modeSmart: el("dcModeSmart"),
      modeManual: el("dcModeManual"),
      rule: el("dcRule"),
      depthField: el("dcDepthField"),
      methodField: el("dcMethodField"),
      method: el("dcMethod")
    };
    if (!ui.panel) return;

    el("btnDecipher").addEventListener("click", togglePanel);
    el("dcClose").addEventListener("click", closePanel);
    ui.run.addEventListener("click", runDecipher);
    if (ui.modeSmart) ui.modeSmart.addEventListener("click", () => setMode("smart"));
    if (ui.modeManual) ui.modeManual.addEventListener("click", () => setMode("manual"));

    // 心跳与"关页面就退出"
    heartbeat();
    setInterval(heartbeat, 5000);
    window.addEventListener("pagehide", sayBye);
    window.addEventListener("beforeunload", sayBye);
    ui.clear.addEventListener("click", () => {
      ui.input.value = "";
      ui.list.innerHTML = "";
      ui.hints.innerHTML = "";
      ui.notes.innerHTML = "";
      ui.status.textContent = "";
      ui.input.focus();
    });
    ui.input.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        runDecipher();
      }
    });
    if (window.__pageStep) window.__pageStep("破译台就绪");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
