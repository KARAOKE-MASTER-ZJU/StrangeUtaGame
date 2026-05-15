"""更新源 URL 模板。

提供三个下载源：

* ``github``    —— 官方 GitHub Release 直链
* ``ghproxy``   —— ``https://mirror.ghproxy.com/`` 反代
* ``fastgit``   —— ``https://download.fastgit.org/``

URL 构造统一通过 :func:`build_release_urls`，避免散落字符串拼接。
"""

from __future__ import annotations

from typing import Dict, List, Literal, Tuple

from ..__version__ import REPO_NAME, REPO_OWNER

SourceId = Literal["github", "ghproxy", "fastgit"]
SOURCE_IDS: Tuple[SourceId, ...] = ("github", "ghproxy", "fastgit")

# 人类可读的标签，供 UI 显示。
SOURCE_LABELS: Dict[SourceId, str] = {
    "github": "GitHub Release（官方）",
    "ghproxy": "GitHub Proxy（mirror.ghproxy.com）",
    "fastgit": "FastGit（download.fastgit.org）",
}

# 默认顺序（用户可在 UI 中拖动调整）。
DEFAULT_ORDER: List[SourceId] = list(SOURCE_IDS)


def normalize_order(order: List[str]) -> List[SourceId]:
    """规范化用户配置的源顺序：

    * 仅保留合法 id；
    * 去重；
    * 缺失的源按 ``DEFAULT_ORDER`` 顺序补到末尾。
    """
    seen: List[SourceId] = []
    for sid in order:
        if sid in SOURCE_IDS and sid not in seen:
            seen.append(sid)  # type: ignore[arg-type]
    for sid in DEFAULT_ORDER:
        if sid not in seen:
            seen.append(sid)
    return seen


def _release_download_path(tag: str, asset_name: str) -> str:
    """构造 ``/<owner>/<repo>/releases/download/<tag>/<file>`` 公共片段。"""
    return f"{REPO_OWNER}/{REPO_NAME}/releases/download/{tag}/{asset_name}"


def build_download_url(source: SourceId, tag: str, asset_name: str) -> str:
    """根据源 id 构造一个具体的下载 URL。"""
    path = _release_download_path(tag, asset_name)
    if source == "github":
        return f"https://github.com/{path}"
    if source == "ghproxy":
        return f"https://mirror.ghproxy.com/https://github.com/{path}"
    if source == "fastgit":
        # FastGit 直接挂在 download.fastgit.org，无需再嵌套 github.com 前缀
        return f"https://download.fastgit.org/{path}"
    raise ValueError(f"未知的更新源 id: {source!r}")


def build_release_urls(order: List[str], tag: str, asset_name: str) -> List[Tuple[SourceId, str]]:
    """按用户排序构造下载 URL 列表，元素为 ``(source_id, url)``。"""
    return [
        (sid, build_download_url(sid, tag, asset_name))
        for sid in normalize_order(order)
    ]


def build_api_urls(order: List[str]) -> List[Tuple[SourceId, str]]:
    """构造"获取 latest release"的 API URL 列表（用于检测版本）。

    GitHub 官方 API: ``https://api.github.com/repos/<owner>/<repo>/releases/latest``
    GHProxy 可包装 ``https://mirror.ghproxy.com/<github_url>``
    FastGit 提供镜像 API：``https://api.fastgit.org/repos/<owner>/<repo>/releases/latest``
    """
    api_path = f"repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
    out: List[Tuple[SourceId, str]] = []
    for sid in normalize_order(order):
        if sid == "github":
            out.append((sid, f"https://api.github.com/{api_path}"))
        elif sid == "ghproxy":
            out.append(
                (sid, f"https://mirror.ghproxy.com/https://api.github.com/{api_path}")
            )
        elif sid == "fastgit":
            out.append((sid, f"https://api.fastgit.org/{api_path}"))
    return out
