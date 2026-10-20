"""Retention for immutable, timestamped snapshots of completed writes."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

_NAME = re.compile(
    r"(?P<prefix>txncount|storage-test)-(?P<stamp>\d{8}T\d{12}Z)-"
    r"[a-f0-9]{32}-(?P<state>pending|committed)\.beancount"
)


def backup_name(prefix: str) -> str:
    return f"{prefix}-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{uuid4().hex}-pending.beancount"


def backup_time(name: str, prefix: str, *, pending: bool = False) -> datetime | None:
    match = _NAME.fullmatch(name)
    if (
        match is None
        or match["prefix"] != prefix
        or match["state"] != ("pending" if pending else "committed")
    ):
        return None
    try:
        return datetime.strptime(match["stamp"], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)
    except ValueError:
        return None


@dataclass(frozen=True)
class BackupEntry:
    name: str
    size: int
    created: datetime


@dataclass(frozen=True)
class BackupPlan:
    remove: tuple[BackupEntry, ...]

    @property
    def size(self) -> int:
        return sum(item.size for item in self.remove)


def retention_plan(entries: list[BackupEntry], now: datetime) -> BackupPlan:
    now = now.astimezone(UTC)
    ordered = sorted(entries, key=lambda item: (item.created, item.name), reverse=True)
    keep = {item.name for item in ordered[:20]}
    days = set()
    months = set()
    for item in ordered:
        created = item.created.astimezone(UTC)
        age = (now.date() - created.date()).days
        month_age = (now.year - created.year) * 12 + now.month - created.month
        if created > now:
            keep.add(item.name)
        if 0 <= age < 30 and created.date() not in days:
            days.add(created.date())
            keep.add(item.name)
        month = (created.year, created.month)
        if 0 <= month_age < 12 and month not in months:
            months.add(month)
            keep.add(item.name)
    return BackupPlan(tuple(item for item in ordered if item.name not in keep))
