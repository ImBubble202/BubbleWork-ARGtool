"""分析引擎：自动识别 → 逐层解密 → 打分排序。"""

from __future__ import annotations

import time

from .core import AnalysisResult, Candidate, Options, RawResult, all_decoders
from .priority import (
    DEFAULT_POPULARITY,
    noise_floor,
    popularity_of,
    rank_score,
    sort_key,
)
from .scoring import looks_like_encoded, score_text

FULLWIDTH_MAP = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ－—–　",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz--- ",
)


def normalize_input(text: str) -> str:
    """把常见的「输入污染」统一掉：全角字符、各种破折号、不间断空格等。"""
    cleaned = text.translate(FULLWIDTH_MAP)
    cleaned = cleaned.replace("\u00a0", " ").replace("\u3000", " ")
    return cleaned


# 前瞻时只试这些「结构化编码」解码器：它们的格式判定很快、也很可靠，
# 没必要让几百个候选把全部 47 种方法都跑一遍。
_LOOKAHEAD_NAMES = frozenset({
    "Base64 解码", "Base32 解码", "十六进制解码", "二进制转文本",
    "摩斯电码（Morse）", "零宽字符隐写", "URL 百分号解码", "HTML 实体解码",
    "Unicode / 转义序列解码", "GB2312 区位码", "盲文点字（Unicode Braille）",
    "八进制解码", "十进制 ASCII 码", "Base85 / ASCII85 解码", "Base58 解码",
})


def _score_candidate(text: str) -> tuple[float, float, dict]:
    sc = score_text(text)
    # 注意：score_text 带缓存，返回的 details 是共享对象，必须复制一份再改写
    return sc.value, sc.confidence, dict(sc.details)


def identify_input(text: str, options: Options) -> list[tuple[str, float]]:
    """粗略判断输入「最像哪一种编码」，给用户提示。"""
    hints: list[tuple[str, float]] = []
    for spec in all_decoders():
        if not options.allows(spec.category) or not options.allows_name(spec.name):
            continue
        try:
            score = float(spec.detect(text))
        except Exception:
            score = 0.0
        if score >= 0.3:
            hints.append((spec.name, round(min(score, 1.0), 2)))
    hints.sort(key=lambda item: item[1], reverse=True)
    return hints[:12]


def _make_candidate(raw: RawResult, spec, options: Options, chain: tuple[str, ...],
                    depth: int, evaluations: list[int],
                    match_factor: float = 1.0,
                    chain_match: float = 0.0,
                    popularity: int = DEFAULT_POPULARITY,
                    root: str = "") -> Candidate | None:
    value, confidence, details = _score_candidate(raw.text)
    evaluations[0] += 1

    # 方法匹配度 + 链长度修正：直解永远比「碰巧凑出来」的多步链更可信
    confidence *= match_factor * (0.96 ** (depth - 1))
    details["_匹配度"] = round(match_factor, 3)
    details["_最低匹配"] = round(chain_match, 3)

    notes = [raw.note] if raw.note else []
    # 命中 flag{...} / key{...} 这类结构，几乎可以确定是最终答案
    from .scoring import find_flag

    flag = find_flag(raw.text)
    if flag:
        confidence = max(confidence, 88.0)
        value = max(value, 0.8)
        notes.append(f"检测到答案格式：{flag}")
    # 结果仍像编码 → 明确提示「可能还要再解一层」
    still_encoded = looks_like_encoded(raw.text)
    if still_encoded >= 0.6:
        notes.append("结果仍像是一段编码，可在多层模式下继续解")

    return Candidate(
        text=raw.text,
        method=spec.name,
        root=root or spec.name,
        category=spec.category,
        chain=chain,
        confidence=round(confidence, 1),
        value=value,
        popularity=popularity,
        rank=round(rank_score(confidence, popularity, options.rank_mode), 1),
        depth=depth,
        note="；".join(notes),
        details=details,
    )


