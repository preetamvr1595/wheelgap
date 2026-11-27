"""uv's exclude-newer upload cutoffs (#521): the forms uv accepts, and the per-file filter it applies.

A cutoff is a timestamp, a date, or a duration ("3 days", "P3D") that uv counts back from the current time each
time it resolves. Durations are read with uv's grammar, which is the jiff crate's `Span` parser (see parse_span)."""

import re
from datetime import UTC, datetime, timedelta

_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)", re.ASCII
)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


class UploadTimeError(ValueError):
    pass


def timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise ValueError("expected an RFC 3339 timestamp with a timezone")
    try:
        return datetime.fromisoformat(value.upper()).astimezone(UTC)
    except (OverflowError, OSError):
        raise ValueError(f"{value} is out of range") from None


def current_time() -> datetime:
    """The time a duration counts back from: now, in UTC, as uv takes it (`Timestamp::now()`)."""
    return datetime.now(UTC)


# The units of a duration, smallest first, with their length in nanoseconds and the largest value jiff's Span
# holds for each (https://docs.rs/jiff/0.2.38/jiff/struct.Span.html#span-limits). uv subtracts the span from the
# current time in UTC, where a day is always 24 hours and a week 7 days. Years and months have no fixed length;
# uv refuses them.
_UNITS = {
    "nanosecond": (1, 9_223_372_036_854_775_807),
    "microsecond": (1_000, 631_107_417_600_000_000),
    "millisecond": (1_000_000, 631_107_417_600_000),
    "second": (1_000_000_000, 631_107_417_600),
    "minute": (60 * 1_000_000_000, 10_518_456_960),
    "hour": (3_600 * 1_000_000_000, 175_307_616),
    "day": (86_400 * 1_000_000_000, 7_304_484),
    "week": (7 * 86_400 * 1_000_000_000, 1_043_497),
    "month": (0, 239_976),
    "year": (0, 19_998),
}
_RANK = {unit: rank for rank, unit in enumerate(_UNITS)}
# The unit names of jiff's "friendly" format, in the order its parser tries them (the longest first: the text after
# a name isn't looked at, so "3 dayz" reads "day" and then fails on the "z"):
# https://github.com/BurntSushi/jiff/blob/0.2.38/crates/jiff/src/fmt/friendly/parser_label.rs
_LABELS: tuple[tuple[bytes, str], ...] = tuple(
    (label.encode("utf-8"), unit)
    for label, unit in (
        ("milliseconds", "millisecond"),
        ("microseconds", "microsecond"),
        ("nanoseconds", "nanosecond"),
        ("millisecond", "millisecond"),
        ("microsecond", "microsecond"),
        ("nanosecond", "nanosecond"),
        ("seconds", "second"),
        ("minutes", "minute"),
        ("\u00b5secs", "microsecond"),
        ("second", "second"),
        ("months", "month"),
        ("minute", "minute"),
        ("millis", "millisecond"),
        ("micros", "microsecond"),
        ("\u00b5sec", "microsecond"),
        ("years", "year"),
        ("weeks", "week"),
        ("usecs", "microsecond"),
        ("nsecs", "nanosecond"),
        ("nanos", "nanosecond"),
        ("msecs", "millisecond"),
        ("month", "month"),
        ("milli", "millisecond"),
        ("micro", "microsecond"),
        ("hours", "hour"),
        ("year", "year"),
        ("week", "week"),
        ("usec", "microsecond"),
        ("secs", "second"),
        ("nsec", "nanosecond"),
        ("nano", "nanosecond"),
        ("msec", "millisecond"),
        ("mins", "minute"),
        ("hour", "hour"),
        ("days", "day"),
        ("\u00b5s", "microsecond"),
        ("yrs", "year"),
        ("wks", "week"),
        ("sec", "second"),
        ("mos", "month"),
        ("min", "minute"),
        ("hrs", "hour"),
        ("day", "day"),
        ("yr", "year"),
        ("wk", "week"),
        ("us", "microsecond"),
        ("ns", "nanosecond"),
        ("ms", "millisecond"),
        ("mo", "month"),
        ("hr", "hour"),
        ("y", "year"),
        ("w", "week"),
        ("s", "second"),
        ("m", "minute"),
        ("h", "hour"),
        ("d", "day"),
    )
)
_ISO_DATE_UNITS = {b"y": "year", b"m": "month", b"w": "week", b"d": "day"}
_ISO_TIME_UNITS = {b"h": "hour", b"m": "minute", b"s": "second"}
_WHITESPACE = b" \t\n\r\x0c"
_FRACTION_NANOS = {  # the nanoseconds in a fraction (in billionths) of a unit; jiff rounds towards zero
    "hour": lambda fraction: fraction * 3_600,
    "minute": lambda fraction: fraction * 60,
    "second": lambda fraction: fraction,
    "millisecond": lambda fraction: fraction // 1_000,
    "microsecond": lambda fraction: fraction // 1_000_000,
}
# jiff's earliest Timestamp, -9999-01-02T01:59:59Z, in seconds since 1970: uv refuses a duration that reaches
# further back from the current time.
_EARLIEST_SECONDS = -377_705_023_201
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_FIRST = datetime.min.replace(tzinfo=UTC)


