"""解密器共用的小工具。"""

from __future__ import annotations

import re
import unicodedata


def strip_all(text: str) -> str:
    return re.sub(r"\s+", "", text)


def control_ratio(text: str) -> float:
    if not text:
        return 1.0
    bad = sum(
        1
        for ch in text
        if (unicodedata.category(ch) == "Cc" and ch not in "\n\r\t")
        or unicodedata.category(ch) == "Co"
    )
    return bad / len(text)


def bytes_candidates(
    data: bytes,
    encodings: tuple[str, ...] = ("utf-8", "latin-1"),
    max_results: int = 3,
) -> list[tuple[str, str]]:
    """把字节串还原为文本，返回 (文本, 编码名) 列表。

    只尝试 UTF-8 与「可打印拉丁字符」两种情况。中文破译已停用，
    不再尝试 GBK / Big5 / Shift-JIS —— 那类解码几乎总能把任意字节流
    解成「看着像中文」的假中文，正确率很低。
    """
    results: list[tuple[str, str]] = []
    if not data:
        return results
    for enc in encodings:
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if "\ufffd" in text or not text.strip():
            continue
        if enc == "latin-1":
            # latin-1 永远不会失败，只有内容确实像可读文本时才采用
            if not all(32 <= b <= 126 or b in (9, 10, 13) for b in data):
                continue
        if control_ratio(text) > 0.1:
            continue
        results.append((text, enc))
    # 去重（不同编码可能得到相同文本）
    seen = set()
    unique: list[tuple[str, str]] = []
    for text, enc in results:
        if text in seen:
            continue
        seen.add(text)
        unique.append((text, enc))
    return unique[:max_results]


def chunk_bits(bits: str, size: int = 8) -> str | None:
    """把 01 串按位切成字符，返回文本。"""
    usable = len(bits) - len(bits) % size
    if usable < size:
        return None
    chars = []
    for i in range(0, usable, size):
        value = int(bits[i:i + size], 2)
        if value == 0:
            continue
        chars.append(chr(value))
    text = "".join(chars)
    return text if text.strip() else None


def looks_printable_text(text: str, threshold: float = 0.85) -> bool:
    if not text:
        return False
    good = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
    return good / len(text) >= threshold


def ints_to_bytes(numbers: list[int], max_value: int = 255) -> bytes | None:
    if not numbers or any(n < 0 or n > max_value for n in numbers):
        return None
    return bytes(numbers)


ENGLISH_FREQ = {
    "a": 8.167, "b": 1.492, "c": 2.782, "d": 4.253, "e": 12.702,
    "f": 2.228, "g": 2.015, "h": 6.094, "i": 6.966, "j": 0.153,
    "k": 0.772, "l": 4.025, "m": 2.406, "n": 6.749, "o": 7.507,
    "p": 1.929, "q": 0.095, "r": 5.987, "s": 6.327, "t": 9.056,
    "u": 2.758, "v": 0.978, "w": 2.360, "x": 0.150, "y": 1.974,
    "z": 0.074,
}


def english_chi_square(text: str) -> float:
    """越小越像英文，用于暴力枚举时快速挑出候选。"""
    # 只抽样前 1200 个字符：长文本的排名结论不会因此改变，速度却快很多
    letters = [ch.lower() for ch in text[:1200] if ch.isascii() and ch.isalpha()]
    n = len(letters)
    if n < 4:
        return 1e9
    counts: dict[str, int] = {}
    for ch in letters:
        counts[ch] = counts.get(ch, 0) + 1
    chi = 0.0
    for ch, pct in ENGLISH_FREQ.items():
        expected = pct / 100.0 * n
        observed = counts.get(ch, 0)
        chi += (observed - expected) ** 2 / expected
    return chi
