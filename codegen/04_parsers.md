# Codegen 04 — cfp/parsers/

## Files to Create
- `cfp/parsers/__init__.py`
- `cfp/parsers/wikicfp.py`        (primary — full implementation)
- `cfp/parsers/ai_deadlines.py`   (full implementation)
- `cfp/parsers/ieee.py`           (stub)
- `cfp/parsers/acm.py`            (stub)
- `cfp/parsers/springer.py`       (stub)
- `cfp/parsers/usenix.py`         (stub)
- `cfp/parsers/conferencelist.py` (stub)
- `cfp/parsers/guide2research.py` (stub)

## Imports needed
```python
from cfp.models import Event, Category
from config import USER_AGENT, HUMAN_DELAY_MIN, HUMAN_DELAY_MAX
```

---

## COPY VERBATIM: _is_english()

```python
import re

_NON_LATIN_RE = re.compile(
    r"[Ѐ-ӿ"   # Cyrillic
    r"؀-ۿ"    # Arabic
    r"ऀ-ॿ"    # Devanagari
    r"一-鿿"    # CJK Unified Ideographs
    r"぀-ヿ"    # Hiragana / Katakana
    r"가-힯"    # Hangul
    r"฀-๿"    # Thai
    r"א-ת]"   # Hebrew
)

def _is_english(text: str) -> bool:
    """Return True if text is predominantly Latin/English script (<5% non-Latin)."""
    if not text:
        return True
    non_latin = len(_NON_LATIN_RE.findall(text))
    return non_latin / max(len(text), 1) < 0.05
```

---

## COPY VERBATIM: _safe_parse_date()

```python
from datetime import date
from typing import Optional
from dateutil import parser as dateparser

def _safe_parse_date(s: Optional[str]) -> Optional[date]:
    """Parse a date string robustly; return None for TBD/N/A/empty."""
    if not s or s.strip() in ("", "TBD", "N/A", "-"):
        return None
    try:
        return dateparser.parse(s, fuzzy=True).date()
    except Exception:
        return None
```

---

## COPY VERBATIM: WikiCFP deadline cell parsing (from conf-scr-org-syn)

```python
def _parse_deadline_cell(dl_text: str):
    """
    WikiCFP deadline cell format: "Dec 1, 2025 (Nov 1, 2025)"
    → paper_deadline=Dec 1, abstract_deadline=Nov 1
    Returns (paper_deadline, abstract_deadline) as date | None.
    """
    paper_dl = abstract_dl = None
    m = re.match(r"^([^(]+?)(?:\s*\(.*\))?$", dl_text)
    if m:
        paper_dl = _safe_parse_date(m.group(1).strip())
    m_abs = re.search(r"\(([^)]+)\)", dl_text)
    if m_abs:
        abstract_dl = _safe_parse_date(m_abs.group(1))
    return paper_dl, abstract_dl
```

---

## COPY VERBATIM: WikiCFP paired-row parser (from current scraper.py)

The existing `scraper.py` has `find_data_table()` and `parse_table()`.
These are CORRECT — copy them into `wikicfp.py` unchanged.

```python
# from scraper.py — copy verbatim
def find_data_table(soup):
    for table in soup.find_all("table"):
        header_row = table.find("tr")
        if not header_row:
            continue
        texts = [td.get_text(strip=True).lower()
                 for td in header_row.find_all(["td", "th"])]
        if "event" in texts and "when" in texts:
            return table
    return None

def parse_table(table) -> list[dict]:
    """Parse paired rows: row-A has acronym+link+name, row-B has when/where/deadline."""
    rows = table.find_all("tr")
    results = []
    i = 0
    while i < len(rows):
        cells_a = rows[i].find_all(["td", "th"])
        if not cells_a or cells_a[0].get_text(strip=True).lower() == "event":
            i += 1
            continue
        link = cells_a[0].find("a")
        if not link:
            i += 1
            continue
        acronym  = link.get_text(strip=True)
        href     = link.get("href", "")
        full_name = cells_a[1].get_text(strip=True) if len(cells_a) > 1 else ""
        url = "http://www.wikicfp.com" + href if href.startswith("/") else href

        i += 1
        when = where = deadline_raw = ""
        if i < len(rows):
            cells_b = rows[i].find_all(["td", "th"])
            if cells_b and not cells_b[0].find("a"):
                when        = cells_b[0].get_text(strip=True) if len(cells_b) > 0 else ""
                where       = cells_b[1].get_text(strip=True) if len(cells_b) > 1 else ""
                deadline_raw = cells_b[2].get_text(strip=True) if len(cells_b) > 2 else ""
                i += 1

        results.append({
            "acronym": acronym, "name": full_name,
            "when": when, "where": where,
            "deadline_raw": deadline_raw, "url": url
        })
    return results
```

