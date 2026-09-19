"""通用编码类：Base 系列、URL、HTML 实体、Unicode 转义、进制转换等。"""

from __future__ import annotations

import base64
import binascii
import html
import quopri
import re
import urllib.parse

from ..core import Options, RawResult, register
from ._util import bytes_candidates, control_ratio, ints_to_bytes, strip_all

CATEGORY = "编码"


def _to_results(data: bytes, label: str, note: str = "") -> list[RawResult]:
    out: list[RawResult] = []
    for text, enc in bytes_candidates(data):
        out.append(RawResult(text=text, label=f"{label}，{enc}", note=note))
    return out


# --------------------------------------------------------------------------
# Base64 / Base32 / Base16
# --------------------------------------------------------------------------

def _detect_base64(text: str) -> float:
    s = strip_all(text)
    if len(s) < 8 or not re.fullmatch(r"[A-Za-z0-9+/=_-]+", s):
        return 0.0
    score = 0.45
    if len(s) % 4 == 0:
        score += 0.1
    if s.endswith("="):
        score += 0.15
    if re.search(r"[A-Z]", s) and re.search(r"[a-z]", s):
        score += 0.15
    if re.search(r"[+/_-]", s):
        score += 0.05
    if _try_b64(s):
        score += 0.25
    return min(score, 1.0)


def _try_b64(s: str) -> bytes | None:
    for decoder, prepared in (
        (base64.b64decode, s),
        (base64.urlsafe_b64decode, s.replace("-", "+").replace("_", "/")),
    ):
        padded = prepared + "=" * (-len(prepared) % 4)
        try:
            data = decoder(padded, validate=False)
        except (binascii.Error, ValueError):
            continue
        if len(data) >= 1:
            return data
    return None


@register(
    "Base64 解码",
    CATEGORY,
    "标准 / URL 安全 Base64，自动补全 = 填充，并按 UTF-8 还原文本",
    detect=_detect_base64,
)
def base64_decode(text: str, options: Options) -> list[RawResult]:
    s = strip_all(text)
    data = _try_b64(s)
    if not data:
        return []
    results = _to_results(data, "Base64")
    if not results and data:
        results.append(RawResult(data.hex(), "Base64 → 十六进制"))
    return results


def _detect_base32(text: str) -> float:
    s = strip_all(text).upper()
    if len(s) < 8 or not re.fullmatch(r"[A-Z2-7=]+", s):
        return 0.0
    padded = s + "=" * (-len(s) % 8)
    try:
        base64.b32decode(padded, casefold=True)
    except Exception:
        return 0.3
    return 0.95


@register(
    "Base32 解码",
    CATEGORY,
    "RFC 4648 Base32，字母表为 A-Z 与 2-7",
    detect=_detect_base32,
)
def base32_decode(text: str, options: Options) -> list[RawResult]:
    s = strip_all(text).upper().replace(" ", "")
    if not re.fullmatch(r"[A-Z2-7=]+", s):
        return []
    padded = s + "=" * (-len(s) % 8)
    out: list[RawResult] = []
    for name, decoder in (("Base32", base64.b32decode), ("Base32hex", base64.b32hexdecode)):
        try:
            data = decoder(padded, casefold=True)
        except Exception:
            continue
        out.extend(_to_results(data, name))
    return out


def _detect_hex(text: str) -> float:
    s = strip_all(text).replace("0x", "").replace("0X", "")
    if len(s) < 6 or len(s) % 2 != 0 or not re.fullmatch(r"[0-9a-fA-F]+", s):
        return 0.0
    digits = sum(1 for c in s if c.isdigit())
    score = 0.45
    if digits / len(s) < 0.85:
        score += 0.2
    if re.search(r"\b[0-9a-fA-F]{2}\b", text) and " " in text:
        score += 0.2
    try:
        data = bytes.fromhex(s)
        if bytes_candidates(data):
            score += 0.25
    except ValueError:
        return 0.0
    return min(score, 1.0)


