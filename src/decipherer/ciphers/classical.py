"""经典密码：凯撒、ROT、Atbash、仿射、维吉尼亚、栅栏、列置换、
培根、A1Z26、波利比奥斯方阵、敲击码、摩斯、按键密码等。"""

from __future__ import annotations

import itertools
import re
from collections import Counter

from ..core import Options, RawResult, register
from ._util import english_chi_square, strip_all

CATEGORY = "经典密码"

ALPHABET = "abcdefghijklmnopqrstuvwxyz"
UPPER = ALPHABET.upper()


def _apply_mono(text: str, mapping: dict[str, str]) -> str:
    out = []
    for ch in text:
        low = ch.lower()
        if low in mapping:
            rep = mapping[low]
            out.append(rep.upper() if ch.isupper() else rep)
        else:
            out.append(ch)
    return "".join(out)


def _letter_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if c.isascii() and c.isalpha()) / len(text)


# --------------------------------------------------------------------------
# 凯撒 / ROT 家族
# --------------------------------------------------------------------------

def _caesar_decrypt(text: str, shift: int) -> str:
    out = []
    for ch in text:
        if ch.isascii() and ch.isalpha():
            base = ord("A") if ch.isupper() else ord("a")
            out.append(chr((ord(ch) - base - shift) % 26 + base))
        else:
            out.append(ch)
    return "".join(out)


def _detect_caesar(text: str) -> float:
    ratio = _letter_ratio(text)
    letters = "".join(c for c in text if c.isascii() and c.isalpha())
    if len(letters) < 8 or ratio < 0.6:
        return 0.0
    chi = english_chi_square(text)
    # 输入本身不像英文 → 更可能是凯撒/替换类密文
    if chi > 60:
        return 0.7
    if chi > 25:
        return 0.5
    return 0.3


@register(
    "凯撒移位（暴力枚举 1-25）",
    CATEGORY,
    "把字母表整体平移 n 位。程序会枚举全部 25 种移位并给出每一种的结果",
    detect=_detect_caesar,
    needs_bruteforce=True,
)
def caesar_bruteforce(text: str, options: Options) -> list[RawResult]:
    if not any(c.isalpha() for c in text):
        return []
    return [
        RawResult(_caesar_decrypt(text, shift), f"移位 {shift}（= 加密时移 {26 - shift}）")
        for shift in range(1, 26)
    ]


