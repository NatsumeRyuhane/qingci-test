"""起草、检索、检查与分流（#6，提案 0003 §7–§13）。"""

from copilot.draft.drafter import Drafter, DraftResult, latest_player_message
from copilot.draft.stub import StubDrafter
from copilot.draft.timectx import (
    DEFAULT_LOOKBACK_DAYS,
    WindowMatch,
    build_now,
    build_time_context,
    candidate_datetimes,
    known_at_for,
    match_notice,
    match_template_validity,
    match_window,
    parse_problem_time,
)

__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "DraftResult",
    "Drafter",
    "StubDrafter",
    "WindowMatch",
    "build_now",
    "build_time_context",
    "candidate_datetimes",
    "known_at_for",
    "latest_player_message",
    "match_notice",
    "match_template_validity",
    "match_window",
    "parse_problem_time",
]
