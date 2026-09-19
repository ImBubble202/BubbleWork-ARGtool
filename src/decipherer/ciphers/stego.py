"""隐写与「文字表面之下」的信息：零宽字符、藏头、大小写二进制、盲文等。"""

from __future__ import annotations

import re

from ..core import Options, RawResult, register
from ._util import bytes_candidates, chunk_bits

CATEGORY = "隐写分析"

ZW_CHARS = "\u200b\u200c\u200d\u2060\ufeff\u200e\u200f"


# --------------------------------------------------------------------------
# 零宽字符
# --------------------------------------------------------------------------

@register(
    "零宽字符隐写",
    CATEGORY,
    "ZWSP/ZWNJ/ZWJ/FEFF 等不可见字符按 0/1 编码（文本复制时最容易夹带）",
    detect=lambda t: 0.95 if any(ch in t for ch in ZW_CHARS) else 0.0,
)
def zero_width_decode(text: str, options: Options) -> list[RawResult]:
    if not any(ch in text for ch in ZW_CHARS):
        return []
    mappings = [
        ({"\u200b": "0", "\u200c": "1"}, "ZWSP=0，ZWNJ=1"),
        ({"\u200c": "0", "\u200d": "1"}, "ZWNJ=0，ZWJ=1"),
        ({"\u200b": "0", "\u200d": "1"}, "ZWSP=0，ZWJ=1"),
        ({"\u200e": "0", "\u200f": "1"}, "LRM=0，RLM=1"),
        ({"\ufeff": "0", "\u200b": "1"}, "BOM=0，ZWSP=1"),
    ]
    out: list[RawResult] = []
    for mapping, label in mappings:
        bits = "".join(mapping[ch] for ch in text if ch in mapping)
        if len(bits) < 8:
            continue
        text_out = chunk_bits(bits)
        if text_out:
            out.append(RawResult(text_out, f"零宽字符二进制（{label}）"))
        if len(bits) % 8 == 0:
            data = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))
            for dec_text, enc in bytes_candidates(data):
                if dec_text != text_out:
                    out.append(RawResult(dec_text, f"零宽字节（{label}，{enc}）"))
    # 只把不可见字符按出现顺序映射成 A/B，交给通用解法继续处理
    visible = "".join("A" if ch in ("\u200b", "\u200c") else "B" for ch in text if ch in ZW_CHARS)
    if len(visible) >= 5:
        out.append(RawResult(visible, "零宽字符 → A/B 序列"))
    return out


# --------------------------------------------------------------------------
# 空白字符二进制
# --------------------------------------------------------------------------

def _detect_whitespace(text: str) -> float:
    if len(text.strip("\n\r")) == 0 and len(text) >= 16:
        return 0.7
    if len(text) >= 20 and sum(1 for c in text if c in " \t") / len(text) > 0.5:
        return 0.5
    lines = text.splitlines()
    trailing = sum(1 for line in lines if line.endswith((" ", "\t")) and line.strip())
    if len(lines) >= 4 and trailing >= 2:
        return 0.75
    return 0.0


@register(
    "空格 / 制表符二进制",
    CATEGORY,
    "用空格与 Tab（或每行行尾空格数量）夹带 0/1 数据",
    detect=_detect_whitespace,
)
def whitespace_binary_decode(text: str, options: Options) -> list[RawResult]:
    out: list[RawResult] = []
    # 整篇只有空白字符
    if len(text.strip("\n\r")) == 0 and len(text) >= 16:
        body = re.sub(r"[\n\r]", "", text)
        for label, mapping in (("空格=0，Tab=1", {" ": "0", "\t": "1"}),
                               ("空格=1，Tab=0", {" ": "1", "\t": "0"})):
            bits = "".join(mapping[ch] for ch in body if ch in mapping)
            text_out = chunk_bits(bits)
            if text_out:
                out.append(RawResult(text_out, label))
    # 每行行尾的空白字符数量 → 字符编码
    lines = text.splitlines()
    if len(lines) >= 4:
        counts = []
        for line in lines:
            m = re.search(r"[ \t]+$", line)
            counts.append(len(m.group(0)) if m else 0)
        if any(counts):
            for label, numbers in (("行尾空格数 → ASCII", counts),
                                   ("行尾空格数 -1 → ASCII", [max(c - 1, 0) for c in counts])):
                chars = "".join(chr(n) for n in numbers if 1 <= n <= 0x10FFFF)
                if chars.strip() and all(32 <= n <= 126 for n in numbers if n):
                    out.append(RawResult(chars, label))
    return out