def _rot47(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 33 <= code <= 126:
            out.append(chr(33 + (code - 33 + 47) % 94))
        else:
            out.append(ch)
    return "".join(out)


@register("ROT13 / ROT5 / ROT18", CATEGORY, "字母旋转 13 位（ROT13）、数字旋转 5 位（ROT5）、两者组合（ROT18）")
def rot_family(text: str, options: Options) -> list[RawResult]:
    def rot_n(s: str, n: int) -> str:
        out = []
        for ch in s:
            if ch.isascii() and ch.isalpha():
                base = ord("A") if ch.isupper() else ord("a")
                out.append(chr((ord(ch) - base + n) % 26 + base))
            else:
                out.append(ch)
        return "".join(out)

    def rot_digits(s: str) -> str:
        return "".join(chr((ord(c) - 48 + 5) % 10 + 48) if c.isdigit() else c for c in s)

    out: list[RawResult] = []
    r13 = rot_n(text, 13)
    if r13 != text:
        out.append(RawResult(r13, "ROT13"))
    r5 = rot_digits(text)
    if r5 != text and any(c.isdigit() for c in text):
        out.append(RawResult(r5, "ROT5（数字）"))
    r18 = rot_digits(r13)
    if r18 != text:
        out.append(RawResult(r18, "ROT18（字母+数字）"))
    return out


@register("ROT47", CATEGORY, "对全部可见 ASCII 字符（33-126）旋转 47 位")
def rot47_decode(text: str, options: Options) -> list[RawResult]:
    result = _rot47(text)
    return [RawResult(result, "ROT47")] if result != text else []


# --------------------------------------------------------------------------
# 单表替换
# --------------------------------------------------------------------------

@register(
    "Atbash 反字母表",
    CATEGORY,
    "字母表倒序替换：a↔z、b↔y…… 常用于希伯来密码传统",
    detect=lambda t: 0.55 if _letter_ratio(t) > 0.7 and len(strip_all(t)) >= 6 else 0.0,
)
def atbash_decode(text: str, options: Options) -> list[RawResult]:
    mapping = {ALPHABET[i]: ALPHABET[-1 - i] for i in range(26)}
    return [RawResult(_apply_mono(text, mapping), "Atbash")]


@register(
    "仿射密码（暴力枚举）",
    CATEGORY,
    "y = a·x + b 的线性替换，枚举全部 312 种合法参数组合",
    detect=_detect_caesar,
    cost=3,
    needs_bruteforce=True,
)
def affine_bruteforce(text: str, options: Options) -> list[RawResult]:
    if not any(c.isalpha() for c in text):
        return []
    valid_a = [a for a in range(1, 26) if _gcd(a, 26) == 1]
    sample = text[:1200]
    out: list[tuple[float, int]] = []
    for a in valid_a:
        a_inv = pow(a, -1, 26)
        for b in range(26):
            result = []
            for ch in sample:
                if ch.isascii() and ch.isalpha():
                    low = ch.lower()
                    idx = ALPHABET.index(low)
                    dec = (a_inv * (idx - b)) % 26
                    result.append(ALPHABET[dec].upper() if ch.isupper() else ALPHABET[dec])
                else:
                    result.append(ch)
            out.append((english_chi_square("".join(result)), a * 100 + b))
    out.sort(key=lambda item: item[0])
    # 只保留最像英文的 40 种参数组合，避免 312 条噪声淹没结果
    results: list[RawResult] = []
    for _chi, params in out[:40]:
        a, b = params // 100, params % 100
        a_inv = pow(a, -1, 26)
        plain = []
        for ch in text:
            if ch.isascii() and ch.isalpha():
                idx = ALPHABET.index(ch.lower())
                dec = (a_inv * (idx - b)) % 26
                plain.append(ALPHABET[dec].upper() if ch.isupper() else ALPHABET[dec])
            else:
                plain.append(ch)
        results.append(RawResult("".join(plain), f"a={a}, b={b}"))
    return results


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


# --------------------------------------------------------------------------
# 维吉尼亚
# --------------------------------------------------------------------------

def _vigenere_decrypt(text: str, key: str) -> str:
    key_letters = [c.lower() for c in key if c.isalpha()]
    if not key_letters:
        return text
    out = []
    i = 0
    for ch in text:
        if ch.isascii() and ch.isalpha():
            shift = ALPHABET.index(key_letters[i % len(key_letters)])
            base = ord("A") if ch.isupper() else ord("a")
            out.append(chr((ord(ch) - base - shift) % 26 + base))
            i += 1
        else:
            out.append(ch)
    return "".join(out)


def _crack_vigenere_key(text: str, key_len: int) -> str:
    letters = [c.lower() for c in text[:3000] if c.isascii() and c.isalpha()]
    key = []
    for column in range(key_len):
        chunk = letters[column::key_len]
        if not chunk:
            key.append("a")
            continue
        best_shift, best_chi = 0, None
        for shift in range(26):
            shifted = "".join(ALPHABET[(ALPHABET.index(c) - shift) % 26] for c in chunk)
            chi = english_chi_square(shifted)
            if best_chi is None or chi < best_chi:
                best_shift, best_chi = shift, chi
        key.append(ALPHABET[best_shift])
    return "".join(key)


@register(
    "维吉尼亚密码",
    CATEGORY,
    "多表替换：若已知密钥请在「密钥」栏填写；未知时自动猜测 1-8 位密钥",
    detect=lambda t: 0.5 if _letter_ratio(t) > 0.75 and len(strip_all(t)) >= 12 else 0.0,
    cost=3,
)
def vigenere_decode(text: str, options: Options) -> list[RawResult]:
    out: list[RawResult] = []
    for key in options.keys:
        if key.isalpha():
            out.append(RawResult(_vigenere_decrypt(text, key), f"密钥 {key}"))
    if not options.enable_bruteforce:
        return out
    letters = [c for c in text if c.isascii() and c.isalpha()]
    if len(letters) < 16:
        return out
    for key_len in range(1, 9):
        key = _crack_vigenere_key(text, key_len)
        out.append(RawResult(_vigenere_decrypt(text, key), f"自动猜测密钥 {key}（长度 {key_len}）"))
    return out


# --------------------------------------------------------------------------
# 换位密码
# --------------------------------------------------------------------------

def _rail_fence_decrypt(text: str, rails: int) -> str:
    if rails < 2 or rails >= len(text):
        return text
    pattern = list(range(rails)) + list(range(rails - 2, 0, -1))
    row_of_char = [pattern[i % len(pattern)] for i in range(len(text))]
    counts = Counter(row_of_char)
    rows: dict[int, list[str]] = {}
    pos = 0
    for row in range(rails):
        size = counts.get(row, 0)
        rows[row] = list(text[pos:pos + size])
        pos += size
    cursors: dict[int, int] = {row: 0 for row in rows}
    out = []
    for row in row_of_char:
        out.append(rows[row][cursors[row]])
        cursors[row] += 1
    return "".join(out)


@register(
    "栅栏密码（Z 栅栏）",
    CATEGORY,
    "把文本按 zigzag 写到 n 条栅栏上再按行读取，枚举 2-10 条栅栏",
    needs_bruteforce=True,
)
def rail_fence_decode(text: str, options: Options) -> list[RawResult]:
    compact = strip_all(text)
    if len(compact) < 6:
        return []
    # 密文里的空格有两种可能：要么是原文空格，要么只是排版
    variants = [(compact, ""), (text, "（保留原空格）")] if text != compact else [(compact, "")]
    out: list[RawResult] = []
    for source, suffix in variants:
        for rails in range(2, 11):
            if rails >= len(source):
                continue
            out.append(RawResult(_rail_fence_decrypt(source, rails), f"{rails} 条栅栏{suffix}"))
    return out


@register(
    "分栏读取密码",
    CATEGORY,
    "把密文按 n 列切分后按列读取，枚举 2-10 列",
    needs_bruteforce=True,
)
def column_read_decode(text: str, options: Options) -> list[RawResult]:
    compact = strip_all(text)
    n = len(compact)
    if n < 6:
        return []
    out: list[RawResult] = []
    variants = [(compact, ""), (text, "（保留原空格）")] if text != compact else [(compact, "")]
    for source, suffix in variants:
        length = len(source)
        for cols in range(2, 11):
            rows = (length + cols - 1) // cols
            grid = [source[i * rows:(i + 1) * rows] for i in range(cols)]
            plain = []
            for r in range(rows):
                for c in range(cols):
                    if r < len(grid[c]):
                        plain.append(grid[c][r])
            out.append(RawResult("".join(plain), f"{cols} 列读取{suffix}"))
    return out


def _columnar_decrypt(text: str, key: str) -> str:
    order = sorted(range(len(key)), key=lambda i: key[i])
    n = len(text)
    cols = len(key)
    rows = (n + cols - 1) // cols
    short_rows = cols - (rows * cols - n) if n % cols else 0
    lengths = []
    for idx in range(cols):
        is_short = idx >= cols - short_rows if short_rows else False
        lengths.append(rows - 1 if is_short else rows)
    columns: list[list[str]] = []
    pos = 0
    for col_index in sorted(range(cols), key=lambda i: order.index(i)):
        length = lengths[col_index]
        columns.append(list(text[pos:pos + length]))
        pos += length
    plain: list[str] = []
    for r in range(rows):
        for c in range(cols):
            if r < len(columns[c]):
                plain.append(columns[c][r])
    return "".join(plain)


@register(
    "列换位密码",
    CATEGORY,
    "按密钥字母顺序重排各列。已知密钥请填入「密钥」栏，否则暴力枚举短密钥",
    cost=3,
    needs_bruteforce=True,
)
def columnar_decode(text: str, options: Options) -> list[RawResult]:
    compact = strip_all(text)
    if len(compact) < 8:
        return []
    out: list[RawResult] = []
    for key in options.keys:
        if len(key) >= 2:
            out.append(RawResult(_columnar_decrypt(compact, key), f"密钥 {key}"))
    if not options.enable_bruteforce:
        return out
    # 用「按字母顺序的排列」暴力枚举 2-5 列的所有列序
    tried: list[tuple[float, str, tuple]] = []
    for cols in range(2, 6):
        for perm in itertools.permutations(range(cols)):
            key = "".join(chr(ord("a") + p) for p in perm)
            plain = _columnar_decrypt(compact, key)
            tried.append((english_chi_square(plain), plain, perm))
    tried.sort(key=lambda item: item[0])
    out.extend(
        RawResult(plain, f"列序 {perm}") for _chi, plain, perm in tried[:40]
    )
    return out


@register("整段倒序 / 单词倒序", CATEGORY, "最简单的换位：整段反过来，或把单词顺序反过来")
def reverse_decode(text: str, options: Options) -> list[RawResult]:
    out = [RawResult(text[::-1], "整段倒序")]
    words = text.split()
    if len(words) > 1:
        out.append(RawResult(" ".join(reversed(words)), "单词顺序倒序"))
    out.append(RawResult(" ".join(w[::-1] for w in words), "每个单词倒序"))
    return out


# --------------------------------------------------------------------------
# 字母 / 数字替换
# --------------------------------------------------------------------------

_BACON_24 = "abcdefghiklmnopqrstuvwxyz".replace("u", "")   # 24 字母：i/j、u/v 合并


def _detect_bacon(text: str) -> float:
    compact = strip_all(text).upper()
    if len(compact) < 10:
        return 0.0
    if not re.fullmatch(r"[AB01]+", compact):
        return 0.0
    if len(compact) % 5 == 0:
        return 0.95
    return 0.4


@register("培根密码（Bacon）", CATEGORY, "用 A/B 两种符号表示 5 位二进制，两种字母表都尝试", detect=_detect_bacon)
def bacon_decode(text: str, options: Options) -> list[RawResult]:
    raw = strip_all(text).upper().replace("0", "A").replace("1", "B")
    if len(raw) < 5 or not re.fullmatch(r"[AB]+", raw):
        return []
    usable = len(raw) - len(raw) % 5
    out: list[RawResult] = []
    for name, alphabet in (("24 字母表（i/j、u/v 合并）", _BACON_24),
                           ("26 字母表", ALPHABET)):
        chars = []
        for i in range(0, usable, 5):
            code = int(raw[i:i + 5].replace("A", "0").replace("B", "1"), 2)
            if code < len(alphabet):
                chars.append(alphabet[code])
        if chars:
            out.append(RawResult("".join(chars), name))
    return out


def _detect_a1z26(text: str) -> float:
    tokens = re.findall(r"\d+", text)
    if len(tokens) < 3:
        return 0.0
    nums = [int(t) for t in tokens]
    if all(1 <= n <= 26 for n in nums):
        return 0.9
    if all(0 <= n <= 25 for n in nums):
        return 0.5
    return 0.0


@register(
    "A1Z26 数字字母",
    CATEGORY,
    "1=A、2=B…… 也兼容 0=A 的写法，支持空格、减号、点号分隔",
    detect=_detect_a1z26,
)
def a1z26_decode(text: str, options: Options) -> list[RawResult]:
    tokens = re.findall(r"\d+", text)
    if len(tokens) < 2:
        return []
    nums = [int(t) for t in tokens]
    out: list[RawResult] = []
    if all(1 <= n <= 26 for n in nums):
        out.append(RawResult("".join(ALPHABET[n - 1] for n in nums), "1=A"))
    if all(0 <= n <= 25 for n in nums):
        out.append(RawResult("".join(ALPHABET[n] for n in nums), "0=A"))
    # 粘连写法：如 8-5-12-12-15 已覆盖，这里再尝试两位数切分
    compact = strip_all(text)
    if len(out) < 2 and compact.isdigit() and len(compact) % 2 == 0:
        pairs = [int(compact[i:i + 2]) for i in range(0, len(compact), 2)]
        if all(1 <= n <= 26 for n in pairs):
            out.append(RawResult("".join(ALPHABET[n - 1] for n in pairs), "两位一组 1=A"))
    return out


_POLYBIUS_IJ = "abcdefghiklmnopqrstuvwxyz"    # 25 字母，j 与 i 合并
_POLYBIUS_CK = "abcdefghijlmnopqrstuvwxyz"    # 25 字母，k 与 c 合并（敲击码习惯）


def _detect_polybius(text: str) -> float:
    digits = re.findall(r"[1-5]", text)
    others = re.sub(r"[1-5\s,;/|.\-]", "", text)
    if len(digits) < 6 or others:
        return 0.0
    return 0.9 if len(digits) % 2 == 0 else 0.4


@register(
    "波利比奥斯方阵（Polybius）",
    CATEGORY,
    "两个 1-5 的数字表示一个字母，5×5 方阵（i/j 或 c/k 合并两种变体）",
    detect=_detect_polybius,
)
def polybius_decode(text: str, options: Options) -> list[RawResult]:
    digits = re.findall(r"[1-5]", text)
    if len(digits) < 4 or len(digits) % 2:
        return []
    coords = [(int(digits[i]) - 1, int(digits[i + 1]) - 1) for i in range(0, len(digits), 2)]
    out: list[RawResult] = []
    for name, alphabet in (("i/j 合并", _POLYBIUS_IJ), ("c/k 合并", _POLYBIUS_CK)):
        chars = []
        ok = True
        for row, col in coords:
            idx = row * 5 + col
            if idx >= len(alphabet):
                ok = False
                break
            chars.append(alphabet[idx])
        if ok:
            out.append(RawResult("".join(chars), f"5×5 方阵（{name}）"))
    # 行列交换变体
    swapped = [(c, r) for r, c in coords]
    chars = []
    for row, col in swapped:
        idx = row * 5 + col
        if idx < len(_POLYBIUS_IJ):
            chars.append(_POLYBIUS_IJ[idx])
    if chars:
        out.append(RawResult("".join(chars), "5×5 方阵（行列对调）"))
    return out


@register(
    "敲击码（Tap Code）",
    CATEGORY,
    "越战战俘常用的敲击密码：行列各敲 1-5 下表示一个字母，k 用 c 代替",
    detect=_detect_polybius,
)
def tap_code_decode(text: str, options: Options) -> list[RawResult]:
    digits = re.findall(r"[1-5]", text)
    if len(digits) < 4 or len(digits) % 2:
        return []
    chars = []
    for i in range(0, len(digits), 2):
        idx = (int(digits[i]) - 1) * 5 + (int(digits[i + 1]) - 1)
        if idx >= len(_POLYBIUS_CK):
            return []
        chars.append(_POLYBIUS_CK[idx])
    return [RawResult("".join(chars), "敲击码数字写法")]


# --------------------------------------------------------------------------
# 摩斯电码
# --------------------------------------------------------------------------

MORSE_TABLE: dict[str, str] = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "'": ".----.", "!": "-.-.--",
    "/": "-..-.", "(": "-.--.", ")": "-.--.-", "&": ".-...", ":": "---...",
    ";": "-.-.-.", "=": "-...-", "+": ".-.-.", "-": "-....-", "_": "..--.-",
    '"': ".-..-.", "$": "...-..-", "@": ".--.-.",
}
MORSE_REVERSE: dict[str, str] = {v: k for k, v in MORSE_TABLE.items()}

