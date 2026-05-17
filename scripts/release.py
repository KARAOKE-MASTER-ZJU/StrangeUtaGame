"""一键本地发布脚本。

子命令：

* ``prepare X.Y.Z``        修改 ``__version__.py`` 并往 ``CHANGELOG.md`` 顶部
                           插入新版本占位段落（如果还没有）。不动 Git。
* ``extract-notes X.Y.Z``  抽取 CHANGELOG.md 中对应版本的 Markdown 段落，
                           写到 stdout 或 ``--output`` 指定的文件 —— 用于
                           粘到 GitHub Release body。
* ``build``                跑 Updater 与主程序打包，生成
                           ``dist/StrangeUtaGame-vX.Y.Z.zip`` 与
                           ``dist/release_notes-vX.Y.Z.md``。需要先 prepare。
* ``all X.Y.Z``            prepare → build → 提示后续 git/GitHub 步骤。

用法示例：

.. code-block:: bat

    python scripts\\release.py prepare 0.3.3
    # 此处手动编辑 CHANGELOG.md 把占位段落填上具体内容
    python scripts\\release.py build
    # 检查 dist/StrangeUtaGame-v0.3.3.zip
    git add -A & git commit -m "release v0.3.3" & git tag SUGv0.3.3 & git push --tags
    # 最后到 GitHub Web 创建 Release：选 SUGv0.3.3，上传 zip，粘 release_notes
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time as _time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _force_utf8_stdio() -> None:
    """强制 stdout/stderr 使用 UTF-8。Windows 默认 cp1252/cp936 都会令中文 print 抛错。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "src" / "strange_uta_game" / "__version__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
UPDATER_BUILD = ROOT / "updater_app" / "build_updater.py"
UPDATER_EXE = ROOT / "updater_app" / "dist" / "Updater.exe"
MAIN_BUILD = ROOT / "build.py"
MAIN_DIST = ROOT / "dist" / "StrangeUtaGame"
RELEASE_DIST = ROOT / "dist"
# 记录上次成功打包的 runtime 内容哈希，随 git 提交，供 --reuse-runtime 使用。
RUNTIME_HASH_CACHE = ROOT / "scripts" / ".runtime-hash-cache.json"

VERSION_RE = re.compile(r'^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$')


def _check_version_format(value: str) -> str:
    if not VERSION_RE.match(value):
        raise SystemExit(f"版本号必须形如 X.Y.Z（收到 {value!r}）")
    return value


# ───────────────────────── prepare ─────────────────────────


def _read_version() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit(f"无法在 {VERSION_FILE} 中解析 __version__")
    return m.group(1)


def _write_version(new_version: str) -> None:
    text = VERSION_FILE.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'(__version__\s*=\s*")[^"]+(")',
        rf'\g<1>{new_version}\g<2>',
        text,
        count=1,
    )
    if n != 1:
        raise SystemExit(f"未能在 {VERSION_FILE} 中替换 __version__")
    VERSION_FILE.write_text(new_text, encoding="utf-8")


_CHANGELOG_PLACEHOLDER = """## [{version}] - {date}

### 新增功能
- *（待补充）*

### 特性改变
- *（待补充）*

### 修复项目
- *（待补充）*

"""


def _has_version_section(content: str, version: str) -> bool:
    pattern = re.compile(rf'^##\s*\[{re.escape(version)}\]', re.MULTILINE)
    return bool(pattern.search(content))


