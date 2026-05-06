"""主题管理器 — 支持浅色/深色模式动态切换。

集成 qfluentwidgets 主题框架，监听系统主题变化。

使用方式：
    from strange_uta_game.frontend.theme import theme, ThemeMode

    # 获取颜色
    bg_color = theme.bg_primary
    text_color = theme.text_primary

    # 切换主题
    theme.mode = ThemeMode.DARK
    theme.mode = ThemeMode.AUTO  # 跟随系统
"""

from __future__ import annotations

import sys
from enum import Enum, auto
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QWidget


class ThemeMode(Enum):
    """主题模式"""
    LIGHT = auto()
    DARK = auto()
    AUTO = auto()  # 跟随系统


class ThemeColors:
    """颜色方案定义"""

    def __init__(self, is_dark: bool):
        self._is_dark = is_dark

    @property
    def is_dark(self) -> bool:
        return self._is_dark

    # ── 背景色 ──

    @property
    def bg_primary(self) -> QColor:
        """主背景色"""
        return QColor("#1E1E1E" if self._is_dark else "#FFFFFF")

    @property
    def bg_secondary(self) -> QColor:
        """次级背景色（面板、卡片）"""
        return QColor("#252526" if self._is_dark else "#F0F0F0")

    @property
    def bg_tertiary(self) -> QColor:
        """三级背景色（输入框、下拉框）"""
        return QColor("#2D2D2D" if self._is_dark else "#FFFFFF")

    @property
    def bg_hover(self) -> QColor:
        """悬停背景色"""
        return QColor("#3E3E3E" if self._is_dark else "#E5E5E5")

    @property
    def bg_selected(self) -> QColor:
        """选中背景色"""
        return QColor("#094771" if self._is_dark else "#CCE4F7")

    # ── 文字色 ──

    @property
    def text_primary(self) -> QColor:
        """主文字色"""
        return QColor("#CCCCCC" if self._is_dark else "#333333")

    @property
    def text_secondary(self) -> QColor:
        """次级文字色"""
        return QColor("#888888")

    @property
    def text_hint(self) -> QColor:
        """提示文字色"""
        return QColor("#666666" if self._is_dark else "#999999")

    @property
    def text_disabled(self) -> QColor:
        """禁用文字色"""
        return QColor("#555555" if self._is_dark else "#BBBBBB")

    # ── 边框色 ──

    @property
    def border_primary(self) -> QColor:
        """主边框色"""
        return QColor("#3E3E3E" if self._is_dark else "#DDDDDD")

    @property
    def border_secondary(self) -> QColor:
        """次级边框色"""
        return QColor("#2D2D2D" if self._is_dark else "#EEEEEE")

    # ── 强调色 ──

    @property
    def accent_primary(self) -> QColor:
        """主强调色（播放头、进度条）"""
        return QColor("#4ECDC4")

    @property
    def accent_secondary(self) -> QColor:
        """次级强调色"""
        return QColor("#5B9BD5")

    @property
    def accent_warning(self) -> QColor:
        """警告色（时间标签、高亮）"""
        return QColor("#FF6B6B")

    # ── 卡拉OK专用色 ──

    @property
    def karaoke_bg(self) -> QColor:
        """卡拉OK预览背景"""
        return QColor("#1E1E1E" if self._is_dark else "#FFFFFF")

    @property
    def karaoke_text_past(self) -> QColor:
        """已唱文字（当前行之前）"""
        return QColor("#666666" if self._is_dark else "#AAAAAA")

    @property
    def karaoke_text_future(self) -> QColor:
        """未唱文字（当前行之后）"""
        return QColor("#888888" if self._is_dark else "#666666")

    @property
    def karaoke_text_current(self) -> QColor:
        """当前行文字"""
        return QColor("#FFFFFF" if self._is_dark else "#000000")

    @property
    def karaoke_highlight_bg(self) -> QColor:
        """当前字符高亮背景"""
        return QColor("#3E3E3E" if self._is_dark else "#FFE0E0")

    @property
    def karaoke_selection_bg(self) -> QColor:
        """划词选中背景"""
        return QColor("#264F78" if self._is_dark else "#BDE0FE")

    # ── 波形专用色 ──

    @property
    def waveform_bg(self) -> QColor:
        """波形背景"""
        return QColor("#1E1E1E" if self._is_dark else "#F0F0F0")

    @property
    def waveform_fill(self) -> QColor:
        """波形填充色"""
        return QColor("#3A7CA5" if self._is_dark else "#9DC8E8")

    @property
    def waveform_line(self) -> QColor:
        """波形中心线"""
        return QColor("#4A9EC5" if self._is_dark else "#6BA8D4")

    # ── 行号专用色 ──

    @property
    def line_number_current(self) -> QColor:
        """当前行号颜色"""
        return QColor("#FF6B6B")

    @property
    def line_number_normal(self) -> QColor:
        """普通行号颜色"""
        return QColor("#666666" if self._is_dark else "#AAAAAA")

    # ── 进度状态色 ──

    @property
    def status_complete(self) -> QColor:
        """完成状态（绿色）"""
        return QColor("#2ecc71")

    @property
    def status_partial(self) -> QColor:
        """部分完成状态（橙色）"""
        return QColor("#f39c12")

    @property
    def status_none(self) -> QColor:
        """未完成状态（灰色）"""
        return QColor("#666666" if self._is_dark else "#999999")

    # ── 默认高亮色（无演唱者时使用）──

    @property
    def default_highlight(self) -> QColor:
        """默认高亮色（红色）"""
        return QColor("#FF6B6B")

    @property
    def default_highlight_complement(self) -> QColor:
        """默认高亮补色"""
        return QColor("#6BFFB6")

    # ── 颜色调整辅助 ──

    def ensure_contrast(self, color: QColor, bg: Optional[QColor] = None) -> QColor:
        """确保颜色在背景上有足够对比度。"""
        if bg is None:
            bg = self.bg_primary

        def relative_luminance(c: QColor) -> float:
            r, g, b = c.redF(), c.greenF(), c.blueF()
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        l1 = relative_luminance(color)
        l2 = relative_luminance(bg)
        contrast = (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)

        if contrast >= 4.5:
            return color

        h, s, v, a = color.getHsvF()
        if relative_luminance(bg) > 0.5:
            v = max(0.0, v * 0.7)
        else:
            v = min(1.0, v * 1.3 + 0.2)

        return QColor.fromHsvF(h, s, v, a)

    # ── 样式表辅助 ──

    def get_hint_style(self) -> str:
        """获取提示文字的样式表"""
        color = self.text_hint.name()
        return f"color: {color};"

    def get_secondary_style(self) -> str:
        """获取次级文字的样式表"""
        color = self.text_secondary.name()
        return f"color: {color};"

    def get_caption_style(self) -> str:
        """获取说明文字的样式表"""
        color = self.text_secondary.name()
        return f"font-size: 13px; color: {color};"


