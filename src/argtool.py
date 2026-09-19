r"""ARGtool —— 把「侦探推理墙」和「通用密码破译器」打包在一起的本地小软件。

运行方式：
    双击 ARGtool.exe（打包后），或者  python src/argtool.py（源码方式）

它会做四件事：
    1. 在本机开一个只监听 127.0.0.1 的小服务，把推理墙网页发出去；
    2. 提供 /api/decipher 接口，把粘贴进来的密文交给破译引擎；
    3. 用一个真正的程序窗口（WebView2）打开界面：窗口是无边框的，标题栏由网页自己画，
       顺序是「置顶 / 最小化 / 放大 / 关闭」；拖动和拉边改大小都是网页报坐标、
       Python 直接 SetWindowPos（不用模态循环，所以不会卡死）。
    4. 界面上还有「🔍 破译台」和「📝 简记」两块：
       破译台负责解密密文，简记把窗口变成一条细长的 Markdown 便签
       （只记文字和图片，没有照片墙和连线）。

    万一 WebView2 用不了，会自动退回浏览器打开，功能一模一样。

   照片和文字（照片墙、简记）都存在「数据目录」里：默认 %APPDATA%\BubbleWork\data，
   界面上点「数据目录」可以换成别的文件夹（关掉程序再打开就生效）。

不会联网、不会上传任何东西，所有计算都在本机完成。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import mimetypes
import os
import shutil
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

APP_NAME = "ARGtool"
APP_VERSION = "1.0.0"
# 代次标记：用来认出「还挂着的旧版本」——旧实例不认这个值，就不会把新窗口顶掉。
APP_BUILD = "2026-09-18-rope2"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 2 * 1024 * 1024          # 粘贴的密文最多 2MB
MAX_CANDIDATES = 20                       # 返回给网页的候选条数（再多也是噪音）

WALL_SIZE = (1180, 820)                   # 推理墙模式窗口尺寸
NOTE_SIZE = (360, 820)                    # 简记模式窗口尺寸（细长条）
NOTE_WIDTH_RATIO = 0.19                   # 简记窗口宽度 ≈ 屏幕宽度的 19%（贴右侧、上下铺满）


# --------------------------------------------------------------------------
# 路径：同时支持「源码运行」和「PyInstaller 打包后的单文件 exe」
# --------------------------------------------------------------------------

def resource_dir() -> Path:
    """返回打包进 exe 的只读资源目录（web/、decipherer/ 都在这里）。"""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent


WEB_DIR = resource_dir() / "web"


def app_icon_path() -> Path | None:
    """程序自己的图标（窗口图标 / 任务栏都它）。

    打包后在 exe 里的 assets/ 下；源码方式运行时在项目根的 assets/ 下。
    """
    for cand in (resource_dir() / "assets" / "BubbleWork.ico",
                 Path(__file__).resolve().parent.parent / "assets" / "BubbleWork.ico"):
        try:
            if cand.is_file():
                return cand
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------
# 数据目录：照片墙 / 简记里的「文字」和「图片」全都落在这一个文件夹里
# --------------------------------------------------------------------------
#   · 文字：照片墙的物品与连线、案件名、简记正文、最近用过的线色
#           → WebView2 的 localStorage
#   · 图片：墙上的照片、简记里的图片
#           → WebView2 的 IndexedDB（一张图一条记录）
#   这两样都由 WebView2 按「用户档案目录」落盘，档案目录 = 传给它的 storage_path。
#
#   <数据目录>\
#     └─ EBWebView\Default\Local Storage\leveldb\      ← 文字（照片墙 / 简记 / 线色）
#         EBWebView\Default\IndexedDB\...leveldb\      ← 图片（一张图一条记录）
#
#   ⚠️ 都是浏览器引擎自己的数据库文件，不是「一张图一个文件」，所以备份／
#      换电脑／换硬盘要**整个数据目录一起拷**。
#   ⚠️ 数据按「网址」归档（http://127.0.0.1:8765），端口别乱改，否则等于换了
#      一个站点、老数据会「看不见」。
#   默认放在 %APPDATA%\BubbleWork\data，界面上点「数据目录」可以改成别处。

def roaming_dir() -> Path:
    """漫游目录（%APPDATA%）。"""
    base = os.environ.get("APPDATA")
    if base and Path(base).is_dir():
        return Path(base)
    return Path.home() / "AppData" / "Roaming"


CONFIG_DIR = roaming_dir() / "BubbleWork"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_DATA_DIR = CONFIG_DIR / "data"
PROFILE_SUBDIR = "EBWebView"        # WebView2 的档案目录（文字 + 图片都在里面）


def load_config() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_config(cfg: dict) -> bool:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        return True
    except Exception as exc:
        log_error(f"[数据目录] 配置文件写不进去：{exc!r}")
        return False


def ensure_writable_dir(path: Path) -> Path | None:
    """能建、能写就返回这个目录；否则返回 None。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".argtool_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return path
    except Exception as exc:
        log_error(f"[数据目录] 这个位置不能用：{path} —— {exc!r}")
        return None


def resolve_data_dir(explicit: str | None = None) -> Path:
    """定下这次运行的数据目录：命令行 > 配置文件 > 默认位置。"""
    cfg_dir = load_config().get("data_dir")
    wanted = (explicit or "").strip()
    source = "命令行"
    if not wanted and isinstance(cfg_dir, str) and cfg_dir.strip():
        wanted, source = cfg_dir.strip(), "配置文件"
    if wanted:
        chosen = ensure_writable_dir(Path(wanted).expanduser())
        if chosen is not None:
            log_event("数据目录", f"用{source}里的位置：{chosen}")
            return chosen
        log_error(f"[数据目录] {source}里的位置用不了，改用默认位置：{DEFAULT_DATA_DIR}")

    chosen = ensure_writable_dir(DEFAULT_DATA_DIR)
    if chosen is not None:
        log_event("数据目录", f"用默认位置：{chosen}")
        return chosen

    spare = ensure_writable_dir(Path(os.environ.get("TEMP") or ".") / "BubbleWork_data")
    if spare is not None:
        log_error(f"[数据目录] 默认位置也写不了，这次先放到临时目录：{spare}")
        return spare
    return DEFAULT_DATA_DIR          # 全都不行：交给 pywebview 自己报错


def copy_profile_data(src_root: Path, dst_root: Path) -> list[str]:
    """只搬「文字 + 图片」这两块（缓存不搬，快很多）。"""
    moved: list[str] = []
    for rel in (Path("Default") / "Local Storage", Path("Default") / "IndexedDB"):
        src = src_root / rel
        if not src.is_dir():
            continue
        try:
            shutil.copytree(src, dst_root / rel, dirs_exist_ok=True)
            moved.append(rel.as_posix())
        except Exception as exc:
            log_error(f"[数据目录] 搬 {rel} 失败：{exc!r}")
    return moved


def profile_has_data(profile: Path) -> bool:
    """这个用户档案里到底有没有我们的东西（图片库有目录，或文字库里带我们的键）。"""
    if (profile / "Default" / "IndexedDB").is_dir():
        return True
    try:
        for f in (profile / "Default" / "Local Storage" / "leveldb").glob("*.log"):
            blob = f.read_bytes()
            if b"linkchart" in blob or b"bubblework" in blob:
                return True
    except Exception:
        pass
    return False


def migrate_old_profile(data_dir: Path) -> None:
    """老版本没指定数据目录，WebView2 把档案建在临时目录里。

    这次启动如果发现老档案里真的有东西，就整体搬进新数据目录 —— 别让用户
    觉得「更新一次，墙上的东西全没了」。
    """
    dest = data_dir / PROFILE_SUBDIR
    try:
        if dest.exists():
            return
    except Exception:
        return

    candidates: list[tuple[float, Path]] = []
    legacy = roaming_dir() / "pywebview" / PROFILE_SUBDIR      # pywebview 的默认位置
    try:
        if legacy.is_dir() and profile_has_data(legacy):
            candidates.append((legacy.stat().st_mtime, legacy))
    except Exception:
        pass
    temp_root = Path(os.environ.get("TEMP") or "")
    try:
        if temp_root.is_dir():
            for item in temp_root.glob("tmp*"):                # 老版本用的临时档案
                profile = item / PROFILE_SUBDIR
                if profile.is_dir() and profile_has_data(profile):
                    candidates.append((profile.stat().st_mtime, profile))
    except Exception:
        pass

    if not candidates:
        return
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    src = candidates[0][1]
    try:
        dest.mkdir(parents=True, exist_ok=True)
        moved = copy_profile_data(src, dest)
        log_event("数据目录", f"老档案里的数据搬进新数据目录：{src} → {moved}")
    except Exception as exc:
        log_error(f"[数据目录] 老档案搬迁失败（不影响新版本使用）：{exc!r}")


def import_engine():
    """导入破译引擎（放在函数里，方便打包时被 PyInstaller 正确收集）。"""
    src_dir = Path(__file__).resolve().parent
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    from decipherer import Options, __version__, analyze  # noqa: WPS433
    from decipherer.core import all_decoders
    from decipherer.ngram import model_info

    return analyze, Options, model_info, __version__, all_decoders