def _insert_placeholder(version: str) -> bool:
    """如果 CHANGELOG 中没有对应版本段落则插入模板；返回是否真的插入了。"""
    if not CHANGELOG.exists():
        raise SystemExit(f"找不到 {CHANGELOG} —— 请先创建 CHANGELOG.md")
    content = CHANGELOG.read_text(encoding="utf-8")
    if _has_version_section(content, version):
        return False

    today = dt.date.today().isoformat()
    placeholder = _CHANGELOG_PLACEHOLDER.format(version=version, date=today)

    # 插在 ``## [Unreleased]`` 段之后；若不存在则插在文件顶部第一段之前
    m = re.search(r'^##\s*\[Unreleased\][^\n]*\n', content, re.MULTILINE)
    if m:
        # 找到下一个 ## 段落开头
        next_section = re.search(r'^## ', content[m.end():], re.MULTILINE)
        if next_section:
            insert_at = m.end() + next_section.start()
        else:
            insert_at = len(content)
        new_content = content[:insert_at] + placeholder + content[insert_at:]
    else:
        # 直接插在文件最上（标题之后）
        title_m = re.search(r'^#\s+Changelog[^\n]*\n', content, re.MULTILINE)
        insert_at = title_m.end() if title_m else 0
        new_content = content[:insert_at] + "\n" + placeholder + content[insert_at:]

    CHANGELOG.write_text(new_content, encoding="utf-8")
    return True


def cmd_prepare(version: str) -> int:
    version = _check_version_format(version)
    old = _read_version()
    if old == version:
        print(f"  __version__ 已是 {version}，跳过版本号写入")
    else:
        _write_version(version)
        print(f"  __version__ {old} → {version}")
    inserted = _insert_placeholder(version)
    if inserted:
        print(f"  已在 CHANGELOG.md 中插入 [{version}] 占位段落，请手动填写具体条目")
    else:
        print(f"  CHANGELOG.md 已存在 [{version}] 段落，未修改")
    print()
    print("✓ prepare 完成。后续步骤：")
    print(f"  1) 编辑 {CHANGELOG.relative_to(ROOT)}，把 [{version}] 段落补充完整")
    print(f"  2) python scripts\\release.py build  # 或 all {version}")
    return 0


# ───────────────────────── extract-notes ─────────────────────────


def _extract_section(version: str) -> str:
    content = CHANGELOG.read_text(encoding="utf-8")
    pattern = re.compile(
        rf'(?ms)^##\s*\[{re.escape(version)}\][^\n]*\n(?P<body>.*?)(?=^##\s|\Z)',
    )
    m = pattern.search(content)
    if not m:
        raise SystemExit(f"CHANGELOG.md 中未找到 [{version}] 段落")
    body = m.group("body").rstrip() + "\n"
    return body


def cmd_extract_notes(version: str, output: Optional[Path]) -> int:
    version = _check_version_format(version)
    notes = _extract_section(version)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(notes, encoding="utf-8")
        print(f"✓ 已写入 {output}")
    else:
        sys.stdout.write(notes)
    return 0


# ───────────────────────── build ─────────────────────────


