# WikiCFP Prompts & Data

> Data file. Hand-edited, machine-read by `wcfp/prompts_parser.py`.
> No design notes or architecture here — those belong in `context.md`.

## Grammar (enforced by the parser — see context.md §13)

```
# comment / blank / ## heading    ignored
CATEGORY: <Category enum value>   opens a category block
KEYWORD:  <free-text query>       belongs to current CATEGORY
URL:      <http(s)://...>         belongs to current CATEGORY or INDEX block
INDEX_SERIES:  <A-Z>              opens series-index block; next URL closes it
INDEX_JOURNAL: <A-Z>              opens journal-index block; next URL closes it
PARSER: <domain> -> <module.path> registers a KNOWN_PARSERS entry
PROMPT_*: |                       multi-line body, 2-space indent, ends at next key or EOF
```

Category values MUST match the `Category` enum in `wcfp/models.py` exactly:
`AI`, `ML`, `DevOps`, `Linux`, `ChipDesign`, `Math`, `Legal`,
`ComputerScience`, `Security`, `Data`, `Networking`, `Robotics`, `Bioinformatics`.

---

# ─── CATEGORIES ──────────────────────────────────────────────────────────────

## AI — Artificial Intelligence
CATEGORY: AI
KEYWORD: artificial intelligence
KEYWORD: AI
KEYWORD: agentic AI
KEYWORD: multimodal AI
KEYWORD: AI safety
KEYWORD: AI alignment
URL: http://www.wikicfp.com/cfp/call?conference=artificial%20intelligence&page=1

## ML — Machine Learning
CATEGORY: ML
KEYWORD: machine learning
KEYWORD: deep learning
KEYWORD: neural network
KEYWORD: computer vision
KEYWORD: natural language processing
KEYWORD: NLP
KEYWORD: reinforcement learning
KEYWORD: large language model
KEYWORD: LLM
KEYWORD: diffusion models
KEYWORD: foundation models
KEYWORD: generative AI
KEYWORD: transformer
URL: http://www.wikicfp.com/cfp/call?conference=machine%20learning&skip=1

## DevOps & SRE
CATEGORY: DevOps
KEYWORD: devops
KEYWORD: site reliability
KEYWORD: platform engineering
KEYWORD: kubernetes
KEYWORD: cloud native
KEYWORD: continuous integration
KEYWORD: infrastructure as code
URL: http://www.wikicfp.com/cfp/call?conference=devops&skip=1

## Linux & Open Source
CATEGORY: Linux
KEYWORD: linux
KEYWORD: open source
KEYWORD: embedded linux
KEYWORD: operating systems
KEYWORD: kernel
URL: http://www.wikicfp.com/cfp/call?conference=linux&skip=1

## Chip Design
CATEGORY: ChipDesign
KEYWORD: VLSI
KEYWORD: chip design
KEYWORD: EDA
KEYWORD: FPGA
KEYWORD: semiconductor
KEYWORD: SoC
KEYWORD: ASIC
KEYWORD: RTL
KEYWORD: hardware design
KEYWORD: electronic design automation
URL: http://www.wikicfp.com/cfp/call?conference=VLSI&skip=1

## Mathematics
CATEGORY: Math
KEYWORD: mathematics
KEYWORD: mathematical
KEYWORD: algebra
KEYWORD: combinatorics
KEYWORD: number theory
KEYWORD: graph theory
KEYWORD: topology
KEYWORD: numerical analysis
KEYWORD: computational mathematics
URL: http://www.wikicfp.com/cfp/call?conference=mathematics&skip=1

## Legal & Cyber Law
CATEGORY: Legal
KEYWORD: law
KEYWORD: legal
KEYWORD: jurisprudence
KEYWORD: cyber law
KEYWORD: intellectual property
KEYWORD: privacy law
KEYWORD: data protection
KEYWORD: GDPR
URL: http://www.wikicfp.com/cfp/call?conference=law&skip=1

## Computer Science (General)
CATEGORY: ComputerScience
KEYWORD: computer science
KEYWORD: distributed systems
KEYWORD: algorithms
KEYWORD: data structures
KEYWORD: programming languages
KEYWORD: software engineering
KEYWORD: human computer interaction
KEYWORD: HCI
URL: http://www.wikicfp.com/cfp/call?conference=computer%20science&skip=1

