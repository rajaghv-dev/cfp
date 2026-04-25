# Codegen 01 — config.py + wcfp/models.py

## Files to Create
- `config.py` (repo root)
- `wcfp/__init__.py` (empty)
- `wcfp/models.py`

## Rule
These two files import NOTHING project-internal. Every other file imports them.

---

## config.py — implement exactly as shown

```python
from pathlib import Path
import os

ROOT         = Path(__file__).parent
DATA_DIR     = ROOT / "data"
REPORTS_DIR  = ROOT / "reports"
ONTOLOGY_DIR = ROOT / "ontology"

# PostgreSQL (primary store — all writes go here)
PG_DSN = os.getenv("PG_DSN", "postgresql://wcfp:wcfp@localhost:5432/wikicfp")

# Redis (queue + rate limiting ONLY — zero business data)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Ollama hosts — one Ollama daemon per machine
OLLAMA_HOSTS = {
    "rtx4090": os.getenv("OLLAMA_RTX4090", "http://10.0.0.10:11434"),
    "rtx3080": os.getenv("OLLAMA_RTX3080", "http://10.0.0.11:11434"),
    "dgx":     os.getenv("OLLAMA_DGX",     "http://10.0.0.12:11434"),
    "local":   os.getenv("OLLAMA_LOCAL",   "http://localhost:11434"),
}

# Model → host routing (must list every model used anywhere in the code)
MODEL_HOST = {
    "qwen3:4b":          "rtx3080",
    "qwen3:14b":         "rtx3080",
    "mistral-nemo:12b":  "rtx3080",
    "nomic-embed-text":  "rtx3080",
    "qwen3:32b":         "rtx4090",
    "deepseek-r1:32b":   "rtx4090",
    "deepseek-r1:70b":   "dgx",
}

# Tier thresholds — escalate if confidence < threshold
TIER_THRESHOLD = {1: 0.85, 2: 0.85, 3: 0.80}

# Route to mistral-nemo:12b when cleaned HTML token count exceeds this
LONG_CONTEXT_TOKENS = 32_000   # measured with tiktoken cl100k_base

# Graph
AGE_GRAPH = "wcfp_graph"

# Embeddings
EMBED_DIM = 768          # nomic-embed-text output dimension
DEDUP_COSINE = 0.92      # Qdrant cosine score → candidate pair if >= this
DEDUP_TOP_K  = 5

# HTTP politeness
USER_AGENT            = "cfp-scraper/1.0 (+contact@example.com)"
HUMAN_DELAY_MEAN      = 8.0
HUMAN_DELAY_STD       = 2.5
HUMAN_DELAY_MIN       = 5.0
HUMAN_DELAY_MAX       = 15.0
HUMAN_DELAY_LONG_PROB = 0.10  # 10% chance of 15–45 s "reading" pause

# Retry
MAX_RETRIES        = 5
RETRY_BACKOFF_BASE = 2.0      # delay = BASE**attempt + jitter(0..1)
RETRY_BACKOFF_CAP  = 600      # cap at 10 min
DEAD_LETTER_KEY    = "wcfp:dead"

# Cloud sync
WCFP_MACHINE  = os.getenv("WCFP_MACHINE", "rtx3080")  # rtx3080 | rtx4090 | dgx | local
WCFP_STORAGE  = os.getenv("WCFP_STORAGE", "b2")       # b2 | gcs | s3 | minio
RCLONE_REMOTE = os.getenv("RCLONE_REMOTE", f"{WCFP_STORAGE}:cfp-state")

# Data files
PROMPTS_FILE  = ROOT / "prompts.md"
SEED_JSON     = DATA_DIR / "latest.json"   # 350-conf seed; imported once into PG
```

---

## wcfp/models.py — implement exactly as shown

### Enums

```python
from enum import Enum

class Category(str, Enum):
    AI               = "AI"
    ML               = "ML"
    DEVOPS           = "DevOps"
    LINUX            = "Linux"
    CHIP_DESIGN      = "ChipDesign"
    MATH             = "Math"
    LEGAL            = "Legal"
    COMPUTER_SCIENCE = "ComputerScience"
    SECURITY         = "Security"
    DATA             = "Data"
    NETWORKING       = "Networking"
    ROBOTICS         = "Robotics"
    BIOINFORMATICS   = "Bioinformatics"

class Tier(int, Enum):
    T1 = 1; T2 = 2; T3 = 3; T4 = 4

class JobStatus(str, Enum):
    PENDING = "pending"; IN_FLIGHT = "in_flight"; DONE = "done"
    FAILED  = "failed";  DEAD      = "dead"

class PersonRole(str, Enum):
    GENERAL_CHAIR = "general_chair"
    PC_CHAIR      = "pc_chair"
    AREA_CHAIR    = "area_chair"
    KEYNOTE       = "keynote"

class OrgType(str, Enum):
    PUBLISHER    = "publisher"
    UNIVERSITY   = "university"
    COMPANY      = "company"
    RESEARCH_LAB = "research_lab"
    GOVERNMENT   = "government"
```

### Event dataclass — merge of both repos

All fields are `Optional` except `event_id`, `acronym`, `name`.
Include `slug`, `days_to_deadline`, `next_deadline` as computed `@property`.
Include `to_markdown()` method.