# --------------------------------------------------------------------------
# HTTP 服务
# --------------------------------------------------------------------------

class ArgToolHandler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{APP_VERSION}"
    protocol_version = "HTTP/1.1"

    # ---------- 工具方法 ----------

    def _send(self, status: int, body: bytes, content_type: str,
              extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send_error_text(self, status: int, message: str) -> None:
        self._send(status, message.encode("utf-8"),
                   "text/plain; charset=utf-8")

    def log_message(self, fmt: str, *args) -> None:      # 安静一点
        if os.environ.get("ARGTOOL_VERBOSE"):
            super().log_message(fmt, *args)

    # ---------- 静态文件 ----------

    def _safe_path(self, raw_path: str) -> Path | None:
        path = unquote(raw_path.split("?", 1)[0])
        if path in ("", "/"):
            path = "/index.html"
        target = (WEB_DIR / path.lstrip("/")).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            return None
        return target

    def do_GET(self) -> None:                            # noqa: N802
        route = urlparse(self.path).path
        query = urllib.parse.parse_qs(urlparse(self.path).query)
        if route == "/api/hello":
            touch_session((query.get("sid") or ["-"])[0],
                          self.headers.get("User-Agent", ""),
                          (query.get("shell") or [""])[0])
            self._send_json({"ok": True})
            return
        if route == "/api/page":
            # 页面自己报「我走到哪一步了」，每种步骤只记一次，方便定位卡在哪
            step = (query.get("step") or ["?"])[0][:60]
            with PAGE_STEPS_LOCK:
                is_new = step not in PAGE_STEPS
                PAGE_STEPS.add(step)
            if is_new:
                log_event("页面", f"页面走到：{step}")
            self._send_json({"ok": True})
            return
        if route == "/api/window":
            # 「推理墙 / 简记」切换走本地 HTTP，不经过 pywebview 的 JS 桥：
            # 纯 Win32 调窗口，跨线程安全，桥出问题时也不影响这个功能。
            want = (query.get("mode") or ["wall"])[0]
            state = WINDOW_API.set_mode(want)
            hwnd = WINDOW_API._hwnd()
            log_event("窗口", f"切到「{state.get('mode')}」模式 rect={WINDOW_API._rect(hwnd)} 句柄={hwnd}")
            self._send_json({"ok": True, "state": state})
            return
        if route == "/api/data-dir":
            # 网页问「照片和文字到底存在哪个文件夹」——窗口模式和浏览器模式都能问
            info = WINDOW_API.data_dir_info()
            info["windowed"] = getattr(WINDOW_API, "_window", None) is not None
            self._send_json(info)
            return
        if route == "/api/open-wait":
            # 网页长轮询：有没有人双击 .lc 要我打开？有没有人要关窗口？
            try:
                wait_s = float((query.get("timeout") or ["20"])[0])
            except ValueError:
                wait_s = 20.0
            event = wait_event(max(1.0, min(60.0, wait_s)))
            if event is None:
                self._send_json({"ok": True, "token": None, "close": False})
                return
            kind, token, path = event
            if kind == "close":
                log_event("关闭", "把「有人要关窗口」转给页面去确认（UA="
                                  + str(self.headers.get("User-Agent") or "")[:70] + "）")
                self._send_json({"ok": True, "token": None, "close": True})
                return
            self._send_json({"ok": True, "token": token, "name": Path(path).name,
                             "dir": str(Path(path).parent), "close": False})
            return
        if route == "/api/session":
            # 页面问「这次是第几次运行」：会话号变了 = 上一轮的东西该清空了
            self._send_json({"ok": True, "session": SESSION_ID, "build": APP_BUILD})
            return
        if route == "/api/methods":
            # 「常规模式」用的手法清单（前端按分类分组显示）
            self._send_json({"ok": True, "methods": method_catalog()})
            return
        if route == "/api/open-file":
            # 只认登记过的 token，页面不能拿这个接口读别的文件
            token = (query.get("token") or [""])[0]
            path = open_file_path(token)
            if not path or not Path(path).is_file():
                self._send_error_text(404, "这个文件已经不在原来的位置了")
                return
            try:
                body = Path(path).read_bytes()
            except Exception as exc:
                self._send_error_text(500, f"读不了这个文件：{exc!r}")
                return
            log_event("打开文件", f"网页把文件取走了：{path}（{len(body)} 字节）")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if route == "/api/bye":
            drop_session((query.get("sid") or ["-"])[0])
            self._send_json({"ok": True})
            return
        if route == "/api/ping":
            with SESSIONS_LOCK:
                session_count = len(SESSIONS)
            self._send_json({"ok": True, "app": APP_NAME, "version": APP_VERSION,
                             "build": APP_BUILD,
                             "engine": ENGINE_VERSION, "model": ENGINE_MODEL,
                             "sessions": session_count,
                             "window_sessions": window_session_count(),
                             "page_steps": sorted(PAGE_STEPS),
                             "windowed": IS_WINDOWED,
                             "ticks": WATCHDOG_TICKS})
            return
        if route == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return

        target = self._safe_path(self.path)
        if target is None or not target.is_file():
            self._send_error_text(404, "404 找不到这个文件：" + route)
            return
        ctype, _ = mimetypes.guess_type(str(target))
        if target.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif target.suffix in (".html", ".htm"):
            ctype = "text/html; charset=utf-8"
        elif target.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        self._send(200, target.read_bytes(), ctype or "application/octet-stream")

    def do_HEAD(self) -> None:                           # noqa: N802
        self.do_GET()

    # ---------- 破译接口 ----------

    def do_POST(self) -> None:                           # noqa: N802
        route = urlparse(self.path).path
        if route == "/api/backup":
            # 「上一轮」的自动保险：启动清空前，页面把还没保存的东西整包发过来，
            # 这里落到数据目录里，万一误关还能用「导入」捡回来。
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 2 or length > MAX_BACKUP_BYTES:
                self._send_json({"ok": False, "error": "内容为空或过大"})
                return
            target = backup_path()
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "wb") as fh:
                    left = length
                    while left > 0:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            break
                        fh.write(chunk)
                        left -= len(chunk)
            except Exception as exc:
                log_error(f"[备份] 写不了 {target}：{exc!r}")
                self._send_json({"ok": False, "error": repr(exc)})
                return
            log_event("备份", f"把上一轮没保存的内容存到了：{target}（{length} 字节）")
            self._send_json({"ok": True, "file": str(target), "bytes": length})
            return
        if route == "/api/open":
            # 双击 .lc 的第二个进程把文件交过来（也可能来自别的程序调用）
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = b""
            if 0 < length <= 64 * 1024:
                try:
                    raw = self.rfile.read(length)
                except Exception:
                    raw = b""
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            token = register_open_file(str(payload.get("path") or ""))
            if token is None:
                self._send_json({"ok": False, "error": "这个文件打不开（可能已经不在了）"})
                return
            # 把窗口弹到最前面，免得用户以为「没反应」
            WINDOW_API.bring_to_front()
            self._send_json({"ok": True, "token": token})
            return
        if route == "/api/shutdown":
            self._send_json({"ok": True, "bye": True})
            threading.Thread(target=lambda: (time.sleep(0.2), SHUTDOWN.set()),
                             daemon=True).start()
            return
        if route == "/api/bye":                      # 页面关闭时用 sendBeacon（POST）上报
            query = urllib.parse.parse_qs(urlparse(self.path).query)
            drop_session((query.get("sid") or ["-"])[0])
            self._send_json({"ok": True})
            return
        if route != "/api/decipher":
            self._send_error_text(404, "404 没有这个接口")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_error_text(400, "请求内容为空或过大")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error_text(400, "请求不是合法的 JSON")
            return

        text = str(payload.get("text") or "")
        if not text.strip():
            self._send_error_text(400, "密文为空")
            return

        rank_mode = payload.get("rank_mode") or "common"
        if rank_mode not in ("common", "balanced", "confidence"):
            rank_mode = "common"
        try:
            depth = int(payload.get("depth") or 1)
        except (TypeError, ValueError):
            depth = 1
        keys = [str(k) for k in (payload.get("keys") or []) if str(k).strip()]

        # 两种模式：
        #   smart  = 智能模式：所有手法都试一遍，结果按「常见度优先」排，摩斯排最前
        #   manual = 常规模式：用户点名一种手法，就只跑这一种、只给它结果（一层）
        mode = "manual" if str(payload.get("mode") or "") == "manual" else "smart"
        method = str(payload.get("method") or "")
        skipped = 0
        if mode == "manual":
            if not method:
                self._send_error_text(400, "常规模式要先选一种破译方式")
                return
            options = OPTIONS_CLASS(
                max_depth=1,
                keys=keys,
                rank_mode="common",
                only_names={method},
            )
        elif keys:
            # 填了密钥 → 那些「用不到密钥」的格式解码（Base64 / 摩斯 / 进制转换…）就不试了
            keep = {spec.name for spec in ALL_DECODERS()
                    if spec.name not in FORMAT_ONLY_METHODS}
            skipped = len(ALL_DECODERS()) - len(keep)
            options = OPTIONS_CLASS(
                max_depth=max(1, min(3, depth)),
                keys=keys,
                rank_mode="common",
                only_names=keep,
            )
        else:
            options = OPTIONS_CLASS(
                max_depth=max(1, min(3, depth)),
                keys=keys,
                rank_mode="common",
            )
        try:
            result = ANALYZE(text, options)
        except Exception as exc:                          # 引擎出错也要给网页一个答复
            self._send_json({"error": f"破译引擎出错：{exc!r}"}, status=500)
            return

        # 注意：这里**不能**先截断再排序 —— 智能模式要按「初步判断的类型」重新排，
        # 真正对的那条（比如 URL 直解出来的明文）往往被引擎按常见度排到很后面，
        # 先截断就把它扔掉了。
        all_candidates = [
            {
                "popularity": cand.popularity,
                "method": cand.chain_text,
                "kind": cand.root or cand.method,      # 这条结果「从哪种手法开始解」的
                "text": cand.text,
                "note": cand.note,
                "depth": cand.depth,
            }
            for cand in result.candidates
        ]
        hints = [{"name": name, "score": round(score * 100)}
                 for name, score in result.input_hints]
        likely = hints[0] if hints else None

        notes = list(result.notes)
        if mode == "smart":
            order = hints
            if keys:
                # 用户填了密钥 → 先把「真的会用到密钥」的手法排最前面
                keyed = [{"name": name, "score": 100} for name in KEYED_METHODS
                         if any(c["kind"] == name for c in all_candidates)]
                order = keyed + [h for h in hints if h["name"] not in KEYED_METHODS]
            candidates = smart_order(all_candidates, order)
            if skipped:
                notes.insert(0, f"填了密钥 → 已自动跳过 {skipped} 种「不需要密钥的格式解码」"
                                "（Base64、摩斯、进制转换之类）")
            # 「该段密文可能是 X」跟排在最前面的那条保持一致
            if candidates:
                likely = {"name": candidates[0]["kind"],
                          "score": next((h["score"] for h in hints
                                         if h["name"] == candidates[0]["kind"]), 100)}
        else:
            candidates = all_candidates[:MAX_CANDIDATES]

        self._send_json({
            "mode": mode,
            "input": result.source,
            "elapsed": round(result.elapsed, 3),
            "evaluated": result.evaluated,
            "hints": hints,
            "likely": likely,          # 「该段密文可能是 xx」用的
            "method": method if mode == "manual" else "",
            "notes": notes,
            "candidates": candidates,
        })


# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------

def find_free_port(preferred: int) -> int:
    """找一个真的没人用的端口。

    注意：Windows 上如果给探测用的 socket 设了 SO_REUSEADDR，别人已经占着的端口
    也能"绑"上（实测能绑成功），于是会误判成空闲、两个程序抢同一个端口。
    所以这里故意不设 SO_REUSEADDR —— 只有真空闲才算数。
    """
    for candidate in [preferred, *range(preferred + 1, preferred + 40), 0]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return probe.getsockname()[1]
    raise RuntimeError("找不到可用端口")


def banner(url: str) -> str:
    return (
        "\n"
        "  ╔══════════════════════════════════════════════════╗\n"
        "  ║   ARGtool · 推理墙 + 破译台 + 简记               ║\n"
        "  ╚══════════════════════════════════════════════════╝\n"
        f"\n   界面地址： {url}\n"
        "   浏览器没自动打开的话，把上面这行复制到地址栏即可。\n"
        "\n   界面里点右上角「破译台」即可粘贴密文。\n"
        "   关闭这个黑色窗口 = 退出程序。\n"
    )


def safe_console() -> None:
    """Windows 控制台默认是 GBK，直接打印中文以外的字符会崩，这里统一成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# --------------------------------------------------------------------------
# 无控制台模式（--windowed 打包时）：用系统弹窗代替命令行输出
# --------------------------------------------------------------------------

def _has_real_console() -> bool:
    """有没有真正的控制台？

    最可靠的办法是直接问 Windows：这个进程有没有挂控制台窗口。
    （PyInstaller 的 --windowed 版本里 sys.stdout 往往不是 None，不能靠它判断。）
    """
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        try:
            return sys.stdout is not None and sys.stdout.fileno() >= 0
        except Exception:
            return False


IS_WINDOWED = not _has_real_console()


def notify(title: str, message: str) -> None:
    """有控制台就打印，没有控制台就弹一个系统对话框。"""
    if not IS_WINDOWED:
        print(f"{title}\n{message}")
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)
    except Exception:
        pass


ERROR_LOG = Path(os.environ.get("TEMP") or ".") / "ARGtool_error.log"


def log_error(text: str) -> None:
    """出错时写一份日志（无控制台版本看不到报错，必须留痕）。"""
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}]\n{text}\n")
    except Exception:
        pass


def log_event(tag: str, text: str) -> None:
    """诊断用：把「启动到哪一步了 / 谁连上来了 / 窗口卡了多久」都记进同一份日志。

    出问题时（尤其在没有控制台的 exe 里）这是唯一能看到现场的地方。
    """
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{tag}] {text}\n")
            fh.flush()
    except Exception:
        pass


def install_error_hooks() -> None:
    import traceback

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log_error(text)
        notify(f"{APP_NAME} 出错了", text[-900:] + f"\n\n详细日志：{ERROR_LOG}")

    sys.excepthook = hook
    try:
        threading.excepthook = lambda args: hook(args.exc_type, args.exc_value, args.exc_traceback)
    except Exception:
        pass


# --------------------------------------------------------------------------
# 会话心跳：网页开着程序就活着；网页全关了自动退出
# --------------------------------------------------------------------------

EXIT_GRACE = 20.0          # 所有页面都关掉后再撑多久才退出
SESSION_TIMEOUT = 40.0     # 心跳超过这个时间没来，就认为那个页面已关
STARTUP_GRACE = 150.0      # 启动后等浏览器打开的时间，一直没人来就退出

# sid -> {"t": 最后一次心跳时间, "ua": 浏览器标识, "shell": window / browser}
SESSIONS: dict[str, dict] = {}
SESSIONS_LOCK = threading.Lock()
PAGE_STEPS: set[str] = set()
PAGE_STEPS_LOCK = threading.Lock()
SHUTDOWN = threading.Event()
STARTED_AT = time.monotonic()
LAST_SESSION_LOST: float | None = None
EVER_HAD_SESSION = False
WATCHDOG_TICKS = 0


def touch_session(sid: str, ua: str = "", shell: str = "") -> bool:
    """记一次心跳。返回 True 表示这是个新会话（顺手把它的来源记进日志）。"""
    global LAST_SESSION_LOST, EVER_HAD_SESSION
    key = sid or "-"
    with SESSIONS_LOCK:
        is_new = key not in SESSIONS
        SESSIONS[key] = {"t": time.monotonic(), "ua": ua, "shell": shell}
        LAST_SESSION_LOST = None
        EVER_HAD_SESSION = True
    if is_new:
        kind = "程序窗口里的页面" if is_window_session_value(ua, shell) else "浏览器页签"
        log_event("连接", f"{kind}连上来了（sid={key[:10]}） UA={ua[:100]}")
    return is_new


def is_window_session_value(ua: str, shell: str) -> bool:
    return shell == "window" or "WebView2" in (ua or "")


def window_session_count() -> int:
    """只数「程序窗口里的那个页面」，避免被其它浏览器页签骗到。"""
    with SESSIONS_LOCK:
        return sum(1 for info in SESSIONS.values()
                   if is_window_session_value(info.get("ua", ""), info.get("shell", "")))


def session_count() -> int:
    with SESSIONS_LOCK:
        return len(SESSIONS)


def drop_session(sid: str) -> None:
    with SESSIONS_LOCK:
        SESSIONS.pop(sid or "-", None)


def session_watchdog() -> None:
    """后台线程：没人看网页了就退出，免得无窗口版本一直悄悄挂在后台。"""
    global LAST_SESSION_LOST, WATCHDOG_TICKS
    while not SHUTDOWN.wait(2.0):
        WATCHDOG_TICKS += 1
        now = time.monotonic()
        with SESSIONS_LOCK:
            for sid in [k for k, info in SESSIONS.items() if now - info.get("t", 0) > SESSION_TIMEOUT]:
                SESSIONS.pop(sid, None)
            alive = bool(SESSIONS)
        if alive:
            LAST_SESSION_LOST = None
            continue
        if LAST_SESSION_LOST is None:
            LAST_SESSION_LOST = now
        if not EVER_HAD_SESSION:
            # 从来没人连上来（浏览器被拦住 / 直接双击了但没开页面）→ 等宽限期过去就退出
            if now - STARTED_AT > STARTUP_GRACE:
                SHUTDOWN.set()
        elif now - LAST_SESSION_LOST > EXIT_GRACE:
            # 网页关掉之后 → 过一小会儿自动退出
            SHUTDOWN.set()


def running_instance(port: int) -> dict | None:
    """默认端口上已经有一个 ARGtool 在跑？在跑的话返回它自报的信息。"""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if isinstance(data, dict) and data.get("app") == APP_NAME:
        return data
    return None


def browser_url(url: str) -> str:
    """浏览器模式专用网址：让页面知道「这次没有程序窗口」，可以给个说明。"""
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}shell=browser"


# --------------------------------------------------------------------------
# 「双击 .lc 用本程序打开」
# --------------------------------------------------------------------------
# 双击 .lc 时 Windows 会把文件路径当参数传给 exe。这里把路径登记成一个带随机
# token 的「待打开文件」，页面（程序窗口或浏览器都行）长轮询把它取走，再走和
# 「导入」完全一样的那条路。
#   · 已经有一个实例在跑 → 这个新进程把路径 POST 给它，然后自己退出；
#   · 文件内容只按 token 发放，页面没法拿这个接口去读别的文件。

OPEN_SUFFIXES = (".lc", ".linkchart", ".json")
OPEN_LOCK = threading.Condition()
PENDING_OPEN: list[tuple[str, str]] = []      # [(token, 绝对路径)] 等着网页来取
SERVE_FILES: dict[str, str] = {}              # token -> 绝对路径（网页来下载内容时用）


# --------------------------------------------------------------------------
# 「一次运行 = 一张白纸」：每次启动生成一个会话号，页面发现会话号变了就清空上一轮
# --------------------------------------------------------------------------
SESSION_ID = uuid.uuid4().hex

# 关窗口前先问一句「还有没保存的东西吗」：页面确认之前不真的关
CLOSE_GUARD = {"pending": False, "allow": False}
BACKUP_NAME = "上一轮未保存.lc"
MAX_BACKUP_BYTES = 512 * 1024 * 1024


_METHOD_CACHE: list[dict] | None = None

# --------------------------------------------------------------------------
# 智能模式的排序规则
# --------------------------------------------------------------------------
#   · 先看「初步判断出来最像哪种密文」，就按这个顺序依次给结果：
#     最像的那种手法的结果在最上面（同一组里「一层直接解出来」的排在前，
#     多层套出来的排在后）；
#   · 每种解密方式最多 3 条，整个列表最多 15 条；
#   · 用户填了密钥 → 自动跳过那些「根本用不到密钥」的格式解码
#     （Base64、摩斯之类），免得它们把真正要用密钥的手法挤下去。
MAX_SMART_RESULTS = 15
MAX_PER_METHOD = 3

FORMAT_ONLY_METHODS = frozenset({
    # Base 系列
    "Base64 解码", "Base32 解码", "Base58 解码", "Base85 / ASCII85 解码",
    # 进制 / 转义
    "十六进制解码", "八进制解码", "二进制转文本", "十进制 ASCII 码", "十进制补零字节",
    "URL 百分号解码", "HTML 实体解码", "Unicode / 转义序列解码", "Quoted-Printable 解码",
    # 一眼能认出来的编码
    "摩斯电码（Morse）", "盲文点字（Unicode Braille）", "A1Z26 数字字母",
    "波利比奥斯方阵（Polybius）", "敲击码（Tap Code）", "手机九宫格多按（T9 Multi-tap）",
    "键盘错位（QWERTY 左右手滑）", "花体 / 全角字符还原", "Leet / 火星文还原",
    # 隐写 / 杂项
    "零宽字符隐写", "空格 / 制表符二进制", "大小写二进制",
    "藏头 / 首字母提取", "整段倒序 / 单词倒序",
    "JWT 解析", "zlib / Deflate 解压", "gzip 解压", "Brainfuck 解释执行",
})

# 真正会用到「密钥」栏的手法（填了密钥时，它们的结果排最前面）
KEYED_METHODS = ("维吉尼亚密码", "列换位密码", "多字节异或（已知密钥）")


def smart_order(candidates: list[dict], hints: list[dict]) -> list[dict]:
    """智能模式的最终顺序：按「初步判断的类型」依次给，每种最多 3 条，最多 15 条。"""
    order = [str(h.get("name") or "") for h in (hints or [])]
    buckets: dict[str, list[dict]] = {}
    rest: list[dict] = []
    for cand in candidates:
        kind = str(cand.get("kind") or cand.get("method") or "")
        if kind in order:
            buckets.setdefault(kind, []).append(cand)
        else:
            rest.append(cand)

    # 同一种手法内部：「先直解、后套娃」，再按引擎原来的顺序
    def by_depth(items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda c: (int(c.get("depth") or 1),))

    out: list[dict] = []
    for name in order:
        for cand in by_depth(buckets.get(name, []))[:MAX_PER_METHOD]:
            out.append(cand)
            if len(out) >= MAX_SMART_RESULTS:
                return out

    # 判断不出来的那部分：补在后面（同样每种最多 3 条），免得只剩一两条结果
    used: dict[str, int] = {}
    for cand in by_depth(rest):
        kind = str(cand.get("kind") or "?")
        if used.get(kind, 0) >= MAX_PER_METHOD:
            continue
        used[kind] = used.get(kind, 0) + 1
        out.append(cand)
        if len(out) >= MAX_SMART_RESULTS:
            break
    return out


def method_catalog() -> list[dict]:
    """「常规模式」里给用户挑的手法清单（常见度高的排前面）。"""
    global _METHOD_CACHE
    if _METHOD_CACHE is None:
        items = []
        for spec in ALL_DECODERS():
            items.append({
                "name": spec.name,
                "category": spec.category,
                "description": spec.description or "",
                "popularity": spec.popularity if spec.popularity is not None else 0,
            })
        items.sort(key=lambda it: (-it["popularity"], it["name"]))
        _METHOD_CACHE = items
    return _METHOD_CACHE


def backup_path() -> Path:
    """「上一轮未保存」的自动备份放在数据目录里（和数据同生共死）。"""
    return (WINDOW_API._data_dir or DEFAULT_DATA_DIR) / BACKUP_NAME


def register_open_file(raw: str) -> str | None:
    """登记一个「等着网页打开」的文件，返回 token；不是能打开的 .lc 就返回 None。"""
    try:
        path = Path(str(raw)).expanduser()
        if not path.is_file() or path.suffix.lower() not in OPEN_SUFFIXES:
            return None
        path = path.resolve()
    except Exception:
        return None
    token = uuid.uuid4().hex
    with OPEN_LOCK:
        SERVE_FILES[token] = str(path)
        PENDING_OPEN.append((token, str(path)))
        OPEN_LOCK.notify_all()
    log_event("打开文件", f"登记了一个待打开的文件：{path}")
    return token


def request_close_guard() -> None:
    """（给 closing 回调用的）标记「有人要关窗口」，并把等着的页面立刻叫醒。"""
    with OPEN_LOCK:
        CLOSE_GUARD["pending"] = True
        OPEN_LOCK.notify_all()


def wait_event(timeout: float) -> tuple[str, str | None, str | None] | None:
    """网页那边长轮询用：最多等 timeout 秒。

    返回 ("open", token, 路径) / ("close", None, None) / None（啥也没等到）。
    关窗口和双击 .lc 走同一根管子，页面就不用开两条轮询。
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    with OPEN_LOCK:
        while True:
            if CLOSE_GUARD["pending"]:
                CLOSE_GUARD["pending"] = False
                return ("close", None, None)
            if PENDING_OPEN:
                token, path = PENDING_OPEN.pop(0)
                return ("open", token, path)
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            OPEN_LOCK.wait(min(left, 5.0))