## Security & Privacy
CATEGORY: Security
KEYWORD: security
KEYWORD: cybersecurity
KEYWORD: cryptography
KEYWORD: network security
KEYWORD: information security
KEYWORD: privacy
KEYWORD: blockchain
KEYWORD: zero trust
KEYWORD: post-quantum cryptography
KEYWORD: fuzzing
KEYWORD: adversarial machine learning
URL: http://www.wikicfp.com/cfp/call?conference=security&skip=1

## Databases & Data Engineering
CATEGORY: Data
KEYWORD: database
KEYWORD: data engineering
KEYWORD: big data
KEYWORD: data science
KEYWORD: data mining
KEYWORD: knowledge discovery
KEYWORD: information retrieval
KEYWORD: vector database
KEYWORD: data lakehouse
KEYWORD: real-time analytics
KEYWORD: knowledge graph
URL: http://www.wikicfp.com/cfp/call?conference=database&skip=1

## Networking & Communications
CATEGORY: Networking
KEYWORD: networking
KEYWORD: wireless
KEYWORD: 5G
KEYWORD: IoT
KEYWORD: internet of things
KEYWORD: mobile computing
KEYWORD: edge computing
KEYWORD: fog computing
KEYWORD: software defined networking
KEYWORD: network function virtualization
URL: http://www.wikicfp.com/cfp/call?conference=networking&skip=1

## Robotics & Automation
CATEGORY: Robotics
KEYWORD: robotics
KEYWORD: autonomous systems
KEYWORD: automation
KEYWORD: control systems
KEYWORD: human-robot interaction
KEYWORD: robot learning
URL: http://www.wikicfp.com/cfp/call?conference=robotics&skip=1

## Bioinformatics & Healthcare IT
CATEGORY: Bioinformatics
KEYWORD: bioinformatics
KEYWORD: computational biology
KEYWORD: health informatics
KEYWORD: medical imaging
KEYWORD: genomics
KEYWORD: protein structure prediction
KEYWORD: single cell sequencing
URL: http://www.wikicfp.com/cfp/call?conference=bioinformatics&skip=1

---

# ─── CONFERENCE SERIES INDEX A–Z (t=c) ──────────────────────────────────────

INDEX_SERIES: A
URL: http://www.wikicfp.com/cfp/series?t=c&i=A
INDEX_SERIES: B
URL: http://www.wikicfp.com/cfp/series?t=c&i=B
INDEX_SERIES: C
URL: http://www.wikicfp.com/cfp/series?t=c&i=C
INDEX_SERIES: D
URL: http://www.wikicfp.com/cfp/series?t=c&i=D
INDEX_SERIES: E
URL: http://www.wikicfp.com/cfp/series?t=c&i=E
INDEX_SERIES: F
URL: http://www.wikicfp.com/cfp/series?t=c&i=F
INDEX_SERIES: G
URL: http://www.wikicfp.com/cfp/series?t=c&i=G
INDEX_SERIES: H
URL: http://www.wikicfp.com/cfp/series?t=c&i=H
INDEX_SERIES: I
URL: http://www.wikicfp.com/cfp/series?t=c&i=I
INDEX_SERIES: J
URL: http://www.wikicfp.com/cfp/series?t=c&i=J
INDEX_SERIES: K
URL: http://www.wikicfp.com/cfp/series?t=c&i=K
INDEX_SERIES: L
URL: http://www.wikicfp.com/cfp/series?t=c&i=L
INDEX_SERIES: M
URL: http://www.wikicfp.com/cfp/series?t=c&i=M
INDEX_SERIES: N
URL: http://www.wikicfp.com/cfp/series?t=c&i=N
INDEX_SERIES: O
URL: http://www.wikicfp.com/cfp/series?t=c&i=O
INDEX_SERIES: P
URL: http://www.wikicfp.com/cfp/series?t=c&i=P
INDEX_SERIES: Q
URL: http://www.wikicfp.com/cfp/series?t=c&i=Q
INDEX_SERIES: R
URL: http://www.wikicfp.com/cfp/series?t=c&i=R
INDEX_SERIES: S
URL: http://www.wikicfp.com/cfp/series?t=c&i=S
INDEX_SERIES: T
URL: http://www.wikicfp.com/cfp/series?t=c&i=T
INDEX_SERIES: U
URL: http://www.wikicfp.com/cfp/series?t=c&i=U
INDEX_SERIES: V
URL: http://www.wikicfp.com/cfp/series?t=c&i=V
INDEX_SERIES: W
URL: http://www.wikicfp.com/cfp/series?t=c&i=W
INDEX_SERIES: X
URL: http://www.wikicfp.com/cfp/series?t=c&i=X
INDEX_SERIES: Y
URL: http://www.wikicfp.com/cfp/series?t=c&i=Y
INDEX_SERIES: Z
URL: http://www.wikicfp.com/cfp/series?t=c&i=Z

