"""唤起独立 ``Updater.exe`` 接管更新流程。

设计要点：

1. **位置约定** —— ``Updater.exe`` 与主程序 ``StrangeUtaGame.exe`` 同目录。这是
   PyInstaller 单目录打包后最自然的位置；``build.py`` 会保证拷贝到位。
   开发环境下（直接 ``python main.py``）不应该出现 Updater.exe，因此本模块在
   未找到 Updater.exe 时返回 ``Result(launched=False, reason=...)``，由调用方
   决定如何提示。

2. **不被自身锁定** —— Updater.exe 也是 Windows 进程，正在运行时不能被替换。
   我们在调起前把 Updater.exe 复制到 ``%TEMP%/StrangeUtaGameUpdater/Updater.exe``
   再执行 temp 副本；安装完毕后由 Updater.exe 自己清理临时目录。

3. **主程序退出顺序** —— 主程序退出后 Updater.exe 才能解锁 ``StrangeUtaGame.exe``
   与 ``_internal/``。本模块在 ``launch_updater`` 中传入主程序 PID，由 Updater
   等待 PID 退出后再开始替换；调用方在调用本函数后应立刻 ``QApplication.quit``。

4. **自更新** —— 主程序在启动 Updater 之前，先尝试从远端 app part zip 中提取
   新的 Updater.exe 并替换本地版本，确保旧 updater 也能被更新。
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# 与主程序同目录下的 Updater.exe 名字。
UPDATER_EXE_NAME = "Updater.exe"
# 临时目录名（在 %TEMP% 下）。
TMP_DIR_NAME = "StrangeUtaGameUpdater"


@dataclass
class LaunchPlan:
    """启动 Updater 的输入参数（供 :func:`launch_updater` 使用）。"""
    app_dir: Path                                   # 主程序所在目录
    app_exe_name: str                               # 主程序 EXE 文件名
    target_version: str                             # 目标版本号（纯版本号，非 tag）
    target_tag: str                                 # 远端 release tag
    asset_name: str                                 # 资产文件名（zip）
    download_urls: List[Tuple[str, str]]            # [(source_id, url), ...]
    proxy_url: str = ""                             # 例 ``http://127.0.0.1:7890``
    internal_dir_name: str = "_internal"            # PyInstaller 内部目录名
    expected_sha256: str = ""                       # 可选：发布方提供的 SHA256
    launch_after_update: bool = True                # 安装完是否自动启动主程序

    # 仅供 LaunchPlan.command_args 内部使用
    extras: List[str] = field(default_factory=list)

    def command_args(self, updater_exe: Path, current_pid: int) -> List[str]:
        """生成传给 Updater.exe 的命令行参数。"""
        args: List[str] = [str(updater_exe)]
        args += ["--app-dir", str(self.app_dir)]
        args += ["--app-exe", self.app_exe_name]
        args += ["--target-version", self.target_version]
        args += ["--target-tag", self.target_tag]
        args += ["--asset-name", self.asset_name]
        args += ["--internal-name", self.internal_dir_name]
        args += ["--pid", str(current_pid)]
        if self.proxy_url:
            args += ["--proxy", self.proxy_url]
        if self.expected_sha256:
            args += ["--sha256", self.expected_sha256]
        if not self.launch_after_update:
            args += ["--no-launch"]
        # ``--url`` 允许重复，按用户配置的源排序提供
        for source_id, url in self.download_urls:
            args += ["--url", f"{source_id}|{url}"]
        args += self.extras
        return args


@dataclass
class LaunchResult:
    """:func:`launch_updater` 的返回。"""
    launched: bool
    updater_path: str = ""
    temp_copy_path: str = ""
    pid: int = 0
    reason: str = ""


# ───────────────────────── 工具 ─────────────────────────


def find_app_dir() -> Path:
    """返回主程序根目录（与 ``Updater.exe`` 同级）。

    PyInstaller 模式下，``sys.executable`` 指向 ``StrangeUtaGame.exe``，因此
    其父目录就是我们要的根。开发环境下回退到项目根（``main.py`` 所在）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # 开发环境兜底
    return Path(sys.argv[0]).resolve().parent