@register(
    "十六进制解码",
    CATEGORY,
    "把 68656c6c6f 这类十六进制串还原成字节，再按多种编码还原文本",
    detect=_detect_hex,
)
def hex_decode(text: str, options: Options) -> list[RawResult]:
    s = strip_all(text).replace("0x", "").replace("0X", "").replace(",", "").replace(":", "")
    if not s or len(s) % 2 != 0 or not re.fullmatch(r"[0-9a-fA-F]+", s):
        return []
    try:
        data = bytes.fromhex(s)
    except ValueError:
        return []
    return _to_results(data, "十六进制")


@register("Base85 / ASCII85 解码", CATEGORY, "Adobe ASCII85 与 RFC 1924 Base85")
def base85_decode(text: str, options: Options) -> list[RawResult]:
    s = text.strip()
    out: list[RawResult] = []
    for name, decoder in (("ASCII85", base64.a85decode), ("Base85", base64.b85decode)):
        try:
            data = decoder(s)
        except Exception:
            continue
        out.extend(_to_results(data, name))
    return out


_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


@register("Base58 解码", CATEGORY, "比特币地址常用的 Base58（不含 0OIl）")
def base58_decode(text: str, options: Options) -> list[RawResult]:
    s = strip_all(text)
    if len(s) < 6 or any(ch not in _B58_ALPHABET for ch in s):
        return []
    num = 0
    for ch in s:
        num = num * 58 + _B58_ALPHABET.index(ch)
    data = num.to_bytes((num.bit_length() + 7) // 8, "big")
    data = b"\x00" * (len(s) - len(s.lstrip("1"))) + data
    return _to_results(data, "Base58")


# --------------------------------------------------------------------------
# 百分号 / HTML / Unicode 转义 / QP
# --------------------------------------------------------------------------

@register(
    "URL 百分号解码",
    CATEGORY,
    "%68%65%6C%6C%6F 这类百分号编码（按 UTF-8 还原）",
    detect=lambda t: 0.95 if len(re.findall(r"%[0-9A-Fa-f]{2}", t)) >= 2 else 0.0,
)
def url_decode(text: str, options: Options) -> list[RawResult]:
    out: list[RawResult] = []
    try:
        decoded = urllib.parse.unquote(text, encoding="utf-8", errors="strict")
    except Exception:
        decoded = text
    if decoded != text:
        out.append(RawResult(decoded, "URL 解码（utf-8）"))
    plus = urllib.parse.unquote_plus(text)
    if plus != text and all(r.text != plus for r in out):
        out.append(RawResult(plus, "URL 解码（+ 视作空格）"))
    return out


def _looks_like_html_entity(text: str) -> float:
    return 0.9 if re.search(r"&#x?[0-9A-Fa-f]+;", text) or re.search(r"&[a-zA-Z]{2,8};", text) else 0.0


@register("HTML 实体解码", CATEGORY, "&#72;&#105; 或 &amp; 这类 HTML 实体", detect=_looks_like_html_entity)
def html_entity_decode(text: str, options: Options) -> list[RawResult]:
    if not _looks_like_html_entity(text):
        return []
    decoded = html.unescape(text)
    if decoded == text:
        return []
    return [RawResult(decoded, "HTML 实体")]


@register(
    "Unicode / 转义序列解码",
    CATEGORY,
    r"\u4f60\u597d、\x68\x69、\U0001F600 等转义写法",
    detect=lambda t: 0.9
    if len(re.findall(r"\\u[0-9a-fA-F]{4}", t)) >= 2
    or len(re.findall(r"\\x[0-9a-fA-F]{2}", t)) >= 3
    else 0.0,
)
def unicode_escape_decode(text: str, options: Options) -> list[RawResult]:
    out: list[RawResult] = []
    raw = text.strip()
    if re.search(r"\\u[0-9a-fA-F]{4}", raw):
        try:
            decoded = raw.encode("utf-8").decode("unicode_escape")
            decoded = decoded.encode("latin-1", "backslashreplace").decode("utf-8", "replace")
            if "\ufffd" not in decoded:
                out.append(RawResult(decoded, "\\uXXXX"))
        except Exception:
            pass
    if not out and re.search(r"\\x[0-9a-fA-F]{2}", raw):
        try:
            decoded = raw.encode("utf-8").decode("unicode_escape")
            out.append(RawResult(decoded, "\\xXX"))
        except Exception:
            pass
    # 形如 4F60 597D 的裸码点
    if not out and ("," in raw or " " in raw):
        points = re.findall(r"(?<![0-9A-Za-z])[0-9A-Fa-f]{4}(?![0-9A-Za-z])", raw)
        if len(points) < 2:
            return out
        try:
            decoded = "".join(chr(int(p, 16)) for p in points)
            if decoded.strip():
                out.append(RawResult(decoded, "Unicode 码点"))
        except ValueError:
            pass
    return out


@register("Quoted-Printable 解码", CATEGORY, "邮件编码：=E4=BD=A0=E5=A5=BD")
def quoted_printable_decode(text: str, options: Options) -> list[RawResult]:
    if len(re.findall(r"=[0-9A-Fa-f]{2}", text)) < 3:
        return []
    out: list[RawResult] = []
    data = quopri.decodestring(text.encode("latin-1", "ignore"))
    for dec_text, enc in bytes_candidates(data):
        out.append(RawResult(dec_text, f"Quoted-Printable（{enc}）"))
    return out


# --------------------------------------------------------------------------
# 二进制 / 进制
# --------------------------------------------------------------------------

@register(
    "二进制转文本",
    CATEGORY,
    "8 位一组的 0/1 串，也支持 7 位分组与任意分隔符",
    detect=lambda t: 0.95
    if re.fullmatch(r"[01\s,|.\-_]{16,}", t.strip()) and len(strip_all(t)) >= 16
    else 0.0,
)
def binary_decode(text: str, options: Options) -> list[RawResult]:
    s = text.strip()
    bits = re.sub(r"[^01]", "", s)
    if len(bits) < 8:
        return []
    out: list[RawResult] = []
    for size in (8, 7):
        if len(bits) % size:
            continue
        chars = []
        ok = True
        for i in range(0, len(bits), size):
            value = int(bits[i:i + size], 2)
            if value == 0 or value > 0x10FFFF:
                ok = False
                break
            chars.append(chr(value))
        if ok and chars and control_ratio("".join(chars)) < 0.2:
            label = "8 位一组" if size == 8 else "7 位一组"
            out.append(RawResult("".join(chars), label))
    # 也可能是「每组 8 位 → 字节 → UTF-8」
    if len(bits) % 8 == 0:
        data = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))
        for dec_text, enc in bytes_candidates(data):
            out.append(RawResult(dec_text, f"二进制字节（{enc}）"))
    return out