---

## wikicfp.py: parse_search_page() — new function wrapping parse_table

```python
def parse_search_page(html: str) -> list[Event]:
    """Parse a WikiCFP search result page into a list of partial Events."""
    soup = BeautifulSoup(html, "lxml")
    table = find_data_table(soup)
    if not table:
        return []
    events = []
    for row in parse_table(table):
        if not _is_english(f"{row['acronym']} {row['name']}"):
            continue
        paper_dl, abstract_dl = _parse_deadline_cell(row["deadline_raw"])
        # Parse date range from "when" field
        start = end = None
        parts = re.split(r"\s*[-–]\s*", row["when"])
        if parts:
            start = _safe_parse_date(parts[0])
        if len(parts) > 1:
            end = _safe_parse_date(parts[-1])
        year = (start.year if start else
                (paper_dl.year if paper_dl else datetime.now().year))
        events.append(Event(
            event_id     = _extract_event_id(row["url"]),  # parse eventid=N from URL
            acronym      = row["acronym"],
            name         = row["name"],
            edition_year = year,
            when_raw     = row["when"],
            start_date   = start,
            end_date     = end,
            where_raw    = row["where"],
            paper_deadline = paper_dl,
            abstract_deadline = abstract_dl,
            origin_url  = row["url"],
            source       = "wikicfp",
        ))
    return events

def _extract_event_id(url: str) -> int:
    """Extract eventid=N from WikiCFP URL; return -1 if not found."""
    m = re.search(r"eventid=(\d+)", url)
    return int(m.group(1)) if m else -1
```

---

## wikicfp.py: parse_event_detail() — enrichment from detail page

```python
def parse_event_detail(html: str) -> dict:
    """
    Parse a WikiCFP event detail page.
    Returns dict with: official_url, when, where, paper_deadline, notification,
    camera_ready, raw_tags, description.
    """
    soup = BeautifulSoup(html, "lxml")
    result = {}

    # Official URL: external link whose text IS the URL
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http") and "wikicfp" not in href:
            text = a.get_text(strip=True)
            if text.startswith("http") or "Link:" in (a.find_parent() or a).get_text():
                result["official_url"] = href
                break

    # When/Where/Submission Deadline/Notification/Camera-ready
    for td in soup.find_all("td"):
        text = td.get_text(separator="|||", strip=True)
        if "Submission Deadline" in text or "Notification Due" in text:
            parts = text.split("|||")
            kv = {}
            for i in range(0, len(parts) - 1, 2):
                kv[parts[i].strip()] = parts[i + 1].strip()
            result["when"]         = kv.get("When", "")
            result["where"]        = kv.get("Where", "")
            result["paper_deadline"] = _safe_parse_date(kv.get("Submission Deadline"))
            result["notification"] = _safe_parse_date(kv.get("Notification Due"))
            result["camera_ready"] = _safe_parse_date(kv.get("Final Version Due"))
            # Categories (raw tags)
            cats_text = kv.get("Categories", "")
            result["raw_tags"] = [t.strip() for t in cats_text.split("|||") if t.strip()]
            break

    # Description: first substantial paragraph
    cfp_div = soup.find("div", class_="cfp")
    if cfp_div:
        result["description"] = cfp_div.get_text(" ", strip=True)[:600]

    return result
```

