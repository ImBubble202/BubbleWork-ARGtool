"""图形界面：把密文粘进来，点一下就能看到各种解密方式和可信度。"""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from .core import Options
from .engine import _dedupe, analyze
from .ngram import model_info
from .priority import RANK_MODE_LABELS, rank_score

APP_TITLE = "UniversalDecipherer · 通用文字解密器"

SAMPLES: list[tuple[str, str]] = [
    ("Base64", "aGVsbG8gd29ybGQgdGhpcyBpcyBhIHNlY3JldCBtZXNzYWdl"),
    ("摩斯电码", "-.. --- --- .-. / .. ... / --- .--. . -. / .-. ..- -. "),
    ("凯撒移位", "wkh phhwlqj lv wrqljkw dw wkh rog vwdwlrq"),
    ("ROT13", "gur xrl vf uvqqra va gur jnyy"),
    ("培根密码", "AABBB AABAA ABABB ABABB ABBBA BABBA ABBBA BAAAB ABABB AAABB"),
    ("栅栏密码", "tseegsdh ertmsaei idnec s he"),
    ("零宽字符", "这里看起来是普通文字，其实藏着零宽字符：" + "".join(
        format(b, "08b").replace("0", "\u200b").replace("1", "\u200c")
        for b in "flag{h1dd3n}".encode()
    )),
    ("URL 编码", "%74%68%65%20%64%6F%6F%72%20%69%73%20%6F%70%65%6E"),
    ("十六进制", "576520617265207761746368696e6720796f75"),
    ("手机九宫格", "44 33 555 555 666 0 9 666 777 555 3"),
]

HELP_TEXT = """使用说明

1. 把密文（或疑似密文）粘到左上角的输入框，点「开始破译」。
2. 程序会把所有内置解密方法都试一遍，然后把结果排成一张表。
3. 排行榜默认「常见度优先」：**先按手法常见度排**。
   摩斯（100）、凯撒/Base64（95）这类 ARG 高频手法永远排在冷门手法前面；
   同一常见度内部，才按「解得像不像人话」排序。
   怎么看「像不像人话」只影响名次，程序不在界面上显示任何打分数字。
4. 「排序偏好」可以切到「平衡」或「可读性优先」，后两种会让冷门手法更容易往前排。
5. 「最大层数」表示允许叠加几层解密，例如 Base64 里套了 ROT13 就需要 2 层。
6. 如果题目给了密钥（维吉尼亚、列换位、异或等），填进「已知密钥」栏，
   多个密钥用逗号分隔；留空则自动猜测。
7. 「格式线索」标签页会告诉你这段密文最像哪种编码，方便顺着线索往下想。

小技巧
· 直接把带隐藏零宽字符的文本复制进来即可，程序会自动识别。
· 结果框里的文本可以全选复制，继续当作下一层密文使用。
· 导出的 JSON 里包含全部候选与评分细节，方便写解题记录。
"""


class _Tooltip:
    """鼠标悬停时显示一小段说明。"""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def show(self, _event=None) -> None:
        if self.window:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        tk.Label(self.window, text=self.text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, padx=8, pady=5).pack()

    def hide(self, _event=None) -> None:
        if self.window:
            self.window.destroy()
            self.window = None


class DeciphererApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1200x820")
        self.minsize(980, 640)

        self.font_ui = self._pick_font(
            ["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "SimHei"], 10
        )
        self.font_mono = self._pick_font(["Cascadia Mono", "Consolas", "Courier New"], 10)
        self.font_bold = self.font_ui.copy()
        self.font_bold.configure(weight="bold")

        self.candidates: list = []
        self.displayed: list = []
        self.result = None
        self._generation = 0
        self._busy = False
        self._worker_queue: "queue.Queue[tuple]" = queue.Queue()

        self._setup_style()
        self._build_widgets()
        self._set_status("准备就绪。把密文粘到上面的输入框，然后点「开始破译」。")
        self.after(80, self._poll_worker_queue)

    # ------------------------------------------------------------ 线程通信
    def _poll_worker_queue(self) -> None:
        """定期在主线程里取子线程的结果（Tkinter 不允许子线程直接操作界面）。"""
        try:
            while True:
                kind, payload, generation = self._worker_queue.get_nowait()
                if kind == "ok":
                    self._show_result(payload, generation)
                else:
                    self._on_error(payload, generation)
        except queue.Empty:
            pass
        self.after(80, self._poll_worker_queue)

    # ---------------------------------------------------------------- 外观
    def _pick_font(self, families: list[str], size: int) -> tkfont.Font:
        available = set(tkfont.families(self))
        for name in families:
            if name in available:
                return tkfont.Font(family=name, size=size)
        return tkfont.Font(size=size)

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=self.font_ui)
        style.configure("Treeview", font=self.font_ui, rowheight=26)
        style.configure("Treeview.Heading", font=self.font_bold)
        style.configure("TButton", padding=(10, 6))
        style.configure("Accent.TButton", padding=(14, 8))
        style.configure("TCheckbutton", padding=(4, 2))
        style.configure("TLabelframe.Label", font=self.font_bold)

    # ---------------------------------------------------------------- 布局
    def _build_widgets(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        self._build_input_area(root)

        paned = ttk.PanedWindow(root, orient="vertical")
        paned.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

        list_frame = ttk.Frame(paned)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(list_frame)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self._build_rank_tab()
        self._build_hints_tab()
        paned.add(list_frame, weight=3)

        detail_frame = ttk.Labelframe(paned, text=" 选中结果的完整内容 ", padding=8)
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(1, weight=1)
        self.detail_meta = ttk.Label(detail_frame, text="点击上面的任意一行查看完整明文。",
                                     justify="left", wraplength=1100)
        self.detail_meta.grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.detail_text = tk.Text(detail_frame, wrap="word", height=10, undo=True,
                                   font=self.font_mono, relief="solid", borderwidth=1)
        self.detail_text.grid(row=1, column=0, sticky="nsew")
        detail_scroll = ttk.Scrollbar(detail_frame, orient="vertical",
                                      command=self.detail_text.yview)
        detail_scroll.grid(row=1, column=1, sticky="ns")
        self.detail_text.configure(yscrollcommand=detail_scroll.set)
        paned.add(detail_frame, weight=4)

        self.status = ttk.Label(root, text="", anchor="w", padding=(4, 6))
        self.status.grid(row=2, column=0, sticky="ew")

    def _build_input_area(self, parent: ttk.Frame) -> None:
        frame = ttk.Labelframe(parent, text=" 密文输入 ", padding=8)
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)

        self.input_text = tk.Text(frame, wrap="word", height=6, font=self.font_mono,
                                  relief="solid", borderwidth=1)
        self.input_text.grid(row=0, column=0, columnspan=4, sticky="ew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.input_text.yview)
        scroll.grid(row=0, column=4, sticky="ns")
        self.input_text.configure(yscrollcommand=scroll.set)

        options = ttk.Frame(frame)
        options.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(8, 0))

        ttk.Label(options, text="排序偏好：").pack(side="left")
        self.rank_var = tk.StringVar(value=RANK_MODE_LABELS["common"])
        rank_box = ttk.Combobox(
            options, textvariable=self.rank_var, width=11, state="readonly",
            values=[RANK_MODE_LABELS["common"], RANK_MODE_LABELS["balanced"],
                    RANK_MODE_LABELS["confidence"]],
            font=self.font_ui,
        )
        rank_box.pack(side="left", padx=(2, 12))
        self._bind_rank_tooltip(rank_box)
        rank_box.bind("<<ComboboxSelected>>", lambda _e: self.on_rank_mode_change())

        ttk.Label(options, text="最大层数：").pack(side="left")
        self.depth_var = tk.IntVar(value=2)
        ttk.Spinbox(options, from_=1, to=3, width=4, textvariable=self.depth_var,
                    font=self.font_ui).pack(side="left", padx=(2, 12))

        ttk.Label(options, text="已知密钥：").pack(side="left")
        self.key_var = tk.StringVar()
        ttk.Entry(options, textvariable=self.key_var, width=18,
                  font=self.font_ui).pack(side="left", padx=(2, 12))

        self.brute_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text="启用暴力枚举（凯撒/仿射/栅栏等）",
                        variable=self.brute_var).pack(side="left", padx=(0, 12))
        self.filter_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text="隐藏低分噪声", variable=self.filter_var,
                        command=self.refresh_tree).pack(side="left")

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        self.analyze_button = ttk.Button(buttons, text="开始破译  (Ctrl+Enter)",
                                         style="Accent.TButton", command=self.on_analyze)
        self.analyze_button.pack(side="left")
        ttk.Button(buttons, text="清空", command=self.on_clear).pack(side="left", padx=6)
        ttk.Button(buttons, text="载入示例", command=self.on_sample).pack(side="left", padx=6)
        ttk.Button(buttons, text="粘贴剪贴板", command=self.on_paste).pack(side="left", padx=6)
        ttk.Button(buttons, text="导出结果", command=self.on_export).pack(side="left", padx=6)
        ttk.Button(buttons, text="使用说明", command=self.on_help).pack(side="right")

        self.bind("<Control-Return>", lambda _event: self.on_analyze())

    def _build_rank_tab(self) -> None:
        frame = ttk.Frame(self.notebook)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        columns = ("rank", "popularity", "method", "preview")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "rank": ("排名", 50, "center"),
            "popularity": ("常见度", 76, "center"),
            "method": ("解密方法链", 300, "w"),
            "preview": ("结果预览", 620, "w"),
        }
        for key, (text, width, anchor) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=anchor,
                             stretch=(key in ("method", "preview")))
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda _event: self.copy_selected())
        self.tree.bind("<Button-3>", self._popup_menu)
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="复制结果到剪贴板", command=self.copy_selected)
        self.menu.add_command(label="把结果当作新的输入", command=self.use_as_input)
        self.notebook.add(frame, text="  排行榜  ")

    def _build_hints_tab(self) -> None:
        frame = ttk.Frame(self.notebook)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.hint_text = tk.Text(frame, wrap="word", font=self.font_ui, relief="flat")
        self.hint_text.grid(row=0, column=0, sticky="nsew")
        self.notebook.add(frame, text="  格式线索  ")

    # ---------------------------------------------------------------- 交互
    def get_text(self) -> str:
        return self.input_text.get("1.0", "end-1c")

    def _set_status(self, message: str) -> None:
        self.status.configure(text=message)

    def _bind_rank_tooltip(self, widget: tk.Widget) -> None:
        _Tooltip(
            widget,
            "常见度优先（默认）：两级排序\n"
            "    第一级 = 手法常见度（摩斯 100 > 凯撒/Base64 95 > …… > JWT 25）\n"
            "    第二级 = 可读性（同一种手法内部才比谁解得通顺）\n"
            "    可读性过低的结果统一沉底，不参与排名\n"
            "平衡：常见度与可读性折中\n"
            "可读性优先：完全按「像不像人话」排序，不看手法冷热",
        )

    def on_rank_mode_change(self) -> None:
        """切换排序偏好时，直接就地重排已有结果，不必重新解密。"""
        mode = self.current_rank_mode()
        self.candidates = _dedupe(
            self.candidates, per_text=4, max_results=max(len(self.candidates), 1), mode=mode
        )
        if self.result is not None:
            self.result.candidates = self.candidates
        self.refresh_tree()
        self._set_status(f"已切换到「{self.rank_var.get()}」排序。")

    def current_rank_mode(self) -> str:
        for key, label in RANK_MODE_LABELS.items():
            if label == self.rank_var.get():
                return key
        return "common"

    def on_paste(self) -> None:
        try:
            data = self.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("提示", "剪贴板里没有文本。")
            return
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", data)
        self._set_status("已粘贴剪贴板内容，点「开始破译」。")

    def on_sample(self) -> None:
        import random

        name, sample = random.choice(SAMPLES)
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", sample)
        self._set_status(f"已载入示例：{name}。点「开始破译」看看结果。")

    def on_clear(self) -> None:
        self.input_text.delete("1.0", "end")
        self.detail_text.delete("1.0", "end")
        self.detail_meta.configure(text="点击上面的任意一行查看完整明文。")
        self.hint_text.delete("1.0", "end")
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.candidates = []
        self.displayed = []
        self._set_status("已清空。")

    def on_help(self) -> None:
        messagebox.showinfo("使用说明", HELP_TEXT)

    def build_options(self) -> Options:
        keys = [k.strip() for k in self.key_var.get().replace("，", ",").split(",") if k.strip()]
        return Options(
            max_depth=int(self.depth_var.get()),
            keys=keys,
            enable_bruteforce=bool(self.brute_var.get()),
            rank_mode=self.current_rank_mode(),
        )

    def on_analyze(self) -> None:
        if self._busy:
            return
        text = self.get_text()
        if not text.strip():
            messagebox.showwarning("没有内容", "请先粘贴或输入要破译的内容。")
            return

        self._generation += 1
        generation = self._generation
        options = self.build_options()
        self._busy = True
        self.analyze_button.configure(state="disabled")
        self._set_status("正在尝试各种解密方式…（层数越深越慢，请稍候）")
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.candidates = []

        thread = threading.Thread(
            target=self._worker, args=(text, options, generation), daemon=True
        )
        thread.start()

    def _worker(self, text: str, options: Options, generation: int) -> None:
        try:
            result = analyze(text, options)
        except Exception as exc:  # 保证界面不会因为单个解密器出错而崩
            self._worker_queue.put(("error", exc, generation))
            return
        self._worker_queue.put(("ok", result, generation))

    def _on_error(self, exc: Exception, generation: int) -> None:
        if generation != self._generation:
            return
        self._busy = False
        self.analyze_button.configure(state="normal")
        self._set_status("分析出错：" + repr(exc))
        messagebox.showerror("出错了", f"分析过程中出现问题：\n{exc}")

    def _show_result(self, result, generation: int) -> None:
        if generation != self._generation:
            return
        self._busy = False
        self.analyze_button.configure(state="normal")
        self.result = result
        self.candidates = result.candidates
        self.refresh_tree()
        self._fill_hints(result)
        total = len(self.candidates)
        shown = len(self.displayed)
        hidden = "" if shown == total else f"（已隐藏 {total - shown} 条低分噪声）"
        self._set_status(
            f"完成：{total} 条候选{hidden}，评分 {result.evaluated} 次，"
            f"耗时 {result.elapsed:.2f} 秒。"
            + ("　最佳结果：" + self.candidates[0].preview(60) if self.candidates else "　没有候选结果。")
        )

    def refresh_tree(self) -> None:
        """按当前筛选条件重建排行榜。"""
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.displayed = self._apply_filter()
        for index, cand in enumerate(self.displayed):
            self.tree.insert(
                "", "end", iid=str(index),
                values=(
                    index + 1,
                    cand.popularity,
                    cand.chain_text,
                    cand.preview(110),
                ),
            )
        if self.displayed:
            self.tree.selection_set("0")
            self.tree.focus("0")

    def _apply_filter(self) -> list:
        if not self.candidates:
            return []
        if not self.filter_var.get():
            return list(self.candidates)
        best = self.candidates[0].confidence
        threshold = max(10.0, min(50.0, best * 0.3))
        shown = [c for c in self.candidates if c.confidence >= threshold]
        if len(shown) < 12:
            shown = self.candidates[:12]
        return shown[:200]

    # ------------------------------------------------------------ 结果操作
    def copy_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        cand = self.displayed[int(selection[0])]
        self.clipboard_clear()
        self.clipboard_append(cand.text)
        self._set_status("已复制该结果到剪贴板，可以直接粘贴到别处继续分析。")

    def use_as_input(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        cand = self.displayed[int(selection[0])]
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", cand.text)
        self.detail_text.delete("1.0", "end")
        self._set_status("已把该结果放到输入框，可以再点一次「开始破译」继续往下解。")

    def _popup_menu(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            self.menu.tk_popup(event.x_root, event.y_root)

    def _fill_hints(self, result) -> None:
        self.hint_text.delete("1.0", "end")
        self.hint_text.insert("end", "一段密文往往能被很多种方法解出「看起来像话」的东西，\n"
                                    "所以先看它最像什么格式，再重点看对应的候选结果。\n\n")
        if result.input_hints:
            self.hint_text.insert("end", "根据格式特征，这段输入最可能是：\n")
            for name, score in result.input_hints:
                self.hint_text.insert("end", f"    · {name}    匹配度 {score * 100:.0f}%\n")
        else:
            self.hint_text.insert("end", "没有识别出明显的编码特征，可能是替换类密码或自创编码。\n")
        self.hint_text.insert("end", "\n程序给出的说明：\n")
        for note in result.notes:
            self.hint_text.insert("end", "    · " + note + "\n")
        info = model_info()
        self.hint_text.insert(
            "end",
            f"\n语言模型规模：四字母组合 {info['四字母组合']} 种，"
            f"三字母组合 {info['三字母组合']} 种，词表 {info['词表']} 个。\n",
        )

    def on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if index >= len(self.displayed):
            return
        cand = self.displayed[index]
        mode = self.current_rank_mode()
        if mode == "common":
            order_text = f"排序依据：手法常见度 {cand.popularity}/100（同一常见度内按可读性排序）"
        elif mode == "balanced":
            order_text = f"排序依据：平衡模式（常见度 {cand.popularity}/100 与可读性折中）"
        else:
            order_text = f"排序依据：可读性优先（只看解得像不像人话，常见度 {cand.popularity}/100）"
        self.detail_meta.configure(
            text=(f"{order_text}　层数：{cand.depth}\n"
                  f"解密方法：{cand.chain_text}"
                  + (f"\n提示：{cand.note}" if cand.note else ""))
        )
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", cand.text)

    # ---------------------------------------------------------------- 导出
    def on_export(self) -> None:
        if not self.result or not self.candidates:
            messagebox.showinfo("提示", "还没有结果可以导出，先点「开始破译」。")
            return
        path = filedialog.asksaveasfilename(
            title="导出解密结果",
            defaultextension=".json",
            initialfile="decipher_result.json",
            filetypes=[("JSON 结果", "*.json"), ("纯文本报告", "*.txt")],
        )
        if not path:
            return
        if path.lower().endswith(".txt"):
            lines = [f"输入：{self.result.source}", ""]
            for index, cand in enumerate(self.candidates):
                lines.append(f"[{index + 1}] 常见度 {cand.popularity}/100  {cand.chain_text}")
                lines.append("    " + cand.text.replace("\n", "\n    "))
                lines.append("")
            Path(path).write_text("\n".join(lines), encoding="utf-8")
        else:
            payload = {
                "输入": self.result.source,
                "格式线索": [{"方法": n, "匹配度": s} for n, s in self.result.input_hints],
                "说明": self.result.notes,
                "候选": [
                    {
                        "手法常见度": c.popularity,
                        "方法": c.chain_text,
                        "结果": c.text,
                        "提示": c.note,
                        "细节": {k: v for k, v in c.details.items() if not k.startswith("_")},
                    }
                    for c in self.candidates
                ],
            }
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._set_status(f"已导出：{path}")


def main(argv: list[str] | None = None) -> int:
    """启动图形界面。

    也支持带内容启动，例如：
        python -m decipherer.gui --text "aGVsbG8=" --auto
        python -m decipherer.gui --file 密文.txt --auto --depth 3
    """
    import argparse

    parser = argparse.ArgumentParser(description="UniversalDecipherer 图形界面")
    parser.add_argument("--text", help="启动时预填的密文")
    parser.add_argument("--file", help="从文件读取密文并预填")
    parser.add_argument("--auto", action="store_true", help="启动后自动开始破译")
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--key", action="append", default=[])
    args = parser.parse_args(argv)

    app = DeciphererApp()
    preload = args.text or ""
    if args.file:
        data = Path(args.file).read_bytes()
        for encoding in ("utf-8", "gbk", "latin-1"):
            try:
                preload = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    if preload:
        app.input_text.insert("1.0", preload)
    app.depth_var.set(max(1, min(3, args.depth)))
    if args.key:
        app.key_var.set("，".join(args.key))
    if args.auto:
        app.after(500, app.on_analyze)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
