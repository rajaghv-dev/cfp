# Codegen 02 — cfp/prompts_parser.py

## File to Create
- `cfp/prompts_parser.py`

## Rule
Reads `prompts.md` (the hand-edited data file) and exposes structured Python
objects to the rest of the pipeline. **Pure parsing, no I/O beyond reading the
file.** No HTTP, no DB, no LLM calls.

`prompts.md` is the source of truth. This module is its only reader.

---

## Imports
```python
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Optional
import re

from config import PROMPTS_FILE
from cfp.models import Category
```

---

## Grammar (must match prompts.md exactly)

Top-level lines, dispatched by prefix:

| Prefix          | Effect                                                            |
|-----------------|-------------------------------------------------------------------|
| `# `            | Comment / heading — ignored                                       |
| (blank)         | Ignored                                                           |
| `## `           | Markdown subheading — ignored (decorative)                        |
| `CATEGORY: X`   | Open a category block. `X` must match `Category` enum exactly.    |
| `KEYWORD: q`    | Belongs to current `CATEGORY` (free-text search query)            |
| `URL: http...`  | Belongs to the most-recently opened block (CATEGORY / INDEX_*)    |
| `INDEX_SERIES:  L`  | Open a series-index block, letter `L` (single A–Z)            |
| `INDEX_JOURNAL: L`  | Open a journal-index block, letter `L`                        |
| `PARSER: domain -> module.path` | Register a domain → parser-module mapping        |
| `PROMPT_NAME: |`            | Open multi-line prompt body. Body lines indented by exactly 2 spaces. Body ends at next top-level key or EOF. |

Whitespace rules:
- 2-space indent for prompt bodies. Tabs forbidden — raise `PromptsSyntaxError`.
- Trailing whitespace tolerated on data lines.
- Blank lines inside a `PROMPT_*` body are preserved.

Errors:
- Unknown `CATEGORY:` value → `PromptsSyntaxError("unknown category: X (line N)")`.
- `KEYWORD:`/`URL:` outside any open block → `PromptsSyntaxError("orphan KEYWORD/URL at line N")`.
- `PARSER:` line that doesn't match `^PARSER: \S+ -> \S+$` → `PromptsSyntaxError`.
- Duplicate `PROMPT_NAME` → `PromptsSyntaxError("duplicate prompt: NAME")`.

---

## Data Classes

```python
@dataclass(frozen=True)
class SeedURL:
    url: str
    kind: Literal["category", "series_index", "journal_index"]
    category: Optional[Category] = None    # filled if kind == "category"
    letter: Optional[str] = None           # filled if kind == "series_index"/"journal_index"

@dataclass(frozen=True)
class ParserEntry:
    domain: str           # e.g. "www.wikicfp.com"
    module: str           # e.g. "cfp.parsers.wikicfp"

@dataclass
class PromptsBundle:
    prompts:        dict[str, str]              # PROMPT_NAME → body
    categories:     dict[Category, list[str]]   # category → KEYWORD list
    seed_urls:      list[SeedURL]               # all URLs grouped by source block
    parsers:        list[ParserEntry]           # KNOWN PARSERS registry
    external_urls:  list[str]                   # EXTERNAL DATA SOURCES URLs (free-form, no enum)


class PromptsSyntaxError(ValueError):
    """Raised on malformed prompts.md content. Message includes line number."""
```

---

## Public API

```python
def load(path: Path = PROMPTS_FILE) -> PromptsBundle: ...

def get_prompt(name: str, *, path: Path = PROMPTS_FILE) -> str:
    """Convenience: load(path).prompts[name]; raises KeyError if absent."""

def category_keywords(cat: Category, *, path: Path = PROMPTS_FILE) -> list[str]:
    """Convenience: load(path).categories[cat]; returns [] if cat absent."""

def parser_for_domain(host: str, *, path: Path = PROMPTS_FILE) -> Optional[str]:
    """Longest-suffix match against PARSER registry. Returns module path or None."""
```

The four functions all share a single in-memory cache (`_BUNDLE_CACHE`)
keyed by absolute path + mtime. Cache is invalidated automatically on file
mtime change (no explicit `reload()` needed). This keeps test fixtures cheap.

---

## Implementation Sketch

```python
_RE_PARSER       = re.compile(r"^PARSER:\s+(\S+)\s+->\s+(\S+)\s*$")
_RE_PROMPT_OPEN  = re.compile(r"^(PROMPT_[A-Z0-9_]+):\s*\|\s*$")
_RE_CATEGORY     = re.compile(r"^CATEGORY:\s+(\S+)\s*$")
_RE_KEYWORD      = re.compile(r"^KEYWORD:\s+(.+?)\s*$")
_RE_URL          = re.compile(r"^URL:\s+(\S+)\s*$")
_RE_INDEX_SER    = re.compile(r"^INDEX_SERIES:\s+([A-Z])\s*$")
_RE_INDEX_JNL    = re.compile(r"^INDEX_JOURNAL:\s+([A-Z])\s*$")
_RE_TOPLEVEL_KEY = re.compile(r"^[A-Z][A-Z0-9_]*:")  # any UPPER_KEY: line ends a PROMPT body

class _State:
    section: Literal["categories","series","journal","parsers","external","prompts"] = "categories"
    current_category: Optional[Category] = None
    current_index_kind: Optional[Literal["series_index","journal_index"]] = None
    current_index_letter: Optional[str] = None
    current_prompt_name: Optional[str] = None
    current_prompt_body: list[str] = []   # 2-space indent already stripped

def load(path: Path = PROMPTS_FILE) -> PromptsBundle:
    text = path.read_text(encoding="utf-8")
    if "\t" in text:
        raise PromptsSyntaxError("tabs forbidden — convert to 2 spaces")
    state = _State()
    bundle = PromptsBundle({}, {}, [], [], [])
    for lineno, raw in enumerate(text.splitlines(), start=1):
        # … dispatch on prefix; collect into bundle …
    if state.current_prompt_name:
        _flush_prompt(state, bundle)
    return bundle
```

