#!/usr/bin/env python3
"""Generate Markdown reports from data/latest.json.

Writes to reports/:
  ai.md, ml.md, devops.md, linux.md, chipdesign.md, math.md, legal.md
  by_date.md
  usa.md, europe.md, uk.md, singapore.md, switzerland.md, india.md
"""

import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

TODAY = datetime.today().date()
DATA_FILE = Path("data/latest.json")
REPORTS_DIR = Path("reports")

# ── Location keyword lists ───────────────────────────────────────────────────

_ONLINE = ["online", "virtual", "remote", "zoom", "webinar", "web-based", "everywhere"]

_UK = [
    "united kingdom", ", uk", " uk,", "(uk)", "uk.",
    "england", "scotland", "wales",
    "london", "manchester", "oxford", "cambridge", "edinburgh", "glasgow",
    "birmingham", "bristol", "leeds", "sheffield", "liverpool", "newcastle",
    "nottingham", "southampton", "exeter", "bath",
]

_CH = [
    "switzerland", "swiss",
    "zurich", "zürich", "zuerich", "geneva", "genève", "geneve",
    "basel", "bern", "lausanne", "lugano", "davos", "lucerne", "luzern",
    "montreux", "winterthur",
]

_EU_EXCL = [   # European countries/cities excluding UK and CH
    "france", "germany", "spain", "italy", "netherlands", "belgium",
    "sweden", "norway", "denmark", "finland", "austria", "poland",
    "czech", "portugal", "greece", "hungary", "ireland", "luxembourg",
    "malta", "croatia", "romania", "bulgaria", "slovakia", "slovenia",
    "estonia", "latvia", "lithuania", "cyprus",
    "paris", "berlin", "madrid", "rome", "amsterdam", "brussels",
    "stockholm", "vienna", "oslo", "copenhagen", "helsinki", "lisbon",
    "athens", "dublin", "warsaw", "prague", "budapest", "bucharest",
    "europe", "european",
]

_USA = [
    "united states", ", usa", " usa,", "u.s.a", "(usa)",
    "new york", "san francisco", "los angeles", "chicago", "boston",
    "seattle", "austin", "denver", "atlanta", "houston", "dallas",
    "washington dc", "washington d.c", "san diego", "las vegas",
    "philadelphia", "orlando", "miami", "portland", "minneapolis",
    "pittsburgh", "salt lake city", "nashville", "phoenix",
    "san jose", "santa clara", "palo alto", "menlo park",
    ", ca,", ", ca.", ", ny,", ", ny.", ", tx,", ", wa,", ", ma,",
    ", il,", ", ga,", ", co,", ", fl,", ", va,", ", pa,", ", oh,",
    ", nc,", ", az,", ", or,",
]

_SG = ["singapore"]

_INDIA_RE = re.compile(r"\bindia\b", re.IGNORECASE)

# ── India city → state map ───────────────────────────────────────────────────