---

## wikicfp.py: parse_series_index()

```python
def parse_series_index(html: str) -> list[dict]:
    """Parse /cfp/series?t=c&i=A — return list of {series_id, acronym, full_name, url}."""
    soup = BeautifulSoup(html, "lxml")
    series = []
    for a in soup.find_all("a", href=re.compile(r"/cfp/program\?id=\d+")):
        m = re.search(r"id=(\d+).*?s=([^&]+).*?f=(.+)", a["href"])
        if m:
            series.append({
                "series_id": int(m.group(1)),
                "acronym":   unquote(m.group(2)),
                "full_name": unquote(m.group(3)),
                "url":       "http://www.wikicfp.com" + a["href"],
            })
    return series
```

---

## ai_deadlines.py — full implementation

```python
AI_DEADLINES_URL = (
    "https://raw.githubusercontent.com/paperswithcode/ai-deadlines"
    "/gh-pages/_data/conferences.yml"
)

def scrape_ai_deadlines() -> list[Event]:
    """Fetch AI/ML/CV/NLP conference deadlines from the paperswithcode ai-deadlines dataset."""
    import yaml
    import requests
    resp = requests.get(AI_DEADLINES_URL,
                        headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    data = yaml.safe_load(resp.text)
    events = []
    seen_slugs: set[str] = set()
    for item in data:
        short      = item.get("title", item.get("name", ""))
        name       = item.get("full_name", short)
        url        = item.get("link", item.get("website", ""))
        year       = item.get("year", datetime.now().year)
        location   = item.get("place", item.get("location", ""))

        abstract_dl = _safe_parse_date(str(item.get("abstract_deadline") or ""))
        raw_deadline = item.get("deadline", "")
        if isinstance(raw_deadline, dict):
            if not abstract_dl:
                abstract_dl = _safe_parse_date(str(raw_deadline.get("abstract", "")))
            paper_dl = _safe_parse_date(str(raw_deadline.get("paper", "")))
        else:
            paper_dl = _safe_parse_date(str(raw_deadline or ""))

        start = _safe_parse_date(str(item.get("start") or ""))
        end   = _safe_parse_date(str(item.get("end")   or ""))

        ev = Event(
            event_id  = -1,        # no WikiCFP id for this source
            acronym   = short,
            name      = name,
            edition_year = year,
            start_date   = start,
            end_date     = end,
            where_raw    = location,
            paper_deadline = paper_dl,
            abstract_deadline = abstract_dl,
            official_url = url,
            source    = "ai_deadlines",
        )
        if ev.slug not in seen_slugs:
            seen_slugs.add(ev.slug)
            events.append(ev)
    return events
```

---

## parsers/__init__.py

```python
from urllib.parse import urlparse
from cfp.parsers import wikicfp as _wikicfp

# Base registry — extended at startup from PARSER: lines in prompts.md
KNOWN_PARSERS: dict[str, callable] = {
    "wikicfp.com":          _wikicfp.parse_event_detail,
    "www.wikicfp.com":      _wikicfp.parse_event_detail,
}

def dispatch(url: str, html: str) -> dict | None:
    """Try rule-based parser for this domain; return enrichment dict or None."""
    domain = urlparse(url).netloc
    fn = KNOWN_PARSERS.get(domain)
    return fn(html) if fn else None

def register(domain: str, fn: callable) -> None:
    KNOWN_PARSERS[domain] = fn
```

---

## Stub pattern for ieee.py / acm.py / springer.py / usenix.py

```python
"""Parser for <domain> conference pages. Stub — implement per-site."""
from cfp.models import Event

def parse(html: str) -> dict:
    """Extract conference fields from an <domain> page.
    Returns same dict shape as wikicfp.parse_event_detail().
    Raise NotImplementedError until implemented."""
    raise NotImplementedError("ieee parser not yet implemented")
```