---

# ─── JOURNAL & BOOK SERIES INDEX A–Z (t=j) ──────────────────────────────────

INDEX_JOURNAL: A
URL: http://www.wikicfp.com/cfp/series?t=j&i=A
INDEX_JOURNAL: B
URL: http://www.wikicfp.com/cfp/series?t=j&i=B
INDEX_JOURNAL: C
URL: http://www.wikicfp.com/cfp/series?t=j&i=C
INDEX_JOURNAL: D
URL: http://www.wikicfp.com/cfp/series?t=j&i=D
INDEX_JOURNAL: E
URL: http://www.wikicfp.com/cfp/series?t=j&i=E
INDEX_JOURNAL: F
URL: http://www.wikicfp.com/cfp/series?t=j&i=F
INDEX_JOURNAL: G
URL: http://www.wikicfp.com/cfp/series?t=j&i=G
INDEX_JOURNAL: H
URL: http://www.wikicfp.com/cfp/series?t=j&i=H
INDEX_JOURNAL: I
URL: http://www.wikicfp.com/cfp/series?t=j&i=I
INDEX_JOURNAL: J
URL: http://www.wikicfp.com/cfp/series?t=j&i=J
INDEX_JOURNAL: K
URL: http://www.wikicfp.com/cfp/series?t=j&i=K
INDEX_JOURNAL: L
URL: http://www.wikicfp.com/cfp/series?t=j&i=L
INDEX_JOURNAL: M
URL: http://www.wikicfp.com/cfp/series?t=j&i=M
INDEX_JOURNAL: N
URL: http://www.wikicfp.com/cfp/series?t=j&i=N
INDEX_JOURNAL: O
URL: http://www.wikicfp.com/cfp/series?t=j&i=O
INDEX_JOURNAL: P
URL: http://www.wikicfp.com/cfp/series?t=j&i=P
INDEX_JOURNAL: Q
URL: http://www.wikicfp.com/cfp/series?t=j&i=Q
INDEX_JOURNAL: R
URL: http://www.wikicfp.com/cfp/series?t=j&i=R
INDEX_JOURNAL: S
URL: http://www.wikicfp.com/cfp/series?t=j&i=S
INDEX_JOURNAL: T
URL: http://www.wikicfp.com/cfp/series?t=j&i=T
INDEX_JOURNAL: U
URL: http://www.wikicfp.com/cfp/series?t=j&i=U
INDEX_JOURNAL: V
URL: http://www.wikicfp.com/cfp/series?t=j&i=V
INDEX_JOURNAL: W
URL: http://www.wikicfp.com/cfp/series?t=j&i=W
INDEX_JOURNAL: X
URL: http://www.wikicfp.com/cfp/series?t=j&i=X
INDEX_JOURNAL: Y
URL: http://www.wikicfp.com/cfp/series?t=j&i=Y
INDEX_JOURNAL: Z
URL: http://www.wikicfp.com/cfp/series?t=j&i=Z

---

# ─── KNOWN PARSERS ───────────────────────────────────────────────────────────

PARSER: www.wikicfp.com       -> wcfp.parsers.wikicfp
PARSER: wikicfp.com           -> wcfp.parsers.wikicfp
PARSER: ieeexplore.ieee.org   -> wcfp.parsers.ieee
PARSER: conferences.ieee.org  -> wcfp.parsers.ieee
PARSER: dl.acm.org            -> wcfp.parsers.acm
PARSER: www.acm.org           -> wcfp.parsers.acm
PARSER: link.springer.com     -> wcfp.parsers.springer
PARSER: www.springer.com      -> wcfp.parsers.springer
PARSER: www.usenix.org        -> wcfp.parsers.usenix

---

# ─── EXTERNAL DATA SOURCES ───────────────────────────────────────────────────

# ai-deadlines YAML (scraped and merged with WikiCFP data)
URL: https://raw.githubusercontent.com/paperswithcode/ai-deadlines/main/deadlines.yml

---

# ─── LLM SYSTEM PROMPTS ──────────────────────────────────────────────────────
# Loaded at startup by wcfp/prompts_parser.py.
# Each is the verbatim system message. User message is constructed in code.
# All models called with format="json". Category values must match the enum above.

