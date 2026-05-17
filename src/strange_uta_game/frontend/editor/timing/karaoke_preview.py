"""卡拉OK 歌词预览控件。

- 实时走字高亮 / Ruby 注音同步 / 连词平滑渲染
- 支持鼠标拖拽划词选区 + 右键菜单
- 渲染状态与 ``EditorInterface`` 通过信号耦合
"""

from __future__ import annotations

import sys
from typing import Optional

from PyQt6.QtCore import QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import Action, RoundMenu

from strange_uta_game.backend.domain import Character, Project, Ruby
from strange_uta_game.frontend.theme import theme


# ──────────────────────────────────────────────
# 卡拉OK 歌词预览
# ──────────────────────────────────────────────


def _anchor_ratio(anchors: list[int], current_time: int) -> float:
    """根据锚点序列计算总 wipe 进度比例。

    anchors 形如 ``[ts_0, ts_1, ..., ts_N]``，共 N+1 个时间戳，
    把演唱划成 N 个 part 段。第 i 段区间为 ``[ts_i, ts_{i+1})``。

    返回值 = ``(i + segment_ratio) / N``，其中 i 为 current_time 落入的段索引，
    segment_ratio 为段内线性比例。clamp 到 [0.0, 1.0]。

    - ``current_time < anchors[0]`` → 0.0
    - ``current_time >= anchors[-1]`` → 1.0
    - 段时长 <= 0（异常数据）→ 该段视为瞬时完成
    - 锚点数 < 2 → 1.0（无法分段，按已完成处理；调用方负责回退）
    """
    n = len(anchors) - 1
    if n <= 0:
        return 1.0
    if current_time < anchors[0]:
        return 0.0
    if current_time >= anchors[-1]:
        return 1.0
    # 线性扫描即可（part 数极少，N <= 10 量级）
    for i in range(n):
        seg_start = anchors[i]
        seg_end = anchors[i + 1]
        if current_time < seg_end:
            seg_dur = seg_end - seg_start
            seg_ratio = (current_time - seg_start) / seg_dur if seg_dur > 0 else 1.0
            seg_ratio = max(0.0, min(1.0, seg_ratio))
            return (i + seg_ratio) / n
    return 1.0


def _anchor_segment(anchors: list[int], current_time: int) -> tuple[int, float, int]:
    """与 _anchor_ratio 同义，但返回 (段索引 i, 段内比例 seg_ratio, 段数 N)。

    - 已完成（>=anchors[-1]）→ (N-1, 1.0, N)
    - 未开始（<anchors[0]）→ (0, 0.0, N)
    - anchors 不足 2 元素 → (0, 1.0, 0)
    """
    n = len(anchors) - 1
    if n <= 0:
        return (0, 1.0, 0)
    if current_time < anchors[0]:
        return (0, 0.0, n)
    if current_time >= anchors[-1]:
        return (n - 1, 1.0, n)
    for i in range(n):
        seg_start = anchors[i]
        seg_end = anchors[i + 1]
        if current_time < seg_end:
            seg_dur = seg_end - seg_start
            seg_ratio = (current_time - seg_start) / seg_dur if seg_dur > 0 else 1.0
            seg_ratio = max(0.0, min(1.0, seg_ratio))
            return (i, seg_ratio, n)
    return (n - 1, 1.0, n)


def _ink_bounds(fm: QFontMetrics, text: str) -> tuple[int, int]:
    """返回 ``text`` 在给定字体度量下的墨水边界：``(ink_left, ink_width)``。

    - ``ink_left``：墨水最左像素相对于 ``drawText(x, y, text)`` 中 ``x`` 的偏移
      （即 ``tightBoundingRect`` 的 ``x()`` 字段）。
    - ``ink_width``：墨水实际占用的水平像素宽度（即 ``tightBoundingRect.width()``）。

    Qt 的 ``tightBoundingRect`` 返回紧贴墨水的最小包围盒：
    - x 可能为负（字形墨水越过 origin 左侧，例如斜体 ``f``）；
    - width 一般 ≤ ``horizontalAdvance(text)``，但极少数斜体场景可能略大；
    - 对空白字符（U+0020 / U+3000 / NBSP / Tab）通常返回零宽 → 此处保留为 (0, 0)，
      由调用方决定回退策略（一般直接跳过 wipe 绘制）。

    返回为 int；底层 Qt API 已是 int。
    """
    if not text:
        return (0, 0)
    rect = fm.tightBoundingRect(text)
    return (int(rect.x()), int(rect.width()))


