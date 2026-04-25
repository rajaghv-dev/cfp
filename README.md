# WikiCFP Conference Scraper

Scrapes [WikiCFP](http://www.wikicfp.com) and collects upcoming conferences in:

| Category | Keywords searched |
|---|---|
| AI | artificial intelligence, AI |
| ML | machine learning, deep learning, neural network |
| DevOps | devops, site reliability, platform engineering |
| Linux | linux, open source, embedded linux |
| ChipDesign | VLSI, chip design, EDA, FPGA, semiconductor, SoC |
| Math | mathematics, mathematical, algebra, combinatorics, number theory |
| Legal | law, legal, jurisprudence, cyber law, intellectual property |

Results are saved as **JSON** and **CSV** under `data/`.

---

## Quick Start

```bash
git clone <repo-url>
cd wiki-cfp
bash setup.sh
source .venv/bin/activate
python3 scraper.py
```

## Usage

```
python3 scraper.py [--pages N]
```

| Flag | Default | Description |
|---|---|---|
| `--pages` | 3 | Max search-result pages per keyword |

## Output

| File | Description |
|---|---|
| `data/latest.json` | Most recent full run (JSON array) |
| `data/conferences_YYYYMMDD_HHMMSS.json` | Timestamped run archives |
| `data/conferences_YYYYMMDD_HHMMSS.csv` | Same data in CSV format |

Each entry contains:

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

## Requirements

- Python 3.10+
- See `requirements.txt` (`requests`, `beautifulsoup4`, `lxml`)

---

Last setup: 2026-04-25 15:58:00