def _match_factor(detect_score: float) -> float:
    """把「这个方法有多符合输入格式」折算成 0.75~1.0 的置信度系数。"""
    return 0.75 + 0.25 * max(0.0, min(1.0, detect_score))


def _dedupe(candidates: list[Candidate], per_text: int = 4, max_results: int = 600,
            mode: str = "common", floor: float | None = None) -> list[Candidate]:
    """排序 + 同一段明文最多保留几条，然后截断。

    排序规则由 mode 决定。默认「常见度优先」是两级排序：
        第一优先级 = 手法常见度（摩斯 100 永远排在冷门手法前面）
        第二优先级 = 置信度（同一种手法内部才比谁解得更通顺）
    置信度低于噪音门槛的结果会被判定为「不像人话」，统一沉到列表末尾。
    """
    if floor is None:
        best = max((c.confidence for c in candidates), default=0.0)
        floor = noise_floor(best)
    for cand in candidates:
        cand.rank = round(rank_score(cand.confidence, cand.popularity, mode), 2)
    candidates.sort(
        key=lambda c: (
            sort_key(c.confidence, c.popularity, mode, c.confidence >= floor),
            c.value,
            -c.depth,
        ),
        reverse=True,
    )
    seen_text: dict[str, int] = {}
    seen_pair: set[tuple[str, str]] = set()
    output: list[Candidate] = []
    for cand in candidates:
        norm = " ".join(cand.text.split()).lower()
        pair = (norm, cand.chain_text)
        if pair in seen_pair:
            continue
        if seen_text.get(norm, 0) >= per_text:
            continue
        seen_pair.add(pair)
        seen_text[norm] = seen_text.get(norm, 0) + 1
        output.append(cand)
        if len(output) >= max_results:
            break
    return output


def _looks_like_text(value: str) -> bool:
    """廉价过滤：解码结果是不是「像文字」，不值得打分的直接跳过。"""
    if not value or len(value) < 2:
        return False
    good = sum(1 for ch in value if ch.isprintable() or ch in "\n\r\t")
    if good / len(value) < 0.95:
        return False
    letters = sum(1 for ch in value if ch.isascii() and ch.isalpha())
    cjk = sum(1 for ch in value if "\u4e00" <= ch <= "\u9fff")
    return (letters + cjk) / len(value) >= 0.4


def _lookahead_potential(cand: Candidate, decoders: list, options: Options,
                         cache: dict[str, float] | None = None) -> float:
    """给中间结果做一次「前瞻」：再往下解一步，看能不能通向通顺的明文。

    例如 Base64 解出一个 ROT13 串，它自己完全不像人话，
    但只要再解一层就是漂亮英文，就应该排到前面继续往下解。
    """
    cache = cache if cache is not None else {}
    if cand.text in cache:
        return cache[cand.text]
    if looks_like_encoded(cand.text) < 0.35:
        cache[cand.text] = cand.value
        return cand.value
    best = cand.value
    matched = []
    for spec in decoders:
        if spec.cost > 2 or (spec.needs_bruteforce and not options.enable_bruteforce):
            continue
        try:
            score = float(spec.detect(cand.text))
        except Exception:
            continue
        if score >= 0.6:
            matched.append((score, spec))
    # 只试「格式最匹配」的一两个解密器，先做廉价的「像不像文字」判断，
    # 通过了才真正打分——这样前瞻几乎不增加额外开销
    matched.sort(key=lambda item: item[0], reverse=True)
    for _score, spec in matched[:2]:
        # 无分隔符的摩斯切分很贵，前瞻里只处理带分隔符的摩斯串
        if spec.name.startswith("摩斯") and " " not in cand.text and "/" not in cand.text:
            continue
        for raw in spec.run(cand.text, options):
            if _looks_like_text(raw.text):
                best = max(best, score_text(raw.text).value)
    cache[cand.text] = best
    return best


