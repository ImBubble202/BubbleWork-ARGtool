"""解密质量评分：判断一段文本「像不像人类可读的明文」。

这是整个程序的判断核心。任何解密方法产出的结果都会经过这里打分，
分数越高越可能是正确明文。评分由三部分组成：

1. 字母频率拟合度（英文）/ 常用字比例（中文）
2. 双字母组合、三字母组合、常用词的「语言习惯」命中率
3. 文本结构分（可打印比例、乱码比例、是否仍然像一串编码）

最终得到一个 0~1 的可读性值，再结合文本长度折算成 0~100 的置信度。
"""

from __future__ import annotations

import math
import re
import string
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from .dictionary import (
    ENGLISH_BIGRAMS,
    ENGLISH_LETTER_FREQ,
    ENGLISH_STOPWORDS,
    ENGLISH_WORDS,
)
from . import ngram

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z']*")
PRINTABLE_ASCII = set(string.printable)

# 分词奖励：每切出一个「认识的词」加的分（抵消短词在逐字母归一化里的劣势）
WORD_INSERTION_BONUS = 2.0

FLAG_RE = re.compile(
    r"(flag|ctf|key|answer|pass(?:word)?)\s*[{(\[]([^)}\]]{1,120})[)}\]]",
    re.IGNORECASE,
)


@dataclass
class Score:
    """一段文本的评分结果。"""

    value: float          # 0~1 可读性
    confidence: float     # 0~100 置信度
    details: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# 基础统计
# --------------------------------------------------------------------------

def _basic_stats(text: str) -> dict:
    total = len(text)
    if total == 0:
        return {"total": 0}
    printable = sum(1 for ch in text if ch in PRINTABLE_ASCII or ord(ch) > 0x2000)
    control = sum(1 for ch in text if unicodedata.category(ch) == "Cc" and ch not in "\n\r\t")
    replacement = text.count("\ufffd")
    ascii_letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    digits = sum(1 for ch in text if ch.isdigit())
    spaces = sum(1 for ch in text if ch.isspace())
    cjk = len(CJK_RE.findall(text))
    others = total - ascii_letters - digits - spaces - cjk
    return {
        "total": total,
        "printable_ratio": printable / total,
        "control_count": control,
        "replacement_count": replacement,
        "ascii_letters": ascii_letters,
        "digits": digits,
        "spaces": spaces,
        "cjk": cjk,
        "others": max(others, 0),
        "letter_ratio": ascii_letters / total,
        "cjk_ratio": cjk / total,
    }


def _greedy_word_coverage(letters_lower: str, vocab: frozenset[str], min_len: int = 3,
                          max_len: int = 12) -> float:
    """用词表贪心覆盖字符串，返回被字典词覆盖的字母比例。

    对没有空格分隔的文本（例如去掉空格的句子）也能给出词汇合理性。
    """
    letters_lower = letters_lower[:600]      # 超长文本取前 600 个字母足够判断
    n = len(letters_lower)
    if n < min_len:
        return 0.0
    covered = 0
    i = 0
    while i < n:
        matched = 0
        for length in range(min(max_len, n - i), min_len - 1, -1):
            if letters_lower[i:i + length] in vocab:
                matched = length
                break
        if matched:
            covered += matched
            i += matched
        else:
            i += 1
    return covered / n