Key behaviour:
- A new top-level key (any `^[A-Z_]+:`) closes any open `PROMPT_*` body.
- Body lines that don't start with `  ` (2 spaces) end the body too — defensive.
- `# ─── KNOWN PARSERS ───` heading switches to `parsers` section but parsing
  is regex-based per line, so the heading is decorative only.
- `EXTERNAL DATA SOURCES` block: bare `URL:` lines that aren't preceded by a
  CATEGORY/INDEX block go into `bundle.external_urls`.

---

## Tests (`tests/test_prompts_parser.py`)

Use the actual `prompts.md` (no fixtures — it's stable enough for v1):

```python
def test_load_real_prompts():
    b = load()
    # contracts confirmed against current prompts.md
    assert len(b.prompts) == 13                       # see prompts.md grep
    assert "PROMPT_TIER1" in b.prompts
    assert "PROMPT_DEDUP" in b.prompts
    assert b.categories[Category.AI]
    assert "AI" in b.categories[Category.AI]          # acronym keyword
    assert all(c in b.categories for c in Category)    # all 13 enum values present
    assert sum(1 for s in b.seed_urls if s.kind == "series_index") == 26
    assert sum(1 for s in b.seed_urls if s.kind == "journal_index") == 26
    assert any(p.module == "cfp.parsers.wikicfp" for p in b.parsers)
    assert len(b.parsers) >= 13                       # WikiCFP, IEEE, ACM, Springer, USENIX, EDAS, EasyChair, HotCRP, CMT, OpenReview…

def test_parser_domain_longest_match():
    assert parser_for_domain("www.wikicfp.com") == "cfp.parsers.wikicfp"
    assert parser_for_domain("example.foo.bar") is None

def test_get_prompt_unknown():
    with pytest.raises(KeyError):
        get_prompt("PROMPT_DOES_NOT_EXIST")

def test_tab_rejected(tmp_path):
    bad = tmp_path / "p.md"
    bad.write_text("CATEGORY: AI\n\tKEYWORD: x\n")
    with pytest.raises(PromptsSyntaxError, match="tabs forbidden"):
        load(bad)

def test_unknown_category_rejected(tmp_path):
    bad = tmp_path / "p.md"
    bad.write_text("CATEGORY: NotARealCategory\n")
    with pytest.raises(PromptsSyntaxError, match="unknown category"):
        load(bad)

def test_orphan_url_rejected(tmp_path):
    bad = tmp_path / "p.md"
    bad.write_text("URL: http://example.com\n")
    with pytest.raises(PromptsSyntaxError, match="orphan"):
        load(bad)

def test_cache_invalidates_on_mtime(tmp_path):
    p = tmp_path / "p.md"
    p.write_text("CATEGORY: AI\nKEYWORD: a\n")
    b1 = load(p)
    p.write_text("CATEGORY: AI\nKEYWORD: a\nKEYWORD: b\n")
    b2 = load(p)
    assert len(b2.categories[Category.AI]) == 2
```

---

## Acceptance Criteria

- `load()` on the real `prompts.md` returns a `PromptsBundle` with:
  - 13 prompts (all `PROMPT_TIER1..4` + `PROMPT_DEDUP` + 8 extraction/utility prompts)
  - 13 category entries (one per `Category` enum value)
  - ≥ 26 series-index URLs + ≥ 26 journal-index URLs
  - ≥ 13 parser entries
- `get_prompt("PROMPT_TIER1")` returns the body verbatim, no `  ` indent prefix.
- `parser_for_domain("www.wikicfp.com")` returns `"cfp.parsers.wikicfp"`.
- All malformed-input tests raise `PromptsSyntaxError` with line-number context.
- No file I/O outside `path.read_text()`. No HTTP. No psycopg / Redis / Ollama.

---

## Downstream Consumers

| Module             | What it consumes                       |
|--------------------|----------------------------------------|
| `cfp/llm/client.py`    | `get_prompt("PROMPT_TIER1")` etc.  |
| `cfp/queue.py`         | `load().seed_urls` for `enqueue-seeds` |
| `cfp/fetch.py`         | `parser_for_domain(host)` to dispatch |
| `cfp/cli.py`           | `load()` wrappers for `bootstrap-ontology` and reports |

This is the only module that reads `prompts.md` — every other consumer goes
through this parser.