CITY_STATE: dict[str, str] = {
    # Maharashtra
    "mumbai": "Maharashtra", "pune": "Maharashtra", "nagpur": "Maharashtra",
    "nashik": "Maharashtra", "aurangabad": "Maharashtra", "kolhapur": "Maharashtra",
    "solapur": "Maharashtra", "navi mumbai": "Maharashtra",
    # Karnataka
    "bangalore": "Karnataka", "bengaluru": "Karnataka", "mysore": "Karnataka",
    "mysuru": "Karnataka", "mangalore": "Karnataka", "hubli": "Karnataka",
    "dharwad": "Karnataka", "belagavi": "Karnataka", "belgaum": "Karnataka",
    # Tamil Nadu
    "chennai": "Tamil Nadu", "coimbatore": "Tamil Nadu", "madurai": "Tamil Nadu",
    "trichy": "Tamil Nadu", "tiruchirappalli": "Tamil Nadu", "vellore": "Tamil Nadu",
    "salem": "Tamil Nadu", "tirunelveli": "Tamil Nadu", "thanjavur": "Tamil Nadu",
    # Delhi
    "delhi": "Delhi", "new delhi": "Delhi",
    # Telangana
    "hyderabad": "Telangana", "secunderabad": "Telangana", "warangal": "Telangana",
    "karimnagar": "Telangana",
    # West Bengal
    "kolkata": "West Bengal", "calcutta": "West Bengal", "siliguri": "West Bengal",
    "durgapur": "West Bengal",
    # Gujarat
    "ahmedabad": "Gujarat", "surat": "Gujarat", "vadodara": "Gujarat",
    "baroda": "Gujarat", "rajkot": "Gujarat", "gandhinagar": "Gujarat",
    # Rajasthan
    "jaipur": "Rajasthan", "jodhpur": "Rajasthan", "udaipur": "Rajasthan",
    "ajmer": "Rajasthan", "kota": "Rajasthan", "bikaner": "Rajasthan",
    # Madhya Pradesh
    "bhopal": "Madhya Pradesh", "indore": "Madhya Pradesh", "gwalior": "Madhya Pradesh",
    "jabalpur": "Madhya Pradesh",
    # Uttar Pradesh
    "lucknow": "Uttar Pradesh", "kanpur": "Uttar Pradesh", "varanasi": "Uttar Pradesh",
    "agra": "Uttar Pradesh", "allahabad": "Uttar Pradesh", "prayagraj": "Uttar Pradesh",
    "noida": "Uttar Pradesh", "ghaziabad": "Uttar Pradesh", "meerut": "Uttar Pradesh",
    "mathura": "Uttar Pradesh",
    # Bihar
    "patna": "Bihar", "gaya": "Bihar", "muzaffarpur": "Bihar",
    # Jharkhand
    "ranchi": "Jharkhand", "jamshedpur": "Jharkhand", "dhanbad": "Jharkhand",
    # Odisha
    "bhubaneswar": "Odisha", "bhubaneshwar": "Odisha", "cuttack": "Odisha",
    # Kerala
    "thiruvananthapuram": "Kerala", "trivandrum": "Kerala",
    "kochi": "Kerala", "cochin": "Kerala", "kozhikode": "Kerala",
    "calicut": "Kerala", "thrissur": "Kerala",
    # Chandigarh (UT)
    "chandigarh": "Chandigarh",
    # Punjab
    "amritsar": "Punjab", "ludhiana": "Punjab", "jalandhar": "Punjab",
    # Haryana
    "gurgaon": "Haryana", "gurugram": "Haryana", "faridabad": "Haryana",
    "panipat": "Haryana",
    # Uttarakhand
    "dehradun": "Uttarakhand", "roorkee": "Uttarakhand", "haridwar": "Uttarakhand",
    # Himachal Pradesh
    "shimla": "Himachal Pradesh", "manali": "Himachal Pradesh",
    # J&K
    "srinagar": "Jammu & Kashmir", "jammu": "Jammu & Kashmir",
    # North-East
    "guwahati": "Assam", "dibrugarh": "Assam",
    "imphal": "Manipur", "shillong": "Meghalaya",
    "agartala": "Tripura", "kohima": "Nagaland",
    # Andhra Pradesh
    "visakhapatnam": "Andhra Pradesh", "vizag": "Andhra Pradesh",
    "vijayawada": "Andhra Pradesh", "tirupati": "Andhra Pradesh",
    "guntur": "Andhra Pradesh",
    # Chhattisgarh
    "raipur": "Chhattisgarh", "bilaspur": "Chhattisgarh",
    # Goa
    "goa": "Goa", "panaji": "Goa",
}

# ── Date helpers ─────────────────────────────────────────────────────────────

def parse_start_date(when: str) -> date | None:
    """Extract the start date from a WikiCFP 'when' string."""
    if not when or when.strip() in ("N/A", "TBD", ""):
        return None
    m = re.search(r"([A-Za-z]+ +\d{1,2},? +\d{4})", when)
    if m:
        try:
            return datetime.strptime(m.group(1).replace(",", ""), "%b %d %Y").date()
        except ValueError:
            pass
    return None


def is_past(when: str) -> bool:
    d = parse_start_date(when)
    return d is not None and d < TODAY


# ── Location detection ───────────────────────────────────────────────────────

def _has(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in keywords)


def location_tags(where: str) -> set[str]:
    """Return a set of location tags for a given 'where' string."""
    if not where:
        return {"Other"}
    if _has(where, _ONLINE):
        return {"Online"}
    tags: set[str] = set()
    if _has(where, _SG):
        tags.add("Singapore")
    if _INDIA_RE.search(where):
        tags.add("India")
    if _has(where, _CH):
        tags.add("Switzerland")
        tags.add("Europe")
    if _has(where, _UK):
        tags.add("UK")
        tags.add("Europe")
    if _has(where, _EU_EXCL):
        tags.add("Europe")
    if _has(where, _USA):
        tags.add("USA")
    return tags or {"Other"}