@lru_cache(maxsize=200_000)
def _segmentation_fit(letters_lower: str) -> float:
    """把没有空格的字母串切分成单词，按词频衡量「这串字母像不像英文句子」。

    做法是动态规划：在所有可能的切分中，找词频乘积最高的一种，
    再折算成每个字母的平均对数概率。乱码即使能硬拆成词，平均分也会很低。
    """
    if not ngram.available():
        return 0.0
    letters_lower = letters_lower[:400]      # 控制计算量，结论不受影响
    n = len(letters_lower)
    if n < 3:
        return 0.0
    neg_inf = float("-inf")
    best = [neg_inf] * (n + 1)
    best[0] = 0.0
    for i in range(1, n + 1):
        max_len = min(16, i)
        for length in range(1, max_len + 1):
            if length == 1 and letters_lower[i - 1] not in ("a", "i"):
                continue
            prev = best[i - length]
            if prev == neg_inf:
                continue
            word = letters_lower[i - length:i]
            # 每切出一个词给一点奖励（分词里的「词插入奖励」）：
            # 否则 let+go 这种短词组合会被「逐字母归一化」拖低，
            # 而一连串生造的长词反而更划算。
            value = prev + ngram.word_logprob_scored(word) + WORD_INSERTION_BONUS
            if value > best[i]:
                best[i] = value
    if best[n] == neg_inf:
        return 0.0
    per_letter = best[n] / n
    # 实测：真实英文约 -0.5 ~ -0.9/字母，乱码切分约 -2.0 以下
    return max(0.0, min(1.0, (per_letter + 2.2) / 1.6))


# --------------------------------------------------------------------------
# 英文评分
# --------------------------------------------------------------------------

def _english_freq_fit(letters_lower: str) -> float:
    n = len(letters_lower)
    if n < 2:
        return 0.0
    counts = Counter(letters_lower)
    chi = 0.0
    for ch, pct in ENGLISH_LETTER_FREQ.items():
        expected = pct / 100.0
        observed = counts.get(ch, 0) / n
        chi += (observed - expected) ** 2 / expected
    # 扣除抽样噪声：n 较小时卡方天然偏大
    chi = max(0.0, chi - 25.0 / n)
    return math.exp(-chi / 1.5)


def _english_bigram_fit(letters_lower: str) -> float:
    n = len(letters_lower)
    if n < 3:
        return 0.0
    total = 0.0
    pairs = 0
    for a, b in zip(letters_lower, letters_lower[1:]):
        total += ENGLISH_BIGRAMS.get(a + b, 0.0)
        pairs += 1
    if pairs == 0:
        return 0.0
    avg = total / pairs            # 每个双字母组合的平均权重
    # 经验标定（实测）：真实英文 ≈ 7~10，凯撒乱码 ≈ 2，随机字母 ≈ 0.1~0.9
    return max(0.0, min(1.0, (avg - 1.2) / 7.5))


_CORPUS_WORDS: frozenset[str] | None = None


def _corpus_words() -> frozenset[str]:
    """语料词表（来自 N-gram 模型文件），与内置词表一起用于覆盖度判断。"""
    global _CORPUS_WORDS
    if _CORPUS_WORDS is None:
        words: set[str] = set()
        if ngram.available():
            from .ngram import _WORDS  # 同包内部数据

            words = {w for w in _WORDS if len(w) >= 3}
        _CORPUS_WORDS = frozenset(words)
    return _CORPUS_WORDS


def _word_token_fit(tokens: list[str]) -> float:
    """按语料词频给每个单词打分：真词得高分，生造词得低分。"""
    if not tokens:
        return 0.0
    tokens = tokens[:200]
    total = 0.0
    for tok in tokens:
        if len(tok) == 1:
            total += 0.6 if tok in ("a", "i") else 0.15
            continue
        wlp = ngram.word_logprob(tok)
        # -7 以下视为未收录，-3.5 以上算高频词
        total += max(0.0, min(1.0, (wlp + 7.0) / 3.5))
    return total / len(tokens)


def _english_lexical(text: str, letters_lower: str) -> float:
    tokens = [t.lower() for t in WORD_RE.findall(text)]
    if not tokens:
        return 0.0
    stop = sum(1 for tok in tokens if tok in ENGLISH_STOPWORDS)
    vocab = ENGLISH_WORDS | _corpus_words()
    coverage = _greedy_word_coverage(letters_lower, vocab)
    segmentation = _segmentation_fit(letters_lower)
    if len(tokens) >= 2:
        token_fit = _word_token_fit(tokens)
        value = 0.70 * token_fit + 0.30 * max(coverage, segmentation)
    else:
        # 没有空格分隔（整段连写）时只能靠覆盖度
        value = max(segmentation, 0.5 * coverage)
    return max(0.0, min(1.0, value + min(0.08, 0.02 * stop)))