class _Span:
    """The units read so far, as jiff's DurationUnits keeps them:
    https://github.com/BurntSushi/jiff/blob/0.2.38/crates/jiff/src/fmt/util.rs"""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.smallest: str | None = None
        self.fraction: int | None = None

    def set(self, unit: str, value: int) -> None:
        if self.smallest is not None and _RANK[self.smallest] <= _RANK[unit]:
            raise ValueError(
                f"{unit}s can't follow {self.smallest}s: units go from the largest to the smallest, each once"
            )
        self.smallest = unit
        self.values[unit] = value

    def set_fraction(self, fraction: int) -> None:
        if self.smallest not in _FRACTION_NANOS:
            raise ValueError(f"a fraction of {self.smallest}s isn't allowed, only of hours down to microseconds")
        self.fraction = fraction

    def set_clock(self, hours: int, minutes: int, seconds: int, fraction: int | None) -> None:
        if self.smallest is not None and _RANK[self.smallest] <= _RANK["hour"]:
            raise ValueError(f"H:MM:SS can't follow {self.smallest}s")
        self.set("hour", hours)
        self.set("minute", minutes)
        self.set("second", seconds)
        if fraction is not None:
            self.set_fraction(fraction)

    def nanoseconds(self) -> int:
        if self.smallest is None:
            raise ValueError("it has no units")
        for unit, value in self.values.items():
            if value > _UNITS[unit][1]:
                raise ValueError(f"{value} {unit}s is too long")
        for unit in ("year", "month"):  # uv's own rule: jiff reads them, but they have no fixed length
            if self.values.get(unit):
                raise ValueError(f"uv doesn't allow {unit}s in a duration; use days instead")
        total = sum(value * _UNITS[unit][0] for unit, value in self.values.items())
        if self.fraction is not None:
            total += _FRACTION_NANOS[self.smallest](self.fraction)
        return total


def _number(data: bytes, at: int) -> tuple[int | None, int]:
    """The run of digits at `at`, at most 21 of them, as jiff's u64_prefix reads it."""
    end = at
    while end < len(data) and end - at <= 20 and 48 <= data[end] <= 57:
        end += 1
    if end == at:
        return None, at
    value = int(data[at:end])
    if value > 0xFFFF_FFFF_FFFF_FFFF:
        raise ValueError("a number in it is too big")
    return value, end


def _fraction(data: bytes, at: int) -> tuple[int, int]:
    """The digits after a `.` or `,` at `at - 1`, as billionths: one to nine of them."""
    end = at
    while end < len(data) and end - at <= 8 and 48 <= data[end] <= 57:
        end += 1
    if end == at:
        raise ValueError("expected digits after the decimal separator")
    return int(data[at:end].ljust(9, b"0")), end


def _spaces(data: bytes, at: int) -> int:
    while at < len(data) and data[at] in _WHITESPACE:
        at += 1
    return at


