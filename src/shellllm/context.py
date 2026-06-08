"""Build a short, factual context block to prepend to an agent system prompt.

The model has a training cutoff; without a hint of the current date it
will interpret "latest", "today", or "recent" against its priors instead
of the actual calendar. The prelude gives it wall-clock awareness — and
a couple of other bits (OS, timezone) that occasionally matter for shell
or web-search answers.

Kept deliberately small: every prelude token competes with the user's
question for context budget.
"""

from __future__ import annotations

import platform
from datetime import datetime


def build_prelude(now: datetime | None = None) -> str:
    """Return a multi-line context block. ``now`` is injectable for tests."""
    dt = now or datetime.now().astimezone()
    tz = dt.strftime("%Z") or dt.strftime("%z") or "local"
    return "\n".join(
        [
            "Context (real-world facts, not part of the user's question):",
            f"- Current date: {dt.strftime('%Y-%m-%d')} ({dt.strftime('%A')})",
            f"- Local time: {dt.strftime('%H:%M')} {tz}",
            f"- Host OS: {platform.system()} {platform.release()}",
            "Use these when interpreting words like 'today', 'latest', or 'recent'.",
        ]
    )