def _english_lm_fit(letters_lower: str) -> float:
    """基于 N-gram 语言模型的「像不像英文」评分。"""
    if not letters_lower or not ngram.available():
        return 0.0
    quad = ngram.quadgram_mean_logprob(letters_lower)
    if quad is None:
        return 0.0
    quad_fit = max(0.0, min(1.0, (quad + 8.3) / 3.9))
    if len(letters_lower) >= 14:
        return quad_fit
    tri = ngram.trigram_mean_logprob(letters_lower)
    if tri is None:
        return quad_fit
    tri_fit = max(0.0, min(1.0, (tri + 7.0) / 3.8))
    return 0.55 * quad_fit + 0.45 * tri_fit


_SYMBOL_OK = set(" \n\r\t.,!?;:'\"()-&%$*=[]{}")


def _symbol_penalty(text: str) -> float:
    """符号惩罚。

    正常英文里标点出现在词的边界（hello, world. / why?），
    如果标点被夹在字母数字中间（I,7NET / HINT(O / ?9INET），那是解码凑出来的垃圾，
    要重罚——这类结果以前会因为「包含字母和常用双字母组合」而骗到高分。
    """
    if not text:
        return 1.0
    weird = 0
    embedded = 0
    length = len(text)
    for index, ch in enumerate(text):
        if ch.isalnum() or ch in " \n\r\t" or CJK_RE.match(ch):
            continue
        previous = text[index - 1] if index > 0 else ""
        following = text[index + 1] if index + 1 < length else ""
        # 单词内部的撇号/连字符（don't、well-known）是正常的
        if ch in "'-" and previous.isalnum() and following.isalnum():
            continue
        if ch not in _SYMBOL_OK:
            weird += 1
            continue
        # 允许的标点，但后面紧跟字母数字 → 被塞进了词中间
        if following.isalnum():
            embedded += 1

    factor = 1.0
    ratio = weird / length
    if ratio > 0.01:
        factor *= max(0.35, 1.0 - ratio * 4.0)
    if embedded:
        factor *= max(0.1, 0.45 ** embedded)
    return factor


@lru_cache(maxsize=200_000)
def _repetition_penalty(text: str) -> float:
    """重复度惩罚。

    人写的句子不会「一段三字母组合重复二十遍」。而凯撒、异或、维吉尼亚这类
    暴力枚举出的错误结果，经常正是这种周期性乱码（sorsorsorsor / eadeadeadead），
    它们能骗过字母频率统计，所以必须单独识别。
    """
    letters = "".join(ch for ch in text.lower() if ch.isalnum())
    n = len(letters)
    if n >= 16:
        quads: dict[str, int] = {}
        for i in range(n - 3):
            gram = letters[i:i + 4]
            quads[gram] = quads.get(gram, 0) + 1
        if quads:
            top_count = max(quads.values())
            top_share = top_count / sum(quads.values())
            if top_count >= 3 and top_share > 0.15:
                return 0.3
            if top_count >= 2 and top_share > 0.28:
                return 0.5

    tokens = [t for t in re.split(r"\s+", text.strip()) if t]
    if len(tokens) >= 6:
        distinct = len({t.lower() for t in tokens}) / len(tokens)
        if distinct < 0.5:
            return 0.4
        if distinct < 0.65:
            return 0.7
    return 1.0


