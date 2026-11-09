"""The number check must pass honest formatting and catch invented numbers. No API calls."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build import check_digest  # noqa: E402

FACTS = {
    "as_of": "2026-10-31",
    "open_usd": 454200,
    "count": 40,
    "rate": 0.1833,
    "other_reps_pct": 54,
    "change": -3,
    "owner": "Danielle Johnson (R01, 11 deals, $114,500)",
}


def errors(text):
    return check_digest(text, FACTS)["errors"]


def test_formatting_variants_pass():
    text = ("1. $454,200 in 40 deals ($454k, $454.2k, $0.5M). R02 converts 18% "
            "(18.3%) vs 54%. Down 3. Largest book: 11 deals, $114,500. As of 2026-10-31.")
    assert errors(text) == []


def test_invented_numbers_fail():
    errs = errors("Pipeline grew 37% to $1.2M across 41 deals.")
    assert "number '37%' is not in facts.json" in errs
    assert "number '$1.2M' is not in facts.json" in errs
    assert "number '41' is not in facts.json" in errs


def test_wrong_rounding_fails():
    # 454200 rounds to $454k, never $455k
    assert errors("$455k at risk.") == ["number '$455k' is not in facts.json"]


def test_unknown_date_and_number_word_fail():
    errs = errors("Due 2026-11-07, about twenty deals.")
    assert "date 2026-11-07 is not in facts.json" in errs
    assert "number word 'twenty' is not in facts.json" in errs


def test_ids_and_list_markers_are_not_numbers():
    assert errors("1. Call R02 about D0589.\n2. Then L01911.\n3. Done.") == []


def test_style_rules():
    long_text = "word " * 251
    assert any("words; the limit" in e for e in errors(long_text))
    assert "hype word 'massive'" in errors("A massive week!")
