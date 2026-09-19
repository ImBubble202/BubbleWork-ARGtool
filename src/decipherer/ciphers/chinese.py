"""【已停用】中文破译：GB2312 区位码、中文乱码修复、中文数字。

停用原因：GBK/Big5 解码会把任意二进制字节流解成「看起来像中文」的假中文，
中文评分又会给这些假中文不错的分数，实际正确率很低。

本文件没有被 ciphers/__init__.py 导入，所以这些解密器不会参与分析。
需要恢复时：在 ciphers/__init__.py 里取消 `from . import chinese` 的注释，
并把 _util.py 里 bytes_candidates 的编码列表加回 "gbk"、"big5"。
（花体/全角字符还原是通用功能，已移到 text_style.py，仍在正常使用。）
"""

from __future__ import annotations

import re
import unicodedata

from ..core import Options, RawResult, register
from ..dictionary import CHINESE_COMMON_SET
from ._util import strip_all

CATEGORY = "中文编码"

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


# --------------------------------------------------------------------------
# 区位码（GB2312）
# --------------------------------------------------------------------------

def _detect_quwei(text: str) -> float:
    tokens = re.findall(r"\d{4}", text)
    if len(tokens) < 2:
        return 0.0
    nums = [int(t) for t in tokens]
    ok = sum(1 for n in nums if 1 <= n // 100 <= 94 and 1 <= n % 100 <= 94)
    if ok == len(nums):
        return 0.85
    return 0.3 if ok >= len(nums) * 0.7 else 0.0


@register(
    "GB2312 区位码",
    CATEGORY,
    "中文电报/区位码：前两位为区、后两位为位（01-94），例如 1717 = 你",
    detect=_detect_quwei,
)
def quwei_decode(text: str, options: Options) -> list[RawResult]:
    tokens = re.findall(r"\d{4}", text)
    if len(tokens) < 2:
        # 也支持 17 17 16 17 这种两位一组写法
        pairs = re.findall(r"\d{2}", strip_all(text))
        if len(pairs) >= 4 and len(pairs) % 2 == 0:
            tokens = ["".join(pairs[i:i + 2]) for i in range(0, len(pairs), 2)]
        else:
            return []
    chars = []
    for token in tokens:
        qu, wei = int(token[:2]), int(token[2:])
        if not (1 <= qu <= 94 and 1 <= wei <= 94):
            return []
        try:
            chars.append(bytes([qu + 0xA0, wei + 0xA0]).decode("gb2312"))
        except UnicodeDecodeError:
            return []
    text_out = "".join(chars)
    return [RawResult(text_out, "GB2312 区位码")] if text_out.strip() else []


# --------------------------------------------------------------------------
# 乱码修复
# --------------------------------------------------------------------------

def _mojibake_score(text: str) -> float:
    cjk = CJK_RE.findall(text)
    if not cjk:
        return 0.0
    common = sum(1 for ch in cjk if ch in CHINESE_COMMON_SET) / len(cjk)
    return common


def _detect_mojibake(text: str) -> float:
    cjk = CJK_RE.findall(text)
    if len(cjk) < 3:
        return 0.0
    common = sum(1 for ch in cjk if ch in CHINESE_COMMON_SET) / len(cjk)
    return 0.8 if common < 0.35 else 0.0


@register(
    "中文乱码修复（UTF-8 ↔ GBK）",
    CATEGORY,
    "「浣犲ソ」这类常见乱码：UTF-8 字节被当成 GBK 读，或反过来",
    detect=_detect_mojibake,
)
def mojibake_fix(text: str, options: Options) -> list[RawResult]:
    out: list[RawResult] = []
    for name, encoder, decoder in (
        ("GBK 误读 → 还原 UTF-8", "gbk", "utf-8"),
        ("UTF-8 误读 → 还原 GBK", "utf-8", "gbk"),
        ("Latin-1 误读 → 还原 UTF-8", "latin-1", "utf-8"),
        ("GBK 误读 → 还原 Big5", "gbk", "big5"),
    ):
        try:
            repaired = text.encode(encoder, "strict").decode(decoder, "strict")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if repaired == text or "\ufffd" in repaired:
            continue
        if _mojibake_score(repaired) > _mojibake_score(text):
            out.append(RawResult(repaired, name))
    return out


# --------------------------------------------------------------------------
# 中文数字
# --------------------------------------------------------------------------

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}


def _cn_to_int(s: str) -> int | None:
    total = 0
    section = 0
    number = 0
    for ch in s:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit >= 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            number = 0
        else:
            return None
    return total + section + number


@register(
    "中文数字转阿拉伯数字",
    CATEGORY,
    "「一二三」→ 123，方便继续当作密码线索使用",
    detect=lambda t: 0.6 if len(re.findall(r"[一二三四五六七八九十百千万]", t)) >= 3 else 0.0,
)
def chinese_number_decode(text: str, options: Options) -> list[RawResult]:
    def repl(match: re.Match) -> str:
        value = _cn_to_int(match.group(0))
        return str(value) if value is not None else match.group(0)

    converted = re.sub(r"[零〇一二两三四五六七八九十百千万亿]+", repl, text)
    if converted == text:
        return []
    return [RawResult(converted, "中文数字 → 数字")]
