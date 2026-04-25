# WikiCFP Conference Scraper

Scrapes [WikiCFP](http://www.wikicfp.com) and collects conferences across categories and regions.
After each scrape, Markdown reports are auto-generated in `reports/`.

---

## Categories

| Category | Keywords searched |
|---|---|
| AI | artificial intelligence, AI |
| ML | machine learning, deep learning, neural network |
| DevOps | devops, site reliability, platform engineering |
| Linux | linux, open source, embedded linux |
| ChipDesign | VLSI, chip design, EDA, FPGA, semiconductor, SoC |
| Math | mathematics, mathematical, algebra, combinatorics, number theory |
| Legal | law, legal, jurisprudence, cyber law, intellectual property |

---

## Quick Start

```bash
git clone <repo-url>
cd wiki-cfp
bash setup.sh
source .venv/bin/activate
python3 scraper.py
```

---

## Usage

```
python3 scraper.py [--pages N] [--md-only]
```

| Flag | Default | Description |
|---|---|---|
| `--pages N` | `3` | Max search-result pages per keyword |
| `--md-only` | — | Skip scraping; regenerate reports from existing `data/latest.json` |

Regenerate reports without re-scraping:

```bash
python3 scraper.py --md-only
# or directly:
python3 generate_md.py
```

---

## Generated Reports (`reports/`)

### By Category

| File | Contents |
|---|---|
| `reports/ai.md` | AI — Artificial Intelligence |
| `reports/ml.md` | ML — Machine Learning & Deep Learning |
| `reports/devops.md` | DevOps & Site Reliability Engineering |
| `reports/linux.md` | Linux & Open Source |
| `reports/chipdesign.md` | Chip Design (VLSI / EDA / FPGA) |
| `reports/math.md` | Mathematics |
| `reports/legal.md` | Legal & Cyber Law |

### By Date

| File | Contents |
|---|---|
| `reports/by_date.md` | All conferences sorted by start date |

### By Region

| File | Contents |
|---|---|
| `reports/usa.md` | USA conferences |
| `reports/europe.md` | European conferences (incl. UK & Switzerland) |
| `reports/uk.md` | UK conferences |
| `reports/singapore.md` | Singapore conferences |
| `reports/switzerland.md` | Switzerland conferences |
| `reports/india.md` | India conferences, **organised state-wise** |

Each report has two sections — **Upcoming** (sorted earliest first) and **Past** (sorted most-recent first).
Reports are regenerated on every scrape run, so past conferences are automatically moved to the Past section.

---

## Data Files (`data/`)

| File | Description |
|---|---|
| `data/latest.json` | Most recent run (always overwritten) |
| `data/conferences_YYYYMMDD_HHMMSS.json` | Timestamped run archive |
| `data/conferences_YYYYMMDD_HHMMSS.csv` | Same data in CSV format |

Each entry:

```json
{
  "acronym": "NeurIPS 2026",
  "name": "Neural Information Processing Systems",
  "category": "ML",
  "keywords": ["machine learning", "deep learning"],
  "when": "Dec 6, 2026 - Dec 12, 2026",
  "where": "Vancouver, Canada",
  "deadline": "May 15, 2026",
  "url": "http://www.wikicfp.com/cfp/..."
}
```

---

## Requirements

- Python 3.10+
- `requests`, `beautifulsoup4`, `lxml` (installed by `setup.sh`)

---

Last setup: —