def _select_frontier(candidates: list[Candidate], beam: int,
                     decoders: list | None = None,
                     options: Options | None = None) -> list[Candidate]:
    """挑出「值得再往下解一层」的中间结果。

    关键点：不能只看置信度。例如 Base64 解出来的是 ROT13 密文，
    它本身完全不像人话（置信度可能只有 1%），但它恰恰是正确链条的下一环。
    所以只要「方法匹配度」很高的解密器（Base64、十六进制、摩斯等）成功解出了东西，
    就优先把它放进下一层继续解。
    """
    expandable = lambda c: looks_like_encoded(c.text) >= 0.35 or c.value < 0.58  # noqa: E731
    shallowest = min((c.depth for c in candidates), default=1)
    layer = [c for c in candidates if c.depth == shallowest]

    by_method: dict[str, list[Candidate]] = {}
    potential_cache: dict[str, float] = {}

    def rank_key(cand: Candidate) -> tuple:
        key = (cand.text.strip(), cand.chain_text)
        text_key = cand.text.strip()
        if text_key not in potential_cache:
            value = cand.value
            if decoders is not None and options is not None:
                value = _lookahead_potential(cand, decoders, options, potential_cache)
            potential_cache[text_key] = value
        # 排序优先级：链条可信 → 前瞻潜力（再解一层能否通顺）→ 格式匹配度 → 置信度
        return (cand.details.get("_最低匹配", 0.0) >= 0.55,
                potential_cache[text_key],
                cand.details.get("_最低匹配", 0.0),
                cand.confidence)

    ranked_layer = sorted(layer, key=rank_key, reverse=True)
    for cand in ranked_layer:
        if cand.details.get("_最低匹配", 0.0) >= 0.55 and expandable(cand):
            bucket = by_method.setdefault(cand.method, [])
            if len(bucket) < 3:
                bucket.append(cand)
    strong = [cand for bucket in by_method.values() for cand in bucket]

    ranked = [c for c in _dedupe(layer, per_text=1, max_results=beam * 4) if expandable(c)]

    frontier: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    for cand in strong[: max(4, int(beam * 0.6))] + ranked:
        key = (cand.text.strip(), cand.chain_text)
        if key in seen:
            continue
        seen.add(key)
        frontier.append(cand)
        if len(frontier) >= beam:
            break
    return frontier


