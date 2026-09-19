"""所有解密方法的集合。导入本包会把各个解密器注册进全局注册表。

注意：中文破译（GB2312 区位码、中文乱码修复、中文数字）已按要求停用，
源码保留在 chinese.py 里但没有导入。需要恢复时：
    1. 打开本文件，取消下面那行 chinese 的注释；
    2. 在 _util.py 的 bytes_candidates 里把 "gbk"、"big5" 加回编码列表；
    3. 参考 README「已停用功能」一节恢复评分与界面选项。
"""

from . import classical, encodings, modern, stego, text_style  # noqa: F401
# from . import chinese  # noqa: F401  ← 取消注释即可恢复中文破译

__all__ = ["classical", "encodings", "modern", "stego", "text_style"]