# --------------------------------------------------------------------------
# 藏头 / 抽取
# --------------------------------------------------------------------------

def _clean_letters(text: str) -> str:
    return "".join(ch for ch in text if ch.isascii() and ch.isalpha())


@register(
    "藏头 / 首字母提取",
    CATEGORY,
    "取每行、每词、每句的开头字母或汉字，ARG 最常见的「藏在明面上」手法",
)
def acrostic_decode(text: str, options: Options) -> list[RawResult]:
    out: list[RawResult] = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 2:
        first = "".join(ln.strip()[0] for ln in lines)
        last = "".join(ln.strip()[-1] for ln in lines)
        out.append(RawResult(first, "每行首字符"))
        out.append(RawResult(last, "每行末字符"))
        # 每行第一个汉字 / 第一个字母
        out.append(RawResult("".join(_first_letter_or_cjk(ln) for ln in lines), "每行首个字母/汉字"))

    words = [w for w in re.split(r"\s+", text.strip()) if w]
    if len(words) >= 3:
        out.append(RawResult("".join(w[0] for w in words), "每词首字符"))
        out.append(RawResult("".join(w[-1] for w in words), "每词末字符"))

    # 只保留大写字母 / 只保留小写字母 / 只保留数字
    upper = "".join(ch for ch in text if ch.isupper())
    if len(upper) >= 3 and sum(1 for c in text if c.islower()) > len(upper):
        out.append(RawResult(upper, "仅大写字母"))
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 3:
        out.append(RawResult(digits, "仅提取数字"))
    return [r for r in out if r.text.strip() and r.text != text]


def _first_letter_or_cjk(line: str) -> str:
    for ch in line.strip():
        if ch.isalpha() or "\u4e00" <= ch <= "\u9fff":
            return ch
    return ""


@register(
    "大小写二进制",
    CATEGORY,
    "字母的大写/小写分别代表 1/0，8 个字母 = 一个字节",
    detect=lambda t: 0.5
    if len(t) >= 16 and 0.4 < sum(1 for c in t if c.isalpha()) / max(len(t), 1) < 1.0
    and any(c.isupper() for c in t) and any(c.islower() for c in t)
    else 0.0,
)
def case_binary_decode(text: str, options: Options) -> list[RawResult]:
    out: list[RawResult] = []
    variants = (
        ("大写=1，小写=0", lambda ch: "1" if ch.isupper() else "0"),
        ("大写=0，小写=1", lambda ch: "0" if ch.isupper() else "1"),
    )
    for label, mapper in variants:
        bits = "".join(mapper(ch) for ch in text if ch.isalpha())
        if len(bits) < 8:
            continue
        text_out = chunk_bits(bits)
        if text_out and text_out.strip():
            out.append(RawResult(text_out, label))
    return out


# --------------------------------------------------------------------------
# 盲文
# --------------------------------------------------------------------------

_BRAILLE = {
    0x01: "a", 0x03: "b", 0x09: "c", 0x19: "d", 0x11: "e", 0x0B: "f",
    0x1B: "g", 0x13: "h", 0x0A: "i", 0x1A: "j", 0x05: "k", 0x07: "l",
    0x0D: "m", 0x1D: "n", 0x15: "o", 0x0F: "p", 0x1F: "q", 0x17: "r",
    0x0E: "s", 0x1E: "t", 0x25: "u", 0x27: "v", 0x3A: "w", 0x2D: "x",
    0x3D: "y", 0x35: "z",
    0x04: "1", 0x06: "2", 0x14: "3", 0x16: "4", 0x1C: "5", 0x24: "6",
    0x2C: "7", 0x34: "8", 0x1C: "9", 0x0C: "0",
    0x02: ",", 0x12: ";", 0x22: ":", 0x32: ".", 0x00: " ",
}


@register(
    "盲文点字（Unicode Braille）",
    CATEGORY,
    "⠓⠑⠇⠇⠕ → hello，把 Unicode 盲文符号还原成英文",
    detect=lambda t: 0.95 if sum(1 for ch in t if 0x2800 <= ord(ch) <= 0x28FF) >= 3 else 0.0,
)
def braille_decode(text: str, options: Options) -> list[RawResult]:
    chars = [ch for ch in text if 0x2800 <= ord(ch) <= 0x28FF]
    if len(chars) < 3:
        return []
    out = "".join(_BRAILLE.get(ord(ch) - 0x2800, "?") for ch in chars)
    out2 = out.upper()
    return [
        RawResult(out, "盲文（小写字母表）"),
        RawResult(out2, "盲文（大写字母表）"),
    ]
