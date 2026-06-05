"""``strange_uta_game.updater.sources`` 单元测试。"""

import pytest

from strange_uta_game.__version__ import REPO_NAME, REPO_OWNER
from strange_uta_game.updater.sources import (
    DEFAULT_ORDER,
    SOURCE_IDS,
    SOURCE_LABELS,
    build_api_urls,
    build_download_url,
    build_release_urls,
    normalize_order,
)


class TestNormalizeOrder:
    def test_empty_returns_default(self):
        assert normalize_order([]) == list(DEFAULT_ORDER)

    def test_keeps_user_order(self):
        assert normalize_order(["gh-proxy", "github"]) == [
            "gh-proxy",
            "github",
            "ghproxy",
            "ghproxy-net",
        ]

    def test_drops_unknown(self):
        assert normalize_order(["bad", "github", "x"]) == list(DEFAULT_ORDER)

    def test_deduplicates(self):
        assert normalize_order(["github", "github", "ghproxy"]) == [
            "github",
            "ghproxy",
            "gh-proxy",
            "ghproxy-net",
        ]


class TestBuildDownloadUrl:
    def test_github_direct(self):
        url = build_download_url("github", "SUGv0.3.2", "StrangeUtaGame-v0.3.2.zip")
        assert url == (
            f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
            f"/releases/download/SUGv0.3.2/StrangeUtaGame-v0.3.2.zip"
        )

    def test_ghproxy_wraps_github(self):
        url = build_download_url("ghproxy", "SUGv0.3.2", "F.zip")
        assert url.startswith("https://mirror.ghproxy.com/https://github.com/")
        assert url.endswith("/SUGv0.3.2/F.zip")

    def test_gh_proxy_wraps_github(self):
        url = build_download_url("gh-proxy", "SUGv0.3.2", "F.zip")
        assert url.startswith("https://gh-proxy.com/https://github.com/")
        assert url.endswith("/SUGv0.3.2/F.zip")

    def test_unknown_source_raises(self):
        with pytest.raises(ValueError):
            build_download_url("rofl", "SUGv0.3.2", "F.zip")  # type: ignore[arg-type]


class TestBuildReleaseUrls:
    def test_default_order(self):
        urls = build_release_urls(list(DEFAULT_ORDER), "SUGv1", "X.zip")
        assert [sid for sid, _ in urls] == list(DEFAULT_ORDER)

    def test_user_order(self):
        urls = build_release_urls(["ghproxy-net", "github"], "SUGv1", "X.zip")
        assert [sid for sid, _ in urls] == [
            "ghproxy-net",
            "github",
            "ghproxy",
            "gh-proxy",
        ]

    def test_url_content(self):
        urls = dict(build_release_urls(SOURCE_IDS, "T", "F.zip"))
        for sid, url in urls.items():
            assert "T" in url and "F.zip" in url


class TestBuildApiUrls:
    def test_all_sources(self):
        api = build_api_urls(list(SOURCE_IDS))
        assert len(api) == len(SOURCE_IDS)
        # GitHub 官方
        assert api[0][1].startswith("https://api.github.com/repos/")
        for _, url in api[1:]:
            assert "/https://api.github.com/repos/" in url


class TestSourceLabels:
    def test_all_have_labels(self):
        for sid in SOURCE_IDS:
            assert sid in SOURCE_LABELS
            assert SOURCE_LABELS[sid]  # 非空
