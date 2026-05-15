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
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "src" / "strange_uta_game" / "__version__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
UPDATER_BUILD = ROOT / "updater_app" / "build_updater.py"
UPDATER_EXE = ROOT / "updater_app" / "dist" / "Updater.exe"
MAIN_BUILD = ROOT / "build.py"
MAIN_DIST = ROOT / "dist" / "StrangeUtaGame"
RELEASE_DIST = ROOT / "dist"

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

### Added
- *（待补充）*

### Changed
- *（待补充）*

### Fixed
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


def _ensure_updater_exe() -> None:
    """确保 Updater.exe 已构建；不存在则尝试构建。"""
    if UPDATER_EXE.exists():
        size_mb = UPDATER_EXE.stat().st_size / 1024 / 1024
        print(f"  ✓ 已存在 Updater.exe ({size_mb:.1f} MB)")
        return
    print("  ! 未发现 updater_app/dist/Updater.exe，开始构建 …")
    rc = _run_python(UPDATER_BUILD)
    if rc != 0:
        raise SystemExit(f"构建 Updater 失败，退出码 {rc}")
    if not UPDATER_EXE.exists():
        raise SystemExit("构建似乎完成，但未在 updater_app/dist 下找到 Updater.exe")


def _run_main_build() -> None:
    print("  构建主程序 (PyInstaller --onedir) …")
    rc = _run_python(MAIN_BUILD)
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


def cmd_build() -> int:
    version = _read_version()
    print(f"== build for v{version} ==")
    _ensure_updater_exe()
    _run_main_build()
    zip_path = _pack_zip(version)
    notes_path = _dump_release_notes(version)

    print()
    print("✓ 打包完成。请手动执行后续步骤：")
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
    print(f"  - 资产：上传 {zip_path.relative_to(ROOT)}")
    return 0


def cmd_all(version: str) -> int:
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
    return cmd_build()


# ───────────────────────── entry ─────────────────────────


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="release.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_prepare = sub.add_parser("prepare", help="改 __version__ 并注入 CHANGELOG 占位段落")
    sp_prepare.add_argument("version", help="目标版本号 X.Y.Z")

    sp_extract = sub.add_parser("extract-notes", help="抽取 CHANGELOG 段落")
    sp_extract.add_argument("version", help="版本号 X.Y.Z")
    sp_extract.add_argument("-o", "--output", type=Path, default=None)

    sub.add_parser("build", help="跑完整构建（Updater + 主程序 + zip）")

    sp_all = sub.add_parser("all", help="prepare + build")
    sp_all.add_argument("version", help="版本号 X.Y.Z")

    args = p.parse_args(argv)
    if args.cmd == "prepare":
        return cmd_prepare(args.version)
    if args.cmd == "extract-notes":
        return cmd_extract_notes(args.version, args.output)
    if args.cmd == "build":
        return cmd_build()
    if args.cmd == "all":
        return cmd_all(args.version)
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