def _friendly_span(data: bytes) -> _Span:
    """jiff's "friendly" duration format: `3 days`, `1w 2d`, `2 hours, 30 minutes`, `1.5h`, `2d 12:30:00`, with
    `ago` or a sign, which uv ignores. Ported from
    https://github.com/BurntSushi/jiff/blob/0.2.38/crates/jiff/src/fmt/friendly/parser.rs"""
    span = _Span()
    signed = data[:1] in (b"+", b"-")
    value, at = _number(data, int(signed))
    if value is None:
        raise ValueError("expected a number at its start")
    after_comma = True
    while True:
        if data[at : at + 1] == b":":
            minutes, at = _number(data, at + 1)
            if minutes is None or data[at : at + 1] != b":":
                raise ValueError("expected H:MM:SS")
            seconds, at = _number(data, at + 1)
            if seconds is None:
                raise ValueError("expected H:MM:SS")
            fraction = None
            if data[at : at + 1] in (b".", b","):
                fraction, at = _fraction(data, at + 1)
            span.set_clock(value, minutes, seconds, fraction)
            break
        fraction = None
        if data[at : at + 1] in (b".", b","):
            fraction, at = _fraction(data, at + 1)
        at = _spaces(data, at)
        found = next(((label, unit) for label, unit in _LABELS if data.startswith(label, at)), None)
        if found is None:
            raise ValueError("expected a unit such as days or hours after a number")
        at, unit = at + len(found[0]), found[1]
        if data[at : at + 1] == b",":
            if data[at + 1 : at + 2] == b"" or data[at + 1] not in _WHITESPACE:
                raise ValueError("expected a space after a comma")
            at += 2
            after_comma = False
        span.set(unit, value)
        if fraction is not None:  # a fraction ends the duration: it's only allowed on the last unit
            span.set_fraction(fraction)
            break
        value, after = _number(data, _spaces(data, at))
        if value is None:
            break
        at, after_comma = after, True
    if not after_comma:
        raise ValueError("expected another unit after a comma")
    if at < len(data) and data[at] in _WHITESPACE:
        at = _spaces(data, at)
        if data.startswith(b"ago", at):
            if signed:
                raise ValueError("it has both a sign and `ago`")
            at += 3
    if at != len(data):
        raise ValueError(f"unexpected text at its end: {data[at:].decode('utf-8', 'replace')!r}")
    return span


def _iso_span(data: bytes) -> _Span:
    """An ISO 8601 duration as jiff reads one: `P3D`, `P1W2D`, `PT36H`, `P1DT1.5H`, in either case. Ported from
    https://github.com/BurntSushi/jiff/blob/0.2.38/crates/jiff/src/fmt/temporal/parser.rs"""
    span = _Span()
    at = int(data[:1] in (b"+", b"-")) + 1  # the caller saw the P
    while True:
        value, after = _number(data, at)
        if value is None:
            break
        unit = _ISO_DATE_UNITS.get(data[after : after + 1].lower())
        if unit is None:
            raise ValueError("expected Y, M, W or D after a number (hours, minutes and seconds go after a T)")
        span.set(unit, value)
        at = after + 1
    if data[at : at + 1] in (b"T", b"t"):
        at += 1
        while True:
            value, after = _number(data, at)
            if value is None:
                break
            fraction = None
            if data[after : after + 1] in (b".", b","):
                fraction, after = _fraction(data, after + 1)
            unit = _ISO_TIME_UNITS.get(data[after : after + 1].lower())
            if unit is None:
                raise ValueError("expected H, M or S after a number that follows the T")
            span.set(unit, value)
            at = after + 1
            if fraction is not None:
                span.set_fraction(fraction)
                break
        if span.smallest is None or _RANK[span.smallest] > _RANK["hour"]:
            raise ValueError("expected hours, minutes or seconds after the T")
    if at != len(data):
        raise ValueError(f"unexpected text at its end: {data[at:].decode('utf-8', 'replace')!r}")
    return span


