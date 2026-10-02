"""The analytics periods (Addis Ababa days): today, this week, this month, 7d, 30d."""
from datetime import datetime, timezone

import pytest

from app.agents.analytics import ADDIS, period_bounds


def addis(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=ADDIS)


# Friday 2 October 2026, 22:30 UTC = Saturday 3 October, 01:30 in Addis.
LATE_FRIDAY_UTC = datetime(2026, 10, 2, 22, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("period", "start"),
    [
        ("today", addis(2026, 10, 3)),
        ("week", addis(2026, 9, 28)),  # Monday
        ("month", addis(2026, 10, 1)),
        ("7d", addis(2026, 9, 27)),
        ("30d", addis(2026, 9, 4)),
    ],
)
def test_periods_start_in_addis_time(period, start):
    assert period_bounds(period, LATE_FRIDAY_UTC) == (start, addis(2026, 10, 4))


def test_week_on_a_monday_is_just_today():
    monday = datetime(2026, 9, 28, 9, 0, tzinfo=ADDIS)
    assert period_bounds("week", monday) == (addis(2026, 9, 28), addis(2026, 9, 29))
