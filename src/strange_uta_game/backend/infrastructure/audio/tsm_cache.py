"""磁盘缓存 TSM 渲染缓存。

设计：
- 切换播放速度（≠ 1.0x）时，后台 worker 用 Pedalboard 的 time_stretch 渲染，
  结果保存到磁盘缓存文件，不占用大量内存。
- 播放时从磁盘缓存读取到内存。
- 缓存文件位于用户目录下的 .cache 文件夹，更换歌曲或退出时自动清理。
- 1.0x 特殊路径：直接返回原始 PCM 引用，零渲染开销。
- 缓存文件采用 MP3 格式压缩，节省磁盘空间。

分块并行渲染架构：
- 最多同时渲染 2 个不同速度（MAX_SPEEDS）
- 每个速度内部，音频分成多个块（30秒/块），由多个 worker 并行处理
- 块之间有 10% 重叠，使用交叉恒定增益淡化拼接

缓存文件命名：{歌曲名}_{speed}x.mp3
"""

from __future__ import annotations

import heapq
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

import numpy as np
import soundfile as sf
from pedalboard import time_stretch
from pedalboard.io import AudioFile


ProgressCallback = Callable[[float, float], None]  # (speed, 0.0~1.0)
DoneCallback = Callable[[float], None]              # (speed,)
LoadProgressCallback = Callable[[str, float], None]  # (stage, 0.0~1.0)

_SPEED_QUANT = 2  # round(speed, 2)，0.01 精度
_CACHE_DIR_NAME = ".cache"
_MP3_QUALITY = 128  # MP3 比特率 (kbps)

# 分块渲染参数
_MAX_SPEEDS = 2         # 最多同时渲染的速度数
_CHUNK_SECONDS = 30     # 每块秒数
_OVERLAP_RATIO = 0.1    # 重叠比例 10%
_CPU_USAGE_RATIO = 0.7  # CPU 使用比例上限


def _get_max_workers() -> int:
    """根据 CPU 核心数计算全局最大 worker 数，不超过 70%。"""
    import os
    cpu_count = os.cpu_count() or 4
    return max(1, int(cpu_count * _CPU_USAGE_RATIO))


def _quantize(speed: float) -> float:
    return round(float(speed), _SPEED_QUANT)


def _get_cache_dir() -> Path:
    """获取缓存目录（软件工作目录下的 .cache 文件夹）"""
    cache_dir = Path.cwd() / _CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _get_cache_path(song_name: str, speed: float) -> Path:
    """获取缓存文件路径"""
    cache_dir = _get_cache_dir()
    q = _quantize(speed)
    filename = f"{song_name}_{q}x.mp3"
    return cache_dir / filename


def _get_source_mp3_path(song_name: str) -> Path:
    """获取源 MP3 文件路径"""
    cache_dir = _get_cache_dir()
    return cache_dir / f"{song_name}_source.mp3"


def clear_cache() -> None:
    """清空所有缓存文件"""
    cache_dir = _get_cache_dir()
    for f in cache_dir.glob("*.mp3"):
        try:
            f.unlink()
        except Exception:
            pass
    print(f"[TSM] Cache cleared: {cache_dir}")


def clear_cache_for_song(song_name: str) -> None:
    """清空指定歌曲的所有缓存文件（包括源 MP3）"""
    cache_dir = _get_cache_dir()
    for f in cache_dir.glob(f"{song_name}_*.mp3"):
        try:
            f.unlink()
        except Exception:
            pass
    source = cache_dir / f"{song_name}_source.mp3"
    if source.exists():
        try:
            source.unlink()
        except Exception:
            pass
    print(f"[TSM] Cache cleared for song: {song_name}")


@dataclass
class ChunkInfo:
    """分块信息"""
    index: int
    src_start: int       # 源 PCM 中的起始采样（含 overlap）
    src_end: int         # 源 PCM 中的结束采样（含 overlap）
    core_start: int      # 核心区域起始（不含 overlap）
    core_end: int        # 核心区域结束（不含 overlap）


