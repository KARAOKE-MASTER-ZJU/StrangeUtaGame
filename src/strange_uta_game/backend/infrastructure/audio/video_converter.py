"""视频音频提取模块。

使用 PyAV 从视频文件中提取音频，并压缩为 MP3 临时文件。
需要系统安装 FFmpeg 并添加到环境变量。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# 与 tsm_cache.py 保持一致的进度回调签名
LoadProgressCallback = Callable[[str, float], None]  # (stage, 0.0~1.0)

# 常见的视频/音频容器扩展名（需要 FFmpeg 处理）
VIDEO_EXTENSIONS = {
    # 常见视频容器（需 FFmpeg 抽取音轨）
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".3gp", ".vob", ".mts", ".m2ts",
    ".rm", ".rmvb", ".asf", ".f4v", ".ogv",
    # 仍需 FFmpeg 的音频容器（BASS 无对应插件）
    ".dts",
}

# MP3 编码参数
_MP3_QUALITY = 128  # kbps
_TARGET_SAMPLE_RATE = 44100  # Hz
_CACHE_DIR_NAME = ".cache"


def _get_cache_dir() -> Path:
    """获取提取音频的存放目录（程序所在目录下的 .cache/extracted 子文件夹）。

    注意：必须放在 .cache 的子目录里，而不是 .cache 根目录。TSM 引擎加载时会调用
    clear_cache() 用 glob(".cache/*.mp3") 非递归删除 .cache 根目录下的所有 mp3——
    若提取音频直接放在 .cache 根目录，加载视频后切换引擎重载时该文件已被删除，导致
    "找不到 ffmpeg 提取的音频文件"。放到子目录可避开这次清理，使其在切换引擎/重载时
    仍然有效。
    """
    program_dir = Path(sys.argv[0]).resolve().parent
    cache_dir = program_dir / _CACHE_DIR_NAME / "extracted"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def is_video_file(file_path: str) -> bool:
    """判断文件扩展名是否为视频/音频容器格式。"""
    return Path(file_path).suffix.lower() in VIDEO_EXTENSIONS


def is_ffmpeg_available() -> bool:
    """检测系统环境变量中是否有 ffmpeg。"""
    return shutil.which("ffmpeg") is not None


def extract_audio(video_path: str, progress_cb: Optional[LoadProgressCallback] = None) -> str:
    """从视频文件中提取音频并压缩为 MP3 临时文件。

    Args:
        video_path: 视频文件路径
        progress_cb: 进度回调 (stage, 0.0~1.0)

    Returns:
        生成的临时 MP3 文件路径

    Raises:
        FileNotFoundError: 视频文件不存在
        RuntimeError: FFmpeg 不可用或提取失败
        ValueError: 视频文件不包含音频流
    """
    if not Path(video_path).is_file():
        raise FileNotFoundError(f"文件不存在: {video_path}")

    if not is_ffmpeg_available():
        raise RuntimeError(
            "当前环境未检测到 FFmpeg，请安装 FFmpeg 并将其添加到系统环境变量后重试。"
        )

    try:
        import av
    except ImportError:
        raise RuntimeError("PyAV 库未安装，请执行: pip install av")

    if progress_cb:
        progress_cb("正在打开视频文件...", 0.0)

    try:
        container = av.open(video_path)
    except av.AVError as e:
        raise RuntimeError(f"无法打开视频文件: {e}")

    # 检查是否有音频流
    audio_stream = next((s for s in container.streams if s.type == "audio"), None)
    if audio_stream is None:
        container.close()
        raise ValueError("该文件不包含音频流")

    if progress_cb:
        progress_cb("正在提取音频...", 0.1)

    # 解码音频为 PCM
    pcm_data = _decode_audio(container, audio_stream, progress_cb)
    container.close()

    if progress_cb:
        progress_cb("正在压缩为 MP3...", 0.8)

    # 压缩为 MP3 临时文件（使用视频文件名作为基础）
    video_stem = Path(video_path).stem
    temp_path = _encode_to_mp3(pcm_data, audio_stream.rate, video_stem, progress_cb)

    if progress_cb:
        progress_cb("音频提取完成", 1.0)

    return temp_path


def _decode_audio(container, audio_stream, progress_cb: Optional[LoadProgressCallback]) -> np.ndarray:
    """解码音频流为 float32 PCM 数据。"""
    frames = []
    total_duration = float(audio_stream.duration * audio_stream.time_base) if audio_stream.duration else 0

    for frame in container.decode(audio_stream):
        # 转换为 float32
        arr = frame.to_ndarray().astype(np.float32)
        # PyAV 返回的形状是 (channels, samples)，需要转置为 (samples, channels)
        if arr.ndim == 2:
            arr = arr.T
        else:
            arr = arr.reshape(-1, 1)
        frames.append(arr)

        # 更新进度
        if progress_cb and total_duration > 0:
            current_time = float(frame.pts * audio_stream.time_base) if frame.pts else 0
            progress = min(0.7, 0.1 + 0.6 * (current_time / total_duration))
            progress_cb("正在提取音频...", progress)

    if not frames:
        raise RuntimeError("未能解码任何音频帧")

    return np.concatenate(frames, axis=0)


def _encode_to_mp3(pcm_data: np.ndarray, sample_rate: int, base_name: str, progress_cb: Optional[LoadProgressCallback]) -> str:
    """将 PCM 数据编码为 MP3 临时文件。"""
    from pedalboard.io import AudioFile

    # 在 .cache 目录创建临时文件（使用视频文件名）
    cache_dir = _get_cache_dir()
    temp_filename = f"{base_name}.mp3"
    temp_path = str(cache_dir / temp_filename)

    try:
        channels = pcm_data.shape[1] if pcm_data.ndim > 1 else 1

        # 如果采样率不匹配，进行简单重采样
        if sample_rate != _TARGET_SAMPLE_RATE:
            if progress_cb:
                progress_cb("正在重采样...", 0.85)
            pcm_data = _resample(pcm_data, sample_rate, _TARGET_SAMPLE_RATE)
            sample_rate = _TARGET_SAMPLE_RATE

        # 使用 pedalboard 编码 MP3（与 tsm_cache.py 保持一致的方式）
        # pedalboard 需要 (channels, samples) 格式
        if pcm_data.ndim == 2:
            encode_data = pcm_data.T
        else:
            encode_data = pcm_data

        mp3_bytes = AudioFile.encode(
            encode_data,
            samplerate=sample_rate,
            format="mp3",
            num_channels=channels,
            quality=_MP3_QUALITY,
        )

        # 写入文件
        with open(temp_path, "wb") as f:
            f.write(mp3_bytes)

    except Exception as e:
        # 清理临时文件
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise RuntimeError(f"MP3 编码失败: {e}")

    return temp_path


def _resample(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """简单线性插值重采样。"""
    if src_rate == dst_rate:
        return data

    duration = len(data) / src_rate
    src_times = np.arange(len(data)) / src_rate
    dst_times = np.arange(int(duration * dst_rate)) / dst_rate

    if data.ndim == 2:
        result = np.zeros((len(dst_times), data.shape[1]), dtype=np.float32)
        for ch in range(data.shape[1]):
            result[:, ch] = np.interp(dst_times, src_times, data[:, ch])
    else:
        result = np.interp(dst_times, src_times, data).astype(np.float32)

    return result
