"""罗马音注音集成测试：验证 AutoCheckService 开关全链路行为。"""

import pytest
from strange_uta_game.backend.application import AutoCheckService
from strange_uta_game.backend.domain import Character, Project, Ruby, RubyPart, Sentence
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import DummyAnalyzer, RubyAnalyzer, RubyResult

ROMAJI_FLAGS = {"romanize_ruby": True, "check_n": True, "check_sokuon": True}


class StaticAnalyzer(RubyAnalyzer):
    def __init__(self, results):
        self._results = results
    def analyze(self, text: str):
        return self._results
    def get_reading(self, text: str) -> str:
        return "".join(result.reading for result in self._results)


def _ruby_parts(sentence: Sentence):
    return [[part.text for part in ch.ruby.parts] if ch.ruby else [] for ch in sentence.characters]


def test_kana_self_reading_gets_ruby_and_romaji():
    service = AutoCheckService(DummyAnalyzer(), auto_check_flags=ROMAJI_FLAGS)
    sentence = Sentence.from_text("あい", "s1")
    service.apply_to_sentence(sentence)
    assert _ruby_parts(sentence) == [["a"], ["i"]]
    assert [ch.check_count for ch in sentence.characters] == [1, 1]


def test_kanji_gets_romaji_ruby():
    analyzer = StaticAnalyzer([RubyResult(text="今日", reading="きょう", start_idx=0, end_idx=2)])
    service = AutoCheckService(analyzer, auto_check_flags=ROMAJI_FLAGS)
    sentence = Sentence.from_text("今日", "s1")
    service.apply_to_sentence(sentence)
    parts = _ruby_parts(sentence)
    for p_list in parts:
        for p in p_list:
            assert all(c.isascii() or c == "'" for c in p), f"Expected ASCII, got {p}"


def test_sokuon_cross_part_context():
    analyzer = StaticAnalyzer([
        RubyResult(text="待", reading="ま", start_idx=0, end_idx=1),
        RubyResult(text="っ", reading="っ", start_idx=1, end_idx=2),
        RubyResult(text="て", reading="て", start_idx=2, end_idx=3),
    ])
    service = AutoCheckService(analyzer, auto_check_flags=ROMAJI_FLAGS)
    sentence = Sentence.from_text("待って", "s1")
    service.apply_to_sentence(sentence)
    assert _ruby_parts(sentence) == [["ma"], ["t"], ["te"]]
    assert [ch.check_count for ch in sentence.characters] == [1, 1, 1]


def test_user_dict_romaji_override():
    service = AutoCheckService(DummyAnalyzer(), auto_check_flags=ROMAJI_FLAGS,
        user_dictionary=[{"enabled": True, "word": "今日", "reading": "{今日||きょ,う}"}])
    sentence = Sentence.from_text("今日", "s1")
    service.apply_to_sentence(sentence)
    assert _ruby_parts(sentence) == [["kyo"], ["u"]]
    assert [ch.check_count for ch in sentence.characters] == [1, 1]


def test_user_dict_restores_kana_for_romaji():
    service = AutoCheckService(DummyAnalyzer(), auto_check_flags=ROMAJI_FLAGS,
        user_dictionary=[{"enabled": True, "word": "\u751f\u304d\u69d8",
            "reading": "{\u751f||\u3044}\u304d{\u69d8||\u3056|\u307e}"}])
    sentence = Sentence.from_text("\u751f\u304d\u69d8", "s1")
    service.apply_to_sentence(sentence)
    assert _ruby_parts(sentence) == [["i"], ["ki"], ["za", "ma"]]
    assert [ch.check_count for ch in sentence.characters] == [1, 1, 2]


def test_linked_kanji_no_romaji_self_ruby():
    entries = [{"enabled": True, "word": "\u8cb4\u65b9", "reading": "{\u8cb4\u65b9||\u3042|\u306a|\u305f,}"}]
    service = AutoCheckService(DummyAnalyzer(), auto_check_flags=ROMAJI_FLAGS, user_dictionary=entries)
    sentence = Sentence.from_text("\u8cb4\u65b9", "s1")
    service.apply_to_sentence(sentence)
    chars = sentence.characters
    assert chars[0].linked_to_next is True
    assert chars[1].ruby is None
    assert chars[1].check_count == 0


def test_update_checkpoints_preserves_part_count_in_romaji_mode():
    service = AutoCheckService(DummyAnalyzer(), auto_check_flags=ROMAJI_FLAGS)
    sentence = Sentence(singer_id="s1", characters=[
        Character(char="\u97f3", ruby=Ruby(parts=[RubyPart(text="hi"), RubyPart(text="bi"), RubyPart(text="ki")]),
                  check_count=1, singer_id="s1")])
    service.update_checkpoints_from_rubies(sentence)
    assert sentence.characters[0].check_count == 3


def test_default_off_preserves_kana_ruby():
    service = AutoCheckService(DummyAnalyzer(), auto_check_flags={})
    sentence = Sentence.from_text("あい", "s1")
    service.apply_to_sentence(sentence)
    assert _ruby_parts(sentence) == [[], []]
    assert [ch.check_count for ch in sentence.characters] == [1, 1]


def test_particle_wa_detection():
    service = AutoCheckService(DummyAnalyzer(), auto_check_flags=ROMAJI_FLAGS)
    sentence = Sentence.from_text("\u79c1\u306f", "s1")
    service.apply_to_sentence(sentence)
    parts = _ruby_parts(sentence)
    assert parts[1] == ["wa"]


def test_word_initial_ha_not_particle():
    service = AutoCheckService(DummyAnalyzer(), auto_check_flags=ROMAJI_FLAGS)
    sentence = Sentence.from_text("\u306f\u3058\u3081", "s1")
    service.apply_to_sentence(sentence)
    parts = _ruby_parts(sentence)
    assert parts[0] == ["ha"]


def test_wo_always_particle():
    service = AutoCheckService(DummyAnalyzer(), auto_check_flags=ROMAJI_FLAGS)
    sentence = Sentence.from_text("\u3092", "s1")
    service.apply_to_sentence(sentence)
    assert _ruby_parts(sentence) == [["o"]]
