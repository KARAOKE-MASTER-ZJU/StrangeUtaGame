"""Nicokara (ニコカラ) LRC 格式导出器。

输出 RhythmicaLyrics 风格的 Nicokara 逐字 LRC 格式：
- 时间戳格式: [MM:SS.CC]（分:秒:厘秒，冒号分隔）
- 每个字符前有独立时间戳
- 行末附加结束时间戳
- @Ruby 注音标签（含字内相对时间；多次出现时每次独立条目+位置范围）
- @Offset 全局偏移
- @Title/@Artist/@Album/@TaggingBy/@SilencemSec 元数据标签（可选）
- 演唱者过滤：可按选定的演唱者筛选输出行/字符
- 演唱者标签：在演唱者切换处自动插入【演唱者名】标签
"""

import re
from collections import OrderedDict
from typing import List, Optional, Dict, Any, Set, Tuple

from .base import BaseExporter, ExportError
from strange_uta_game.backend.domain import Project, Sentence, Singer

_NICOKARA_TS_RE = re.compile(r'\[\d+:\d+:\d+\]')


def _format_nicokara_ts(timestamp_ms: int, offset_ms: int = 0) -> str:
    """格式化 Nicokara 时间戳 [MM:SS:CC]

    Args:
        timestamp_ms: 毫秒时间戳
        offset_ms: 偏移量（毫秒）

    Returns:
        格式化后的字符串，如 [00:12:34]
    """
    timestamp_ms = max(0, timestamp_ms + offset_ms)
    # 四舍五入到厘秒，再提取各分量（避免 995ms→100cs 溢出）
    total_cs = round(timestamp_ms / 10)
    minutes = total_cs // 6000
    seconds = (total_cs % 6000) // 100
    centiseconds = total_cs % 100
    return f"[{minutes:02d}:{seconds:02d}:{centiseconds:02d}]"


