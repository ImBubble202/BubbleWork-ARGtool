"""现代/网络类：异或（XOR）、压缩流、JWT、Leet 火星文、Brainfuck 等。"""

from __future__ import annotations

import base64
import binascii
import gzip
import json
import re
import zlib

from ..core import Options, RawResult, register
from ._util import bytes_candidates, english_chi_square, strip_all

CATEGORY = "现代编码"


def _input_bytes(text: str, options: Options) -> list[tuple[bytes, str]]:
    """猜测输入背后真正的字节：十六进制 / Base64 / 二进制 / 原始文本。"""
    out: list[tuple[bytes, str]] = []
    s = strip_all(text)
    if len(s) >= 4 and len(s) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", s):
        try:
            out.append((bytes.fromhex(s), "十六进制"))
        except ValueError:
            pass
    if len(s) >= 8 and re.fullmatch(r"[01]+", s) and len(s) % 8 == 0:
        out.append((bytes(int(s[i:i + 8], 2) for i in range(0, len(s), 8)), "二进制"))
    if len(s) >= 8 and re.fullmatch(r"[A-Za-z0-9+/=_-]+", s):
        for name, decoder in (("Base64", base64.b64decode), ("Base64URL", base64.urlsafe_b64decode)):
            try:
                data = decoder(s + "=" * (-len(s) % 4))
            except (binascii.Error, ValueError):
                continue
            if data:
                out.append((data, name))
                break
    out.append((text.encode("utf-8", "surrogatepass"), "原始文本"))
    # 去重
    seen = set()
    unique = []
    for data, label in out:
        if data in seen:
            continue
        seen.add(data)
        unique.append((data, label))
    return unique


def _looks_like_binary_blob(text: str) -> float:
    s = strip_all(text)
    if len(s) < 8:
        return 0.0
    if re.fullmatch(r"[0-9a-fA-F]+", s) and len(s) % 2 == 0:
        return 0.6
    if re.fullmatch(r"[01]+", s) and len(s) % 8 == 0:
        return 0.7
    if re.fullmatch(r"[A-Za-z0-9+/=_-]{12,}", s):
        return 0.4
    return 0.0


@register(
    "单字节异或（XOR 暴力枚举）",
    CATEGORY,
    "对整段数据逐字节异或同一个密钥，枚举 0-255 并提供最像明文的结果",
    detect=_looks_like_binary_blob,
    cost=3,
    needs_bruteforce=True,
)
def xor_single_byte(text: str, options: Options) -> list[RawResult]:
    from ..scoring import score_text

    # 输入本身就是通顺的明文时，异或枚举没有意义
    if score_text(text).value > 0.55:
        return []
    results: list[RawResult] = []
    scored_all: list[tuple[float, str, str, str]] = []
    for data, source_label in _input_bytes(text, options):
        if len(data) < 3:
            continue
        for key in range(1, 256):
            decoded_bytes = bytes(b ^ key for b in data)
            if sum(1 for b in decoded_bytes if 32 <= b <= 126 or b in (9, 10, 13)) < len(decoded_bytes) * 0.85:
                continue
            plain = decoded_bytes.decode("latin-1")
            if english_chi_square(plain) > 200:
                continue
            scored_all.append((score_text(plain).value, f"{key}", plain, source_label))
    scored_all.sort(key=lambda item: item[0], reverse=True)
    for value, key_text, plain, source_label in scored_all[:12]:
        if value < 0.45:
            continue
        key = int(key_text)
        printable_key = chr(key) if 32 <= key <= 126 else f"0x{key:02X}"
        results.append(
            RawResult(plain, f"密钥 {printable_key}（0x{key:02X}）", note=f"来源：{source_label}")
        )
    return results


@register(
    "多字节异或（已知密钥）",
    CATEGORY,
    "用「密钥」栏里填写的文本重复异或，用于 CTF/ARG 中常见的循环密钥",
    cost=2,
)
def xor_repeating_key(text: str, options: Options) -> list[RawResult]:
    keys = [k for k in options.keys if k]
    if not keys:
        return []
    out: list[RawResult] = []
    for data, source_label in _input_bytes(text, options):
        for key in keys:
            key_bytes = key.encode("utf-8")
            decoded = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(data))
            for dec_text, enc in bytes_candidates(decoded):
                out.append(RawResult(dec_text, f"密钥 {key}（{source_label}，{enc}）"))
    return out


def _compressed_inputs(text: str) -> list[tuple[bytes, str]]:
    s = strip_all(text)
    candidates: list[tuple[bytes, str]] = []
    if re.fullmatch(r"[0-9a-fA-F]+", s) and len(s) % 2 == 0:
        try:
            candidates.append((bytes.fromhex(s), "十六进制"))
        except ValueError:
            pass
    if re.fullmatch(r"[A-Za-z0-9+/=_-]{8,}", s):
        try:
            candidates.append((base64.b64decode(s + "=" * (-len(s) % 4)), "Base64"))
        except Exception:
            pass
    return candidates


