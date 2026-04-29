"""WikiCFP parser. Paired-row table parsing copied verbatim from scraper.py."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from bs4 import BeautifulSoup
from dateutil import parser as dateparser


_BASE_URL = "http://www.wikicfp.com"


# Non-Latin script ranges — used to drop non-English entries early.
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


def _safe_parse_date(s: Optional[str]) -> Optional[date]:
    """Parse a date string robustly; return None for TBD/N/A/empty."""
    if not s or s.strip() in ("", "TBD", "N/A", "-"):
        return None
    try:
        return dateparser.parse(s, fuzzy=True).date()
    except Exception:
        return None


def _parse_deadline_cell(dl_text: str):
    """WikiCFP deadline cell format: "Dec 1, 2025 (Nov 1, 2025)"
    -> paper_deadline=Dec 1, abstract_deadline=Nov 1.
    """
    paper_dl = abstract_dl = None
    m = re.match(r"^([^(]+?)(?:\s*\(.*\))?$", dl_text or "")
    if m:
        paper_dl = _safe_parse_date(m.group(1).strip())
    m_abs = re.search(r"\(([^)]+)\)", dl_text or "")
    if m_abs:
        abstract_dl = _safe_parse_date(m_abs.group(1))
    return paper_dl, abstract_dl


def find_data_table(soup):
    """Return the main results table that has Event/When/Where/Deadline headers."""
    for table in soup.find_all("table"):
        header_row = table.find("tr")
        if not header_row:
            continue
        texts = [td.get_text(strip=True).lower() for td in header_row.find_all(["td", "th"])]
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
        acronym = link.get_text(strip=True)
        href = link.get("href", "")
        full_name = cells_a[1].get_text(strip=True) if len(cells_a) > 1 else ""
        url = _BASE_URL + href if href.startswith("/") else href

        i += 1
        when = where = deadline_raw = ""
        if i < len(rows):
            cells_b = rows[i].find_all(["td", "th"])
            if cells_b and not cells_b[0].find("a"):
                when = cells_b[0].get_text(strip=True) if len(cells_b) > 0 else ""
                where = cells_b[1].get_text(strip=True) if len(cells_b) > 1 else ""
                deadline_raw = cells_b[2].get_text(strip=True) if len(cells_b) > 2 else ""
                i += 1

        results.append({
            "acronym": acronym, "name": full_name,
            "when": when, "where": where,
            "deadline_raw": deadline_raw, "url": url,
        })
    return results


def _extract_event_id(url: str) -> int:
    m = re.search(r"eventid=(\d+)", url or "")
    return int(m.group(1)) if m else -1


def parse_search_page(html: str) -> list[dict]:
    """Parse a WikiCFP search/listing page; return partial event dicts."""
    soup = BeautifulSoup(html, "lxml")
    table = find_data_table(soup)
    if not table:
        return []
    out: list[dict] = []
    for row in parse_table(table):
        if not _is_english(f"{row['acronym']} {row['name']}"):
            continue
        paper_dl, abstract_dl = _parse_deadline_cell(row["deadline_raw"])

        start = end = None
        parts = re.split(r"\s*[-–]\s*", row["when"]) if row["when"] else []
        if parts:
            start = _safe_parse_date(parts[0])
        if len(parts) > 1:
            end = _safe_parse_date(parts[-1])
        year = (start.year if start else
                (paper_dl.year if paper_dl else datetime.now().year))

        out.append({
            "event_id":          _extract_event_id(row["url"]),
            "acronym":           row["acronym"],
            "name":              row["name"],
            "edition_year":      year,
            "when_raw":          row["when"],
            "start_date":        start,
            "end_date":          end,
            "where_raw":         row["where"],
            "paper_deadline":    paper_dl,
            "abstract_deadline": abstract_dl,
            "origin_url":        row["url"],
            "source":            "wikicfp",
        })
    return out


def parse_event_detail(html: str, url: str) -> dict:
    """Parse a WikiCFP event detail page into an enrichment dict."""
    soup = BeautifulSoup(html, "lxml")
    result: dict = {
        "event_id":   _extract_event_id(url),
        "origin_url": url,
        "source":     "wikicfp",
    }

    # Official URL: external link whose text IS the URL or labeled "Link:".
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http") and "wikicfp" not in href:
            text = a.get_text(strip=True)
            parent_text = (a.find_parent() or a).get_text() if a.find_parent() else ""
            if text.startswith("http") or "Link:" in parent_text:
                result["official_url"] = href
                break

    # When/Where/Submission Deadline/Notification/Camera-ready
    for td in soup.find_all("td"):
        text = td.get_text(separator="|||", strip=True)
        if "Submission Deadline" in text or "Notification Due" in text:
            parts = text.split("|||")
            kv: dict[str, str] = {}
            for i in range(0, len(parts) - 1, 2):
                kv[parts[i].strip()] = parts[i + 1].strip()
            result["when_raw"]          = kv.get("When", "")
            result["where_raw"]         = kv.get("Where", "")
            result["paper_deadline"]    = _safe_parse_date(kv.get("Submission Deadline"))
            result["notification"]      = _safe_parse_date(kv.get("Notification Due"))
            result["camera_ready"]      = _safe_parse_date(kv.get("Final Version Due"))
            cats_text = kv.get("Categories", "")
            result["raw_tags"] = [t.strip() for t in cats_text.split(",") if t.strip()]
            break

    cfp_div = soup.find("div", class_="cfp")
    if cfp_div:
        result["description"] = cfp_div.get_text(" ", strip=True)[:600]

    return result


def parse(url: str, html: str) -> Optional[dict]:
    """Dispatch entry. Search/listing pages return list under 'events' key;
    detail pages return a flat enrichment dict.
    """
    # WikiCFP detail pages use /cfp/servlet/event.showcfp?eventid=...
    if "event.showcfp" in url or "eventid=" in url:
        return parse_event_detail(html, url)
    if "/cfp/call" in url or "search" in url.lower():
        return {"events": parse_search_page(html), "source": "wikicfp"}
    # Default: try detail extraction.
    return parse_event_detail(html, url)
