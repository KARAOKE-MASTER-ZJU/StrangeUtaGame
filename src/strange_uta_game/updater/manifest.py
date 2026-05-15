"""GitHub Release "latest" 抽象。

提供 :func:`fetch_latest_release`：依次尝试三个源的 API（受 ``UpdaterSettings``
排序与代理影响），把 GitHub Release JSON 收敛为 :class:`LatestRelease` 数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import http_client
from .sources import SourceId, build_api_urls, build_download_url
from .version import strip_tag_prefix


@dataclass(frozen=True)
class ReleaseAsset:
    """Release 下的单个文件资产。"""
    name: str
    size: int
    download_url: str

    @property
    def extension(self) -> str:
        name = self.name.lower()
        for ext in (".zip", ".rar", ".7z", ".tar.gz", ".tgz", ".sha256"):
            if name.endswith(ext):
                return ext
        # 兜底
        idx = name.rfind(".")
        return name[idx:] if idx >= 0 else ""


@dataclass(frozen=True)
class LatestRelease:
    """聚合的 latest release 信息。"""
    tag: str
    version: str          # tag 去掉 SUGv 前缀后的纯版本号
    name: str             # release 标题（可能为空，回落到 tag）
    body: str             # changelog 正文（markdown）
    html_url: str         # Release 页面 URL
    prerelease: bool
    published_at: str
    assets: List[ReleaseAsset] = field(default_factory=list)

    # ── 资产挑选 ──────────────────────────────────────────────

    def pick_primary_asset(self, preferred_name: Optional[str] = None) -> Optional[ReleaseAsset]:
        """挑选用于安装的主资产。

        优先级：
        1. 名字精确等于 ``preferred_name``（通常是 ``StrangeUtaGame-v{ver}.zip``）；
        2. 任何 ``.zip``；
        3. 任何 ``.rar`` —— 兼容旧版发布；
        4. 任何 ``.7z``；
        5. 第一个非 ``.sha256`` 资产。
        """
        if not self.assets:
            return None
        if preferred_name:
            for a in self.assets:
                if a.name == preferred_name:
                    return a
        for ext in (".zip", ".rar", ".7z"):
            for a in self.assets:
                if a.name.lower().endswith(ext):
                    return a
        for a in self.assets:
            if not a.name.lower().endswith(".sha256"):
                return a
        return None

    def pick_sha256_asset(self, primary_name: str) -> Optional[ReleaseAsset]:
        """挑选与 ``primary_name`` 配对的 ``.sha256`` 文件（可选）。"""
        target = f"{primary_name}.sha256"
        for a in self.assets:
            if a.name == target:
                return a
        return None


# ───────────────────────── 解析 ─────────────────────────


def _parse_release_json(payload: Dict[str, Any]) -> LatestRelease:
    """把 GitHub Release JSON 解析为 :class:`LatestRelease`。"""
    tag = str(payload.get("tag_name") or "")
    assets_raw = payload.get("assets") or []
    assets: List[ReleaseAsset] = []
    for a in assets_raw:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or "")
        if not name:
            continue
        assets.append(
            ReleaseAsset(
                name=name,
                size=int(a.get("size") or 0),
                download_url=str(a.get("browser_download_url") or ""),
            )
        )
    return LatestRelease(
        tag=tag,
        version=strip_tag_prefix(tag),
        name=str(payload.get("name") or "") or tag,
        body=str(payload.get("body") or ""),
        html_url=str(payload.get("html_url") or ""),
        prerelease=bool(payload.get("prerelease") or False),
        published_at=str(payload.get("published_at") or ""),
        assets=assets,
    )


# ───────────────────────── 主入口 ─────────────────────────


def fetch_latest_release(
    source_order: List[str],
    proxies: Optional[Dict[str, str]] = None,
    include_prerelease: bool = False,
) -> Tuple[Optional[LatestRelease], List[Tuple[SourceId, str, str]]]:
    """按 ``source_order`` 顺序请求 release API，返回首个成功的结果。

    Args:
        source_order: 用户配置的源排序。
        proxies: ``requests`` 风格代理 dict（可为 ``None``）。
        include_prerelease: 当前未启用预发布通道；保留参数以便未来扩展。

    Returns:
        ``(release, attempts)``：

        * ``release`` 为成功获取的 :class:`LatestRelease`，全部失败则为 ``None``；
        * ``attempts`` 是 ``(source_id, url, error)`` 序列，供调用方记录日志。
    """
    attempts: List[Tuple[SourceId, str, str]] = []
    candidates = build_api_urls(source_order)
    for source_id, url in candidates:
        result = http_client.get_json(url, proxies=proxies)
        if not result.ok or not isinstance(result.body, dict):
            attempts.append((source_id, url, result.error or "未知错误"))
            continue
        try:
            release = _parse_release_json(result.body)  # type: ignore[arg-type]
        except Exception as e:
            attempts.append((source_id, url, f"解析失败: {e}"))
            continue
        # release.html_url 走的是 github.com，没问题；但是
        # 资产的 download_url 也来自 GitHub，可能需要替换为镜像。
        # 我们暂不在这里改写；让调用方使用 :func:`override_assets_with_source`
        # 决定是否替换。
        if not release.tag:
            attempts.append((source_id, url, "缺少 tag_name"))
            continue
        if release.prerelease and not include_prerelease:
            attempts.append((source_id, url, "命中预发布版本，已跳过"))
            continue
        attempts.append((source_id, url, ""))
        return release, attempts
    return None, attempts


def override_asset_urls(
    release: LatestRelease,
    source: SourceId,
    primary_asset_name: Optional[str] = None,
) -> LatestRelease:
    """把 release 的资产下载 URL 替换为指定源的 URL。

    GitHub Release JSON 里的 ``browser_download_url`` 永远是 github.com 的，但
    用户走 ``ghproxy`` / ``fastgit`` 下载时需要把 URL 改写。
    """
    new_assets: List[ReleaseAsset] = []
    for a in release.assets:
        if primary_asset_name and a.name != primary_asset_name:
            # 非主资产保持原样
            new_assets.append(a)
            continue
        new_url = build_download_url(source, release.tag, a.name)
        new_assets.append(ReleaseAsset(name=a.name, size=a.size, download_url=new_url))
    return LatestRelease(
        tag=release.tag,
        version=release.version,
        name=release.name,
        body=release.body,
        html_url=release.html_url,
        prerelease=release.prerelease,
        published_at=release.published_at,
        assets=new_assets,
    )