def open_file_path(token: str) -> str | None:
    with OPEN_LOCK:
        return SERVE_FILES.get(token or "")


def handoff_open_files(port: int, files: list[str]) -> bool:
    """把「要打开的文件」交给已经跑着的那个实例（它会自己弹到最前面并载入）。"""
    done = False
    for raw in files:
        try:
            payload = json.dumps({"path": str(Path(raw).expanduser())}).encode("utf-8")
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/open", data=payload,
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                ok = bool(json.loads(resp.read().decode("utf-8")).get("ok"))
            log_event("打开文件", f"已把手里的文件交给已在运行的实例：{raw}（{ok}）")
            done = done or ok
        except Exception as exc:
            log_error(f"[打开文件] 交给已在运行的实例失败（{raw}）：{exc!r}")
    return done


def open_files_from_args(argv: list[str] | None) -> list[str]:
    """从命令行里挑出「要打开的文件」（双击 .lc 时系统就是这么传的）。"""
    out: list[str] = []
    for item in (argv if argv is not None else sys.argv[1:]):
        text = str(item).strip().strip('"')
        if not text or text.startswith("-"):
            continue
        try:
            if Path(text).expanduser().is_file():
                out.append(text)
        except Exception:
            continue
    return out


def _sleep_then_open(url: str) -> None:
    time.sleep(0.6)
    try:
        webbrowser.open(url)
    except Exception:
        notify(APP_NAME, f"浏览器没能自动打开，请手动访问：\n{url}")