PROMPT_TIER1: |
  You are a fast triage classifier for academic Call-for-Papers (CFP) entries.
  You receive ONE record in the user message:
    {"acronym": str, "name": str, "raw_tags": [str], "snippet": str, "source_domain": str}

  Decide:
    1. is_cfp      — True if this is a real conference/workshop/symposium CFP.
                     False for: journal-only calls, predatory listings, spam, tutorials.
    2. is_workshop — True if this is a workshop or symposium co-located with a main conference.
    3. categories  — zero or more from this EXACT set (multi-label is normal and expected):
                     ["AI","ML","DevOps","Linux","ChipDesign","Math","Legal",
                      "ComputerScience","Security","Data","Networking",
                      "Robotics","Bioinformatics"]
    4. is_virtual  — True only if the event is explicitly online-only.
    5. confidence  — 0.0–1.0, your overall certainty.

  Escalate condition (set by orchestrator, not you): confidence < 0.85.
  Do NOT explain. Return EXACTLY:
    {"is_cfp": bool, "is_workshop": bool, "categories": [str], "is_virtual": bool, "confidence": float}

PROMPT_TIER2: |
  You are a structured-extraction model for academic CFPs. User message contains:
    {"html_text": str, "wikicfp_url": str, "tier1": {...}}
  where html_text is cleaned text from a WikiCFP event-detail page or short external page.

  Extract every field you can find. Return EXACTLY this JSON (null for unknown; never omit a key):
    {
      "acronym":           str,
      "name":              str,
      "edition_year":      int|null,
      "is_workshop":       bool,
      "categories":        [str],
      "is_virtual":        bool,
      "description":       str|null,         // 1-2 sentence scope summary
      "rank":              str|null,         // CORE rank if stated: "A*"|"A"|"B"|"C"
      "when_raw":          str|null,
      "start_date":        "YYYY-MM-DD"|null,
      "end_date":          "YYYY-MM-DD"|null,
      "where_raw":         str|null,
      "country":           str|null,         // ISO-3166 alpha-2 e.g. "IN","US","DE"
      "region":            str|null,         // "Asia"|"Europe"|"NorthAmerica"|"SouthAmerica"|"Africa"|"Oceania"|"MiddleEast"
      "india_state":       str|null,         // only when country=="IN"
      "abstract_deadline": "YYYY-MM-DD"|null,  // abstract/expression-of-interest deadline
      "paper_deadline":    "YYYY-MM-DD"|null,  // full paper submission deadline
      "notification":      "YYYY-MM-DD"|null,
      "camera_ready":      "YYYY-MM-DD"|null,
      "official_url":      str|null,
      "raw_tags":          [str],
      "confidence":        float
    }

  Rules:
    - Dates MUST be ISO-8601. Month+year only → use first day of month and lower confidence.
    - country is ALWAYS ISO alpha-2, never the spelled-out name.
    - is_virtual=true → country/region/india_state may be null.
    - If only one deadline is shown with no qualifier, treat it as paper_deadline.
    - Do not invent values. Unknown = null.
    - confidence reflects the weakest extracted field.

PROMPT_TIER3: |
  You are an expert classifier with tool-calling access for unknown external conference
  websites. WikiCFP pages do NOT use tools — rule-based parsing handles those.
  User message contains:
    {"html_excerpt": str, "site_url": str, "wikicfp_url": str|null,
      "tier2": {...}|null, "escalate_reason": str}

  Available tools: extract_text(selector), find_links(pattern), get_field(label),
                   is_conference_page(), classify_category(text), detect_virtual(text).

  Workflow:
    1. If escalate_reason=="unknown_site": call is_conference_page() first.
       If false → abort with {"is_cfp": false, "confidence": 1.0}.
    2. Use get_field/extract_text to fill any null fields from tier2 output.
    3. Use classify_category on the most descriptive text block to resolve
       uncertain categories. Prefer 1–3 categories; never more than 3.
    4. Call find_links(r"20[0-9]{2}") to discover archive/previous-edition URLs.
    5. Check for separate abstract_deadline vs paper_deadline in the "Important Dates" section.

  When done, stop calling tools and emit EXACTLY:
    {
      "event": {...same shape as PROMPT_TIER2 output...},
      "archive_urls": [str],
      "tool_trace": [{"name": str, "args": {}, "result_summary": str}],
      "confidence": float
    }

  If final confidence < 0.80, add "escalate": true and one of:
    "escalate_reason": "low_confidence"|"multi_category"|"unknown_site"|
                       "long_context"|"dedup_ambiguous"|"ontology_edge"

