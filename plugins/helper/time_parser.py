"""Human-friendly duration parsing, e.g. "1h30m", "2 days", "45s", "600"."""
import re

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}
_TOKEN_RE = re.compile(r"(\d+)\s*([a-zA-Z]*)")


def parse_time(time_str: str) -> int:
    """Parse a duration string into seconds. Raises ValueError if it can't."""
    time_str = time_str.strip()
    if not time_str:
        raise ValueError("Empty duration")

    total = 0
    matched_any = False
    for number, unit in _TOKEN_RE.findall(time_str):
        if not number:
            continue
        unit = unit.lower() or "s"
        if unit not in _UNITS:
            raise ValueError(f"Unknown time unit: {unit!r}")
        total += int(number) * _UNITS[unit]
        matched_any = True

    if not matched_any:
        raise ValueError(f"Could not parse duration: {time_str!r}")
    return total


def format_time(seconds: int) -> str:
    """Convert seconds into a human readable string, e.g. '1 hour 5 minutes'."""
    seconds = int(seconds)
    if seconds <= 0:
        return "0 seconds"

    periods = [("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1)]
    parts = []
    for name, size in periods:
        value, seconds = divmod(seconds, size)
        if value:
            parts.append(f"{value} {name}{'s' if value != 1 else ''}")
    return " ".join(parts) if parts else "0 seconds"