def india_state(where: str) -> str:
    w = where.lower()
    # Check multi-word cities first
    for city, state in sorted(CITY_STATE.items(), key=lambda x: -len(x[0])):
        if city in w:
            return state
    return "Other / Location Unspecified"


# ── Markdown table helpers ───────────────────────────────────────────────────

def _header(with_cat: bool = False) -> str:
    if with_cat:
        return (
            "| Acronym | Category | Conference | Date | Location | Deadline | Link |\n"
            "|:--------|:---------|:-----------|:-----|:---------|:---------|:----:|"
        )
    return (
        "| Acronym | Conference | Date | Location | Deadline | Link |\n"
        "|:--------|:-----------|:-----|:---------|:---------|:----:|"
    )


def _row(c: dict, with_cat: bool = False) -> str:
    acr   = c.get("acronym", "—")
    nm    = (c.get("name", "") or "")[:70]
    cat   = c.get("category", "")
    when  = c.get("when", "N/A") or "N/A"
    where = c.get("where", "N/A") or "N/A"
    dl    = c.get("deadline", "N/A") or "N/A"
    url   = c.get("url", "")
    lnk   = f"[↗]({url})" if url else "—"
    if with_cat:
        return f"| {acr} | {cat} | {nm} | {when} | {where} | {dl} | {lnk} |"
    return f"| {acr} | {nm} | {when} | {where} | {dl} | {lnk} |"


def _sort_up(confs: list[dict]) -> list[dict]:
    return sorted(confs, key=lambda c: parse_start_date(c.get("when", "")) or date.max)


def _sort_past(confs: list[dict]) -> list[dict]:
    return sorted(confs, key=lambda c: parse_start_date(c.get("when", "")) or date.min, reverse=True)


def _table_section(heading: str, confs: list[dict], with_cat: bool = False) -> list[str]:
    up = [c for c in confs if not is_past(c.get("when", ""))]
    ps = [c for c in confs if is_past(c.get("when", ""))]
    out: list[str] = []

    out += [f"## {heading} — Upcoming ({len(up)})", ""]
    if up:
        out.append(_header(with_cat))
        out.extend(_row(c, with_cat) for c in _sort_up(up))
    else:
        out.append("_No upcoming conferences._")

    out += ["", f"## {heading} — Past ({len(ps)})", ""]
    if ps:
        out.append(_header(with_cat))
        out.extend(_row(c, with_cat) for c in _sort_past(ps))
    else:
        out.append("_No past conferences._")

    return out


# ── Report writers ───────────────────────────────────────────────────────────

def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _meta(confs: list[dict]) -> str:
    up = sum(1 for c in confs if not is_past(c.get("when", "")))
    return f"> Last updated: **{TODAY}**  |  Upcoming: **{up}**  |  Past: **{len(confs) - up}**  |  Total: **{len(confs)}**"


def write_category_md(path: Path, title: str, confs: list[dict]) -> None:
    up = [c for c in confs if not is_past(c.get("when", ""))]
    ps = [c for c in confs if is_past(c.get("when", ""))]

    lines = [
        f"# {title}",
        "",
        _meta(confs),
        "",
        f"## Upcoming ({len(up)})",
        "",
    ]
    if up:
        lines.append(_header())
        lines.extend(_row(c) for c in _sort_up(up))
    else:
        lines.append("_No upcoming conferences._")

    lines += ["", f"## Past ({len(ps)})", ""]
    if ps:
        lines.append(_header())
        lines.extend(_row(c) for c in _sort_past(ps))
    else:
        lines.append("_No past conferences._")

    _write(path, lines)
    print(f"  {path}  (↑ {len(up)}  ↓ {len(ps)})")


def write_by_date_md(path: Path, confs: list[dict]) -> None:
    up = [c for c in confs if not is_past(c.get("when", ""))]
    ps = [c for c in confs if is_past(c.get("when", ""))]

    lines = [
        "# All Conferences — Sorted by Date",
        "",
        _meta(confs),
        "",
        f"## Upcoming ({len(up)})",
        "",
    ]
    if up:
        lines.append(_header(with_cat=True))
        lines.extend(_row(c, with_cat=True) for c in _sort_up(up))
    else:
        lines.append("_No upcoming conferences._")

    lines += ["", f"## Past ({len(ps)})", ""]
    if ps:
        lines.append(_header(with_cat=True))
        lines.extend(_row(c, with_cat=True) for c in _sort_past(ps))
    else:
        lines.append("_No past conferences._")

    _write(path, lines)
    print(f"  {path}  (↑ {len(up)}  ↓ {len(ps)})")


