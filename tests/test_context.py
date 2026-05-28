"""Tests for the system-prompt prelude builder."""

from __future__ import annotations

from datetime import datetime, timezone

from shellllm.context import build_prelude


def test_prelude_includes_iso_date():
    fixed = datetime(2026, 5, 28, 14, 30, tzinfo=timezone.utc)
    out = build_prelude(now=fixed)
    assert "2026-05-28" in out


def test_prelude_includes_weekday():
    # 2026-05-28 is a Thursday.
    fixed = datetime(2026, 5, 28, tzinfo=timezone.utc)
    out = build_prelude(now=fixed)
    assert "Thursday" in out


def test_prelude_includes_time():
    fixed = datetime(2026, 5, 28, 14, 7, tzinfo=timezone.utc)
    out = build_prelude(now=fixed)
    assert "14:07" in out


def test_prelude_mentions_host_os():
    out = build_prelude(now=datetime(2026, 5, 28, tzinfo=timezone.utc))
    assert "Host OS" in out


def test_prelude_guides_interpretation():
    # The model should know what to do with the date — without that hint
    # the bare numbers are just noise.
    out = build_prelude(now=datetime(2026, 5, 28, tzinfo=timezone.utc))
    assert "today" in out.lower() or "latest" in out.lower()


def test_default_now_does_not_crash():
    # No fixed clock — just make sure the no-arg path returns a string.
    out = build_prelude()
    assert isinstance(out, str)
    assert len(out) > 0
