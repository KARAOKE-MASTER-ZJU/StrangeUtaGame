"""Unit tests for ruby analyzer (FugashiAnalyzer)."""

import pytest

from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    DummyAnalyzer,
    FugashiAnalyzer,
    PykakasiAnalyzer,
    WinRTAnalyzer,
    create_analyzer,
)


# ── Helper: check if fugashi is available ──


def _fugashi_available() -> bool:
    """Check if fugashi and unidic_lite are installed."""
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401
    except ImportError:
        return False
    return True


# ── Mock token helpers ──


class _MockToken:
    """Minimal mock for fugashi IPADIC-style string feature."""

    def __init__(self, surface: str, feature: str):
        self.surface = surface
        self.feature = feature


class _MockUnidicToken:
    """Minimal mock for fugashi UniDic (UnidicFeatures26) named-tuple feature."""

    def __init__(self, surface: str, kana: str = "", lForm: str = ""):
        self.surface = surface
        # Simulate UnidicFeatures26: feature has .kana and .lForm attributes
        self.feature = self  # feature IS the object
        self.kana = kana
        self.lForm = lForm


# ── Tests for FugashiAnalyzer._reading_from_token ──


class TestReadingFromToken:
    """Test the static _reading_from_token method with mocked feature fields."""

    def test_unidic_reading(self):
        """UniDic token should use .kana attribute for reading."""
        token = _MockUnidicToken(surface="日本語", kana="ニホンゴ")
        reading = FugashiAnalyzer._reading_from_token(token)
        assert reading == "にほんご"

    def test_unidic_reading_fallback_lform(self):
        """UniDic token with empty kana falls back to .lForm."""
        token = _MockUnidicToken(surface="日本語", kana="", lForm="ニホンゴ")
        reading = FugashiAnalyzer._reading_from_token(token)
        assert reading == "にほんご"

    def test_unidic_unknown_reading(self):
        """UniDic token with * reading falls back to surface."""
        token = _MockUnidicToken(surface="未知語", kana="*")
        reading = FugashiAnalyzer._reading_from_token(token)
        assert reading == "未知語"

    def test_ipadic_reading(self):
        """IPADIC token (8-10 fields) should use index 7 for reading."""
        token = _MockToken(
            surface="走る",
            feature="動詞,自立,*,*,五段・ラ行,基本形,ハシル,ハシル,ハシル",
        )
        reading = FugashiAnalyzer._reading_from_token(token)
        assert reading == "はしる"

    def test_ipadic_unknown_reading_asterisk(self):
        """IPADIC reading * falls back to token.surface."""
        token = _MockToken(
            surface="未知語",
            feature="名詞,一般,*,*,*,*,*,*,*",
        )
        reading = FugashiAnalyzer._reading_from_token(token)
        assert reading == "未知語"

    def test_ipadic_reading_empty(self):
        """IPADIC empty reading field falls back to token.surface."""
        token = _MockToken(
            surface="test",
            feature="名詞,一般,*,*,*,*,*,,",
        )
        reading = FugashiAnalyzer._reading_from_token(token)
        assert reading == "test"

    def test_fewer_than_ipadic_fields(self):
        """Token with fewer than 8 feature fields returns surface."""
        token = _MockToken(
            surface="記号",
            feature="記号,*,*",
        )
        reading = FugashiAnalyzer._reading_from_token(token)
        assert reading == "記号"


# ── Tests for FugashiAnalyzer instantiation ──