class KaraokePreview(QWidget):
    """多行歌词预览，带逐字高亮、注音显示和滚动支持。

    滚动模型：_scroll_center_line 表示视口中央对应的行索引（浮点数）。
    - 自动跟随：打轴推进时自动居中当前行
    - 手动滚动：鼠标滚轮浏览，点击某行后重新居中
    - 首行居中：_scroll_center_line=0 时首行在正中央，上方留空
    """

    line_clicked = pyqtSignal(int)
    checkpoint_clicked = pyqtSignal(int, int, int)  # line_idx, char_idx, checkpoint_idx
    char_edit_requested = pyqtSignal(int, int)  # line_idx, char_idx (F2 key)
    seek_to_char_requested = pyqtSignal(int, int)  # line_idx, char_idx (click)
    seek_to_checkpoint_requested = pyqtSignal(int, int ,int)  # line_idx, char_idx, checkpoint_idx (double-click)
    char_selected = pyqtSignal(int, int)  # line_idx, char_idx
    singer_change_requested = pyqtSignal(
        int, int, int, str
    )  # line_idx, start_char, end_char, singer_id
    delete_chars_requested = pyqtSignal(int, int, int)
    delete_timestamp_requested = pyqtSignal(int, int)
    insert_space_before_requested = pyqtSignal(int, int)
    insert_space_after_requested = pyqtSignal(int, int)
    merge_line_up_requested = pyqtSignal(int)
    delete_line_requested = pyqtSignal(int)
    insert_blank_line_before_requested = pyqtSignal(int)
    insert_blank_line_requested = pyqtSignal(int)
    add_checkpoint_requested = pyqtSignal(int, int)
    remove_checkpoint_requested = pyqtSignal(int, int)
    toggle_sentence_end_requested = pyqtSignal(int, int)
    auto_scroll_line_changed = pyqtSignal()
    user_interaction_during_auto_scroll = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._current_line_idx = 0
        self._current_char_idx = 0
        self._current_checkpoint_idx: Optional[int] = None
        self._current_time_ms = 0
        self._global_offset_ms = 0
        self._duration_ms = 0  # 音频总时长（用于行尾非句尾时的wipe右边界）
        self._visible_lines = 7  # 视口内可见行数（决定行高）
        self._scroll_center_line: float = 0.0  # 视口中央对应的行索引
        self._checkpoint_hitboxes: list = []  # [(QRect, line_idx, char_idx, cp_idx)]
        self._char_hitboxes: list = []  # [(QRect, line_idx, char_idx)]
        self.setMinimumHeight(400)
        self.setMouseTracking(True)

        # 音频引擎引用（用于 paintEvent 主动拉取高精度时间）
        self._audio_engine = None

        # 划词选中状态
        self._focus_line_idx: int = -1
        self._focus_char_idx: int = -1
        self._focus_char_range_end: int = -1
        self._focus_dragging: bool = False

        self._disable_click_jump: bool = False  # 禁用单击跳转

        # 单击/双击处理：快照机制
        # 单击时锁定 hitbox 快照，双击时使用快照判断，避免居中导致 hitbox 变化
        self._pending_click: Optional[dict] = None  # 按下时记录的待处理点击
        self._click_snapshot: Optional[dict] = None  # 单击后保存的快照（用于双击判断）
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(300)  # 双击间隔
        self._click_timer.timeout.connect(self._clear_click_snapshot)
        self._press_pos: Optional[tuple] = None  # 鼠标按下位置 (x, y)
        self._CLICK_MOVE_THRESHOLD = 5  # 像素级防抖阈值：移动超过此距离视为划词

        # 缓存字体和 QFontMetrics，避免每帧重建
        self._font_current = QFont("Microsoft YaHei", 22, QFont.Weight.Bold)
        self._font_context = QFont("Microsoft YaHei", 18)
        self._font_ruby = QFont("Microsoft YaHei", 10)
        self._font_checkpoint = QFont("Microsoft YaHei", 8)
        self._font_line_number = QFont("Microsoft YaHei", 10)
        self._fm_current = QFontMetrics(self._font_current)
        self._fm_context = QFontMetrics(self._font_context)
        self._fm_ruby = QFontMetrics(self._font_ruby)
        self._fm_checkpoint = QFontMetrics(self._font_checkpoint)
        self._fm_line_number = QFontMetrics(self._font_line_number)
        self._line_number_margin = 45  # 行号左侧区域宽度
        self._ruby_spacing = 4  # Ruby与主文字的垂直间距

        # 歌词对齐方式："left" / "center" / "right"
        self._alignment: str = "center"
        self._alignment_margin: int = 168  # 左/右对齐时的页边距(px)

        # Checkpoint 标记字符（可配置）
        self._checkpoint_markers: dict[str, str] = {
            "cp_first_timed": "▶",
            "cp_first_empty": "▷",
            "cp_multi_timed": "▮",
            "cp_multi_empty": "▯",
            "cp_sentence_end_timed": "⬟",
            "cp_sentence_end_empty": "⬠",
        }

        # 逐句渲染数据缓存（避免每帧重复计算）
        # 每行有自己的版本号，只有数据改变的行才重新计算
        self._sentence_cache: dict = {}
        self._line_versions: dict = {}  # line_idx -> version
        self._global_version: int = 0  # 全局版本号，用于字体变化等全局刷新
        self._is_playing: bool = False
        self._auto_scroll_enabled: bool = True  # 自动滚动开关，特殊场景可关闭
        self._auto_scroll_suspended: bool = False  # 用户交互后挂起自动滚动
        self._last_auto_scroll_line_idx: int = -1  # 上次自动滚动到的行（与 _current_line_idx 独立）
        self._line_switch_points: list[tuple[int, int]] = []  # [(switch_ms, line_idx)]
        self._current_switch_idx: int = 0  # 当前快照位置

        # 监听主题变化，触发重绘
        theme.changed.connect(self.update)

    def set_playing(self, playing: bool):
        """由外部同步播放状态，用于决定 paintEvent 是否旁路缓存。"""
        self._is_playing = bool(playing)
        if playing:
            self._build_line_switch_points()

    def _build_line_switch_points(self):
        """构建换行时间点快照，记录每个时间点应切换到哪一行。"""
        self._line_switch_points = []
        self._current_switch_idx = 0
        if not self._project or not self._project.sentences:
            return
        for idx, sentence in enumerate(self._project.sentences):
            ts = sentence.global_timing_start_ms
            if ts is not None:
                self._line_switch_points.append((ts, idx))
        self._line_switch_points.sort()

    def set_disable_click_jump(self, disable: bool):
        """设置是否禁用单击跳转功能。"""
        self._disable_click_jump = bool(disable)

    def set_auto_scroll_enabled(self, enabled: bool):
        """设置是否启用自动滚动功能。特殊场景可关闭。"""
        self._auto_scroll_enabled = bool(enabled)

    def _suspend_auto_scroll(self):
        """用户交互时挂起自动滚动，通知外部停止 cooldown timer。"""
        self._auto_scroll_suspended = True
        self.user_interaction_during_auto_scroll.emit()

    def resume_auto_scroll(self):
        """恢复自动滚动：将视口同步到当前播放行（不改变编辑光标）。"""
        self._auto_scroll_suspended = False
        self._scroll_to_line(self._last_auto_scroll_line_idx)

    def set_duration(self, duration_ms: int):
        """设置音频总时长（用于行尾非句尾时的wipe右边界）"""
        self._duration_ms = duration_ms

    def set_project(self, project: Project):
        self._project = project
        self._scroll_center_line = 0.0
        self._sentence_cache.clear()
        # focus 域默认值：首个非空行的首字符。空项目保持 -1。
        # focus 是用户视觉/操作真理来源（点字符/拖选/纯←→），与 current（cp 域反馈）独立。
        self._focus_line_idx = -1
        self._focus_char_idx = -1
        self._focus_char_range_end = -1
        self._focus_dragging = False
        if project and project.sentences:
            for idx, sentence in enumerate(project.sentences):
                if sentence.characters:
                    self._focus_line_idx = idx
                    self._focus_char_idx = 0
                    self._focus_char_range_end = 0
                    break
            # 预渲染所有句子到缓存
            self._prewarm_all_sentences()
        self._update_display()

    def set_focus_position(self, line_idx:int = 0,char_idx: int = 0):
        # 用于打轴状态下更新foucs
        new_line = float(line_idx)
        if new_line == self._scroll_center_line and line_idx == self._current_char_idx:
            self._focus_char_idx = char_idx
            self._focus_char_range_end = char_idx
            self._update_display()
            return
        self._focus_line_idx = line_idx
        self._focus_char_idx = char_idx
        self._focus_char_range_end = char_idx
        if self._is_playing:
            self._warm_nearby_cache(budget=2)
        self._update_display()

    def set_current_position(self, line_idx: int, char_idx: int = 0):
        """更新当前打轴位置（行+字符），并居中滚动。"""
        if line_idx == self._current_line_idx:
            self._current_char_idx = char_idx
            self._update_display()
            return
        self._current_line_idx = line_idx
        self._current_char_idx = char_idx
        self.scroll_current_line_to_center()
        # 行切换时重新锚定预热中心（仅播放期间）
        if self._is_playing:
            self._warm_nearby_cache(budget=2)
        self._update_display()

    def scroll_current_line_to_center(self):
        """将当前行滚动到视口中央。

        幂等保护：若目标 line_idx 已是当前 scroll_center_line，
        避免短暂跳变导致空白行。
        """
        new_line = float(self._current_line_idx)
        if new_line == self._scroll_center_line:
            return
        self._scroll_center_line = new_line
        self._update_display()

    def _scroll_to_line(self, line_idx: int):
        """纯视觉滚动：将指定行移到视口中央，不改变 _current_line_idx（编辑光标）。

        用于自动滚动，与编辑光标完全解耦。
        """
        if line_idx < 0:
            return
        new_line = float(line_idx)
        if new_line == self._scroll_center_line:
            return
        self._scroll_center_line = new_line
        self._update_display()

    def _find_line_for_time(self, time_ms: int) -> Optional[int]:
        """查找当前时间对应的歌词行索引（使用快照索引，O(1)判断）。

        Args:
            time_ms: 当前播放时间（毫秒）

        Returns:
            行索引，如果没有找到返回 None
        """
        points = self._line_switch_points
        if not points:
            return None

        # 时间倒退（拖动进度条）：重置索引
        if self._current_switch_idx > 0:
            if time_ms < points[self._current_switch_idx][0]:
                self._current_switch_idx = 0

        # 从当前位置向后推进
        while self._current_switch_idx < len(points) - 1:
            next_time, _ = points[self._current_switch_idx + 1]
            if time_ms < next_time:
                break
            self._current_switch_idx += 1

        return points[self._current_switch_idx][1]

    def set_current_time_ms(self, time_ms: int):
        self._current_time_ms = time_ms
        # 播放期间按就近扩散顺序预热少量邻近行，降低视口内首帧卡顿
        if self._is_playing:
            self._warm_nearby_cache(budget=2)
        # 自动滚动：始终检测播放行变化（用于 cooldown 判断），
        # 仅在未挂起时才移动视口（不改变编辑光标 _current_line_idx）
        if self._auto_scroll_enabled and self._is_playing:
            target_line_idx = self._find_line_for_time(time_ms)
            if target_line_idx is not None:
                if target_line_idx != self._last_auto_scroll_line_idx:
                    self._last_auto_scroll_line_idx = target_line_idx
                    self.auto_scroll_line_changed.emit()
                if not self._auto_scroll_suspended:
                    self._scroll_to_line(target_line_idx)
        self.update()

    def _prewarm_all_sentences(self) -> None:
        """项目加载时预渲染所有句子到缓存，避免切换界面时卡顿。"""
        if not self._project or not self._project.sentences:
            return
        # 使用默认字体预渲染，实际渲染时会按 is_current 切换字体
        for idx, sentence in enumerate(self._project.sentences):
            if sentence.characters:
                self._get_sentence_render_data(idx, sentence, self._fm_context, "ctx")
                if idx == self._current_line_idx:
                    self._get_sentence_render_data(idx, sentence, self._fm_current, "cur")

    def _warm_nearby_cache(self, budget: int = 2) -> None:
        """按 L, L+1, L-1, L+2, L-2, ... 的就近扩散顺序预热 _sentence_cache。

        - 每次最多预热 budget 句，避免阻塞 paint
        - 已缓存/版本匹配的行直接跳过，不重复计算
        - 行号边界：center 被 clamp 到 [0, n-1]，扩散候选再次越界检查；
          两端都越界后早退，避免无意义空转
        """
        if not self._project or not self._project.sentences:
            return
        sentences = self._project.sentences
        n = len(sentences)
        if n <= 0:
            return
        # 播放+自动滚动时以播放行为中心预热，否则以编辑光标为中心
        if (
            self._is_playing
            and self._auto_scroll_enabled
            and not self._auto_scroll_suspended
            and self._last_auto_scroll_line_idx >= 0
        ):
            center = max(0, min(self._last_auto_scroll_line_idx, n - 1))
        else:
            center = max(0, min(self._current_line_idx, n - 1))
        warmed = 0
        for offset in range(n):
            # 扩散序：0, +1, -1, +2, -2, +3, -3, ...
            if offset == 0:
                candidates = (center,)
            else:
                candidates = (center + offset, center - offset)
            any_valid = False
            for idx in candidates:
                if idx < 0 or idx >= n:
                    continue
                any_valid = True
                entry = self._sentence_cache.get(idx)
                is_current_line = idx == self._current_line_idx
                fk = "cur" if is_current_line else "ctx"
                line_version = self._line_versions.get(idx, 0)
                if entry and entry["v"] == line_version and entry["gv"] == self._global_version and entry["fk"] == fk:
                    continue
                main_fm = self._fm_current if is_current_line else self._fm_context
                self._get_sentence_render_data(idx, sentences[idx], main_fm, fk)
                warmed += 1
                if warmed >= budget:
                    return
            # 两端都越界（向前到 0、向后到 n-1 之外）→ 无需继续扩散
            if offset > 0 and not any_valid:
                return

    def set_global_offset(self, offset_ms: int):
        """设置全局偏移量（毫秒），更新所有字符的时间戳"""
        self._global_offset_ms = offset_ms
        # 偏移变更时，清除缓存使 wipe 区间重新计算
        self._sentence_cache.clear()

    def set_audio_engine(self, engine):
        """设置音频引擎引用，使 paintEvent 可主动拉取高精度时间。"""
        self._audio_engine = engine
        self._line_versions.clear()
        self._global_version += 1
        self.update()

    def set_alignment(self, alignment: str):
        """设置歌词对齐方式。

        Args:
            alignment: "left"（左对齐）、"center"（居中对齐）或 "right"（右对齐）
        """
        if alignment not in ("left", "center", "right"):
            alignment = "center"
        if self._alignment != alignment:
            self._alignment = alignment
            self.update()

    def set_alignment_margin(self, margin: int):
        """设置左/右对齐时的页边距。

        Args:
            margin: 页边距像素值
        """
        margin = max(0, min(500, margin))
        if self._alignment_margin != margin:
            self._alignment_margin = margin
            self.update()

    def set_font_sizes(self, base_size: int, current_line_size: int = 0, ruby_size: int = 10, cp_size: int = 8, line_height_factor: float = 1.20, ruby_spacing: int = 4):
        """设置字体大小并自动适配预览行数。

        Args:
            base_size: 基础字体大小（非当前行）
            current_line_size: 当前行放大字体大小，0 表示自动比基础大4
            ruby_size: 注音字体大小
            cp_size: 节奏点标记字体大小
            line_height_factor: 行高系数（默认1.20）
            ruby_spacing: Ruby与主文字的垂直间距（默认4px）
        """
        context_size = max(12, base_size)
        current_size = max(12, current_line_size if current_line_size > 0 else base_size + 4)
        ruby_size = max(6, ruby_size)
        cp_size = max(6, cp_size)
        line_height_factor = max(0.5, min(5.0, line_height_factor))
        ruby_spacing = max(0, min(20, ruby_spacing))

        self._font_current = QFont("Microsoft YaHei", current_size, QFont.Weight.Bold)
        self._font_context = QFont("Microsoft YaHei", context_size)
        self._font_ruby = QFont("Microsoft YaHei", ruby_size)
        self._font_checkpoint = QFont("Microsoft YaHei", cp_size)
        self._fm_current = QFontMetrics(self._font_current)
        self._fm_context = QFontMetrics(self._font_context)
        self._fm_ruby = QFontMetrics(self._font_ruby)
        self._fm_checkpoint = QFontMetrics(self._font_checkpoint)
        self._ruby_spacing = ruby_spacing
        self._line_height_factor = line_height_factor

        # 行高以当前行（放大后）字体大小为准，需容纳 ruby + ruby_spacing + cp
        total_height = self._fm_current.height() + self._fm_ruby.height() + ruby_spacing + self._fm_checkpoint.height()
        line_h = total_height * line_height_factor
        h = self.height() if self.height() > 0 else 600
        self._visible_lines = max(3, min(15, int(h / line_h)))

        # 清除缓存并重绘
        self._sentence_cache.clear()
        self._line_versions.clear()
        self._global_version += 1
        self.update()

    def set_checkpoint_markers(self, markers: dict[str, str]):
        """设置 checkpoint 标记字符并刷新缓存。"""
        self._checkpoint_markers.update(markers)
        self._sentence_cache.clear()
        self._line_versions.clear()
        self._global_version += 1
        self.update()

    def _update_display(self):
        self._global_version += 1
        self.update()

    def _invalidate_line(self, line_idx: int):
        """使特定行的缓存失效（用于行内数据改变时）"""
        if line_idx in self._line_versions:
            self._line_versions[line_idx] += 1
        else:
            self._line_versions[line_idx] = 0
        self.update()

    def _invalidate_all_lines(self):
        """使所有行的缓存失效（用于全局数据改变时）"""
        for line_idx in list(self._line_versions.keys()):
            self._line_versions[line_idx] += 1
        self.update()

    # ---- 窗口大小变化 ----

    def resizeEvent(self, event):
        """窗口大小变化时重新计算可见行数，保持行高不变。"""
        super().resizeEvent(event)
        if hasattr(self, '_fm_current') and hasattr(self, '_fm_ruby'):
            total_height = (self._fm_current.height() + self._fm_ruby.height()
                           + self._ruby_spacing + self._fm_checkpoint.height())
            line_h = total_height * getattr(self, '_line_height_factor', 1.20)
            h = self.height() if self.height() > 0 else 600
            self._visible_lines = max(3, min(15, int(h / line_h)))
            self.update()

    # ---- 滚动 ----

    def wheelEvent(self, a0):
        """鼠标滚轮滚动浏览歌词"""
        if not a0 or not self._project or not self._project.sentences:
            return
        self._suspend_auto_scroll()
        delta = a0.angleDelta().y()
        # 每个滚轮 notch（120 单位）滚动 1 行
        self._scroll_center_line -= delta / 120.0
        total = len(self._project.sentences)
        self._scroll_center_line = max(
            0.0, min(float(total - 1), self._scroll_center_line)
        )
        self.update()

    # ---- 点击 ----

    def mousePressEvent(self, a0: Optional[QMouseEvent]):
        if not a0 or not self._project or not self._project.sentences:
            return
        self._suspend_auto_scroll()

        click_x = int(a0.position().x())
        click_y = int(a0.position().y())

        # 右键点击 → 打开上下文菜单
        if a0.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(a0.globalPosition().toPoint(), click_x, click_y)
            return

        # 记录按下位置，用于像素级防抖判断
        self._press_pos = (click_x, click_y)

        # 检查是否点击在 checkpoint 标记上
        for marker_rect, line_idx, char_idx, cp_idx in self._checkpoint_hitboxes:
            if marker_rect.contains(click_x, click_y):
                self._pending_click = {
                    "type": "cp",
                    "line_idx": line_idx,
                    "char_idx": char_idx,
                    "cp_idx": cp_idx,
                }
                return

        # 检查字符文本点击 → 开始划词选择
        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if char_rect.contains(click_x, click_y):
                self._focus_line_idx = line_idx
                self._focus_char_idx = char_idx
                self._focus_char_range_end = char_idx
                self._focus_dragging = True
                self._pending_click = {
                    "type": "char",
                    "line_idx": line_idx,
                    "char_idx": char_idx,
                }
                self.update()
                return

        # 点击在行内空白区域：按水平距离找最近的 hitbox
        nearest = self._find_nearest_hitbox(click_x, click_y)
        if nearest:
            hit_type, line_idx, char_idx, cp_idx = nearest
            self._focus_line_idx = line_idx
            self._focus_char_idx = char_idx
            self._focus_char_range_end = char_idx
            self._focus_dragging = True
            if hit_type == "cp":
                self._pending_click = {
                    "type": "cp",
                    "line_idx": line_idx,
                    "char_idx": char_idx,
                    "cp_idx": cp_idx,
                }
            else:
                self._pending_click = {
                    "type": "char",
                    "line_idx": line_idx,
                    "char_idx": char_idx,
                }
            self.update()
            return

        # 回退到行级别点击：根据 y 坐标反算行索引
        # 清除选中状态
        self._focus_line_idx = -1
        self._focus_char_idx = -1
        self._focus_char_range_end = -1
        self._clear_click_snapshot()

        h = self.height()
        line_height = h / self._visible_lines
        center_y = h / 2.0
        # 点击位置对应的行索引（浮点）
        clicked_line = self._scroll_center_line + (click_y - center_y) / line_height
        target_idx = int(round(clicked_line))
        total = len(self._project.sentences)
        if 0 <= target_idx < total:
            self.line_clicked.emit(target_idx)
        self.update()

    def mouseMoveEvent(self, a0: Optional[QMouseEvent]):
        """鼠标拖拽 → 扩展划词选择范围"""
        if not a0:
            return

        move_x = int(a0.position().x())
        move_y = int(a0.position().y())

        # 像素级防抖：移动超过阈值时清除待处理点击（说明用户是划词而非单击）
        if self._press_pos is not None and self._pending_click is not None:
            dx = move_x - self._press_pos[0]
            dy = move_y - self._press_pos[1]
            if dx * dx + dy * dy > self._CLICK_MOVE_THRESHOLD * self._CLICK_MOVE_THRESHOLD:
                self._pending_click = None

        if not self._focus_dragging:
            return

        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if char_rect.contains(move_x, move_y) and line_idx == self._focus_line_idx:
                self._focus_char_range_end = char_idx
                self.update()
                return

    def mouseReleaseEvent(self, a0: Optional[QMouseEvent]):
        """鼠标释放 → 结束划词，或触发单击"""
        if not a0 or a0.button() != Qt.MouseButton.LeftButton:
            return

        self._focus_dragging = False

        # 如果已有快照（双击的第二次松开），跳过单击
        if self._click_snapshot is not None:
            self._pending_click = None
            self._press_pos = None
            return

        # 如果有待处理点击 且 移动距离 < 阈值，立即执行单击
        if self._pending_click is not None and self._press_pos is not None:
            click = self._pending_click
            self._pending_click = None
            self._press_pos = None

            if not self._disable_click_jump:
                if click["type"] == "cp":
                    self.checkpoint_clicked.emit(
                        click["line_idx"], click["char_idx"], click["cp_idx"]
                    )
                elif click["type"] == "char":
                    self.char_selected.emit(click["line_idx"], click["char_idx"])
                    self.line_clicked.emit(click["line_idx"])

                # 保存快照用于双击判断，300ms 后清除
                self._click_snapshot = click
                self._click_timer.start()
        else:
            # _pending_click 被拖拽检测清掉了，但 focus 已在 mousePressEvent
            # 里更新。用 focus 位置作为单击坐标，确保 current 域同步。
            if (
                not self._disable_click_jump
                and self._focus_line_idx >= 0
                and self._focus_char_idx >= 0
            ):
                self.char_selected.emit(self._focus_line_idx, self._focus_char_idx)
                self.line_clicked.emit(self._focus_line_idx)
            self._pending_click = None
            self._press_pos = None

    def _clear_click_snapshot(self):
        """清除单击快照和相关定时器。

        300ms 超时 → 确认是单击而非双击，兜底同步 current 域到 focus 位置，
        防止期间有状态漂移。
        """
        self._click_snapshot = None
        self._click_timer.stop()
        if self._focus_line_idx >= 0 and self._focus_char_idx >= 0:
            self.set_current_position(self._focus_line_idx, self._focus_char_idx)

    def _show_context_menu(self, global_pos, click_x: int, click_y: int):
        """显示字符上下文菜单。"""
        if not self._project or not self._project.sentences:
            return

        target_line_idx = self._current_line_idx
        target_char_idx = self._current_char_idx
        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if char_rect.contains(click_x, click_y):
                target_line_idx = line_idx
                target_char_idx = char_idx
                self._current_line_idx = line_idx
                self._current_char_idx = char_idx
                break

        if target_line_idx < 0 or target_line_idx >= len(self._project.sentences):
            return

        sentence = self._project.sentences[target_line_idx]
        if target_char_idx < 0:
            target_char_idx = 0
        if sentence.characters and target_char_idx >= len(sentence.characters):
            target_char_idx = len(sentence.characters) - 1

        in_selection = False
        if (
            self._focus_line_idx == target_line_idx
            and self._focus_char_idx >= 0
            and self._focus_char_range_end >= 0
        ):
            sel_start = min(self._focus_char_idx, self._focus_char_range_end)
            sel_end = max(self._focus_char_idx, self._focus_char_range_end)
            in_selection = sel_start <= target_char_idx <= sel_end
        else:
            sel_start = target_char_idx
            sel_end = target_char_idx

        delete_start = sel_start if in_selection else target_char_idx
        delete_end = sel_end + 1 if in_selection else target_char_idx + 1

        menu = RoundMenu(parent=self)

        delete_action = Action("删除字符", menu)
        delete_action.triggered.connect(
            lambda checked=False: self.delete_chars_requested.emit(
                target_line_idx, delete_start, delete_end
            )
        )
        menu.addAction(delete_action)

        delete_timestamp = Action("删除当前时间戳并回滚", menu)
        delete_timestamp.triggered.connect(
            lambda checked=False: self.delete_timestamp_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(delete_timestamp)

        insert_space_before_action = Action("在此前插入空格", menu)
        insert_space_before_action.triggered.connect(
            lambda checked=False: self.insert_space_before_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(insert_space_before_action)

        insert_space_after_action = Action("在此后插入空格", menu)
        insert_space_after_action.triggered.connect(
            lambda checked=False: self.insert_space_after_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(insert_space_after_action)
        menu.addSeparator()

        merge_up_action = Action("合并上一行", menu)
        merge_up_action.setEnabled(target_line_idx > 0)
        merge_up_action.triggered.connect(
            lambda checked=False: self.merge_line_up_requested.emit(target_line_idx)
        )
        menu.addAction(merge_up_action)

        delete_line_action = Action("删除本行", menu)
        delete_line_action.triggered.connect(
            lambda checked=False: self.delete_line_requested.emit(target_line_idx)
        )
        menu.addAction(delete_line_action)

        insert_blank_line_before_action = Action("在此前插入空行", menu)
        insert_blank_line_before_action.triggered.connect(
            lambda checked=False: self.insert_blank_line_before_requested.emit(target_line_idx)
        )
        menu.addAction(insert_blank_line_before_action)

        insert_blank_line_after_action = Action("在此后插入空行", menu)
        insert_blank_line_after_action.triggered.connect(
            lambda checked=False: self.insert_blank_line_requested.emit(target_line_idx)
        )
        menu.addAction(insert_blank_line_after_action)
        menu.addSeparator()

        add_checkpoint_action = Action("增加节奏点", menu)
        add_checkpoint_action.triggered.connect(
            lambda checked=False: self.add_checkpoint_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(add_checkpoint_action)

        remove_checkpoint_action = Action("减少节奏点", menu)
        remove_checkpoint_action.triggered.connect(
            lambda checked=False: self.remove_checkpoint_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(remove_checkpoint_action)

        toggle_sentence_end_action = Action("设置/取消句尾", menu)
        toggle_sentence_end_action.triggered.connect(
            lambda checked=False: self.toggle_sentence_end_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(toggle_sentence_end_action)
        menu.addSeparator()

        singer_start = delete_start if in_selection else target_char_idx
        singer_end = delete_end - 1 if in_selection else target_char_idx
        singer_menu = RoundMenu("设置演唱者", self)
        default_singer = self._project.get_default_singer()
        default_action = Action("默认演唱者", singer_menu)
        default_action.triggered.connect(
            lambda checked=False: self.singer_change_requested.emit(
                target_line_idx, singer_start, singer_end, default_singer.id
            )
        )
        singer_menu.addAction(default_action)
        singer_menu.addSeparator()

        for singer in self._project.singers:
            action = Action(singer.name, singer_menu)
            action.triggered.connect(
                lambda checked=False, sid=singer.id: self.singer_change_requested.emit(
                    target_line_idx, singer_start, singer_end, sid
                )
            )
            singer_menu.addAction(action)

        menu.addMenu(singer_menu)
        menu.exec(global_pos)

    def _find_next_line_first_timestamp(self, current_line_idx: int) -> Optional[int]:
        """查找下一行的第一个checkpoint时间戳，用于行尾非句尾时的wipe右边界。

        Returns:
            下一行第一个字符的 global_timestamps[0]，如果不存在返回 None
        """
        if not self._project or not self._project.sentences:
            return None
        next_line_idx = current_line_idx + 1
        if next_line_idx >= len(self._project.sentences):
            return None
        next_sentence = self._project.sentences[next_line_idx]
        for ch in next_sentence.characters:
            if ch.global_timestamps:
                return int(ch.global_timestamps[0])
        return None

    def _get_sentence_render_data(
        self, idx: int, sentence, main_fm, font_key: str
    ) -> dict:
        """返回逐句渲染数据。

        渲染模型：
          - wipe 时间线的单位 = 一整行（sentence），与打轴的连词组解耦
          - 行内所有带 global_timestamps 的字符 + 行尾（is_sentence_end +
            global_sentence_end_ts）构成"锚点"
          - 相邻锚点之间的所有字符（含无时间戳的）按字符像素宽度加权线性插值
            分配 wipe 区间——解决等字符数分配导致的宽度/节奏不匹配跳变
          - 首锚之前 / 末锚之后的字符贴首/末锚（wipe 恒 0 或 1）
          - linked_to_next 只影响视觉渲染层（连词不拆字画），不参与 wipe 计算

        缓存策略：
          - 每行有自己的版本号，只有数据改变的行才重新计算
          - 全局版本号用于字体变化等全局刷新
        """
        line_version = self._line_versions.get(idx, 0)
        entry = self._sentence_cache.get(idx)
        if entry and entry["v"] == line_version and entry["gv"] == self._global_version and entry["fk"] == font_key:
            return entry

        chars = sentence.chars
        characters = sentence.characters
        n_chars = len(chars)

        # 字符像素宽度（初始为字符本身的 advance width，含侧 bearings/字间距）
        # 同时计算字符的"墨水"边界（tightBoundingRect）—— wipe 严格按墨水边界裁剪，
        # 不再扫过字形周围的透明像素或句尾扩展区，确保 wipe 的视觉起止与时间戳一致。
        fm_ruby = self._fm_ruby
        avg_char_w = main_fm.averageCharWidth()
        char_widths = []
        # char_ink_offsets[ci] = ink_left（相对 drawText 的 x）
        # char_ink_widths[ci]  = ink 实际像素宽度
        # 空白字符 ink_width = 0，wipe 时跳过裁剪绘制（视觉上保留空白等待）。
        char_ink_offsets: list[int] = []
        char_ink_widths: list[int] = []
        for ci, ch in enumerate(chars):
            char_w = main_fm.horizontalAdvance(ch) if ch != ' ' else avg_char_w
            char_widths.append(char_w)
            ink_off, ink_w = _ink_bounds(main_fm, ch)
            char_ink_offsets.append(ink_off)
            char_ink_widths.append(ink_w)

        # ---------- 连词组（仅用于视觉层，与 wipe 计算无关） ----------
        char_groups: list = []
        cur_grp: Optional[list] = None
        for ci in range(n_chars):
            if cur_grp is None:
                cur_grp = [ci]
                char_groups.append(cur_grp)
            elif ci > 0 and characters[ci - 1].linked_to_next:
                cur_grp.append(ci)
            else:
                cur_grp = [ci]
                char_groups.append(cur_grp)

        linked_leader_groups: dict = {}
        linked_non_leader: set = set()
        for group in char_groups:
            if len(group) > 1:
                linked_leader_groups[group[0]] = group
                for _ci in group[1:]:
                    linked_non_leader.add(_ci)

        # 连词组：将合并后的 ruby 宽度平均分配到组内每个字符
        for leader_ci, group in linked_leader_groups.items():
            merged_ruby_text = ""
            for _gci in group:
                _r = characters[_gci].ruby
                if _r:
                    merged_ruby_text += _r.text
            if merged_ruby_text:
                merged_ruby_w = fm_ruby.horizontalAdvance(merged_ruby_text)
                group_total_char_w = sum(char_widths[g] for g in group)
                # 确保组的总宽度 >= 合并后ruby的宽度
                target_total_w = max(group_total_char_w, merged_ruby_w)
                # 将宽度平均分配到组内每个字符
                per_char_w = target_total_w / len(group)
                for g in group:
                    char_widths[g] = max(char_widths[g], per_char_w)

        # 单字符：确保宽度 >= ruby宽度（跳过连词组成员，已由连词组逻辑处理）
        for ci, ch in enumerate(chars):
            if ci in linked_leader_groups or ci in linked_non_leader:
                continue  # 连词组成员已处理
            ruby = characters[ci].ruby
            if ruby:
                ruby_w = fm_ruby.horizontalAdvance(ruby.text)
                char_widths[ci] = max(char_widths[ci], ruby_w)

        # 确保字符宽度 >= CP标记总宽度（避免CP标记重叠）
        # 句尾marker独立于普通marker，在字符右侧扩展半字符宽度
        fm_checkpoint = self._fm_checkpoint
        end_sentence_w: dict[int, int] = {}
        for ci, ch_obj in enumerate(characters):
            if ch_obj.total_timing_points > 0:
                total_cp_w = 0
                for cp_idx in range(ch_obj.total_timing_points):
                    is_sentence_end_marker = (
                        ch_obj.is_sentence_end and cp_idx == ch_obj.check_count
                    )
                    if is_sentence_end_marker:
                        continue  # 句尾marker不计入普通CP宽度
                    elif cp_idx == 0:
                        marker_char = self._checkpoint_markers["cp_first_timed"] if cp_idx < len(ch_obj.global_timestamps) else self._checkpoint_markers["cp_first_empty"]
                    else:
                        marker_char = self._checkpoint_markers["cp_multi_timed"] if cp_idx < len(ch_obj.global_timestamps) else self._checkpoint_markers["cp_multi_empty"]
                    total_cp_w += fm_checkpoint.horizontalAdvance(marker_char)
                char_widths[ci] = max(char_widths[ci], total_cp_w)
            # 句尾字符扩展该字符自身宽度的一半用于放置句尾marker（单独追踪，不混入 char_widths）
            if ch_obj.is_sentence_end:
                end_sentence_w[ci] = char_widths[ci] // 2

        # ---------- wipe 时间线（离散字符开始时间模型） ----------
        # 每个字符的 wipe 开始时间 = 该字符第一个 cp 的时间戳（global_timestamps[0]）。
        # 字符 wipe 结束时间 = 同句子内下一个有 start_ts 的字符的开始时间；
        # 若后面无 start_ts，则使用句尾时间戳（global_sentence_end_ts）。
        # 中间无 timestamp 的字符与上一个有 timestamp 的字符连读，共享同一段 wipe。
        # 一行可能含多个句子（多个 is_sentence_end），各句子独立计算段。
        start_times: dict[int, int] = {}
        for ci, ch in enumerate(characters):
            if ch.global_timestamps:
                start_times[ci] = int(ch.global_timestamps[0])

        # 按 is_sentence_end 拆分为句子范围 [(sent_start, sent_end)]，含边界
        sent_ranges: list[tuple[int, int]] = []
        s_start = 0
        for ci, ch in enumerate(characters):
            if ch.is_sentence_end:
                sent_ranges.append((s_start, ci))
                s_start = ci + 1
        if s_start < n_chars:
            sent_ranges.append((s_start, n_chars - 1))

        # 预处理：为行尾非句尾的情况准备右边界
        # 如果最后一个 sent_range 的最后一个字符不是句尾，去下一行借时间戳
        fallback_sentence_end_ts: Optional[int] = None
        if sent_ranges:
            last_sent_start, last_sent_end = sent_ranges[-1]
            last_char = characters[last_sent_end]
            if not last_char.is_sentence_end:
                next_ts = self._find_next_line_first_timestamp(idx)
                if next_ts is not None:
                    fallback_sentence_end_ts = next_ts
                elif self._duration_ms > 0:
                    fallback_sentence_end_ts = self._duration_ms

        char_wipe_times: dict = {}
        for sent_start, sent_end in sent_ranges:
            # 句子内有 start_ts 的 leader 字符索引
            leaders = [ci for ci in range(sent_start, sent_end + 1) if ci in start_times]
            if not leaders:
                continue

            for i, leader in enumerate(leaders):
                next_leader = leaders[i + 1] if i + 1 < len(leaders) else None
                seg_end = (next_leader - 1) if next_leader is not None else sent_end

                if next_leader is not None:
                    end_ts = start_times[next_leader]
                else:
                    # 句子最后一段：找 sentence_end_ts
                    end_ts = None
                    for ci in range(leader, sent_end + 1):
                        if characters[ci].is_sentence_end and characters[ci].global_sentence_end_ts is not None:
                            end_ts = int(characters[ci].global_sentence_end_ts)
                            break
                    if end_ts is None:
                        # 使用预处理的 fallback（行尾非句尾时从下一行借的时间戳）
                        end_ts = fallback_sentence_end_ts if fallback_sentence_end_ts is not None else start_times[leader]

                # 整体：leader + 它后面的无 ts 字符，按位置从左到右分配时间
                total_chars = seg_end - leader + 1
                for j, ci in enumerate(range(leader, seg_end + 1)):
                    ratio = j / total_chars
                    next_ratio = (j + 1) / total_chars
                    char_start_ts = int(start_times[leader] + (end_ts - start_times[leader]) * ratio)
                    char_end_ts = int(start_times[leader] + (end_ts - start_times[leader]) * next_ratio)
                    char_wipe_times[ci] = (char_start_ts, char_end_ts)

            # 句子内第一个 leader 之前的无 ts 字符：与第一个 leader 作为整体从左到右 wipe
            first_leader = leaders[0]
            if first_leader > sent_start:
                leader_start_ts, leader_end_ts = char_wipe_times[first_leader]
                # 按位置分配时间，从左到右依次完成
                total_chars = first_leader - sent_start + 1  # 包含 first_leader
                for i, ci in enumerate(range(sent_start, first_leader)):
                    ratio = i / total_chars
                    next_ratio = (i + 1) / total_chars
                    char_start_ts = int(leader_start_ts + (leader_end_ts - leader_start_ts) * ratio)
                    char_end_ts = int(leader_start_ts + (leader_end_ts - leader_start_ts) * next_ratio)
                    char_wipe_times[ci] = (char_start_ts, char_end_ts)

        # ---------- 每字符的 part 锚点序列（用于 check_count>=2 的多 checkpoint 字符） ----------
        # char_part_anchors[ci] = [ts_0, ts_1, ..., ts_N]，N = part 数
        #   - 仅在 ch.global_timestamps 数量 >= 2 时构造（即 check_count>=2 且至少打了 2 个轴）
        #   - 末尾的 ts_N 取 char_wipe_times[ci][1]（沿用现状的回退链：下一字符 ts[0] /
        #     global_sentence_end_ts / 下一行首 ts / _duration_ms）
        #   - ruby.parts 数与 check_count 不匹配也启用：渲染层会按 ruby 文本像素 N 等分回退
        char_part_anchors: dict[int, list[int]] = {}
        for ci, ch_obj in enumerate(characters):
            gts = list(ch_obj.global_timestamps)
            if len(gts) < 2:
                continue
            wt = char_wipe_times.get(ci)
            if not wt:
                continue
            seg_end_ts = wt[1]
            # 末尾锚点必须严格大于倒数第二个，否则视为脏数据，跳过
            if seg_end_ts <= gts[-1]:
                continue
            char_part_anchors[ci] = gts + [int(seg_end_ts)]

        # ---------- 连词组的总锚点轴（组内 char 的 global_timestamps 串成总轴） ----------
        # group_anchors[leader_ci] = [ts_0, ts_1, ..., ts_M]，M = 组内总 part 数
        #   - 仅当组内至少有一个字符具备多 checkpoint 时启用
        #   - 末尾用组最后一个字符的 wipe end_ts
        #   - 组内中间字符若有 global_timestamps[0]，会自然加入（保证时序单调）
        group_anchors: dict[int, list[int]] = {}
        group_char_pixel_starts: dict[int, dict[int, int]] = {}  # leader_ci -> {ci: cum_w_before}
        group_char_total_w: dict[int, int] = {}  # leader_ci -> 组总像素宽
        for leader_ci, group in linked_leader_groups.items():
            ts_list: list[int] = []
            for _gci in group:
                ch_obj = characters[_gci]
                gts = list(ch_obj.global_timestamps)
                ts_list.extend(int(t) for t in gts)
            if len(ts_list) < 1:
                continue
            # 末尾锚点：组最后一个字符的 wipe 结束时间
            last_ci = group[-1]
            last_wt = char_wipe_times.get(last_ci)
            if not last_wt:
                continue
            seg_end_ts = int(last_wt[1])
            # 必须严格单调递增，否则跳过
            if any(ts_list[i] >= ts_list[i + 1] for i in range(len(ts_list) - 1)):
                continue
            if seg_end_ts <= ts_list[-1]:
                continue
            # 至少要有 2 个锚点（含末尾），否则没必要走新模型
            if len(ts_list) < 2 and seg_end_ts > ts_list[-1]:
                # ts_list 只有 1 个值 → 与旧 char_wipe_times 等价，不启用新模型
                continue
            group_anchors[leader_ci] = ts_list + [seg_end_ts]
            # 累计像素分布（用 char_widths，含已 inflate 的 ruby 对齐宽度）
            cum = 0
            offsets: dict[int, int] = {}
            for _gci in group:
                offsets[_gci] = cum
                cum += int(char_widths[_gci])
            group_char_pixel_starts[leader_ci] = offsets
            group_char_total_w[leader_ci] = cum

        # ---------- Ruby 整串墨水边界缓存 ----------
        # 单字符 ruby：以 ruby.text 整串的 tightBoundingRect 为准
        # 连词组 ruby：以组内所有 ruby.text 拼接后整串的 tightBoundingRect 为准
        # 返回 (ink_left, ink_width)，wipe 时把 clip 的左边界从 ruby_x 收缩到
        # ruby_x + ink_left，宽度由 ink_width × ratio 决定。
        char_ruby_ink: dict[int, tuple[int, int]] = {}
        group_ruby_ink: dict[int, tuple[int, int]] = {}
        for ci, ch_obj in enumerate(characters):
            if ci in linked_leader_groups or ci in linked_non_leader:
                continue
            ruby = ch_obj.ruby
            if ruby and ruby.text:
                char_ruby_ink[ci] = _ink_bounds(fm_ruby, ruby.text)
        for leader_ci, group in linked_leader_groups.items():
            merged_text = ""
            for _gci in group:
                _r = characters[_gci].ruby
                if _r:
                    merged_text += _r.text
            if merged_text:
                group_ruby_ink[leader_ci] = _ink_bounds(fm_ruby, merged_text)

        entry = {
            "v": line_version,
            "gv": self._global_version,
            "fk": font_key,
            "char_widths": char_widths,
            "char_ink_offsets": char_ink_offsets,
            "char_ink_widths": char_ink_widths,
            "end_sentence_w": end_sentence_w,
            "total_text_width": sum(char_widths) + sum(end_sentence_w.values()),
            "char_wipe_times": char_wipe_times,
            "linked_leader_groups": linked_leader_groups,
            "linked_non_leader": linked_non_leader,
            "char_part_anchors": char_part_anchors,
            "group_anchors": group_anchors,
            "group_char_pixel_starts": group_char_pixel_starts,
            "group_char_total_w": group_char_total_w,
            "char_ruby_ink": char_ruby_ink,
            "group_ruby_ink": group_ruby_ink,
        }
        self._sentence_cache[idx] = entry
        return entry

    def _find_nearest_hitbox(self, click_x: int, click_y: int) -> tuple[str, int, int, int | None] | None:
        """在行内空白区域找水平距离最近的 hitbox

        Returns:
            (hit_type, line_idx, char_idx, cp_idx) 或 None
        """
        # 收集所有 hitbox（字符和 checkpoint），按行分组
        line_hitboxes: dict[int, list[tuple[QRect, str, int, int, int | None]]] = {}
        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if line_idx not in line_hitboxes:
                line_hitboxes[line_idx] = []
            line_hitboxes[line_idx].append((char_rect, "char", line_idx, char_idx, None))
        for marker_rect, line_idx, char_idx, cp_idx in self._checkpoint_hitboxes:
            if line_idx not in line_hitboxes:
                line_hitboxes[line_idx] = []
            line_hitboxes[line_idx].append((marker_rect, "cp", line_idx, char_idx, cp_idx))

        # 找到点击所在的行（垂直范围内）
        for line_idx, hitboxes in line_hitboxes.items():
            if not hitboxes:
                continue
            # 检查是否在行内垂直范围内
            first_rect = hitboxes[0][0]
            rect_top = first_rect.top()
            rect_bottom = first_rect.bottom()
            if not (rect_top <= click_y <= rect_bottom):
                continue
            # 在该行内找水平距离最近的 hitbox
            min_dist = float('inf')
            nearest = None
            for rect, hit_type, li, ci, cpi in hitboxes:
                # 计算水平距离
                if click_x < rect.left():
                    dist = rect.left() - click_x
                elif click_x > rect.right():
                    dist = click_x - rect.right()
                else:
                    dist = 0
                if dist < min_dist:
                    min_dist = dist
                    nearest = (hit_type, li, ci, cpi)
            return nearest
        return None

    def mouseDoubleClickEvent(self, a0: Optional[QMouseEvent]):
        """双击 → 跳转到时间戳"""
        if not a0 or not self._project or not self._project.sentences:
            return

        click_x = int(a0.position().x())
        click_y = int(a0.position().y())

        # 双击时停止单击定时器
        self._click_timer.stop()

        # 优先使用快照判断双击目标（快照在单击时锁定，避免居中导致 hitbox 变化）
        # 禁用单击跳转时不使用快照，直接走当前 hitbox 判断
        if not self._disable_click_jump and self._click_snapshot is not None:
            snapshot = self._click_snapshot
            self._click_snapshot = None
            if snapshot["type"] == "cp":
                self.seek_to_checkpoint_requested.emit(
                    snapshot["line_idx"], snapshot["char_idx"], snapshot["cp_idx"]
                )
                return
            elif snapshot["type"] == "char":
                self.seek_to_char_requested.emit(
                    snapshot["line_idx"], snapshot["char_idx"]
                )
                return

        # 快照不存在时（如禁用单击跳转），回退到当前 hitbox 判断
        for marker_rect, line_idx, char_idx, cp_idx in self._checkpoint_hitboxes:
            if marker_rect.contains(click_x, click_y):
                self.seek_to_checkpoint_requested.emit(line_idx, char_idx, cp_idx)
                return

        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if char_rect.contains(click_x, click_y):
                self.seek_to_char_requested.emit(line_idx, char_idx)
                return

        # 双击在行内空白区域：按水平距离找最近的 hitbox
        nearest = self._find_nearest_hitbox(click_x, click_y)
        if nearest:
            hit_type, line_idx, char_idx, cp_idx = nearest
            if hit_type == "cp":
                self.seek_to_checkpoint_requested.emit(line_idx, char_idx, cp_idx)
            else:
                self.seek_to_char_requested.emit(line_idx, char_idx)
            return

    # ---- 绘制 ----

    def paintEvent(self, a0: Optional[QPaintEvent]):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 填充背景
        painter.fillRect(self.rect(), theme.karaoke_bg)

        # 清空 hitbox 缓存
        self._checkpoint_hitboxes = []
        self._char_hitboxes = []

        # 渲染时间：播放中主动拉取基于 perf_counter 外推的高精度时间，
        # 消除 QTimer 间隔 + Qt paint 调度带来的 ~16ms 抖动。
        if self._is_playing and self._audio_engine is not None:
            current_time = self._audio_engine.get_position_ms()
            self._current_time_ms = current_time
        else:
            current_time = self._current_time_ms

        if not self._project or not self._project.sentences:
            painter.setPen(theme.text_hint)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "请拖入sug项目或者歌词文件或ctrl+v粘贴剪贴板上的歌词文件"
            )
            painter.end()
            return

        w, h = self.width(), self.height()
        total = len(self._project.sentences)
        line_height = h / self._visible_lines
        center_y = h / 2.0

        font_current = self._font_current
        font_context = self._font_context
        font_ruby = self._font_ruby
        font_checkpoint = self._font_checkpoint

        fm_current = self._fm_current
        fm_context = self._fm_context
        fm_ruby = self._fm_ruby
        fm_checkpoint = self._fm_checkpoint

        default_highlight = theme.default_highlight

        # 计算可见行范围（留 1 行余量避免边缘裁切）
        half_visible = self._visible_lines / 2.0 + 1
        first_visible = max(0, int(self._scroll_center_line - half_visible))
        last_visible = min(total - 1, int(self._scroll_center_line + half_visible))

        # 播放+自动滚动时，用播放行做视觉高亮，不污染编辑光标
        effective_current = (
            self._last_auto_scroll_line_idx
            if self._is_playing
            and self._auto_scroll_enabled
            and not self._auto_scroll_suspended
            and self._last_auto_scroll_line_idx >= 0
            else self._current_line_idx
        )

        for idx in range(first_visible, last_visible + 1):
            # 行中心 y 坐标
            y_center_f = center_y + (idx - self._scroll_center_line) * line_height
            y_center = int(round(y_center_f))

            # 跳过完全不可见的行
            if y_center_f < -line_height or y_center_f > h + line_height:
                continue

            line = self._project.sentences[idx]
            is_current = idx == effective_current

            # 绘制行号（左侧固定区域）
            painter.setFont(self._font_line_number)
            line_num_color = theme.line_number_current if is_current else theme.line_number_normal
            painter.setPen(line_num_color)
            line_num_text = str(idx + 1)
            line_num_w = self._fm_line_number.horizontalAdvance(line_num_text)
            painter.drawText(
                int(self._line_number_margin - line_num_w - 5),
                int(y_center),
                line_num_text,
            )

            # 根据演唱者获取行级别默认高亮颜色
            singer = (
                self._project.get_singer(line.singer_id) if line.singer_id else None
            )
            highlight_color = (
                QColor(singer.color) if singer and singer.color else default_highlight
            )

            # 预计算每个字符的 per-char singer 颜色（从 Character.singer_id 读取）
            _char_singer_colors: dict = {}  # char_idx -> QColor (基色)
            _char_complement_colors: dict = {}  # char_idx -> QColor (选中高亮色 = 演唱者补色)
            default_singer = self._project.get_default_singer()
            for ci, char in enumerate(line.characters):
                singer_obj = self._project.get_singer(char.singer_id)
                singer_color = singer_obj.color if singer_obj and singer_obj.color else default_singer.color
                comp_color = (
                    singer_obj.complement_color
                    if singer_obj and singer_obj.complement_color
                    else default_singer.complement_color or singer_color
                )
                _char_singer_colors[ci] = QColor(singer_color)
                _char_complement_colors[ci] = QColor(comp_color)

            if is_current:
                main_font = font_current
                main_fm = fm_current
                base_color = theme.karaoke_text_current
            elif idx < effective_current:
                main_font = font_context
                main_fm = fm_context
                base_color = theme.karaoke_text_past
            else:
                main_font = font_context
                main_fm = fm_context
                base_color = theme.karaoke_text_future

            # 使用缓存的渲染数据（字符宽度/分组/wipe时间/连词信息）
            _rd = self._get_sentence_render_data(
                idx, line, main_fm, "cur" if is_current else "ctx"
            )
            char_widths = _rd["char_widths"]
            _char_ink_offsets = _rd["char_ink_offsets"]
            _char_ink_widths = _rd["char_ink_widths"]
            _end_sentence_w = _rd["end_sentence_w"]
            total_text_width = _rd["total_text_width"]
            char_wipe_times = _rd["char_wipe_times"]
            _linked_leader_groups = _rd["linked_leader_groups"]
            _linked_non_leader = _rd["linked_non_leader"]
            _char_part_anchors = _rd["char_part_anchors"]
            _group_anchors = _rd["group_anchors"]
            _group_char_pixel_starts = _rd["group_char_pixel_starts"]
            _group_char_total_w = _rd["group_char_total_w"]
            _char_ruby_ink = _rd["char_ruby_ink"]
            _group_ruby_ink = _rd["group_ruby_ink"]
            # 反查表：组员 ci -> leader_ci（用于主文字段判断是否走组总轴）
            _ci_to_leader: dict[int, int] = {}
            for _l_ci, _grp in _linked_leader_groups.items():
                if _l_ci in _group_anchors:
                    for _g in _grp:
                        _ci_to_leader[_g] = _l_ci

            # 根据对齐方式计算起始 x 坐标
            text_area_left = self._line_number_margin + 5  # 行号区域右侧留 5px 间距
            text_area_right = w  # 文本区域右边界
            available_width = text_area_right - text_area_left

            if self._alignment == "left":
                start_x = text_area_left + self._alignment_margin
            elif self._alignment == "right":
                start_x = text_area_right - total_text_width - self._alignment_margin
                # 确保不覆盖行号区域
                start_x = max(start_x, text_area_left)
            else:  # center
                start_x = text_area_left + (available_width - total_text_width) // 2

            curr_x = start_x

            for char_pos, ch in enumerate(line.chars):
                char_w = char_widths[char_pos]

                # 统一高亮/hitbox 矩形：以行逻辑中心 y_center_f 为唯一锚点、
                # 高度 clamp 到 int(line_height)。此前 _rect_top 在「行框顶」
                # 与「字体 ascent 顶」之间取 max()，在当前行字体放大（22pt）
                # 相邻行字体缩小（18pt）时两套锚点不一致，会让选中行下方出现
                # 大块空白（issue #9）。现统一以行中心垂直居中矩形，消除跳变。
                _rect_height = min(main_fm.height() + 4, int(line_height))
                _rect_top = int(round(y_center_f - _rect_height / 2))

                # 当前打轴位置高亮背景
                if is_current and char_pos == self._current_char_idx:
                    highlight_bg = theme.karaoke_highlight_bg
                    bg_rect = QRect(
                        int(curr_x) - 1,
                        _rect_top,
                        int(char_w) + 2,
                        _rect_height,
                    )
                    painter.fillRect(bg_rect, highlight_bg)

                # 划词选中高亮背景
                if idx == self._focus_line_idx and self._focus_char_idx >= 0:
                    sel_lo = min(self._focus_char_idx, self._focus_char_range_end)
                    sel_hi = max(self._focus_char_idx, self._focus_char_range_end)
                    if sel_lo <= char_pos <= sel_hi:
                        sel_bg = theme.karaoke_selection_bg
                        sel_rect = QRect(
                            int(curr_x) - 1,
                            _rect_top,
                            int(char_w) + 2,
                            _rect_height,
                        )
                        painter.fillRect(sel_rect, sel_bg)

                # 存储字符 hitbox 用于点击检测（与高亮矩形对齐）
                char_rect = QRect(
                    int(curr_x),
                    _rect_top,
                    int(char_w),
                    _rect_height,
                )
                self._char_hitboxes.append((char_rect, idx, char_pos))

                # Ruby — 连词组合并绘制 / 单字独立绘制
                if char_pos in _linked_non_leader:
                    pass  # Ruby 由组 leader 统一绘制
                elif char_pos in _linked_leader_groups:
                    # 连词组 leader：收集组内所有 ruby 合并绘制
                    _grp = _linked_leader_groups[char_pos]
                    _grp_rubies: list = []
                    for _gci in _grp:
                        _r = line.characters[_gci].ruby
                        if _r:
                            _grp_rubies.append(_r)
                    if _grp_rubies:
                        _merged = "".join(r.text for r in _grp_rubies)
                        _grp_w = sum(char_widths[g] for g in _grp)
                        ruby_text_w = fm_ruby.horizontalAdvance(_merged)
                        ruby_x = curr_x + (_grp_w - ruby_text_w) // 2
                        ruby_y = int(y_center - main_fm.ascent() - self._ruby_spacing)
                        # Ruby 整串墨水边界（合并后整体 tightBoundingRect）：
                        # wipe 按墨水起止点裁剪，不再扫过 ruby 首尾的透明侧 bearings。
                        _r_ink_off, _r_ink_w = _group_ruby_ink.get(char_pos, (0, ruby_text_w))
                        _r_ink_x = ruby_x + _r_ink_off
                        painter.setFont(font_ruby)
                        painter.setPen(base_color)
                        painter.drawText(int(ruby_x), ruby_y, _merged)
                        # Wipe — 优先使用组锚点轴；缺锚点回退旧整段线性
                        _fw = char_wipe_times.get(_grp[0])
                        _lw = char_wipe_times.get(_grp[-1])
                        _rs = _fw[0] if _fw else None
                        _re = _lw[1] if _lw else None
                        _rh = _char_singer_colors.get(_grp[0], highlight_color)
                        _g_anchors_r = _group_anchors.get(char_pos)
                        if _g_anchors_r is not None and len(_g_anchors_r) >= 2:
                            _i, _sr, _n = _anchor_segment(_g_anchors_r, current_time)
                            if _n > 0:
                                # 收集组内所有 part 顺序排列
                                _all_parts: list = []
                                for _r in _grp_rubies:
                                    if _r.parts:
                                        _all_parts.extend(_r.parts)
                                # 计算整体进度比例 _ratio ∈ [0,1]，再乘 ink 宽度
                                if len(_all_parts) == _n and _n > 0:
                                    _part_ws = [
                                        fm_ruby.horizontalAdvance(p.text)
                                        for p in _all_parts
                                    ]
                                    _total_pw = sum(_part_ws)
                                    if _total_pw > 0:
                                        _cum = sum(_part_ws[:_i])
                                        _local = _part_ws[_i] * _sr
                                        _ratio = (_cum + _local) / _total_pw
                                    else:
                                        _ratio = (_i + _sr) / _n
                                else:
                                    _ratio = (_i + _sr) / _n
                                _rww = int(_r_ink_w * _ratio) if _r_ink_w > 0 else 0
                                if _ratio >= 1.0:
                                    painter.setPen(_rh)
                                    painter.drawText(int(ruby_x), ruby_y, _merged)
                                elif _rww > 0:
                                    painter.save()
                                    painter.setClipRect(
                                        QRect(
                                            int(_r_ink_x),
                                            ruby_y - fm_ruby.ascent() - 2,
                                            _rww,
                                            fm_ruby.height() + 4,
                                        )
                                    )
                                    painter.setPen(_rh)
                                    painter.drawText(int(ruby_x), ruby_y, _merged)
                                    painter.restore()
                        elif _rs is not None and _re is not None:
                            if current_time >= _re:
                                painter.setPen(_rh)
                                painter.drawText(int(ruby_x), ruby_y, _merged)
                            elif current_time >= _rs:
                                _rd = _re - _rs
                                _rr = (
                                    min(1.0, (current_time - _rs) / _rd)
                                    if _rd > 0
                                    else 1.0
                                )
                                if _rr > 0 and _r_ink_w > 0:
                                    painter.save()
                                    _rww = int(_r_ink_w * _rr)
                                    painter.setClipRect(
                                        QRect(
                                            int(_r_ink_x),
                                            ruby_y - fm_ruby.ascent() - 2,
                                            _rww,
                                            fm_ruby.height() + 4,
                                        )
                                    )
                                    painter.setPen(_rh)
                                    painter.drawText(int(ruby_x), ruby_y, _merged)
                                    painter.restore()
                        # 连词框
                        painter.save()
                        _fc = QColor(base_color)
                        _fc.setAlpha(120)
                        _fp = QPen(_fc, 1.0)
                        _fp.setStyle(Qt.PenStyle.SolidLine)
                        painter.setPen(_fp)
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawRoundedRect(
                            int(ruby_x) - 2,
                            ruby_y - fm_ruby.ascent() - 1,
                            int(ruby_text_w) + 4,
                            fm_ruby.height() + 2,
                            2,
                            2,
                        )
                        painter.restore()
                else:
                    ruby = line.characters[char_pos].ruby
                    if ruby:
                        # 单字符 ruby（per-char 模型）- 渲染剥离 '#' 分组标记
                        _ruby_disp = ruby.text
                        ruby_text_w = fm_ruby.horizontalAdvance(_ruby_disp)
                        ruby_x = curr_x + (char_w - ruby_text_w) // 2
                        ruby_y = int(y_center - main_fm.ascent() - self._ruby_spacing)
                        # Ruby 文本整串墨水边界（含所有假名）
                        _r_ink_off, _r_ink_w = _char_ruby_ink.get(char_pos, (0, ruby_text_w))
                        _r_ink_x = ruby_x + _r_ink_off
                        painter.setFont(font_ruby)
                        painter.setPen(base_color)
                        painter.drawText(int(ruby_x), ruby_y, _ruby_disp)
                        # Wipe — 优先用 part 锚点轴分段；缺锚点回退旧整段线性
                        ruby_highlight = _char_singer_colors.get(
                            char_pos, highlight_color
                        )
                        _r_anchors = _char_part_anchors.get(char_pos)
                        if _r_anchors is not None and len(_r_anchors) >= 2:
                            _i, _sr, _n = _anchor_segment(_r_anchors, current_time)
                            if _n > 0:
                                # 计算整体进度比例 _ratio ∈ [0,1]，再乘 ink 宽度。
                                # parts 数与段数 N 匹配则按 part 实际 advance 占比，否则等分。
                                _parts = ruby.parts if ruby.parts else []
                                if len(_parts) == _n and _n > 0:
                                    _part_ws = [
                                        fm_ruby.horizontalAdvance(p.text)
                                        for p in _parts
                                    ]
                                    _total_pw = sum(_part_ws)
                                    if _total_pw > 0:
                                        _cum = sum(_part_ws[:_i])
                                        _local = _part_ws[_i] * _sr
                                        _ratio = (_cum + _local) / _total_pw
                                    else:
                                        _ratio = (_i + _sr) / _n
                                else:
                                    _ratio = (_i + _sr) / _n
                                r_wipe_w = int(_r_ink_w * _ratio) if _r_ink_w > 0 else 0
                                if _ratio >= 1.0:
                                    painter.setPen(ruby_highlight)
                                    painter.drawText(int(ruby_x), ruby_y, _ruby_disp)
                                elif r_wipe_w > 0:
                                    painter.save()
                                    painter.setClipRect(
                                        QRect(
                                            int(_r_ink_x),
                                            ruby_y - fm_ruby.ascent() - 2,
                                            r_wipe_w,
                                            fm_ruby.height() + 4,
                                        )
                                    )
                                    painter.setPen(ruby_highlight)
                                    painter.drawText(int(ruby_x), ruby_y, _ruby_disp)
                                    painter.restore()
                        else:
                            ruby_wipe_st = char_wipe_times.get(char_pos)
                            ruby_st = ruby_wipe_st[0] if ruby_wipe_st else None
                            if ruby_st is not None:
                                ruby_wipe_et = char_wipe_times.get(char_pos)
                                ruby_et = ruby_wipe_et[1] if ruby_wipe_et else ruby_st + 300
                                if current_time >= ruby_et:
                                    painter.setPen(ruby_highlight)
                                    painter.drawText(int(ruby_x), ruby_y, _ruby_disp)
                                elif current_time >= ruby_st:
                                    r_dur = ruby_et - ruby_st
                                    r_ratio = (
                                        min(1.0, (current_time - ruby_st) / r_dur)
                                        if r_dur > 0
                                        else 1.0
                                    )
                                    if r_ratio > 0 and _r_ink_w > 0:
                                        painter.save()
                                        r_wipe_w = int(_r_ink_w * r_ratio)
                                        painter.setClipRect(
                                            QRect(
                                                int(_r_ink_x),
                                                ruby_y - fm_ruby.ascent() - 2,
                                                r_wipe_w,
                                                fm_ruby.height() + 4,
                                            )
                                        )
                                        painter.setPen(ruby_highlight)
                                        painter.drawText(int(ruby_x), ruby_y, _ruby_disp)
                                        painter.restore()

                # 主文字 — 基于 checkpoint 的逐字 wipe
                painter.setFont(main_font)
                # 使用 per-char singer 颜色（如果该字符有不同的演唱者）
                char_highlight = _char_singer_colors.get(char_pos, highlight_color)

                # 字符在 char_w 宽度内居中（与 ruby 对齐）
                char_text_w = main_fm.horizontalAdvance(ch)
                char_draw_x = curr_x + (char_w - char_text_w) // 2

                if char_pos in char_wipe_times:
                    char_time, next_time = char_wipe_times[char_pos]

                    # 决定 wipe ratio 来源：
                    # 1) 组总轴（连词组且组锚点足够）→ 组 ratio 映射回该字符像素区间
                    # 2) 字符 part 锚点（check_count>=2 且打过轴）→ 该字符 ratio
                    # 3) 否则 → 旧的整字线性
                    _grp_leader = _ci_to_leader.get(char_pos)
                    if _grp_leader is not None:
                        _g_anchors = _group_anchors[_grp_leader]
                        _g_total_w = _group_char_total_w[_grp_leader]
                        _g_offset = _group_char_pixel_starts[_grp_leader][char_pos]
                        _g_ratio = _anchor_ratio(_g_anchors, current_time)
                        # 组累计已 wipe 像素，clip 到 [0, char_w]
                        _wiped_total_px = _g_total_w * _g_ratio
                        local_wipe_px = _wiped_total_px - _g_offset
                        if local_wipe_px <= 0:
                            wipe_ratio = 0.0
                        elif local_wipe_px >= char_w:
                            wipe_ratio = 1.0
                        else:
                            wipe_ratio = local_wipe_px / char_w
                    elif char_pos in _char_part_anchors:
                        wipe_ratio = _anchor_ratio(
                            _char_part_anchors[char_pos], current_time
                        )
                    else:
                        if current_time >= next_time:
                            wipe_ratio = 1.0
                        elif current_time >= char_time:
                            duration = next_time - char_time
                            wipe_ratio = (
                                min(1.0, (current_time - char_time) / duration)
                                if duration > 0
                                else 1.0
                            )
                        else:
                            wipe_ratio = 0.0

                    if wipe_ratio >= 1.0:
                        # 已唱完 → 全高亮
                        painter.setPen(char_highlight)
                        painter.drawText(int(char_draw_x), int(y_center), ch)
                    elif wipe_ratio > 0.0:
                        # 正在唱 → wipe 渐变
                        painter.setPen(base_color)
                        painter.drawText(int(char_draw_x), int(y_center), ch)

                        # 按字形墨水（ink）边界裁剪，而非 advance box：
                        # - 起点 = char_draw_x + ink_left（字形真正起墨像素列）
                        # - 终点 = 起点 + ink_width × ratio（字形墨水终止像素列）
                        # 这样 wipe 不会扫过字符左右两侧的透明侧 bearings；
                        # 句尾扩展区（_esw）只用于放置 marker，不再参与 wipe。
                        ink_w = _char_ink_widths[char_pos]
                        if ink_w > 0:
                            ink_off = _char_ink_offsets[char_pos]
                            painter.save()
                            wipe_w = int(ink_w * wipe_ratio)
                            clip_rect = QRect(
                                int(char_draw_x + ink_off),
                                int(y_center - main_fm.ascent() - 5),
                                wipe_w,
                                main_fm.height() + 10,
                            )
                            painter.setClipRect(clip_rect)
                            painter.setPen(char_highlight)
                            painter.drawText(int(char_draw_x), int(y_center), ch)
                            painter.restore()
                        # ink_w == 0（空格/全角空格/NBSP/Tab 等空白字符）：
                        # 没有可见墨水，跳过 clip 绘制，wipe 期间保持 base_color 即可。
                    else:
                        # 未唱 → 基色
                        painter.setPen(base_color)
                        painter.drawText(int(char_draw_x), int(y_center), ch)
                else:
                    # 不在任何字符组内 → 基色
                    painter.setPen(base_color)
                    painter.drawText(int(char_draw_x), int(y_center), ch)

                # 当前打轴位置指示线
                if is_current and char_pos == self._current_char_idx:
                    _esw = _end_sentence_w.get(char_pos, 0)
                    painter.setPen(highlight_color)
                    painter.drawLine(
                        int(curr_x),
                        int(y_center + main_fm.descent() + 2),
                        int(curr_x + char_w + _esw),
                        int(y_center + main_fm.descent() + 2),
                    )

                # Checkpoint 标记（逐 checkpoint 绘制）
                # 句尾marker独立于普通marker，绘制在字符右侧扩展区域
                ch_obj = line.characters[char_pos]
                if ch_obj.total_timing_points > 0:
                    painter.setFont(font_checkpoint)

                    # 普通markers（不含句尾marker）
                    regular_markers = []
                    for cp_idx in range(ch_obj.check_count):
                        has_timed = cp_idx < len(ch_obj.global_timestamps)
                        if cp_idx == 0:
                            marker_char = self._checkpoint_markers["cp_first_timed"] if has_timed else self._checkpoint_markers["cp_first_empty"]
                        else:
                            marker_char = self._checkpoint_markers["cp_multi_timed"] if has_timed else self._checkpoint_markers["cp_multi_empty"]
                        regular_markers.append((cp_idx, marker_char, has_timed))

                    # 左对齐排列普通marker（在原始字符宽度内）
                    mx = curr_x
                    marker_y = int(y_center + main_fm.descent() + 14)

                    for cp_idx, marker_char, has_timed in regular_markers:
                        is_selected = (
                            ch_obj.selected_checkpoint_idx == cp_idx
                        )
                        if is_selected:
                            color = _char_singer_colors.get(char_pos, highlight_color)
                        elif not has_timed:
                            color = theme.karaoke_text_current
                        else:
                            color = base_color

                        mw = fm_checkpoint.horizontalAdvance(marker_char)

                        painter.setPen(color)
                        painter.drawText(int(mx), marker_y, marker_char)

                        marker_rect = QRect(
                            int(mx),
                            marker_y - fm_checkpoint.ascent(),
                            int(mw),
                            fm_checkpoint.height(),
                        )
                        self._checkpoint_hitboxes.append(
                            (marker_rect, idx, char_pos, cp_idx)
                        )

                        mx += mw

                    # 句尾marker：独立绘制在字符右侧扩展区域
                    if ch_obj.is_sentence_end:
                        se_cp_idx = ch_obj.check_count
                        has_timed = ch_obj.global_sentence_end_ts is not None
                        marker_char = self._checkpoint_markers["cp_sentence_end_timed"] if has_timed else self._checkpoint_markers["cp_sentence_end_empty"]

                        is_selected = (
                            ch_obj.selected_checkpoint_idx == se_cp_idx
                        )
                        if is_selected:
                            color = _char_singer_colors.get(char_pos, highlight_color)
                        elif not has_timed:
                            color = theme.karaoke_text_current
                        else:
                            color = base_color

                        # 扩展区域：字符右侧，宽度为半字符宽
                        se_area_x = curr_x + char_w
                        se_area_w = _end_sentence_w.get(char_pos, 0)

                        painter.setPen(color)
                        painter.drawText(int(se_area_x), marker_y, marker_char)

                        # hitbox覆盖整个扩展区域（高度与字符区域一致）
                        se_rect = QRect(
                            int(se_area_x),
                            _rect_top,
                            int(se_area_w),
                            _rect_height,
                        )
                        self._checkpoint_hitboxes.append(
                            (se_rect, idx, char_pos, se_cp_idx)
                        )

                curr_x += char_w + _end_sentence_w.get(char_pos, 0)
