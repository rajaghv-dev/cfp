"""Tests for cfp/parsers/ — WikiCFP, ai_deadlines, dispatch, stubs."""
from __future__ import annotations

from datetime import date

import pytest

from cfp.parsers import dispatch
from cfp.parsers import wikicfp, ai_deadlines
from cfp.parsers import ieee, acm, springer, usenix, edas, easychair, hotcrp, cmt, openreview


WIKICFP_HTML = """
<html><body>
<table>
  <tr><th>Event</th><th>When</th><th>Where</th><th>Deadline</th></tr>
  <tr>
    <td><a href="/cfp/servlet/event.showcfp?eventid=1001">NEURIPS 2026</a></td>
    <td colspan="3">Conference on Neural Information Processing Systems</td>
  </tr>
  <tr>
    <td>Dec 6, 2026 - Dec 12, 2026</td>
    <td>Vancouver, Canada</td>
    <td>May 15, 2026 (May 1, 2026)</td>
  </tr>
  <tr>
    <td><a href="/cfp/servlet/event.showcfp?eventid=1002">ICML 2026</a></td>
    <td colspan="3">International Conference on Machine Learning</td>
  </tr>
  <tr>
    <td>Jul 19, 2026 - Jul 25, 2026</td>
    <td>Vienna, Austria</td>
    <td>Feb 1, 2026</td>
  </tr>
</table>
</body></html>
"""


AI_DEADLINES_YAML = """
- title: NeurIPS
  year: 2026
  full_name: Conference on Neural Information Processing Systems
  link: https://neurips.cc/
  deadline: '2026-05-15 23:59:00'
  abstract_deadline: '2026-05-01 23:59:00'
  place: Vancouver, Canada
  sub: ML
- title: ICML
  year: 2026
  link: https://icml.cc/
  deadline: '2026-02-01 23:59:00'
  place: Vienna, Austria
  sub: ML
"""


def test_parse_search_page_returns_two_records():
    records = wikicfp.parse_search_page(WIKICFP_HTML)
    assert len(records) == 2
    r0, r1 = records
    assert r0["acronym"] == "NEURIPS 2026"
    assert r0["name"].startswith("Conference on Neural")
    assert r0["paper_deadline"] == date(2026, 5, 15)
    assert r0["abstract_deadline"] == date(2026, 5, 1)
    assert r0["where_raw"] == "Vancouver, Canada"
    assert r0["when_raw"].startswith("Dec 6, 2026")
    assert r0["start_date"] == date(2026, 12, 6)
    assert r0["end_date"] == date(2026, 12, 12)
    assert r0["event_id"] == 1001
    assert r0["origin_url"].endswith("eventid=1001")
    assert r0["source"] == "wikicfp"

    assert r1["acronym"] == "ICML 2026"
    assert r1["paper_deadline"] == date(2026, 2, 1)
    assert r1["where_raw"] == "Vienna, Austria"


def test_parse_search_page_uses_paper_deadline_field():
    records = wikicfp.parse_search_page(WIKICFP_HTML)
    for r in records:
        assert "paper_deadline" in r
        assert "deadline" not in r


def test_ai_deadlines_parse_returns_two_records():
    records = ai_deadlines.parse("https://example/deadlines.yml", AI_DEADLINES_YAML)
    assert len(records) == 2
    r0, r1 = records
    assert r0["acronym"] == "NeurIPS"
    assert r0["edition_year"] == 2026
    assert r0["paper_deadline"] == date(2026, 5, 15)
    assert r0["abstract_deadline"] == date(2026, 5, 1)
    assert r0["official_url"] == "https://neurips.cc/"
    assert r0["where_raw"] == "Vancouver, Canada"
    assert r0["raw_tags"] == ["ML"]
    assert r0["source"] == "ai_deadlines"

    assert r1["acronym"] == "ICML"
    assert r1["paper_deadline"] == date(2026, 2, 1)


def test_ai_deadlines_uses_paper_deadline_field():
    records = ai_deadlines.parse("https://example/deadlines.yml", AI_DEADLINES_YAML)
    for r in records:
        assert "paper_deadline" in r
        assert "deadline" not in r


def test_dispatch_routes_wikicfp_domain():
    url = "http://www.wikicfp.com/cfp/servlet/event.showcfp?eventid=1001"
    result = dispatch(url, "<html><body><table></table></body></html>")
    # WikiCFP detail-style URL → flat dict (not None).
    assert result is not None
    assert result.get("source") == "wikicfp"
    assert result.get("event_id") == 1001


def test_dispatch_unknown_domain_returns_none():
    assert dispatch("https://no-such-domain.example/foo", "<html></html>") is None


def test_dispatch_malformed_url_returns_none():
    assert dispatch("not a url", "<html></html>") is None


@pytest.mark.parametrize("mod", [
    ieee, acm, springer, usenix, edas, easychair, hotcrp, cmt, openreview,
])
def test_stubs_return_none(mod):
    assert mod.parse("https://example.com/x", "<html></html>") is None


def test_dispatch_stub_domain_returns_none():
    # ieeexplore.ieee.org is registered in prompts.md → routes to ieee stub → None.
    assert dispatch("https://ieeexplore.ieee.org/document/12345",
                    "<html></html>") is None


def test_no_record_uses_legacy_deadline_field():
    """Regression guard: no parser may emit 'deadline' (must be 'paper_deadline')."""
    wiki = wikicfp.parse_search_page(WIKICFP_HTML)
    aid = ai_deadlines.parse("u", AI_DEADLINES_YAML)
    for r in wiki + aid:
        assert "deadline" not in r, f"forbidden 'deadline' key in {r}"
        assert "paper_deadline" in r