@register("八进制解码", CATEGORY, "三位一组的八进制 ASCII，例如 150 145 154")
def octal_decode(text: str, options: Options) -> list[RawResult]:
    tokens = re.findall(r"\b[0-7]{2,3}\b", text)
    if len(tokens) < 3:
        return []
    try:
        data = bytes(int(tok, 8) for tok in tokens)
    except ValueError:
        return []
    return _to_results(data, "八进制")


def _numbers_to_text(text: str, base: int = 10, min_tokens: int = 3) -> list[RawResult]:
    tokens = re.findall(r"\d+", text)
    if len(tokens) < min_tokens:
        return []
    numbers = [int(tok, base) for tok in tokens]
    if any(n > 255 for n in numbers):
        return []
    data = ints_to_bytes(numbers)
    if not data:
        return []
    return _to_results(data, "十进制字节" if base == 10 else "字节序列")


@register(
    "十进制 ASCII 码",
    CATEGORY,
    "用十进制数字写的 ASCII，例如 104 101 108 108 111",
)
def decimal_ascii_decode(text: str, options: Options) -> list[RawResult]:
    return _numbers_to_text(text, 10)


@register("十进制补零字节", CATEGORY, "形如 104101108 的定长数字串，按 3 位一组切分")
def zero_padded_decimal_decode(text: str, options: Options) -> list[RawResult]:
    s = strip_all(text)
    if len(s) < 9 or len(s) % 3 or not s.isdigit():
        return []
    numbers = [int(s[i:i + 3]) for i in range(0, len(s), 3)]
    if any(n > 255 for n in numbers):
        return []
    data = bytes(numbers)
    return _to_results(data, "三位一组十进制")