def analyze(text: str, options: Options | None = None) -> AnalysisResult:
    """对输入文本做完整分析，返回按置信度排序的候选明文。"""
    options = options or Options()
    started = time.time()
    notes: list[str] = []
    if not text or not text.strip():
        return AnalysisResult(source=text, notes=["输入为空"])

    original = text
    variants: list[tuple[str, str]] = [("", text)]
    cleaned = normalize_input(text)
    if cleaned != text:
        variants.append(("全角/破折号已归一", cleaned))
        notes.append("检测到全角字符或特殊破折号，已生成归一化副本参与分析")

    hints = identify_input(text, options)
    detect_cache: dict[str, float] = dict(hints)
    decoders = [d for d in all_decoders()
                if options.allows(d.category) and options.allows_name(d.name)]
    lookahead_decoders = [d for d in decoders if d.name in _LOOKAHEAD_NAMES]

    # 超长输入按比例收窄搜索宽度，避免多层解码时算太久
    beam = options.beam
    if len(text) > 1500:
        beam = max(8, beam // 3)
    if len(text) > 6000:
        beam = max(6, beam // 6)

    evaluations = [0]
    all_candidates: list[Candidate] = []

    # ---------- 第一层：所有解密器作用在原始输入上 ----------
    for prefix, variant in variants:
        for spec in decoders:
            if spec.needs_bruteforce and not options.enable_bruteforce:
                continue
            for raw in spec.run(variant, options):
                chain = (f"{spec.name}（{raw.label}）" if raw.label else spec.name,)
                if prefix:
                    chain = (prefix,) + chain
                if spec.name not in detect_cache:
                    try:
                        detect_cache[spec.name] = float(spec.detect(variant))
                    except Exception:
                        detect_cache[spec.name] = 0.0
                cand = _make_candidate(
                    raw, spec, options, chain, 1, evaluations,
                    match_factor=_match_factor(detect_cache.get(spec.name, 0.0)),
                    chain_match=detect_cache.get(spec.name, 0.0),
                    popularity=spec.popularity or DEFAULT_POPULARITY,
                )
                if cand:
                    all_candidates.append(cand)
            if evaluations[0] > options.max_evaluations:
                notes.append("已达到评分次数上限，结果可能不完整（可减少暴力破解范围）")
                break
        if evaluations[0] > options.max_evaluations:
            break

    # ---------- 后续层：只对「看起来还没解干净」的高分结果继续解 ----------
    if options.max_depth > 1:
        frontier = _select_frontier(all_candidates, beam, lookahead_decoders, options)

        for depth in range(2, options.max_depth + 1):
            if not frontier or evaluations[0] > options.max_evaluations:
                break
            next_frontier: list[Candidate] = []
            for base in frontier:
                base_match = float(base.details.get("_匹配度", 1.0))
                base_chain_match = float(base.details.get("_最低匹配", 0.0))
                # 链条常见度取最冷门的一环（木桶原理）
                base_popularity = base.popularity
                for prefix, variant in [("", base.text)] + (
                    [("归一化", normalize_input(base.text))] if normalize_input(base.text) != base.text else []
                ):
                    for spec in decoders:
                        if spec.needs_bruteforce and not options.enable_bruteforce:
                            continue
                        if spec.cost > 2 and depth >= 3:
                            continue
                        if spec.name in base.chain[-1]:
                            continue          # 不重复套用同一个方法
                        # 第二层开始只保留「输入格式对得上」的解密器，
                        # 否则会在无关的中间结果上乱套方法，既慢又刷出噪声
                        threshold = 0.3 if depth == 2 else 0.55
                        try:
                            spec_detect = float(spec.detect(variant))
                            if spec_detect < threshold:
                                continue
                        except Exception:
                            continue
                        for raw in spec.run(variant, options):
                            label = f"{spec.name}（{raw.label}）" if raw.label else spec.name
                            chain = base.chain + ((prefix + label,) if prefix else (label,))
                            cand = _make_candidate(
                                raw, spec, options, chain, depth, evaluations,
                                match_factor=base_match * _match_factor(spec_detect),
                                chain_match=min(base_chain_match, spec_detect),
                                popularity=min(base_popularity, spec.popularity or DEFAULT_POPULARITY),
                                root=base.root,          # 还是「从哪种手法开始解」
                            )
                            if cand:
                                all_candidates.append(cand)
                                next_frontier.append(cand)
                        if evaluations[0] > options.max_evaluations:
                            break
                    if evaluations[0] > options.max_evaluations:
                        break
            # 注意：这里不能先按置信度截断，否则「解出来还是编码」的正确中间结果
            # （本身不像人话、分数很低）会被丢掉
            frontier = _select_frontier(next_frontier, max(6, beam // 2), lookahead_decoders, options)

    ranked = _dedupe(all_candidates, per_text=4, max_results=options.max_results,
                     mode=options.rank_mode)

    if ranked:
        best = ranked[0]
        notes.append(
            f"最佳结果来自「{best.chain_text}」（手法常见度 {best.popularity}/100）"
        )
        if best.confidence < 25:
            notes.append("结果普遍较短或不够通顺（短句本来就会这样）："
                         "如果不符合预期，可能是自创编码、缺少密钥，或需要补充线索")
    else:
        notes.append("没有产出任何候选结果，请检查输入格式或换用「密钥」输入框")

    return AnalysisResult(
        source=original,
        candidates=ranked,
        input_hints=hints,
        notes=notes,
        evaluated=evaluations[0],
        elapsed=time.time() - started,
    )