MORSE_NORMALIZE = str.maketrans({
    "·": ".", "。": ".", "•": ".", "．": ".", "・": ".", "∙": ".",
    "—": "-", "–": "-", "−": "-", "－": "-", "―": "-", "_": "-", "ー": "-",
    "／": "/", "|": "/", "｜": "/", "│": "/", "\\": "/",
})


def _normalize_morse(text: str) -> str:
    s = text.translate(MORSE_NORMALIZE)
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[ \t\u3000]+", " ", s)
    s = re.sub(r"/+", " / ", s)
    return s.strip()


def _detect_morse(text: str) -> float:
    s = _normalize_morse(text)
    if len(s) < 5:
        return 0.0
    if not re.fullmatch(r"[.\-/ ]+", s):
        return 0.0
    if "." in s and "-" in s:
        return 0.95
    return 0.35


def _segment_morse(code: str, limit: int = 40) -> list[str]:
    """无分隔符的摩斯串：枚举所有合理切分，挑出最像人话的若干种。

    关键技巧是「按切出来的字母个数从少到多枚举」：
    先看长字母（H、L 这类 4 位码）组成的短解，再看碎切的解。
    这样 HELP 之类的正确答案不会像纯深度优先那样被埋在 EEEEE 里。
    """
    import time as _time

    n = len(code)
    if n < 2 or n > 90:
        return []

    # 每个后缀至少/至多需要多少个字母
    min_letters = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        best = 10 ** 9
        for size in (1, 2, 3, 4, 5, 6):
            if i + size <= n and code[i:i + size] in MORSE_REVERSE:
                best = min(best, 1 + min_letters[i + size])
        min_letters[i] = best
    if min_letters[0] >= 10 ** 9:
        return []

    # 枚举预算：正常题目（20 个点划以内）几十毫秒就能算完，
    # 这里限时是为了防止在乱码上反复尝试时拖慢整体速度
    deadline = _time.time() + 0.25
    found: list[str] = []
    cap = 8000

    def walk(pos: int, left: int, acc: list[str]) -> None:
        if len(found) >= cap or _time.time() > deadline:
            return
        if pos == n:
            if left == 0:
                found.append("".join(acc))
            return
        if left <= 0 or min_letters[pos] > left or left > n - pos:
            return
        for size in (4, 2, 6, 3, 5, 1):
            if pos + size > n:
                continue
            letter = MORSE_REVERSE.get(code[pos:pos + size])
            if letter is None:
                continue
            acc.append(letter)
            walk(pos + size, left - 1, acc)
            acc.pop()

    for letters in range(min_letters[0], n + 1):
        if len(found) >= cap or _time.time() > deadline:
            break
        walk(0, letters, [])

    if not found:
        return []
    ranked = sorted({text: _morse_word_rank(text) for text in found}.items(),
                    key=lambda item: item[1], reverse=True)
    return [text for text, _score in ranked[:limit]]