def _split_chunks(total_samples: int, sample_rate: int) -> List[ChunkInfo]:
    """将音频分成多个块，每块向两侧扩展 overlap 用于交叉淡化。

    例如 60 秒音频，30 秒/块，3 秒 overlap：
    块0: core=[0, 30s),      src=[0, 33s)      右侧扩展3秒
    块1: core=[30s, 60s),    src=[27s, 60s)    左侧扩展3秒

    重叠区域：27~33秒（6秒），两个块都有数据。
    淡化过程：27~33秒，块0淡出(1→0)，块1淡入(0→1)

    Returns:
        ChunkInfo 列表
    """
    chunk_samples = _CHUNK_SECONDS * sample_rate
    overlap_samples = int(chunk_samples * _OVERLAP_RATIO)

    chunks = []
    core_start = 0
    idx = 0
    while core_start < total_samples:
        core_end = min(core_start + chunk_samples, total_samples)
        # 左侧：非第一块向左扩展 overlap
        src_start = max(0, core_start - overlap_samples) if idx > 0 else core_start
        # 右侧：非最后一块向右扩展 overlap
        src_end = min(total_samples, core_end + overlap_samples) if core_end < total_samples else core_end

        chunks.append(ChunkInfo(
            index=idx,
            src_start=src_start,
            src_end=src_end,
            core_start=core_start,
            core_end=core_end,
        ))

        core_start = core_end
        idx += 1

    return chunks


def _crossfade_constant_power(a: np.ndarray, b: np.ndarray, overlap_len: int) -> np.ndarray:
    """交叉恒定功率淡化拼接。

    使用 sin/cos 曲线，满足 a² + b² = 1，总功率始终为 1。

    Args:
        a: 前段数据 (n, ch)
        b: 后段数据 (n, ch)
        overlap_len: 重叠长度

    Returns:
        拼接后的数据
    """
    if overlap_len <= 0 or len(a) == 0 or len(b) == 0:
        return np.concatenate([a, b], axis=0)

    actual_overlap = min(overlap_len, len(a), len(b))
    if actual_overlap <= 0:
        return np.concatenate([a, b], axis=0)

    # 恒定功率淡化曲线：cos/sin，满足 a² + b² = 1
    t = np.linspace(0, np.pi / 2, actual_overlap, dtype=np.float32)
    fade_out = np.cos(t)  # 从 1 到 0
    fade_in = np.sin(t)   # 从 0 到 1

    # 扩展维度以匹配声道
    if a.ndim == 2:
        fade_out = fade_out[:, np.newaxis]
        fade_in = fade_in[:, np.newaxis]

    # 混合重叠区域
    a_tail = a[-actual_overlap:] * fade_out
    b_head = b[:actual_overlap] * fade_in
    mixed = a_tail + b_head

    # 拼接：a 非重叠部分 + 混合部分 + b 非重叠部分
    result = np.concatenate([a[:-actual_overlap], mixed, b[actual_overlap:]], axis=0)
    return result


@dataclass
class SpeedTask:
    """单个速度的渲染任务"""
    speed: float
    priority: int
    progress_cb: Optional[ProgressCallback]
    done_cb: Optional[DoneCallback]
    version: int
    chunks: List[ChunkInfo] = field(default_factory=list)
    results: Dict[int, np.ndarray] = field(default_factory=dict)  # {chunk_index: pcm}
    pending_chunks: Set[int] = field(default_factory=set)  # 待处理的块 index
    futures: List[Future] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    completed: threading.Event = field(default_factory=threading.Event)
    cancelled: bool = False


# 全局线程池（所有速度任务共享，总并发数不超过 CPU 70%）
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """获取全局线程池（懒初始化）。"""
    global _executor
    with _executor_lock:
        if _executor is None:
            max_workers = _get_max_workers()
            _executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="TSMWorker")
            print(f"[TSM] Thread pool initialized with {max_workers} workers")
        return _executor


