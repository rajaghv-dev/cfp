# Codegen 01 — config.py + cfp/models.py

## Files to Create
- `config.py` (repo root)
- `cfp/__init__.py` (empty)
- `cfp/models.py`

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
PG_DSN = os.getenv("PG_DSN", "postgresql://cfp:cfp@localhost:5432/cfp")

# Redis (queue + rate limiting ONLY — zero business data)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Ollama — single local daemon per machine (no per-model host routing).
# Models that the current CFP_MACHINE profile does not include are skipped
# and their jobs land in cfp:escalate:tier4 for the next capable machine.
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# Machine profile — controls which models are pulled and which tiers run locally.
# See context.md §2 for the full hardware matrix.
CFP_MACHINE = os.getenv("CFP_MACHINE", "gpu_mid")
# valid: dgx | gpu_large | gpu_mid | gpu_small | cpu_only

# Per-profile model roster. The pipeline pulls and uses ONLY these models on
# the current machine. Tier checks call get_available_models() (see spec 09)
# and skip tiers whose model is absent. Quantisation is pinned per profile to
# avoid Ollama silently picking Q4 on small VRAM (see arch.md §1 Q14).
PROFILE_MODELS: dict[str, list[str]] = {
    "dgx":       ["qwen3:4b-q8_0",   "qwen3:14b-q8_0",   "qwen3:32b-q8_0",
                  "deepseek-r1:32b",  "deepseek-r1:70b",  "nomic-embed-text"],
    "gpu_large": ["qwen3:4b-q4_K_M", "qwen3:14b-q4_K_M", "qwen3:32b-q4_K_M",
                  "deepseek-r1:32b",  "nomic-embed-text"],
    "gpu_mid":   ["qwen3:4b-q4_K_M", "qwen3:14b-q4_K_M", "nomic-embed-text"],
    "gpu_small": ["qwen3:4b-q4_K_M", "nomic-embed-text"],
    "cpu_only":  ["qwen3:4b-q4_K_M", "nomic-embed-text"],
}

# Tier thresholds — escalate if confidence < threshold
TIER_THRESHOLD = {1: 0.85, 2: 0.85, 3: 0.80}

# Route to mistral-nemo:12b when cleaned HTML token count exceeds this
LONG_CONTEXT_TOKENS = 32_000   # measured with tiktoken cl100k_base

# Graph (Apache AGE — v2; v1 uses relational tables only — see arch.md §6)
AGE_GRAPH = "cfp_graph"

# Embeddings
EMBED_DIM         = 768          # nomic-embed-text output dimension
DEDUP_COSINE      = 0.92         # pgvector cosine ≥ → candidate dedup pair
DEDUP_AUTO_MERGE  = 0.97         # pgvector cosine ≥ → skip write (idempotent)
DEDUP_TOP_K       = 5

# HTTP politeness
USER_AGENT            = os.getenv("USER_AGENT",
                                  "cfp-scraper/1.0 (+contact@example.com)")
HUMAN_DELAY_MEAN      = 8.0
HUMAN_DELAY_STD       = 2.5
HUMAN_DELAY_MIN       = 5.0
HUMAN_DELAY_MAX       = 15.0
HUMAN_DELAY_LONG_PROB = 0.10  # 10% chance of 15–45 s "reading" pause

# Retry
MAX_RETRIES        = 5
RETRY_BACKOFF_BASE = 2.0      # delay = BASE**attempt + jitter(0..1)
RETRY_BACKOFF_CAP  = 600      # cap at 10 min
DEAD_LETTER_KEY    = "cfp:dead"

# LLM JSON failure recovery (arch.md §1 Q12 — RESOLVED 2026-04-29)
# Strategy: local repair → 1 same-tier retry → escalate one tier
JSON_REPAIR_ENABLED   = True   # attempt json5/regex repair before retry
JSON_RETRY_SAME_TIER  = 1      # retries at same tier with reminder preamble
PARSE_FAIL_THRESHOLD  = 0.01   # flag model for review if parse-fail rate > 1%

# GCS / rclone settings — off-machine persistence (see context.md §18)
GCS_BUCKET    = os.getenv("GCS_BUCKET", "cfp-data")
GCS_PREFIX    = os.getenv("GCS_PREFIX", "prod")
RCLONE_REMOTE = os.getenv("RCLONE_REMOTE", "gcs")     # name of the rclone remote

# Data files
PROMPTS_FILE  = ROOT / "prompts.md"
SEED_JSON     = DATA_DIR / "latest.json"   # 350-conf seed; imported once into PG
```

---

## cfp/models.py — implement exactly as shown

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
    ORGANIZER     = "organizer"   # local/publicity/publication/web/sponsorship/steering
    OTHER         = "other"        # any committee role not covered above

class OrgType(str, Enum):
    PUBLISHER    = "publisher"
    UNIVERSITY   = "university"
    COMPANY      = "company"
    RESEARCH_LAB = "research_lab"
    GOVERNMENT   = "government"
    OTHER        = "other"
```

### Event dataclass — merge of both repos

All fields are `Optional` except `event_id`, `acronym`, `name`.
Include `slug`, `days_to_deadline`, `next_deadline` as computed `@property`.
Include `to_markdown()` method.

