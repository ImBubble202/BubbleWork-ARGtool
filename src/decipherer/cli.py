"""命令行入口：在终端里直接破译。

示例：
    python -m decipherer "aGVsbG8gd29ybGQ="
    python -m decipherer -f 密文.txt --top 15 --depth 3
    python -m decipherer "..." --key mykey --json 结果.json
    type 密文.txt | python -m decipherer -
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import Options
from .engine import analyze
from .ngram import model_info
from .priority import RANK_MODE_LABELS, noise_floor


def _safe_stdout() -> None:
    """Windows 控制台默认是 GBK，直接打印中文/箭头会报错。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decipherer",
        description="UniversalDecipherer —— 输入一段密文，自动尝试各种解密方式并按可信度排序",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("text", nargs="?", help="密文内容；输入 - 表示从标准输入读取")
    parser.add_argument("-f", "--file", help="从文件读取密文（UTF-8 或 GBK）")
    parser.add_argument("--top", type=int, default=10, help="显示前多少条结果（默认 10）")
    parser.add_argument("--rank", choices=["common", "balanced", "confidence"], default="common",
                        help="排序偏好：common 常见度优先（默认）／balanced 平衡／confidence 可读性优先")
    parser.add_argument("--depth", type=int, default=1, help="最多叠加几层解密（1-3，默认 1）")
    parser.add_argument("--key", action="append", default=[],
                        help="已知密钥，可重复填写，例如 --key key --key secret")
    parser.add_argument("--no-bruteforce", action="store_true", help="关闭暴力枚举类（更快）")
    parser.add_argument("--json", help="把完整结果导出为 JSON 文件")
    parser.add_argument("--all", action="store_true", help="显示全部候选结果（可能很多）")
    parser.add_argument("--prompt", action="store_true",
                        help="没有传入密文时，弹出提示让你直接粘贴")
    parser.add_argument("--info", action="store_true", help="显示语言模型信息后退出")
    return parser


def _read_input(args) -> str:
    if args.file:
        data = Path(args.file).read_bytes()
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", "replace")
    if args.text == "-":
        return sys.stdin.read()
    if args.text is None:
        if args.prompt:
            try:
                return input("请粘贴密文后按回车：")
            except (EOFError, KeyboardInterrupt):
                return ""
        if not sys.stdin.isatty():
            return sys.stdin.read()
    return args.text or ""


def _format_report(result, top: int, show_all: bool, rank_mode: str = "common") -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("输入内容：" + (result.source[:120] + ("…" if len(result.source) > 120 else "")))
    if result.input_hints:
        hints = "，".join(f"{name} {score * 100:.0f}%" for name, score in result.input_hints[:6])
        lines.append(f"格式线索：{hints}")
    lines.append(f"尝试结果：{len(result.candidates)} 条候选，评分 {result.evaluated} 次，"
                 f"耗时 {result.elapsed:.2f} 秒")
    if rank_mode == "confidence":
        lines.append("排序方式：可读性优先（只看解得像不像人话，不看手法冷热）")
    elif rank_mode == "balanced":
        lines.append("排序方式：平衡（常见度与可读性折中）")
    else:
        lines.append("排序方式：常见度优先（先按手法常见度排，同一常见度内再按可读性）")
    lines.append("=" * 78)

    count = len(result.candidates) if show_all else min(top, len(result.candidates))
    if count == 0:
        lines.append("没有找到任何候选结果。")
    floor = noise_floor(result.candidates[0].confidence) if result.candidates else 0.0
    noise_shown = False
    for index in range(count):
        cand = result.candidates[index]
        if cand.confidence < floor and not noise_shown:
            lines.append("")
            lines.append("-" * 78)
            lines.append(f"以下 {sum(1 for c in result.candidates if c.confidence < floor)} 条"
                         f"基本是乱码，只作参考：")
            lines.append("-" * 78)
            noise_shown = True
        lines.append("")
        lines.append(f"[{index + 1}] 手法常见度 {cand.popularity:3d}/100")
        lines.append(f"    方法：{cand.chain_text}")
        if cand.note:
            lines.append(f"    提示：{cand.note}")
        lines.append("    结果：" + cand.text if "\n" not in cand.text
                     else "    结果：\n" + "\n".join("        " + ln for ln in cand.text.splitlines()))
    if result.notes:
        lines.append("")
        lines.append("说明：")
        for note in result.notes:
            lines.append("  · " + note)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _safe_stdout()
    args = build_parser().parse_args(argv)
    if args.info:
        print("语言模型：", model_info())
        return 0

    text = _read_input(args)
    if not text or not text.strip():
        print("没有输入内容。用法示例：python -m decipherer \"aGVsbG8gd29ybGQ=\"", file=sys.stderr)
        return 2

    options = Options(
        max_depth=max(1, min(3, args.depth)),
        keys=[k for k in args.key if k],
        enable_bruteforce=not args.no_bruteforce,
        rank_mode=args.rank,
    )
    result = analyze(text, options)
    print(_format_report(result, args.top, args.all, args.rank))

    if args.json:
        payload = {
            "输入": result.source,
            "格式线索": [{"方法": name, "匹配度": score} for name, score in result.input_hints],
            "说明": result.notes,
            "耗时秒": round(result.elapsed, 3),
            "候选": [
                {
                    "手法常见度": cand.popularity,
                    "方法": cand.chain_text,
                    "结果": cand.text,
                    "提示": cand.note,
                    "细节": {k: v for k, v in cand.details.items() if not k.startswith("_")},
                }
                for cand in result.candidates
            ],
        }
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n已导出 JSON：{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