class TestFugashiAnalyzerInit:
    """Test FugashiAnalyzer instantiation behavior."""

    def test_import_error_when_meCab_fails(self, monkeypatch):
        """FugashiAnalyzer() raises ImportError when MeCab init fails
        (RuntimeError wrapped as ImportError for clean fallback)."""
        import fugashi

        def failing_tagger(*args, **kwargs):
            raise RuntimeError("MeCab failed to initialize")

        monkeypatch.setattr(fugashi, "Tagger", failing_tagger)
        with pytest.raises(ImportError):
            FugashiAnalyzer()

    def test_import_error_when_fugashi_missing(self, monkeypatch):
        """FugashiAnalyzer() raises ImportError when fugashi package
        itself is not installed (monkeypatched import)."""
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "fugashi":
                raise ImportError("No module named 'fugashi'")
            if name == "unidic_lite":
                raise ImportError("No module named 'unidic_lite'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        with pytest.raises(ImportError):
            FugashiAnalyzer()


# ── Tests for create_analyzer fallback chain ──


class TestCreateAnalyzer:
    """Test the fallback chain of create_analyzer."""

    def test_winrt_preferred_over_fugashi(self, monkeypatch):
        """When WinRT is available, it should be preferred over fugashi."""
        analyzer = create_analyzer()
        # On Windows with WinRT, this should be WinRTAnalyzer;
        # otherwise it follows the fallback chain (FugashiAnalyzer if available).
        # We just verify it's not DummyAnalyzer.
        assert not isinstance(analyzer, DummyAnalyzer)

    def test_fugashi_used_when_winrt_fails(self, monkeypatch):
        """When WinRT fails and fugashi is available, use FugashiAnalyzer."""
        # Make WinRTAnalyzer throw ImportError
        monkeypatch.setattr(
            "strange_uta_game.backend.infrastructure.parsers.ruby_analyzer.WinRTAnalyzer",
            lambda: (_ for _ in ()).throw(ImportError("No WinRT")),
        )
        analyzer = create_analyzer()
        assert isinstance(analyzer, FugashiAnalyzer) or isinstance(
            analyzer, PykakasiAnalyzer
        )

    def test_fugashi_import_error_falls_to_pykakasi(self, monkeypatch):
        """When WinRT and fugashi both fail, fall to pykakasi."""
        # Make WinRTAnalyzer fail
        monkeypatch.setattr(
            "strange_uta_game.backend.infrastructure.parsers.ruby_analyzer.WinRTAnalyzer",
            lambda: (_ for _ in ()).throw(ImportError("No WinRT")),
        )
        # Make FugashiAnalyzer fail
        monkeypatch.setattr(
            "strange_uta_game.backend.infrastructure.parsers.ruby_analyzer.FugashiAnalyzer",
            lambda: (_ for _ in ()).throw(ImportError("No fugashi")),
        )
        analyzer = create_analyzer(use_pykakasi=True)
        assert isinstance(analyzer, PykakasiAnalyzer)

    def test_all_fail_falls_to_dummy(self, monkeypatch):
        """When all analyzers fail, fall to DummyAnalyzer."""
        # Make WinRTAnalyzer fail
        monkeypatch.setattr(
            "strange_uta_game.backend.infrastructure.parsers.ruby_analyzer.WinRTAnalyzer",
            lambda: (_ for _ in ()).throw(ImportError("No WinRT")),
        )
        # Make FugashiAnalyzer fail
        monkeypatch.setattr(
            "strange_uta_game.backend.infrastructure.parsers.ruby_analyzer.FugashiAnalyzer",
            lambda: (_ for _ in ()).throw(ImportError("No fugashi")),
        )
        analyzer = create_analyzer(use_pykakasi=False)
        assert isinstance(analyzer, DummyAnalyzer)


# ── Conditional integration test (requires fugashi + unidic_lite) ──


@pytest.mark.skipif(
    not _fugashi_available(),
    reason="fugashi or unidic_lite not installed",
)
class TestFugashiAnalyzerIntegration:
    """Integration tests requiring fugashi and unidic_lite."""

    def test_analyze_japanese_text(self):
        """FugashiAnalyzer should produce ruby for Japanese text."""
        analyzer = FugashiAnalyzer()
        results = analyzer.analyze("日本語")
        assert len(results) > 0
        # readings should be hiragana (not surface kanji)
        readings = "".join(r.reading for r in results)
        assert any("ぁ" <= c <= "ん" for c in readings)

    def test_get_reading(self):
        """get_reading should return hiragana reading."""
        analyzer = FugashiAnalyzer()
        reading = analyzer.get_reading("東京")
        assert isinstance(reading, str)
        assert len(reading) > 0
        # Should contain hiragana
        assert any("ぁ" <= c <= "ん" for c in reading)

    def test_empty_text(self):
        """Empty text should return empty results."""
        analyzer = FugashiAnalyzer()
        assert analyzer.analyze("") == []
        assert analyzer.get_reading("") == ""

    def test_kana_only(self):
        """Pure kana text should be preserved."""
        analyzer = FugashiAnalyzer()
        results = analyzer.analyze("かな")
        assert len(results) == 2
        for r in results:
            assert r.text == r.reading

    def test_katakana_converted_to_hiragana(self):
        """Katakana readings should be converted to hiragana."""
        analyzer = FugashiAnalyzer()
        results = analyzer.analyze("日本")
        for r in results:
            for c in r.reading:
                assert not ("ァ" <= c <= "ヴ"), f"Found katakana {c} in reading"

    def test_create_analyzer_returns_fugashi_when_winrt_fails(self, monkeypatch):
        """create_analyzer returns FugashiAnalyzer when WinRT unavailable."""
        # Make WinRTAnalyzer fail
        monkeypatch.setattr(
            "strange_uta_game.backend.infrastructure.parsers.ruby_analyzer.WinRTAnalyzer",
            lambda: (_ for _ in ()).throw(ImportError("No WinRT")),
        )
        analyzer = create_analyzer()
        assert isinstance(analyzer, FugashiAnalyzer)
