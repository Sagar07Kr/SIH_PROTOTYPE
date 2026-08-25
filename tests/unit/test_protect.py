"""Placeholder masking must be exact, including under adversarial input."""
from __future__ import annotations

import pytest

from backend.translation.protect import (TOKEN_RE, mask, repair_instruction,
                                         restore, verify)

CASES = [
    "Contact a.b+tag@example.co.uk before 2026-04-01.",
    "See https://x.example/path?q=1&r=2#frag and www.example.org.",
    "Cost €2,500.00 rose 12.5% to ₹3,10,000 (ref JSP/2026/1184).",
    "Use `curl -X POST` with $E=mc^2$ per §17(2) [3], [4-6].",
    "Server 10.0.0.14 at /var/log/app.log, 250 ms, 4.5 kg, API v2.",
    "12.03.2026 · Abs. 4 · ISO 8601 · 2.500 Datensätze",
]


@pytest.mark.parametrize("text", CASES)
def test_round_trip_is_lossless(text: str) -> None:
    m = mask(text)
    assert m.count > 0
    back, missing = restore(m.text, m)
    assert not missing
    assert back == text


def test_placeholders_are_unique_and_well_formed() -> None:
    m = mask(CASES[2])
    tokens = TOKEN_RE.findall(m.text)
    assert len(tokens) == len(set(tokens)) == m.count


def test_adversarial_text_that_already_contains_brackets() -> None:
    # A source document that itself uses the bracket characters must not be
    # able to forge or collide with our placeholders.
    text = "Weird ⟦P0⟧ marker and a real number 42."
    m = mask(text)
    out, missing = restore(m.text, m)
    assert "42" in out
    assert missing == []


def test_verify_detects_dropped_and_duplicated_tokens() -> None:
    m = mask("Total 1,234 units")
    token = next(iter(m.tokens))
    assert verify(m, "Gesamt units") == [token]          # dropped
    assert verify(m, f"{token} {token}") == [token]      # duplicated
    assert verify(m, f"Gesamt {token} Einheiten") == []


def test_locked_term_is_replaced_with_the_target() -> None:
    m = mask("The Layout Engine is fast.", {"Layout Engine": "Layout-Engine"})
    out, _ = restore(m.text, m)
    assert "Layout-Engine" in out
    assert "GLOSSARY_LOCKED_TERM" in m.kinds.values()


def test_repair_instruction_names_the_missing_tokens() -> None:
    assert "⟦P3⟧" in repair_instruction(["⟦P3⟧"])