def _morse_word_rank(text: str) -> float:
    """摩斯切分排序用：像英文 + 整段就是个真单词时额外加分（轻量、可缓存）。"""
    from .. import ngram

    letters = text.lower()
    score = 0.0
    quad = ngram.quadgram_mean_logprob(letters)
    if quad is not None:
        score += 0.6 * max(0.0, min(1.0, (quad + 8.3) / 3.9))
    tri = ngram.trigram_mean_logprob(letters)
    if tri is not None:
        score += 0.4 * max(0.0, min(1.0, (tri + 7.0) / 3.8))
    if ngram.word_known(letters):
        score += 0.35
    return score


@register(
    "摩斯电码（Morse）",
    CATEGORY,
    "点划与分隔符会先归一化（·/。/—/＿ 等都能识别），并支持无分隔符时的自动切分",
    detect=_detect_morse,
)
def morse_decode(text: str, options: Options) -> list[RawResult]:
    s = _normalize_morse(text)
    if not s or not re.fullmatch(r"[.\-/ ]+", s):
        return []
    out: list[RawResult] = []
    segments = [seg for seg in s.split("/") if seg.strip()]
    has_word_sep = len(segments) > 1
    tokens_per_segment = [[t for t in seg.split(" ") if t] for seg in segments]
    flat_tokens = [t for seg in tokens_per_segment for t in seg]

    codes_valid = all(tok in MORSE_REVERSE for tok in flat_tokens)
    # 情况一：空格 = 字母分隔，/ = 单词分隔（最常见的标准写法）
    if len(flat_tokens) > 1 and codes_valid:
        words = ["".join(MORSE_REVERSE[tok] for tok in seg) for seg in tokens_per_segment]
        if has_word_sep:
            out.append(RawResult(" ".join(words), "空格=字母分隔，/ = 单词分隔"))
        else:
            out.append(RawResult("".join(words), "空格=字母分隔"))
            out.append(RawResult(" ".join(words), "空格=单词分隔（另一种解读）"))
    # 情况二：完全没有分隔符（或只有 / ）→ 自动切分
    segment_candidates: list[list[str]] = []
    for seg in segments:
        compact = seg.replace(" ", "")
        if len(compact) < 2 or " " in seg.strip():
            continue
        found = _segment_morse(compact)[:6]
        if found:
            segment_candidates.append(found)
            for candidate in found:
                out.append(RawResult(candidate, "无分隔符自动切分"))
    # 多段（有 / 或换行）时，把各段的最佳切分拼起来
    if len(segment_candidates) >= 2:
        for candidate in itertools.islice(
            itertools.product(*[c[:8] for c in segment_candidates]), 60
        ):
            out.append(RawResult(" ".join(candidate), "无分隔符自动切分（分段拼接）"))
    # 情况三：整段只有 / 分隔
    if has_word_sep and len(flat_tokens) == 1 and codes_valid:
        letters = "".join(MORSE_REVERSE[tok] for tok in flat_tokens)
        out.append(RawResult(letters, "仅按 / 分隔"))
    return out