def parse_span(value: str) -> int:
    """The length in nanoseconds of a duration uv accepts for exclude-newer, or ValueError.

    uv parses the text as a jiff `Span` (an ISO 8601 duration if it starts with P, after a sign; jiff's "friendly"
    format otherwise), refuses years and months, and subtracts its absolute value from the current time, so a
    sign or `ago` changes nothing:
    https://github.com/astral-sh/uv/blob/0.13.9/crates/uv-distribution-types/src/exclude_newer.rs#L217-L264
    """
    data = value.encode("utf-8")
    iso = data[int(data[:1] in (b"+", b"-")) :][:1] in (b"P", b"p")
    return (_iso_span(data) if iso else _friendly_span(data)).nanoseconds()


def span_before(nanoseconds: int, now: datetime) -> datetime:
    """The cutoff a duration gives at `now`. One uv accepts that reaches back before year 1, which a datetime can't
    hold, gives the earliest datetime: no file was uploaded before either."""
    since_epoch = now - _EPOCH
    now_nanoseconds = ((since_epoch.days * 86_400 + since_epoch.seconds) * 1_000_000 + since_epoch.microseconds) * 1_000
    cutoff = now_nanoseconds - nanoseconds
    if cutoff < _EARLIEST_SECONDS * 1_000_000_000:
        raise ValueError("it's too long to subtract from the current time")
    try:
        # Whole microseconds, rounded down: uv compares in whole milliseconds, rounded down too (before_cutoff).
        return _EPOCH + timedelta(microseconds=cutoff // 1_000)
    except OverflowError:
        return _FIRST


def is_duration(value: object) -> bool:
    """Whether uv reads `value` as a duration rather than a date or timestamp (it tries those first)."""
    if not isinstance(value, str) or _DATE.fullmatch(value) or _TIMESTAMP.fullmatch(value):
        return False
    try:
        parse_span(value)
    except ValueError:
        return False
    return True


def parse_cutoff(value: object, now: datetime | None = None) -> datetime:
    """An RFC 3339 timestamp; a date: as uv reads one, the midnight that ends it in the local time zone; or a
    duration, counted back from `now` (default: the current time), as uv counts it back each time it resolves."""
    if isinstance(value, str) and _DATE.fullmatch(value):
        try:
            return (datetime.fromisoformat(value) + timedelta(days=1)).astimezone(UTC)
        except (OverflowError, OSError):
            raise ValueError(f"{value} is out of range") from None
    try:
        return timestamp(value)
    except ValueError as not_a_timestamp:
        # uv tries a timestamp, then a date, then a duration. Text that starts like a date (2025-...) is one of
        # the first two written in a way wheelgap doesn't read, never a duration: say that.
        if not isinstance(value, str) or re.match(r"[+-]?\d{4}-", value):
            raise
        try:
            nanoseconds = parse_span(value)
        except ValueError as not_a_span:
            raise ValueError(
                f"{not_a_timestamp}, or a duration such as 3 days or P3D ({value!r} isn't one: {not_a_span})"
            ) from None
        return span_before(nanoseconds, current_time() if now is None else now)


def describe(cutoff: datetime) -> str:
    return cutoff.isoformat().replace("+00:00", "Z")


def _millisecond(moment: datetime) -> datetime:
    """uv compares upload times and cutoffs in whole milliseconds."""
    return moment.replace(microsecond=moment.microsecond - moment.microsecond % 1000)


def before_cutoff(files: list[dict], cutoff: datetime | None) -> list[dict]:
    """The files uploaded before `cutoff`, as uv keeps them: each file on its own, so a release can keep some."""
    if cutoff is None:
        return files
    limit = _millisecond(cutoff)
    kept = []
    for entry in files:
        try:
            uploaded = timestamp(entry.get("upload_time_iso_8601"))
        except ValueError:
            raise UploadTimeError(
                f"exclude-newer: {entry['filename']} has no valid upload_time_iso_8601; its age is unknown"
            ) from None
        if _millisecond(uploaded) < limit:
            kept.append(entry)
    return kept
