"""坐席流程：案件在工作台中的状态流转（#8）。

API 是这一层之上的薄封装；以后的 #10、#11 调用同样的函数。
"""

from copilot.workbench.service import ConflictError, generate_draft

__all__ = ["ConflictError", "generate_draft"]
