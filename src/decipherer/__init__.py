"""UniversalDecipherer —— ARG / 解谜场景的通用文字解密与分析工具。

用法：
    from decipherer import analyze
    result = analyze("aGVsbG8gd29ybGQ=")
    for cand in result.top(5):
        print(f"{cand.confidence:5.1f}%  {cand.method}: {cand.text}")
"""

from .core import Candidate, Options, AnalysisResult
from .engine import analyze

__all__ = ["analyze", "Candidate", "Options", "AnalysisResult", "__version__"]
__version__ = "1.0.0"