def screen_size() -> tuple[int, int]:
    """主屏幕分辨率（仅在没有窗口句柄时兜底用）。"""
    try:
        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:
        return 1920, 1080


# --------------------------------------------------------------------------
# 原生窗口：最小化 / 放大 / 关闭 / 置顶 四个按钮 + 拖动 + 拉边缩放
# --------------------------------------------------------------------------

# 只留下「放窗口到指定位置/大小」需要的标志位；
# 拖动、拉边、最小化、最大化、关闭现在全走系统自带边框，不需要我们再碰消息。
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
MONITOR_DEFAULTTONEAREST = 2
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SW_MINIMIZE = 6
WS_EX_TOPMOST = 0x00000008
GWL_EXSTYLE = -20


class MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class WindowApi:
    """网页 ↔ 原生窗口 的桥。

    窗口是无边框的：标题栏由网页自己画，拖动 / 拉边改大小都由网页把坐标报过来，
    这里用纯 Win32（SetWindowPos / ShowWindow / PostMessage）去操作窗口 ——
    全程不走模态循环，也不碰 pywebview 的 .NET 属性，所以不会卡死。
    这里管三件事：**置顶 / 最小化 / 放大 / 关闭**、「推理墙 / 简记」切换时的
    窗口尺寸位置标题、以及**照片和文字存到哪个文件夹（数据目录）**。
    """

    def __init__(self) -> None:
        # ⚠️ 必须用下划线开头！pywebview 注入 JS 桥时会遍历 js_api 对象的所有"公开"属性，
        #    一旦它看到这个 pywebview 窗口对象，就会递归去点 WinForms/DWM 的内部属性
        #    （native.AccessibilityObject.Bounds.Empty.Empty…），在非 UI 线程上狂调 .NET，
        #    直接把 UI 线程和 Python GIL 一起锁死 —— 表现就是「窗口外壳没连上 + 一拖就未响应」。
        #    下划线开头的属性会被跳过，所以这里不能改成 public 的名字。
        self._window = None                      # 由 start_window() 注入
        self.mode = "wall"
        self._max_restore: tuple | None = None  # 最大化之前的 (x, y, w, h)
        self._wall_rect: tuple | None = None    # 推理墙模式的窗口位置尺寸
        self._wall_maximized = False            # 进简记之前，推理墙窗口是不是最大化的
        self._logged_bridge = False             # 「网页外壳连上了」只记一次日志
        self.close_reason = ""                  # "blank" = 白屏被看门线程关掉
        self._hwnd_cache = 0                    # 主窗口句柄缓存（纯 ctypes 找出来的）
        self._drag_start: tuple | None = None   # 自绘标题栏拖动
        self._resize_start: tuple | None = None # 自绘边框拉边
        self._data_dir: Path | None = None      # 照片/文字存到哪个目录（由启动时注入）
        self._picking_dir = False               # 「选择文件夹」对话框是不是开着
        self._centered = False                  # 启动时居中过没有（只做一次）

    # ---------- 底层：窗口句柄 / 物理像素 ----------

    def _native(self):
        win = self._window
        if win is None:
            return None
        try:
            return win.native
        except Exception:
            return None

    def _hwnd(self) -> int:
        """拿本程序主窗口的句柄。

        故意用纯 Win32（EnumWindows）去找，而不是走 .NET 的 Control.Handle：
        从后台线程访问 WinForms 控件属性是有风险的（可能把 UI 线程一起卡住）。
        """
        cached = self._hwnd_cache
        if cached and ctypes.windll.user32.IsWindow(cached):
            return cached
        pid = os.getpid()
        found = {"hwnd": 0}
        user32 = ctypes.windll.user32
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]

        def _visit(hwnd, _lparam):
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value != pid:
                return True
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            # 排除工具窗口（WebView2 之类会弄出一些看不见的辅助窗口）
            ex_style = user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF
            if ex_style & 0x00000080:           # WS_EX_TOOLWINDOW
                return True
            text = ctypes.create_unicode_buffer(length + 2)
            user32.GetWindowTextW(hwnd, text, length + 2)
            # 标题必须以程序名开头 —— 进程里还有 "GDI+ Window (ARGtool.exe)" 之类的窗口，
            # 不卡这一条有可能会抓错窗口，然后去挪它。
            # （自绘窗口没有 WS_CAPTION，所以这里只认标题，不认边框样式。）
            if not text.value.startswith(APP_NAME):
                return True
            found["hwnd"] = int(hwnd)
            return False

        try:
            callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t)(_visit)
            user32.EnumWindows(callback, 0)
        except Exception:
            return 0
        self._hwnd_cache = found["hwnd"]
        return self._hwnd_cache

    def _hwnd_wait(self, timeout: float = 2.0) -> int:
        """等窗口句柄出现（窗口刚创建、还没显示出来的那一小段时间）。"""
        deadline = time.monotonic() + timeout
        while True:
            hwnd = self._hwnd()
            if hwnd or time.monotonic() >= deadline:
                return hwnd
            time.sleep(0.1)

    def _scale(self) -> float:
        """屏幕缩放（125% 这类）。网页给的是逻辑像素，Windows 要物理像素。"""
        try:
            value = float(getattr(self._native(), "_scale", 1.0) or 1.0)
        except Exception:
            value = 1.0
        return value if value > 0 else 1.0

    def _rect(self, hwnd: int) -> tuple | None:
        box = wintypes.RECT()
        if not hwnd or not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(box)):
            return None
        return box.left, box.top, box.right - box.left, box.bottom - box.top

    def _place(self, hwnd: int, x: float, y: float, w: float, h: float) -> bool:
        try:
            return bool(ctypes.windll.user32.SetWindowPos(
                hwnd, 0, int(round(x)), int(round(y)), int(round(w)), int(round(h)),
                SWP_NOZORDER | SWP_NOACTIVATE))
        except Exception:
            return False

    def _move_to(self, hwnd: int, x: float, y: float) -> bool:
        """只挪位置（大小不变）——自绘标题栏拖动窗口用。"""
        try:
            return bool(ctypes.windll.user32.SetWindowPos(
                hwnd, 0, int(round(x)), int(round(y)), 0, 0,
                SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE))
        except Exception:
            return False

    def _work_area(self, hwnd: int) -> tuple[int, int, int, int]:
        """窗口所在那块屏幕的工作区（已经去掉任务栏），物理像素。"""
        try:
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(MonitorInfo)
            monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            if monitor and ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                area = info.rcWork
                return area.left, area.top, area.right - area.left, area.bottom - area.top
        except Exception:
            pass
        width, height = screen_size()
        return 0, 0, width, height

    # ---------- 状态 ----------

    def state(self) -> dict:
        win = self._window
        if win is None:
            return {"app": False}
        if not self._logged_bridge:
            self._logged_bridge = True
            log_event("连接", "网页外壳（pywebview 桥）第一次被调用 —— 前端和窗口的通信是通的。")
        hwnd = self._hwnd()
        rect = self._rect(hwnd) if hwnd else None
        scale = self._scale()
        # 启动后页面第一次问状态时，把窗口摆到屏幕正中间（只做一次；
        # 之后用户自己拖过、拉过，就用他摆的位置，不再插手）
        if self.mode == "wall" and not self._centered and self._wall_rect is None and hwnd:
            self._centered = True
            self.center_wall()
            hwnd = self._hwnd()
            rect = self._rect(hwnd) if hwnd else None
        return {
            "app": True,
            "mode": self.mode,
            "maximized": self._max_restore is not None,
            "on_top": self.is_topmost(),
            "x": int(rect[0]) if rect else 0,
            "y": int(rect[1]) if rect else 0,
            "width": int(rect[2] / scale) if rect else 0,
            "height": int(rect[3] / scale) if rect else 0,
        }

    # ---------- 四个窗口按钮 ----------

    def toggle_maximize(self) -> dict:
        """放大 / 还原。自己算工作区，免得无边框窗口把任务栏也盖住。"""
        hwnd = self._hwnd_wait()
        if not hwnd:
            return self.state()
        if self._max_restore:
            self._unmaximize(hwnd)
        else:
            rect = self._rect(hwnd)
            if rect:
                self._max_restore = rect
                self._place(hwnd, *self._work_area(hwnd))
        return self.state()

    def _unmaximize(self, hwnd: int | None = None) -> None:
        hwnd = hwnd or self._hwnd()
        rect = self._max_restore
        self._max_restore = None
        if hwnd and rect:
            self._place(hwnd, *rect)

    def minimize(self) -> dict:
        """最小化（纯 Win32，不经过 .NET）。"""
        hwnd = self._hwnd_wait()
        if hwnd:
            try:
                ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE)
            except Exception:
                pass
        return self.state()

    # ---------- 置顶（自绘标题栏上的那个图钉） ----------

    def is_topmost(self) -> bool:
        hwnd = self._hwnd()
        if not hwnd:
            return False
        try:
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
            return bool(style & WS_EX_TOPMOST)
        except Exception:
            return False

    def toggle_top(self) -> dict:
        """置顶 / 取消置顶。用 SetWindowPos 换 TOPMOST，不碰 pywebview 的 .NET 属性。"""
        hwnd = self._hwnd_wait()
        if not hwnd:
            return self.state()
        want = not self.is_topmost()
        try:
            user32 = ctypes.windll.user32
            # HWND_TOPMOST/NOTOPMOST 是 -1 / -2，必须声明参数类型，否则 64 位下会传错
            user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            user32.SetWindowPos.restype = ctypes.c_int
            insert_after = ctypes.c_void_p(-1 if want else -2)
            ok = bool(user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0,
                                          SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))
            log_event("窗口", f"置顶={want} 调用结果={ok}")
        except Exception as exc:
            log_event("警告", f"切换置顶失败：{exc!r}")
        return self.state()

    # ---------- 自绘标题栏：拖动窗口（纯 SetWindowPos，绝不进模态循环） ----------

    def drag_start(self, screen_x: float = 0, screen_y: float = 0) -> dict:
        """按住标题栏开始拖。只记下"起点坐标 + 当时的窗口位置"，不碰任何消息循环。"""
        hwnd = self._hwnd_wait()
        if not hwnd:
            return {"app": False}
        if self._max_restore:
            self._unmaximize(hwnd)                 # 最大化时拖一下 = 先还原
        rect = self._rect(hwnd)
        if rect:
            self._drag_start = (float(screen_x), float(screen_y), *rect)
        log_event("窗口", f"开始拖动（起点 {int(screen_x)},{int(screen_y)}，窗口 {rect}）")
        return {"app": True}

    def drag_move(self, screen_x: float, screen_y: float) -> dict:
        hwnd = self._hwnd()
        start = self._drag_start
        if not hwnd or not start:
            return {"app": bool(hwnd)}
        sx, sy, x0, y0 = start[0], start[1], start[2], start[3]
        scale = self._scale()
        self._move_to(hwnd, x0 + (float(screen_x) - sx) * scale,
                      y0 + (float(screen_y) - sy) * scale)
        return {"app": True}

    def drag_end(self) -> dict:
        self._drag_start = None
        log_event("窗口", "结束拖动")
        return self.state()

    # ---------- 自绘边框：拉边改大小（同样是纯 SetWindowPos） ----------

    def resize_start(self, screen_x: float = 0, screen_y: float = 0, edge: str = "se") -> dict:
        hwnd = self._hwnd_wait()
        if not hwnd:
            return {"app": False}
        if self._max_restore:
            self._unmaximize(hwnd)
        rect = self._rect(hwnd)
        if not rect:
            return {"app": False}
        self._resize_start = (float(screen_x), float(screen_y), *rect,
                              str(edge or "se").lower())
        return {"app": True}

    def resize_move(self, screen_x: float, screen_y: float) -> dict:
        hwnd = self._hwnd()
        start = self._resize_start
        if not hwnd or not start:
            return {"app": bool(hwnd)}
        sx, sy, x0, y0, w0, h0, edge = start
        scale = self._scale()
        dx = (float(screen_x) - sx) * scale
        dy = (float(screen_y) - sy) * scale
        min_w, min_h = 360 * scale, 320 * scale
        x, y, w, h = x0, y0, w0, h0
        if "e" in edge:
            w = max(min_w, w0 + dx)
        if "s" in edge:
            h = max(min_h, h0 + dy)
        if "w" in edge:
            w = max(min_w, w0 - dx)
            x = x0 + (w0 - w)
        if "n" in edge:
            h = max(min_h, h0 - dy)
            y = y0 + (h0 - h)
        self._place(hwnd, x, y, w, h)
        return {"app": True}

    def resize_end(self) -> dict:
        self._resize_start = None
        if self.mode == "wall":
            self._wall_rect = self._rect(self._hwnd())
        return self.state()

    def close(self, force: bool = False) -> None:
        """关掉窗口：直接给窗口发 WM_CLOSE，不经过 .NET（跨线程更安全）。

        force=False（默认）时，如果页面还开着，这次关闭会先被「未保存确认」拦下来
        （见 start_window 里的 closing 回调）；页面里用户点了「确定关闭」之后
        才会带着 force=True 再来一次。
        """
        if force:
            CLOSE_GUARD["allow"] = True
        hwnd = self._hwnd_wait()
        if hwnd:
            try:
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)     # WM_CLOSE
            except Exception:
                pass

    # ---------- 推理墙 / 简记 两种模式 ----------

    def _apply_title(self) -> None:
        hwnd = self._hwnd()
        if not hwnd:
            return
        name = "简记" if self.mode == "note" else "BubbleWork"
        try:
            ctypes.windll.user32.SetWindowTextW(hwnd, f"{APP_NAME} · {name}")
        except Exception:
            pass

    def set_mode(self, mode: str) -> dict:
        mode = "note" if mode == "note" else "wall"
        changed = mode != self.mode
        self.mode = mode
        hwnd = self._hwnd_wait()      # 页面可能比窗口先就绪，等一下句柄
        self._apply_title()
        if not hwnd:
            return self.state()

        scale = self._scale()
        area = self._work_area(hwnd)
        rect = self._rect(hwnd)

        if mode == "note":
            # 细长条：贴在屏幕右侧，高度贴着工作区
            if changed and rect:
                self._wall_rect = rect
                self._wall_maximized = self._max_restore is not None
            self._max_restore = None
            # 宽度按屏幕比例来（大约五分之一屏），高度铺满工作区上下 ——
            # 一条「顶着右上角、上下铺满」的细长条，写笔记最舒服。
            w = int(min(max(area[2] * NOTE_WIDTH_RATIO, 330 * scale), 620 * scale))
            w = min(w, max(int(280 * scale), int(area[2] - 24 * scale)))
            h = int(max(320 * scale, area[3] - 8 * scale))
            x = area[0] + area[2] - w - int(8 * scale)
            y = area[1] + int(4 * scale)
            self._place(hwnd, x, y, w, h)
        else:
            # 回到推理墙：尽量用回上次的位置尺寸
            self._max_restore = None
            w = int(min(WALL_SIZE[0], (area[2] - 60) / scale) * scale)
            h = int(min(WALL_SIZE[1], (area[3] - 80) / scale) * scale)
            saved = self._wall_rect if changed else None
            if saved and self._wall_maximized:
                # 进简记之前是最大化的，回来也最大化
                self._place(hwnd, saved[0], saved[1], saved[2], saved[3])
                self._max_restore = saved
                self._place(hwnd, *self._work_area(hwnd))
            elif saved and saved[2] >= 320 * scale and saved[3] >= 260 * scale:
                self._place(hwnd, saved[0], saved[1], saved[2], saved[3])
            else:
                # 正常启动 / 没有历史位置：摆在屏幕（工作区）正中间
                self._place(hwnd,
                            area[0] + (area[2] - w) // 2,
                            area[1] + (area[3] - h) // 2,
                            w, h)
            self._wall_rect = self._rect(hwnd)
        return self.state()

    def center_wall(self) -> dict:
        """把推理墙窗口摆到屏幕正中间（启动时调用一次）。

        自己算工作区来居中，而不是靠 pywebview 的 CenterScreen ——
        后者是按整块屏幕居中的，底下有任务栏时会看着偏下。
        """
        hwnd = self._hwnd_wait(1.5)
        if not hwnd:
            return {"app": False}
        scale = self._scale()
        area = self._work_area(hwnd)
        w = int(min(WALL_SIZE[0], (area[2] - 60) / scale) * scale)
        h = int(min(WALL_SIZE[1], (area[3] - 80) / scale) * scale)
        self._place(hwnd, area[0] + (area[2] - w) // 2, area[1] + (area[3] - h) // 2, w, h)
        self._wall_rect = self._rect(hwnd)
        log_event("窗口", f"推理墙窗口已居中：窗口{self._wall_rect} 工作区{area}")
        return self.state()

    # ---------- 数据目录（照片 + 文字 存到哪儿） ----------

    def bring_to_front(self) -> dict:
        """把窗口弹到最前面（双击 .lc 时用：最小化了就还原，被挡住就顶上来）。

        纯 Win32 两个调用，没有消息循环、没有模态，不存在卡死的可能。
        """
        hwnd = self._hwnd_wait(1.0)
        if not hwnd:
            return {"app": False}
        try:
            user32 = ctypes.windll.user32
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)          # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        except Exception as exc:
            log_error(f"[打开文件] 把窗口弹到最前面失败：{exc!r}")
        return {"app": True}

    def data_dir_info(self) -> dict:
        """网页问「现在东西存在哪」。"""
        cur = self._data_dir or DEFAULT_DATA_DIR
        return {
            "ok": True,
            "dir": str(cur),
            "profile": str(cur / PROFILE_SUBDIR),
            "default": str(DEFAULT_DATA_DIR),
            "config": str(CONFIG_FILE),
        }

    def choose_data_dir(self) -> dict:
        """弹系统「选择文件夹」，把数据目录换到用户挑的地方（下次启动生效）。

        用的是 pywebview 自带的文件夹对话框，跟「导出 / 导入」一样是系统原生
        弹窗；这里不做任何窗口尺寸/置顶操作，所以不可能把窗口拖卡住。
        """
        win = self._window
        if win is None:
            return {"ok": False, "error": "窗口还没准备好，稍后再试"}
        if self._picking_dir:
            return {"ok": False, "error": "上一个选择框还开着"}

        import webview

        cur = Path(self._data_dir or DEFAULT_DATA_DIR)
        self._picking_dir = True
        try:
            picked = win.create_file_dialog(webview.FOLDER_DIALOG, directory=str(cur))
        except Exception as exc:
            log_error(f"[数据目录] 打开「选择文件夹」失败：{exc!r}")
            return {"ok": False, "error": f"{exc!r}"}
        finally:
            self._picking_dir = False

        if not picked:
            return {"ok": False, "cancel": True}
        target = Path(picked[0] if isinstance(picked, (list, tuple)) else picked)
        target = ensure_writable_dir(target.expanduser())
        if target is None:
            return {"ok": False, "error": "这个文件夹写不进去，换一个试试（比如 D 盘或桌面）"}

        moved: list[str] = []
        if target != cur:
            # 老数据一起搬过去，用户不用重新导入
            moved = copy_profile_data(cur / PROFILE_SUBDIR, target / PROFILE_SUBDIR)

        cfg = load_config()
        cfg["data_dir"] = str(target)
        save_config(cfg)
        self._data_dir = target
        log_event("数据目录", f"用户把数据目录改成了：{target}（搬了 {moved}）")
        return {"ok": True, "dir": str(target), "moved": moved,
                "default": str(DEFAULT_DATA_DIR), "restart": True}


