"""注音分析器 - 为日文文本提供假名注音。

唯一分析器为 fugashi（MeCab 分词，跨平台），不再使用 WinRT IME / pykakasi / Sudachi。
fugashi 不可用时降级 DummyAnalyzer。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple

from strange_uta_game.backend.domain import Ruby, RubyPart, Sentence
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    split_ruby_for_checkpoints,
)


@dataclass
class RubyResult:
    """注音分析结果"""

    text: str  # 原始字符
    reading: str  # 注音（假名）
    start_idx: int  # 起始索引
    end_idx: int  # 结束索引


class RubyAnalyzer(ABC):
    """注音分析器抽象基类"""

    @abstractmethod
    def analyze(self, text: str) -> List[RubyResult]:
        """分析文本并返回注音结果"""
        pass

    @abstractmethod
    def get_reading(self, text: str) -> str:
        """获取文本的完整读音"""
        pass


# ──────────────────────────────────────────────
# 假名分配基类（分词器无关）
# ──────────────────────────────────────────────


class KanaDistributingAnalyzer(RubyAnalyzer):
    """把 (surface, 平假名读音) 序列分配为逐字/逐块注音的共享基类。

    不依赖任何具体分词器；子类只需产出 (surface, reading) 序列并复用
    :meth:`_results_from_pairs`，保证下游 block 分组逻辑完全一致。

    对于含漢字的形態素：
    1. 先用假名字符作为锚点分配读音（如 迷い → 迷{まよ}い）
    2. 对纯漢字块，尝试对复合读音进行单字分配
       （如 世界{せかい} → 世{せ}界{かい}）
    3. 分配失败时保持复合词读音不拆分（如 今日{きょう}）
    """

    def _results_from_pairs(
        self, pairs: List[Tuple[str, str]]
    ) -> List[RubyResult]:
        """将 (surface, 平假名读音) 序列分配为逐块 RubyResult。

        供各分词器实现（如 WinRTAnalyzer）共用，保证下游 block 分组逻辑一致。
        """
        results: List[RubyResult] = []
        pos = 0

        for surface, reading in pairs:
            start = pos
            end = pos + len(surface)

            has_kanji = any(self._is_kanji(c) for c in surface)

            if not has_kanji or not reading or surface == reading:
                # 纯假名/符号/无読音: 逐字处理，片假名转平假名
                for i, c in enumerate(surface):
                    results.append(
                        RubyResult(
                            text=c,
                            reading=self._kata_to_hira(c),
                            start_idx=start + i,
                            end_idx=start + i + 1,
                        )
                    )
            else:
                # 含漢字：分配读音
                distributed = self._distribute_morpheme_reading(surface, reading)
                char_offset = 0
                for block_text, block_reading in distributed:
                    block_start = start + char_offset
                    block_end = block_start + len(block_text)
                    results.append(
                        RubyResult(
                            text=block_text,
                            reading=block_reading,
                            start_idx=block_start,
                            end_idx=block_end,
                        )
                    )
                    char_offset += len(block_text)

            pos = end

        return results

    # ── 读音分配 ──

    def _distribute_morpheme_reading(
        self, surface: str, reading: str
    ) -> List[Tuple[str, str]]:
        """将形態素的读音分配到各个字符。

        利用假名字符作为锚点切分读音，纯漢字块再尝试单字分配。
        """
        # 将 surface 切成连续的漢字段和非漢字段
        segments: List[Tuple[str, bool]] = []
        i = 0
        while i < len(surface):
            if self._is_kanji(surface[i]):
                j = i + 1
                while j < len(surface) and self._is_kanji(surface[j]):
                    j += 1
                segments.append((surface[i:j], True))
                i = j
            else:
                j = i + 1
                while j < len(surface) and not self._is_kanji(surface[j]):
                    j += 1
                segments.append((surface[i:j], False))
                i = j

        matched = self._match_segments(segments, reading, 0, 0)
        if matched is None:
            # 匹配失败：整块返回
            return [(surface, reading)]
        return matched

    def _match_segments(
        self,
        segments: List[Tuple[str, bool]],
        reading: str,
        seg_idx: int,
        read_idx: int,
    ) -> Optional[List[Tuple[str, str]]]:
        """递归将 segments 与 reading 对齐。"""
        if seg_idx == len(segments):
            return [] if read_idx == len(reading) else None
        if read_idx > len(reading):
            return None

        seg_text, is_kanji = segments[seg_idx]

        if not is_kanji:
            # 非漢字段：转成平假名后字面匹配
            hira = self._kata_to_hira(seg_text)
            seg_len = len(hira)
            if reading[read_idx : read_idx + seg_len] == hira:
                rest = self._match_segments(
                    segments, reading, seg_idx + 1, read_idx + seg_len
                )
                if rest is not None:
                    per_char = [(c, c) for c in seg_text]
                    return per_char + rest
            return None

        # 漢字段：尝试不同长度
        remaining_literal = 0
        for s, k in segments[seg_idx + 1 :]:
            if not k:
                remaining_literal += len(self._kata_to_hira(s))

        min_len = len(seg_text)  # 每个漢字至少 1 假名
        max_len = len(reading) - read_idx - remaining_literal

        for try_len in range(min_len, max_len + 1):
            portion = reading[read_idx : read_idx + try_len]
            rest = self._match_segments(
                segments, reading, seg_idx + 1, read_idx + try_len
            )
            if rest is not None:
                # 多漢字段按 morpheme 整块返回（同一 RubyResult → 同一 block_id），
                # 由下游 auto_check 的 library→fallback 路径按库候选切分到单字。
                # 不在分析器内拆单字，避免同 morpheme 字符被打散到多个 block。
                return [(seg_text, portion)] + rest

        return None

    def _try_distribute_kanji_block(
        self, kanji_text: str, compound_reading: str
    ) -> Optional[List[Tuple[str, str]]]:
        """尝试将复合读音分配到各个漢字。

        不再使用 pykakasi 参考读音，直接进行无约束分配。
        最终失败时保持整块。
        """
        n = len(kanji_text)
        empty_refs = [""] * n
        return self._partition_with_refs(kanji_text, compound_reading, empty_refs, 0, 0)

    def _partition_with_refs(
        self,
        kanji_text: str,
        reading: str,
        ref_readings: List[str],
        ki: int,
        ri: int,
    ) -> Optional[List[Tuple[str, str]]]:
        """递归分区：将复合读音分配到各個漢字。

        三级匹配策略：
        1. 参考读音精确匹配（如果有参考）
        2. 参考读音前缀匹配
        3. 无约束匹配（当参考读音不适用时，放宽限制）
        """
        if ki == len(kanji_text):
            return [] if ri == len(reading) else None
        if ri > len(reading):
            return None

        remaining_kanji = len(kanji_text) - ki
        remaining_chars = len(reading) - ri
        if remaining_chars < remaining_kanji:
            return None

        max_len = remaining_chars - (remaining_kanji - 1)
        ref = ref_readings[ki]

        tried: set = set()

        # 优先尝试参考读音精确匹配
        if ref:
            ref_len = len(ref)
            if ref_len <= max_len:
                portion = reading[ri : ri + ref_len]
                if portion == ref:
                    rest = self._partition_with_refs(
                        kanji_text, reading, ref_readings, ki + 1, ri + ref_len
                    )
                    if rest is not None:
                        return [(kanji_text[ki], portion)] + rest
                    tried.add(ref_len)

        # 其次尝试前缀匹配：分配部分是参考读音的前缀
        for try_len in range(1, max_len + 1):
            if try_len in tried:
                continue
            portion = reading[ri : ri + try_len]
            if ref and not ref.startswith(portion):
                continue  # 不符合参考约束
            rest = self._partition_with_refs(
                kanji_text, reading, ref_readings, ki + 1, ri + try_len
            )
            if rest is not None:
                return [(kanji_text[ki], portion)] + rest
            tried.add(try_len)

        # 最后无约束匹配：当参考读音不匹配时，尝试所有未试过的长度
        for try_len in range(1, max_len + 1):
            if try_len in tried:
                continue
            rest = self._partition_with_refs(
                kanji_text, reading, ref_readings, ki + 1, ri + try_len
            )
            if rest is not None:
                return [(kanji_text[ki], reading[ri : ri + try_len])] + rest

        return None

    # ── 工具方法 ──

    @staticmethod
    def _kata_to_hira(text: str) -> str:
        """片假名 → 平假名"""
        result = []
        for ch in text:
            code = ord(ch)
            if 0x30A1 <= code <= 0x30F6:
                result.append(chr(code - 0x60))
            else:
                result.append(ch)
        return "".join(result)

    @staticmethod
    def _is_kanji(char: str) -> bool:
        code = ord(char)
        return (
            (0x4E00 <= code <= 0x9FFF)
            or (0x3400 <= code <= 0x4DBF)
            or (0xF900 <= code <= 0xFAFF)
            or code == 0x3005  # 々 IDEOGRAPHIC ITERATION MARK
        )

    @staticmethod
    def _is_kana(char: str) -> bool:
        code = ord(char)
        return (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)


# ──────────────────────────────────────────────
# WinRT IME 分析器（日语注音主引擎）
# ──────────────────────────────────────────────


class WinRTJapaneseUnavailable(ImportError):
    """WinRT 日语注音引擎不可用（通常缺少日语 IME 功能）。

    继承 ImportError 使 create_analyzer 的回退路径可统一捕获；
    ``reason`` 为机器可读原因，``guidance`` 为面向用户的安装引导文案。
    """

    def __init__(self, reason: str, guidance: str):
        self.reason = reason
        self.guidance = guidance
        super().__init__(f"WinRT Japanese engine unavailable ({reason})")


class WinRTAnalyzer(KanaDistributingAnalyzer):
    """基于 Windows.Globalization.JapanesePhoneticAnalyzer 的注音分析器。"""

    _MAX_LEN = 100

    def __init__(self):
        available, reason = winrt_japanese_status()
        if not available:
            if reason == "no_winrt_package":
                raise ImportError(
                    "winrt-Windows.Globalization is required. Install with: "
                    "pip install winrt-Windows.Globalization"
                )
            # 引擎缺失（缺日语 IME 功能）或其他异常：抛带安装引导的错误，
            # 供 create_analyzer 优雅回退，调用方可捕获后向用户展示引导。
            raise WinRTJapaneseUnavailable(reason, winrt_install_guidance())

        from winrt._winrt import init_apartment, STA

        try:
            init_apartment(STA)
        except OSError:
            # 线程已初始化为某 apartment（如 PyQt 主线程已是 STA）→ 忽略
            pass
        from winrt.windows.globalization import JapanesePhoneticAnalyzer

        self._jpa = JapanesePhoneticAnalyzer
        # 预热：首次调用有冷启开销，启动时空跑一次
        try:
            self._jpa.get_words("予熱")
        except Exception:
            pass
    def _get_pairs(self, text: str) -> List[Tuple[str, str]]:
        """整段 → [(原文 surface, 平假名读音)]，按 ≤100 字切块。"""
        pairs: List[Tuple[str, str]] = []
        for off in range(0, len(text), self._MAX_LEN):
            chunk = text[off : off + self._MAX_LEN]
            words = self._jpa.get_words(chunk)
            cursor = 0
            for w in words:
                disp_len = len(w.display_text)
                # surface 取原文切片（display_text 已全角归一，不可信）
                surface = chunk[cursor : cursor + disp_len]
                reading = w.yomi_text or surface
                pairs.append((surface, reading))
                cursor += disp_len
            # 兜底：若 GetWords 返回空（超长或异常），逐字回退
            if cursor < len(chunk):
                for c in chunk[cursor:]:
                    pairs.append((c, c))
        return pairs

    def get_reading(self, text: str) -> str:
        if not text:
            return ""
        try:
            return "".join(r for _, r in self._get_pairs(text))
        except Exception:
            return text

    def analyze(self, text: str) -> List[RubyResult]:
        if not text:
            return []
        try:
            pairs = self._get_pairs(text)
        except Exception:
            return [
                RubyResult(text=c, reading=c, start_idx=i, end_idx=i + 1)
                for i, c in enumerate(text)
            ]
        return self._results_from_pairs(pairs)


# ──────────────────────────────────────────────
# fugashi (MeCab) 分析器
# ──────────────────────────────────────────────


class FugashiAnalyzer(KanaDistributingAnalyzer):
    """基于 fugashi (MeCab 封装) 的注音分析器。

    提供形态素级别的日语注音分析，复用
    KanaDistributingAnalyzer 的读音分配逻辑（_results_from_pairs）。
    """

    def __init__(self):
        # lazy import: fugashi 和 unidic_lite 为可选依赖
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        # unidic_lite 通过 mecabrc 自动注册词典路径，
        # fugashi.Tagger() 无需显式 -d 参数即可找到词典
        try:
            self._tagger = fugashi.Tagger()
        except RuntimeError as e:
            raise ImportError(
                f"fugashi/MeCab initialization failed: {e}"
            ) from e

    @staticmethod
    def _reading_from_token(token) -> str:
        """从 fugashi token 中提取读音（平假名）。

        处理两种格式：
        - UniDic（unidic_lite）：feature 为 UnidicFeatures26 对象，通过 .kana 属性获取
        - IPADIC：feature 为逗号分隔字符串，从索引 7 获取读音
        """
        feat = token.feature

        # UniDic (unidic_lite): feature 是 UnidicFeatures26 对象，有 .kana 属性
        if hasattr(feat, "kana"):
            reading = feat.kana or feat.lForm or ""
        elif isinstance(feat, str):
            # IPADIC: 逗号分隔字符串，读音在索引 7
            fields = feat.split(",")
            if len(fields) >= 8:
                reading = fields[7]
            else:
                reading = ""
        else:
            reading = ""

        if not reading or reading == "*":
            return token.surface

        # 片假名 → 平假名
        return KanaDistributingAnalyzer._kata_to_hira(reading)

    def _get_pairs(self, text: str) -> List[Tuple[str, str]]:
        """整段 → [(原文 surface, 平假名读音)]。

        修复两个 fugashi 跨平台问题：
        - fugashi 跳过空格导致索引错位 → 补齐空格作独立 token
        - 々（踊り字）无注音 → 继承前一个漢字的读音
        """
        pairs: List[Tuple[str, str]] = []
        try:
            idx = 0  # 在原文中的游标位置
            prev_kanji_reading = ""  # 前一个漢字的读音，供 々 继承

            for token in self._tagger(text):
                surface = token.surface

                # fugashi 跳过空格；在空格位置插入 surface==reading 的占位 token，
                # 保证下游 _results_from_pairs 按 len(surface) 计算的索引与原文一致
                while idx < len(text) and text[idx] != surface[0]:
                    pairs.append((text[idx], text[idx]))
                    idx += 1

                reading = self._reading_from_token(token)

                # 々（踊り字）继承前一个漢字的读音
                # 当 fugashi 把 々 单独分词或与标点合并（如 "々，"），
                # 其特征字段不包含注音，reading 等同于 surface。
                # 此处检测并补上继承读音。
                if "々" in surface and reading == surface and prev_kanji_reading:
                    new_reading = []
                    for c in surface:
                        if c == "々":
                            new_reading.append(prev_kanji_reading)
                        else:
                            new_reading.append(c)
                    reading = "".join(new_reading)

                pairs.append((surface, reading))

                # 记录前一个漢字的注音，供后续 々 继承
                if any(self._is_kanji(c) for c in surface):
                    if reading and reading != surface:
                        prev_kanji_reading = reading

                idx += len(surface)
        except Exception:
            # 回退：逐字处理
            for c in text:
                pairs.append((c, c))
        return pairs

    def get_reading(self, text: str) -> str:
        if not text:
            return ""
        try:
            return "".join(r for _, r in self._get_pairs(text))
        except Exception:
            return text

    def analyze(self, text: str) -> List[RubyResult]:
        if not text:
            return []
        try:
            pairs = self._get_pairs(text)
        except Exception:
            return [
                RubyResult(text=c, reading=c, start_idx=i, end_idx=i + 1)
                for i, c in enumerate(text)
            ]
        return self._results_from_pairs(pairs)


class DummyAnalyzer(RubyAnalyzer):
    """虚拟注音分析器（用于测试）"""

    def analyze(self, text: str) -> List[RubyResult]:
        return [
            RubyResult(text=char, reading=char, start_idx=i, end_idx=i + 1)
            for i, char in enumerate(text)
        ]

    def get_reading(self, text: str) -> str:
        return text


# ──────────────────────────────────────────────
# WinRT 日语注音引擎：可用性探测 + 安装引导
# ──────────────────────────────────────────────

# 日语「Basic」语言功能（含微软日语 IME），JapanesePhoneticAnalyzer 的注音引擎来源
WINRT_JA_CAPABILITY = "Language.Basic~~~ja-JP~0.0.1.0"


def winrt_japanese_status() -> Tuple[bool, str]:
    """探测 WinRT 日语注音引擎是否可用。

    返回 (available, reason)。reason 取值：
      - "ok"                  引擎可用
      - "no_winrt_package"    未安装 winrt-Windows.Globalization
      - "engine_unavailable"  缺少日语 IME 功能（GetWords 返回空/无假名）
      - "error:<类型>"        其他异常

    探测方式：对确定含汉字读音的 "日本語" 调 GetWords，引擎缺失时会返回空
    或读音等于原文（无假名），据此判定。
    """
    try:
        from winrt._winrt import init_apartment, STA  # type: ignore
    except ImportError:
        return (False, "no_winrt_package")
    try:
        try:
            init_apartment(STA)
        except OSError:
            pass  # 线程已初始化为某 apartment
        from winrt.windows.globalization import JapanesePhoneticAnalyzer  # type: ignore

        words = JapanesePhoneticAnalyzer.get_words("日本語")
        reading = "".join(w.yomi_text or "" for w in words)
        has_kana = any("぀" <= c <= "ヿ" for c in reading)
        if words and reading and reading != "日本語" and has_kana:
            return (True, "ok")
        return (False, "engine_unavailable")
    except Exception as e:  # noqa: BLE001
        return (False, f"error:{type(e).__name__}")


def winrt_install_guidance() -> str:
    """缺少日语 IME 功能时的安装引导文案（面向用户）。"""
    return (
        "WinRT 日语注音需要 Windows 的日语功能（含日语 IME）。当前系统未安装。\n"
        "\n"
        "方式一（命令行，需管理员）：以管理员身份运行 PowerShell，执行\n"
        f"    Add-WindowsCapability -Online -Name {WINRT_JA_CAPABILITY}\n"
        "需联网，约几十 MB，从 Windows Update 下载（非完整语言包）。\n"
        "\n"
        "方式二（图形界面）：设置 → 时间和语言 → 语言和区域 → 添加语言 →\n"
        "搜索「日本語」→ 安装（勾选「基本键入/Basic typing」即可）。\n"
        "\n"
        "安装后无需把日语设为显示语言，也无需加入语言列表，重启应用即可生效。"
    )


def install_winrt_japanese(timeout: int = 600) -> Tuple[bool, str]:
    """通过 UAC 提权安装日语 IME 功能（Add-WindowsCapability）。

    用 ``Start-Process -Verb RunAs`` 触发 UAC 弹窗提权运行 PowerShell；
    用户拒绝提权或安装失败时返回 (False, 原因)，调用方应转为展示
    :func:`winrt_install_guidance` 引导用户手动安装。

    注意：本函数会弹出 UAC，**调用前应先向用户说明用途并征得同意**。

    返回 (success, message)。
    """
    import subprocess

    # 子进程以管理员身份执行安装并按结果设置退出码；-Verb RunAs 触发 UAC。
    inner = (
        f"$ErrorActionPreference='Stop';"
        f"try{{Add-WindowsCapability -Online -Name {WINRT_JA_CAPABILITY};exit 0}}"
        f"catch{{exit 2}}"
    )
    launcher = (
        "$p=Start-Process powershell "
        "-ArgumentList '-NoProfile','-NonInteractive','-Command',"
        f"'{inner}' -Verb RunAs -Wait -PassThru;"
        "exit $p.ExitCode"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", launcher],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return (False, "powershell_not_found")
    except subprocess.TimeoutExpired:
        return (False, "timeout")

    if proc.returncode == 0:
        # 安装命令成功；复测引擎确认可用
        ok, reason = winrt_japanese_status()
        return (ok, "ok" if ok else f"installed_but_{reason}")
    # 1603/RunAs 取消等：UAC 被拒或安装失败
    if "拒绝" in (proc.stderr or "") or proc.returncode in (1223, -1):
        return (False, "uac_declined")
    return (False, f"install_failed:{proc.returncode}")


def create_analyzer() -> RubyAnalyzer:
    """创建注音分析器（fugashi 唯一引擎）。

    使用 fugashi（MeCab 分词，跨平台）。fugashi 不可用时降级 DummyAnalyzer。
    """
    try:
        return FugashiAnalyzer()
    except ImportError:
        pass

    print("Warning: fugashi unavailable, using DummyAnalyzer")
    return DummyAnalyzer()


def _group_reading_for_character(reading: str, checkpoint_count: int) -> List[str]:
    """按字符 checkpoint 数量拆分读音为分段列表。

    入参: reading 读音串; checkpoint_count 节奏点数量。
    出参: 长度为 checkpoint_count 的分段列表（或单段列表）。

    #1: 纯 ASCII 英文 reading 不参与 mora 切分，整体作为一个 part。
    """
    if not reading:
        return []
    # 英文 reading：整体一个 part，不按 mora 切
    if all(c.isascii() and c.isalpha() for c in reading):
        return [reading]
    if checkpoint_count <= 1:
        return [reading]
    return split_ruby_for_checkpoints(reading, checkpoint_count)


def is_all_katakana(text: str) -> bool:
    """Return True when text is made only of katakana word characters."""
    if not text:
        return False
    for char in text:
        code = ord(char)
        if char in "ー・":
            continue
        if not (0x30A1 <= code <= 0x30FF):
            return False
    return True


def is_english_reading(reading: str) -> bool:
    """Return True for simple English readings from dictionary/LLM output."""
    if not reading:
        return False
    return any(c.isascii() and c.isalpha() for c in reading) and all(
        c.isascii() and (c.isalpha() or c in " -'")
        for c in reading
    )


def analyze_sentence_ruby(
    sentence: Sentence,
    analyzer: Optional[RubyAnalyzer] = None,
) -> None:
    """重新分析句子的 Ruby，并按 checkpoint 生成分组。"""
    analyzer = analyzer or create_analyzer()

    for char in sentence.characters:
        char.set_ruby(None)

    results = analyzer.analyze(sentence.text)
    for result in results:
        block_len = result.end_idx - result.start_idx
        if block_len <= 0:
            continue

        if block_len == 1:
            split_parts = [result.reading]
        else:
            split_parts = split_ruby_for_checkpoints(result.reading, block_len)

        for offset in range(block_len):
            char_idx = result.start_idx + offset
            if char_idx >= len(sentence.characters):
                break

            part = split_parts[offset] if offset < len(split_parts) else ""
            if not part:
                continue

            character = sentence.characters[char_idx]
            grouped_parts = _group_reading_for_character(part, character.check_count)
            if grouped_parts and "".join(grouped_parts) != character.char:
                character.set_ruby(Ruby(parts=[RubyPart(text=p) for p in grouped_parts if p]))