def _run_python(script: Path, args: Optional[list] = None) -> int:
    cmd = [sys.executable, str(script)] + list(args or [])
    print(f"  $ {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(ROOT))


def _updater_sources_max_mtime() -> float:
    """返回 ``updater_app/`` 下所有 ``.py`` 文件的最大 mtime。

    用于和现有 ``Updater.exe`` 的 mtime 做比较 —— 源代码改过就需要重打，否则
    release 出去的 zip 里 Updater.exe 还是旧的，新加的功能（manifest/sha256 等）
    都不会生效。
    """
    src_dir = ROOT / "updater_app"
    mtimes: List[float] = []
    for p in src_dir.rglob("*.py"):
        # 跳过 build/ dist/ 等产物目录，只看源码
        rel = p.relative_to(src_dir)
        if rel.parts and rel.parts[0] in {"build", "dist"}:
            continue
        try:
            mtimes.append(p.stat().st_mtime)
        except OSError:
            pass
    return max(mtimes) if mtimes else 0.0


def _ensure_updater_exe(force: bool = False, clean: bool = False) -> None:
    """确保 ``Updater.exe`` 存在且不落后于源代码。

    判定规则：
    1. 若不存在 → 重打
    2. 若 ``--rebuild-updater`` 强制 → 重打
    3. 若 ``updater_app/`` 任一 .py 文件的 mtime 比 ``Updater.exe`` 新 → 重打
    4. 否则跳过

    踩过的坑：之前只检查存在性，导致改完 ``updater_app/main.py`` 后 release.py
    仍然用旧的 Updater.exe，发出去的版本不带新功能。
    """
    if force:
        print("  ! --rebuild-updater 强制重打 Updater.exe …")
        _do_rebuild_updater(clean=clean)
        return

    if not UPDATER_EXE.exists():
        print("  ! 未发现 updater_app/dist/Updater.exe，开始构建 …")
        _do_rebuild_updater(clean=clean)
        return

    exe_mtime = UPDATER_EXE.stat().st_mtime
    src_mtime = _updater_sources_max_mtime()
    if src_mtime > exe_mtime:
        import datetime as _dt
        exe_dt = _dt.datetime.fromtimestamp(exe_mtime).strftime("%Y-%m-%d %H:%M:%S")
        src_dt = _dt.datetime.fromtimestamp(src_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"  ! Updater.exe 已过期（exe mtime={exe_dt}, 源码 mtime={src_dt}），"
            f"重新打包 Updater.exe …"
        )
        _do_rebuild_updater(clean=clean)
        return

    size_mb = UPDATER_EXE.stat().st_size / 1024 / 1024
    print(f"  ✓ 已存在 Updater.exe，源码未更新（{size_mb:.1f} MB）")


def _do_rebuild_updater(clean: bool = False) -> None:
    extra = ["--clean"] if clean else []
    rc = _run_python(UPDATER_BUILD, extra)
    if rc != 0:
        raise SystemExit(f"构建 Updater 失败，退出码 {rc}")
    if not UPDATER_EXE.exists():
        raise SystemExit("构建似乎完成，但未在 updater_app/dist 下找到 Updater.exe")


def _verify_release_assets(version: str, dist_root: Path, full_zip: Path) -> None:
    """打完 build 后立刻自检每个产物是否到位，让"漏写文件"在 push 之前暴露。

    任一缺失直接 SystemExit；这样 cmd_build 失败用户能马上看到，不会带病发布。
    """
    parent = full_zip.parent
    required_release_assets = [
        full_zip,
        full_zip.with_name(full_zip.name + ".sha256"),
        parent / f"StrangeUtaGame-v{version}-app.zip",
        parent / f"StrangeUtaGame-v{version}-app.zip.sha256",
        parent / f"StrangeUtaGame-v{version}-runtime.zip",
        parent / f"StrangeUtaGame-v{version}-runtime.zip.sha256",
        parent / f"manifest-v{version}.json",
    ]
    missing: List[str] = []
    for p in required_release_assets:
        if not p.exists():
            missing.append(str(p.relative_to(ROOT)))

    # dist 内的"出厂本地清单"也必须存在
    installed_manifest = dist_root / "_internal" / ".installed_manifest.json"
    if not installed_manifest.exists():
        missing.append(str(installed_manifest.relative_to(ROOT)))

    # Updater.exe 必须随主程序一起到位
    updater_in_dist = dist_root / "Updater.exe"
    if not updater_in_dist.exists():
        missing.append(str(updater_in_dist.relative_to(ROOT)))

    # strange_uta_game.updater 子包必须被 PyInstaller 收集到位
    updater_pkg = dist_root / "_internal" / "strange_uta_game" / "updater"
    if not updater_pkg.is_dir():
        missing.append(str(updater_pkg.relative_to(ROOT)) + "/  (updater 子包缺失)")

    if missing:
        print("  ✗ 自检失败，以下文件缺失：")
        for m in missing:
            print(f"      • {m}")
        raise SystemExit(
            "构建产物不完整。常见原因：在 release.py build 之后又单独跑了一次 "
            "`python build.py`，PyInstaller --noconfirm 会清空 dist/StrangeUtaGame/ "
            "把 .installed_manifest.json 等文件一并删除。"
        )

    # 把每个产物的 sha256 摘要打一下，便于排错
    print("  ✓ 所有发布资产就绪：")
    for p in required_release_assets:
        rel = p.relative_to(ROOT)
        size_mb = p.stat().st_size / 1024 / 1024
        print(f"      • {rel}  ({size_mb:.2f} MB)")
    print(f"      • {installed_manifest.relative_to(ROOT)}  (出厂本地清单)")
    print(f"      • {updater_in_dist.relative_to(ROOT)}  (Updater.exe 已就位)")
    updater_pkg = dist_root / "_internal" / "strange_uta_game" / "updater"
    print(f"      • {updater_pkg.relative_to(ROOT)}/  (updater 子包，{len(list(updater_pkg.iterdir()))} 文件)")


def _run_main_build(clean: bool = False) -> None:
    print("  构建主程序 (PyInstaller --onedir) …")
    extra = ["--clean"] if clean else []
    rc = _run_python(MAIN_BUILD, extra)
    if rc != 0:
        raise SystemExit(f"主程序构建失败，退出码 {rc}")


def _pack_zip(version: str) -> Path:
    if not MAIN_DIST.exists():
        raise SystemExit(f"主程序产物目录不存在: {MAIN_DIST}")
    zip_name = f"StrangeUtaGame-v{version}.zip"
    zip_path = RELEASE_DIST / zip_name
    if zip_path.exists():
        zip_path.unlink()
    print(f"  打包 {MAIN_DIST.name}/ → {zip_path.name}")
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in MAIN_DIST.rglob("*"):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(MAIN_DIST.parent))
    print(f"  ✓ {zip_path}  ({zip_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return zip_path


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _content_hash_of_zip(zip_path: Path) -> str:
    """计算 zip 内所有文件的内容哈希（确定性，不受打包元数据影响）。

    算法：对 zip 内每个文件计算 sha256(file_content)，然后按 arcname 排序拼接
    ``arcname:content_hash`` 再做一次总 sha256。这样只要文件内容不变，哈希就不变，
    无论 zip 的时间戳、文件顺序如何。
    """
    entries: List[tuple[str, str]] = []
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            content = zf.read(info.filename)
            content_hash = hashlib.sha256(content).hexdigest()
            entries.append((info.filename, content_hash))
    entries.sort(key=lambda e: e[0])
    combined = "\n".join(f"{name}:{h}" for name, h in entries)
    return hashlib.sha256(combined.encode("ascii")).hexdigest().lower()


def _write_sha256(target: Path) -> Path:
    """为 ``target`` 生成同名 ``.sha256`` 文件，与 sha256sum / coreutils 兼容。

    始终使用文件本身的 raw-file SHA256（供 Updater 的 ``verify_sha256`` 校验全量 zip 用）。
    增量分包的内容哈希（content-hash）由 ``_content_hash_of_zip`` 单独计算并写入 manifest，
    两者算法不同，不要混用。
    """
    digest = _sha256_of(target)
    sha_path = target.with_name(target.name + ".sha256")
    sha_path.write_text(f"{digest}  {target.name}\n", encoding="ascii")
    print(f"  ✓ {sha_path.name}  (sha256={digest})")
    return sha_path


# ───────────────────────── runtime 哈希缓存 ─────────────────────────


def _load_runtime_cache() -> Optional[Dict]:
    if not RUNTIME_HASH_CACHE.exists():
        return None
    try:
        return json.loads(RUNTIME_HASH_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _requirements_hash() -> str:
    """对 ``pip freeze`` 输出取 SHA-256，作为"依赖是否变化"的快速判断。

    只要安装的包列表/版本不变，哈希就不变 —— 即使改了 import 语句、
    也没有增删第三方包，runtime 里的 DLL/pyd 文件就不会变。
    运行失败时返回空字符串（调用方视为"无法判断，保守重建"）。
    """
    try:
        out = subprocess.check_output(
            [sys.executable, "-m", "pip", "freeze"],
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError:
        return ""
    return hashlib.sha256(out.encode("utf-8")).hexdigest().lower()


def _save_runtime_cache(
    version: str,
    content_hash: str,
    size: int,
    requirements_hash: str = "",
) -> None:
    data = {
        "version": version,
        "content_hash": content_hash,
        "size": size,
        "requirements_hash": requirements_hash,
    }
    RUNTIME_HASH_CACHE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  ✓ 已更新 runtime 哈希缓存: {RUNTIME_HASH_CACHE.relative_to(ROOT)}")
    print("  ⚠ 记得把 scripts/.runtime-hash-cache.json 一并提交到 git！")


# ───────────────────────── 增量打包：app + runtime 分包 ─────────────────────────

# 局部约定：app 部分（用户自己的应用代码，~5MB），其余归 runtime（依赖，~178MB）。
# 注意：以下字面量需与 updater_app/main.py 中的同名常量保持同步：
#   UPDATER_EXE_NAME          = "Updater.exe"
#   LOCAL_MANIFEST_FILENAME   = ".installed_manifest.json"
# 如果改名，两处都要同步修改。
APP_TARGETS_BASE: List[str] = [
    "StrangeUtaGame.exe",
    "Updater.exe",               # 同步自 main.py:UPDATER_EXE_NAME
    "_internal/strange_uta_game",
]
# `_internal/` 顶层下 **不** 归入 runtime 的条目（要么是 app 的、要么是 Updater 运行时维护的）。
INTERNAL_NON_RUNTIME_NAMES = {
    "strange_uta_game",
    ".installed_manifest.json",  # 同步自 main.py:LOCAL_MANIFEST_FILENAME
}


def _compute_runtime_targets(dist_root: Path) -> List[str]:
    """扫描 ``dist_root/_internal/`` 把不属于 app 的所有顶层条目（子目录与文件）列为 runtime targets。"""
    internal_dir = dist_root / "_internal"
    if not internal_dir.is_dir():
        return []
    out: List[str] = []
    for child in sorted(internal_dir.iterdir(), key=lambda p: p.name.lower()):
        if child.name in INTERNAL_NON_RUNTIME_NAMES:
            continue
        out.append(f"_internal/{child.name}")
    return out


def _pack_part_zip(zip_path: Path, dist_root: Path, targets: List[str]) -> None:
    """把 ``dist_root`` 下 targets 列出的内容（文件或目录）打成一个 zip。

    zip 内的 arcname 严格相对 ``dist_root``，与全量包结构一致——这样 Updater 把
    part-zip 解压到 ``app_dir`` 时就是原位覆盖，无需任何路径转换。

    哈希比较走内容哈希（对 zip 内文件的路径+内容计算 sha256），不受打包时间戳影响。
    """
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for t in targets:
            src = dist_root / t
            if not src.exists():
                print(f"  ! 跳过不存在的 target: {t}")
                continue
            if src.is_file():
                zf.write(src, arcname=t)
            else:
                for f in src.rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(dist_root)
                        zf.write(f, arcname=str(rel).replace("\\", "/"))


def _pack_parts(
    version: str,
    rebuild_runtime: bool = False,
) -> Tuple[Path, Path, List[str], List[str]]:
    """打 app + runtime 两个 part zip 并生成各自 .sha256 文件。

    返回 ``(app_zip_path, runtime_zip_path, app_targets, runtime_targets)``。
    重要：``dist/StrangeUtaGame/_internal/.installed_manifest.json`` **不在任何
    part targets 中**，因此它即便存在也不会影响 part-zip 的 sha256，从而避免循环
    依赖（part sha256 → 写本地清单 → 再依赖含清单的内容）。

    **runtime 自动判断策略**（``rebuild_runtime=False`` 时）：

    1. 读取 ``scripts/.runtime-hash-cache.json`` 中上次打包时的 ``pip freeze`` 哈希；
    2. 对当前环境重新执行 ``pip freeze`` 并计算哈希；
    3. 两者相同 → 依赖包未变 → 复用上次的 runtime zip（content hash 与上次一致
       → Updater 不会让用户重新下载 runtime）；
    4. 不同 → 重新打包，更新缓存。

    ``rebuild_runtime=True`` 时跳过上述判断，强制重打（用于 PyInstaller 版本升级、
    手动确认依赖有变化等场景）。
    """
    dist_root = MAIN_DIST
    parent = dist_root.parent

    app_targets = list(APP_TARGETS_BASE)
    runtime_targets = _compute_runtime_targets(dist_root)

    app_zip = parent / f"StrangeUtaGame-v{version}-app.zip"
    runtime_zip = parent / f"StrangeUtaGame-v{version}-runtime.zip"

    # ── app part（始终重新打包）──
    print(f"  打包 app part → {app_zip.name}（{len(app_targets)} targets）")
    _pack_part_zip(app_zip, dist_root, app_targets)
    print(f"  ✓ {app_zip.name}  ({app_zip.stat().st_size / 1024 / 1024:.1f} MB)")
    _write_sha256(app_zip)

    # ── runtime part：自动判断是否可复用 ──
    reused = False
    if not rebuild_runtime:
        cache = _load_runtime_cache()
        current_req_hash = _requirements_hash()
        if not current_req_hash:
            print("  ! pip freeze 失败，无法自动判断依赖变化，重新打包 runtime")
        elif cache and cache.get("requirements_hash") == current_req_hash:
            prev_version = cache.get("version", "")
            prev_hash = cache.get("content_hash", "")
            prev_zip = parent / f"StrangeUtaGame-v{prev_version}-runtime.zip"
            if prev_hash and prev_zip.exists():
                print(
                    f"  依赖未变（pip freeze hash 相同），复用 v{prev_version} runtime"
                    f" → {runtime_zip.name}"
                )
                shutil.copy2(str(prev_zip), str(runtime_zip))
                print(
                    f"  ✓ {runtime_zip.name}"
                    f"  ({runtime_zip.stat().st_size / 1024 / 1024:.1f} MB)"
                    f"  [content hash 与 v{prev_version} 相同，用户不会重新下载]"
                )
                _write_sha256(runtime_zip)
                # 更新缓存版本号（指向当前版本 zip，供下次使用）
                _save_runtime_cache(
                    version, prev_hash, runtime_zip.stat().st_size, current_req_hash
                )
                reused = True
            else:
                reason = "content_hash 缺失" if not prev_hash else f"找不到 {prev_zip.name}"
                print(f"  ! 依赖未变，但缓存 zip 不可用（{reason}），重新打包 runtime")
        elif cache:
            print("  依赖已变化（pip freeze hash 不同），重新打包 runtime")
        else:
            print("  无缓存记录（首次构建），打包 runtime 并建立缓存")

    if rebuild_runtime:
        print("  --rebuild-runtime：强制重新打包 runtime")

    if not reused:
        print(f"  打包 runtime part → {runtime_zip.name}（{len(runtime_targets)} targets）")
        _pack_part_zip(runtime_zip, dist_root, runtime_targets)
        print(f"  ✓ {runtime_zip.name}  ({runtime_zip.stat().st_size / 1024 / 1024:.1f} MB)")
        _write_sha256(runtime_zip)
        content_hash = _content_hash_of_zip(runtime_zip)
        req_hash = _requirements_hash() if not rebuild_runtime else _requirements_hash()
        _save_runtime_cache(version, content_hash, runtime_zip.stat().st_size, req_hash)

    return app_zip, runtime_zip, app_targets, runtime_targets


def _write_installed_manifest_into_dist(
    version: str,
    app_zip: Path,
    runtime_zip: Path,
) -> Path:
    """把"出厂版本"的 .installed_manifest.json 直接写到 ``dist/StrangeUtaGame/_internal/``。

    这样无论用户**怎么拿到**这一版（GitHub Web 下载全量 zip 解压、Updater 走全量、
    Updater 走增量），开包后 ``_internal/`` 里都自带这份本地清单。下次升级时
    Updater 读到清单就能直接走增量路径，**首次升级不再必走全量**。

    生成的字段与 Updater 运行时 ``write_local_manifest`` 保持完全一致，避免一安装
    完就被 Updater 覆写时格式漂移。
    """
    dist_root = MAIN_DIST
    payload = {
        "version": version,
        "schema": 1,
        "parts": {
            "app": {"sha256": _content_hash_of_zip(app_zip), "asset": app_zip.name},
            "runtime": {"sha256": _content_hash_of_zip(runtime_zip), "asset": runtime_zip.name},
        },
        "installed_at": int(_time.time()),
    }
    p = dist_root / "_internal" / ".installed_manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  ✓ 已写入出厂本地清单: {p.relative_to(dist_root.parent)}")
    return p


def _write_release_manifest(
    version: str,
    full_zip: Path,
    app_zip: Path,
    runtime_zip: Path,
    app_targets: List[str],
    runtime_targets: List[str],
) -> Path:
    """生成对外发布的 ``manifest-vX.Y.Z.json``（描述全量 + 两个 part + 各自 targets）。"""
    parent = full_zip.parent
    manifest: Dict = {
        "version": version,
        "schema": 1,
        "parts": {
            "app": {
                "asset": app_zip.name,
                "sha256": _content_hash_of_zip(app_zip),
                "size": app_zip.stat().st_size,
                "targets": app_targets,
            },
            "runtime": {
                "asset": runtime_zip.name,
                "sha256": _content_hash_of_zip(runtime_zip),
                "size": runtime_zip.stat().st_size,
                "targets": runtime_targets,
            },
        },
        "full": {
            "asset": full_zip.name,
            "sha256": _content_hash_of_zip(full_zip),
            "size": full_zip.stat().st_size,
        },
    }
    manifest_path = parent / f"manifest-v{version}.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  ✓ {manifest_path.name}")
    return manifest_path


def _dump_release_notes(version: str) -> Optional[Path]:
    try:
        notes = _extract_section(version)
    except SystemExit as e:
        print(f"  ! 跳过抽取 release_notes：{e}")
        return None
    notes_path = RELEASE_DIST / f"release_notes-v{version}.md"
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(notes, encoding="utf-8")
    print(f"  ✓ {notes_path}")
    return notes_path


def cmd_build(
    rebuild_updater: bool = False,
    clean: bool = False,
    rebuild_runtime: bool = False,
) -> int:
    version = _read_version()
    print(f"== build for v{version} ==")
    _ensure_updater_exe(force=rebuild_updater, clean=clean)
    _run_main_build(clean=clean)

    # 关键顺序：
    #   1) 先打 app + runtime part zip（不含 .installed_manifest.json）→ 算 sha256
    #   2) 把 .installed_manifest.json 写到 dist/StrangeUtaGame/_internal/
    #      ← 用上一步算出的 part sha256 填充。这样全量 zip / 用户首次安装都自带清单
    #   3) 再打全量 zip（这次会一并打入 .installed_manifest.json）→ 算 sha256
    #   4) 写对外发布的 manifest-vX.Y.Z.json
    print()
    print("[step] 打增量分包 part zip ...")
    app_zip, runtime_zip, app_targets, runtime_targets = _pack_parts(
        version, rebuild_runtime=rebuild_runtime
    )

    print()
    print("[step] 写出厂本地清单到 _internal/.installed_manifest.json ...")
    _write_installed_manifest_into_dist(version, app_zip, runtime_zip)

    print()
    print("[step] 打全量 zip（含本地清单）...")
    zip_path = _pack_zip(version)
    sha_path = _write_sha256(zip_path)

    print()
    print("[step] 写对外发布 manifest-vX.Y.Z.json ...")
    manifest_path = _write_release_manifest(
        version, zip_path, app_zip, runtime_zip, app_targets, runtime_targets,
    )

    notes_path = _dump_release_notes(version)

    # ── 完整性自检 ── 一次性把"漏写文件"的所有踩坑都消灭在 release 之前
    print()
    print("[step] 完整性自检 ...")
    _verify_release_assets(version, dist_root=MAIN_DIST, full_zip=zip_path)

    print()
    print("✓ 打包完成。请手动执行后续步骤：")
    print()
    print("⚠ 重要：不要再单独跑 `python build.py`，那会清空 dist/StrangeUtaGame/，")
    print("   把刚刚写入的 _internal/.installed_manifest.json 一并删除。")
    print("   `scripts/release.py build` 内部已经会调 PyInstaller 完成主程序构建。")
    print()
    print(f"  git add -A")
    print(f'  git commit -m "release v{version}"')
    print(f"  git tag SUGv{version}")
    print(f"  git push origin main --tags")
    print()
    print("然后到 GitHub Web 创建 Release：")
    print(f"  - tag: SUGv{version}")
    print(f"  - 标题：v{version}")
    if notes_path:
        print(f"  - body：直接复制 {notes_path.relative_to(ROOT)} 全文")
    print(f"  - 资产（全部上传）：")
    print(f"      • {zip_path.relative_to(ROOT)}            ← 全量包（自带 _internal/.installed_manifest.json）")
    print(f"      • {sha_path.relative_to(ROOT)}     ← 全量包校验")
    print(f"      • {manifest_path.relative_to(ROOT)}   ← 增量更新清单")
    parent = zip_path.parent
    for name in (
        f"StrangeUtaGame-v{version}-app.zip",
        f"StrangeUtaGame-v{version}-app.zip.sha256",
        f"StrangeUtaGame-v{version}-runtime.zip",
        f"StrangeUtaGame-v{version}-runtime.zip.sha256",
    ):
        p = parent / name
        if p.exists():
            print(f"      • {p.relative_to(ROOT)}")
    return 0


def cmd_all(
    version: str,
    rebuild_updater: bool = False,
    clean: bool = False,
    rebuild_runtime: bool = False,
) -> int:
    rc = cmd_prepare(version)
    if rc != 0:
        return rc
    # 二次确认
    print()
    print("⚠ prepare 已完成。在继续 build 之前，建议先编辑 CHANGELOG.md 补充内容。")
    answer = input("是否继续 build？(y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        print("已取消 build。")
        return 0
    return cmd_build(
        rebuild_updater=rebuild_updater, clean=clean, rebuild_runtime=rebuild_runtime
    )


# ───────────────────────── entry ─────────────────────────


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="release.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_prepare = sub.add_parser("prepare", help="改 __version__ 并注入 CHANGELOG 占位段落")
    sp_prepare.add_argument("version", help="目标版本号 X.Y.Z")

    sp_extract = sub.add_parser("extract-notes", help="抽取 CHANGELOG 段落")
    sp_extract.add_argument("version", help="版本号 X.Y.Z")
    sp_extract.add_argument("-o", "--output", type=Path, default=None)

    sp_build = sub.add_parser("build", help="跑完整构建（Updater + 主程序 + zip）")
    sp_build.add_argument(
        "--rebuild-updater",
        action="store_true",
        help="强制重新打包 Updater.exe，即便源码 mtime 未变（用于排查/确认）",
    )
    sp_build.add_argument(
        "--clean",
        action="store_true",
        help="传给 PyInstaller --clean，完整重建（改了 import 或打包配置时使用）",
    )
    sp_build.add_argument(
        "--rebuild-runtime",
        action="store_true",
        help=(
            "强制重新打包 runtime zip，忽略 pip freeze 自动判断。"
            "用于 PyInstaller 版本升级、或确认新增了第三方依赖但 pip freeze "
            "哈希未变（极少见）等场景。正常情况下不需要加此标志。"
        ),
    )

    sp_all = sub.add_parser("all", help="prepare + build")
    sp_all.add_argument("version", help="目标版本号 X.Y.Z")
    sp_all.add_argument(
        "--rebuild-updater",
        action="store_true",
        help="强制重新打包 Updater.exe",
    )
    sp_all.add_argument(
        "--clean",
        action="store_true",
        help="传给 PyInstaller --clean，完整重建（改了 import 或打包配置时使用）",
    )
    sp_all.add_argument(
        "--rebuild-runtime",
        action="store_true",
        help="强制重新打包 runtime zip（正常情况下不需要）",
    )

    args = p.parse_args(argv)
    if args.cmd == "prepare":
        return cmd_prepare(args.version)
    if args.cmd == "extract-notes":
        return cmd_extract_notes(args.version, args.output)
    if args.cmd == "build":
        return cmd_build(
            rebuild_updater=args.rebuild_updater,
            clean=args.clean,
            rebuild_runtime=args.rebuild_runtime,
        )
    if args.cmd == "all":
        return cmd_all(
            args.version,
            rebuild_updater=args.rebuild_updater,
            clean=args.clean,
            rebuild_runtime=args.rebuild_runtime,
        )
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