def write_region_md(path: Path, title: str, confs: list[dict]) -> None:
    up = [c for c in confs if not is_past(c.get("when", ""))]
    ps = [c for c in confs if is_past(c.get("when", ""))]

    lines = [
        f"# {title}",
        "",
        _meta(confs),
        "",
        f"## Upcoming ({len(up)})",
        "",
    ]
    if up:
        lines.append(_header(with_cat=True))
        lines.extend(_row(c, with_cat=True) for c in _sort_up(up))
    else:
        lines.append("_No upcoming conferences found._")

    lines += ["", f"## Past ({len(ps)})", ""]
    if ps:
        lines.append(_header(with_cat=True))
        lines.extend(_row(c, with_cat=True) for c in _sort_past(ps))
    else:
        lines.append("_No past conferences._")

    _write(path, lines)
    print(f"  {path}  (↑ {len(up)}  ↓ {len(ps)})")


def write_india_md(path: Path, confs: list[dict]) -> None:
    up = [c for c in confs if not is_past(c.get("when", ""))]
    ps = [c for c in confs if is_past(c.get("when", ""))]

    def statewise_block(section_confs: list[dict], reverse: bool) -> list[str]:
        if not section_confs:
            return ["_None._"]
        by_state: dict[str, list[dict]] = defaultdict(list)
        for c in section_confs:
            by_state[india_state(c.get("where", ""))].append(c)
        out: list[str] = []
        sorter = _sort_past if reverse else _sort_up
        for state in sorted(by_state):
            out += [f"### {state}", "", _header(with_cat=True)]
            out.extend(_row(c, with_cat=True) for c in sorter(by_state[state]))
            out.append("")
        return out

    lines = [
        "# India Conferences — State-wise",
        "",
        _meta(confs),
        "",
        f"## Upcoming ({len(up)})",
        "",
        *statewise_block(up, reverse=False),
        f"## Past ({len(ps)})",
        "",
        *statewise_block(ps, reverse=True),
    ]
    _write(path, lines)
    print(f"  {path}  (↑ {len(up)}  ↓ {len(ps)})")


# ── Entry point ──────────────────────────────────────────────────────────────

CAT_TITLES: dict[str, str] = {
    "AI":         "AI — Artificial Intelligence",
    "ML":         "ML — Machine Learning & Deep Learning",
    "DevOps":     "DevOps & Site Reliability Engineering",
    "Linux":      "Linux & Open Source",
    "ChipDesign": "Chip Design — VLSI / EDA / FPGA / Semiconductor",
    "Math":       "Mathematics",
    "Legal":      "Legal, Cyber Law & Intellectual Property",
}


def generate_all(data_file: Path = DATA_FILE, reports_dir: Path = REPORTS_DIR) -> None:
    if not data_file.exists():
        print(f"Error: {data_file} not found. Run scraper.py first.")
        return

    all_confs: list[dict] = json.loads(data_file.read_text(encoding="utf-8"))
    print(f"Loaded {len(all_confs)} conferences from {data_file}")
    print(f"Generating Markdown reports in {reports_dir}/\n")

    reports_dir.mkdir(parents=True, exist_ok=True)

    # Category files
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for c in all_confs:
        for cat in c.get("category", "Other").split(", "):
            by_cat[cat.strip()].append(c)

    print("Category reports:")
    for cat in sorted(by_cat):
        title = CAT_TITLES.get(cat, cat)
        write_category_md(reports_dir / f"{cat.lower()}.md", title, by_cat[cat])

    # By date
    print("\nDate report:")
    write_by_date_md(reports_dir / "by_date.md", all_confs)

    # Regional
    print("\nRegional reports:")
    regional = [
        ("usa.md",         "USA Conferences",         "USA"),
        ("europe.md",      "Europe Conferences",      "Europe"),
        ("uk.md",          "UK Conferences",          "UK"),
        ("singapore.md",   "Singapore Conferences",   "Singapore"),
        ("switzerland.md", "Switzerland Conferences", "Switzerland"),
    ]
    for fname, title, tag in regional:
        tagged = [c for c in all_confs if tag in location_tags(c.get("where", ""))]
        write_region_md(reports_dir / fname, title, tagged)

    india_confs = [c for c in all_confs if "India" in location_tags(c.get("where", ""))]
    write_india_md(reports_dir / "india.md", india_confs)

    print(f"\nDone. All reports in {reports_dir}/")


if __name__ == "__main__":
    generate_all()
