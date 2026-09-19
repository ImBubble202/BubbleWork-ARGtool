"""内置语言模型数据：字母/双字母/常用词/常用汉字频率。

全部离线内置于源码中，不依赖任何第三方语料库或网络下载。
数据来源为公开的通用统计结果（英文书信、报刊语料与汉字使用频度表）的近似值，
用于给「解密结果像不像人类语言」打分，不追求学术精确。
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# 英文
# --------------------------------------------------------------------------

# 英文字母出现频率（百分比），来源：通用英文语料统计
ENGLISH_LETTER_FREQ: dict[str, float] = {
    "a": 8.167, "b": 1.492, "c": 2.782, "d": 4.253, "e": 12.702,
    "f": 2.228, "g": 2.015, "h": 6.094, "i": 6.966, "j": 0.153,
    "k": 0.772, "l": 4.025, "m": 2.406, "n": 6.749, "o": 7.507,
    "p": 1.929, "q": 0.095, "r": 5.987, "s": 6.327, "t": 9.056,
    "u": 2.758, "v": 0.978, "w": 2.360, "x": 0.150, "y": 1.974,
    "z": 0.074,
}

# 常见英文双字母组合，值为每 1000 个字母中的出现次数（近似）
ENGLISH_BIGRAMS: dict[str, float] = {
    "th": 35.6, "he": 30.7, "in": 24.3, "er": 20.5, "an": 19.9, "re": 18.5,
    "on": 17.6, "at": 14.9, "en": 14.5, "nd": 13.5, "ti": 13.4, "es": 13.4,
    "or": 12.8, "te": 12.0, "of": 11.7, "ed": 11.7, "is": 11.3, "it": 11.2,
    "al": 10.9, "ar": 10.7, "st": 10.5, "to": 10.4, "nt": 10.4, "ng": 9.5,
    "se": 9.3, "ha": 9.3, "as": 8.7, "ou": 8.7, "io": 8.3, "le": 8.3,
    "ve": 8.3, "co": 7.9, "me": 7.9, "de": 7.6, "hi": 7.6, "ri": 7.3,
    "ro": 7.3, "ic": 7.0, "ne": 6.9, "ea": 6.9, "ra": 6.9, "ce": 6.5,
    "li": 6.2, "ch": 6.0, "ll": 5.8, "be": 5.8, "ma": 5.7, "si": 5.5,
    "om": 5.5, "ur": 5.4, "ca": 5.3, "el": 5.3, "ta": 5.3, "la": 5.3,
    "ns": 5.1, "di": 5.0, "fo": 4.9, "ho": 4.6, "pe": 4.6, "ec": 4.5,
    "pr": 4.5, "no": 4.4, "ct": 4.4, "us": 4.3, "ac": 4.3, "ot": 4.2,
    "il": 4.2, "tr": 4.2, "ly": 4.2, "nc": 4.1, "et": 4.1, "ut": 4.0,
    "ss": 4.0, "so": 4.0, "rs": 3.9, "un": 3.9, "lo": 3.9, "wa": 3.9,
    "ge": 3.8, "ie": 3.8, "wh": 3.8, "ee": 3.8, "wi": 3.7, "em": 3.7,
    "ad": 3.6, "ol": 3.6, "rt": 3.6, "po": 3.6, "we": 3.5, "na": 3.5,
    "ul": 3.5, "ni": 3.5, "ts": 3.4, "mo": 3.4, "ow": 3.4, "pa": 3.3,
    "im": 3.3, "mi": 3.3, "ai": 3.2, "sh": 3.2, "ir": 3.2, "su": 3.1,
    "id": 3.1, "os": 3.1, "iv": 3.1, "ia": 3.1, "am": 3.0, "fi": 3.0,
    "ci": 3.0, "vi": 3.0, "ju": 3.0, "fe": 2.9, "ay": 2.9, "ag": 2.9,
    "bo": 2.8, "bu": 2.8, "ty": 2.8, "qu": 2.8, "rd": 2.8, "dd": 2.7,
    "tu": 2.7, "ck": 2.7, "oo": 2.7, "da": 2.6, "ry": 2.6, "wo": 2.6,
    "eg": 2.5, "ht": 2.5, "ga": 2.5, "ev": 2.5, "sp": 2.5, "sl": 2.4,
    "ki": 2.4, "pe": 2.4, "ob": 2.3, "sw": 2.3, "ot": 2.3, "gn": 2.3,
    "iz": 2.2, "if": 2.2, "ee": 2.2, "ai": 2.2, "ph": 2.2, "od": 2.2,
    "ep": 2.2, "iz": 2.1, "gl": 2.1, "lu": 2.1, "bl": 2.1, "tw": 2.0,
}

# 常见英文单词（含 ARG / 解谜场景高频词）
ENGLISH_WORDS: frozenset[str] = frozenset(
    """
    the be to of and a in that have i it for not on with he as you do at this but
    his by from they we say her she or an will my one all would there their what
    so up out if about who get which go me when make can like time no just him
    know take people into year your good some could them see other than then now
    look only come its over think also back after use two how our work first well
    way even new want because any these give day most us is are was were been has
    had did does doing done am being very much too many more such same own old
    long little own keep put end set turn hand part place case point group number
    world house area money story fact month lot right study book eye job word
    business issue side kind head far black white light dark night day morning
    evening water fire earth air tree door room home city street road name life
    death love hate fear hope dream memory secret hidden message answer question
    key lock open close find found lost search seek follow listen watch wait
    silence voice whisper sound noise signal broadcast station radio static
    pattern code cipher puzzle riddle clue escape truth danger safe trust lie
    friend enemy stranger ghost shadow mirror glass blood wound heart mind soul
    body bone skin face eye hand foot step walk run stop move stay leave return
    remember forget before after always never again once twice here there where
    why how what when who whom whose this that these those mine yours ours theirs
    help please thank sorry hello goodbye welcome goodbye morning welcome flag
    hello world test project subject experiment patient sample facility sector
    level access denied granted protocol system control agent subject report
    file data record log journal note letter mail phone call record tape video
    camera photo image picture frame color sound music song verse chorus chapter
    page line word letter sentence paragraph story tale legend myth truth
    alive dead live die kill save protect guard hide reveal show tell speak talk
    quiet loud slow fast cold warm hot wet dry clean dirty bright dim heavy soft
    strong weak young new fresh broken whole empty full half double single
    north south east west left right up down back front inside outside near
    above below between under over around through across along behind ahead
    one two three four five six seven eight nine ten eleven twelve twenty thirty
    hundred thousand million zero first second third last next final beginning
    begin started finished complete continue pause break wait hold release
    check verify confirm deny accept refuse listen read write speak think feel
    believe doubt know understand learn teach study train practice repeat
    return continue proceed stop halt resume abort cancel exit enter leave
    awake asleep alive dead awake dream sleep wake rise fall stand sit kneel
    throw catch hold drop lift push pull break fix build make create destroy
    send receive deliver arrive depart travel journey path route course map
    compass guide lead follow track trace trail mark sign symbol token marker
    we you they them us me him her it its our your their his hers theirs
    and or but if then else when while until since because although though
    yes no maybe perhaps surely never always sometimes often rarely soon later
    today tomorrow yesterday now then soon already still yet again
    """.split()
)

# 极高频英文单词，命中时额外加权
ENGLISH_STOPWORDS: frozenset[str] = frozenset(
    "the of and to in a is that it for on with as was at by be this from or "
    "have an not are but they you all we can her has his one will there their "
    "what so out if about who get which go me when make time no".split()
)

# 常见英文三字母组合（用于识别「看起来像不像英文」）
ENGLISH_TRIGRAMS: frozenset[str] = frozenset(
    """
    the and ing her tha nth int tio ere ent ion ter est ers ati hat ate all
    eth hes ver his oft ith fth sth oth res ong col oul our itt tth hin whi
    ich you are not but was his one out wit for tha the wor wha hat ion eve
    ery thi ing nce men our ave owt uld tly ous ent ith ugh bei
    """.split()
)

# 编程/技术词黑名单：这些词在英文技术文档里出现得极频繁，
# 但正常英文句子里几乎不会出现。如果不排除，像 "int"、"der"、"param"
# 这样的词会把暴力枚举产生的碎切垃圾撑成「看起来有词」的假英文。
CODE_WORDS: frozenset[str] = frozenset(
    """
    int ints str strs bool bools def len self cls args arg kwargs kwarg
    py py2 py3 pyc pyo pip pypi venv repo docstring docstrings
    utf utf8 ascii ucs latin cp1252
    api apis json jsonl xml html css js jsx ts tsx sql uri uris http https
    tcp udp tls ssl dns dnssec ip ips mac
    cfg config configs param params paramiko func funcs callable callables
    iter iterable iterables enum enums dict dicts tuple tuples obj objs
    init argv stdout stderr stdin regex regexes
    gz bz2 xz tar zip ttl utf8 ctrl ctrlc
    os sys io fd fds gc pid pids uuid uuids
    ns namespace namespaces attr attrs attrs kwargs
    pre post intra inter multi sub super proto
    tsx utf asyncio aio async await coroutine coroutines
    unicode utf codec codecs encode encodes decode decodes
    """
    .split()
)

# --------------------------------------------------------------------------
# 中文相关数据（常用汉字 / 中文词组）已按要求移除。
# 现在的评分只针对英文：解密结果里出现汉字会被当作乱码扣分。
# 如需恢复中文破译，请参考 README 的「已停用功能」一节。
# --------------------------------------------------------------------------
