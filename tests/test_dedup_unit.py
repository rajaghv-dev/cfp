"""Unit tests for cfp/dedup.py -- no PostgreSQL required.

Covers acronym normalisation only; everything else needs the live cfp_postgres
container and lives in tests/test_dedup_pg.py.
"""
from __future__ import annotations

import pytest

from cfp.dedup import _normalise_acronym


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ICRA-2025",       "icra"),
        ("ICRA 25",         "icra"),
        ("ICRA",            "icra"),
        ("12th ICML 2025",  "icml"),
        ("the NeurIPS",     "neurips"),
        ("ACM/SIGCHI'24",   "acmsigchi"),
        ("",                ""),
        (None,              ""),
        ("CVPR.2025",       "cvpr"),
        ("21st AAAI",       "aaai"),
    ],
)
def test_normalise_acronym(raw, expected):
    assert _normalise_acronym(raw) == expected
