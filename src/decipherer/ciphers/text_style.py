"""字符外观还原：花体字母、圆圈字母、小型大写、全角字符等视觉变体。

这一类不是「中文破译」，而是把看起来像字母的各种 Unicode 变体
（𝔥𝔢𝔩𝔩𝔬、ⓗⓔⓛⓛⓞ、ｈｅｌｌｏ）还原成普通 ASCII，ARG 里很常见。
"""

from __future__ import annotations

import unicodedata

from ..core import Options, RawResult, register

CATEGORY = "编码"

# 数学字母符号（粗体、斜体、花体、双线体、无衬线体等）统一映射回 ASCII
_FANCY_MAP: dict[str, str] = {}
_MATH_STYLES = [
    (0x1D400, 0x1D419, "A"), (0x1D41A, 0x1D433, "a"),
    (0x1D434, 0x1D44D, "A"), (0x1D44E, 0x1D467, "a"),
    (0x1D468, 0x1D481, "A"), (0x1D482, 0x1D49B, "a"),
    (0x1D49C, 0x1D4B5, "A"), (0x1D4B6, 0x1D4CF, "a"),
    (0x1D4D0, 0x1D4E9, "A"), (0x1D4EA, 0x1D503, "a"),
    (0x1D504, 0x1D51D, "A"), (0x1D51E, 0x1D537, "a"),
    (0x1D538, 0x1D551, "A"), (0x1D552, 0x1D56B, "a"),
    (0x1D56C, 0x1D585, "A"), (0x1D586, 0x1D59F, "a"),
    (0x1D5A0, 0x1D5B9, "A"), (0x1D5BA, 0x1D5D3, "a"),
    (0x1D5D4, 0x1D5ED, "A"), (0x1D5EE, 0x1D607, "a"),
    (0x1D608, 0x1D621, "A"), (0x1D622, 0x1D63B, "a"),
    (0x1D63C, 0x1D655, "A"), (0x1D656, 0x1D66F, "a"),
    (0x1D670, 0x1D689, "A"), (0x1D68A, 0x1D6A3, "a"),
]
for _start, _end, _base_char in _MATH_STYLES:
    for _i, _cp in enumerate(range(_start, _end + 1)):
        _FANCY_MAP[chr(_cp)] = chr(ord(_base_char) + _i)

# 圆圈字母
_CIRCLED = "ⓐⓑⓒⓓⓔⓕⓖⓗⓘⓙⓚⓛⓜⓝⓞⓟⓠⓡⓢⓣⓤⓥⓦⓧⓨⓩ"
for _i, _ch in enumerate(_CIRCLED):
    _FANCY_MAP[_ch] = chr(ord("a") + _i)
for _i, _ch in enumerate(_CIRCLED.upper()):
    _FANCY_MAP[_ch] = chr(ord("A") + _i)

# 小型大写字母
for _i, _ch in enumerate("ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘqʀꜱᴛᴜᴠᴡxʏᴢ"):
    if _ch.isalpha():
        _FANCY_MAP[_ch] = "abcdefghijklmnopqrstuvwxyz"[_i]


def _detect_fancy(text: str) -> float:
    fancy = sum(1 for ch in text if ch in _FANCY_MAP)
    fullwidth = sum(
        1 for ch in text
        if unicodedata.east_asian_width(ch) == "F" and not ch.isascii()
    )
    if fancy >= 2:
        return 0.85
    if fullwidth >= 2:
        return 0.6
    return 0.0


@register(
    "花体 / 全角字符还原",
    CATEGORY,
    "𝔥𝔢𝔩𝔩𝔬、ⓗⓔⓛⓛⓞ、ｈｅｌｌｏ 等视觉变体统一还原为普通字符",
    detect=_detect_fancy,
)
def fancy_unicode_decode(text: str, options: Options) -> list[RawResult]:
    out: list[RawResult] = []
    mapped = "".join(_FANCY_MAP.get(ch, ch) for ch in text)
    if mapped != text:
        out.append(RawResult(mapped, "花体字符映射"))
    normalized = unicodedata.normalize("NFKC", text)
    if normalized != text and normalized != mapped:
        out.append(RawResult(normalized, "全角/兼容字符归一化（NFKC）"))
    return out