# --------------------------------------------------------------------------
# 按键类
# --------------------------------------------------------------------------

T9_KEYS = {"2": "abc", "3": "def", "4": "ghi", "5": "jkl", "6": "mno",
           "7": "pqrs", "8": "tuv", "9": "wxyz"}


@register(
    "手机九宫格多按（T9 Multi-tap）",
    CATEGORY,
    "44 33 555 555 666 → hello。支持空格分隔与连写自动切分",
)
def t9_multitap_decode(text: str, options: Options) -> list[RawResult]:
    if not re.fullmatch(r"[0-9\s\-]+", text.strip()):
        return []
    out: list[RawResult] = []
    # 0 在多按编码里表示空格，1 表示标点（这里忽略标点符号本身）
    tokens = [t for t in re.split(r"[\s\-]+", text.strip()) if t]
    if len(tokens) > 1 and all(t.isdigit() for t in tokens):
        chars = []
        ok = True
        for tok in tokens:
            if tok == "0":
                chars.append(" ")
                continue
            key = tok[0]
            if key not in T9_KEYS or len(set(tok)) != 1:
                ok = False
                break
            idx = (len(tok) - 1) % len(T9_KEYS[key])
            chars.append(T9_KEYS[key][idx])
        if ok and any(c.strip() for c in chars):
            out.append(RawResult("".join(chars), "空格分隔多按（0 = 空格）"))
    compact = strip_all(text)
    if compact.isdigit() and len(compact) >= 6:
        candidates = _segment_t9(compact)
        for cand in candidates[:10]:
            out.append(RawResult(cand, "连写自动切分"))
    return [r for r in out if r.text != text]


