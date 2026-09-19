"""英文 N-gram 语言模型：判断一串字母「像不像真正的英文」。

模型文件由 tools/build_ngram_model.py 生成，存放在 decipherer/data/。
如果数据文件缺失，本模块会自动降级（返回 None），评分退回纯频率统计，
程序依然可用，只是排序精度会下降。
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

_QUAD: dict[str, float] = {}
_TRI: dict[str, float] = {}
_WORDS: dict[str, float] = {}
_LOADED = False
_QUAD_FLOOR = -8.0
_TRI_FLOOR = -6.0
_WORD_FLOOR = -9.0

LETTERS_RE = re.compile(r"[^a-z]")


def _read_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not path.exists():
        return counts
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 2:
                continue
            try:
                counts[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return counts


def _load() -> None:
    global _LOADED, _QUAD_FLOOR, _TRI_FLOOR, _WORD_FLOOR
    if _LOADED:
        return
    _LOADED = True

    quad_counts = _read_counts(DATA_DIR / "english_4grams.txt")
    if quad_counts:
        total = sum(quad_counts.values())
        _QUAD.update({gram: math.log10(count / total) for gram, count in quad_counts.items()})
        # 没见过的组合给一个很低的概率（比最低频的已知组合再低一档）
        _QUAD_FLOOR = math.log10(0.05 / total)

    tri_counts = _read_counts(DATA_DIR / "english_3grams.txt")
    if tri_counts:
        total = sum(tri_counts.values())
        _TRI.update({gram: math.log10(count / total) for gram, count in tri_counts.items()})
        _TRI_FLOOR = math.log10(0.05 / total)

    word_counts = _read_counts(DATA_DIR / "english_words.txt")
    if word_counts:
        from .dictionary import CODE_WORDS

        # 过滤掉只在代码/标识符里偶尔出现的稀有词，避免把乱码「切」成单词；
        # 同时排除编程专用词（int / der / param 这类），它们会让碎切垃圾显得像英文
        total = sum(word_counts.values())
        _WORDS.update({
            word: math.log10(count / total)
            for word, count in word_counts.items()
            if count >= 25 and word not in CODE_WORDS
        })
        _WORD_FLOOR = math.log10(0.01 / total)


def available() -> bool:
    """语言模型是否可用。"""
    _load()
    return bool(_QUAD)


def _letters(text: str) -> str:
    # 超长文本只取前 4000 个字母，统计结果已经足够稳定
    return LETTERS_RE.sub("", text.lower())[:4000]


@lru_cache(maxsize=200_000)
def quadgram_mean_logprob(text: str) -> float | None:
    """平均每个四字母组合的对数概率（越大越像英文）。"""
    _load()
    if not _QUAD:
        return None
    stream = _letters(text)
    if len(stream) < 4:
        return None
    total = 0.0
    count = 0
    for i in range(len(stream) - 3):
        total += _QUAD.get(stream[i:i + 4], _QUAD_FLOOR)
        count += 1
    return total / count if count else None


@lru_cache(maxsize=200_000)
def trigram_mean_logprob(text: str) -> float | None:
    """平均每个三字母组合的对数概率，适合很短的文本。"""
    _load()
    if not _TRI:
        return None
    stream = _letters(text)
    if len(stream) < 3:
        return None
    total = 0.0
    count = 0
    for i in range(len(stream) - 2):
        total += _TRI.get(stream[i:i + 3], _TRI_FLOOR)
        count += 1
    return total / count if count else None


@lru_cache(maxsize=100_000)
def word_logprob(word: str) -> float:
    """单词的对数频率，未收录的词返回惩罚值。"""
    _load()
    word = word.lower().strip("'")
    if not word:
        return _WORD_FLOOR
    return _WORDS.get(word, _WORD_FLOOR)


@lru_cache(maxsize=200_000)
def word_logprob_scored(word: str) -> float:
    """给「切词打分」用的词对数概率：未收录的词按长度递增惩罚。

    否则动态规划会倾向于把乱码整段当成一个超长生僻词，反而比正确切分更划算。
    """
    _load()
    known = _WORDS.get(word)
    if known is not None:
        return known
    return _WORD_FLOOR - 1.2 * max(0, len(word) - 2)


def word_known(word: str) -> bool:
    _load()
    return word.lower() in _WORDS


def model_info() -> dict:
    """模型概况，供界面与说明文档展示。"""
    _load()
    return {
        "四字母组合": len(_QUAD),
        "三字母组合": len(_TRI),
        "词表": len(_WORDS),
        "数据目录": str(DATA_DIR),
    }
