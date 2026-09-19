"""手法常见度（先验权重）。

置信度回答的是「这段明文像不像人话」，但它不知道「这种加密手法在 ARG/解谜里有多常见」。
两者缺一不可：把一段摩斯电码解出完整英文（常见度 100）和把一段乱码
用 Brainfuck 解释出几个单词（常见度 40），显然前者更值得排在前面。

排序规则（默认「常见度优先」）是**两级排序**：

    第一优先级：常见度（摩斯 100 > 凯撒/Base64 95 > …… > JWT 25）
    第二优先级：置信度（同一个常见度内部，谁解得通顺谁排前面）

所以摩斯解出来的结果永远排在「冷门手法碰巧拼出的句子」前面，
哪怕后者置信度更高；只有同属摩斯的结果之间才比置信度。

唯一例外是「噪音门槛」：置信度低于 max(5%, 最高置信度 × 10%) 的结果算噪音，
统一沉到列表底部（否则一段 0.3% 的乱码会因为手法常见而挂在最上面）。

另外两种模式供对比：
    平衡       = 置信度 × 常见度权重（0.7~1.3），两者折中
    置信度优先 = 完全按语言模型打分，不看手法冷热

多层解密链的常见度取链条上**最冷门**的那一环（木桶原理）。
数值是主观经验值，依据是 ARG / 解谜 / CTF 社区里各类题目的出现频率，
想按自己的口味调整，直接改这张表即可。
"""

from __future__ import annotations

DEFAULT_POPULARITY = 50

POPULARITY: dict[str, int] = {
    # ---- 经典密码：ARG 最常见的手法 ----
    "摩斯电码（Morse）": 100,        # 几乎每部 ARG 都会出现
    "凯撒移位（暴力枚举 1-25）": 95,
    "ROT13 / ROT5 / ROT18": 95,
    "A1Z26 数字字母": 85,            # 1=A 2=B 这类数字字母，入门题常客
    "Atbash 反字母表": 80,
    "栅栏密码（Z 栅栏）": 75,
    "维吉尼亚密码": 70,
    "整段倒序 / 单词倒序": 65,
    "培根密码（Bacon）": 65,
    "手机九宫格多按（T9 Multi-tap）": 65,
    "ROT47": 60,
    "波利比奥斯方阵（Polybius）": 60,
    "分栏读取密码": 55,
    "敲击码（Tap Code）": 55,
    "键盘错位（QWERTY 左右手滑）": 50,
    "仿射密码（暴力枚举）": 45,
    "列换位密码": 45,
    "字母表分组替换": 35,

    # ---- 编码类：数字题里的绝对主力 ----
    "Base64 解码": 95,
    "十六进制解码": 85,
    "二进制转文本": 80,
    "十进制 ASCII 码": 70,
    "URL 百分号解码": 65,
    "HTML 实体解码": 60,
    "Unicode / 转义序列解码": 60,
    "Base32 解码": 55,
    "八进制解码": 45,
    "十进制补零字节": 40,
    "Base85 / ASCII85 解码": 30,
    "Base58 解码": 30,
    "Quoted-Printable 解码": 25,

    # ---- 隐写：现代 ARG 里越来越常见 ----
    "零宽字符隐写": 70,
    "藏头 / 首字母提取": 65,
    "空格 / 制表符二进制": 45,
    "大小写二进制": 45,
    "盲文点字（Unicode Braille）": 45,

    # ---- 字符外观还原（属于编码类） ----
    "花体 / 全角字符还原": 50,

    # ---- 以下中文手法当前已停用，数值保留以便日后恢复 ----
    "中文乱码修复（UTF-8 ↔ GBK）": 45,
    "GB2312 区位码": 40,
    "中文数字转阿拉伯数字": 35,

    # ---- 现代 / 技术向：多出现在 CTF，普通 ARG 较少 ----
    "Leet / 火星文还原": 55,
    "单字节异或（XOR 暴力枚举）": 50,
    "多字节异或（已知密钥）": 45,
    "Brainfuck 解释执行": 40,
    "zlib / Deflate 解压": 35,
    "gzip 解压": 30,
    "JWT 解析": 25,
}

RANK_MODE_LABELS = {
    "common": "常见度优先",
    "balanced": "平衡",
    "confidence": "可读性优先",
}


def popularity_of(name: str) -> int:
    return POPULARITY.get(name, DEFAULT_POPULARITY)


def prior_weight(popularity: int) -> float:
    """把 0~100 的常见度折算成 0.7~1.3 的权重。"""
    return 0.7 + max(0, min(100, popularity)) / 100.0 * 0.6


def rank_score(confidence: float, popularity: int, mode: str = "common") -> float:
    """列表里显示的「排序分」：

    常见度优先 → 整数部分是常见度、小数部分是置信度/100（数值大小顺序 = 排序顺序）
    平衡       → 置信度 × 常见度权重
    置信度优先 → 就是置信度本身
    """
    if mode == "confidence":
        return confidence
    if mode == "balanced":
        return confidence * prior_weight(popularity)
    return popularity + confidence / 100.0


def sort_key(confidence: float, popularity: int, mode: str, plausible: bool) -> tuple:
    """真正的排序键（越大越靠前）。plausible=False 的一律沉底。"""
    if mode == "confidence":
        return (plausible, confidence)
    if mode == "balanced":
        return (plausible, confidence * prior_weight(popularity), confidence)
    # 默认：常见度优先，其次置信度
    return (plausible, popularity, confidence)


def noise_floor(max_confidence: float) -> float:
    """噪音门槛：低于它的结果不参与正常排名，统一沉到列表底部。"""
    return max(5.0, max_confidence * 0.3)