WINDOW_API = WindowApi()


def start_window(url: str, data_dir: Path | None = None) -> bool:
    """用 WebView2 开一个真正的程序窗口（不是浏览器）。失败返回 False。"""
    log_event("启动", "正在创建程序窗口（WebView2）…")
    try:
        import webview
    except Exception as exc:                                  # 没装/装不上 → 退回浏览器
        log_error(f"没有可用的 WebView2 外壳（{exc!r}），改用浏览器打开。")
        return False

    # 照片 / 文字存哪儿：明确告诉 WebView2（不定的话它会用一次性的临时档案，
    # 关掉程序就等于把墙上的东西丢在临时目录里）。
    store = None
    if data_dir is not None:
        data_dir = ensure_writable_dir(Path(data_dir))
        if data_dir is not None:
            store = str(data_dir)
            WINDOW_API._data_dir = data_dir
            log_event("数据目录", f"照片和文字会存在：{data_dir}")
        else:
            log_error(f"[数据目录] {data_dir} 不能用，这次先不指定（数据可能不保存）。")

    try:
        window = webview.create_window(
            APP_NAME + " · BubbleWork",
            url,
            width=WALL_SIZE[0],
            height=WALL_SIZE[1],
            min_size=(360, 320),
            frameless=True,          # 自己画标题栏（要多一个"置顶"按钮，系统标题栏放不下）
            easy_drag=False,         # 拖动由我们自己实现（纯 SetWindowPos，不用模态循环）
            shadow=True,             # 无边框窗口也给它一点系统阴影，看起来更像真窗口
            resizable=True,
            text_select=True,
            background_color="#120C07",
            js_api=WINDOW_API,
        )
        WINDOW_API._window = window

        def _on_closing():
            """用户点了窗口的关闭（✕ / Alt+F4 / 任务栏右键关闭）都会走到这里。

            这个回调跑在 UI 线程上（pywebview 是同步派发的），所以**只能做极快的事**：
            置个标记、返回 False 把这次关闭取消掉，具体问不问用户交给页面去办。
            返回 True 表示「放行」，窗口就真的关了。
            """
            if CLOSE_GUARD["allow"]:
                return True
            if window_session_count() == 0:
                return True          # 页面根本没连上（白屏/已关闭）→ 别把窗口卡住
            request_close_guard()
            log_event("关闭", "收到关闭请求 → 先让页面问一句「还有没保存的」")
            return False

        window.events.closing += _on_closing
        log_error(f"[信息] 已用程序窗口打开：{url}")
        log_event("启动", f"窗口对象已创建（句柄 {WINDOW_API._hwnd()}），准备进入消息循环…")
        icon = app_icon_path()
        webview.start(private_mode=False, storage_path=store,
                      icon=str(icon) if icon else None)         # 阻塞到窗口关闭
        log_event("启动", "窗口已关闭，程序退出。")
        if WINDOW_API.close_reason == "blank":
            log_event("启动", "刚才是白屏窗口被自动关掉的 → 继续走浏览器模式。")
            return False
        return True
    except Exception as exc:
        log_error(f"窗口启动失败：{exc!r}")
        return False