def _segment_t9(digits: str, limit: int = 10) -> list[str]:
    from ..scoring import score_text

    results: list[list[str]] = []

    def walk(pos: int, acc: list[str]) -> None:
        if len(results) >= 800:
            return
        if pos == len(digits):
            results.append(list(acc))
            return
        key = digits[pos]
        letters = T9_KEYS.get(key)
        if not letters:
            return
        run = 1
        while pos + run < len(digits) and digits[pos + run] == key and run < 4:
            run += 1
        for size in range(1, run + 1):
            acc.append(letters[(size - 1) % len(letters)])
            walk(pos + size, acc)
            acc.pop()

    walk(0, [])
    if not results:
        return []
    scored = sorted(
        (("".join(chars), score_text("".join(chars)).value) for chars in results),
        key=lambda item: item[1],
        reverse=True,
    )
    seen = set()
    out = []
    for text, _ in scored:
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


_QWERTY_ROWS = ["`1234567890-=", "qwertyuiop[]\\", "asdfghjkl;'", "zxcvbnm,./"]

_DVORAK_TO_QWERTY = str.maketrans(
    "',.pyfgcrl/=aoeuidhtns-;qjkxbmwvz",
    "qwertyuiop[]asdfghjkl;'zxcvbnm,./",
)


@register(
    "键盘错位（QWERTY 左右手滑）",
    CATEGORY,
    "相邻按键错位 1 位（左手多打/右手多打）以及 Dvorak 与 QWERTY 布局换算",
)
def keyboard_shift_decode(text: str, options: Options) -> list[RawResult]:
    out: list[RawResult] = []
    for direction in (-1, 1):
        mapping: dict[str, str] = {}
        for row in _QWERTY_ROWS:
            for i, ch in enumerate(row):
                target = i + direction
                if 0 <= target < len(row):
                    mapping[ch] = row[target]
        shifted = "".join(mapping.get(ch, mapping.get(ch.lower(), ch.lower())).upper()
                          if ch.isupper() else mapping.get(ch, ch) for ch in text)
        if shifted != text and any(c.isalpha() for c in shifted):
            out.append(RawResult(shifted, "左手错位" if direction == -1 else "右手错位"))
    dvorak = text.translate(_DVORAK_TO_QWERTY)
    if dvorak != text:
        out.append(RawResult(dvorak, "Dvorak 键盘 → QWERTY"))
    return out


@register(
    "字母表分组替换",
    CATEGORY,
    "常见变体：A=1…Z=26 反过来（Z=1），或按键盘三行分组编号",
    detect=_detect_a1z26,
)
def alt_alphabet_decode(text: str, options: Options) -> list[RawResult]:
    tokens = re.findall(r"\d+", text)
    if len(tokens) < 3:
        return []
    nums = [int(t) for t in tokens]
    if not all(1 <= n <= 26 for n in nums):
        return []
    return [RawResult("".join(ALPHABET[26 - n] for n in nums), "Z=1 倒序数字表")]
