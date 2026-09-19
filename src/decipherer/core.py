"""核心数据结构：解密器注册表、候选结果、分析结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

from .priority import DEFAULT_POPULARITY


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------

@dataclass
class Options:
    """一次分析所用的参数。"""

    max_depth: int = 1              # 最多套用几层解密（默认只解一层，需要套娃时再调大）
    beam: int = 24                  # 每层保留多少个中间结果继续往下解
    keys: list[str] = field(default_factory=list)   # 用户猜测的密钥
    max_results: int = 600          # 排行榜最多保留多少条
    enable_bruteforce: bool = True  # 是否启用暴力枚举类（凯撒/仿射/栅栏等）
    enabled_categories: set[str] | None = None
    only_names: set[str] | None = None   # 只跑点名的那几个方法（「常规模式」用）
    max_evaluations: int = 120_000  # 评分次数上限，防止卡死
    rank_mode: str = "common"       # common（常见度优先）/ balanced / confidence

    def allows(self, category: str) -> bool:
        return self.enabled_categories is None or category in self.enabled_categories

    def allows_name(self, name: str) -> bool:
        return self.only_names is None or name in self.only_names


# --------------------------------------------------------------------------
# 解密器
# --------------------------------------------------------------------------

@dataclass
class RawResult:
    """某个解密器产出的单条结果。"""

    text: str
    label: str = ""      # 方法细节，例如 "移位 13"
    note: str = ""       # 给用户的提示


@dataclass
class DecoderSpec:
    """一个解密方法。"""

    name: str
    category: str
    func: Callable[[str, Options], Iterable[RawResult]]
    description: str = ""
    detect: Callable[[str], float] = lambda _t: 0.0   # 输入匹配度 0~1
    cost: int = 1        # 1 轻量 / 2 中等 / 3 昂贵（多层搜索时会收敛）
    needs_bruteforce: bool = False
    popularity: int | None = None    # 手法常见度 0~100，见 priority.py

    def run(self, text: str, options: Options) -> Iterator[RawResult]:
        try:
            for item in self.func(text, options):
                if item is not None and isinstance(item.text, str) and item.text.strip():
                    yield item
        except Exception:      # 单个解密器出错不应影响整体分析
            return


REGISTRY: list[DecoderSpec] = []


def register(name: str, category: str, description: str = "", cost: int = 1,
             needs_bruteforce: bool = False, popularity: int | None = None,
             detect: Callable[[str], float] | None = None):
    """把一个解密函数注册进全局注册表。"""

    def decorator(func: Callable[[str, Options], Iterable[RawResult]]):
        REGISTRY.append(
            DecoderSpec(
                name=name,
                category=category,
                func=func,
                description=description or func.__doc__ or "",
                cost=cost,
                needs_bruteforce=needs_bruteforce,
                popularity=popularity,
                detect=detect or (lambda _t: 0.0),
            )
        )
        return func

    return decorator


def all_decoders() -> list[DecoderSpec]:
    """返回当前已注册的全部解密器。"""
    _ensure_loaded()
    return list(REGISTRY)


_LOADED = False


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    # 中文破译（chinese）已停用，故意不在这里导入
    from .ciphers import classical, encodings, modern, stego, text_style  # noqa: F401
    from .priority import popularity_of

    # 没有在 register() 里显式指定常见度的，统一从 priority.py 的表格里取
    for spec in REGISTRY:
        if spec.popularity is None:
            spec.popularity = popularity_of(spec.name)

    _LOADED = True


# --------------------------------------------------------------------------
# 候选结果
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    """一条候选明文。"""

    text: str
    method: str
    root: str = ""               # 这条链最外层的那个手法（多层链时用来看「从哪种手法开始解」）
    category: str = ""
    chain: tuple[str, ...] = ()
    confidence: float = 0.0
    value: float = 0.0
    popularity: int = DEFAULT_POPULARITY
    rank: float = 0.0            # 综合排序分 = 置信度 × 常见度权重
    depth: int = 1
    note: str = ""
    details: dict = field(default_factory=dict)

    @property
    def chain_text(self) -> str:
        return " → ".join(self.chain) if self.chain else self.method

    def preview(self, width: int = 90) -> str:
        flat = " ".join(self.text.split())
        return flat if len(flat) <= width else flat[: width - 1] + "…"

    def key(self) -> tuple:
        return (self.text.strip(), self.chain_text)


@dataclass
class AnalysisResult:
    """一次完整分析的结果。"""

    source: str
    candidates: list[Candidate] = field(default_factory=list)
    input_hints: list[tuple[str, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evaluated: int = 0
    elapsed: float = 0.0

    def top(self, n: int = 5) -> list[Candidate]:
        return self.candidates[:n]

    def by_category(self) -> dict[str, list[Candidate]]:
        grouped: dict[str, list[Candidate]] = {}
        for cand in self.candidates:
            grouped.setdefault(cand.category or "其他", []).append(cand)
        return grouped

    def distinct_methods(self) -> int:
        return len({c.chain_text for c in self.candidates})
