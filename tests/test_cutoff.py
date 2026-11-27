from datetime import UTC, datetime

import pytest

from wheelgap.cutoff import before_cutoff, is_duration, parse_cutoff, parse_span, span_before


def test_date_includes_its_whole_local_day():
    cutoff = parse_cutoff("2025-01-01")
    expected = datetime(2025, 1, 2).astimezone(UTC)
    assert cutoff == expected


def test_timestamp_normalizes_its_explicit_offset():
    assert parse_cutoff("2025-01-01T05:30:00+05:30") == datetime(2025, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("offset", ["+00:60", "-00:60", "+05:99"])
def test_timestamp_refuses_invalid_offset_minutes(offset):
    with pytest.raises(ValueError):
        parse_cutoff(f"2025-01-01T00:00:00{offset}")


def test_cutoff_excludes_equal_and_later_artifacts():
    files = [
        {"filename": "old.whl", "upload_time_iso_8601": "2024-12-31T23:59:59Z"},
        {"filename": "equal.whl", "upload_time_iso_8601": "2025-01-01T00:00:00Z"},
        {"filename": "later.whl", "upload_time_iso_8601": "2025-01-01T00:00:01Z"},
    ]
    assert before_cutoff(files, parse_cutoff("2025-01-01T00:00:00Z")) == files[:1]


def test_artifact_times_are_checked_separately_for_one_release():
    files = [
        {"filename": "old.tar.gz", "upload_time_iso_8601": "2024-12-31T00:00:00Z"},
        {"filename": "late.whl", "upload_time_iso_8601": "2025-01-02T00:00:00Z"},
    ]
    assert before_cutoff(files, parse_cutoff("2025-01-01T00:00:00Z")) == files[:1]


def test_range_ignores_timestamps_of_releases_outside_its_constraint():
    from wheelgap.pypi import _newest_matching

    older = {"filename": "dated-1.0-py3-none-any.whl"}
    matching = {"filename": "dated-2.0-py3-none-any.whl", "upload_time_iso_8601": "2024-01-01T00:00:00Z"}
    assert _newest_matching(
        {"releases": {"1.0": [older], "2.0": [matching]}}, ">=2", "3.13", parse_cutoff("2025-01-01")
    ) == ("2.0", [matching])


@pytest.mark.parametrize("value", ["9999-12-31", "0001-01-01T00:00:00+01:00", "2025-01-01T00:00:00.1234567890Z"])
def test_out_of_range_cutoffs_are_refused_not_a_crash(value):
    with pytest.raises(ValueError):
        parse_cutoff(value)


def test_cutoff_compares_whole_milliseconds_as_uv_does():
    files = [{"filename": "same-millisecond.whl", "upload_time_iso_8601": "2025-01-01T00:00:00.000400Z"}]
    assert before_cutoff(files, parse_cutoff("2025-01-01T00:00:00.000900Z")) == []


NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
SECOND = 1_000_000_000
HOUR, DAY = 3_600 * SECOND, 86_400 * SECOND

# Every row here was checked against uv 0.13.1: `uv lock` on a project with `exclude-newer = TEXT` writes the span
# it read to uv.lock as `exclude-newer-span`, in ISO 8601 form, and warns about a value it refuses.
UV_READS = [
    ("3 days", 3 * DAY),
    ("3 day", 3 * DAY),
    ("3days", 3 * DAY),
    ("3d", 3 * DAY),
    ("3\tdays", 3 * DAY),
    ("3 days ", 3 * DAY),  # spaces at the end are fine; at the start they aren't
    ("1 week", 7 * DAY),
    ("2 wks", 14 * DAY),
    ("1w", 7 * DAY),
    ("1w 2d", 9 * DAY),
    ("1w2d3h4m5s6ms7us8ns", 9 * DAY + 3 * HOUR + 245 * SECOND + 6_007_008),
    ("24 hours", DAY),
    ("36 hrs", 36 * HOUR),
    ("90 mins", 90 * 60 * SECOND),
    ("3 m", 180 * SECOND),  # m is minutes; months are mo
    ("2 hours, 30 minutes", 9_000 * SECOND),
    ("1.5h", 5_400 * SECOND),
    ("1,5 hours", 5_400 * SECOND),
    ("1h 1.5m", HOUR + 90 * SECOND),
    ("1.5 ms", 1_500_000),
    ("0.0000005 ms", 0),  # rounded towards zero
    ("5µs", 5_000),
    ("2d 12:30:00", 2 * DAY + 45_000 * SECOND),
    ("1:02:03.5", HOUR + 123 * SECOND + SECOND // 2),
    ("1:99:99", HOUR + 99 * 60 * SECOND + 99 * SECOND),
    ("3 days ago", 3 * DAY),
    ("-3 days", 3 * DAY),
    ("+3 days", 3 * DAY),
    ("0 years 3 days", 3 * DAY),
    ("0 days", 0),
    ("00000000000000000003 days", 3 * DAY),
    ("P3D", 3 * DAY),
    ("p3d", 3 * DAY),
    ("-P3D", 3 * DAY),
    ("P1W", 7 * DAY),
    ("P1W2D", 9 * DAY),
    ("PT24H", DAY),
    ("PT90M", 5_400 * SECOND),
    ("PT1.5H", 5_400 * SECOND),
    ("pt1,5h", 5_400 * SECOND),
    ("P1DT2H3M4.5S", DAY + 2 * HOUR + 184 * SECOND + SECOND // 2),
    ("P0Y3D", 3 * DAY),
    ("PT0S", 0),
    ("9223372036854775807 ns", 9_223_372_036_854_775_807),
    ("4000000 days", 4_000_000 * DAY),
]
UV_REFUSES = [
    "",
    " ",
    " 3 days",
    "3",
    "days",
    "three days",
    "3 Days",
    "3 dayz",
    "3 fortnights",
    "3 days!",
    "- 3 days",
    "-3 days ago",
    "3 days ago ",
    "3 daysago",
    "1 day and 2 hours",
    "1 day 1 day",
    "30m 1h",
    "1d,2h",
    "1d, ",
    "1.5 days",
    "1.5 weeks",
    "1.5 ns",
    "1.5h 3m",
    "1. h",
    ".5h",
    "1.1234567891 h",
    "1:00",
    "1h 1:00:00",
    "5μs",  # the Greek letter mu, not the micro sign
    "1 month",
    "3 mo",
    "1 year",
    "3 y",
    "P1M",
    "P1Y",
    "P",
    "PT",
    "P1DT",
    "P1H",
    "P1.5D",
    "P1D1W",
    "PT1S1H",
    "PT1.5H30M",
    "P3D ",
    " P3D",
    "P-3D",
    "P1W ago",
    "000000000000000000000003 days",  # more than 21 digits
    "18446744073709551616 ns",
    "9223372036854775808 ns",
    "7304485 days",
    "1043498 weeks",
    "175307617 hours",
]


@pytest.mark.parametrize("text, nanoseconds", UV_READS)
def test_duration_is_read_as_uv_reads_it(text, nanoseconds):
    assert parse_span(text) == nanoseconds
    assert is_duration(text)
    assert parse_cutoff(text, NOW) == span_before(nanoseconds, NOW)


@pytest.mark.parametrize("text", UV_REFUSES)
def test_duration_uv_refuses_is_refused(text):
    with pytest.raises(ValueError):
        parse_cutoff(text, NOW)
    assert not is_duration(text)


def test_duration_counts_back_from_now_in_utc():
    assert parse_cutoff("3 days", NOW) == datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
    assert parse_cutoff("1 week", NOW) == datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    assert parse_cutoff("36 hours", NOW) == datetime(2026, 11, 20, 0, 0, 0, tzinfo=UTC)
    # A day is 24 hours whatever the local time zone does: 2026-03-29 is the day Europe's clocks go forward.
    after = datetime.fromisoformat("2026-03-29T12:00:00+02:00")
    assert parse_cutoff("1 day", after) == datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC)


def test_duration_uses_the_current_time_by_default(monkeypatch):
    from wheelgap import cutoff

    monkeypatch.setattr(cutoff, "current_time", lambda: NOW)
    assert parse_cutoff("P3D") == datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
    monkeypatch.undo()
    before = datetime.now(UTC)
    assert before <= parse_cutoff("0 days") <= datetime.now(UTC)


def test_duration_is_not_tried_for_dates_and_timestamps():
    # uv tries a timestamp, then a date, then a duration; these keep their answers and their errors.
    assert parse_cutoff("2025-01-01T00:00:00Z", NOW) == datetime(2025, 1, 1, tzinfo=UTC)
    assert parse_cutoff("2025-01-01", NOW) == datetime(2025, 1, 2).astimezone(UTC)
    assert not is_duration("2025-01-01") and not is_duration("2025-01-01T00:00:00Z")
    for text in ("2025-02-30", "2025-01-01T00:00:00", "2025-1-1"):
        with pytest.raises(ValueError) as error:
            parse_cutoff(text, NOW)
        assert "duration" not in str(error.value)


def test_duration_cutoff_is_floored_like_uv_compares():
    # 1.5 microseconds back from a whole second: uv keeps nanoseconds and compares whole milliseconds.
    cutoff = parse_cutoff("1500 ns", NOW)
    assert cutoff == datetime(2026, 11, 21, 11, 59, 59, 999998, tzinfo=UTC)
    files = [
        {"filename": "a.whl", "upload_time_iso_8601": "2026-11-21T11:59:59.998999Z"},
        {"filename": "b.whl", "upload_time_iso_8601": "2026-11-21T11:59:59.999000Z"},
    ]
    assert before_cutoff(files, cutoff) == files[:1]


def test_duration_reaching_before_year_one_is_before_every_upload():
    # uv accepts it (its timestamps go back to year -9999); a datetime can't hold the cutoff it gives.
    cutoff = parse_cutoff("800000 days", NOW)
    assert cutoff == datetime.min.replace(tzinfo=UTC)
    assert before_cutoff([{"filename": "a.whl", "upload_time_iso_8601": "1999-01-01T00:00:00Z"}], cutoff) == []


def test_longest_duration_uv_subtracts_from_now():
    # Found with uv 0.13.1 and UV_INTERNAL__TEST_CURRENT_TIMESTAMP=2026-11-21T12:00:00Z: 379500285601 seconds is
    # accepted and one more is "too large to subtract from current time".
    assert parse_cutoff("379500285601 seconds", NOW) == datetime.min.replace(tzinfo=UTC)
    assert parse_cutoff("4392364 days", NOW) == datetime.min.replace(tzinfo=UTC)
    for text in ("379500285602 seconds", "4392365 days", "105416747 hours"):
        with pytest.raises(ValueError, match="too long to subtract from the current time"):
            parse_cutoff(text, NOW)
