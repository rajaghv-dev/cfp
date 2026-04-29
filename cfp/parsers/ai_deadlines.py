"""ai-deadlines YAML parser.

Source: https://raw.githubusercontent.com/paperswithcode/ai-deadlines/main/deadlines.yml
Each upstream entry: title, year, link, deadline, abstract_deadline, place, sub.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import yaml
from dateutil import parser as dateparser


def _safe_parse_date(s) -> Optional[date]:
    if s is None:
        return None
    if isinstance(s, date) and not isinstance(s, datetime):
        return s
    if isinstance(s, datetime):
        return s.date()
    s = str(s).strip()
    if s in ("", "TBD", "N/A", "-"):
        return None
    try:
        return dateparser.parse(s, fuzzy=True).date()
    except Exception:
        return None


def parse(url: str, content: str) -> list[dict]:
    """Parse ai-deadlines YAML text; return list of event dicts."""
    data = yaml.safe_load(content) or []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("name") or ""
        year = item.get("year")

        raw_deadline = item.get("deadline")
        raw_abstract = item.get("abstract_deadline")
        if isinstance(raw_deadline, dict):
            paper_dl = _safe_parse_date(raw_deadline.get("paper"))
            if not raw_abstract:
                raw_abstract = raw_deadline.get("abstract")
        else:
            paper_dl = _safe_parse_date(raw_deadline)
        abstract_dl = _safe_parse_date(raw_abstract)

        sub = item.get("sub")
        raw_tags = [sub] if sub else []

        out.append({
            "event_id":          -1,
            "acronym":           title,
            "name":              item.get("full_name") or title,
            "edition_year":      year,
            "official_url":      item.get("link") or item.get("website") or "",
            "paper_deadline":    paper_dl,
            "abstract_deadline": abstract_dl,
            "where_raw":         item.get("place") or item.get("location") or "",
            "start_date":        _safe_parse_date(item.get("start")),
            "end_date":          _safe_parse_date(item.get("end")),
            "raw_tags":          raw_tags,
            "source":            "ai_deadlines",
        })
    return out
