"""Private, bounded notification-import state; no journal writes or platform APIs."""

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.domain.notification_rules import MAX_NOTIFICATION_LENGTH, Rule
from app.infrastructure.notification_rule_codec import decode_rule, encode_rule

DAY = 24 * 60 * 60
PENDING_AGE = 30 * DAY
SAMPLE_AGE = 7 * DAY
RECORD_AGE = 90 * DAY
MAX_PENDING = 500
MAX_SAMPLES = 200
MAX_RECORDS = 10_000
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_SELECTED_APPS = 200
_STATUSES = frozenset(
    {
        "pending",
        "saving",
        "imported",
        "skipped",
        "ignored",
        "unmatched",
        "uncertain",
        "expired",
        "cleared",
    }
)

Mode = Literal["off", "manual", "background"]
Outcome = Literal["pending", "ignored", "unmatched"]
IntakeResult = Literal[
    "unselected",
    "off",
    "too_old",
    "duplicate",
    "updated",
    "changed",
    "uncertain",
    "capacity",
    "recorded",
]


class NotificationStoreError(RuntimeError):
    """Notification state cannot safely be used."""


class NotificationCapacityError(NotificationStoreError):
    """Pending notification capacity has been exhausted."""


@dataclass(frozen=True)
class IncomingNotification:
    app_id: str
    key: str
    posted: int
    version: str
    title: str | None
    body: str | None
    posted_millis: int | None = None


@dataclass(frozen=True)
class Evaluation:
    outcome: Outcome
    rule_name: str | None = None
    draft: dict[str, object] | None = None
    journal_id: str | None = None


@dataclass(frozen=True)
class QueueItem:
    id: int
    app_id: str
    key: str
    posted: int
    posted_millis: int
    first_seen: int
    status: str
    rule_name: str | None
    title: str | None
    body: str | None
    draft: dict[str, object] | None
    changed: bool
    journal_id: str | None
    version: str
    snapshot: str
    review_snapshot: str
    stale_rule: bool


@dataclass(frozen=True)
class StoredRule:
    id: str
    rule: Rule