def _english_score(text: str, stats: dict) -> tuple[float, dict]:
    letters_lower = "".join(ch.lower() for ch in text if ch.isascii() and ch.isalpha())
    n = len(letters_lower)
    if n < 2:
        return 0.0, {"reason": "英文字母过少"}

    lm = _english_lm_fit(letters_lower)
    freq = _english_freq_fit(letters_lower)
    bigram = _english_bigram_fit(letters_lower)
    lexical = _english_lexical(text, letters_lower)

    value = 0.45 * lm + 0.45 * lexical + 0.06 * freq + 0.04 * bigram
    value *= _symbol_penalty(text)

    # 一堆「词」里没有一个真词 → 不是人话（维吉尼亚/凯撒暴力枚举的典型产物）
    tokens = WORD_RE.findall(text)
    if len(tokens) >= 3 and _word_token_fit([t.lower() for t in tokens]) < 0.25:
        value = min(value, 0.42)

    # 短且「查无此词」的整段字母（ASETC 这一类）证据太少，不能给高分：
    # 四五个字母碰巧凑出常见组合的概率很高，只有真词才配拿高分。
    if n <= 8 and " " not in text.strip():
        if not ngram.word_known(letters_lower) and _segmentation_fit(letters_lower) < 0.7:
            value = min(value, 0.6)

    # 结构惩罚：大量数字/符号、控制字符、无法识别字符
    letter_ratio = stats.get("letter_ratio", 0.0)
    if letter_ratio < 0.6:
        value *= 0.35 + 0.65 * (letter_ratio / 0.6)
    # 正常英文里数字很少；数字占比过高通常说明这串东西还没解干净
    digit_ratio = stats.get("digits", 0) / max(stats.get("total", 1), 1)
    if digit_ratio > 0.15:
        value *= max(0.25, 1.0 - (digit_ratio - 0.15) * 2.5)
    if stats.get("control_count"):
        value *= max(0.1, 1.0 - 0.35 * stats["control_count"])
    if stats.get("replacement_count"):
        value *= 0.25
    # 汉字一律视为乱码：中文破译已停用，混进汉字说明这是按错误编码解出来的结果
    cjk_ratio = stats.get("cjk_ratio", 0.0)
    if stats.get("cjk", 0) and cjk_ratio > 0.02:
        value *= max(0.05, 1.0 - cjk_ratio * 1.6)
    # 单个字母反复出现（aaaaa）不是语言
    if n >= 4:
        top_ratio = Counter(letters_lower).most_common(1)[0][1] / n
        if top_ratio > 0.4:
            value *= 0.4

    value = max(0.0, min(1.0, value))
    details = {
        "语言模型": round(lm, 3),
        "词汇合理性": round(lexical, 3),
        "字母频率": round(freq, 3),
        "组合习惯": round(bigram, 3),
        "字母占比": round(letter_ratio, 3),
    }
    return value, details


# --------------------------------------------------------------------------
# 中文评分
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# 结构判断：结果是否「仍然是一串编码」
# --------------------------------------------------------------------------

_HEXISH_RE = re.compile(r"^[0-9a-fA-F\s:,\-]+$")
_BASE64ISH_RE = re.compile(r"^[A-Za-z0-9+/\s]+={0,2}$")
_BINARY_RE = re.compile(r"^[01\s]+$")


@lru_cache(maxsize=400_000)
def looks_like_encoded(text: str) -> float:
    """返回 0~1，表示这段文本「看起来还是一种编码」的程度。

    用于两类场景：多层解码时决定要不要继续往下解；以及惩罚那些
    「解出来还是一串乱码/编码」的候选结果。
    """
    s = text.strip()
    if len(s) < 6:
        return 0.0
    compact = "".join(s.split())
    if not compact:
        return 0.0

    hints = 0.0
    letters = sum(1 for c in compact if c.isalpha())
    cjk = len(CJK_RE.findall(compact))
    has_inner_space = " " in s and " " in s.strip()

    if _BINARY_RE.match(compact) and len(compact) >= 8:
        hints = max(hints, 0.9)
    if _HEXISH_RE.match(compact) and len(compact) >= 8 and len(compact) % 2 == 0:
        hex_like = sum(1 for c in compact if c in "0123456789")
        if hex_like / len(compact) > 0.15:
            hints = max(hints, 0.85)
    # Base64 判定要严格：不能含空格、要有大小写混合、且不该包含成串的英文单词
    if (
        _BASE64ISH_RE.match(compact)
        and not has_inner_space
        and len(compact) >= 16
        and letters / len(compact) > 0.6
        and re.search(r"[A-Z]", compact)
        and re.search(r"[a-z]", compact)
        and (len(compact) % 4 == 0 or "=" in compact)
        and not re.search(r"[aeiou]{3}", compact.lower())
        and _greedy_word_coverage(compact.lower(), ENGLISH_WORDS) < 0.45
    ):
        hints = max(hints, 0.7)
    if re.fullmatch(r"[.\-·—–~_\s/|]{6,}", s):
        hints = max(hints, 0.95)
    if re.fullmatch(r"[ABab01\s]{10,}", s):
        hints = max(hints, 0.8)
    if re.fullmatch(r"[\d\s,;:\-\.]{6,}", s) and sum(c.isdigit() for c in s) >= 6:
        hints = max(hints, 0.6)
    if re.search(r"(%[0-9A-Fa-f]{2}){2,}", s):
        hints = max(hints, 0.9)
    if re.search(r"(&#x?[0-9A-Fa-f]+;){2,}", s):
        hints = max(hints, 0.9)
    if re.search(r"(\\u[0-9a-fA-F]{4}){2,}", s):
        hints = max(hints, 0.9)
    if any(ch in text for ch in "\u200b\u200c\u200d\u2060\ufeff"):
        hints = max(hints, 0.95)
    if cjk == 0 and letters >= 8 and not re.search(r"[aeiouAEIOU]", compact):
        hints = max(hints, 0.7)
    return hints