class TSMRenderCache:
    """磁盘缓存 TSM 渲染缓存，支持分块并行渲染和优先级队列。

    架构：加载时将原始音频保存为源 MP3，后续所有操作（播放、TSM 渲染）
    都从这份 MP3 读取，减少内存占用。
    """

    # MP3 支持的采样率
    _MP3_TARGET_SR = 44100

    def __init__(self) -> None:
        self._source_mp3_path: Optional[Path] = None
        self._sample_rate: int = 0
        self._channels: int = 0
        self._song_name: str = ""

        # 速度级别任务队列：(priority, speed, progress_cb, done_cb)
        self._speed_queue: list = []
        self._queue_lock = threading.Lock()

        # 正在渲染的速度任务：{speed: SpeedTask}
        self._active_tasks: Dict[float, SpeedTask] = {}
        self._active_lock = threading.Lock()

        # 版本控制
        self._render_version: int = 0
        self._lock = threading.Lock()

        # 调度线程
        self._scheduler_thread: Optional[threading.Thread] = None
        self._scheduler_stop = threading.Event()

    # ---------- 加载 ----------

    def set_source(
        self,
        song_name: str,
        original_pcm: np.ndarray,
        sample_rate: int,
        progress_cb: Optional[LoadProgressCallback] = None,
    ) -> None:
        """切换原始音频。将原始 PCM 保存为源 MP3，清空旧缓存。"""
        self._cancel_all_and_wait()
        with self._lock:
            if progress_cb:
                progress_cb("清理旧缓存...", 0.0)
            clear_cache()

            self._song_name = song_name
            channels = int(original_pcm.shape[1]) if original_pcm.ndim > 1 else 1

            if progress_cb:
                progress_cb("转换为 MP3...", 0.1)
            source_path = _get_source_mp3_path(song_name)
            actual_sr = self._save_source_as_mp3(
                original_pcm, sample_rate, channels, source_path, progress_cb
            )
            self._source_mp3_path = source_path
            self._sample_rate = actual_sr
            self._channels = channels

            if progress_cb:
                progress_cb("完成", 1.0)
            print(f"[TSM] Source MP3 saved: {source_path} ({actual_sr}Hz, {channels}ch)")

    def _save_source_as_mp3(
        self,
        pcm: np.ndarray,
        sample_rate: int,
        channels: int,
        path: Path,
        progress_cb: Optional[LoadProgressCallback] = None,
    ) -> int:
        """将 PCM 保存为 MP3，如果采样率不支持则降采样。"""
        mp3_rates = [32000, 44100, 48000]
        target_sr = sample_rate
        if sample_rate not in mp3_rates:
            target_sr = min(mp3_rates, key=lambda r: abs(r - sample_rate))
            print(f"[TSM] Resampling {sample_rate}Hz -> {target_sr}Hz for MP3")

        data = pcm
        if target_sr != sample_rate:
            if progress_cb:
                progress_cb("降采样中...", 0.3)
            from pedalboard.io import StreamResampler
            resampler = StreamResampler(sample_rate, target_sr, channels)
            resampled = resampler.process(pcm.T)
            tail = resampler.process(None)
            resampled_full = np.concatenate([resampled, tail], axis=1)
            data = resampled_full.T.astype(np.float32)

        if progress_cb:
            progress_cb("编码 MP3...", 0.6)
        mp3_bytes = AudioFile.encode(
            data.T,
            samplerate=target_sr,
            format="mp3",
            num_channels=channels,
            quality=_MP3_QUALITY,
        )
        if progress_cb:
            progress_cb("保存文件...", 0.9)
        with open(path, "wb") as f:
            f.write(mp3_bytes)

        return target_sr

    def clear(self) -> None:
        self._cancel_all_and_wait()
        with self._lock:
            if self._song_name:
                clear_cache_for_song(self._song_name)
            self._source_mp3_path = None

    # ---------- 查询 ----------

    def get(self, speed: float) -> Optional[np.ndarray]:
        """从磁盘缓存读取。命中返回 ndarray；未命中返回 None。"""
        if self._source_mp3_path is None:
            return None
        q = _quantize(speed)
        if abs(q - 1.0) < 1e-9:
            return self._load_source_pcm()

        cache_path = _get_cache_path(self._song_name, q)
        if cache_path.exists():
            try:
                data = self._load_from_mp3(cache_path)
                return data
            except Exception as e:
                print(f"[TSM] Failed to read cache: {e}")
        return None

    def _load_source_pcm(self) -> Optional[np.ndarray]:
        """从源 MP3 加载 PCM 数据。"""
        if self._source_mp3_path is None or not self._source_mp3_path.exists():
            return None
        return self._load_from_mp3(self._source_mp3_path)

    def _load_from_mp3(self, path: Path) -> np.ndarray:
        """从 MP3 文件加载 PCM 数据。返回 (samples, channels) float32。"""
        with AudioFile(str(path)) as f:
            audio = f.read(f.frames)
        return audio.T.astype(np.float32)

    # ---------- 渲染 ----------

    def ensure(
        self,
        speed: float,
        priority: int = 99,
        progress_cb: Optional[ProgressCallback] = None,
        done_cb: Optional[DoneCallback] = None,
    ) -> Optional[np.ndarray]:
        """确保 ``speed`` 对应的 PCM 就绪。"""
        if self._source_mp3_path is None:
            return None
        q = _quantize(speed)
        if abs(q - 1.0) < 1e-9:
            return self._load_source_pcm()

        # 检查磁盘缓存
        cached = self.get(q)
        if cached is not None:
            print(f"[TSM] Cache hit for speed {q}x")
            return cached

        # 检查是否已在渲染或队列中
        with self._active_lock:
            if q in self._active_tasks:
                print(f"[TSM] Already rendering speed {q}x, skipping")
                return None

        with self._queue_lock:
            for _, queued_speed, _, _ in self._speed_queue:
                if abs(queued_speed - q) < 1e-9:
                    print(f"[TSM] Already queued for speed {q}x, skipping")
                    return None

        with self._queue_lock:
            heapq.heappush(self._speed_queue, (priority, q, progress_cb, done_cb))
            print(f"[TSM] Queued render for speed {q}x with priority {priority}")

        self._ensure_scheduler_running()
        return None

    # ---------- 调度 ----------

    def _ensure_scheduler_running(self) -> None:
        """确保调度线程在运行。"""
        with self._lock:
            if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
                return
            self._scheduler_stop.clear()
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop, daemon=True, name="TSMScheduler"
            )
            self._scheduler_thread.start()

    def _scheduler_loop(self) -> None:
        """调度线程：从速度队列取出任务，将块提交到全局线程池。"""
        while not self._scheduler_stop.is_set():
            task = None
            with self._queue_lock:
                if self._speed_queue:
                    task = heapq.heappop(self._speed_queue)

            if task is None:
                with self._active_lock:
                    if not self._active_tasks:
                        break
                time.sleep(0.1)
                continue

            priority, speed, progress_cb, done_cb = task

            with self._lock:
                current_version = self._render_version

            # 等待有空闲速度槽位
            while not self._scheduler_stop.is_set():
                with self._active_lock:
                    if len(self._active_tasks) < _MAX_SPEEDS:
                        break
                time.sleep(0.05)

            if self._scheduler_stop.is_set():
                break

            # 读取源 PCM 并分块
            source_pcm = self._load_source_pcm()
            if source_pcm is None:
                continue

            chunks = _split_chunks(len(source_pcm), self._sample_rate)
            print(f"[TSM] Speed {speed}x: {len(chunks)} chunks, submitting to global pool")

            # 创建速度任务
            speed_task = SpeedTask(
                speed=speed,
                priority=priority,
                progress_cb=progress_cb,
                done_cb=done_cb,
                version=current_version,
                chunks=chunks,
                pending_chunks=set(range(len(chunks))),
            )

            with self._active_lock:
                self._active_tasks[speed] = speed_task

            # 将所有块提交到全局线程池
            executor = _get_executor()
            for chunk in chunks:
                future = executor.submit(
                    self._render_chunk,
                    speed_task, source_pcm, chunk, current_version,
                )
                future.add_done_callback(self._on_chunk_done)
                speed_task.futures.append(future)

    def _render_chunk(
        self,
        task: SpeedTask,
        source_pcm: np.ndarray,
        chunk: ChunkInfo,
        render_version: int,
    ) -> Optional[tuple]:
        """渲染单个块（在线程池中执行）。返回 (chunk_index, rendered_pcm) 或 None。"""
        if task.cancelled or self._render_version != render_version:
            return None

        try:
            chunk_pcm = source_pcm[chunk.src_start:chunk.src_end]
            chunk_pcm = np.ascontiguousarray(chunk_pcm, dtype=np.float32)

            rendered = time_stretch(
                chunk_pcm,
                float(self._sample_rate),
                stretch_factor=task.speed,
            )

            if task.cancelled or self._render_version != render_version:
                return None

            return (chunk.index, rendered.astype(np.float32))

        except Exception as e:
            print(f"[TSM] Chunk {chunk.index} render error for speed {task.speed}x: {e}")
            return None

    def _on_chunk_done(self, future: Future) -> None:
        """块完成回调（在线程池 worker 线程中执行）。"""
        result = future.result()
        if result is None:
            return

        chunk_index, rendered_pcm = result

        # 找到对应的 SpeedTask
        task = None
        with self._active_lock:
            for t in self._active_tasks.values():
                if chunk_index in t.pending_chunks:
                    task = t
                    break

        if task is None or task.cancelled:
            return

        with task.lock:
            task.results[chunk_index] = rendered_pcm
            task.pending_chunks.discard(chunk_index)
            progress = len(task.results) / len(task.chunks)

        # 报告进度
        if task.progress_cb is not None:
            try:
                task.progress_cb(task.speed, progress * 0.9)
            except Exception:
                pass

        # 检查是否所有块都完成了
        self._check_and_finalize(task)

    def _check_and_finalize(self, task: SpeedTask) -> None:
        """检查所有块是否完成，完成后拼接并保存。"""
        with task.lock:
            if task.completed.is_set():
                return
            if len(task.results) < len(task.chunks):
                return
            # 检查是否有失败的块
            for idx, result in task.results.items():
                if result is None:
                    return
            task.completed.set()

        # 所有块完成，拼接
        try:
            print(f"[TSM] All chunks done for speed {task.speed}x, merging...")
            if task.progress_cb:
                try:
                    task.progress_cb(task.speed, 0.95)
                except Exception:
                    pass

            final_pcm = self._merge_chunks(task)

            # 保存到磁盘
            cache_path = _get_cache_path(self._song_name, task.speed)
            self._save_as_mp3(final_pcm, cache_path)
            print(f"[TSM] Render complete for speed {task.speed}x, saved to {cache_path}")

            if task.progress_cb:
                try:
                    task.progress_cb(task.speed, 1.0)
                except Exception:
                    pass

            if task.done_cb:
                try:
                    task.done_cb(task.speed)
                except Exception as e:
                    print(f"[TSM] done_cb error: {e}")

        except Exception as e:
            print(f"[TSM] Merge error for speed {task.speed}x: {e}")
        finally:
            with self._active_lock:
                self._active_tasks.pop(task.speed, None)

    def _merge_chunks(self, task: SpeedTask) -> np.ndarray:
        """使用交叉恒定功率淡化拼接所有块。

        淡化发生在 overlap 区域（6秒）：
        - 块0的尾部(27~33s)淡出
        - 块1的头部(0~6s，对应原始27~33s)淡入

        例如 60 秒音频，30 秒/块，3 秒 overlap：
        - 块0: src=[0, 33s)，提取 0~33s
        - 块1: src=[27s, 60s)，提取 0~33s（从src开头取33s）
        - 混合后：0~27s + 混合(27~33s) + 33~60s = 60s
        """
        overlap_samples = int(_CHUNK_SECONDS * self._sample_rate * _OVERLAP_RATIO)

        # 按 index 排序
        sorted_chunks = sorted(task.chunks, key=lambda c: c.index)

        # 提取每个块的完整 src 区域
        segments = []
        for chunk in sorted_chunks:
            rendered = task.results[chunk.index]
            if rendered is None:
                raise ValueError(f"Chunk {chunk.index} is None")

            # 提取整个 src 区域
            segment = rendered  # 已经是整个 src 的渲染结果
            segments.append(segment)

        if not segments:
            return np.zeros((0, self._channels), dtype=np.float32)

        # 计算变速后的 overlap 长度（6秒）
        overlap_rendered = int(overlap_samples * 2 / task.speed) if len(segments) > 1 else 0

        # 逐段交叉淡化拼接
        result = segments[0]
        for i in range(1, len(segments)):
            result = _crossfade_constant_power(result, segments[i], overlap_rendered)

        # 裁剪到正确长度（core 区域总长度）
        total_core_samples = sum(c.core_end - c.core_start for c in sorted_chunks)
        total_rendered = int(total_core_samples / task.speed)
        result = result[:total_rendered]

        return result

        return result

    def _save_as_mp3(self, pcm: np.ndarray, path: Path) -> None:
        """将 PCM 数据保存为 MP3 文件。"""
        mp3_bytes = AudioFile.encode(
            pcm.T,
            samplerate=self._sample_rate,
            format="mp3",
            num_channels=self._channels,
            quality=_MP3_QUALITY,
        )
        with open(path, "wb") as f:
            f.write(mp3_bytes)

    def _cancel_all_and_wait(self) -> None:
        """取消所有渲染任务并等待完成。"""
        with self._lock:
            self._render_version += 1

        # 取消所有活跃任务
        with self._active_lock:
            for task in self._active_tasks.values():
                task.cancelled = True
            tasks = list(self._active_tasks.values())

        # 等待所有 future 完成
        for task in tasks:
            for future in task.futures:
                future.cancel()

        with self._active_lock:
            self._active_tasks.clear()

        with self._queue_lock:
            self._speed_queue.clear()

        self._scheduler_stop.set()
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=2.0)
        self._scheduler_thread = None
