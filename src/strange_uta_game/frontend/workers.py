"""后台工作线程 — 将耗时 I/O 和 CPU 操作移出 UI 线程。

所有 Worker 均为 QObject，配合 QThread 使用 moveToThread 模式。
调用方负责创建 QThread、moveToThread、连接信号、启动。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, pyqtSignal

if TYPE_CHECKING:
    from strange_uta_game.backend.infrastructure.audio.sounddevice_engine import (
        SoundDeviceEngine,
    )
    from strange_uta_game.backend.domain import Project


# ──────────────────────────────────────────────
# 音频 / 视频
# ──────────────────────────────────────────────


class AudioLoadWorker(QObject):
    """在后台线程加载音频文件到引擎。"""

    progress = pyqtSignal(str, float)  # (stage, 0.0~1.0)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, engine: SoundDeviceEngine, file_path: str):
        super().__init__()
        self._engine = engine
        self._file_path = file_path

    def run(self) -> None:
        try:
            self._engine.stop()
            self._engine.load(self._file_path, progress_cb=self.progress.emit)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class VideoExtractWorker(QObject):
    """从视频提取音频并加载到引擎（编辑器用）。"""

    progress = pyqtSignal(str, float)
    finished = pyqtSignal(str)  # temp_path，供调用方清理
    error = pyqtSignal(str)

    def __init__(self, engine: SoundDeviceEngine, file_path: str):
        super().__init__()
        self._engine = engine
        self._file_path = file_path

    def run(self) -> None:
        temp_path = None
        try:
            from strange_uta_game.backend.infrastructure.audio.video_converter import (
                extract_audio,
            )

            self.progress.emit("正在提取音频...", 0.0)
            temp_path = extract_audio(self._file_path, progress_cb=self.progress.emit)

            self._engine.stop()
            self._engine.load(temp_path, progress_cb=self.progress.emit)
            self.finished.emit(temp_path or "")
        except Exception as e:
            self.error.emit(str(e))


class VideoExtractOnlyWorker(QObject):
    """仅从视频提取音频（不加载到引擎），用于首页。"""

    progress = pyqtSignal(str, float)
    finished = pyqtSignal(str)  # extracted audio path
    error = pyqtSignal(str)

    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    def run(self) -> None:
        try:
            from strange_uta_game.backend.infrastructure.audio.video_converter import (
                extract_audio,
            )

            self.progress.emit("正在提取音频...", 0.0)
            temp_path = extract_audio(self._file_path, progress_cb=self.progress.emit)
            self.finished.emit(temp_path)
        except Exception as e:
            self.error.emit(str(e))


# ──────────────────────────────────────────────
# 项目
# ──────────────────────────────────────────────


class ProjectLoadWorker(QObject):
    """后台加载 .sug 项目文件。"""

    finished = pyqtSignal(object, str)  # (Project, file_path)
    error = pyqtSignal(str)

    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    def run(self) -> None:
        try:
            from strange_uta_game.backend.infrastructure.persistence.sug_io import (
                SugProjectParser,
            )

            project = SugProjectParser.load(self._file_path)
            self.finished.emit(project, self._file_path)
        except Exception as e:
            self.error.emit(str(e))


class ProjectSaveWorker(QObject):
    """后台保存项目。

    接收 Project 的深拷贝，避免保存过程中 UI 线程修改 project 导致数据竞争。
    """

    finished = pyqtSignal(str)  # saved path
    error = pyqtSignal(str)

    def __init__(self, project: Project, file_path: str):
        super().__init__()
        self._project = project
        self._file_path = file_path

    def run(self) -> None:
        try:
            from strange_uta_game.backend.infrastructure.persistence.sug_io import (
                SugProjectParser,
            )

            SugProjectParser.save(self._project, self._file_path)
            self.finished.emit(self._file_path)
        except Exception as e:
            self.error.emit(str(e))


# ──────────────────────────────────────────────
# 歌词
# ──────────────────────────────────────────────


class LyricReadWorker(QObject):
    """后台读取歌词文件原始内容。"""

    finished = pyqtSignal(str)  # raw text content
    error = pyqtSignal(str)

    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    def run(self) -> None:
        try:
            path = Path(self._file_path)
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = path.read_text(encoding="shift_jis")
            self.finished.emit(content)
        except Exception as e:
            self.error.emit(str(e))
