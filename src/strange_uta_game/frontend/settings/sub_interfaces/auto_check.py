"""AutoCheck 子页面。"""

from __future__ import annotations

from qfluentwidgets import FluentIcon as FIF, SettingCardGroup

from ..cards import MultiBoolSettingCard, MultiCheckSettingCard, SwitchSettingCard
from .base import SubSettingInterface


class AutoCheckSubInterface(SubSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        g = SettingCardGroup("Auto Check", self.scrollWidget)
        self.card_checkpoint_chars = MultiBoolSettingCard(
            FIF.MUSIC, "节奏点字符类型", "选择哪些字符类型自动生成节奏点",
            items=[
                ("hiragana", "ひらがな（平假名）"), ("katakana", "カタカナ（片假名）"),
                ("kanji", "漢字（汉字）"), ("alphabet", "アルファベット（英文字母）"),
                ("digit", "数字"), ("symbol", "記号（符号）"),
                ("space", "空格"),
                ("space_after_japanese", "  ↳日语后空格check"),
                ("space_after_alphabet", "  ↳字母后空格check"),
                ("space_after_symbol", "  ↳符号/数字后空格check"),
            ], parent=g)
        self.card_check_rules = MultiBoolSettingCard(
            FIF.SETTING, "check 规则", "选择启用哪些自动节奏点规则",
            items=[
                ("check_n", "「ん/ン」check"), ("check_sokuon", "促音check"),
                ("check_long_vowel", "长音符号check"), ("small_kana", "小写假名check"),
                ("check_parentheses", "括号内文字check"), ("checkpoint_on_punctuation", "标点参与节奏点"),
                ("check_empty_lines", "空行check"), ("check_line_start", "行首check"),
                ("check_line_end", "行尾check"),
                ("check_space_as_line_end", "空格视为句尾"),
                ("check_english_word_end", "英文单词结尾句尾"),
                ("english_syllable_check", "按音节Check英文单词"),
            ], parent=g)
        self.card_auto_on_load = SwitchSettingCard(FIF.ACCEPT, "读取时自动check",
            "导入文本后自动执行check分析", parent=g)
        self.card_chinese_lyrics_detection = SwitchSettingCard(FIF.LANGUAGE, "中文歌词检测",
            "加载歌词时，若未检测到日文假名则自动切换为中文模式（汉字每字一个节奏点，跳过日文注音）",
            parent=g)
        self.card_delete_ruby_types = MultiCheckSettingCard(
            FIF.DELETE, "自动删除注音", "自动注音完成后，自动删除指定类型的注音",
            options=[
                ("hiragana", "ひらがな（平假名）"),
                ("katakana_hiragana_ruby", "カタカナ（片假名・注音为平假名）"),
                ("katakana_english_ruby", "カタカナ（片假名・注音含有英文）"),
                ("kanji", "漢字（汉字）"), ("alphabet", "アルファベット（英文字母）"),
                ("number", "数字"), ("symbol", "記号（符号）"),
                ("long_vowel", "長音符号（ー、～等）"), ("sokuon", "促音（っ/ッ）"),
                ("other", "その他（♪等特殊符号）"), ("space", "空格"),
            ], parent=g)
        for c in [self.card_checkpoint_chars, self.card_check_rules,
                  self.card_auto_on_load, self.card_chinese_lyrics_detection,
                  self.card_delete_ruby_types]:
            g.addSettingCard(c)
        self.expandLayout.addWidget(g)

    def connect_signals(self):
        self.card_checkpoint_chars.selection_changed.connect(self._notify_changed)
        self.card_check_rules.selection_changed.connect(self._notify_changed)
        self.card_auto_on_load.checked_changed.connect(self._notify_changed)
        self.card_chinese_lyrics_detection.checked_changed.connect(self._notify_changed)
        self.card_delete_ruby_types.selection_changed.connect(self._notify_changed)

    def load_settings(self, s):
        self.card_checkpoint_chars.setValues({
            "hiragana": s.get("auto_check.hiragana", True),
            "katakana": s.get("auto_check.katakana", True),
            "kanji": s.get("auto_check.kanji", True),
            "alphabet": s.get("auto_check.alphabet", False),
            "digit": s.get("auto_check.digit", False),
            "symbol": s.get("auto_check.symbol", False),
            "space": s.get("auto_check.space", False),
            "space_after_japanese": s.get("auto_check.space_after_japanese", True),
            "space_after_alphabet": s.get("auto_check.space_after_alphabet", True),
            "space_after_symbol": s.get("auto_check.space_after_symbol", True),
        })
        self.card_check_rules.setValues({
            "check_n": s.get("auto_check.check_n", True),
            "check_sokuon": s.get("auto_check.check_sokuon", True),
            "check_long_vowel": s.get("auto_check.check_long_vowel", False),
            "small_kana": s.get("auto_check.small_kana", False),
            "check_parentheses": s.get("auto_check.check_parentheses", True),
            "checkpoint_on_punctuation": s.get("auto_check.checkpoint_on_punctuation", False),
            "check_empty_lines": s.get("auto_check.check_empty_lines", False),
            "check_line_start": s.get("auto_check.check_line_start", False),
            "check_line_end": s.get("auto_check.check_line_end", True),
            "check_space_as_line_end": s.get("auto_check.check_space_as_line_end", True),
            "check_english_word_end": s.get("auto_check.check_english_word_end", True),
            "english_syllable_check": s.get("auto_check.english_syllable_check", True),
        })
        self.card_auto_on_load.setChecked(s.get("auto_check.auto_on_load", True))
        self.card_chinese_lyrics_detection.setChecked(s.get("auto_check.chinese_lyrics_detection", True))
        saved_delete_types = s.get("auto_check.delete_ruby_types", [])
        # 向后兼容：将旧的 "katakana" 转换为新的两个子类型并自动更新配置
        if "katakana" in saved_delete_types:
            saved_delete_types.remove("katakana")
            if "katakana_hiragana_ruby" not in saved_delete_types:
                saved_delete_types.append("katakana_hiragana_ruby")
            if "katakana_english_ruby" not in saved_delete_types:
                saved_delete_types.append("katakana_english_ruby")
            s.set("auto_check.delete_ruby_types", saved_delete_types)
            s.save()
        self.card_delete_ruby_types.setSelectedValues(saved_delete_types)

    def collect_settings(self, s):
        for key, val in self.card_checkpoint_chars.values().items():
            s.set(f"auto_check.{key}", val)
        for key, val in self.card_check_rules.values().items():
            s.set(f"auto_check.{key}", val)
        s.set("auto_check.auto_on_load", self.card_auto_on_load.isChecked())
        s.set("auto_check.chinese_lyrics_detection", self.card_chinese_lyrics_detection.isChecked())
        s.set("auto_check.delete_ruby_types", self.card_delete_ruby_types.selectedValues())
