"""StrangeUtaGame 版本号。

发布流程：
- 推送一个 git tag，格式 ``SUGv{version}``（如 ``SUGv0.3.2``）。
- 发布资产命名为 ``StrangeUtaGame-v{version}.zip``。

更新器（``strange_uta_game.updater``）与设置-关于卡片均从此处读取版本号。
请始终使用 :class:`Version`（``packaging.version`` 兼容的语义版本字符串）。
"""

from __future__ import annotations

__version__ = "0.3.12"

# Git tag 前缀。GitHub Release 的 tag 命名规则为 ``{TAG_PREFIX}{__version__}``。
TAG_PREFIX = "SUGv"

# Release 资产名称模板（含 ``{version}`` 占位）。注意保留前缀 ``v``。
ASSET_NAME_TEMPLATE = "StrangeUtaGame-v{version}.zip"

# 仓库标识：用于构造 GitHub / 镜像下载 URL。
REPO_OWNER = "Xuan-cc"
REPO_NAME = "StrangeUtaGame"


def current_tag() -> str:
    """返回当前版本对应的 Git tag 字符串。"""
    return f"{TAG_PREFIX}{__version__}"


def current_asset_name() -> str:
    """返回当前版本对应的发布资产文件名。"""
    return ASSET_NAME_TEMPLATE.format(version=__version__)


__all__ = [
    "__version__",
    "TAG_PREFIX",
    "ASSET_NAME_TEMPLATE",
    "REPO_OWNER",
    "REPO_NAME",
    "current_tag",
    "current_asset_name",
]
