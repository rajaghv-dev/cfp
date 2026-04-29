"""Tests for cfp/queue.py using fakeredis."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import pytest

from cfp import queue as Q
from cfp.prompts_parser import PromptsBundle, SeedURL
from cfp.models import Category


def _make_client():
    """Prefer fakeredis; fall back to a dedicated DB on the live cfp_redis
    container when fakeredis is unavailable in the local env."""
    try:
        import fakeredis  # type: ignore
        return fakeredis.FakeStrictRedis(decode_responses=True)
    except ImportError:
        import redis as _redis
        url = os.getenv("CFP_TEST_REDIS_URL", "redis://localhost:6379/15")
        c = _redis.Redis.from_url(url, decode_responses=True)
        c.flushdb()
        return c


@pytest.fixture
def r():
    """Fresh client per test, injected as the queue module's client."""
    client = _make_client()
    Q.set_redis_for_tests(client)
    yield client
    try:
        client.flushdb()
    except Exception:
        pass
    Q.set_redis_for_tests(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Canonicalisation + job_id


def test_canonical_url_strips_default_https_port_and_trailing_slash():
    a = Q.canonical_url("HTTPS://X.com:443/a/")
    b = Q.canonical_url("https://x.com/a")
    assert a == b == "https://x.com/a"


def test_canonical_url_keeps_non_default_port():
    assert Q.canonical_url("http://example.com:8080/p/") == "http://example.com:8080/p"


def test_canonical_url_strips_default_http_port():
    assert Q.canonical_url("http://example.com:80/") == "http://example.com/"


def test_canonical_url_preserves_query():
    assert Q.canonical_url("https://x.com/a?b=1") == "https://x.com/a?b=1"


def test_canonical_url_drops_fragment():
    assert Q.canonical_url("https://x.com/a#frag") == "https://x.com/a"


def test_make_job_id_is_32_hex():
    jid = Q.make_job_id("https://example.com/")
    assert len(jid) == 32
    assert all(c in "0123456789abcdef" for c in jid)


def test_job_id_stable_across_calls():
    a = Q.make_job_id("HTTPS://X.com:443/a/")
    b = Q.make_job_id("https://x.com/a")
    c = Q.make_job_id("https://x.com/a")
    assert a == b == c


# ---------------------------------------------------------------------------
# Job round-trip


def test_job_to_from_json_roundtrip():
    j = Q.Job(
        job_id="abc",
        url="https://x.com/a",
        tier=1,
        enqueued_at=datetime.now(timezone.utc),
        attempts=2,
        metadata={"category": "AI"},
    )
    j2 = Q.Job.from_json(j.to_json())
    assert j2.job_id == j.job_id
    assert j2.url == j.url
    assert j2.tier == j.tier
    assert j2.attempts == j.attempts
    assert j2.metadata == j.metadata
    assert j2.enqueued_at.tzinfo is not None
    assert j2.enqueued_at == j.enqueued_at


# ---------------------------------------------------------------------------
# enqueue_url


def test_enqueue_dedup_returns_false_on_second(r):
    assert Q.enqueue_url("https://x.com/a", tier=1) is True
    assert Q.enqueue_url("https://x.com/a", tier=1) is False
    assert r.zcard(Q.QUEUE_KEY_FMT.format(tier=1)) == 1


def test_enqueue_canonicalisation_dedups(r):
    assert Q.enqueue_url("HTTPS://X.com:443/a/", tier=1) is True
    assert Q.enqueue_url("https://x.com/a", tier=1) is False
    assert r.zcard(Q.QUEUE_KEY_FMT.format(tier=1)) == 1


def test_enqueue_dedup_off_allows_double(r):
    assert Q.enqueue_url("https://x.com/a", tier=1, dedup=False) is True
    assert Q.enqueue_url("https://x.com/a", tier=1, dedup=False) is True
    # Same job_id → ZADD just updates the score; cardinality stays at 1.
    assert r.zcard(Q.QUEUE_KEY_FMT.format(tier=1)) == 1


def test_enqueue_invalid_tier_raises(r):
    with pytest.raises(Q.QueueError, match="invalid tier"):
        Q.enqueue_url("https://x.com/a", tier=5)
    with pytest.raises(Q.QueueError):
        Q.enqueue_url("https://x.com/a", tier=0)


def test_enqueue_metadata_round_trips(r):
    Q.enqueue_url("https://x.com/a", tier=2, metadata={"category": "AI", "letter": "A"})
    job = Q.pop_one(2)
    assert job is not None
    assert job.metadata == {"category": "AI", "letter": "A"}


# ---------------------------------------------------------------------------
# pop_one


def test_pop_one_empty_returns_none(r):
    assert Q.pop_one(1) is None


def test_pop_one_sets_inflight_with_ttl_and_bumps_attempts(r):
    Q.enqueue_url("https://x.com/a", tier=1)
    job = Q.pop_one(1, lease_seconds=600)
    assert job is not None
    assert job.attempts == 1
    inflight_key = Q.INFLIGHT_KEY_FMT.format(job_id=job.job_id)
    assert r.exists(inflight_key) == 1
    ttl = r.ttl(inflight_key)
    assert 0 < ttl <= 600
    assert r.sismember(Q.INFLIGHT_SET_KEY, job.job_id)


def test_pop_one_fifo_order(r):
    Q.enqueue_url("https://x.com/a", tier=1)
    time.sleep(0.005)
    Q.enqueue_url("https://x.com/b", tier=1)
    j1 = Q.pop_one(1)
    j2 = Q.pop_one(1)
    assert j1 is not None and j2 is not None
    assert j1.url.endswith("/a")
    assert j2.url.endswith("/b")


def test_pop_one_priority_overrides_fifo(r):
    Q.enqueue_url("https://x.com/low", tier=1, priority=0.0)
    Q.enqueue_url("https://x.com/high", tier=1, priority=10.0)
    j1 = Q.pop_one(1)
    assert j1 is not None
    assert j1.url.endswith("/high")


def test_pop_one_invalid_tier_raises(r):
    with pytest.raises(Q.QueueError):
        Q.pop_one(99)


# ---------------------------------------------------------------------------
# complete


def test_complete_clears_lease_and_job_keys(r):
    Q.enqueue_url("https://x.com/a", tier=1)
    job = Q.pop_one(1)
    assert job is not None
    Q.complete(job)
    assert r.exists(Q.INFLIGHT_KEY_FMT.format(job_id=job.job_id)) == 0
    assert r.exists(Q.JOB_KEY_FMT.format(job_id=job.job_id)) == 0
    assert not r.sismember(Q.INFLIGHT_SET_KEY, job.job_id)


# ---------------------------------------------------------------------------
# fail


def test_fail_no_escalation_lands_in_dead(r):
    Q.enqueue_url("https://x.com/a", tier=1)
    job = Q.pop_one(1)
    assert job is not None
    Q.fail(job, reason="boom")
    assert r.llen(Q.DEAD_KEY) == 1
    payload = json.loads(r.lindex(Q.DEAD_KEY, 0))
    assert payload["failure_reason"] == "boom"
    assert not r.sismember(Q.INFLIGHT_SET_KEY, job.job_id)
    assert r.exists(Q.INFLIGHT_KEY_FMT.format(job_id=job.job_id)) == 0
    assert r.exists(Q.JOB_KEY_FMT.format(job_id=job.job_id)) == 0


def test_fail_escalate_to_tier4_lands_in_escalate(r):
    Q.enqueue_url("https://x.com/a", tier=3)
    job = Q.pop_one(3)
    assert job is not None
    Q.fail(job, reason="needs t4", escalate_to_tier=4)
    assert r.llen(Q.ESCALATE_KEY_FMT.format(tier=4)) == 1
    assert r.llen(Q.DEAD_KEY) == 0


def test_fail_invalid_escalation_tier_raises(r):
    Q.enqueue_url("https://x.com/a", tier=1)
    job = Q.pop_one(1)
    assert job is not None
    with pytest.raises(Q.QueueError, match="invalid escalation tier"):
        Q.fail(job, reason="x", escalate_to_tier=2)
    with pytest.raises(Q.QueueError):
        Q.fail(job, reason="x", escalate_to_tier=99)


# ---------------------------------------------------------------------------
# extend_lease


def test_extend_lease_extends_ttl(r):
    Q.enqueue_url("https://x.com/a", tier=1)
    job = Q.pop_one(1, lease_seconds=10)
    assert job is not None
    Q.extend_lease(job, seconds=120)
    ttl = r.ttl(Q.INFLIGHT_KEY_FMT.format(job_id=job.job_id))
    assert ttl > 10


def test_extend_lease_after_expiry_raises(r):
    Q.enqueue_url("https://x.com/a", tier=1)
    job = Q.pop_one(1, lease_seconds=10)
    assert job is not None
    # Simulate expiry by deleting the inflight key.
    r.delete(Q.INFLIGHT_KEY_FMT.format(job_id=job.job_id))
    with pytest.raises(Q.QueueError, match="lease expired"):
        Q.extend_lease(job, seconds=60)


# ---------------------------------------------------------------------------
# reset_inflight


def test_reset_inflight_requeues_all(r):
    for i in range(5):
        Q.enqueue_url(f"https://x.com/{i}", tier=1)
    jobs = [Q.pop_one(1) for _ in range(5)]
    assert all(j is not None for j in jobs)
    assert r.scard(Q.INFLIGHT_SET_KEY) == 5
    assert r.zcard(Q.QUEUE_KEY_FMT.format(tier=1)) == 0

    n = Q.reset_inflight()
    assert n == 5
    assert r.scard(Q.INFLIGHT_SET_KEY) == 0
    assert r.zcard(Q.QUEUE_KEY_FMT.format(tier=1)) == 5
    for j in jobs:
        assert r.exists(Q.INFLIGHT_KEY_FMT.format(job_id=j.job_id)) == 0


# ---------------------------------------------------------------------------
# dead_letter_drain


def test_dead_letter_drain_non_destructive(r):
    Q.enqueue_url("https://x.com/a", tier=1)
    Q.enqueue_url("https://x.com/b", tier=1)
    j1 = Q.pop_one(1)
    j2 = Q.pop_one(1)
    Q.fail(j1, reason="r1")
    Q.fail(j2, reason="r2")
    assert r.llen(Q.DEAD_KEY) == 2

    drained = list(Q.dead_letter_drain())
    assert len(drained) == 2
    assert r.llen(Q.DEAD_KEY) == 2  # iteration is read-only
    urls = {j.url for j in drained}
    assert urls == {"https://x.com/a", "https://x.com/b"}

    # iterate again — still all there
    drained2 = list(Q.dead_letter_drain())
    assert len(drained2) == 2


# ---------------------------------------------------------------------------
# enqueue_seeds


def _bundle_with(seed_urls, external_urls):
    return PromptsBundle(
        prompts={},
        categories={},
        seed_urls=seed_urls,
        parsers=[],
        external_urls=external_urls,
    )


def test_enqueue_seeds_counts_and_idempotent(r):
    bundle = _bundle_with(
        seed_urls=[
            SeedURL(url="https://wikicfp.com/cfp/c1", kind="category", category=Category.AI),
            SeedURL(url="https://wikicfp.com/series/A", kind="series_index", letter="A"),
        ],
        external_urls=[
            "https://aideadlin.es/",
            "https://huggingface.co/papers",
        ],
    )
    n = Q.enqueue_seeds(bundle)
    assert n == 4
    assert r.zcard(Q.QUEUE_KEY_FMT.format(tier=1)) == 4

    # Re-running with the same bundle is a no-op via dedup gate.
    n2 = Q.enqueue_seeds(bundle)
    assert n2 == 0
    assert r.zcard(Q.QUEUE_KEY_FMT.format(tier=1)) == 4


def test_enqueue_seeds_metadata_propagates(r):
    bundle = _bundle_with(
        seed_urls=[
            SeedURL(url="https://wikicfp.com/cfp/c1", kind="category", category=Category.AI),
            SeedURL(url="https://wikicfp.com/series/B", kind="series_index", letter="B"),
        ],
        external_urls=["https://aideadlin.es/"],
    )
    Q.enqueue_seeds(bundle)
    seen = {}
    while True:
        j = Q.pop_one(1)
        if j is None:
            break
        seen[j.url] = j.metadata
    assert seen["https://wikicfp.com/cfp/c1"] == {
        "source_block": "category",
        "category": "AI",
    }
    assert seen["https://wikicfp.com/series/B"] == {
        "source_block": "series_index",
        "letter": "B",
    }
    assert seen["https://aideadlin.es/"] == {"source_block": "external"}


# ---------------------------------------------------------------------------
# cursor + rate limit


def test_cursor_set_get(r):
    assert Q.get_cursor("wikicfp") is None
    Q.set_cursor("wikicfp", "page=42")
    assert Q.get_cursor("wikicfp") == "page=42"


def test_rate_limit_acquire_blocks_within_window(r):
    assert Q.rate_limit_acquire("example.com", crawl_delay_s=5) is True
    assert Q.rate_limit_acquire("example.com", crawl_delay_s=5) is False
    # different domain not blocked
    assert Q.rate_limit_acquire("other.com", crawl_delay_s=5) is True
