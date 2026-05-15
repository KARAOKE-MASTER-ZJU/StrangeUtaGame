"""打包 ``Updater.exe`` —— 独立的小型可执行文件。

输出位置：``<repo>/updater_app/dist/Updater/Updater.exe`` 与
``<repo>/updater_app/dist/Updater.exe`` 等 PyInstaller 默认产物。
在 ``build.py`` 中会进一步把 ``Updater.exe`` 复制到主程序 ``dist/StrangeUtaGame/``。

使用：

.. code:: bat

    python updater_app\\build_updater.py

设计权衡：

* ``--onefile`` —— Updater 是一次性流程，体积比启动速度更重要；onefile 让最终
  发布产物里只多出一个 ``Updater.exe``，不需要额外子目录。
* ``--console`` —— 用户能在 cmd 窗口看到进度（与 March7thAssistant 一致）。
* 不引入 PyQt6 等重依赖；只走标准库 + ``requests``，约 12~16 MB。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_ROOT.parent


def main() -> int:
    try:
        import PyInstaller.__main__  # noqa: F401
    except ImportError:
        print("缺少 pyinstaller。请先 `pip install pyinstaller`。", file=sys.stderr)
        return 1

    args = [
        str(PROJECT_ROOT / "main.py"),
        "--name=Updater",
        "--onefile",
        "--console",          # Updater 走控制台 UI，让用户能看到进度
        "--clean",
        "--noconfirm",
        "--distpath", str(PROJECT_ROOT / "dist"),
        "--workpath", str(PROJECT_ROOT / "build"),
        "--specpath", str(PROJECT_ROOT),
        # 仅依赖标准库 + requests
        "--hidden-import=requests",
        "--hidden-import=urllib3",
        "--hidden-import=charset_normalizer",
        "--hidden-import=idna",
        "--hidden-import=certifi",
        # 默认排除主程序的重型依赖，缩小体积
        "--exclude-module=PyQt6",
        "--exclude-module=qfluentwidgets",
        "--exclude-module=numpy",
        "--exclude-module=sounddevice",
        "--exclude-module=soundfile",
        "--exclude-module=pedalboard",
        "--exclude-module=av",
        "--exclude-module=pykakasi",
        "--exclude-module=sudachipy",
        "--exclude-module=sudachidict_core",
        "--exclude-module=jaconv",
        "--exclude-module=matplotlib",
        "--exclude-module=scipy",
        "--exclude-module=tkinter",
    ]

    # 图标（沿用主程序图标，找不到就跳过）
    icon = REPO_ROOT / "src" / "strange_uta_game" / "resource" / "icon.ico"
    if icon.exists():
        args.append(f"--icon={icon}")

    import PyInstaller.__main__ as pi_main
    print("开始打包 Updater.exe ...")
    pi_main.run(args)
    print()
    exe = PROJECT_ROOT / "dist" / "Updater.exe"
    if exe.exists():
        print(f"✓ 打包完成: {exe}")
        print(f"  体积: {exe.stat().st_size / 1024 / 1024:.1f} MB")
    else:
        print("! 未在 dist/Updater.exe 找到产物，请检查 PyInstaller 输出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
