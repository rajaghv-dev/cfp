"""Tests for cfp/prompts_parser.py against the real prompts.md."""

import pytest
from pathlib import Path

from cfp.prompts_parser import (
    load, get_prompt, category_keywords, parser_for_domain,
    PromptsSyntaxError,
)
from cfp.models import Category


def test_load_real_prompts():
    b = load()
    assert len(b.prompts) == 13
    assert "PROMPT_TIER1" in b.prompts
    assert "PROMPT_DEDUP" in b.prompts
    assert "PROMPT_QUALITY_GUARD" in b.prompts
    assert "PROMPT_DEADLINE_CHANGE" in b.prompts


def test_all_categories_present():
    b = load()
    for c in Category:
        assert c in b.categories, f"missing category: {c}"
    assert "AI" in b.categories[Category.AI]


def test_seed_urls_indices():
    b = load()
    series = [s for s in b.seed_urls if s.kind == "series_index"]
    journal = [s for s in b.seed_urls if s.kind == "journal_index"]
    assert len(series) == 26
    assert len(journal) == 26
    letters_s = sorted(s.letter for s in series)
    assert letters_s == list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def test_parser_registry():
    b = load()
    modules = {p.module for p in b.parsers}
    assert "cfp.parsers.wikicfp" in modules
    assert "cfp.parsers.ieee" in modules
    assert "cfp.parsers.acm" in modules
    assert "cfp.parsers.openreview" in modules
    assert len(b.parsers) >= 13


def test_external_urls_non_empty():
    b = load()
    assert b.external_urls
    assert any("ai-deadlines" in u for u in b.external_urls)


def test_parser_for_domain_longest_match():
    assert parser_for_domain("www.wikicfp.com") == "cfp.parsers.wikicfp"
    assert parser_for_domain("wikicfp.com") == "cfp.parsers.wikicfp"
    assert parser_for_domain("conferences.ieee.org") == "cfp.parsers.ieee"
    assert parser_for_domain("example.foo.bar") is None


def test_get_prompt_known():
    body = get_prompt("PROMPT_TIER1")
    assert body
    assert isinstance(body, str)


def test_get_prompt_unknown():
    with pytest.raises(KeyError):
        get_prompt("PROMPT_DOES_NOT_EXIST")


def test_category_keywords_returns_list():
    kws = category_keywords(Category.AI)
    assert isinstance(kws, list)
    assert "AI" in kws


def test_tab_rejected(tmp_path):
    bad = tmp_path / "p.md"
    bad.write_text("CATEGORY: AI\n\tKEYWORD: x\n")
    with pytest.raises(PromptsSyntaxError, match="tabs forbidden"):
        load(bad)


def test_unknown_category_rejected(tmp_path):
    bad = tmp_path / "p.md"
    bad.write_text("CATEGORY: NotARealCategory\n")
    with pytest.raises(PromptsSyntaxError, match="unknown category"):
        load(bad)


def test_orphan_keyword_rejected(tmp_path):
    bad = tmp_path / "p.md"
    bad.write_text("KEYWORD: x\n")
    with pytest.raises(PromptsSyntaxError, match="orphan KEYWORD"):
        load(bad)


def test_cache_invalidates_on_mtime(tmp_path):
    p = tmp_path / "p.md"
    p.write_text("CATEGORY: AI\nKEYWORD: a\n")
    b1 = load(p)
    assert len(b1.categories[Category.AI]) == 1
    import time
    time.sleep(0.01)
    p.write_text("CATEGORY: AI\nKEYWORD: a\nKEYWORD: b\n")
    b2 = load(p)
    assert len(b2.categories[Category.AI]) == 2
