#!/usr/bin/env python3
"""WikiCFP scraper for AI, ML, DevOps, Linux, chip design, math, and legal conferences."""

import csv
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "http://www.wikicfp.com"
SEARCH_URL = f"{BASE_URL}/cfp/call"

CATEGORIES = {
    "AI": ["artificial intelligence", "AI"],
    "ML": ["machine learning", "deep learning", "neural network"],
    "DevOps": ["devops", "site reliability", "platform engineering"],
    "Linux": ["linux", "open source", "embedded linux"],
    "ChipDesign": ["VLSI", "chip design", "EDA", "FPGA", "semiconductor", "SoC"],
    "Math": ["mathematics", "mathematical", "algebra", "combinatorics", "number theory"],
    "Legal": ["law", "legal", "jurisprudence", "cyber law", "intellectual property"],
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


@dataclass
class Conference:
    acronym: str
    name: str
    category: str
    keywords: list[str] = field(default_factory=list)
    when: str = ""
    where: str = ""
    deadline: str = ""
    url: str = ""


def fetch(url: str, params: dict = None, retries: int = 3) -> BeautifulSoup | None:
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")
        except requests.RequestException as e:
            print(f"  [warn] {url} attempt {attempt+1}: {e}")
            time.sleep(2**attempt)
    return None


def find_data_table(soup: BeautifulSoup):
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
        # Skip header or spacer rows
        if not cells_a or cells_a[0].get_text(strip=True).lower() == "event":
            i += 1
            continue
        # Row A: acronym link | full name | (empty)
        link = cells_a[0].find("a")
        if not link:
            i += 1
            continue
        acronym = link.get_text(strip=True)
        href = link.get("href", "")
        full_name = cells_a[1].get_text(strip=True) if len(cells_a) > 1 else ""
        url = BASE_URL + href if href.startswith("/") else href

        # Row B: when | where | deadline
        i += 1
        when = where = deadline = ""
        if i < len(rows):
            cells_b = rows[i].find_all(["td", "th"])
            if cells_b and not cells_b[0].find("a"):  # confirm it's the detail row
                when = cells_b[0].get_text(strip=True) if len(cells_b) > 0 else ""
                where = cells_b[1].get_text(strip=True) if len(cells_b) > 1 else ""
                deadline = cells_b[2].get_text(strip=True) if len(cells_b) > 2 else ""
                i += 1

        results.append(
            {"acronym": acronym, "name": full_name, "when": when, "where": where, "deadline": deadline, "url": url}
        )
    return results


def scrape_keyword(keyword: str, category: str, max_pages: int = 3) -> list[Conference]:
    results = []
    seen_urls: set[str] = set()

    for page in range(1, max_pages + 1):
        print(f"  Fetching '{keyword}' page {page}...")
        soup = fetch(SEARCH_URL, params={"conference": keyword, "page": page})
        if soup is None:
            break

        table = find_data_table(soup)
        if table is None:
            break

        rows_data = parse_table(table)
        if not rows_data:
            break

        for d in rows_data:
            if d["url"] in seen_urls:
                continue
            seen_urls.add(d["url"])
            results.append(
                Conference(
                    acronym=d["acronym"],
                    name=d["name"],
                    category=category,
                    keywords=[keyword],
                    when=d["when"],
                    where=d["where"],
                    deadline=d["deadline"],
                    url=d["url"],
                )
            )

        time.sleep(1.0)
    return results


def deduplicate(confs: list[Conference]) -> list[Conference]:
    seen: dict[str, Conference] = {}
    for c in confs:
        key = c.url or f"{c.acronym}|{c.when}"
        if key in seen:
            seen[key].keywords = list(set(seen[key].keywords + c.keywords))
            if c.category not in seen[key].category:
                seen[key].category += f", {c.category}"
        else:
            seen[key] = c
    return list(seen.values())


def save(confs: list[Conference], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    json_path = out_dir / f"conferences_{timestamp}.json"
    json_path.write_text(json.dumps([asdict(c) for c in confs], indent=2))
    print(f"Saved JSON : {json_path}")

    csv_path = out_dir / f"conferences_{timestamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["acronym", "name", "category", "keywords", "when", "where", "deadline", "url"],
        )
        writer.writeheader()
        for c in confs:
            row = asdict(c)
            row["keywords"] = "; ".join(row["keywords"])
            writer.writerow(row)
    print(f"Saved CSV  : {csv_path}")

    latest = out_dir / "latest.json"
    latest.write_text(json_path.read_text())
    print(f"Updated    : {latest}")


def print_summary(confs: list[Conference]) -> None:
    from collections import Counter
    counts = Counter(c.category for c in confs)
    print("\nSummary by category:")
    for cat, n in sorted(counts.items()):
        print(f"  {cat:<20} {n}")
    print(f"  {'TOTAL':<20} {len(confs)}")


def main(max_pages: int = 3) -> None:
    all_confs: list[Conference] = []

    for category, keywords in CATEGORIES.items():
        print(f"\n[{category}]")
        for keyword in keywords:
            confs = scrape_keyword(keyword, category, max_pages=max_pages)
            print(f"  '{keyword}': {len(confs)} entries")
            all_confs.extend(confs)

    all_confs = deduplicate(all_confs)
    print_summary(all_confs)
    save(all_confs, Path("data"))
    print("\nDone.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape WikiCFP conferences.")
    parser.add_argument("--pages", type=int, default=3, help="Max pages per keyword (default: 3)")
    args = parser.parse_args()
    main(max_pages=args.pages)