def find_app_exe_name() -> str:
    """主程序 EXE 文件名。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).name
    return "StrangeUtaGame.exe"


def find_updater_exe(app_dir: Optional[Path] = None) -> Optional[Path]:
    """定位与主程序同目录的 ``Updater.exe``；找不到返回 ``None``。"""
    app_dir = app_dir or find_app_dir()
    p = app_dir / UPDATER_EXE_NAME
    if p.exists():
        return p
    # 兼容：放在 _internal/updater/ 下的版本
    p2 = app_dir / "_internal" / "updater" / UPDATER_EXE_NAME
    if p2.exists():
        return p2
    return None


def _copy_updater_to_temp(updater_exe: Path) -> Path:
    """把 Updater.exe 复制到临时目录，避免自身被锁。"""
    tmp_dir = Path(tempfile.gettempdir()) / TMP_DIR_NAME
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dest = tmp_dir / UPDATER_EXE_NAME
    # 已存在的副本可能正被另一次更新流程占用 —— 用唯一时间戳后缀兜底
    try:
        shutil.copy2(str(updater_exe), str(dest))
    except PermissionError:
        import time
        dest = tmp_dir / f"Updater-{int(time.time())}.exe"
        shutil.copy2(str(updater_exe), str(dest))
    return dest


# ───────────────────────── 主入口 ─────────────────────────


def _update_updater_from_remote(
    plan: LaunchPlan,
    proxies: Optional[dict] = None,
) -> bool:
    """尝试从远端 app part zip 提取并替换本地 Updater.exe。

    解决鸡生蛋问题：已分发的旧 Updater.exe 没有自更新逻辑，无法更新自身。
    主程序在启动 updater 之前调用本函数，先拉取 app part zip，提取新的
    Updater.exe 并替换本地版本。这样即使旧 updater 没有自更新代码，
    也能通过主程序间接触发更新。

    返回 ``True`` 表示成功更新了 Updater.exe（或已是最新），``False`` 表示
    失败但不影响后续流程（降级使用旧 updater）。
    """
    import requests

    app_dir = plan.app_dir
    local_updater = app_dir / UPDATER_EXE_NAME

    # 构造 app part zip 的 URL（从 download_urls 推导）
    # download_urls 格式: [(source_id, full_zip_url), ...]
    app_zip_name = f"StrangeUtaGame-v{plan.target_version}-app.zip"
    candidates: List[Tuple[str, str]] = []
    for source_id, url in plan.download_urls:
        # URL 形如 https://.../StrangeUtaGame-vX.Y.Z.zip
        # 替换为  https://.../StrangeUtaGame-vX.Y.Z-app.zip
        prefix = url.rsplit("/", 1)[0]
        app_url = f"{prefix}/{app_zip_name}"
        candidates.append((source_id, app_url))

    proxies_dict = {"http": plan.proxy_url, "https": plan.proxy_url} if plan.proxy_url else None

    for source_id, url in candidates:
        log.info("[self-update] 尝试下载 app part: %s", url)
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "StrangeUtaGame-MainApp/self-update"},
                proxies=proxies_dict,
                timeout=(10, 60),
                allow_redirects=True,
            )
            if resp.status_code != 200:
                log.warning("[self-update] HTTP %d from %s", resp.status_code, source_id)
                continue

            # 从 zip 中提取 Updater.exe
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                names = zf.namelist()
                # 查找 Updater.exe（可能在根目录或子目录下）
                updater_entry = None
                for name in names:
                    if name.endswith(UPDATER_EXE_NAME) and not name.endswith("/"):
                        updater_entry = name
                        break
                if updater_entry is None:
                    log.warning("[self-update] app part zip 中未找到 %s", UPDATER_EXE_NAME)
                    continue

                new_bytes = zf.read(updater_entry)

                # 比较内容是否一致（避免无意义替换）
                if local_updater.exists():
                    local_bytes = local_updater.read_bytes()
                    if local_bytes == new_bytes:
                        log.info("[self-update] Updater.exe 内容一致，无需更新")
                        return True

                # 写入新 Updater.exe
                local_updater.write_bytes(new_bytes)
                log.info("[self-update] 已更新 Updater.exe（%d bytes）", len(new_bytes))
                return True

        except Exception as e:
            log.warning("[self-update] 从 %s 下载/提取失败: %s", source_id, e)
            continue

    log.warning("[self-update] 所有源均失败，将使用旧版 Updater.exe")
    return False


def launch_updater(plan: LaunchPlan) -> LaunchResult:
    """根据 ``plan`` 启动独立 Updater.exe；调用后调用方应立刻退出 Qt 应用。

    返回 :class:`LaunchResult`；``launched=False`` 时由调用方提示用户。
    """
    updater = find_updater_exe(plan.app_dir)
    if updater is None:
        return LaunchResult(
            launched=False,
            reason=(
                "未找到 Updater.exe。请重新下载完整安装包，或确保 "
                "Updater.exe 与主程序位于同一目录。"
            ),
        )

    # 先尝试从远端更新 Updater.exe 自身（解决旧 updater 无自更新逻辑的问题）
    try:
        _update_updater_from_remote(plan)
        # 重新定位（可能已被更新）
        updater = find_updater_exe(plan.app_dir) or updater
    except Exception as e:
        log.warning("自更新 Updater.exe 失败（忽略，继续使用旧版）: %s", e)

    try:
        temp_copy = _copy_updater_to_temp(updater)
    except OSError as e:
        return LaunchResult(
            launched=False,
            updater_path=str(updater),
            reason=f"无法复制 Updater.exe 到临时目录: {e}",
        )

    args = plan.command_args(temp_copy, os.getpid())

    # Windows 下，必须给 Updater 一个**可见的新控制台**（不能用 DETACHED_PROCESS）：
    #
    # * ``DETACHED_PROCESS (0x08)``  会让进程完全无控制台，print/log 全部消失 —— 用户
    #   什么都看不到，万一报错也无从排查。
    # * ``CREATE_NEW_CONSOLE (0x10)`` 为新进程开一个独立 cmd 窗口（与主程序解耦），
    #   用户能实时看到下载进度与错误信息。这才符合"控制台 UI Updater"的设计意图。
    # * ``CREATE_NEW_PROCESS_GROUP (0x200)`` 让新进程独立于父进程的进程组，
    #   主程序退出时不会连带把 Updater 杀掉。
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000010 | 0x00000200  # CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP

    try:
        proc = subprocess.Popen(  # noqa: S603 — 受信任的本地 EXE
            args,
            close_fds=True,
            cwd=str(plan.app_dir),
            creationflags=flags,
            # 不接管 Updater 的 stdio —— 让它的新控制台自己管，否则即便有窗口也看不到内容。
            stdin=None,
            stdout=None,
            stderr=None,
        )
    except OSError as e:
        return LaunchResult(
            launched=False,
            updater_path=str(updater),
            temp_copy_path=str(temp_copy),
            reason=f"启动 Updater 失败: {e}",
        )

    return LaunchResult(
        launched=True,
        updater_path=str(updater),
        temp_copy_path=str(temp_copy),
        pid=proc.pid,
    )


def is_updater_available() -> bool:
    """便利方法：用于 UI 决定"立即更新"按钮是否可用。"""
    return find_updater_exe() is not None