def find_flag(text: str) -> str | None:
    m = FLAG_RE.search(text)
    if not m:
        return None
    return m.group(2)


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------

def _length_factor(units: int) -> float:
    """文本越短，结论越不可靠。"""
    if units <= 0:
        return 0.0
    if units >= 24:
        return 1.0
    return 0.35 + 0.65 * (units / 24.0) ** 0.6


def _confidence_from_value(value: float) -> float:
    """把 0~1 的可读性折算成 0~100 的置信度。

    用 logistic 曲线而不是线性映射：低于 0.55 的结果快速掉到低分区，
    0.7 附近开始进入「较可信」，0.9 以上才会显示为高置信。
    """
    center, steepness = 0.70, 8.0
    return 100.0 / (1.0 + math.exp(-steepness * (value - center)))


@lru_cache(maxsize=400_000)
def score_text(text: str) -> Score:
    """对一段文本打分（只按英文/拉丁字母的可读性评估）。

    中文破译已按要求停用：结果里出现汉字时不再走中文评分，
    而是当作「不像人话」处理（在英文评分里扣分），避免 GBK 误码
    解出来的假中文拿到高分。
    """
    if not text or not text.strip():
        return Score(0.0, 0.0, {"reason": "空文本"})

    stats = _basic_stats(text)
    value, details = _english_score(text, stats)

    # 如果结果本身仍然像一串编码，说明「还没解干净」，降权
    encoded_like = looks_like_encoded(text)
    if encoded_like >= 0.8:            # 二进制 / 十六进制 / 摩斯 / 零宽字符等铁证
        value *= max(0.15, 1.0 - encoded_like * 0.85)
        details["仍像编码"] = round(encoded_like, 2)
    elif encoded_like >= 0.6:          # 疑似编码，轻度降权
        value *= 0.7
        details["仍像编码"] = round(encoded_like, 2)

    # 周期性重复的文本不是人话（暴力枚举最容易产出这种结果）
    repeat_factor = _repetition_penalty(text)
    if repeat_factor < 1.0:
        value *= repeat_factor
        details["重复度惩罚"] = round(repeat_factor, 2)

    units = stats.get("ascii_letters", 0)
    # 汉字一律视为乱码：整段几乎没有字母时，长度折算按 1 计，直接沉底
    details["有效长度"] = units
    confidence = _confidence_from_value(value) * _length_factor(units)
    return Score(value, confidence, details)


def confidence_band(confidence: float) -> str:
    """把置信度翻译成人话。"""
    if confidence >= 70:
        return "很可能正确"
    if confidence >= 45:
        return "较可能正确"
    if confidence >= 25:
        return "存疑，需人工判断"
    if confidence >= 10:
        return "可能性较低"
    return "基本可以排除"