The canonical paper-deadline field is `paper_deadline` (not `deadline`) —
this name is used uniformly across prompts.md, parsers, DB, and Markdown.

```python
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

@dataclass(slots=True)
class Event:
    # Primary key (WikiCFP eventid=N; use -1 for non-WikiCFP sources — see
    # codegen/05 for how these get a unique id at insert time via SERIAL)
    event_id:     int
    acronym:      str
    name:         str

    # Series / edition
    series_id:    Optional[int]  = None
    edition_year: Optional[int]  = None

    # Classification
    categories:   list[Category] = field(default_factory=list)
    is_workshop:  bool           = False
    is_virtual:   bool           = False
    raw_tags:     list[str]      = field(default_factory=list)  # WikiCFP "Categories:" tags

    # Dates (all as datetime.date)
    when_raw:          Optional[str]  = None   # original "Aug 26, 2026 - Aug 28, 2026"
    start_date:        Optional[date] = None
    end_date:          Optional[date] = None
    abstract_deadline: Optional[date] = None
    paper_deadline:    Optional[date] = None   # full paper / regular submission deadline
    notification:      Optional[date] = None
    camera_ready:      Optional[date] = None

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

    # Submission infrastructure (new — sourced from PROMPT_TIER2 / TIER3 / TIER4)
    submission_system: Optional[str] = None   # link to EasyChair/EDAS/HotCRP/CMT/OpenReview
    sponsor_names:     list[str]     = field(default_factory=list)
    # e.g. ["IEEE", "ACM SIGCHI", "Springer LNCS"]

    # URLs
    origin_url:  Optional[str]  = None
    official_url: Optional[str]  = None
    source:       str            = "wikicfp"  # wikicfp|ai_deadlines|conferencelist|manual

    # Quality / audit
    quality_flags:    list[str]    = field(default_factory=list)
    # values from PROMPT_QUALITY_GUARD: predatory_publisher | journal_not_conference |
    # invented_url | wrong_rank | date_anomaly | location_contradiction
    quality_severity: Optional[str] = None    # "block" | "warn" | "ok"
    scrape_session_id: Optional[str] = None   # audit trail (arch.md §4 S9) — which
    # pipeline run wrote this row; FK to scrape_sessions.session_id

    # Metadata
    scraped_at:   datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_checked: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def slug(self) -> str:
        """Deterministic ACRONYM-YEAR slug. Handles year already in acronym."""
        short = self.acronym.upper()
        year  = str(self.edition_year or
                    (self.start_date.year     if self.start_date     else
                     (self.paper_deadline.year if self.paper_deadline else "")))
        slug_base = re.sub(r"\s*" + re.escape(year) + r"\s*$", "", short).strip()
        return re.sub(r"[^A-Z0-9]+", "-", f"{slug_base}-{year}").strip("-")

    @property
    def next_deadline(self) -> Optional[date]:
        today = date.today()
        candidates = [d for d in [self.abstract_deadline, self.paper_deadline]
                      if d and d >= today]
        return min(candidates) if candidates else None

    @property
    def days_to_deadline(self) -> Optional[int]:
        nd = self.next_deadline
        return (nd - date.today()).days if nd else None

    def to_markdown(self) -> str:
        def fmt(d: Optional[date]) -> str:
            return d.strftime("%Y-%m-%d") if d else "TBD"
        kind = "Workshop" if self.is_workshop else "Conference"
        lines = [
            f"# {self.name} ({self.acronym})", "",
            f"**Type:** {kind}  ",
            f"**Category:** {', '.join(c.value for c in self.categories)}  ",
            f"**Year:** {self.edition_year or 'TBD'}  ",
            f"**Rank:** {self.rank or 'N/A'}  ",
            f"**Source:** {self.source}  ",
            f"**URL:** {self.official_url or self.origin_url or ''}  ", "",
            "## Description", "",
            self.description or "_No description available._", "",
            "## Deadlines", "",
            "| Milestone | Date |", "|-----------|------|",
            f"| Abstract deadline | {fmt(self.abstract_deadline)} |",
            f"| Paper deadline    | {fmt(self.paper_deadline)} |",
            f"| Notification      | {fmt(self.notification)} |",
            f"| Camera ready      | {fmt(self.camera_ready)} |", "",
            "## Event", "",
            "| Field | Value |", "|-------|-------|",
            f"| Start    | {fmt(self.start_date)} |",
            f"| End      | {fmt(self.end_date)} |",
            f"| Location | {self.where_raw or 'TBD'} |",
            f"| Submission system | {self.submission_system or 'N/A'} |", "",
        ]
        if self.sponsor_names:
            lines += ["## Sponsors", "",
                      ", ".join(f"`{s}`" for s in self.sponsor_names), ""]
        if self.raw_tags:
            lines += ["## Tags", "",
                      ", ".join(f"`{t}`" for t in self.raw_tags), ""]
        if self.notes:
            lines += ["## Notes", "", self.notes, ""]
        return "\n".join(lines)
```

### Remaining dataclasses — implement with @dataclass(slots=True)

```
Person:       person_id(int), full_name(str), email, dblp_url, homepage
Organisation: org_id(int), name(str), type(OrgType), country, website
Venue:        venue_id(int), name, city, state, country, latitude, longitude
Series:       series_id(int), acronym(str), full_name, origin_url, org_id
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