@register("zlib / Deflate 解压", CATEGORY, "被压缩后再编码的密文，常见于网页与 CTF 题")
def zlib_decode(text: str, options: Options) -> list[RawResult]:
    out: list[RawResult] = []
    for data, label in _compressed_inputs(text):
        for name, func in (("zlib", zlib.decompress),
                           ("deflate（raw）", lambda d: zlib.decompress(d, -15))):
            try:
                raw = func(data)
            except Exception:
                continue
            for dec_text, enc in bytes_candidates(raw):
                out.append(RawResult(dec_text, f"{name} 解压（来自 {label}，{enc}）"))
    return out


@register(
    "gzip 解压",
    CATEGORY,
    "以 1F 8B 开头的 gzip 数据",
    detect=lambda t: 0.7 if strip_all(t).upper().startswith("1F8B") else 0.0,
)
def gzip_decode(text: str, options: Options) -> list[RawResult]:
    out: list[RawResult] = []
    for data, label in _compressed_inputs(text):
        try:
            raw = gzip.decompress(data)
        except Exception:
            continue
        for dec_text, enc in bytes_candidates(raw):
            out.append(RawResult(dec_text, f"gzip 解压（来自 {label}，{enc}）"))
    return out


@register(
    "JWT 解析",
    CATEGORY,
    "形如 xxxxx.yyyyy.zzzzz 的 JSON Web Token，解出头部与载荷",
    detect=lambda t: 0.9 if len(t.strip().split(".")) == 3 and t.count(".") == 2 else 0.0,
)
def jwt_decode(text: str, options: Options) -> list[RawResult]:
    parts = text.strip().split(".")
    if len(parts) != 3:
        return []
    out: list[RawResult] = []
    for name, segment in (("头部", parts[0]), ("载荷", parts[1])):
        try:
            data = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
            payload = json.loads(data.decode("utf-8"))
        except Exception:
            continue
        out.append(RawResult(json.dumps(payload, ensure_ascii=False), f"JWT {name}"))
    return out


_LEET_MAP = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b",
    "9": "g", "2": "z", "6": "g", "@": "a", "$": "s", "!": "i", "|": "l",
    "+": "t", "(": "c", "<": "c",
})


@register(
    "Leet / 火星文还原",
    CATEGORY,
    "h3ll0 → hello：把数字与符号还原成字母",
    detect=lambda t: 0.6
    if re.search(r"[A-Za-z]", t) and re.search(r"[01345789@$!]", t) and len(t.strip()) >= 6
    else 0.0,
)
def leet_decode(text: str, options: Options) -> list[RawResult]:
    if not re.search(r"[01345789@$!]", text):
        return []
    out = [RawResult(text.translate(_LEET_MAP), "Leet 还原")]
    # 还原后再跑一次常见变体：把剩余数字当成单词内字符
    return out


BRAINFUCK_OPS = set("><+-.,[]")


@register(
    "Brainfuck 解释执行",
    CATEGORY,
    "由 > < + - . , [ ] 组成的 esolang，ARG 里偶有出现",
    detect=lambda t: 0.8
    if len(strip_all(t)) >= 16 and len(set(strip_all(t))) <= 8 and set(strip_all(t)) <= BRAINFUCK_OPS
    else 0.0,
    cost=3,
)
def brainfuck_decode(text: str, options: Options) -> list[RawResult]:
    code = re.sub(r"[^><+\-.,\[\]]", "", text)
    if len(code) < 16:
        return []
    # 预匹配括号
    stack: list[int] = []
    jump: dict[int, int] = {}
    for i, ch in enumerate(code):
        if ch == "[":
            stack.append(i)
        elif ch == "]":
            if not stack:
                return []
            start = stack.pop()
            jump[start] = i
            jump[i] = start
    if stack:
        return []
    tape = [0] * 30000
    pointer = 0
    output = []
    pc = 0
    steps = 0
    while pc < len(code) and steps < 2_000_000:
        ch = code[pc]
        if ch == ">":
            pointer = min(pointer + 1, len(tape) - 1)
        elif ch == "<":
            pointer = max(pointer - 1, 0)
        elif ch == "+":
            tape[pointer] = (tape[pointer] + 1) % 256
        elif ch == "-":
            tape[pointer] = (tape[pointer] - 1) % 256
        elif ch == ".":
            output.append(chr(tape[pointer]))
        elif ch == ",":
            tape[pointer] = 0
        elif ch == "[" and tape[pointer] == 0:
            pc = jump[pc]
        elif ch == "]" and tape[pointer] != 0:
            pc = jump[pc]
        pc += 1
        steps += 1
        if len(output) > 5000:
            break
    result = "".join(output)
    return [RawResult(result, "Brainfuck")] if result.strip() else []
