"""txt2ass 与 ASS 字幕格式导出器。

包含两类导出器：
1. Txt2AssExporter: 给 txt2ass 工具用的简单 [mm:ss.xx]text 文本格式。
2. ASSDirectExporter: 直接生成 Aegisub 兼容的 .ass 卡拉OK字幕，
   支持 \\k 时长标签和 Aegisub 风格的注音 ({字|<かな})。

设计原则（参考 entities.py 重构后契约）：
1. 时间永远从 char.global_timestamps / char.global_sentence_end_ts 取，
   领域层已经把偏移量算好，导出器不再二次叠加。
2. 每个字符在 Dialogue 文本里只出现一次。多 checkpoint 字符的额外
   timestamps 不再生成重复字符（修复字符重影 bug）。
3. 行 End Time 不再依赖「下一行 Start」（会让字幕跨过整段间奏），
   而是用本行最后字符的 global_sentence_end_ts，没有则退化为
   global_timing_end_ms + post-roll。
4. ASS 卡拉OK标签 \\k 的单位是厘秒(10ms)。每字时长 =
   下一个时间戳(或行末 sentence_end_ts) - 当前字时间戳，转厘秒。
"""

from typing import List, Optional
from .base import BaseExporter, ExportError
from strange_uta_game.backend.domain import Project, Sentence


class Txt2AssExporter(BaseExporter):
    """txt2ass 格式导出器

    导出 txt2ass 格式，用于配合外部 txt2ass 工具生成 ASS。
    格式简单：每行 [mm:ss.xx]Lyrics
    """

    @property
    def name(self) -> str:
        return "txt2ass"

    @property
    def description(self) -> str:
        return "用于生成 ASS 字幕的格式"

    @property
    def file_extension(self) -> str:
        return ".txt"

    @property
    def file_filter(self) -> str:
        return "txt2ass 文件 (*.txt)"

    def export(self, project: Project, file_path: str) -> None:
        """导出为 txt2ass 格式"""
        self._validate_project(project)

        lines: List[str] = []

        # 标题信息（注释）
        if project.metadata:
            if project.metadata.title:
                lines.append(f"# Title: {project.metadata.title}")
            if project.metadata.artist:
                lines.append(f"# Artist: {project.metadata.artist}")

        lines.append("# Format: [mm:ss.xx]Lyrics")
        lines.append("")

        for sentence in project.sentences:
            line_text = self._export_sentence(sentence)
            if line_text:
                lines.append(line_text)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    def _export_sentence(self, sentence: Sentence) -> str:
        """导出一行歌词"""
        if not sentence.has_timetags:
            return f"[00:00.00]{sentence.text}"

        start_ms = sentence.global_timing_start_ms
        if start_ms is None:
            return f"[00:00.00]{sentence.text}"

        time_str = self._format_timestamp(start_ms, "lrc")
        return f"{time_str}{sentence.text}"


# ──────────────────────────────────────────────
# ASSDirectExporter
# ──────────────────────────────────────────────

# 行前后留白（毫秒），让字幕进入/退出更自然
_PRE_ROLL_MS = 200
_POST_ROLL_MS = 200
# 行末若无 sentence_end_ts 时的兜底拖音时长（毫秒）
_FALLBACK_TAIL_MS = 500


