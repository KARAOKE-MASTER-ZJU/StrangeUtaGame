"""StrangeUtaGame Updater 入口（独立可执行）。

调用约定（由主程序的 ``installer.py`` 构造命令行）：

.. code::

    Updater.exe
        --app-dir <主程序所在目录>
        --app-exe <主程序 EXE 文件名>
        --target-version <X.Y.Z>
        --target-tag <SUGvX.Y.Z>
        --asset-name <StrangeUtaGame-vX.Y.Z.zip>
        --internal-name <_internal>
        --pid <主程序 PID>
        --url <source_id|url>     (允许重复)
        [--proxy http://127.0.0.1:port]
        [--sha256 <十六进制摘要>]
        [--no-launch]

执行流程：

1. 等待主程序 PID 退出（最长 30 秒）
2. 按 ``--url`` 顺序尝试下载 zip 到 ``%TEMP%/StrangeUtaGameUpdater/download``
3. （可选）校验 SHA-256
4. 解压到 ``%TEMP%/StrangeUtaGameUpdater/extracted/<topdir>``
5. 备份 ``<app_dir>/_internal`` 至 ``<app_dir>/_internal.bak`` —— 失败回滚
6. 覆盖 ``StrangeUtaGame.exe`` 与 ``_internal/`` 至 ``<app_dir>``
7. 启动新版本主程序（除非 ``--no-launch``）
8. 清理临时目录后退出

任何步骤失败均执行尽量保守的回滚，并把日志写到
``%TEMP%/StrangeUtaGameUpdater/updater.log`` 以及标准输出。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import requests


def _force_utf8_stdio() -> None:
    """强制 stdout/stderr 使用 UTF-8 —— 避免 Windows 控制台默认 cp1252/cp936 时
    在打包后的 Updater.exe 中 ``print/log`` 抛 ``UnicodeEncodeError``。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_stdio()

LOG_FORMAT = "[%(asctime)s] %(levelname)s %(message)s"
DATE_FORMAT = "%H:%M:%S"

TMP_DIR_NAME = "StrangeUtaGameUpdater"
CHUNK_SIZE = 128 * 1024
DEFAULT_USER_AGENT = "StrangeUtaGame-Updater/standalone"

# 等待主程序退出的总时长（秒）
WAIT_PID_TIMEOUT = 30.0
# tasklist 探测到 PID 消失后，再宽限多久让 Windows 完全释放 DLL/_internal 文件句柄。
# 即便主进程已"退出"，Win 内核清理 DLL 句柄、Defender 实时扫描等都可能让短时间内的
# 文件操作返回 Access Denied。
POST_EXIT_GRACE_SECONDS = 2.0
# 备份 / 覆盖 _internal 时遇到 PermissionError 的最大重试次数与间隔。
FILE_LOCK_RETRY_COUNT = 6
FILE_LOCK_RETRY_INTERVAL = 1.5


# ───────────────────────── 数据结构 ─────────────────────────


@dataclass
class Args:
    app_dir: Path
    app_exe: str
    target_version: str
    target_tag: str
    asset_name: str
    internal_name: str
    pid: int
    urls: List[Tuple[str, str]]
    proxy_url: str
    sha256: str
    launch_after: bool


# ───────────────────────── 命令行解析 ─────────────────────────


def parse_args(argv: Optional[List[str]] = None) -> Args:
    p = argparse.ArgumentParser(
        prog="StrangeUtaGame Updater",
        description="替换 StrangeUtaGame.exe 与 _internal/ 下的文件，并重启应用。",
    )
    p.add_argument("--app-dir", required=True, type=Path)
    p.add_argument("--app-exe", required=True, type=str)
    p.add_argument("--target-version", required=True, type=str)
    p.add_argument("--target-tag", required=True, type=str)
    p.add_argument("--asset-name", required=True, type=str)
    p.add_argument("--internal-name", default="_internal", type=str)
    p.add_argument("--pid", required=True, type=int)
    p.add_argument(
        "--url",
        dest="urls",
        action="append",
        default=[],
        help='下载候选 URL，格式 "source_id|https://..."，可重复',
    )
    p.add_argument("--proxy", dest="proxy_url", default="", type=str)
    p.add_argument("--sha256", dest="sha256", default="", type=str)
    p.add_argument(
        "--no-launch",
        dest="launch_after",
        action="store_false",
        default=True,
    )
    ns = p.parse_args(argv)

    urls: List[Tuple[str, str]] = []
    for raw in ns.urls or []:
        s = str(raw)
        if "|" not in s:
            urls.append(("unknown", s))
            continue
        sid, url = s.split("|", 1)
        urls.append((sid.strip() or "unknown", url.strip()))

    return Args(
        app_dir=Path(ns.app_dir).resolve(),
        app_exe=str(ns.app_exe),
        target_version=str(ns.target_version),
        target_tag=str(ns.target_tag),
        asset_name=str(ns.asset_name),
        internal_name=str(ns.internal_name),
        pid=int(ns.pid),
        urls=urls,
        proxy_url=str(ns.proxy_url or "").strip(),
        sha256=str(ns.sha256 or "").strip().lower(),
        launch_after=bool(ns.launch_after),
    )


