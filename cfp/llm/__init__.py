"""LLM client layer for the CFP pipeline.

Public surface:
    OllamaClient            — wraps the Ollama Python SDK (sync)
    TierEscalation          — raised by tier handlers to push a job to the
                              escalation queue (cfp:escalate:tier4) when the
                              current tier cannot handle the input
    ESCALATE_REASONS        — canonical set of escalation reasons recognised
                              by the dispatcher (see arch.md §1 ADR-8)
    resolve_model           — alias → quant-tagged model lookup
    get_available_models    — models actually pulled on the local Ollama daemon
    profile_intersection    — needed ∩ available, sorted
"""
from __future__ import annotations

from cfp.llm.client import (
    OllamaClient,
    resolve_model,
    get_available_models,
    profile_intersection,
)


class TierEscalation(Exception):
    """Raised when a tier cannot complete a job and the dispatcher must
    enqueue it on cfp:escalate:tier<target_tier>.

    Attributes:
        reason: a string drawn from ESCALATE_REASONS describing why the
                escalation is happening (used for metrics/labels).
        target_tier: the tier number to retry on (1..4). Tier 4 means the
                     batched DeepSeek-R1 70b run on the largest box.
    """

    def __init__(self, reason: str, target_tier: int) -> None:
        super().__init__(f"escalate→tier{target_tier}: {reason}")
        self.reason = reason
        self.target_tier = target_tier


ESCALATE_REASONS: frozenset[str] = frozenset({
    "low_confidence",       # tier returned conf < TIER_THRESHOLD
    "json_parse_fail",      # JSON repair exhausted (codegen/10 spec name)
    "json_parse_failed",    # legacy alias
    "tool_loop_exhausted",  # chat_with_tools hit max_iters
    "model_unavailable",    # required model not in profile_intersection()
    "long_context",         # input exceeds tier's context window
    "ambiguous",            # tier flagged result as ambiguous
    "extraction_failed",    # tools returned no usable signal
    "multi_category",       # tier 2 unable to disambiguate categories
    "unknown_site",         # external site without a parser
    "dedup_ambiguous",      # cosine in grey zone, needs LLM/human
    "dedup_collapsed",      # already merged into another event
    "ontology_edge",        # tier 4 to draw a new ontology edge
    "quality_block",        # PROMPT_QUALITY_GUARD severity == "block"
})


__all__ = [
    "OllamaClient",
    "TierEscalation",
    "ESCALATE_REASONS",
    "resolve_model",
    "get_available_models",
    "profile_intersection",
]
