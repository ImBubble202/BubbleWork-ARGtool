/* ========== 自绘窗口：标题栏（含置顶）+ 拖动 + 拉边 ==========

   这是一个"仿系统窗口"：无边框，标题栏自己画，右边四个按钮：
       📌 置顶 | ─ 最小化 | ▢ 最大化/还原 | ✕ 关闭

   为什么它不会再卡死（上一版踩过的坑，这次全部避开）：
     1. 拖动/拉边**不用**给 Windows 发 WM_NCLBUTTONDOWN/SC_MOVE —— 那是模态循环，会把 UI 冻住；
        这里改成「网页报坐标 → Python 直接 SetWindowPos」，一帧一次，绝不进循环；
     2. Python 侧操作窗口全部是纯 Win32（ctypes），**不调用 .NET**；
     3. js_api 对象里**没有**任何公开的窗口对象属性（pywebview 会递归遍历公开属性，
        上次的死锁就是这么来的）。

   只有用 ARGtool.exe 打开时才会激活；浏览器里这些都不显示，界面照旧。 */
(function () {
  "use strict";

  const EDGES = ["n", "s", "w", "e", "nw", "ne", "sw", "se"];

  let ready = false;
  let dragging = null;
  let resizing = null;

  function bridge() {
    const api = window.pywebview && window.pywebview.api;
    return api && typeof api.state === "function" ? api : null;
  }

  function has(name) {
    const api = bridge();
    return !!(api && typeof api[name] === "function");
  }

  async function call(name, ...args) {
    if (!has(name)) return null;
    try {
      const st = await bridge()[name](...args);
      syncState(st);
      return st;
    } catch (err) {
      return null;
    }
  }

  function syncState(st) {
    if (!st || st.app !== true) return;
    document.body.classList.toggle("win-maximized", !!st.maximized);
    const top = document.getElementById("winTop");
    if (top) {
      top.classList.toggle("on", !!st.on_top);
      top.title = st.on_top ? "取消置顶（不再钉在最前面）" : "置顶窗口（钉在所有窗口前面）";
    }
    const maxBtn = document.getElementById("winMax");
    if (maxBtn) maxBtn.title = st.maximized ? "还原窗口大小" : "放大窗口";
  }

  /* ---------------- 右边四个按钮 ---------------- */

  function bindButtons() {
    const on = (id, fn) => {
      const el = document.getElementById(id);
      if (el && !el.dataset.bound) {
        el.dataset.bound = "1";
        el.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
      }
    };
    on("winTop", () => call("toggle_top"));
    on("winMin", () => call("minimize"));
    on("winMax", () => call("toggle_maximize"));
    // ✕：交给 Python 走「先问一句还有没保存」的流程（Python 会拦下这次关闭，
    // 让页面弹确认框；页面确认后才带 force=True 真的关）。
    on("winClose", () => {
      if (has("close")) call("close");
      else if (window.AppClose) window.AppClose.request();
      else window.close();
    });
  }

  /* ---------------- 拖动窗口（纯坐标上报，不进任何模态循环） ---------------- */

  function startWindowDrag(e) {
    if (!has("drag_start")) return;
    e.preventDefault();
    if (window.__pageStep) window.__pageStep("已请求拖动窗口");
    dragging = { id: e.pointerId };
    document.body.classList.add("win-dragging");
    const bar = document.getElementById("tbDrag");
    try { bar.setPointerCapture(e.pointerId); } catch (err) { /* 忽略 */ }
    call("drag_start", e.screenX, e.screenY);
  }

  function onWindowDragMove(e) {
    if (!dragging || dragging.id !== e.pointerId) return;
    if (dragging.pending) { dragging.latest = e; return; }
    dragging.pending = true;
    dragging.latest = e;
    requestAnimationFrame(() => {
      const ev = dragging && dragging.latest;
      if (dragging) dragging.pending = false;
      if (ev && has("drag_move")) bridge().drag_move(ev.screenX, ev.screenY).catch(() => {});
    });
  }

  function endWindowDrag(e) {
    if (!dragging || (e && dragging.id !== e.pointerId)) return;
    dragging = null;
    document.body.classList.remove("win-dragging");
    call("drag_end");
  }

  function bindDrag() {
    const bar = document.getElementById("tbDrag");
    if (!bar || bar.dataset.bound) return;
    bar.dataset.bound = "1";
    bar.addEventListener("pointerdown", (e) => {
      if (!ready || e.button !== 0) return;
      if (e.target.closest(".tb-btn")) return;
      if (window.__pageStep) window.__pageStep("标题栏收到 pointerdown");
      startWindowDrag(e);
    });
    bar.addEventListener("pointermove", onWindowDragMove);
    bar.addEventListener("pointerup", endWindowDrag);
    bar.addEventListener("pointercancel", endWindowDrag);
    bar.addEventListener("dblclick", (e) => {
      if (!ready || e.target.closest(".tb-btn")) return;
      call("toggle_maximize");
    });
  }

  /* ---------------- 拉边改大小 ---------------- */

  function buildGrips() {
    const box = document.getElementById("winGrips");
    if (!box || box.childElementCount) return;
    EDGES.forEach((edge) => {
      const grip = document.createElement("div");
      grip.className = "wg wg-" + edge;
      grip.dataset.edge = edge;
      grip.addEventListener("pointerdown", (e) => {
        if (!ready || e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        resizing = { edge: edge, id: e.pointerId };
        try { grip.setPointerCapture(e.pointerId); } catch (err) { /* 忽略 */ }
        document.body.classList.add("win-resizing");
        if (has("resize_start")) bridge().resize_start(e.screenX, e.screenY, edge).catch(() => {});
      });
      grip.addEventListener("pointermove", (e) => {
        if (!resizing || resizing.id !== e.pointerId) return;
        if (resizing.pending) { resizing.latest = e; return; }
        resizing.pending = true;
        resizing.latest = e;
        requestAnimationFrame(() => {
          const ev = resizing && resizing.latest;
          if (resizing) resizing.pending = false;
          if (ev && has("resize_move")) bridge().resize_move(ev.screenX, ev.screenY).catch(() => {});
        });
      });
      const stop = (e) => {
        if (!resizing || resizing.id !== e.pointerId) return;
        resizing = null;
        document.body.classList.remove("win-resizing");
        call("resize_end");
      };
      grip.addEventListener("pointerup", stop);
      grip.addEventListener("pointercancel", stop);
      box.appendChild(grip);
    });
  }

  /* ---------------- 对外接口（简记 / 推理墙 切换用） ---------------- */

  window.WindowChrome = {
    active: function () { return ready; },
    state: function () { return null; },
    sync: function () { return call("state"); },
    // 走本地 HTTP 接口，不依赖 JS 桥
    setMode: function (mode) {
      const want = mode === "note" ? "note" : "wall";
      return fetch("/api/window?mode=" + want, { cache: "no-store" })
        .then((res) => (res.ok ? res.json() : null))
        .catch(() => null);
    }
  };

  /* ---------------- 没有程序窗口时的说明 ---------------- */

  function shellParam() {
    try { return new URLSearchParams(location.search).get("shell") || ""; } catch (err) { return ""; }
  }

  function makeNotice(title, body) {
    if (document.getElementById("shellNotice")) return;
    const box = document.createElement("div");
    box.id = "shellNotice";
    box.className = "shell-notice";
    box.innerHTML =
      '<div class="sn-title">' + title + "</div>" +
      '<div class="sn-body">' + body + "</div>" +
      '<button class="sn-close" type="button" title="关掉这条提示">✕</button>';
    box.querySelector(".sn-close").addEventListener("click", () => box.remove());
    document.body.appendChild(box);
  }

  function browserNotice() {
    if (shellParam() !== "browser" || bridge()) return;
    makeNotice(
      "当前是浏览器模式（没有程序窗口）",
      "这台机器上没找到可用的 WebView2 运行时，所以这次是用浏览器打开的：" +
      "没有自绘标题栏、也不能拖着窗口边改大小。<br />" +
      "装上 <b>Microsoft Edge WebView2 Runtime</b> 之后再双击 ARGtool.exe 即可；" +
      "浏览器里其它功能（推理墙、破译台、简记）都一样能用。"
    );
  }

  function brokenShellNotice() {
    if (bridge() || document.getElementById("shellNotice")) return;
    if (!(window.chrome && window.chrome.webview)) return;
    makeNotice(
      "窗口外壳没连上",
      "界面显示正常，但和程序窗口的通信没建立起来，标题栏那几个按钮暂时不生效。<br />" +
      "按 <b>Alt + F4</b> 关掉窗口，再双击一次 ARGtool.exe 通常就好了。"
    );
  }

  /* ---------------- 启动 ---------------- */

  function boot() {
    if (ready || !bridge()) return;
    ready = true;
    document.body.classList.add("has-winbar", "has-titlebar");
    const box = document.getElementById("shellNotice");
    if (box) box.remove();
    buildGrips();
    bindDrag();
    bindButtons();
    call("state");
    // 上报一下标题栏实际位置（出问题时能看出来它到底在哪儿 / 有没有显示）
    const bar = document.getElementById("titlebar");
    const r = bar.getBoundingClientRect();
    if (window.__pageStep) {
      window.__pageStep("标题栏 " + Math.round(r.left) + "," + Math.round(r.top) +
                        " " + Math.round(r.width) + "x" + Math.round(r.height));
    }
    const pin = document.getElementById("winTop");
    if (pin) {
      const p = pin.getBoundingClientRect();
      if (window.__pageStep) {
        window.__pageStep("置顶按钮中心 " + Math.round(p.left + p.width / 2) + "," +
                          Math.round(p.top + p.height / 2));
      }
    }
    // 一次性诊断：页面到底能不能收到鼠标（收到就记一笔）
    if (!window.__clickProbe) {
      window.__clickProbe = true;
      document.addEventListener("pointerdown", (e) => {
        if (window.__clickProbe === true && window.__pageStep) {
          window.__clickProbe = "done";           // 只记第一次，别每次点击都发请求
          window.__pageStep("页面收到点击 client=" + Math.round(e.clientX) + "," + Math.round(e.clientY) +
                            " target=" + (e.target && e.target.id ? e.target.id : (e.target && e.target.className) || "?"));
        }
      }, true);
    }
    // 标题栏占掉一块高度，通知画布重新量一次（坐标换算才准）
    window.dispatchEvent(new Event("resize"));
    if (window.__pageStep) window.__pageStep("网页外壳桥已就绪");
  }

  if (bridge()) boot();
  window.addEventListener("pywebviewready", () => { boot(); bindDrag(); bindButtons(); });
  setTimeout(boot, 600);
  setTimeout(boot, 1800);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", browserNotice);
  } else {
    browserNotice();
  }
  setTimeout(() => {
    if (!bridge()) return;
    const box = document.getElementById("shellNotice");
    if (box) box.remove();
  }, 2200);
  setTimeout(brokenShellNotice, 9000);
  if (window.__pageStep) window.__pageStep("window.js 已加载");
})();