def spawn_browser_instance(port: int) -> bool:
    """另起一个「纯浏览器模式」的本程序（窗口卡死时的兜底用）。"""
    import subprocess

    exe = sys.executable or ""
    if not exe:
        return False
    args = [exe]
    if not getattr(sys, "frozen", False):
        args.append(str(Path(__file__).resolve()))     # 源码方式运行：要带上脚本路径
    args += ["--browser", "--new", "--port", str(port)]
    flags = 0x00000008 | 0x00000200                     # DETACHED_PROCESS | NEW_PROCESS_GROUP
    try:
        subprocess.Popen(args, close_fds=True, creationflags=flags)
        log_event("启动", f"已经另起一个浏览器模式的实例：{' '.join(args)}")
        return True
    except Exception as exc:
        log_event("警告", f"另起浏览器实例失败：{exc!r}")
        return False


def window_guard(url: str, port: int, timeout: float = 25.0, warn_at: float = 10.0) -> None:
    """窗口模式下的看门线程。

    · 只认「窗口里的那个页面」，不会被别的浏览器页签骗到；
    · 到点还没连上（窗口白屏 / WebView2 卡死）就：另起一个浏览器模式的实例，
      然后把自己这个已经卡住的进程直接结束 —— 用户拿到的至少是一个能用的界面。
    """
    t0 = time.monotonic()
    warned = False
    while time.monotonic() - t0 < timeout:
        if SHUTDOWN.is_set():
            return
        if window_session_count():
            log_event("连接", f"窗口里的页面在 {(time.monotonic() - t0):.1f} 秒时连上来了。")
            return
        if not warned and time.monotonic() - t0 > warn_at:
            warned = True
            log_event("警告",
                      f"{int(warn_at)} 秒了，窗口里的页面还没连上来"
                      f"（总会话 {session_count()} 个，其中来自窗口的 {window_session_count()} 个）"
                      "——多半是 WebView2 没渲染出来、窗口是白屏。")
        time.sleep(0.5)
    if SHUTDOWN.is_set() or window_session_count():
        return
    log_event("警告", f"{int(timeout)} 秒都没等到窗口里的页面，判定窗口白屏/卡死。"
                      "改用浏览器兜底：先另起一个浏览器模式的实例，再结束这个卡住的进程。")
    if spawn_browser_instance(port or DEFAULT_PORT):
        time.sleep(0.5)
        try:
            os._exit(0)          # 这个进程已经卡住了，直接结束（不做任何清理）
        except Exception:
            pass
    WINDOW_API.close_reason = "blank"
    try:
        WINDOW_API.close()
    except Exception:
        pass


def ui_health_watch(interval: float = 4.0) -> None:
    """（保留）判断窗口是否"未响应"。

    注意：真正卡死时这个线程自己也跑不动（GIL 被占），所以真正的保险是
    下面的 start_deadlock_guard()。
    """
    """盯着主窗口：一旦 Windows 把它标成「未响应」，立刻把所有线程堆栈写进日志。"""
    import faulthandler

    user32 = ctypes.windll.user32
    try:
        user32.IsHungAppWindow.argtypes = [ctypes.c_void_p]
        user32.IsHungAppWindow.restype = ctypes.c_bool
    except Exception:
        pass

    was_hung = False
    while not SHUTDOWN.wait(interval):
        hwnd = WINDOW_API._hwnd()
        if not hwnd:
            continue
        try:
            hung = bool(user32.IsHungAppWindow(hwnd))
        except Exception:
            hung = False
        if hung and not was_hung:
            log_event("警告", "窗口已经几秒没有响应了（被 Windows 标记为“未响应”）。"
                              "下面把所有 Python 线程的堆栈写下来，方便定位卡在哪：")
            try:
                with open(ERROR_LOG, "a", encoding="utf-8") as fh:
                    fh.write(f"\n--- 线程堆栈 @ {time.strftime('%H:%M:%S')} ---\n")
                    faulthandler.dump_traceback(file=fh, all_threads=True)
                    fh.write("--- 堆栈结束 ---\n")
                    fh.flush()
            except Exception:
                pass
        elif was_hung and not hung:
            log_event("恢复", "窗口又恢复正常响应了。")
        was_hung = hung


