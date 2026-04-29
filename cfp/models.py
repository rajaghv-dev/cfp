from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional


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
    T1 = 1
    T2 = 2
    T3 = 3
    T4 = 4


class JobStatus(str, Enum):
    PENDING   = "pending"
    IN_FLIGHT = "in_flight"
    DONE      = "done"
    FAILED    = "failed"
    DEAD      = "dead"


class PersonRole(str, Enum):
    GENERAL_CHAIR = "general_chair"
    PC_CHAIR      = "pc_chair"
    AREA_CHAIR    = "area_chair"
    KEYNOTE       = "keynote"
    ORGANIZER     = "organizer"
    OTHER         = "other"


class OrgType(str, Enum):
    PUBLISHER    = "publisher"
    UNIVERSITY   = "university"
    COMPANY      = "company"
    RESEARCH_LAB = "research_lab"
    GOVERNMENT   = "government"
    OTHER        = "other"


@dataclass(slots=True)
class Event:
    event_id:     int
    acronym:      str
    name:         str

    series_id:    Optional[int]  = None
    edition_year: Optional[int]  = None

    categories:   list[Category] = field(default_factory=list)
    is_workshop:  bool           = False
    is_virtual:   bool           = False
    raw_tags:     list[str]      = field(default_factory=list)

    when_raw:          Optional[str]  = None
    start_date:        Optional[date] = None
    end_date:          Optional[date] = None
    abstract_deadline: Optional[date] = None
    paper_deadline:    Optional[date] = None
    notification:      Optional[date] = None
    camera_ready:      Optional[date] = None

    where_raw:    Optional[str]  = None
    country:      Optional[str]  = None
    region:       Optional[str]  = None
    india_state:  Optional[str]  = None
    venue_id:     Optional[int]  = None

    description:  Optional[str]  = None
    notes:        str            = ""
    rank:         Optional[str]  = None

    submission_system: Optional[str] = None
    sponsor_names:     list[str]     = field(default_factory=list)

    origin_url:   Optional[str]  = None
    official_url: Optional[str]  = None
    source:       str            = "wikicfp"

    quality_flags:    list[str]      = field(default_factory=list)
    quality_severity: Optional[str]  = None
    scrape_session_id: Optional[str] = None

    scraped_at:   datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_checked: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def slug(self) -> str:
        short = self.acronym.upper()
        year_val = (
            self.edition_year
            or (self.start_date.year if self.start_date else None)
            or (self.paper_deadline.year if self.paper_deadline else None)
        )
        year = str(year_val) if year_val else ""
        slug_base = re.sub(r"\s*" + re.escape(year) + r"\s*$", "", short).strip() if year else short
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


@dataclass(slots=True)
class Person:
    person_id: int
    full_name: str
    email:    Optional[str] = None
    dblp_url: Optional[str] = None
    homepage: Optional[str] = None


@dataclass(slots=True)
class Organisation:
    org_id:  int
    name:    str
    type:    OrgType         = OrgType.OTHER
    country: Optional[str]   = None
    website: Optional[str]   = None


@dataclass(slots=True)
class Venue:
    venue_id:  int
    name:      Optional[str]   = None
    city:      Optional[str]   = None
    state:     Optional[str]   = None
    country:   Optional[str]   = None
    latitude:  Optional[float] = None
    longitude: Optional[float] = None


@dataclass(slots=True)
class Series:
    series_id:  int
    acronym:    str
    full_name:  Optional[str] = None
    origin_url: Optional[str] = None
    org_id:     Optional[int] = None


@dataclass(slots=True)
class ScrapeJob:
    url:             str
    domain:          str
    priority:        int                = 0
    source_event_id: Optional[int]      = None
    attempts:        int                = 0
    status:          JobStatus          = JobStatus.PENDING
    added_at:        datetime           = field(default_factory=lambda: datetime.now(timezone.utc))
    last_error:      Optional[str]      = None


@dataclass(slots=True)
class TierResult:
    tier:           Tier
    model:          str
    confidence:     float
    output:         dict
    escalate:       bool          = False
    escalate_reason: Optional[str] = None
    elapsed_ms:     int           = 0


@dataclass(slots=True)
class EscalationPayload:
    record:           dict
    tier_results:     list[TierResult]
    escalate_reason:  str
    raw_html:         Optional[str] = None


@dataclass(slots=True)
class OntologyEdge:
    subject:        str
    predicate:      str
    object:         str
    confidence:     float
    source_event_id: Optional[int] = None
