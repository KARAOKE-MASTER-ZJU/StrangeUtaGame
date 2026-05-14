"""设置界面 — RhythmicaLyrics 风格层级设定系统。

使用 qfluentwidgets 的 SettingCardGroup + ExpandLayout 实现分组卡片布局。
所有设置通过 AppSettings 统一管理，默认保存到程序所在目录的 config.json。

本模块仅包含 ``SettingsInterface`` 主类。周边组件已拆分到：
- ``app_settings``   : AppSettings / _parse_rl_dictionary
- ``cards``          : SpinSettingCard / DoubleSpinSettingCard / SwitchSettingCard /
                       ComboSettingCard / BrowseSettingCard / ShortcutSettingCard
- ``dictionary_dialog`` : DictionaryEditDialog
- ``nicokara_dialog``   : NicokaraTagsDialog
- ``calibration_dialog``: CalibrationDialog / CalibrationCanvas

为保留历史 import 路径（``from ...settings.settings_interface import AppSettings``），
本模块对 AppSettings 等符号进行 re-export。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QWidget,
)
from qfluentwidgets import (
    ExpandLayout,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SettingCard,
    SettingCardGroup,
)

from .app_settings import AppSettings, _parse_rl_dictionary
from .calibration_dialog import CalibrationCanvas, CalibrationDialog
from .checkpoint_marker_dialog import CheckpointMarkerDialog
from .cards import (
    BrowseSettingCard,
    ComboSettingCard,
    DoubleSpinSettingCard,
    MultiBoolSettingCard,
    MultiCheckSettingCard,
    ShortcutSettingCard,
    SpinSettingCard,
    SwitchSettingCard,
)
from .dictionary_dialog import DictionaryEditDialog
from .nicokara_dialog import NicokaraTagsDialog

__all__ = [
    "SettingsInterface",
    # re-exports for backward compatibility
    "AppSettings",
    "DictionaryEditDialog",
    "NicokaraTagsDialog",
    "CalibrationDialog",
    "CalibrationCanvas",
    "SpinSettingCard",
    "DoubleSpinSettingCard",
    "SwitchSettingCard",
    "ComboSettingCard",
    "BrowseSettingCard",
    "ShortcutSettingCard",
    "_parse_rl_dictionary",
]

class SettingsInterface(ScrollArea):
    """设置界面 — RhythmicaLyrics 风格分组卡片布局

    分组结构：
    1. 演奏控制 — 快进/快退量、默认音量/速度
    2. 打轴设定 — 偏移量、速度补正、预览行数
    3. Auto Check — 各字符类型的开关
    4. 界面设定 — 主题、字体大小
    5. 导出设定 — 默认格式、导出目录
    6. 快捷键
    7. 关于
    """

    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = None
        self._settings = AppSettings()
        self._calibration_dialog = None

        # 自动保存防抖定时器
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.setInterval(500)
        self._auto_save_timer.timeout.connect(self._do_auto_save)
        self._loading_settings = False  # 防止加载时触发自动保存

        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self._init_ui()
        self._load_current_settings()
        self._connect_auto_save_signals()

        # ScrollArea 配置
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setObjectName("settingInterface")

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store

    def _connect_auto_save_signals(self):
        """将所有设置卡片的变更信号连接到自动保存。"""
        # 遍历所有属性，按类型连接信号
        for attr_name in dir(self):
            card = getattr(self, attr_name, None)
            if isinstance(card, SpinSettingCard):
                card.value_changed.connect(self._schedule_auto_save)
            elif isinstance(card, DoubleSpinSettingCard):
                card.value_changed.connect(self._schedule_auto_save)
            elif isinstance(card, SwitchSettingCard):
                card.checked_changed.connect(self._schedule_auto_save)
            elif isinstance(card, ComboSettingCard):
                card.index_changed.connect(self._schedule_auto_save)
            elif isinstance(card, BrowseSettingCard):
                card.path_changed.connect(self._schedule_auto_save)
        # 快捷键卡片存在嵌套 dict self._shortcut_cards[mode][action]，
        # dir(self) 无法遍历到，改由 _init_shortcut_group 创建时直接 connect。

    def _schedule_auto_save(self, *_args):
        """防抖调度自动保存（500ms 内无新操作则保存）。"""
        if self._loading_settings:
            return
        self._auto_save_timer.start()

    def _do_auto_save(self):
        """执行自动保存：收集设置 → 保存到磁盘 → 通知变更。

        #4：若快捷键冲突自动解除，逐条以 InfoBar 提示占用方功能名。
        #11：切换页面时 flush 未完成的 debounce，确保设置立即固化。
        """
        # #3 直接在快捷键变更时判断并落盘
        self._collect_settings()
        self._settings.save()
        self.settings_changed.emit()
        if self._store is not None:
            self._store.notify("settings")

        # 同步主题管理器
        self._apply_theme_setting()

    def _apply_theme_setting(self):
        """将设置中的主题同步到主题管理器"""
        from strange_uta_game.frontend.theme import theme, ThemeMode

        theme_value = self._settings.get("ui.theme", "auto")
        mode_map = {
            "light": ThemeMode.LIGHT,
            "dark": ThemeMode.DARK,
            "auto": ThemeMode.AUTO,
        }
        theme.mode = mode_map.get(theme_value, ThemeMode.AUTO)

    def _on_shortcut_changed(self, changed_card: ShortcutSettingCard, new_value: str):
        """快捷键卡片变更事件。处理冲突判断并保存。

        冲突规则：同模式 + 同按键 + 同触发类型 → 冲突；
        同按键不同触发类型 → 允许。
        特殊：tag_now（打轴键）使用 press/release 语义，同时占用短按和长按。
        """
        if self._loading_settings:
            return

        # 解析新值中的 (key, trigger) 对
        new_pairs: list[tuple[str, str]] = []
        for k in new_value.split(","):
            k = k.strip()
            if not k:
                continue
            if ":" in k:
                key_part, trigger_part = k.rsplit(":", 1)
                new_pairs.append((key_part.strip().upper(), trigger_part.strip().lower()))
            else:
                new_pairs.append((k.upper(), "short"))

        for mode_key, mode_label in self._SHORTCUT_MODES:
            # 只检查 changed_card 参与的模式
            mode_actions = self._shortcut_cards[mode_key]
            if not any(card is changed_card for card in mode_actions.values()):
                continue

            # 在此模式内检查冲突
            for action_key, card in mode_actions.items():
                if card is changed_card:
                    continue

                other_pairs = card.all_keys_with_trigger()
                # tag_now 使用 press/release 语义，同时占用短按和长按
                if action_key == "tag_now":
                    expanded: list[tuple[str, str]] = []
                    for k, _ in other_pairs:
                        expanded.append((k, "short"))
                        expanded.append((k, "long"))
                    other_pairs = expanded

                for new_key, new_trigger in new_pairs:
                    if not new_key:
                        continue
                    for other_key, other_trigger in other_pairs:
                        if new_key == other_key and new_trigger == other_trigger:
                            # 冲突！同键+同触发类型
                            # #2 恢复原按键
                            for btn in [changed_card.btn_key1, changed_card.btn_key2]:
                                if btn.get_key().strip().upper() == new_key and btn.get_trigger_type() == new_trigger:
                                    btn.restore_original_key()

                            # 弹出提示
                            action_titles = {a[0]: a[2] for a in self._SHORTCUT_ACTIONS}
                            trigger_label = "长按" if new_trigger == "long" else "短按"
                            InfoBar.warning(
                                title="快捷键冲突",
                                content=f"[{mode_label}]「{action_titles[action_key]}」已占用{trigger_label}按键 {new_key}",
                                orient=Qt.Orientation.Horizontal,
                                isClosable=True,
                                position=InfoBarPosition.TOP,
                                duration=4000,
                                parent=self,
                            )
                            return  # 冲突已处理，不保存

        # 无冲突，直接保存
        self._schedule_auto_save()

    def _init_ui(self):
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(60, 20, 60, 20)

        self._init_playback_group()
        self._init_timing_group()
        self._init_calibration_group()
        self._init_auto_save_group()
        self._init_auto_check_group()
        self._init_dictionary_group()
        self._init_ui_group()
        self._init_export_group()
        self._init_shortcut_group()
        self._init_buttons()
        self._init_about_group()

    # ── 演奏控制 ──

    def _init_playback_group(self):
        self.playback_group = SettingCardGroup("演奏控制", self.scrollWidget)

        self.card_volume = SpinSettingCard(
            FIF.VOLUME,
            "默认音量",
            "音频加载后的初始音量",
            min_val=0,
            max_val=100,
            suffix=" %",
            parent=self.playback_group,
        )
        self.card_speed = DoubleSpinSettingCard(
            FIF.SPEED_HIGH,
            "默认速度",
            "音频加载后的初始播放速度",
            min_val=0.2,
            max_val=2.0,
            step=0.1,
            suffix=" x",
            parent=self.playback_group,
        )
        self.card_fast_forward = SpinSettingCard(
            FIF.CHEVRON_RIGHT,
            "快进量",
            "按下快进键跳过的时间",
            min_val=1000,
            max_val=30000,
            step=1000,
            suffix=" ms",
            parent=self.playback_group,
        )
        self.card_rewind = SpinSettingCard(
            FIF.LEFT_ARROW,
            "快退量",
            "按下快退键后退的时间",
            min_val=1000,
            max_val=30000,
            step=1000,
            suffix=" ms",
            parent=self.playback_group,
        )
        self.card_auto_play = SwitchSettingCard(
            FIF.PLAY,
            "自动播放",
            "加载音频文件后自动开始播放",
            parent=self.playback_group,
        )
        self.card_jump_before = SpinSettingCard(
            FIF.HISTORY,
            "删除节奏点跳转提前量",
            "删除节奏点时跳转到该时间戳前的毫秒数",
            min_val=0,
            max_val=30000,
            step=500,
            suffix=" ms",
            parent=self.playback_group,
        )

        self.playback_group.addSettingCard(self.card_volume)
        self.playback_group.addSettingCard(self.card_speed)
        self.playback_group.addSettingCard(self.card_fast_forward)
        self.playback_group.addSettingCard(self.card_rewind)
        self.playback_group.addSettingCard(self.card_auto_play)
        self.playback_group.addSettingCard(self.card_jump_before)
        self.expandLayout.addWidget(self.playback_group)

    # ── 打轴设定 ──

    def _init_timing_group(self):
        self.timing_group = SettingCardGroup("打轴设定", self.scrollWidget)

        self.card_offset = SpinSettingCard(
            FIF.DATE_TIME,
            "按键补偿",
            "建议用下方的offset校正来矫正，用于设备引起的反应延迟（负值=提前，正值=延后）",
            min_val=-5000,
            max_val=5000,
            step=10,
            suffix=" ms",
            parent=self.timing_group,
        )
        self.card_speed_correction = SpinSettingCard(
            FIF.SPEED_MEDIUM,
            "速度补正",
            "打轴时间戳的速度修正系数",
            min_val=50,
            max_val=200,
            step=5,
            suffix=" %",
            parent=self.timing_group,
        )
        self.card_export_offset = SpinSettingCard(
            FIF.HISTORY,
            "全局偏移",
            "全局偏移（原RL内默认为-230补偿）,用于控制本软件内整体轴时间偏移（毫秒），（负值=提前，正值=延后）",
            min_val=-5000,
            max_val=5000,
            step=10,
            suffix=" ms",
            parent=self.timing_group,
        )
        self.card_timing_step = SpinSettingCard(
            FIF.UP,
            "微调时间戳步长",
            "Alt+↑/Alt+↓ 微调选中节奏点时间戳的步长",
            min_val=1,
            max_val=500,
            step=1,
            suffix=" ms",
            parent=self.timing_group,
        )
        self.card_disable_click_jump = SwitchSettingCard(
            FIF.CLOSE,
            "禁用单击跳转",
            "关闭单击字符/节奏点延迟后跳转到目标行的功能（双击跳转不受影响）",
            parent=self.timing_group,
        )

        self.timing_group.addSettingCard(self.card_offset)
        self.timing_group.addSettingCard(self.card_speed_correction)
        self.timing_group.addSettingCard(self.card_export_offset)
        self.timing_group.addSettingCard(self.card_timing_step)
        self.timing_group.addSettingCard(self.card_disable_click_jump)
        self.expandLayout.addWidget(self.timing_group)

    def _init_calibration_group(self):
        """Offset 校准。"""
        self.calibration_group = SettingCardGroup("Offset 校准", self.scrollWidget)

        cal_card = SettingCard(
            FIF.SPEED_HIGH,
            "节拍器校准",
            "打开校准弹窗，跟随节拍器按空格键测量 Offset",
            self.calibration_group,
        )

        self.btn_cal_open = PushButton("开始校准", cal_card)
        self.btn_cal_open.setFont(QFont("Microsoft YaHei", 10))
        self.btn_cal_open.clicked.connect(self._open_calibration_dialog)

        cal_card.hBoxLayout.addWidget(self.btn_cal_open, 0, Qt.AlignmentFlag.AlignRight)
        cal_card.hBoxLayout.addSpacing(16)

        self.calibration_group.addSettingCard(cal_card)
        self.expandLayout.addWidget(self.calibration_group)

    def _open_calibration_dialog(self):
        self._calibration_dialog = CalibrationDialog(self)
        self._calibration_dialog.exec()
        # 安全网：无论对话框如何关闭，确保节拍器已停止
        if self._calibration_dialog is not None:
            self._calibration_dialog._stop_metronome()
        self._calibration_dialog = None

    # ── 自动保存 ──

    def _init_auto_save_group(self):
        self.auto_save_group = SettingCardGroup("自动保存", self.scrollWidget)

        self.card_auto_save_enabled = SwitchSettingCard(
            FIF.SAVE,
            "启用定时自动保存",
            "定时将项目保存为临时文件，防止闪退丢失数据",
            parent=self.auto_save_group,
        )
        self.card_auto_save_interval = SpinSettingCard(
            FIF.HISTORY,
            "自动保存间隔",
            "每隔多少分钟自动保存一次（1~60分钟）",
            min_val=1,
            max_val=60,
            step=1,
            suffix=" 分钟",
            parent=self.auto_save_group,
        )

        self.auto_save_group.addSettingCard(self.card_auto_save_enabled)
        self.auto_save_group.addSettingCard(self.card_auto_save_interval)
        self.expandLayout.addWidget(self.auto_save_group)

    def keyPressEvent(self, a0: QKeyEvent | None):
        """设置界面按键事件。"""
        super().keyPressEvent(a0)

    def hideEvent(self, a0):
        """设置界面隐藏时关闭校准弹窗并释放资源。

        #11：切换页面即保存——若 500ms debounce 还未到期，立即 flush。
        """
        if self._calibration_dialog is not None:
            self._calibration_dialog.close()
        # flush 未完成的自动保存
        try:
            if self._auto_save_timer.isActive():
                self._auto_save_timer.stop()
                self._do_auto_save()
        except Exception:
            pass
        super().hideEvent(a0)

    # ── Auto Check ──

    def _init_auto_check_group(self):
        self.auto_check_group = SettingCardGroup("Auto Check", self.scrollWidget)

        # 节奏点字符类型
        self.card_checkpoint_chars = MultiBoolSettingCard(
            FIF.MUSIC,
            "节奏点字符类型",
            "选择哪些字符类型自动生成节奏点",
            items=[
                ("hiragana", "ひらがな（平假名）"),
                ("katakana", "カタカナ（片假名）"),
                ("kanji", "漢字（汉字）"),
                ("alphabet", "アルファベット（英文字母）"),
                ("digit", "数字"),
                ("symbol", "記号（符号）"),
                ("space", "空格"),
            ],
            parent=self.auto_check_group,
        )
        self.card_checkpoint_chars.selection_changed.connect(
            lambda vals: self._on_multi_bool_changed("auto_check", vals)
        )

        # check 规则
        self.card_check_rules = MultiBoolSettingCard(
            FIF.SETTING,
            "check 规则",
            "选择启用哪些自动节奏点规则",
            items=[
                ("check_n", "「ん/ン」check"),
                ("check_sokuon", "促音check"),
                ("small_kana", "小写假名check"),
                ("check_parentheses", "括号内文字check"),
                ("checkpoint_on_punctuation", "标点参与节奏点"),
                ("check_empty_lines", "空行check"),
                ("check_line_start", "行首check"),
                ("check_line_end", "行尾check"),
                ("space_after_japanese", "日语后空格check"),
                ("space_after_alphabet", "字母后空格check"),
                ("space_after_symbol", "符号数字后空格check"),
                ("space_as_line_end", "空格视为句尾"),
                ("check_english_word_end", "英文单词结尾句尾"),
            ],
            parent=self.auto_check_group,
        )
        self.card_check_rules.selection_changed.connect(
            lambda vals: self._on_multi_bool_changed("auto_check", vals)
        )

        # 自动行为
        self.card_auto_on_load = SwitchSettingCard(
            FIF.ACCEPT,
            "读取时自动check",
            "导入文本后自动执行check分析",
            parent=self.auto_check_group,
        )

        # 自动删除注音
        self.card_delete_ruby_types = MultiCheckSettingCard(
            FIF.DELETE,
            "自动删除注音",
            "自动注音完成后，自动删除指定类型的注音",
            options=[
                ("hiragana", "ひらがな（平假名）"),
                ("katakana", "カタカナ（片假名）"),
                ("kanji", "漢字（汉字）"),
                ("alphabet", "アルファベット（英文字母）"),
                ("number", "数字"),
                ("symbol", "記号（符号）"),
                ("long_vowel", "長音符号（ー、～等）"),
                ("sokuon", "促音（っ/ッ）"),
                ("other", "その他（♪等特殊符号）"),
                ("space", "空格"),
            ],
            parent=self.auto_check_group,
        )
        self.card_delete_ruby_types.selection_changed.connect(
            lambda types: self._on_multi_bool_changed("auto_check", {"delete_ruby_types": types})
        )

        self.auto_check_group.addSettingCard(self.card_checkpoint_chars)
        self.auto_check_group.addSettingCard(self.card_check_rules)
        self.auto_check_group.addSettingCard(self.card_auto_on_load)
        self.auto_check_group.addSettingCard(self.card_delete_ruby_types)
        self.expandLayout.addWidget(self.auto_check_group)

    def _on_multi_bool_changed(self, prefix: str, vals: dict):
        """MultiBoolSettingCard 选择变更回调，批量写入 config 并保存。"""
        for key, val in vals.items():
            self._settings.set(f"{prefix}.{key}", val)
        self._settings.save()

    # ── 读音词典 ──

    def _init_dictionary_group(self):
        self.dictionary_group = SettingCardGroup("读音词典", self.scrollWidget)

        dict_card = SettingCard(
            FIF.DICTIONARY,
            "自定义读音",
            "固定特定词汇的注音读法（最长匹配优先）",
            self.dictionary_group,
        )
        self.btn_open_dict = PushButton("编辑词典", dict_card)
        self.btn_open_dict.setFont(QFont("Microsoft YaHei", 10))
        self.btn_open_dict.clicked.connect(self._on_open_dictionary)
        dict_card.hBoxLayout.addWidget(
            self.btn_open_dict, 0, Qt.AlignmentFlag.AlignRight
        )
        dict_card.hBoxLayout.addSpacing(16)
        self.dict_card = dict_card

        self.dictionary_group.addSettingCard(self.dict_card)
        self.expandLayout.addWidget(self.dictionary_group)

    def _on_open_dictionary(self):
        entries = self._settings.load_dictionary()
        dialog = DictionaryEditDialog(entries, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_entries = dialog.get_entries()
            self._settings.save_dictionary(new_entries)

    def _on_open_checkpoint_markers(self):
        current = self._settings.get("ui.checkpoint_markers", {})
        dialog = CheckpointMarkerDialog(current, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            markers = dialog.get_markers()
            self._settings.set("ui.checkpoint_markers", markers)
            self._settings.save()
            self.settings_changed.emit()

    # ── 界面设定 ──

    def _init_ui_group(self):
        self.ui_group = SettingCardGroup("界面设定", self.scrollWidget)

        # 主题选择（支持手动切换，Win10 兼容方案）
        self.card_theme = ComboSettingCard(
            FIF.BRUSH,
            "主题",
            "选择界面主题，或设为自动跟随系统切换",
            items=["自动", "浅色", "深色"],
            parent=self.ui_group,
        )
        self.card_font_size = SpinSettingCard(
            FIF.FONT_SIZE,
            "基础字体大小",
            "非当前行的歌词字体像素大小",
            min_val=12,
            max_val=48,
            step=2,
            suffix=" px",
            parent=self.ui_group,
        )
        self.card_current_line_font_size = SpinSettingCard(
            FIF.FONT_SIZE,
            "当前行字体大小",
            "当前高亮行的字体像素大小（放大效果）",
            min_val=12,
            max_val=64,
            step=2,
            suffix=" px",
            parent=self.ui_group,
        )
        self.card_ruby_size = SpinSettingCard(
            FIF.FONT_SIZE,
            "注音字体大小",
            "Ruby注音的字体像素大小",
            min_val=6,
            max_val=24,
            step=1,
            suffix=" px",
            parent=self.ui_group,
        )
        self.card_ruby_spacing = SpinSettingCard(
            FIF.FONT_SIZE,
            "注音与主文字间距",
            "Ruby注音与主文字之间的垂直间距",
            min_val=0,
            max_val=20,
            step=1,
            suffix=" px",
            parent=self.ui_group,
        )
        self.card_cp_size = SpinSettingCard(
            FIF.FONT_SIZE,
            "节奏点标记大小",
            "Checkpoint节奏点标记的字体像素大小",
            min_val=6,
            max_val=20,
            step=1,
            suffix=" px",
            parent=self.ui_group,
        )
        self.card_line_height_factor = DoubleSpinSettingCard(
            FIF.FONT_SIZE,
            "行间距系数",
            "行高 = (当前行字体 + 注音 + 注音间距 + 节奏点)高度 × 系数",
            min_val=0.50,
            max_val=5.00,
            step=0.05,
            decimals=2,
            suffix=" x",
            parent=self.ui_group,
        )
        self.card_alignment_margin = SpinSettingCard(
            FIF.FONT_SIZE,
            "左/右对齐时页边距",
            "左对齐或右对齐时歌词与窗口边缘的间距",
            min_val=0,
            max_val=500,
            step=4,
            suffix=" px",
            parent=self.ui_group,
        )
        self.card_lyrics_alignment = ComboSettingCard(
            FIF.ALIGNMENT,
            "歌词对齐方式",
            "卡拉OK预览中歌词文本的水平对齐方式（左对齐时注意行号区域不被覆盖）",
            items=["左对齐", "居中对齐", "右对齐"],
            parent=self.ui_group,
        )
        self.card_checkpoint_markers = SettingCard(
            FIF.FONT_SIZE,
            "Checkpoint 字符设定",
            "自定义节奏点标记的显示字符（首节奏点 / 后续 / 句尾，已打轴 / 未打轴）",
            self.ui_group,
        )
        self.btn_cp_markers = PushButton("设置字符", self.card_checkpoint_markers)
        self.btn_cp_markers.clicked.connect(self._on_open_checkpoint_markers)
        self.card_checkpoint_markers.hBoxLayout.addWidget(
            self.btn_cp_markers, 0, Qt.AlignmentFlag.AlignRight
        )
        self.card_checkpoint_markers.hBoxLayout.addSpacing(16)

        self.ui_group.addSettingCard(self.card_theme)
        self.ui_group.addSettingCard(self.card_font_size)
        self.ui_group.addSettingCard(self.card_current_line_font_size)
        self.ui_group.addSettingCard(self.card_ruby_size)
        self.ui_group.addSettingCard(self.card_ruby_spacing)
        self.ui_group.addSettingCard(self.card_cp_size)
        self.ui_group.addSettingCard(self.card_line_height_factor)
        self.ui_group.addSettingCard(self.card_alignment_margin)
        self.ui_group.addSettingCard(self.card_lyrics_alignment)
        self.ui_group.addSettingCard(self.card_checkpoint_markers)
        self.expandLayout.addWidget(self.ui_group)

    # ── 导出设定 ──

    def _init_export_group(self):
        self.export_group = SettingCardGroup("导出设定", self.scrollWidget)

        self.card_default_format = ComboSettingCard(
            FIF.SHARE,
            "默认导出格式",
            "导出歌词时的默认文件格式",
            items=[
                "LRC (增强型)",
                "LRC (逐行)",
                "LRC (逐字)",
                "KRA",
                "TXT",
                "SRT",
                "txt2ass",
                "ASS",
                "Nicokara",
                "Nicokara (带注音)",
                "RL 编辑模式",
            ],
            parent=self.export_group,
        )
        self.card_export_dir = BrowseSettingCard(
            FIF.FOLDER,
            "默认导出目录",
            "导出文件的默认保存位置",
            parent=self.export_group,
        )
        self.card_software_compensation = SpinSettingCard(
            FIF.HISTORY,
            "软件导出补偿",
            "导出时给时间戳加上此补偿值（除.sug外的所有格式），负值=提前，正值=延后",
            min_val=-5000,
            max_val=5000,
            step=10,
            suffix=" ms",
            parent=self.export_group,
        )

        self.export_group.addSettingCard(self.card_default_format)
        self.export_group.addSettingCard(self.card_export_dir)
        self.export_group.addSettingCard(self.card_software_compensation)
        self.expandLayout.addWidget(self.export_group)

    # ── 快捷键 ──

    # 统一维护两种模式下的动作元数据，避免 UI/加载/保存三处重复写死。
    # 结构: (action_key, 图标, 标题, 描述, 默认按键(打轴模式), 默认按键(编辑模式), 作用域)
    # 作用域 scope:
    #   "both"         两模式下按键相同，UI 只渲染一次
    #   "timing_only"  仅打轴模式可用，UI 渲染一次并标注【仅打轴】
    #   "edit_only"    仅编辑模式可用，UI 渲染一次并标注【仅编辑】
    #   "split"        两模式下按键不同，UI 渲染两张卡片
    _SHORTCUT_ACTIONS: list[tuple[str, object, str, str, str, str, str, str, str, bool]] = [
        # (key, icon, title, content, default_timing, default_edit, scope, timing_content, edit_content, readonly)
        # — 两模式通用 —
        ("play_pause", FIF.PLAY, "播放/暂停", "切换播放和暂停", "D:short", "D:short", "both", None, None, False),
        ("stop", FIF.PAUSE, "停止", "停止播放", "S:short", "S:short", "both", None, None, False),
        ("speed_down", FIF.SPEED_OFF, "减速", "降低播放速度", "Q:short", "Q:short", "both", None, None, False),
        ("speed_up", FIF.SPEED_HIGH, "加速", "提高播放速度", "W:short", "W:short", "both", None, None, False),
        ("volume_up", FIF.VOLUME, "音量增大", "增大播放音量", "", "", "both", None, None, False),
        ("volume_down", FIF.MUTE, "音量减小", "减小播放音量", "", "", "both", None, None, False),
        ("nav_prev_line", FIF.UP, "上一行", "移动到上一歌词行", "UP:short", "UP:short", "both", None, None, False),
        ("nav_next_line", FIF.DOWN, "下一行", "移动到下一歌词行", "DOWN:short", "DOWN:short", "both", None, None, False),
        ("nav_prev_char", FIF.LEFT_ARROW, "上一字符", "在当前行内移动到上一个字符；若已在首字符则跳到上一行末字符", "LEFT:short", "LEFT:short", "both", None, None, False),
        ("nav_next_char", FIF.RIGHT_ARROW, "下一字符", "在当前行内移动到下一个字符；若已在末字符则跳到下一行首字符", "RIGHT:short", "RIGHT:short", "both", None, None, False),
        ("cycle_checkpoint_prev", FIF.SYNC, "切换字内节奏点（反向）", "在当前字符的多个节奏点之间反向循环切换（Alt+←）", "ALT+LEFT:short", "ALT+LEFT:short", "both", None, None, False),
        ("timestamp_up", FIF.UP, "时间戳+步长", "增加选中节奏点时间戳", "ALT+UP:short", "ALT+UP:short", "both", None, None, False),
        ("timestamp_down", FIF.DOWN, "时间戳-步长", "减少选中节奏点时间戳", "ALT+DOWN:short", "ALT+DOWN:short", "both", None, None, False),
        ("cycle_checkpoint", FIF.SYNC, "切换字内节奏点", "在当前字符的多个节奏点之间循环切换（Alt+→）", "ALT+RIGHT:short", "ALT+RIGHT:short", "both", None, None, False),
        ("edit_ruby", FIF.EDIT, "注音编辑", "编辑当前字符注音", "F2:short", "F2:short", "both", None, None, False),
        ("toggle_word_join", FIF.LINK, "连词", "连词/取消连词", "F3:short", "F3:short", "both", None, None, False),
        # — 仅打轴模式 —
        ("tag_now", FIF.PLAY, "打轴键", "打轴操作的按键【仅打轴模式】", "Space:short", "", "timing_only", None, None, False),
        ("tag_now_extra", FIF.PLAY, "打轴键 Extra", "打轴操作的备用按键【仅打轴模式】", "", "", "timing_only", None, None, False),
        ("seek_back", FIF.LEFT_ARROW, "后退", "后退跳转【仅打轴模式】", "Z:short", "", "timing_only", None, None, False),
        ("seek_forward", FIF.CHEVRON_RIGHT, "前进", "前进跳转【仅打轴模式】", "X:short", "", "timing_only", None, None, False),
        ("delete_timestamp", FIF.DELETE, "删除当前时间戳并回滚", "删除跳转【仅打轴模式】", "Backspace:short", "", "timing_only", None, None, False),
        # — 两模式下按键不同 —
        ("add_checkpoint", FIF.PIN, "增加节奏点", "增加当前字符的节奏点数量", "F5:short", "Space:short", "split", "增加当前字符的节奏点数量（默认 F5）", "增加当前字符的节奏点数量（默认 Space）", False),
        ("remove_checkpoint", FIF.REMOVE, "删除节奏点", "减少当前字符的节奏点数量", "F6:short", "Backspace:short", "split", "减少当前字符的节奏点数量（默认 F6）", "减少当前字符的节奏点数量（默认 Backspace）", False),
        ("toggle_line_end", FIF.TAG, "切换句尾", "切换当前字符的句尾标记", "F4:short", ".:short", "split", "切换当前字符的句尾标记（默认 F4）", "切换当前字符的句尾标记（默认 句号）", False),
        # — 通用工具栏功能 —
        ("bulk_change", FIF.EDIT, "批量变更", "打开批量变更对话框", "CTRL+H:short", "CTRL+H:short", "both", None, None, False),
        ("modify_char", FIF.EDIT, "修改所选字符", "打开修改所选字符对话框", "", "", "both", None, None, False),
        ("insert_guide", FIF.ADD, "插入导唱符", "打开插入导唱符对话框", "", "", "both", None, None, False),
        ("modify_line", FIF.EDIT, "修改选中行", "打开修改选中行对话框", "", "", "both", None, None, False),
        ("analyze_rubies", FIF.SYNC, "注音分析", "自动分析全部注音", "", "", "both", None, None, False),
        ("delete_rubies_by_type", FIF.DELETE, "按类型删除注音", "按类型删除注音对话框", "", "", "both", None, None, False),
        ("set_singer_by_line", FIF.PEOPLE, "按行设置演唱者", "按行批量设置演唱者", "", "", "both", None, None, False),
        ("apply_singer", FIF.PEOPLE, "应用演唱者", "为选中字符设置演唱者", "", "", "both", None, None, False),
        ("timestamps_to_sentence_end", FIF.TAG, "时间戳转句尾", "取消所有节奏点、清除时间戳并标记为句尾", "", "", "both", None, None, False),
        ("quick_export", FIF.SHARE, "快捷导出", "使用默认导出格式快速导出到文件", "", "", "both", None, None, False),
        # — 硬编码按键（仅用于冲突检测，不可编辑） —
        ("undo", FIF.CANCEL, "撤销", "撤销操作", "CTRL+Z:short", "CTRL+Z:short", "both", None, None, True),
        ("redo", FIF.SYNC, "重做", "重做操作", "CTRL+Y:short", "CTRL+Y:short", "both", None, None, True),
        ("save", FIF.SAVE, "保存", "保存项目", "CTRL+S:short", "CTRL+S:short", "both", None, None, True),
        ("paste_lyrics", FIF.PASTE, "粘贴歌词", "粘贴歌词", "CTRL+V:short", "CTRL+V:short", "both", None, None, True),
        ("insert_line_break", FIF.RETURN, "插入换行", "在光标处插入换行", "Enter:short", "Enter:short", "both", None, None, True),
        ("delete_char", FIF.DELETE, "删除字符", "删除选中内容或当前字符", "Delete:short", "Delete:short", "both", None, None, True),
    ]

    # 两种模式的中文标签，供 UI 标题与冲突提示使用
    _SHORTCUT_MODES: list[tuple[str, str]] = [
        ("timing_mode", "打轴模式（音乐播放时）"),
        ("edit_mode", "编辑模式（音乐暂停时）"),
    ]

    def _init_shortcut_group(self):
        """渲染快捷键设置（按作用域合并呈现）。

        #10：两模式下表现一致的快捷键合并为一张卡片（描述中标注作用域）；
        仅单一模式可用的动作标注【仅打轴】/【仅编辑】；
        两模式按键不同的动作（scope=split）保留两张卡片、标注模式。
        后台数据结构仍按 mode_key 区分，保证 config 与引擎侧键位映射不变。
        """
        # self._shortcut_cards[mode_key][action_key] -> ShortcutSettingCard
        self._shortcut_cards: dict[str, dict[str, ShortcutSettingCard]] = {
            mode_key: {} for mode_key, _ in self._SHORTCUT_MODES
        }

        group = SettingCardGroup("快捷键", self.scrollWidget)
        self._shortcut_groups: dict[str, SettingCardGroup] = {}  # 兼容旧引用
        self._shortcut_groups["_merged"] = group

        # #6 定义模式描述颜色 (QSS)
        # 浅色模式下，深蓝色/深绿色/深紫色比较清晰
        # 深色模式下，亮蓝/亮绿/亮紫
        # 这里使用 qfluentwidgets 兼容的颜色，或者直接用 hex
        color_timing = "#0078D4" # 蓝色
        color_edit = "#107C10"   # 绿色
        color_both = "#5C2D91"   # 紫色

        for row in self._SHORTCUT_ACTIONS:
            action_key, icon, title, content, default_timing, default_edit, scope, timing_content, edit_content, readonly = row
            
            # 模式前缀样式处理 (#6)
            def _wrap_title(t, s):
                if s == "both":
                    return f'<span style="color: {color_both}; font-weight: bold;">[通用]</span> {t}'
                if s == "timing_only":
                    return f'<span style="color: {color_timing}; font-weight: bold;">[打轴]</span> {t}'
                if s == "edit_only":
                    return f'<span style="color: {color_edit}; font-weight: bold;">[编辑]</span> {t}'
                if s == "split_timing":
                    return f'<span style="color: {color_timing}; font-weight: bold;">[打轴]</span> {t}'
                if s == "split_edit":
                    return f'<span style="color: {color_edit}; font-weight: bold;">[编辑]</span> {t}'
                return t

            if scope == "both":
                card = ShortcutSettingCard(icon, "", content, default_timing, parent=group)
                card.setTitle(_wrap_title(title, "both"))
                if readonly:
                    card.setReadOnly(True)
                self._shortcut_cards["timing_mode"][action_key] = card
                self._shortcut_cards["edit_mode"][action_key] = card
                group.addSettingCard(card)
                if not readonly:
                    card.value_changed.connect(lambda v, c=card: self._on_shortcut_changed(c, v))
            elif scope == "timing_only":
                card = ShortcutSettingCard(icon, "", content, default_timing, parent=group)
                card.setTitle(_wrap_title(title, "timing_only"))
                if readonly:
                    card.setReadOnly(True)
                self._shortcut_cards["timing_mode"][action_key] = card
                group.addSettingCard(card)
                if not readonly:
                    card.value_changed.connect(lambda v, c=card: self._on_shortcut_changed(c, v))
            elif scope == "edit_only":
                card = ShortcutSettingCard(icon, "", content, default_edit, parent=group)
                card.setTitle(_wrap_title(title, "edit_only"))
                if readonly:
                    card.setReadOnly(True)
                self._shortcut_cards["edit_mode"][action_key] = card
                group.addSettingCard(card)
                if not readonly:
                    card.value_changed.connect(lambda v, c=card: self._on_shortcut_changed(c, v))
            elif scope == "split":
                # #5 使用独立的 content
                t_content = timing_content if timing_content else content
                e_content = edit_content if edit_content else content
                
                card_t = ShortcutSettingCard(icon, "", t_content, default_timing, parent=group)
                card_t.setTitle(_wrap_title(title, "split_timing"))
                if readonly:
                    card_t.setReadOnly(True)
                self._shortcut_cards["timing_mode"][action_key] = card_t
                group.addSettingCard(card_t)
                if not readonly:
                    card_t.value_changed.connect(lambda v, c=card_t: self._on_shortcut_changed(c, v))
                
                card_e = ShortcutSettingCard(icon, "", e_content, default_edit, parent=group)
                card_e.setTitle(_wrap_title(title, "split_edit"))
                if readonly:
                    card_e.setReadOnly(True)
                self._shortcut_cards["edit_mode"][action_key] = card_e
                group.addSettingCard(card_e)
                card_e.value_changed.connect(lambda v, c=card_e: self._on_shortcut_changed(c, v))
        
        self.expandLayout.addWidget(group)

    def _get_all_shortcut_cards(
        self,
    ) -> list[tuple[str, str, str, "ShortcutSettingCard"]]:
        """返回 (模式键, 模式标签, 功能名称, 卡片) 列表。

        注意：scope=both 的卡片在 timing/edit 两个模式下引用同一对象。
        冲突检测仍按模式分桶，使用身份去重避免同一卡片被列两次。
        """
        action_titles = {a[0]: a[2] for a in self._SHORTCUT_ACTIONS}
        result: list[tuple[str, str, str, ShortcutSettingCard]] = []
        for mode_key, mode_label in self._SHORTCUT_MODES:
            for action_key, card in self._shortcut_cards[mode_key].items():
                result.append(
                    (mode_key, mode_label, action_titles[action_key], card)
                )
        return result

    def _resolve_shortcut_conflicts(self) -> list[str]:
        """检测并解决快捷键冲突。

        - 冲突检测 **仅在同一模式内** 进行（#13：打轴/编辑两套独立）。
        - 同按键 + 同触发类型 → 冲突；同按键不同触发类型 → 允许。
        - tag_now（打轴键）使用 press/release 语义，同时占用短按和长按。
        - 冲突提示需包含另一个占用该按键的功能名称（#12）。
        - scope=both 的卡片在两个模式下是同一对象，用 id() 去重避免自冲突。
        """
        conflicts: list[str] = []
        action_titles = {a[0]: a[2] for a in self._SHORTCUT_ACTIONS}
        # 以模式为粒度独立检测冲突
        for mode_key, mode_label in self._SHORTCUT_MODES:
            seen_ids: set[int] = set()
            # (action_key, name, card)
            mode_entries: list[tuple[str, str, ShortcutSettingCard]] = []
            for action_key, card in self._shortcut_cards[mode_key].items():
                if id(card) in seen_ids:
                    continue
                seen_ids.add(id(card))
                name = action_titles.get(action_key, action_key)
                mode_entries.append((action_key, name, card))

            # 冲突检测：同按键 + 同触发类型不能被两个不同的功能占用
            key_owners: dict[tuple[str, str], tuple[str, ShortcutSettingCard]] = {}
            for action_key, name, card in mode_entries:
                pairs = card.all_keys_with_trigger()
                # tag_now 使用 press/release 语义，同时占用短按和长按
                if action_key == "tag_now":
                    expanded: list[tuple[str, str]] = []
                    for k, _ in pairs:
                        expanded.append((k, "short"))
                        expanded.append((k, "long"))
                    pairs = expanded

                for key, trigger in pairs:
                    if not key:
                        continue
                    pair = (key, trigger)
                    if pair in key_owners:
                        old_name, old_card = key_owners[pair]
                        old_card.clear_key_by_name(key)
                        trigger_label = "长按" if trigger == "long" else "短按"
                        conflicts.append(
                            f"[{mode_label}]「{name}」与「{old_name}」的{trigger_label}按键 {key} 冲突，"
                            f"已清除「{old_name}」上的该按键"
                        )
                    key_owners[pair] = (name, card)
        return conflicts

    # ── 操作按钮 ──

    def _init_buttons(self):
        btn_widget = QWidget(self.scrollWidget)
        btn_widget.setMinimumHeight(60)  # 按钮高度36 + 底部边距24
        btn_layout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(0, 10, 0, 24)

        self.btn_save = PrimaryPushButton("保存设置", btn_widget)
        self.btn_save.setIcon(FIF.SAVE)
        self.btn_save.setMinimumHeight(36)
        self.btn_save.clicked.connect(self._on_save)
        btn_layout.addWidget(self.btn_save)

        self.btn_reset = PushButton("重置为默认设置", btn_widget)
        self.btn_reset.setIcon(FIF.DELETE)
        self.btn_reset.setMinimumHeight(36)
        self.btn_reset.clicked.connect(self._reset_settings)
        btn_layout.addWidget(self.btn_reset)

        btn_layout.addStretch()
        self.expandLayout.addWidget(btn_widget)

    # ── 关于 ──

    def _init_about_group(self):
        self.about_group = SettingCardGroup("关于", self.scrollWidget)

        about_card = SettingCard(
            FIF.INFO,
            "StrangeUtaGame - 歌词打轴软件",
            "版本 0.3.1 | 由 RhythmicaLyrics 启发",
            self.about_group,
        )
        self.about_group.addSettingCard(about_card)

        link_card = SettingCard(
            FIF.GITHUB,
            "GitHub",
            "https://github.com/Xuan-cc/StrangeUtaGame",
            self.about_group,
        )
        self.about_group.addSettingCard(link_card)

        # 配置文件路径
        self._path_card = SettingCard(
            FIF.FOLDER,
            "配置文件位置",
            str(self._settings._config_path),
            self.about_group,
        )
        btn_open_config = PushButton("打开目录", self._path_card)
        btn_open_config.setFont(QFont("Microsoft YaHei", 10))
        btn_open_config.clicked.connect(self._open_config_dir)
        self._path_card.hBoxLayout.addWidget(
            btn_open_config, 0, Qt.AlignmentFlag.AlignRight
        )
        btn_change_config = PushButton("更改位置", self._path_card)
        btn_change_config.setFont(QFont("Microsoft YaHei", 10))
        btn_change_config.clicked.connect(self._change_config_dir)
        self._path_card.hBoxLayout.addWidget(
            btn_change_config, 0, Qt.AlignmentFlag.AlignRight
        )
        self._path_card.hBoxLayout.addSpacing(16)

        self.about_group.addSettingCard(self._path_card)

        self.expandLayout.addWidget(self.about_group)

    def _open_config_dir(self):
        """打开配置文件所在目录。"""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        config_dir = self._settings._config_path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_dir)))

    def _change_config_dir(self):
        """更改配置文件存储位置。"""
        new_dir = QFileDialog.getExistingDirectory(
            self, "选择配置文件存储目录", str(self._settings._config_path.parent)
        )
        if not new_dir:
            return

        new_dir_path = Path(new_dir)
        program_dir = Path(sys.argv[0]).resolve().parent
        redirect_file = program_dir / ".config_redirect"

        if new_dir_path.resolve() == program_dir.resolve():
            # 回到默认位置 — 删除重定向文件
            try:
                if redirect_file.exists():
                    redirect_file.unlink()
            except Exception:
                pass
        else:
            try:
                redirect_file.write_text(str(new_dir_path), encoding="utf-8")
            except Exception as e:
                InfoBar.error(
                    title="更改失败",
                    content=f"无法写入重定向文件: {e}",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )
                return

        old_path = self._settings._config_path
        new_path = new_dir_path / "config.json"

        # 复制配置到新位置
        if old_path.exists() and old_path != new_path:
            try:
                import shutil

                new_dir_path.mkdir(exist_ok=True)
                shutil.copy2(str(old_path), str(new_path))
                # 同时复制词典和演唱者文件
                for fname in ("dictionary.json", "singers.json"):
                    old_extra = old_path.parent / fname
                    new_extra = new_dir_path / fname
                    if old_extra.exists() and old_extra != new_extra:
                        shutil.copy2(str(old_extra), str(new_extra))
            except Exception as e:
                InfoBar.warning(
                    title="配置复制失败",
                    content=f"请手动复制配置文件: {e}",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )

        self._settings._config_path = new_path
        self._settings._dict_path = new_dir_path / "dictionary.json"
        self._settings._singers_path = new_dir_path / "singers.json"
        self._path_card.setContent(str(new_path))
        InfoBar.success(
            title="配置位置已更改",
            content=f"配置文件将保存到: {new_path}",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=self,
        )

    # ==================== 数据绑定 ====================

    def _load_current_settings(self):
        """从 AppSettings 加载所有设置到 UI 控件"""
        self._loading_settings = True
        try:
            self._load_current_settings_inner()
        finally:
            self._loading_settings = False

    def _load_current_settings_inner(self):
        """实际加载逻辑（被 _loading_settings 守护）"""
        # 演奏控制
        self.card_volume.setValue(self._settings.get("audio.default_volume", 80))
        self.card_speed.setValue(self._settings.get("audio.default_speed", 1.0))
        self.card_fast_forward.setValue(
            self._settings.get("timing.fast_forward_ms", 5000)
        )
        self.card_rewind.setValue(self._settings.get("timing.rewind_ms", 5000))
        self.card_auto_play.setChecked(
            self._settings.get("audio.auto_play_on_load", False)
        )
        self.card_jump_before.setValue(
            self._settings.get("timing.jump_before_ms", 3000)
        )

        # 打轴设定
        self.card_offset.setValue(self._settings.get("timing.tag_offset_ms", 0))
        self.card_speed_correction.setValue(
            self._settings.get("timing.speed_correction", 80)
        )
        self.card_timing_step.setValue(
            self._settings.get("timing.timing_adjust_step_ms", 10)
        )
        self.card_disable_click_jump.setChecked(
            self._settings.get("timing.disable_click_jump", False)
        )

        # Auto Check
        self.card_checkpoint_chars.setValues({
            "hiragana": self._settings.get("auto_check.hiragana", True),
            "katakana": self._settings.get("auto_check.katakana", True),
            "kanji": self._settings.get("auto_check.kanji", True),
            "alphabet": self._settings.get("auto_check.alphabet", False),
            "digit": self._settings.get("auto_check.digit", False),
            "symbol": self._settings.get("auto_check.symbol", False),
            "space": self._settings.get("auto_check.space", False),
        })
        self.card_check_rules.setValues({
            "check_n": self._settings.get("auto_check.check_n", False),
            "check_sokuon": self._settings.get("auto_check.check_sokuon", False),
            "small_kana": self._settings.get("auto_check.small_kana", False),
            "check_parentheses": self._settings.get("auto_check.check_parentheses", True),
            "checkpoint_on_punctuation": self._settings.get("auto_check.checkpoint_on_punctuation", False),
            "check_empty_lines": self._settings.get("auto_check.check_empty_lines", False),
            "check_line_start": self._settings.get("auto_check.check_line_start", False),
            "check_line_end": self._settings.get("auto_check.check_line_end", True),
            "space_after_japanese": self._settings.get("auto_check.space_after_japanese", True),
            "space_after_alphabet": self._settings.get("auto_check.space_after_alphabet", True),
            "space_after_symbol": self._settings.get("auto_check.space_after_symbol", True),
            "space_as_line_end": self._settings.get("auto_check.check_space_as_line_end", True),
            "check_english_word_end": self._settings.get("auto_check.check_english_word_end", True),
        })
        self.card_auto_on_load.setChecked(
            self._settings.get("auto_check.auto_on_load", True)
        )
        self.card_delete_ruby_types.setSelectedValues(
            self._settings.get("auto_check.delete_ruby_types", [])
        )

        # 界面设定
        theme_value = self._settings.get("ui.theme", "auto")
        theme_idx = {"auto": 0, "light": 1, "dark": 2}.get(theme_value, 0)
        self.card_theme.setCurrentIndex(theme_idx)
        self.card_font_size.setValue(self._settings.get("ui.font_size", 18))
        self.card_current_line_font_size.setValue(self._settings.get("ui.current_line_font_size", 22))
        self.card_ruby_size.setValue(self._settings.get("ui.ruby_size", 10))
        self.card_ruby_spacing.setValue(self._settings.get("ui.ruby_spacing", 4))
        self.card_cp_size.setValue(self._settings.get("ui.cp_size", 8))
        self.card_line_height_factor.setValue(self._settings.get("ui.line_height_factor", 1.20))
        self.card_alignment_margin.setValue(self._settings.get("ui.alignment_margin", 168))
        alignment = self._settings.get("ui.lyrics_alignment", "center")
        alignment_idx = {"left": 0, "center": 1, "right": 2}.get(alignment, 1)
        self.card_lyrics_alignment.setCurrentIndex(alignment_idx)

        # 导出设定
        fmt = self._settings.get("export.default_format", "Nicokara (带注音)")
        fmt_idx = {
            "LRC (增强型)": 0,
            "LRC (逐行)": 1,
            "LRC (逐字)": 2,
            "KRA": 3,
            "TXT": 4,
            "SRT": 5,
            "txt2ass": 6,
            "ASS": 7,
            "Nicokara": 8,
            "Nicokara (带注音)": 9,
            "RL 编辑模式": 10,
            "LRC": 0,  # 旧配置兼容
        }.get(fmt, 9)
        self.card_default_format.setCurrentIndex(fmt_idx)
        export_dir = self._settings.get("export.last_export_dir", "")
        if export_dir:
            self.card_export_dir.setText(export_dir)
        self.card_export_offset.setValue(self._settings.get("export.offset_ms", 0))
        self.card_software_compensation.setValue(self._settings.get("export.software_compensation_ms", -90))

        # 自动保存
        self.card_auto_save_enabled.setChecked(
            self._settings.get("auto_save.enabled", True)
        )
        self.card_auto_save_interval.setValue(
            self._settings.get("auto_save.interval_minutes", 5)
        )

        # 快捷键（双模式）
        for mode_key, _ in self._SHORTCUT_MODES:
            for row in self._SHORTCUT_ACTIONS:
                action_key = row[0]
                default_timing = row[4]
                default_edit = row[5]
                default_key = default_timing if mode_key == "timing_mode" else default_edit
                card = self._shortcut_cards[mode_key].get(action_key)
                if card is None:
                    # scope 限制此动作不在该模式下出现（如 timing_only/edit_only）
                    continue
                
                # 双模式合一的卡片不需要重复赋值
                # (但这步目前是幂等的，不优化也没关系)
                
                value = self._settings.get(
                    f"shortcuts.{mode_key}.{action_key}", default_key
                )
                card.setValue(value)

        # 应用主题设置
        self._apply_theme_setting()

    def _collect_settings(self):
        """从 UI 控件收集所有设置并写入 AppSettings"""
        # 演奏控制
        self._settings.set("audio.default_volume", self.card_volume.value())
        self._settings.set("audio.default_speed", self.card_speed.value())
        self._settings.set("audio.auto_play_on_load", self.card_auto_play.isChecked())
        self._settings.set("timing.fast_forward_ms", self.card_fast_forward.value())
        self._settings.set("timing.rewind_ms", self.card_rewind.value())
        self._settings.set("timing.jump_before_ms", self.card_jump_before.value())

        # 打轴设定
        self._settings.set("timing.tag_offset_ms", self.card_offset.value())
        self._settings.set(
            "timing.speed_correction", self.card_speed_correction.value()
        )
        self._settings.set(
            "timing.timing_adjust_step_ms", self.card_timing_step.value()
        )
        self._settings.set(
            "timing.disable_click_jump", self.card_disable_click_jump.isChecked()
        )

        # Auto Check
        for key, val in self.card_checkpoint_chars.values().items():
            self._settings.set(f"auto_check.{key}", val)
        for key, val in self.card_check_rules.values().items():
            self._settings.set(f"auto_check.{key}", val)
        self._settings.set(
            "auto_check.auto_on_load", self.card_auto_on_load.isChecked()
        )
        self._settings.set(
            "auto_check.delete_ruby_types",
            self.card_delete_ruby_types.selectedValues(),
        )

        # 界面设定
        theme_map = {0: "auto", 1: "light", 2: "dark"}
        self._settings.set("ui.theme", theme_map.get(self.card_theme.currentIndex(), "auto"))
        self._settings.set("ui.font_size", self.card_font_size.value())
        self._settings.set("ui.current_line_font_size", self.card_current_line_font_size.value())
        self._settings.set("ui.ruby_size", self.card_ruby_size.value())
        self._settings.set("ui.ruby_spacing", self.card_ruby_spacing.value())
        self._settings.set("ui.cp_size", self.card_cp_size.value())
        self._settings.set("ui.line_height_factor", self.card_line_height_factor.value())
        self._settings.set("ui.alignment_margin", self.card_alignment_margin.value())
        alignment_map = {0: "left", 1: "center", 2: "right"}
        self._settings.set(
            "ui.lyrics_alignment",
            alignment_map.get(self.card_lyrics_alignment.currentIndex(), "center"),
        )

        # 导出设定
        fmt_map = {
            0: "LRC (增强型)",
            1: "LRC (逐行)",
            2: "LRC (逐字)",
            3: "KRA",
            4: "TXT",
            5: "SRT",
            6: "txt2ass",
            7: "ASS",
            8: "Nicokara",
            9: "Nicokara (带注音)",
            10: "RL 编辑模式",
        }
        self._settings.set(
            "export.default_format",
            fmt_map.get(self.card_default_format.currentIndex(), "Nicokara (带注音)"),
        )
        export_dir = self.card_export_dir.text()
        if export_dir:
            self._settings.set("export.last_export_dir", export_dir)
        self._settings.set("export.offset_ms", self.card_export_offset.value())
        self._settings.set("export.software_compensation_ms", self.card_software_compensation.value())

        # 自动保存
        self._settings.set("auto_save.enabled", self.card_auto_save_enabled.isChecked())
        self._settings.set(
            "auto_save.interval_minutes", self.card_auto_save_interval.value()
        )

        # 快捷键（双模式）
        for mode_key, _ in self._SHORTCUT_MODES:
            for row in self._SHORTCUT_ACTIONS:
                action_key = row[0]
                readonly = row[9] if len(row) > 9 else False
                if readonly:
                    continue  # 跳过只读快捷键
                card = self._shortcut_cards[mode_key].get(action_key)
                if card is None:
                    continue
                self._settings.set(
                    f"shortcuts.{mode_key}.{action_key}", card.value()
                )

    # ==================== 操作 ====================

    def _on_save(self):
        self._collect_settings()
        self._settings.save()
        self.settings_changed.emit()
        if self._store is not None:
            self._store.notify("settings")

        InfoBar.success(
            title="设置已保存",
            content="所有设置已保存到配置文件",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _reset_settings(self):
        from PyQt6.QtWidgets import QMessageBox

        msg = QMessageBox(self)
        msg.setWindowTitle("确认重置")
        msg.setText("确定要将所有设置重置为默认值吗？\n这将覆盖您当前的设置（用户词典和演唱者预设不受影响）。")
        btn_yes = msg.addButton("是", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("否", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_yes)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is btn_yes:
            try:
                if self._settings._config_path.exists():
                    self._settings._config_path.unlink()
                # 保留 dictionary.json 和 singers.json 不删除
                self._settings = AppSettings()
                self._load_current_settings()

                InfoBar.success(
                    title="设置已重置",
                    content="所有设置已恢复为默认值",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
            except Exception as e:
                InfoBar.error(
                    title="重置失败",
                    content=str(e),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )

    def get_settings(self) -> AppSettings:
        return self._settings

    def reload_from_disk(self):
        """从磁盘重新加载配置并刷新 UI 控件。"""
        self._settings.reload()
        self._load_current_settings()
