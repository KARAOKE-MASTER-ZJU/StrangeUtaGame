"""文件加载管理器。

处理项目、音频、歌词文件的加载逻辑，包括拖拽和菜单触发。
从 EditorInterface 中提取，保持主界面代码简洁。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from qfluentwidgets import InfoBar, InfoBarPosition

from .lyric_loader import read_lyric_file, parse_lyric_content

if TYPE_CHECKING:
    from ..timing_interface import EditorInterface


class FileLoader:
    """文件加载管理器 — 处理项目/音频/歌词的加载"""

    _AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"}
    _LYRIC_EXTENSIONS = {".lrc", ".txt", ".kra"}
    _PROJECT_EXTENSIONS = {".sug"}

    def __init__(self, editor: "EditorInterface"):
        self._editor = editor

    @property
    def _project(self):
        return self._editor._project

    @property
    def _store(self):
        return self._editor._store

    @property
    def _timing_service(self):
        return self._editor._timing_service

    # ── 拖拽 ──

    def can_accept_drop(self, file_path: str) -> bool:
        """判断文件是否可接受拖拽"""
        ext = Path(file_path).suffix.lower()
        return ext in (self._AUDIO_EXTENSIONS | self._LYRIC_EXTENSIONS | self._PROJECT_EXTENSIONS)

    def handle_drop(self, file_path: str):
        """处理拖拽文件"""
        ext = Path(file_path).suffix.lower()
        if ext in self._AUDIO_EXTENSIONS:
            self._editor.load_audio(file_path)
        elif ext in self._LYRIC_EXTENSIONS:
            self.load_lyrics(file_path)
        elif ext in self._PROJECT_EXTENSIONS:
            self.load_project(file_path)

    # ── 菜单/按钮触发 ──

    def prompt_load_project(self):
        """弹出文件选择框加载项目"""
        if not self.check_unsaved_changes():
            return
        path, _ = QFileDialog.getOpenFileName(
            self._editor, "打开项目", "",
            "StrangeUtaGame 项目 (*.sug);;所有文件 (*.*)",
        )
        if path:
            self.load_project(path)

    def prompt_load_audio(self):
        """弹出文件选择框加载音频"""
        path, _ = QFileDialog.getOpenFileName(
            self._editor, "选择音频文件", "",
            "音频文件 (*.mp3 *.wav *.flac *.aac *.ogg *.m4a);;所有文件 (*.*)",
        )
        if path:
            self._editor.load_audio(path)

    def prompt_load_lyrics(self):
        """弹出文件选择框加载歌词"""
        if not self._project:
            InfoBar.warning(
                title="无法加载", content="请先创建或打开一个项目",
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=3000,
                parent=self._editor,
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self._editor, "选择歌词文件", "",
            "歌词文件 (*.lrc *.txt *.kra);;所有文件 (*.*)",
        )
        if path:
            self.load_lyrics(path)

    # ── 实际加载逻辑 ──

    def check_unsaved_changes(self) -> bool:
        """检查当前项目是否有未保存内容，提示用户保存。

        Returns:
            True: 可以继续加载新项目
            False: 用户取消了操作
        """
        if not self._project:
            return True

        store = self._store
        has_save_path = store and store.save_path

        if not has_save_path:
            # 临时项目：提示保存
            msg = QMessageBox(self._editor)
            msg.setWindowTitle("保存当前项目")
            msg.setText("当前项目尚未保存，是否保存？")
            btn_save = msg.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
            btn_discard = msg.addButton("放弃", QMessageBox.ButtonRole.DestructiveRole)
            msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            msg.setDefaultButton(btn_save)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked is btn_save:
                self._editor._on_save()
                return True
            elif clicked is btn_discard:
                return True
            else:  # Cancel
                return False

        return True

    def load_project(self, file_path: str):
        """加载 .sug 项目文件"""
        if not self.check_unsaved_changes():
            return
        try:
            from strange_uta_game.backend.infrastructure.persistence.sug_io import (
                SugProjectParser,
            )

            project = SugProjectParser.load(file_path)
            if self._store:
                self._store._project = project
                self._store._save_path = file_path
                self._store.notify("project")
            else:
                self._editor.set_project(project)
        except Exception as e:
            InfoBar.error(
                title="打开失败", content=str(e),
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=5000,
                parent=self._editor,
            )

    def load_lyrics(self, path: str):
        """加载歌词文件（自动检测格式并解析）"""
        content = read_lyric_file(path)
        if content is None:
            InfoBar.error(
                title="读取失败", content="无法读取歌词文件",
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=5000,
                parent=self._editor,
            )
            return

        try:
            from strange_uta_game.backend.application import ProjectService
            from strange_uta_game.backend.domain import Singer

            # 如果没有项目，自动创建
            if not self._project:
                if self._store:
                    project_service = ProjectService()
                    project = project_service.create_project()
                    self._store._project = project
                    self._store.notify("project")
                else:
                    InfoBar.warning(
                        title="无法加载", content="请先创建或打开一个项目",
                        orient=Qt.Orientation.Horizontal, isClosable=True,
                        position=InfoBarPosition.TOP, duration=3000,
                        parent=self._editor,
                    )
                    return

            default_singer = self._project.get_default_singer()

            # 解析歌词
            sentences, is_nicokara, new_singers = parse_lyric_content(
                content, default_singer.id, self._project.singers
            )

            # 添加新演唱者
            for singer in new_singers:
                self._project.add_singer(singer)

            if not sentences:
                InfoBar.warning(
                    title="解析结果为空", content="歌词文件未解析出有效内容",
                    orient=Qt.Orientation.Horizontal, isClosable=True,
                    position=InfoBarPosition.TOP, duration=3000,
                    parent=self._editor,
                )
                return

            # 替换项目歌词
            self._project.sentences.clear()
            for s in sentences:
                self._project.sentences.append(s)

            # 重建引擎状态
            if self._timing_service:
                self._timing_service.set_project(self._project)
            if self._store:
                self._store.notify("lyrics")

            self._editor.refresh_lyric_display()

            InfoBar.success(
                title="歌词已加载", content=f"已加载 {len(sentences)} 行歌词",
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=3000,
                parent=self._editor,
            )

            # Nicokara 格式弹窗
            if is_nicokara:
                self._prompt_nicokara_ruby_choice()

        except Exception as e:
            InfoBar.error(
                title="加载失败", content=str(e),
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=5000,
                parent=self._editor,
            )

    def _prompt_nicokara_ruby_choice(self):
        """Nicokara 格式注音处理弹窗（三选一）"""
        msg = QMessageBox(self._editor)
        msg.setWindowTitle("Nicokara 格式检测")
        msg.setText("检测到 Nicokara 格式歌词（已包含注音）。")
        msg.setInformativeText(
            "「保留原有注音」使用文件中的 @Ruby 注音。\n"
            "「全部重新分析」清除原有注音，使用自动分析。\n"
            "「仅分析未注音字符」保留已有注音，补充缺失的。"
        )
        btn_keep = msg.addButton("保留原有注音", QMessageBox.ButtonRole.AcceptRole)
        btn_all = msg.addButton("全部重新分析", QMessageBox.ButtonRole.DestructiveRole)
        btn_only_noruby = msg.addButton("仅分析未注音字符", QMessageBox.ButtonRole.ActionRole)
        msg.setDefaultButton(btn_keep)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked is btn_all:
            self._editor._auto_analyze_rubies(only_noruby=False)
        elif clicked is btn_only_noruby:
            self._editor._auto_analyze_rubies(only_noruby=True)