# ───────────────────────── 日志 ─────────────────────────


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("sug.updater")
    logger.setLevel(logging.INFO)
    # 控制台
    ch = logging.StreamHandler(stream=sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    logger.addHandler(ch)
    # 文件
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(log_path), mode="w", encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(fh)
    except OSError:
        pass
    return logger


# ───────────────────────── 流程步骤 ─────────────────────────


def wait_for_pid_exit(pid: int, log: logging.Logger, timeout: float = WAIT_PID_TIMEOUT) -> bool:
    """等待指定 PID 退出，并在其后宽限 :data:`POST_EXIT_GRACE_SECONDS` 秒。"""
    log.info("等待主程序退出 (PID=%d)...", pid)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_pid_alive(pid):
            log.info("主程序进程已结束")
            # 关键：tasklist 报告 PID 消失，并不等于 Windows 已经释放 DLL/_internal 的
            # 文件句柄。给 OS 一点宽限时间，否则后续 rename _internal 会拿到 ERROR_ACCESS_DENIED。
            log.info("等待文件句柄释放（%.1fs）...", POST_EXIT_GRACE_SECONDS)
            time.sleep(POST_EXIT_GRACE_SECONDS)
            return True
        time.sleep(0.4)
    log.warning("等待主程序退出超时 (%.0fs)，将强制继续", timeout)
    return False


def _retry_on_permission_error(
    op_desc: str,
    func,  # type: ignore[no-untyped-def]
    log: logging.Logger,
    max_retries: int = FILE_LOCK_RETRY_COUNT,
    interval: float = FILE_LOCK_RETRY_INTERVAL,
):  # type: ignore[no-untyped-def]
    """在遇到 PermissionError / WinError 5 时重试给定操作。

    Windows 的文件锁释放是异步的：主进程"退出"后，DLL 句柄可能仍被内核挂着
    一两秒；杀毒软件也会临时锁住新文件。多次重试通常能在几秒内成功。
    """
    last_exc: BaseException = OSError("no attempt made")
    for attempt in range(1, max_retries + 1):
        try:
            return func()
        except PermissionError as e:
            last_exc = e
        except OSError as e:
            # WinError 5 (拒绝访问) / 32 (文件被占用) 同样视为可重试
            if getattr(e, "winerror", None) in (5, 32):
                last_exc = e
            else:
                raise
        log.warning(
            "%s 第 %d/%d 次失败：%s；%.1fs 后重试…",
            op_desc, attempt, max_retries, last_exc, interval,
        )
        time.sleep(interval)
    raise last_exc


def _is_pid_alive(pid: int) -> bool:
    """检测 PID 是否仍存活（Windows 用 tasklist 简单实现）。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(  # noqa: S603
                ["tasklist", "/FI", f"PID eq {pid}"],
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
                timeout=5,
            )
            return str(pid).encode() in out
        except Exception:
            return False
    # POSIX 兜底
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def download_one(
    url: str,
    dest: Path,
    proxies: Optional[dict],
    log: logging.Logger,
) -> Tuple[bool, str]:
    """下载一个 URL；返回 ``(ok, error_message)``。"""
    try:
        with requests.get(
            url,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"},
            stream=True,
            proxies=proxies,
            timeout=(10, 60),
            allow_redirects=True,
        ) as resp:
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}"
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            last_pct = -1
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(CHUNK_SIZE):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if total > 0:
                        pct = int(done * 100 / total)
                        if pct >= last_pct + 5:
                            log.info("  下载中: %3d%%  (%.1f / %.1f MB)",
                                     pct, done / 1024 / 1024, total / 1024 / 1024)
                            last_pct = pct
        return True, ""
    except requests.RequestException as e:
        return False, f"网络异常: {e}"
    except OSError as e:
        return False, f"写文件失败: {e}"


def try_download_from_sources(
    args: Args,
    download_path: Path,
    log: logging.Logger,
) -> tuple[bool, str]:
    """逐个尝试 ``args.urls`` 下载 zip；返回 ``(成功?, 命中的 URL)``。"""
    proxies = {"http": args.proxy_url, "https": args.proxy_url} if args.proxy_url else None
    if proxies:
        log.info("使用代理: %s", args.proxy_url)
    for source_id, url in args.urls:
        log.info("[%s] 尝试下载: %s", source_id, url)
        ok, err = download_one(url, download_path, proxies, log)
        if ok:
            log.info("[%s] 下载成功 (%.1f MB)",
                     source_id, download_path.stat().st_size / 1024 / 1024)
            return True, url
        log.warning("[%s] 失败: %s", source_id, err)
    return False, ""


def try_fetch_sha256(success_url: str, proxies: Optional[dict], log: logging.Logger) -> str:
    """主动尝试拉取与 zip 同源的 ``<url>.sha256`` 文件并解析摘要。

    发布流程会在 zip 同目录上传 ``StrangeUtaGame-vX.Y.Z.zip.sha256`` 资产（格式
    ``<64位hex>  文件名\\n``，coreutils ``sha256sum`` 兼容）。本函数：

    * 用 ``<成功的 zip URL> + ".sha256"`` 拼接 sha256 URL —— 因为 GitHub Release
      所有资产都在同一目录下，镜像源（ghproxy / fastgit）也透传相同路径；
    * 取首个连续 64 位十六进制子串作为摘要，对换行 / 行尾空格 / 大小写宽容；
    * 任何失败都返回 ``""``，由上游降级为"跳过校验"。
    """
    if not success_url:
        return ""
    sha_url = success_url + ".sha256"
    log.info("尝试拉取 SHA-256 校验: %s", sha_url)
    try:
        resp = requests.get(
            sha_url,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"},
            proxies=proxies,
            timeout=(5, 15),
            allow_redirects=True,
        )
    except requests.RequestException as e:
        log.warning("SHA-256 拉取失败（将跳过校验）: %s", e)
        return ""
    if resp.status_code != 200:
        log.warning("SHA-256 文件 HTTP %s（将跳过校验）", resp.status_code)
        return ""
    import re as _re
    m = _re.search(r"\b([0-9a-fA-F]{64})\b", resp.text)
    if not m:
        log.warning("SHA-256 文件内容无法解析（将跳过校验）")
        return ""
    digest = m.group(1).lower()
    log.info("拿到 SHA-256: %s", digest)
    return digest


def verify_sha256(file_path: Path, expected_hex: str, log: logging.Logger) -> bool:
    if not expected_hex:
        log.info("未提供 SHA-256，跳过校验")
        return True
    log.info("校验 SHA-256 中...")
    h = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                h.update(chunk)
    except OSError as e:
        log.error("读取下载文件失败: %s", e)
        return False
    actual = h.hexdigest().lower()
    if actual != expected_hex.lower():
        log.error("SHA-256 不匹配（期望 %s，实际 %s）", expected_hex, actual)
        return False
    log.info("SHA-256 校验通过")
    return True


def extract_archive(
    archive: Path,
    extract_dir: Path,
    log: logging.Logger,
) -> Optional[Path]:
    """解压 zip；返回解压根目录（如果 zip 内有单一顶层目录则返回它，否则就是 ``extract_dir``）。"""
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    log.info("解压: %s → %s", archive.name, extract_dir)

    if archive.suffix.lower() != ".zip":
        log.error("当前 Updater 仅支持 .zip 格式（收到 %s）。"
                  "若仓库发布的是 .rar，请改为发布 .zip。", archive.suffix)
        return None

    try:
        with zipfile.ZipFile(str(archive)) as zf:
            zf.extractall(str(extract_dir))
    except (zipfile.BadZipFile, OSError) as e:
        log.error("解压失败: %s", e)
        return None

    # 单一顶层目录探测
    entries = [p for p in extract_dir.iterdir() if not p.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        log.info("检测到单一顶层目录: %s", entries[0].name)
        return entries[0]
    return extract_dir


def apply_update(
    app_dir: Path,
    app_exe: str,
    internal_name: str,
    new_root: Path,
    log: logging.Logger,
) -> Tuple[bool, str]:
    """把 ``new_root`` 中的内容应用到 ``app_dir``。"""
    new_exe = new_root / app_exe
    new_internal = new_root / internal_name

    # 容错：有些发布把所有文件平铺在 new_root，没有 _internal 子目录 —— 这种情况说明源包不完整
    if not new_exe.exists():
        return False, f"更新包中找不到 {app_exe}"
    if not new_internal.exists() or not new_internal.is_dir():
        return False, f"更新包中找不到 {internal_name}/"

    # 备份 _internal —— 用重试包裹，应对 Windows 异步释放 DLL 句柄的常见延迟
    backup_internal = app_dir / f"{internal_name}.bak"
    cur_internal = app_dir / internal_name
    if backup_internal.exists():
        log.info("清理旧备份: %s", backup_internal)
        shutil.rmtree(backup_internal, ignore_errors=True)
    if cur_internal.exists():
        log.info("备份 %s → %s", cur_internal.name, backup_internal.name)
        try:
            _retry_on_permission_error(
                f"备份 {internal_name}",
                lambda: os.rename(str(cur_internal), str(backup_internal)),
                log,
            )
        except OSError as e:
            return False, (
                f"备份 {internal_name} 失败: {e}（主程序可能仍未完全释放文件句柄）"
            )

    # 备份 EXE
    cur_exe = app_dir / app_exe
    backup_exe = app_dir / f"{app_exe}.bak"
    if backup_exe.exists():
        try:
            backup_exe.unlink()
        except OSError:
            pass
    exe_was_present = cur_exe.exists()
    if exe_was_present:
        log.info("备份 %s → %s", cur_exe.name, backup_exe.name)
        try:
            _retry_on_permission_error(
                "备份 EXE",
                lambda: os.rename(str(cur_exe), str(backup_exe)),
                log,
            )
        except OSError as e:
            # 回滚 _internal
            try:
                if backup_internal.exists() and not cur_internal.exists():
                    os.rename(str(backup_internal), str(cur_internal))
            except OSError:
                pass
            return False, f"备份 EXE 失败: {e}（主程序可能未完全退出）"

    # 写入新内容 —— 同样带重试
    log.info("写入新 %s/", internal_name)
    try:
        _retry_on_permission_error(
            f"写入 {internal_name}",
            lambda: shutil.copytree(str(new_internal), str(cur_internal)),
            log,
        )
        log.info("写入新 %s", app_exe)
        _retry_on_permission_error(
            f"写入 {app_exe}",
            lambda: shutil.copy2(str(new_exe), str(cur_exe)),
            log,
        )
    except (OSError, shutil.Error) as e:
        log.error("写入新文件失败，尝试回滚: %s", e)
        # 回滚
        try:
            if cur_internal.exists():
                shutil.rmtree(str(cur_internal), ignore_errors=True)
            if backup_internal.exists():
                os.rename(str(backup_internal), str(cur_internal))
        except OSError:
            pass
        try:
            if cur_exe.exists():
                cur_exe.unlink()
            if backup_exe.exists():
                os.rename(str(backup_exe), str(cur_exe))
        except OSError:
            pass
        return False, f"写入失败: {e}"

    # 删除备份（用户数据保留）
    try:
        if backup_internal.exists():
            shutil.rmtree(str(backup_internal), ignore_errors=True)
        if backup_exe.exists():
            backup_exe.unlink()
    except OSError as e:
        log.warning("清理备份时出错（不影响功能）: %s", e)

    return True, ""


def launch_main_app(app_dir: Path, app_exe: str, log: logging.Logger) -> bool:
    exe_path = app_dir / app_exe
    if not exe_path.exists():
        log.error("找不到主程序 EXE: %s", exe_path)
        return False
    log.info("启动新版本: %s", exe_path)
    try:
        # 启动主程序：主程序是 GUI 应用（PyInstaller --windowed），不需要新建控制台。
        # 同时与 Updater 解耦，避免我们关闭 Updater 控制台时把它一并杀掉。
        flags = 0
        if sys.platform == "win32":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            flags = 0x00000008 | 0x00000200
        subprocess.Popen(  # noqa: S603
            [str(exe_path)],
            cwd=str(app_dir),
            close_fds=True,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError as e:
        log.error("启动主程序失败: %s", e)
        return False


# ───────────────────────── 主流程 ─────────────────────────


def run(args: Args) -> int:
    work_dir = Path(tempfile.gettempdir()) / TMP_DIR_NAME
    work_dir.mkdir(parents=True, exist_ok=True)

    log = setup_logger(work_dir / "updater.log")
    log.info("=" * 60)
    log.info("StrangeUtaGame Updater 启动")
    log.info("目标版本: v%s  (tag: %s)", args.target_version, args.target_tag)
    log.info("主程序目录: %s", args.app_dir)
    log.info("主程序 EXE: %s", args.app_exe)
    log.info("内部目录名: %s", args.internal_name)
    log.info("下载候选: %d 个源", len(args.urls))
    log.info("=" * 60)

    # 1. 等待主程序退出
    wait_for_pid_exit(args.pid, log)

    # 2. 下载
    download_path = work_dir / "download" / args.asset_name
    if download_path.exists():
        try:
            download_path.unlink()
        except OSError:
            pass
    if not args.urls:
        log.error("未提供任何下载 URL")
        return _exit_with_pause(2)
    ok, success_url = try_download_from_sources(args, download_path, log)
    if not ok:
        log.error("所有源均下载失败")
        return _exit_with_pause(3)

    # 3. 校验 —— 如果命令行未传 --sha256，尝试自动从同源 .sha256 资产拉取
    if not args.sha256:
        proxies = (
            {"http": args.proxy_url, "https": args.proxy_url} if args.proxy_url else None
        )
        args.sha256 = try_fetch_sha256(success_url, proxies, log)
    if not verify_sha256(download_path, args.sha256, log):
        log.error("校验失败")
        return _exit_with_pause(4)

    # 4. 解压
    extract_dir = work_dir / "extracted"
    new_root = extract_archive(download_path, extract_dir, log)
    if new_root is None:
        return _exit_with_pause(5)

    # 5. 应用更新（带回滚）
    ok, err = apply_update(
        args.app_dir, args.app_exe, args.internal_name, new_root, log
    )
    if not ok:
        log.error("应用更新失败: %s", err)
        return _exit_with_pause(6)
    log.info("文件替换完成")

    # 6. 启动新版本
    if args.launch_after:
        launch_main_app(args.app_dir, args.app_exe, log)

    # 7. 清理临时文件（保留 log 一段时间，便于排错）
    try:
        shutil.rmtree(str(extract_dir), ignore_errors=True)
        if download_path.exists():
            download_path.unlink()
    except OSError:
        pass

    log.info("更新完成 ✓")
    # 控制台稍作停留，便于用户看清结果
    if sys.platform == "win32":
        try:
            print()
            print("更新完成。窗口将在 3 秒后关闭。")
            time.sleep(3)
        except Exception:
            pass
    return 0


def _exit_with_pause(code: int) -> int:
    """失败退出前在控制台停留，等待用户确认。"""
    if sys.platform == "win32":
        try:
            print()
            print(f"更新失败 (退出码 {code})。日志位于 "
                  f"%TEMP%/{TMP_DIR_NAME}/updater.log")
            print("按回车键退出 ...")
            try:
                input()
            except EOFError:
                time.sleep(5)
        except Exception:
            pass
    return code


def main(argv: Optional[List[str]] = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    return run(args)


def _fatal_pause(exc: BaseException) -> int:
    """顶层未处理异常的兜底：把堆栈打到控制台并 ``pause``，让用户能看到。"""
    import traceback
    try:
        print()
        print("=" * 60)
        print("FATAL: Updater 顶层未处理异常")
        print("=" * 60)
        traceback.print_exception(exc)
        print()
        print(f"日志（如有）位于：%TEMP%/{TMP_DIR_NAME}/updater.log")
        print("按回车键退出 ...")
        try:
            input()
        except EOFError:
            time.sleep(10)
    except Exception:
        # 连 print 都失败说明 stdout 都没了 —— 静默退出
        pass
    return 99


if __name__ == "__main__":
    # 顶层全局 catch：即便 ``main()`` 漏抛了什么也至少让用户看见错误
    # （PyInstaller bootloader 阶段的 import 错误 catch 不住，那由控制台
    # 一闪而过显示；运行时所有错误本兜底都能接住）。
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as _exc:  # noqa: BLE001 — 这里是最末端兜底
        sys.exit(_fatal_pause(_exc))