def start_deadlock_guard(dump_after: float = 20.0, interval: float = 5.0) -> None:
    """保险丝：万一整个进程卡死（连日志都不再写了），自动把所有线程的堆栈写进日志。

    faulthandler 的这套机制在 C 层跑、**不需要 GIL**，就是专门用来抓死锁的：
    进程健康时每 5 秒把倒计时重置一次；一旦真的卡住，倒计时走完就落盘。
    """
    import faulthandler

    try:
        fd = os.open(str(ERROR_LOG), os.O_APPEND | os.O_CREAT | os.O_WRONLY)
        stream = os.fdopen(fd, "a", encoding="utf-8", errors="replace")
    except Exception:
        return

    def loop() -> None:
        while not SHUTDOWN.wait(interval):
            try:
                faulthandler.cancel_dump_traceback_later()
                faulthandler.dump_traceback_later(dump_after, file=stream)
            except Exception:
                return

    threading.Thread(target=loop, daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    install_error_hooks()
    if os.environ.get("ARGTOOL_SELFTEST"):
        return self_test()
    try:
        return _run(argv)
    except Exception:
        import traceback

        text = traceback.format_exc()
        log_error(text)
        notify(f"{APP_NAME} 启动失败", text[-900:] + f"\n\n详细日志：{ERROR_LOG}")
        return 1


def self_test() -> int:
    """打包后自检（设置环境变量 ARGTOOL_SELFTEST=1 时跑）。

    检查 exe 里该有的东西是不是都在：WebView2 外壳、破译引擎、网页文件。
    结果写到 %TEMP%\\ARGtool_selftest.txt，打不开的时候照着看就行。
    """
    lines = [
        f"app = {APP_NAME} {APP_VERSION}",
        f"frozen = {bool(getattr(sys, 'frozen', False))}",
        f"windowed = {IS_WINDOWED}",
        f"web = {WEB_DIR}（存在：{WEB_DIR.is_dir()}）",
        f"icon = {app_icon_path()}（窗口/任务栏图标）",
        f"engine = {ENGINE_VERSION}",
        f"model = 四字母组合 {ENGINE_MODEL.get('四字母组合', 0)} 种、词表 {ENGINE_MODEL.get('词表', 0)} 个",
    ]
    for label, importer in (
        ("webview", lambda: __import__("webview")),
        ("webview.platforms.winforms", lambda: __import__("webview.platforms.winforms", fromlist=["x"])),
        ("webview.platforms.edgechromium", lambda: __import__("webview.platforms.edgechromium", fromlist=["x"])),
        ("clr", lambda: __import__("clr")),
        ("System.Windows.Forms", lambda: __import__("System.Windows.Forms", fromlist=["x"])),
    ):
        try:
            importer()
            lines.append(f"{label} = OK")
        except Exception as exc:
            lines.append(f"{label} = 失败：{exc!r}")
    try:
        result = ANALYZE(".. -- -... .- -.-. -.-", OPTIONS_CLASS(max_depth=1, rank_mode="common"))
        top = result.candidates[0].text if result.candidates else "(没有候选)"
        lines.append(f"破译引擎 = OK，测试密文解出来是：{top}")
    except Exception as exc:
        lines.append(f"破译引擎 = 失败：{exc!r}")

    text = "\n".join(lines)
    log_error("[selftest]\n" + text)
    target = Path(os.environ.get("TEMP") or ".") / "ARGtool_selftest.txt"
    try:
        target.write_text(text, encoding="utf-8")
    except Exception:
        pass
    try:
        print(text)
    except Exception:
        pass
    return 0


def _run(argv: list[str] | None = None) -> int:
    safe_console()
    log_event("启动", f"程序开始启动：frozen={bool(getattr(sys, 'frozen', False))} "
                      f"windowed={IS_WINDOWED} 参数={argv if argv is not None else sys.argv[1:]}")
    parser = argparse.ArgumentParser(description="ARGtool 本地服务")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口（默认 8765）")
    parser.add_argument("--no-browser", action="store_true", help="不要自动打开浏览器")
    parser.add_argument("--browser", action="store_true", help="强制用浏览器打开（不用程序窗口）")
    parser.add_argument("--verbose", action="store_true", help="打印访问日志")
    parser.add_argument("--new", action="store_true", help="即使已有实例在运行，也强制新开一个")
    parser.add_argument("--data-dir", default=None,
                        help="照片和文字存到哪个文件夹（不给就用配置/默认位置）")
    parser.add_argument("files", nargs="*",
                        help="要打开的 .lc 文件（双击文件时由系统传进来）")
    args = parser.parse_args(argv)

    if args.verbose:
        os.environ["ARGTOOL_VERBOSE"] = "1"

    # 双击 .lc 打开：把命令行里的文件挑出来（可能不止一个）
    open_files = open_files_from_args(args.files)
    if open_files:
        log_event("启动", f"这次要打开的文件：{open_files}")

    # 数据目录：先定下来（照片墙 / 简记 的文字和图片都存这里）
    data_dir = resolve_data_dir(args.data_dir)
    WINDOW_API._data_dir = data_dir
    migrate_old_profile(data_dir)

    # 已经有一个在跑？（默认端口 + 没要求强制新开时才会走这里）
    if not args.new and not args.no_browser and args.port == DEFAULT_PORT:
        existing = running_instance(DEFAULT_PORT)
        if existing:
            url = f"http://127.0.0.1:{DEFAULT_PORT}/"
            if existing.get("build") != APP_BUILD:
                # 老版本还挂在后台：这次另起一个新实例，别让它把新版顶掉
                # （旧的那个等它自己的页面都关掉就会自动退出）
                log_error(f"[信息] 发现旧版本 ARGtool（build={existing.get('build')}）还在运行，"
                          "这次另开一个新的。")
                args.port = DEFAULT_PORT + 1      # 明确避开旧实例占着的端口
            else:
                # 同一个版本：照着再开一个一模一样的程序窗口（两个窗口共享同一份数据）
                if open_files and handoff_open_files(DEFAULT_PORT, open_files):
                    # 双击 .lc 时走这里：文件交给已经开着的那个窗口，自己就退出
                    return 0
                log_event("启动", f"已经有同版本实例在跑，这次只开一个新窗口连过去：{url}")
                if not args.browser and start_window(url, data_dir):
                    return 0
                webbrowser.open(browser_url(url))
                return 0

    if not WEB_DIR.is_dir():
        notify(APP_NAME, f"启动失败：找不到网页目录\n{WEB_DIR}")
        return 2

    port = find_free_port(args.port)
    url = f"http://127.0.0.1:{port}/"
    server = ThreadingHTTPServer(("127.0.0.1", port), ArgToolHandler)
    server.daemon_threads = True
    log_event("启动", f"本地服务已监听 127.0.0.1:{port}")

    # 这次是被「双击 .lc」叫起来的：把文件登记好，页面一加载就会来取
    for raw in open_files:
        if register_open_file(raw) is None:
            log_error(f"[打开文件] 这个文件打不开：{raw}")

    if not IS_WINDOWED:
        print(banner(url))
        print(f"   破译引擎：{ENGINE_VERSION}　语言模型：四字母组合 "
              f"{ENGINE_MODEL.get('四字母组合', 0)} 种、词表 {ENGINE_MODEL.get('词表', 0)} 个\n")

    # 服务跑在后台线程，主线程负责开窗口 / 等退出信号
    threading.Thread(target=server.serve_forever, daemon=True).start()
    start_deadlock_guard()          # 进程真卡死时自动打印所有线程堆栈

    if not args.browser and not args.no_browser:
        # 把网页装进一个真正的程序窗口（WebView2）
        # 15 秒还没看见窗口里的页面 = 白屏 → 自动关掉白窗口、改用浏览器（别让用户对着死窗口）
        threading.Thread(target=window_guard, args=(url, port),
                         kwargs={"timeout": 15.0, "warn_at": 8.0},
                         daemon=True).start()
        threading.Thread(target=ui_health_watch, daemon=True).start()
        if start_window(url, data_dir):
            SHUTDOWN.set()
            server.shutdown()
            server.server_close()
            return 0
        log_event("启动", "程序窗口没起来，改用浏览器模式。")

    # 退路：浏览器模式（网页全关掉后自动退出）
    threading.Thread(target=session_watchdog, daemon=True).start()
    if not args.no_browser:
        threading.Thread(target=_sleep_then_open, args=(browser_url(url),), daemon=True).start()

    try:
        while not SHUTDOWN.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    if not IS_WINDOWED:
        print("\n   已停止。")
    server.shutdown()
    server.server_close()
    return 0


ANALYZE, OPTIONS_CLASS, MODEL_INFO, ENGINE_VERSION, ALL_DECODERS = import_engine()
ENGINE_MODEL = MODEL_INFO()


if __name__ == "__main__":
    raise SystemExit(main())