```python
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

@dataclass(slots=True)
class Event:
    # Primary key (WikiCFP eventid=N; use -1 for non-WikiCFP sources)
    event_id:     int
    acronym:      str
    name:         str

    # Series / edition
    series_id:    Optional[int]  = None
    edition_year: Optional[int]  = None

    # Classification
    categories:   list[Category] = field(default_factory=list)
    is_virtual:   bool           = False
    raw_tags:     list[str]      = field(default_factory=list)  # WikiCFP "Categories:" tags

    # Dates (all as datetime.date)
    when_raw:        Optional[str]  = None   # original "Aug 26, 2026 - Aug 28, 2026"
    start_date:      Optional[date] = None
    end_date:        Optional[date] = None
    abstract_deadline: Optional[date] = None
    deadline:        Optional[date] = None   # paper deadline
    notification:    Optional[date] = None
    camera_ready:    Optional[date] = None

    # Location
    where_raw:    Optional[str]  = None
    country:      Optional[str]  = None   # ISO-3166 alpha-2 e.g. "IN","US","DE"
    region:       Optional[str]  = None   # "Asia"|"Europe"|"NorthAmerica"|etc.
    india_state:  Optional[str]  = None   # only when country == "IN"
    venue_id:     Optional[int]  = None

    # Content
    description:  Optional[str]  = None
    notes:        str            = ""     # user-editable; preserved across re-syncs
    rank:         Optional[str]  = None   # CORE ranking: A*, A, B, C

    # URLs
    wikicfp_url:  Optional[str]  = None
    official_url: Optional[str]  = None
    source:       str            = "wikicfp"  # wikicfp|ai_deadlines|conferencelist|manual

    # Metadata
    scraped_at:   datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_checked: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def slug(self) -> str:
        """Deterministic ACRONYM-YEAR slug. Handles year already in acronym."""
        short = self.acronym.upper()
        year  = str(self.edition_year or
                    (self.start_date.year if self.start_date else
                     (self.deadline.year  if self.deadline  else "")))
        slug_base = re.sub(r"\s*" + re.escape(year) + r"\s*$", "", short).strip()
        return re.sub(r"[^A-Z0-9]+", "-", f"{slug_base}-{year}").strip("-")

    @property
    def next_deadline(self) -> Optional[date]:
        today = date.today()
        candidates = [d for d in [self.abstract_deadline, self.deadline]
                      if d and d >= today]
        return min(candidates) if candidates else None

    @property
    def days_to_deadline(self) -> Optional[int]:
        nd = self.next_deadline
        return (nd - date.today()).days if nd else None

    def to_markdown(self) -> str:
        def fmt(d: Optional[date]) -> str:
            return d.strftime("%Y-%m-%d") if d else "TBD"
        lines = [
            f"# {self.name} ({self.acronym})", "",
            f"**Category:** {', '.join(c.value for c in self.categories)}  ",
            f"**Year:** {self.edition_year or 'TBD'}  ",
            f"**Rank:** {self.rank or 'N/A'}  ",
            f"**Source:** {self.source}  ",
            f"**URL:** {self.official_url or self.wikicfp_url or ''}  ", "",
            "## Description", "",
            self.description or "_No description available._", "",
            "## Deadlines", "",
            "| Milestone | Date |", "|-----------|------|",
            f"| Abstract deadline | {fmt(self.abstract_deadline)} |",
            f"| Paper deadline    | {fmt(self.deadline)} |",
            f"| Notification      | {fmt(self.notification)} |",
            f"| Camera ready      | {fmt(self.camera_ready)} |", "",
            "## Event", "",
            "| Field | Value |", "|-------|-------|",
            f"| Start    | {fmt(self.start_date)} |",
            f"| End      | {fmt(self.end_date)} |",
            f"| Location | {self.where_raw or 'TBD'} |", "",
        ]
        if self.raw_tags:
            lines += ["## Tags", "", ", ".join(f"`{t}`" for t in self.raw_tags), ""]
        if self.notes:
            lines += ["## Notes", "", self.notes, ""]
        return "\n".join(lines)
```

### Remaining dataclasses — implement with @dataclass(slots=True)

```
Person:       person_id(int), full_name(str), email, dblp_url, homepage
Organisation: org_id(int), name(str), type(OrgType), country, website
Venue:        venue_id(int), name, city, state, country, latitude, longitude
Series:       series_id(int), acronym(str), full_name, wikicfp_url, org_id
ScrapeJob:    url(str), domain(str), priority(int), source_event_id,
              attempts(int=0), status(JobStatus=PENDING), added_at, last_error
TierResult:   tier(Tier), model(str), confidence(float), output(dict),
              escalate(bool=False), escalate_reason(Optional[str]), elapsed_ms(int=0)
EscalationPayload: record(dict), tier_results(list[TierResult]),
                   escalate_reason(str), raw_html(Optional[str])
OntologyEdge: subject(str), predicate(str), object(str), confidence(float),
              source_event_id(Optional[int])
```

### escalate_reason values (for TierResult and EscalationPayload)
```
"low_confidence" | "multi_category" | "unknown_site" |
"long_context"   | "dedup_ambiguous"| "ontology_edge"
```