class Theme(QObject):
    """主题管理器单例

    集成 qfluentwidgets 主题框架，支持运行时动态切换。
    监听系统主题变化，自动更新颜色方案。
    """

    changed = pyqtSignal()  # 主题变化信号

    _instance: Optional[Theme] = None

    def __new__(cls) -> Theme:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        super().__init__()
        self._initialized = True

        self._mode: ThemeMode = ThemeMode.AUTO
        self._colors: Optional[ThemeColors] = None
        self._system_is_dark: bool = False
        self._listeners: List[Callable] = []

        # 检测初始系统主题
        self._detect_system_theme()

        # 监听系统主题变化
        self._setup_system_theme_listener()

    def _detect_system_theme(self) -> None:
        """检测系统主题"""
        if sys.platform == "win32":
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
                )
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                winreg.CloseKey(key)
                self._system_is_dark = (value == 0)
            except Exception:
                self._system_is_dark = False
        else:
            app = QApplication.instance()
            if app:
                palette = app.palette()
                bg = palette.color(QPalette.ColorRole.Window)
                self._system_is_dark = (bg.lightness() < 128)
            else:
                self._system_is_dark = False

    def _setup_system_theme_listener(self) -> None:
        """设置系统主题变化监听器"""
        app = QApplication.instance()
        if app:
            # Qt 6.5+ 支持 colorSchemeChanged 信号
            try:
                app.styleHints().colorSchemeChanged.connect(self._on_system_theme_changed)
            except AttributeError:
                # 旧版本 Qt 不支持，使用定时器轮询
                pass

    def _on_system_theme_changed(self, scheme: Qt.ColorScheme) -> None:
        """系统主题变化回调

        只有在 AUTO 模式下才响应系统主题变化，
        手动设置 LIGHT/DARK 时忽略系统变化。
        """
        if self._mode != ThemeMode.AUTO:
            return

        old_dark = self._system_is_dark
        self._system_is_dark = (scheme == Qt.ColorScheme.Dark)

        if old_dark != self._system_is_dark:
            self._invalidate()
            self._apply_qfluentwidgets_theme()
            self._refresh_all_widgets()
            self.changed.emit()

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @mode.setter
    def mode(self, value: ThemeMode) -> None:
        if self._mode == value:
            return
        self._mode = value
        self._invalidate()
        self._apply_qfluentwidgets_theme()
        self._refresh_all_widgets()
        self.changed.emit()

    @property
    def is_dark(self) -> bool:
        """当前是否为深色模式"""
        if self._mode == ThemeMode.AUTO:
            return self._system_is_dark
        return self._mode == ThemeMode.DARK

    @property
    def colors(self) -> ThemeColors:
        """获取当前颜色方案"""
        if self._colors is None:
            self._colors = ThemeColors(self.is_dark)
        return self._colors

    def _invalidate(self) -> None:
        """清除颜色缓存"""
        self._colors = None

    def _apply_qfluentwidgets_theme(self) -> None:
        """同步应用 qfluentwidgets 主题"""
        try:
            from qfluentwidgets import setTheme, Theme as QfwTheme
            if self.is_dark:
                setTheme(QfwTheme.DARK, lazy=True)
            else:
                setTheme(QfwTheme.LIGHT, lazy=True)
        except Exception:
            pass

    def _refresh_all_widgets(self) -> None:
        """强制刷新所有控件的样式"""
        app = QApplication.instance()
        if app:
            # 先处理待处理的事件
            app.processEvents()
            # 遍历所有顶层窗口，强制更新样式
            for widget in app.topLevelWidgets():
                self._update_widget_style(widget)
            # 再次应用 qfluentwidgets 主题，确保内部样式（如菜单）也被刷新
            self._apply_qfluentwidgets_theme()
            app.processEvents()

    def _update_widget_style(self, widget) -> None:
        """递归更新控件及其子控件的样式"""
        # 强制重新应用样式表
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()
        # 递归处理子控件
        for child in widget.findChildren(QWidget):
            child.style().unpolish(child)
            child.style().polish(child)
            child.update()

    def on_change(self, callback: Callable) -> None:
        """注册主题变化回调"""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def off_change(self, callback: Callable) -> None:
        """取消主题变化回调"""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def refresh(self) -> None:
        """刷新主题（重新检测系统主题）"""
        self._detect_system_theme()
        self._invalidate()
        self._apply_qfluentwidgets_theme()
        self._refresh_all_widgets()
        self.changed.emit()

    def __getattr__(self, name: str):
        """代理 ThemeColors 的属性访问"""
        if name.startswith('_') or name in ('changed', 'mode', 'is_dark', 'colors',
                                              'on_change', 'off_change', 'refresh',
                                              '_invalidate', '_apply_qfluentwidgets_theme',
                                              '_detect_system_theme', '_setup_system_theme_listener',
                                              '_on_system_theme_changed'):
            raise AttributeError(name)
        return getattr(self.colors, name)


# 全局单例
theme = Theme()
