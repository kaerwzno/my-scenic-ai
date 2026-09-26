"""Runtime hook: 阻止 tenacity 导入 tornado（Fay 不需要 tornado）。
在 tenacity.__init__ 执行前，将 tornado 注册为不可导入，
使 tenacity 走到 `except ImportError: tornado = None` 分支。
"""
import sys
sys.modules["tornado"] = None