class NotificationStore:
    """SQLite state scoped to app-private storage on one device."""

    def __init__(self, path: Path) -> None:
        self._db = sqlite3.connect(path, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA secure_delete = ON")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS notification_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                mode TEXT NOT NULL CHECK (mode IN ('off', 'manual', 'background')),
                last_prune INTEGER NOT NULL,
                expired_count INTEGER NOT NULL DEFAULT 0,
                last_checked INTEGER
            );
            INSERT OR IGNORE INTO notification_settings (id, mode, last_prune)
                VALUES (1, 'off', 0);
            CREATE TABLE IF NOT EXISTS notification_apps (
                app_id TEXT PRIMARY KEY,
                selected INTEGER NOT NULL CHECK (selected IN (0, 1)),
                cutoff INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS notification_items (
                id INTEGER PRIMARY KEY,
                app_id TEXT NOT NULL REFERENCES notification_apps(app_id),
                notification_key TEXT NOT NULL,
                posted INTEGER NOT NULL,
                posted_millis INTEGER NOT NULL,
                version TEXT NOT NULL,
                first_seen INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'saving', 'imported', 'skipped',
                               'ignored', 'unmatched', 'uncertain', 'expired', 'cleared')
                ),
                rule_name TEXT,
                rule_revision TEXT,
                title TEXT,
                body TEXT,
                draft TEXT,
                journal_id TEXT,
                editing INTEGER NOT NULL DEFAULT 0 CHECK (editing IN (0, 1)),
                changed INTEGER NOT NULL DEFAULT 0 CHECK (changed IN (0, 1)),
                UNIQUE (app_id, notification_key, posted_millis)
            );
            CREATE INDEX IF NOT EXISTS notification_age
                ON notification_items (first_seen, id);
            CREATE TABLE IF NOT EXISTS notification_rules (
                id TEXT PRIMARY KEY,
                app_id TEXT NOT NULL REFERENCES notification_apps(app_id),
                position INTEGER NOT NULL,
                rule TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS notification_rule_order
                ON notification_rules (app_id, position, id);
            """
        )
        with self._transaction():
            columns = {
                row["name"] for row in self._db.execute("PRAGMA table_info(notification_settings)")
            }
            if "last_checked" not in columns:
                self._db.execute(
                    "ALTER TABLE notification_settings ADD COLUMN last_checked INTEGER"
                )
            item_columns = {
                row["name"] for row in self._db.execute("PRAGMA table_info(notification_items)")
            }
            if "rule_revision" not in item_columns:
                self._db.execute("ALTER TABLE notification_items ADD COLUMN rule_revision TEXT")

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.commit()
        finally:
            if self._db.in_transaction:
                self._db.rollback()

    def set_mode(self, mode: Mode) -> None:
        if mode not in ("off", "manual", "background"):
            raise NotificationStoreError("Invalid monitoring mode.")
        with self._transaction():
            self._db.execute("UPDATE notification_settings SET mode = ? WHERE id = 1", (mode,))

    def mode(self) -> Mode:
        value = self._db.execute("SELECT mode FROM notification_settings WHERE id = 1").fetchone()[
            "mode"
        ]
        if value == "off":
            return "off"
        if value == "manual":
            return "manual"
        if value == "background":
            return "background"
        raise NotificationStoreError("Invalid stored monitoring mode.")

    def last_checked(self) -> int | None:
        value = self._db.execute(
            "SELECT last_checked FROM notification_settings WHERE id = 1"
        ).fetchone()["last_checked"]
        if value is not None and (type(value) is not int or value <= 0):
            raise NotificationStoreError("Invalid last notification check time.")
        return value

    def record_check(self, now: int, selected: tuple[str, ...]) -> None:
        """Record a completed explicit check only if its source selection is unchanged."""
        with self._transaction():
            if now <= 0 or self.mode() == "off" or self.selected_apps() != selected:
                raise NotificationStoreError("Notification settings changed during the check.")
            previous = self.last_checked()
            if previous is not None and now < previous:
                raise NotificationStoreError("Device clock moved backwards; review is required.")
            self._db.execute(
                "UPDATE notification_settings SET last_checked = ? WHERE id = 1", (now,)
            )

    def set_app_selected(self, app_id: str, selected: bool) -> None:
        self._validate_identity(app_id, "App identity")
        with self._transaction():
            if selected:
                row = self._db.execute(
                    "SELECT selected FROM notification_apps WHERE app_id = ?", (app_id,)
                ).fetchone()
                count = self._db.execute(
                    "SELECT COUNT(*) FROM notification_apps WHERE selected = 1"
                ).fetchone()[0]
                if (row is None or not row["selected"]) and count >= MAX_SELECTED_APPS:
                    raise NotificationStoreError("Selected app capacity is exhausted.")
            self._db.execute(
                """
                INSERT INTO notification_apps (app_id, selected) VALUES (?, ?)
                ON CONFLICT(app_id) DO UPDATE SET selected = excluded.selected
                """,
                (app_id, int(selected)),
            )
            if not selected:
                self._db.execute(
                    """
                    UPDATE notification_items SET title = NULL, body = NULL, draft = NULL
                    WHERE app_id = ? AND status IN ('unmatched', 'ignored', 'uncertain')
                    """,
                    (app_id,),
                )

    def selected_apps(self) -> tuple[str, ...]:
        rows = self._db.execute(
            "SELECT app_id FROM notification_apps WHERE selected = 1 ORDER BY app_id"
        ).fetchall()
        return tuple(row["app_id"] for row in rows)

    def rules(self, app_id: str) -> tuple[StoredRule, ...]:
        self._validate_identity(app_id, "App identity")
        rows = self._db.execute(
            "SELECT id, rule FROM notification_rules WHERE app_id = ? ORDER BY position, id",
            (app_id,),
        ).fetchall()
        result: list[StoredRule] = []
        for row in rows:
            rule = decode_rule(row["rule"])
            if rule.app_id != app_id:
                raise NotificationStoreError("Stored rule source does not match its app.")
            result.append(StoredRule(row["id"], rule))
        return tuple(result)

    def rule_revision(self, app_id: str) -> str:
        self._validate_identity(app_id, "App identity")
        rows = self._db.execute(
            "SELECT id, position, rule FROM notification_rules "
            "WHERE app_id = ? ORDER BY position, id",
            (app_id,),
        )
        entries = [(row["id"], row["position"], row["rule"]) for row in rows]
        return hashlib.sha256(json.dumps(entries, ensure_ascii=False).encode("utf-8")).hexdigest()

    def save_rule(
        self,
        rule_id: str,
        rule: Rule,
        *,
        position: int | None = None,
        expected_revision: str | None = None,
    ) -> None:
        self._validate_identity(rule_id, "Rule identity")
        self._validate_identity(rule.app_id, "App identity")
        if rule.enabled and rule.missing_captures():
            raise NotificationStoreError("An enabled rule refers to an undefined capture.")
        encoded = encode_rule(rule)
        # Round-trip through the same strict decoder used for untrusted drafts.
        decode_rule(encoded)
        with self._transaction():
            if (
                expected_revision is not None
                and self.rule_revision(rule.app_id) != expected_revision
            ):
                raise NotificationStoreError("Rules changed; test the rule again.")
            if not self._db.execute(
                "SELECT 1 FROM notification_apps WHERE app_id = ? AND selected = 1",
                (rule.app_id,),
            ).fetchone():
                raise NotificationStoreError("Select the source app before saving its rules.")
            existing = self._db.execute(
                "SELECT app_id FROM notification_rules WHERE id = ?", (rule_id,)
            ).fetchone()
            if existing is not None and existing["app_id"] != rule.app_id:
                raise NotificationStoreError("A rule cannot change its source app.")
            order: list[str] | None = None
            if position is not None:
                order = [
                    row["id"]
                    for row in self._db.execute(
                        "SELECT id FROM notification_rules "
                        "WHERE app_id = ? AND id != ? ORDER BY position, id",
                        (rule.app_id, rule_id),
                    )
                ]
                if type(position) is not int or not 0 <= position <= len(order):
                    raise NotificationStoreError("Rule position is invalid.")
                order.insert(position, rule_id)
            if existing is None:
                count = self._db.execute("SELECT COUNT(*) FROM notification_rules").fetchone()[0]
                if count >= 1000:
                    raise NotificationStoreError("Rule capacity is exhausted.")
                append_position = self._db.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 "
                    "FROM notification_rules WHERE app_id = ?",
                    (rule.app_id,),
                ).fetchone()[0]
                self._db.execute(
                    "INSERT INTO notification_rules (id, app_id, position, rule) "
                    "VALUES (?, ?, ?, ?)",
                    (rule_id, rule.app_id, append_position, encoded),
                )
            else:
                self._db.execute(
                    "UPDATE notification_rules SET rule = ? WHERE id = ?", (encoded, rule_id)
                )
            if order is not None:
                self._db.executemany(
                    "UPDATE notification_rules SET position = ? WHERE id = ? AND app_id = ?",
                    ((index, entry_id, rule.app_id) for index, entry_id in enumerate(order)),
                )

    def reorder_rules(self, app_id: str, rule_ids: tuple[str, ...]) -> None:
        self._validate_identity(app_id, "App identity")
        with self._transaction():
            existing = {
                row["id"]
                for row in self._db.execute(
                    "SELECT id FROM notification_rules WHERE app_id = ?", (app_id,)
                )
            }
            if len(rule_ids) != len(existing) or set(rule_ids) != existing:
                raise NotificationStoreError("Rule order must include each source-app rule once.")
            self._db.executemany(
                "UPDATE notification_rules SET position = ? WHERE id = ? AND app_id = ?",
                ((position, rule_id, app_id) for position, rule_id in enumerate(rule_ids)),
            )

    def delete_rule(self, rule_id: str, *, expected_revision: str | None = None) -> None:
        self._validate_identity(rule_id, "Rule identity")
        with self._transaction():
            row = self._db.execute(
                "SELECT app_id FROM notification_rules WHERE id = ?", (rule_id,)
            ).fetchone()
            if row is None:
                raise NotificationStoreError("Rule is unavailable.")
            if (
                expected_revision is not None
                and self.rule_revision(row["app_id"]) != expected_revision
            ):
                raise NotificationStoreError("Rules changed; review the rule again.")
            if (
                self._db.execute("DELETE FROM notification_rules WHERE id = ?", (rule_id,)).rowcount
                != 1
            ):
                raise NotificationStoreError("Rule is unavailable.")

    @staticmethod
    def _validate_identity(value: str, label: str) -> None:
        if not value or len(value) > 1024 or any(ord(char) < 32 for char in value):
            raise NotificationStoreError(f"{label} is invalid.")

    @staticmethod
    def _validate_notification(item: IncomingNotification, now: int) -> None:
        NotificationStore._validate_identity(item.app_id, "App identity")
        NotificationStore._validate_identity(item.key, "Notification identity")
        NotificationStore._validate_identity(item.version, "Notification version")
        if item.posted <= 0 or now <= 0 or item.posted > now + 5 * 60:
            raise NotificationStoreError(
                "Notification time is uncertain; manual review is required."
            )
        if item.posted_millis is not None and (
            item.posted_millis <= 0 or item.posted_millis // 1000 != item.posted
        ):
            raise NotificationStoreError("Notification time is invalid.")
        if any(
            text is not None and len(text) > MAX_NOTIFICATION_LENGTH
            for text in (item.title, item.body)
        ):
            raise NotificationStoreError("Notification content exceeds the supported size.")

    @staticmethod
    def _encode(evaluation: Evaluation) -> str | None:
        if evaluation.outcome not in ("pending", "ignored", "unmatched"):
            raise NotificationStoreError("Invalid notification evaluation.")
        if evaluation.outcome == "pending" and evaluation.draft is None:
            raise NotificationStoreError("A pending notification needs a review draft.")
        if evaluation.outcome != "pending" and evaluation.draft is not None:
            raise NotificationStoreError("Only pending notifications may have a draft.")
        if evaluation.journal_id is not None:
            NotificationStore._validate_journal_id(evaluation.journal_id)
            if evaluation.outcome != "pending":
                raise NotificationStoreError("Only pending notifications can target a journal.")
        if evaluation.rule_name is not None and (
            not evaluation.rule_name.strip() or len(evaluation.rule_name) > 120
        ):
            raise NotificationStoreError("Rule name is invalid.")
        if evaluation.draft is None:
            return None
        try:
            encoded = json.dumps(evaluation.draft, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise NotificationStoreError("The notification draft cannot be stored.") from exc
        if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise NotificationStoreError("The notification draft exceeds the supported size.")
        return encoded

    @staticmethod
    def _validate_journal_id(journal_id: str) -> None:
        if len(journal_id) != 64 or any(char not in "0123456789abcdef" for char in journal_id):
            raise NotificationStoreError("Journal identity must be a SHA-256 fingerprint.")

    def check_clock(self, now: int) -> None:
        if type(now) is not int or now <= 0:
            raise NotificationStoreError("Invalid current time.")
        settings = self._db.execute(
            "SELECT last_prune FROM notification_settings WHERE id = 1"
        ).fetchone()
        if now < settings["last_prune"]:
            raise NotificationStoreError("Device clock moved backwards; review is required.")

    def _prune(self, now: int) -> None:
        self.check_clock(now)
        self._db.execute("UPDATE notification_settings SET last_prune = ? WHERE id = 1", (now,))
        expired = self._db.execute(
            """
            UPDATE notification_items
            SET status = 'expired', title = NULL, body = NULL, draft = NULL, editing = 0
            WHERE status = 'pending' AND first_seen <= ?
            """,
            (now - PENDING_AGE,),
        ).rowcount
        expired += self._db.execute(
            """
            UPDATE notification_items SET title = NULL, body = NULL, draft = NULL
            WHERE status = 'saving' AND draft IS NOT NULL AND first_seen <= ?
            """,
            (now - PENDING_AGE,),
        ).rowcount
        if expired:
            self._db.execute(
                "UPDATE notification_settings SET expired_count = expired_count + ? WHERE id = 1",
                (expired,),
            )
        self._db.execute(
            """
            UPDATE notification_items SET title = NULL, body = NULL
            WHERE status IN ('ignored', 'unmatched', 'uncertain') AND first_seen <= ?
            """,
            (now - SAMPLE_AGE,),
        )
        self._db.execute(
            """
            UPDATE notification_items SET title = NULL, body = NULL
            WHERE id IN (
                SELECT id FROM notification_items
                WHERE status IN ('ignored', 'unmatched', 'uncertain')
                    AND (title IS NOT NULL OR body IS NOT NULL)
                ORDER BY first_seen DESC, id DESC LIMIT -1 OFFSET ?
            )
            """,
            (MAX_SAMPLES,),
        )
        old = self._db.execute(
            """
            SELECT id, app_id, posted FROM notification_items
            WHERE status NOT IN ('pending', 'saving') AND first_seen <= ?
            ORDER BY first_seen, id
            """,
            (now - RECORD_AGE,),
        ).fetchall()
        self._forget(old)
        count = self._db.execute("SELECT COUNT(*) FROM notification_items").fetchone()[0]
        extra = max(0, count - MAX_RECORDS)
        if extra:
            excess = self._db.execute(
                """
                SELECT id, app_id, posted FROM notification_items
                WHERE status NOT IN ('pending', 'saving')
                ORDER BY first_seen, id LIMIT ?
                """,
                (extra,),
            ).fetchall()
            if len(excess) != extra:
                raise NotificationStoreError("Notification history capacity is exhausted.")
            self._forget(excess)

    def _forget(self, rows: list[sqlite3.Row]) -> None:
        for row in rows:
            self._db.execute(
                "UPDATE notification_apps SET cutoff = MAX(cutoff, ?) WHERE app_id = ?",
                (row["posted"], row["app_id"]),
            )
            self._db.execute("DELETE FROM notification_items WHERE id = ?", (row["id"],))

    def prune(self, now: int) -> None:
        with self._transaction():
            self._prune(now)

    def intake(
        self,
        item: IncomingNotification,
        *,
        now: int,
        source: Literal["manual", "background"],
        evaluate: Callable[[IncomingNotification], Evaluation],
    ) -> IntakeResult:
        self._validate_identity(item.app_id, "App identity")
        if source not in ("manual", "background"):
            raise NotificationStoreError("Invalid notification intake source.")
        with self._transaction():
            self._prune(now)
            app = self._db.execute(
                "SELECT selected, cutoff FROM notification_apps WHERE app_id = ?", (item.app_id,)
            ).fetchone()
            if app is None or not app["selected"]:
                return "unselected"
            mode = self._db.execute(
                "SELECT mode FROM notification_settings WHERE id = 1"
            ).fetchone()["mode"]
            if mode == "off" or (source == "background" and mode != "background"):
                return "off"
            self._validate_notification(item, now)
            posted_millis = (
                item.posted_millis if item.posted_millis is not None else item.posted * 1000
            )
            old = self._db.execute(
                """
                SELECT * FROM notification_items
                WHERE app_id = ? AND notification_key = ? AND posted_millis = ?
                """,
                (item.app_id, item.key, posted_millis),
            ).fetchone()
            if old is not None:
                if old["version"] == item.version:
                    return "duplicate"
                if old["status"] == "uncertain":
                    self._db.execute(
                        """
                        UPDATE notification_items
                        SET version = ?, changed = 1,
                            title = CASE WHEN title IS NOT NULL OR body IS NOT NULL
                                         THEN ? ELSE NULL END,
                            body = CASE WHEN title IS NOT NULL OR body IS NOT NULL
                                        THEN ? ELSE NULL END
                        WHERE id = ?
                        """,
                        (item.version, item.title, item.body, old["id"]),
                    )
                    return "changed"
                if old["status"] in (
                    "saving",
                    "imported",
                    "skipped",
                    "expired",
                    "cleared",
                ):
                    self._db.execute(
                        "UPDATE notification_items SET version = ?, changed = 1 WHERE id = ?",
                        (item.version, old["id"]),
                    )
                    return "changed"
                if old["status"] == "pending" and old["editing"]:
                    self._db.execute(
                        "UPDATE notification_items SET changed = 1 WHERE id = ?", (old["id"],)
                    )
                    return "changed"
            elif item.posted <= app["cutoff"]:
                return "too_old"
            elif self._db.execute(
                """
                SELECT 1 FROM notification_items
                WHERE app_id = ? AND notification_key = ? LIMIT 1
                """,
                (item.app_id, item.key),
            ).fetchone():
                # Without a removal event, an updated post time cannot be
                # distinguished safely from reuse of the same notification key.
                self._db.execute(
                    """
                    UPDATE notification_items SET changed = 1
                    WHERE app_id = ? AND notification_key = ?
                    """,
                    (item.app_id, item.key),
                )
                self._db.execute(
                    """
                    INSERT INTO notification_items
                    (app_id, notification_key, posted, posted_millis, version, first_seen, status,
                     title, body, changed)
                    VALUES (?, ?, ?, ?, ?, ?, 'uncertain', ?, ?, 1)
                    """,
                    (
                        item.app_id,
                        item.key,
                        item.posted,
                        posted_millis,
                        item.version,
                        now,
                        item.title,
                        item.body,
                    ),
                )
                self._prune(now)
                return "uncertain"
            result = evaluate(item)
            draft = self._encode(result)
            rule_revision = self.rule_revision(item.app_id) if result.outcome == "pending" else None
            if (
                old is not None
                and old["status"] == "pending"
                and old["journal_id"] is not None
                and old["journal_id"] != result.journal_id
            ):
                self._db.execute(
                    "UPDATE notification_items SET changed = 1 WHERE id = ?", (old["id"],)
                )
                return "changed"
            if old is not None and old["status"] == "pending" and result.outcome != "pending":
                self._db.execute(
                    "UPDATE notification_items SET version = ?, changed = 1 WHERE id = ?",
                    (item.version, old["id"]),
                )
                return "changed"
            if result.outcome == "pending" and (old is None or old["status"] != "pending"):
                count = self._db.execute(
                    "SELECT COUNT(*) FROM notification_items WHERE status IN ('pending', 'saving')"
                ).fetchone()[0]
                if count >= MAX_PENDING:
                    return "capacity"
            if old is None:
                self._db.execute(
                    """
                    INSERT INTO notification_items
                    (app_id, notification_key, posted, posted_millis, version, first_seen, status,
                     rule_name, title, body, draft, journal_id, rule_revision)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.app_id,
                        item.key,
                        item.posted,
                        posted_millis,
                        item.version,
                        now,
                        result.outcome,
                        result.rule_name,
                        item.title,
                        item.body,
                        draft,
                        result.journal_id,
                        rule_revision,
                    ),
                )
            else:
                self._db.execute(
                    """
                    UPDATE notification_items
                    SET version = ?, status = ?, rule_name = ?, title = ?, body = ?, draft = ?,
                        journal_id = COALESCE(journal_id, ?), rule_revision = ?, changed = 0
                    WHERE id = ?
                    """,
                    (
                        item.version,
                        result.outcome,
                        result.rule_name,
                        item.title,
                        item.body,
                        draft,
                        result.journal_id,
                        rule_revision,
                        old["id"],
                    ),
                )
            self._prune(now)
            return "recorded" if old is None else "updated"

    def old_recovery_candidates(
        self, notifications: tuple[IncomingNotification, ...], *, now: int
    ) -> tuple[IncomingNotification, ...]:
        """Find old visible items without evaluating or retaining their contents."""
        if self.mode() == "off":
            raise NotificationStoreError("Enable notification checking before recovery.")
        if len(notifications) > 10_000:
            raise NotificationStoreError("Too many notifications to review.")
        cutoffs = {
            row["app_id"]: row["cutoff"]
            for row in self._db.execute(
                "SELECT app_id, cutoff FROM notification_apps WHERE selected = 1"
            )
        }
        candidates: list[IncomingNotification] = []
        seen: set[tuple[str, str, int]] = set()
        for item in notifications:
            if item.app_id not in cutoffs:
                continue
            self._validate_notification(item, now)
            posted_millis = (
                item.posted_millis if item.posted_millis is not None else item.posted * 1000
            )
            identity = (item.app_id, item.key, posted_millis)
            if (
                identity in seen
                or item.posted > cutoffs[item.app_id]
                or (item.title is None and item.body is None)
            ):
                continue
            seen.add(identity)
            if (
                self._db.execute(
                    "SELECT 1 FROM notification_items WHERE app_id = ? "
                    "AND notification_key = ? AND posted_millis = ?",
                    identity,
                ).fetchone()
                is None
            ):
                candidates.append(item)
        return tuple(candidates)

    def recover_old(
        self, item: IncomingNotification, *, now: int, confirm_possible_duplicate: bool
    ) -> int:
        """Retain one explicitly selected old notification for duplicate-aware review."""
        if confirm_possible_duplicate is not True:
            raise NotificationStoreError("Confirm review of a possible duplicate.")
        self._validate_identity(item.app_id, "App identity")
        with self._transaction():
            self._prune(now)
            app = self._db.execute(
                "SELECT selected, cutoff FROM notification_apps WHERE app_id = ?", (item.app_id,)
            ).fetchone()
            if app is None or not app["selected"]:
                raise NotificationStoreError("The notification's source app is not selected.")
            mode = self._db.execute(
                "SELECT mode FROM notification_settings WHERE id = 1"
            ).fetchone()["mode"]
            if mode == "off":
                raise NotificationStoreError("Enable notification checking before recovery.")
            self._validate_notification(item, now)
            if item.posted > app["cutoff"]:
                raise NotificationStoreError("Notification is not outside the replay cutoff.")
            posted_millis = (
                item.posted_millis if item.posted_millis is not None else item.posted * 1000
            )
            if self._db.execute(
                "SELECT 1 FROM notification_items WHERE app_id = ? "
                "AND notification_key = ? AND posted_millis = ?",
                (item.app_id, item.key, posted_millis),
            ).fetchone():
                raise NotificationStoreError("Notification already has a retained record.")
            cursor = self._db.execute(
                """
                INSERT INTO notification_items
                    (app_id, notification_key, posted, posted_millis, version, first_seen, status,
                     title, body, changed)
                VALUES (?, ?, ?, ?, ?, ?, 'uncertain', ?, ?, 1)
                """,
                (
                    item.app_id,
                    item.key,
                    item.posted,
                    posted_millis,
                    item.version,
                    now,
                    item.title,
                    item.body,
                ),
            )
            item_id = cursor.lastrowid
            if item_id is None:
                raise NotificationStoreError("Could not retain the notification for review.")
            self._prune(now)
            return item_id

    @staticmethod
    def _status_filter(statuses: tuple[str, ...]) -> str:
        if not statuses or not set(statuses) <= _STATUSES:
            raise NotificationStoreError("Invalid notification status filter.")
        return ",".join("?" for _ in statuses)

    def count(self, statuses: tuple[str, ...]) -> int:
        placeholders = self._status_filter(statuses)
        return self._db.execute(
            f"SELECT COUNT(*) FROM notification_items WHERE status IN ({placeholders})",
            statuses,
        ).fetchone()[0]

    def items(
        self,
        statuses: tuple[str, ...],
        *,
        limit: int | None = None,
        offset: int = 0,
        newest_first: bool = False,
        app_id: str | None = None,
        with_content: bool = False,
    ) -> tuple[QueueItem, ...]:
        placeholders = self._status_filter(statuses)
        if app_id is not None:
            self._validate_identity(app_id, "App identity")
        if (
            (limit is not None and (type(limit) is not int or not 1 <= limit <= MAX_PENDING))
            or type(offset) is not int
            or offset < 0
            or (offset and limit is None)
            or type(newest_first) is not bool
            or type(with_content) is not bool
        ):
            raise NotificationStoreError("Invalid notification page.")
        order = "DESC" if newest_first else "ASC"
        source = " AND app_id = ?" if app_id is not None else ""
        content = " AND (title IS NOT NULL OR body IS NOT NULL)" if with_content else ""
        paging = " LIMIT ? OFFSET ?" if limit is not None else ""
        parameters: tuple[object, ...] = statuses
        if app_id is not None:
            parameters += (app_id,)
        if limit is not None:
            parameters += (limit, offset)
        rows = self._db.execute(
            f"SELECT * FROM notification_items WHERE status IN ({placeholders}){source}{content} "
            f"ORDER BY first_seen {order}, id {order}{paging}",
            parameters,
        ).fetchall()
        items: list[QueueItem] = []
        revisions: dict[str, str] = {}
        for row in rows:
            try:
                draft = json.loads(row["draft"]) if row["draft"] is not None else None
            except json.JSONDecodeError as exc:
                raise NotificationStoreError("Stored notification draft is invalid.") from exc
            if draft is not None and not isinstance(draft, dict):
                raise NotificationStoreError("Stored notification draft is invalid.")
            stale_rule = False
            if row["status"] == "pending":
                item_app_id: str = row["app_id"]
                if item_app_id not in revisions:
                    revisions[item_app_id] = self.rule_revision(item_app_id)
                stale_rule = row["rule_revision"] != revisions[item_app_id]
            items.append(
                QueueItem(
                    row["id"],
                    row["app_id"],
                    row["notification_key"],
                    row["posted"],
                    row["posted_millis"],
                    row["first_seen"],
                    row["status"],
                    row["rule_name"],
                    row["title"],
                    row["body"],
                    draft,
                    bool(row["changed"]),
                    row["journal_id"],
                    row["version"],
                    self._snapshot(row),
                    self._snapshot(row, include_rules=False),
                    stale_rule,
                )
            )
        return tuple(items)

    def _snapshot(self, row: sqlite3.Row, *, include_rules: bool = True) -> str:
        fields = (
            row["id"],
            row["app_id"],
            row["notification_key"],
            row["posted"],
            row["posted_millis"],
            row["version"],
            row["first_seen"],
            row["status"],
            row["rule_name"],
            row["rule_revision"],
            row["title"],
            row["body"],
            row["draft"],
            row["journal_id"],
            row["editing"],
            row["changed"],
        )
        if include_rules:
            rules = [
                (entry["id"], entry["position"], entry["rule"])
                for entry in self._db.execute(
                    "SELECT id, position, rule FROM notification_rules "
                    "WHERE app_id = ? ORDER BY position, id",
                    (row["app_id"],),
                )
            ]
            fields += (rules,)
        return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode("utf-8")).hexdigest()

    def apply_evaluation(
        self,
        item_id: int,
        snapshot: str,
        evaluation: Evaluation,
        *,
        overwrite_edits: bool = False,
        confirm_possible_duplicate: bool = False,
    ) -> None:
        """Apply one explicitly reviewed evaluation only to its unchanged source item."""
        with self._transaction():
            self._apply_evaluation(
                item_id,
                snapshot,
                evaluation,
                overwrite_edits=overwrite_edits,
                confirm_possible_duplicate=confirm_possible_duplicate,
            )

    def apply_recent_evaluations(
        self, evaluations: tuple[tuple[int, str, Evaluation], ...], *, now: int
    ) -> None:
        """Atomically apply a reviewed batch of unchanged recent samples."""
        if (
            not evaluations
            or len(evaluations) > MAX_SAMPLES
            or len({item_id for item_id, _, _ in evaluations}) != len(evaluations)
            or type(now) is not int
            or now <= 0
        ):
            raise NotificationStoreError("Invalid recent notification preview.")
        with self._transaction():
            self.check_clock(now)
            for item_id, snapshot, evaluation in evaluations:
                self._apply_evaluation(item_id, snapshot, evaluation, recent_now=now)

    def _apply_evaluation(
        self,
        item_id: int,
        snapshot: str,
        evaluation: Evaluation,
        *,
        overwrite_edits: bool = False,
        confirm_possible_duplicate: bool = False,
        recent_now: int | None = None,
    ) -> None:
        if len(snapshot) != 64 or any(char not in "0123456789abcdef" for char in snapshot):
            raise NotificationStoreError("Invalid notification preview.")
        draft = self._encode(evaluation)
        row = self._db.execute(
            "SELECT * FROM notification_items WHERE id = ?", (item_id,)
        ).fetchone()
        allowed = (
            ("unmatched", "ignored")
            if recent_now is not None
            else ("pending", "unmatched", "ignored", "uncertain")
        )
        if row is None or row["status"] not in allowed:
            raise NotificationStoreError("Notification is unavailable for re-evaluation.")
        if recent_now is not None and not row["first_seen"] <= recent_now < (
            row["first_seen"] + SAMPLE_AGE
        ):
            raise NotificationStoreError("Recent notification sample has expired.")
        if self._snapshot(row) != snapshot:
            raise NotificationStoreError("Notification changed; preview the rule again.")
        selected = self._db.execute(
            "SELECT selected FROM notification_apps WHERE app_id = ?", (row["app_id"],)
        ).fetchone()
        if selected is None or not selected["selected"]:
            raise NotificationStoreError("The notification's source app is not selected.")
        if row["editing"] and not overwrite_edits:
            raise NotificationStoreError("Confirm replacement of the active transaction draft.")
        if row["status"] == "uncertain" and not confirm_possible_duplicate:
            raise NotificationStoreError("Confirm review of a possible duplicate.")
        if row["title"] is None and row["body"] is None:
            raise NotificationStoreError("The original notification content is unavailable.")
        if (
            evaluation.outcome == "pending"
            and row["journal_id"] is not None
            and row["journal_id"] != evaluation.journal_id
        ):
            raise NotificationStoreError("Draft targets another journal.")
        if evaluation.outcome == "pending" and row["status"] != "pending":
            count = self._db.execute(
                "SELECT COUNT(*) FROM notification_items WHERE status IN ('pending', 'saving')"
            ).fetchone()[0]
            if count >= MAX_PENDING:
                raise NotificationCapacityError("Pending notification capacity is exhausted.")
        self._db.execute(
            """
            UPDATE notification_items
            SET status = ?, rule_name = ?, draft = ?, journal_id = ?, rule_revision = ?,
                editing = 0, changed = 0
            WHERE id = ?
            """,
            (
                evaluation.outcome,
                evaluation.rule_name,
                draft,
                evaluation.journal_id,
                self.rule_revision(row["app_id"]) if evaluation.outcome == "pending" else None,
                item_id,
            ),
        )

    def set_editing(self, item_id: int, editing: bool) -> None:
        with self._transaction():
            updated = self._db.execute(
                "UPDATE notification_items SET editing = ? WHERE id = ? AND status = 'pending'",
                (int(editing), item_id),
            ).rowcount
            if updated != 1:
                raise NotificationStoreError("Pending notification is unavailable.")

    def begin_review(self, item_id: int, snapshot: str, *, journal_id: str) -> QueueItem:
        """Bind and protect the exact candidate presented to the editor."""
        self._validate_journal_id(journal_id)
        with self._transaction():
            row = self._db.execute(
                "SELECT * FROM notification_items WHERE id = ?", (item_id,)
            ).fetchone()
            if (
                row is None
                or row["status"] != "pending"
                or self._snapshot(row, include_rules=False) != snapshot
            ):
                raise NotificationStoreError("Notification changed; review it again.")
            if row["journal_id"] is not None and row["journal_id"] != journal_id:
                raise NotificationStoreError("Draft targets another journal.")
            self._db.execute(
                "UPDATE notification_items SET journal_id = ?, editing = 1 WHERE id = ?",
                (journal_id, item_id),
            )
            updated = next(item for item in self.items(("pending",)) if item.id == item_id)
            return updated

    def bind_journal(self, item_id: int, journal_id: str) -> None:
        """Bind an unassigned draft to a user-selected journal."""
        self._validate_journal_id(journal_id)
        with self._transaction():
            updated = self._db.execute(
                """
                UPDATE notification_items SET journal_id = ?
                WHERE id = ? AND status = 'pending'
                    AND (journal_id IS NULL OR journal_id = ?)
                """,
                (journal_id, item_id, journal_id),
            ).rowcount
            if updated != 1:
                raise NotificationStoreError("Draft targets another journal or is unavailable.")

    def begin_import(self, item_id: int, *, journal_id: str, snapshot: str | None = None) -> None:
        self._validate_journal_id(journal_id)
        with self._transaction():
            if snapshot is not None:
                row = self._db.execute(
                    "SELECT * FROM notification_items WHERE id = ?", (item_id,)
                ).fetchone()
                if row is None or self._snapshot(row, include_rules=False) != snapshot:
                    raise NotificationStoreError("Notification changed; review it again.")
            updated = self._db.execute(
                """
                UPDATE notification_items SET status = 'saving', editing = 0
                WHERE id = ? AND status = 'pending' AND journal_id = ?
                """,
                (item_id, journal_id),
            ).rowcount
            if updated != 1:
                raise NotificationStoreError(
                    "Notification is unavailable or targets another journal."
                )

    def finish_import(
        self,
        item_id: int,
        *,
        saved: bool,
        journal_id: str | None = None,
        snapshot: str | None = None,
    ) -> None:
        """Resolve a save only after its outcome has been verified."""
        if journal_id is not None:
            self._validate_journal_id(journal_id)
        with self._transaction():
            row = self._db.execute(
                "SELECT * FROM notification_items WHERE id = ? AND status = 'saving'",
                (item_id,),
            ).fetchone()
            if row is None:
                raise NotificationStoreError("There is no unresolved import for this notification.")
            if journal_id is not None and row["journal_id"] != journal_id:
                raise NotificationStoreError("Draft targets another journal.")
            if snapshot is not None and self._snapshot(row, include_rules=False) != snapshot:
                raise NotificationStoreError("Notification changed; review it again.")
            status = "imported" if saved else "pending" if row["draft"] is not None else "cleared"
            updated = self._db.execute(
                """
                UPDATE notification_items SET status = ?,
                    title = CASE WHEN ? THEN NULL ELSE title END,
                    body = CASE WHEN ? THEN NULL ELSE body END,
                    draft = CASE WHEN ? THEN NULL ELSE draft END
                WHERE id = ? AND status = 'saving'
                """,
                (status, saved, saved, saved, item_id),
            ).rowcount
            if updated != 1:
                raise NotificationStoreError("There is no unresolved import for this notification.")

    def skip(self, item_id: int, *, snapshot: str | None = None) -> None:
        with self._transaction():
            if snapshot is not None:
                row = self._db.execute(
                    "SELECT * FROM notification_items WHERE id = ?", (item_id,)
                ).fetchone()
                if row is None or self._snapshot(row, include_rules=False) != snapshot:
                    raise NotificationStoreError("Notification changed; review it again.")
            updated = self._db.execute(
                """
                UPDATE notification_items
                SET status = 'skipped', title = NULL, body = NULL, draft = NULL, editing = 0
                WHERE id = ? AND status = 'pending'
                """,
                (item_id,),
            ).rowcount
            if updated != 1:
                raise NotificationStoreError("Pending notification is unavailable.")

    def clear_captured(self) -> None:
        with self._transaction():
            self._db.execute(
                """
                UPDATE notification_items
                SET status = CASE WHEN status = 'pending' THEN 'cleared' ELSE status END,
                    title = NULL, body = NULL, draft = NULL, editing = 0
                """
            )

    def reset_history(self, now: int) -> None:
        with self._transaction():
            self._prune(now)
            if self._db.execute(
                "SELECT 1 FROM notification_items WHERE status = 'saving' LIMIT 1"
            ).fetchone():
                raise NotificationStoreError("Resolve uncertain imports before resetting history.")
            self._db.execute("DELETE FROM notification_items")
            self._db.execute("UPDATE notification_apps SET cutoff = MAX(cutoff, ?)", (now,))
            self._db.execute("UPDATE notification_settings SET expired_count = 0 WHERE id = 1")

    def expired_count(self) -> int:
        return self._db.execute(
            "SELECT expired_count FROM notification_settings WHERE id = 1"
        ).fetchone()[0]

    def acknowledge_expired(self, expected_count: int) -> None:
        with self._transaction():
            if type(expected_count) is not int or expected_count <= 0:
                raise NotificationStoreError("Invalid expired notification count.")
            updated = self._db.execute(
                "UPDATE notification_settings SET expired_count = 0 "
                "WHERE id = 1 AND expired_count = ?",
                (expected_count,),
            ).rowcount
            if updated != 1:
                raise NotificationStoreError("Expired notification count changed; review it again.")