class ASSDirectExporter(BaseExporter):
    """ASS 字幕直接导出器

    直接生成 Aegisub 兼容的 ASS 卡拉OK字幕。
    支持 \\k 时长标签和 Aegisub 注音 ({汉字|<かな})。
    """

    @property
    def name(self) -> str:
        return "ASS"

    @property
    def description(self) -> str:
        return "ASS 字幕格式（Advanced SubStation Alpha）"

    @property
    def file_extension(self) -> str:
        return ".ass"

    @property
    def file_filter(self) -> str:
        return "ASS 字幕文件 (*.ass)"

    def export(self, project: Project, file_path: str) -> None:
        """导出为 ASS 格式"""
        self._validate_project(project)
        file_path = self._ensure_extension(file_path)

        lines: List[str] = []

        # ASS 文件头
        lines.extend(self._generate_header(project))
        lines.append("")

        # Styles
        lines.extend(self._generate_styles())
        lines.append("")

        # Events
        lines.extend(self._generate_events(project))

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    def _generate_header(self, project: Project) -> List[str]:
        title = project.metadata.title if project.metadata else "Untitled"
        return [
            "[Script Info]",
            f"Title: {title}",
            "ScriptType: v4.00+",
            "Collisions: Normal",
            "PlayDepth: 0",
            "Timer: 100.0000",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.601",
        ]

    def _generate_styles(self) -> List[str]:
        return [
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,10,10,10,1",
            "Style: Karaoke,Arial,24,&H00FF6B6B,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,5,10,10,30,1",
        ]

    def _generate_events(self, project: Project) -> List[str]:
        lines = [
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        for sentence in project.sentences:
            if not sentence.has_timetags:
                continue

            line_start_ms = sentence.global_timing_start_ms
            if line_start_ms is None:
                continue

            # 行结束时间：优先用本行 sentence_end_ts，否则用最大全局时间戳 + 兜底
            line_end_ms = self._compute_line_end_ms(sentence)

            # 行首尾留白
            start_str = self._format_timestamp(
                max(0, line_start_ms - _PRE_ROLL_MS), "ass"
            )
            end_str = self._format_timestamp(line_end_ms + _POST_ROLL_MS, "ass")

            # 卡拉OK文本
            karaoke_text = self._generate_karaoke_text(
                sentence, line_start_ms, line_end_ms
            )

            event_line = (
                f"Dialogue: 0,{start_str},{end_str},Karaoke,,0,0,0,,{karaoke_text}"
            )
            lines.append(event_line)

        return lines

    def _compute_line_end_ms(self, sentence: Sentence) -> int:
        """计算本行的结束时间（毫秒）。

        优先级：
        1. 最后一个 is_sentence_end 字符的 global_sentence_end_ts
        2. 行内最晚全局时间戳 + 兜底拖音
        """
        for ch in reversed(sentence.characters):
            if ch.is_sentence_end and ch.global_sentence_end_ts is not None:
                return ch.global_sentence_end_ts

        end = sentence.global_timing_end_ms
        if end is None:
            # 不应发生：has_timetags 已保证至少一个时间戳
            return 0
        return end + _FALLBACK_TAIL_MS

    def _generate_karaoke_text(
        self, sentence: Sentence, line_start_ms: int, line_end_ms: int
    ) -> str:
        """生成带卡拉OK效果的 Dialogue 文本。

        - 每字符只出现一次，前缀 {\\k<cs>} 标签控制点亮时长。
        - 时长 = 下一个字符的 global_timestamps[0]
                - 当前字符的 global_timestamps[0]
                （最后一个有时间戳的字符用 line_end_ms 收尾）
        - 无时间戳的字符（标点、未打轴）会跟着前一个字符的 \\k 内显示。
        - 行首加 pre-roll \\k，行尾加 post-roll \\k 让进入/退出更平滑。
        - 含 Ruby 的字符按 Aegisub 注音格式输出：{\\k...}{字|<かな}
        """
        # 先把字符顺序里「下一个有时间戳的字符的时间戳」预算好，
        # 方便给每个有时间戳的字符算 duration。
        chars = sentence.characters
        next_ts_for: List[Optional[int]] = [None] * len(chars)
        # 从尾部回扫
        running: Optional[int] = line_end_ms
        for i in range(len(chars) - 1, -1, -1):
            next_ts_for[i] = running
            if chars[i].global_timestamps:
                running = chars[i].global_timestamps[0]

        parts: List[str] = [f"{{\\k{_PRE_ROLL_MS // 10}}}"]

        # 缓冲：未打轴的字符（如标点）会暂存，挂到下一个有时间戳的字符上
        pending_untimed: List[str] = []

        for i, ch in enumerate(chars):
            if not ch.global_timestamps:
                # 标点等无时间戳字符：留到下一个有时间戳的字符一起出
                pending_untimed.append(self._escape_ass_text(ch.char))
                continue

            current_ts = ch.global_timestamps[0]
            nxt_raw = next_ts_for[i]
            nxt = nxt_raw if nxt_raw is not None else line_end_ms
            duration_ms = max(0, nxt - current_ts)
            k_cs = duration_ms // 10  # 厘秒

            # 把字符（可能含 ruby）和前面累积的标点一起输出
            char_token = self._format_char_with_ruby(ch)
            # 标点跟在前面字符同一拍点亮（视觉合理），所以放在 \k 之后、字之前
            prefix_untimed = "".join(pending_untimed)
            pending_untimed.clear()

            parts.append(f"{{\\k{k_cs}}}{prefix_untimed}{char_token}")

        # 若末尾还残留未打轴字符（极端情况），收尾输出
        if pending_untimed:
            parts.append("".join(pending_untimed))

        # 行尾 post-roll
        parts.append(f"{{\\k{_POST_ROLL_MS // 10}}}")

        return "".join(parts)

    @staticmethod
    def _format_char_with_ruby(ch) -> str:
        """格式化单个字符。

        有 ruby → {汉字|<かな}（Aegisub 卡拉OK注音惯例）
        无 ruby → 字符原文
        """
        if ch.ruby and ch.ruby.text:
            kanji = ASSDirectExporter._escape_ass_text(ch.char)
            kana = ASSDirectExporter._escape_ass_text(ch.ruby.text)
            return f"{kanji}|<{kana}"
        return ASSDirectExporter._escape_ass_text(ch.char)

    @staticmethod
    def _escape_ass_text(text: str) -> str:
        """转义 ASS 文本中的特殊字符。

        ASS 里 `{` `}` `\\` 是标签语法的一部分，需转义。
        """
        if not text:
            return text
        # 反斜杠先处理，避免连锁替换
        text = text.replace("\\", "\\\\")
        text = text.replace("{", "\\{").replace("}", "\\}")
        return text