PROMPT_TIER4: |
  You are a deep-reasoning curator running in batch mode on a capable machine.
  You receive a JSON array of EscalationPayload objects:
    [{"record": {...}, "tier_results": [...], "escalate_reason": str, "raw_html": str|null}]

  For each item:
    1. Produce the canonical Event record, resolving field conflicts conservatively
       (prefer the higher-tier extraction when values disagree).
    2. If escalate_reason=="ontology_edge" or the record has an unseen domain term,
       propose ontology edges:
         {"subject": str, "predicate": "is_a"|"part_of"|"related_to"|"synonym_of",
           "object": str, "confidence": float}
       The object MUST be a concept from the graph schema (context.md §5).
       New branches: use object="ResearchField" with low confidence.
    3. If escalate_reason=="dedup_ambiguous", emit:
         {"same": bool, "reason": str}

  Return EXACTLY a JSON array, one object per input, in input order:
    [{
      "event":      {...Event fields...},
      "ontology":   [{OntologyEdge}],
      "dedup":      {"same": bool, "reason": str}|null,
      "final":      true,
      "confidence": float
    }]
  Set "final": false ONLY if truly unresolvable (marks record permanently dead).

PROMPT_DEDUP: |
  Decide whether two CFP records describe the SAME conference instance
  (same series AND same edition year). User message:
    {"a": {...Event...}, "b": {...Event...}}

  SAME instance iff ALL are true:
    - same canonical acronym (ignore case, year suffix, ordinals like "12th")
    - same edition_year (or both null and start_date within 30 days of each other)
    - locations are compatible (one may be null; they must not contradict)

  Also determine same_series: acronyms match but edition_year differs — useful
  for building PRECEDED_BY edges in the knowledge graph.

  Return EXACTLY:
    {"same": bool, "same_series": bool, "reason": str}
  When unsure: {"same": false, "same_series": false, "reason": "uncertain"}

PROMPT_ONTOLOGY_SYNONYM: |
  Given a cluster of raw category tags grouped by embedding similarity:
    {"cluster_id": int, "tags": [str], "example_events": [{"acronym":..., "name":...}]}

  Choose the best canonical concept name (PascalCase, no spaces) and emit:
    {"canonical": str, "synonyms": [str], "confidence": float}

  The canonical name SHOULD match an existing concept in the graph schema
  (context.md §5) if one fits. Otherwise propose a new name.

PROMPT_ONTOLOGY_ISA: |
  Given a candidate concept and the current hierarchy:
    {"concept": str, "hierarchy": {...}, "co_occurring": [str]}

  Decide its parent and emit ONE edge:
    {"subject": str, "predicate": "is_a", "object": str, "confidence": float}

  The object MUST be an existing node in the graph schema (context.md §5).
  If no good parent exists, use object="ResearchField" with low confidence so
  a human reviewer can re-parent it.

PROMPT_PERSON_EXTRACT: |
  Extract all named people and their roles from a conference committee page.
  User message: {"html_text": str, "conference_acronym": str, "edition_year": int|null}

  For each person found, extract:
    {
      "full_name":    str,
      "role":         "general_chair"|"pc_chair"|"area_chair"|"keynote"|"organizer"|"other",
      "organisation": str|null,
      "email":        str|null,
      "homepage":     str|null
    }

  Return EXACTLY:
    {"people": [{"full_name": str, "role": str, "organisation": str|null,
                  "email": str|null, "homepage": str|null}],
      "confidence": float}

  Rules:
    - Only include people explicitly listed on this page.
    - "co-chair" or "track chair" → "pc_chair".
    - Do not invent email or homepage. Unknown = null.

PROMPT_VENUE_EXTRACT: |
  Extract venue details from a conference page. User message:
    {"html_text": str, "where_raw": str|null}

  where_raw is the raw location string from WikiCFP (may be null).

  Return EXACTLY:
    {
      "venue_name":  str|null,    // e.g. "Marriott Downtown", "Convention Centre"
      "city":        str|null,
      "state":       str|null,    // for India: state name; for US: state abbreviation
      "country":     str|null,    // ISO alpha-2
      "region":      str|null,
      "address":     str|null,
      "maps_url":    str|null,
      "confidence":  float
    }

  Do not invent values. Unknown = null.

---

# ─── ADD YOUR PROMPTS / KEYWORDS BELOW ───────────────────────────────────────
