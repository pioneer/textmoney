"""Timezone conversion at the UI boundary; journal dates remain accounting dates."""

import re
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.ledger import LedgerError


def named_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise LedgerError("crud_timezone_invalid") from exc


def local_datetime(value: datetime, zone: str = "system") -> datetime:
    return value.astimezone(None if zone == "system" else named_zone(zone))


def wall_timestamp(day: date, clock: str, zone: str = "system") -> str:
    try:
        if not re.fullmatch(r"\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?", clock):
            raise ValueError
        wall = datetime.fromisoformat(f"{day.isoformat()}T{clock}")
        if wall.tzinfo is not None or "T" in clock or " " in clock:
            raise ValueError
    except ValueError as exc:
        raise LedgerError("crud_time_invalid") from exc
    candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        naive = wall.replace(fold=fold)
        candidate = (
            naive.astimezone() if zone == "system" else naive.replace(tzinfo=named_zone(zone))
        )
        instant = candidate.astimezone(UTC)
        if local_datetime(instant, zone).replace(tzinfo=None) == wall:
            candidates[instant] = candidate
    if not candidates:
        raise LedgerError("crud_time_missing")
    if len(candidates) != 1:
        raise LedgerError("crud_time_ambiguous")
    return next(iter(candidates.values())).isoformat()
