"""fugashi 日语注音引擎可用性 UI 引导。

本分支不再使用 WinRT IME，改用 fugashi（MeCab）作为注音引擎。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QMessageBox,
    QWidget,
)


def _fugashi_available() -> bool:
    """检查 fugashi + unidic_lite 是否已安装（跨平台回退）。"""
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401
    except ImportError:
        return False
    return True


def ensure_winrt_japanese(parent: Optional[QWidget] = None) -> bool:
    """确保日语注音引擎可用。

    本分支不再使用 WinRT IME，改用 fugashi（MeCab）作为注音引擎。
    返回 True 表示 fugashi 可用（注音功能正常），False 表示无可用引擎。
    """
    if _fugashi_available():
        return True

    QMessageBox.critical(
        parent,
        "缺少注音组件",
        "未找到 fugashi 注音引擎（fugashi + unidic-lite）。\n"
        "请运行: pip install fugashi unidic-lite",
    )
    return False