class NicokaraExporter(BaseExporter):
    """Nicokara 逐字 LRC 格式导出器

    每个字符前有独立 [MM:SS:CC] 时间戳，行末附加结束时间戳。
    支持演唱者过滤和演唱者标签插入。
    """

    @property
    def name(self) -> str:
        return "Nicokara"

    @property
    def description(self) -> str:
        return "Nicokara 逐字 LRC 格式（ニコカラメーカー用）"

    @property
    def file_extension(self) -> str:
        return ".lrc"

    @property
    def file_filter(self) -> str:
        return "Nicokara LRC 文件 (*.lrc)"

    def export(
        self,
        project: Project,
        file_path: str,
        singer_ids: Optional[Set[str]] = None,
        insert_singer_tags: bool = False,
        singer_map: Optional[Dict[str, str]] = None,
    ) -> None:
        """导出为 Nicokara 逐字 LRC 格式

        Args:
            project: 项目数据
            file_path: 输出文件路径
            singer_ids: 要输出的演唱者 ID 集合（None 表示全部）
            insert_singer_tags: 是否在演唱者切换处插入【演唱者名】标签
            singer_map: singer_id → 演唱者显示名的映射（insert_singer_tags 时使用）
        """
        self._validate_project(project)

        output_lines: List[str] = []
        prev_end_ms = 0
        prev_singer_id: Optional[str] = None
        default_singer_id = self._get_default_singer_id(project)

        for i, sentence in enumerate(project.sentences):
            # 空行（用户排版意图）无条件保留
            is_blank_line = not sentence.text.strip() and not sentence.characters
            # 演唱者过滤：检查行内是否有选中的演唱者字符
            if singer_ids is not None and not is_blank_line:
                if not self._sentence_has_singer(
                    sentence, singer_ids, default_singer_id
                ):
                    continue

            # 段落间距不再自动插入空行：由 project.sentences 原始空行负责
            # （批 18 #6：导入剥空行 / 导出保留空行，双向对称）

            line_text, prev_singer_id = self._export_sentence_with_singer(
                sentence,
                singer_ids,
                insert_singer_tags,
                singer_map,
                prev_singer_id,
                default_singer_id,
            )
            # 过滤后无字符的行需要区分：原本就是空行（保留）vs 被过滤掉内容的行（跳过）
            stripped = line_text.strip()
            content_only = _NICOKARA_TS_RE.sub('', stripped)
            if (
                singer_ids is not None
                and not content_only.strip()
                and sentence.text.strip()
            ):
                continue
            output_lines.append(line_text)

            if sentence.has_timetags:
                end_ms = sentence.timing_end_ms
                if end_ms is not None:
                    prev_end_ms = end_ms

            # 空行后重置 prev_singer_id 规则已废弃：
            # 实际 n3_color 数据显示，空行后若 singer 未变更，则不重复插入
            # 【svN】标签。先注释保留以便回溯，不作为 fallback。
            # if is_blank_line:
            #     prev_singer_id = None

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(output_lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")
    @staticmethod
    def _get_default_singer_id(project: Project) -> Optional[str]:
        """获取项目的默认演唱者 ID"""
        for singer in project.singers:
            if singer.is_default:
                return singer.id
        if project.singers:
            return project.singers[0].id
        return None

    @staticmethod
    def _normalize_singer_id(
        singer_id: Optional[str], default_singer_id: Optional[str]
    ) -> Optional[str]:
        """将空/未知/? 演唱者 ID 归一化为默认演唱者"""
        if not singer_id or singer_id in ("?", "未知"):
            return default_singer_id
        return singer_id

    def _sentence_has_singer(
        self,
        sentence: Sentence,
        singer_ids: Set[str],
        default_singer_id: Optional[str] = None,
    ) -> bool:
        """检查行内是否有属于指定演唱者的字符

        未知/空/? 演唱者视为默认演唱者。
        """
        eff = self._normalize_singer_id(sentence.singer_id, default_singer_id)
        if eff in singer_ids:
            return True
        # per-char 级别检查
        for ch in sentence.characters:
            eff_ch = self._normalize_singer_id(
                ch.singer_id or sentence.singer_id, default_singer_id
            )
            if eff_ch in singer_ids:
                return True
        return False

    def _export_sentence_with_singer(
        self,
        sentence: Sentence,
        singer_ids: Optional[Set[str]],
        insert_singer_tags: bool,
        singer_map: Optional[Dict[str, str]],
        prev_singer_id: Optional[str] = None,
        default_singer_id: Optional[str] = None,
    ) -> Tuple[str, Optional[str]]:
        """导出一行，支持演唱者过滤和标签插入

        Returns:
            (行文本, 最后一个有效演唱者 ID)
        """
        if not sentence.has_timetags or not sentence.characters:
            return sentence.text, prev_singer_id

        parts: List[str] = []

        for i, ch in enumerate(sentence.characters):
            # 有效演唱者：优先使用 per-char，回退到行级别
            effective_singer = ch.singer_id or sentence.singer_id
            # 归一化未知演唱者为默认演唱者
            effective_singer = self._normalize_singer_id(
                effective_singer, default_singer_id
            )

            # 演唱者过滤：跳过不属于选定演唱者的字符
            if singer_ids is not None and effective_singer not in singer_ids:
                continue

            # 演唱者标签插入：在演唱者发生变化时插入标签
            if insert_singer_tags and singer_map and effective_singer != prev_singer_id:
                singer_name = singer_map.get(effective_singer, "") if effective_singer else ""
                if singer_name:
                    parts.append(f"【{singer_name}】")
                prev_singer_id = effective_singer

            # 字符起始时间戳（第一个 checkpoint，使用导出时间戳含偏移）
            if ch.global_timestamps:
                parts.append(_format_nicokara_ts(ch.global_timestamps[0]))
            parts.append(ch.char)

            # 非行尾句尾字符的释放时间戳（句中句尾需要输出一前一后两个时间戳）
            if (
                i < len(sentence.characters) - 1
                and ch.is_sentence_end
                and ch.global_sentence_end_ts is not None
            ):
                eff = self._normalize_singer_id(
                    ch.singer_id or sentence.singer_id, default_singer_id
                )
                if singer_ids is None or eff in singer_ids:
                    parts.append(_format_nicokara_ts(ch.global_sentence_end_ts))

        # 行末结束时间戳（最后一个字符的 sentence-end checkpoint）
        if sentence.characters:
            last_char = sentence.characters[-1]
            if (
                last_char.is_sentence_end
                and last_char.global_sentence_end_ts is not None
            ):
                # 演唱者过滤：只有该字符属于选定演唱者时才输出
                eff = self._normalize_singer_id(
                    last_char.singer_id or sentence.singer_id, default_singer_id
                )
                if singer_ids is None or eff in singer_ids:
                    parts.append(_format_nicokara_ts(last_char.global_sentence_end_ts))

        return "".join(parts), prev_singer_id

    def _export_sentence(self, sentence: Sentence) -> str:
        """导出一行（向后兼容，不带演唱者过滤）"""
        text, _ = self._export_sentence_with_singer(sentence, None, False, None)
        return text


class NicokaraWithRubyExporter(NicokaraExporter):
    """带注音的 Nicokara LRC 格式导出器

    在 Nicokara 逐字格式基础上追加：
    - @Offset 全局偏移
    - @RubyN=漢字,読み[相対時間],出現位置1,出現位置2,...
    """

    @property
    def name(self) -> str:
        return "Nicokara (带注音)"

    @property
    def description(self) -> str:
        return "Nicokara 逐字 LRC 格式（含 @Ruby 注音标签）"

    def validate_ruby_parts(self, project: Project) -> List[dict]:
        """校验项目中所有字符的 rubyPart 数量与 checkCount 是否匹配

        Args:
            project: 项目数据

        Returns:
            不匹配的字符信息列表，每个元素为 dict:
            {
                "sentence_idx": 句子索引,
                "char_idx": 字符索引,
                "char": 字符文本,
                "check_count": check_count,
                "ruby_parts_count": ruby.parts 数量,
                "ruby_text": ruby 文本,
                "ruby_parts": ruby.parts 拆分情况 (List[str])
            }
        """
        mismatches = []
        for sent_idx, sentence in enumerate(project.sentences):
            for char_idx, ch in enumerate(sentence.characters):
                if ch.ruby and ch.check_count > 0:
                    ruby_parts_count = len(ch.ruby.parts)
                    if ruby_parts_count != ch.check_count:
                        mismatches.append({
                            "sentence_idx": sent_idx,
                            "char_idx": char_idx,
                            "char": ch.char,
                            "check_count": ch.check_count,
                            "ruby_parts_count": ruby_parts_count,
                            "ruby_text": ch.ruby.text,
                            "ruby_parts": [p.text for p in ch.ruby.parts],
                        })
        return mismatches

    def export(
        self,
        project: Project,
        file_path: str,
        singer_ids: Optional[Set[str]] = None,
        insert_singer_tags: bool = False,
        singer_map: Optional[Dict[str, str]] = None,
        tag_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """导出为带 @Ruby 注音标签的 Nicokara LRC 格式

        Args:
            project: 项目数据
            file_path: 输出文件路径
            singer_ids: 要输出的演唱者 ID 集合（None 表示全部）
            insert_singer_tags: 是否在演唱者切换处插入【演唱者名】标签
            singer_map: singer_id → 演唱者显示名的映射
            tag_data: Nicokara 元数据标签，格式与 AppSettings["nicokara_tags"] 相同
        """
        self._validate_project(project)

        output_lines: List[str] = []
        prev_end_ms = 0
        prev_singer_id: Optional[str] = None
        default_singer_id = self._get_default_singer_id(project)

        for i, sentence in enumerate(project.sentences):
            # 空行（用户排版意图）无条件保留
            is_blank_line = not sentence.text.strip() and not sentence.characters
            # 演唱者过滤
            if singer_ids is not None and not is_blank_line:
                if not self._sentence_has_singer(
                    sentence, singer_ids, default_singer_id
                ):
                    continue

            # 段落间距不再自动插入空行（批 18 #6）

            line_text, prev_singer_id = self._export_sentence_with_singer(
                sentence,
                singer_ids,
                insert_singer_tags,
                singer_map,
                prev_singer_id,
                default_singer_id,
            )
            # 过滤后无字符的行需要区分：原本就是空行（保留）vs 被过滤掉内容的行（跳过）
            stripped = line_text.strip()
            content_only = _NICOKARA_TS_RE.sub('', stripped)
            if (
                singer_ids is not None
                and not content_only.strip()
                and sentence.text.strip()
            ):
                continue
            output_lines.append(line_text)

            if sentence.has_timetags:
                end_ms = sentence.timing_end_ms
                if end_ms is not None:
                    prev_end_ms = end_ms

            # 空行后重置 prev_singer_id 规则已废弃：
            # 实际 n3_color 数据显示，空行后若 singer 未变更，则不重复插入
            # 【svN】标签。先注释保留以便回溯，不作为 fallback。
            # if is_blank_line:
            #     prev_singer_id = None

        # 元数据标签（从 AppSettings 或传入的 tag_data 读取）
        tags = tag_data or {}
        if not tags:
            try:
                from strange_uta_game.frontend.settings.settings_interface import (
                    AppSettings,
                )

                tags = AppSettings().get("nicokara_tags") or {}
            except Exception:
                tags = {}

        # 规整 body 尾部：连续空行折叠为最多 1 个（与 nicokara3 原生格式一致）
        while len(output_lines) >= 2 and output_lines[-1] == "" and output_lines[-2] == "":
            output_lines.pop()
        # 若 body 尾部没有空行，补一个（与 ruby 段隔开）
        if output_lines and output_lines[-1] != "":
            output_lines.append("")

        if tags.get("title"):
            output_lines.append(f"@Title={tags['title']}")
        if tags.get("artist"):
            output_lines.append(f"@Artist={tags['artist']}")
        if tags.get("album"):
            output_lines.append(f"@Album={tags['album']}")
        if tags.get("tagging_by"):
            output_lines.append(f"@TaggingBy={tags['tagging_by']}")
        silence = tags.get("silence_ms", 0)
        if silence:
            output_lines.append(f"@SilencemSec={silence}")
        for custom in tags.get("custom", []):
            if custom:
                output_lines.append(custom)

        # @Offset（仅当存在非零偏移时输出，避免污染 round-trip）
        offset_ms = 0
        try:
            offset_ms = int(getattr(project, "offset_ms", 0) or 0)
        except (TypeError, ValueError):
            offset_ms = 0
        if offset_ms != 0:
            sign = "+" if offset_ms >= 0 else "-"
            output_lines.append(f"@Offset={sign}{abs(offset_ms)}")

        # @Ruby 注音标签（也按演唱者过滤）
        ruby_entries = self._collect_ruby_entries(project, singer_ids)
        for idx, entry in enumerate(ruby_entries, 1):
            output_lines.append(f"@Ruby{idx}={entry}")

        try:
            # 与 nicokara3 原生格式一致：UTF-8-BOM + CRLF 行尾 + 末尾 newline
            with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
                f.write("\r\n".join(output_lines) + "\r\n")
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    # ------------------------------------------------------------------
    # @Ruby 生成
    # ------------------------------------------------------------------

    def _collect_ruby_entries(
        self, project: Project, singer_ids: Optional[Set[str]] = None
    ) -> List[str]:
        """收集所有注音并生成 @Ruby 条目列表（朴素版 — 每段独立 entry）

        策略（用户指定，2026-05-11）：
          - 按 body 顺序扫描 sentence，遇到连续的有 ruby 的 char 即为一段。
          - 每段独立输出一个 @RubyN，N 严格递增，不复用、不去重、不按 kanji 合并。
          - 作用域写死在该段自身的时间范围内：
              pos1 = 段首字第一个 global timestamp
              pos2 = 段尾字 sentence_end_ts（若是句尾）或最后一个 timestamp
          - reading 由 _build_reading_with_timestamps 按段内 char.ruby.parts 原样拼出。

        这一版**不做任何省略**：每个 entry 都完整写 kanji,reading,pos1,pos2，
        即使作用域只覆盖两三个字符。语义最清晰，不依赖任何 substring 消歧或
        kanji+reading 折叠规则。

        Args:
            project: 项目数据
            singer_ids: 要输出的演唱者 ID 集合（None 表示全部）

        Returns:
            格式: ["漢字,読み[ts],pos1,pos2", ...]，调用方加 @RubyN= 前缀
        """
        default_singer_id = self._get_default_singer_id(project)
        result: List[str] = []

        for sent_idx, sentence in enumerate(project.sentences):
            if singer_ids is not None:
                if not self._sentence_has_singer(
                    sentence, singer_ids, default_singer_id
                ):
                    continue

            chars = sentence.characters
            n = len(chars)
            i = 0
            while i < n:
                if chars[i].ruby is None:
                    i += 1
                    continue
                # 扫一段连续 ruby char [start_idx, end_idx)
                # 终止条件：
                #   1. 下一个 char 无 ruby
                #   2. 当前 char 是句尾（is_sentence_end=True）—— 句子边界天然切段，
                #      避免把同一 lrc 行中跨"释放 ts"的两句 ruby 合并（如 乙女|心）
                start_idx = i
                while i < n and chars[i].ruby is not None:
                    if chars[i].is_sentence_end:
                        i += 1
                        break
                    i += 1
                end_idx = i

                kanji = "".join(c.char for c in chars[start_idx:end_idx])
                reading_fallback = "".join(
                    p.text
                    for c in chars[start_idx:end_idx]
                    if c.ruby
                    for p in c.ruby.parts
                )
                reading_display, _ = self._build_reading_with_timestamps(
                    sentence, start_idx, end_idx, reading_fallback
                )

                # pos1: 段首字第一个 global ts；若段首字无 ts（linked group 头字 如 死/高），
                # 严格向上就近找：先在本句段前 char 找最近 ts，再跨 sentence 向上找。
                first_ch = chars[start_idx]
                pos1_ts: Optional[int] = None
                if first_ch.global_timestamps:
                    pos1_ts = first_ch.global_timestamps[0]
                if pos1_ts is None:
                    # 向前在本句找最近 ts（优先句尾释放 ts，否则最后一个 ts）
                    for k in range(start_idx - 1, -1, -1):
                        if chars[k].global_sentence_end_ts is not None:
                            pos1_ts = chars[k].global_sentence_end_ts
                            break
                        if chars[k].global_timestamps:
                            pos1_ts = chars[k].global_timestamps[-1]
                            break
                if pos1_ts is None:
                    # 向前跨 sentence 找最近 ts
                    for ps in reversed(project.sentences[:sent_idx]):
                        found = False
                        for pc in reversed(ps.characters):
                            if pc.global_sentence_end_ts is not None:
                                pos1_ts = pc.global_sentence_end_ts
                                found = True
                                break
                            if pc.global_timestamps:
                                pos1_ts = pc.global_timestamps[-1]
                                found = True
                                break
                        if found:
                            break
                # pos2: 段尾字"作用结束"时刻
                #   - 若段尾字是句尾 → 句尾释放 ts（global_sentence_end_ts）
                #   - 否则 → 下一个有 ts 的 char 的起始 ts（下字开始 = 本字结束）
                #   - 找不到下一字（全文末尾且用户未标句尾）→ pos2 省略
                last_ch = chars[end_idx - 1]
                pos2_ts: Optional[int] = None
                pos2_omit = False
                if last_ch.is_sentence_end and last_ch.global_sentence_end_ts is not None:
                    pos2_ts = last_ch.global_sentence_end_ts
                else:
                    # 先在本句剩余 char 找下一个有 ts 的
                    for k in range(end_idx, n):
                        if chars[k].global_timestamps:
                            pos2_ts = chars[k].global_timestamps[0]
                            break
                    if pos2_ts is None:
                        # 本句后续无 ts char → 找后续 sentence 第一个有 ts 的 char
                        sent_idx_in_proj = sent_idx
                        for ns in project.sentences[sent_idx_in_proj + 1:]:
                            found = False
                            for nc in ns.characters:
                                if nc.global_timestamps:
                                    pos2_ts = nc.global_timestamps[0]
                                    found = True
                                    break
                            if found:
                                break
                    if pos2_ts is None:
                        # 全文最后一字且未标句尾 → pos2 省略
                        pos2_omit = True

                pos1_str = _format_nicokara_ts(pos1_ts) if pos1_ts is not None else ""
                if pos2_omit:
                    # 全文最后一字未标句尾 → 省略 pos2 字段
                    result.append(f"{kanji},{reading_display},{pos1_str}")
                else:
                    pos2_str = _format_nicokara_ts(pos2_ts) if pos2_ts is not None else ""
                    result.append(f"{kanji},{reading_display},{pos1_str},{pos2_str}")

        return result

    def _collect_ruby_entries_OLD_DO_NOT_USE(
        self, project: Project, singer_ids: Optional[Set[str]] = None
    ) -> List[str]:
        """[DEPRECATED 2026-05-11] 旧实现，基于错误的 spec（按 kanji 字符串聚合 + 子串消歧）。
        本体已删除（用户要求：先注释/删除，免得之后忘了，但是不要留做 fallback）。
        若需参考子串干扰逻辑（阶段 C），见 git history。
        """
        raise NotImplementedError("旧实现已废弃，请使用 _collect_ruby_entries")
    def _build_reading_with_timestamps(
        self,
        sentence: Sentence,
        start_idx: int,
        end_idx: int,
        reading: str,
    ) -> tuple[str, tuple]:
        """构建带相对时间戳的读音文本

        格式: た[00:00:15]か[00:00:27]らばこ
        相对时间基于 ruby 组第一个字符的首个 checkpoint。

        Args:
            sentence:  句子
            start_idx: ruby 起始字符索引
            end_idx:   ruby 结束字符索引
            reading:   读音文本

        Returns:
            (display_str, ms_key)
            - display_str: 带厘秒时间戳的显示字符串
            - ms_key: 毫秒精度的比较元组，用于区分不同 offset 的读音
        """
        # 建立 kana → (char_idx, checkpoint_idx) 的映射
        mapping: List[tuple[str, int, int]] = []

        for char_idx in range(start_idx, end_idx):
            if char_idx >= len(sentence.characters):
                break
            ch = sentence.characters[char_idx]
            ruby = ch.ruby
            groups = [p.text for p in ruby.parts] if ruby else []

            # 如果 ruby.parts 数量少于 check_count，需要补充空条目
            # 以确保 mapping 中包含所有 checkpoint
            check_count = ch.check_count
            if check_count > 0 and len(groups) < check_count:
                # 补充空字符串条目到 check_count 个
                groups = groups + [""] * (check_count - len(groups))

            for cp_idx, group_text in enumerate(groups):
                mapping.append((group_text, char_idx, cp_idx))

        if not mapping and reading:
            mapping.append((reading, -1, -1))

        if not mapping:
            return reading, ()

        # 获取组起始时间（第一个字符的首个 checkpoint，使用导出时间戳）
        first_char = (
            sentence.characters[start_idx]
            if start_idx < len(sentence.characters)
            else None
        )
        if not first_char or not first_char.global_timestamps:
            return reading, ()
        group_start_ms = first_char.global_timestamps[0]

        # 拼装读音字符 + 相对时间戳
        display_parts: List[str] = []
        ms_key_parts: List = []

        for i, (group_text, char_idx, cp_idx) in enumerate(mapping):
            if i == 0:
                # 第一个假名不加时间戳
                display_parts.append(group_text)
                ms_key_parts.append(group_text)
                continue

            if char_idx >= 0 and cp_idx >= 0:
                ch = sentence.characters[char_idx]
                if cp_idx < len(ch.global_timestamps):
                    relative_ms = ch.global_timestamps[cp_idx] - group_start_ms
                    display_parts.append(_format_nicokara_ts(relative_ms))
                    ms_key_parts.append(relative_ms)

            display_parts.append(group_text)
            ms_key_parts.append(group_text)

        return "".join(display_parts), tuple(ms_key_parts)
