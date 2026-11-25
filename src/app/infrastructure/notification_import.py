"""Manual notification checking and explicit, journal-safe review saves."""

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from datetime import date

from app.domain.ledger import Ledger, LedgerError, money
from app.domain.notification_rules import Draft, Rule, first_match, preview_transaction
from app.domain.storage import ConflictError
from app.infrastructure.android_storage import AndroidFolder
from app.infrastructure.notification_store import (
    SAMPLE_AGE,
    Evaluation,
    IncomingNotification,
    IntakeResult,
    NotificationStore,
    NotificationStoreError,
    QueueItem,
)
from app.infrastructure.notifications_android import AndroidNotifications
from app.journal import Journal


@dataclass(frozen=True)
class CheckResult:
    recorded: int = 0
    updated: int = 0
    changed: int = 0
    duplicates: int = 0
    excluded: int = 0
    capacity: int = 0


@dataclass(frozen=True)
class RulePreview:
    snapshot: str
    evaluation: Evaluation
    captures: dict[str, str]
    previous_draft: dict[str, object] | None


@dataclass(frozen=True)
class RuleTest:
    proposed_matched: bool
    winning_rule_id: str | None
    previous_evaluation: Evaluation
    evaluation: Evaluation
    captures: dict[str, str]


@dataclass(frozen=True)
class RecentRulePreview:
    item: QueueItem
    preview: RulePreview


async def set_manual_monitoring(
    bridge: AndroidNotifications, store: NotificationStore, *, enabled: bool
) -> None:
    """Apply the privacy boundary before enabling or disabling the native listener."""
    store.set_mode("manual" if enabled else "off")
    if enabled:
        await bridge.enable()
    else:
        await bridge.disable()


async def open_notification_access_settings(bridge: AndroidNotifications) -> None:
    """Make the listener available to Android settings without changing capture mode."""
    await bridge.enable()
    await bridge.open_settings()


def test_rule(
    store: NotificationStore,
    rule_id: str,
    rule: Rule,
    position: int,
    *,
    title: str | None,
    body: str | None,
    ledger: Ledger | None,
    today: date,
) -> RuleTest:
    """Test a proposed rule at a priority without persisting or replaying anything."""
    if rule.app_id not in store.selected_apps():
        raise NotificationStoreError("The notification's source app is not selected.")
    if not rule_id or len(rule_id) > 1024 or any(ord(char) < 32 for char in rule_id):
        raise NotificationStoreError("Rule identity is invalid.")
    existing = list(store.rules(rule.app_id))
    previous_evaluation, _ = _evaluate(
        tuple(entry.rule for entry in existing),
        rule.app_id,
        title,
        body,
        ledger,
        today=today,
        journal_id=None,
    )
    without_proposal = [entry for entry in existing if entry.id != rule_id]
    if type(position) is not int or not 0 <= position <= len(without_proposal):
        raise NotificationStoreError("Rule position is invalid.")
    proposed = replace(rule, enabled=True)
    entries = [(entry.id, entry.rule) for entry in without_proposal]
    entries.insert(position, (rule_id, proposed))
    rules = tuple(entry_rule for _, entry_rule in entries)
    winning = first_match(rules, rule.app_id, title, body)
    winner = next(
        (entry_id for entry_id, value in entries if winning and value is winning.rule), None
    )
    evaluation, captures = _evaluate(
        rules, rule.app_id, title, body, ledger, today=today, journal_id=None
    )
    return RuleTest(
        proposed_matched=first_match((proposed,), rule.app_id, title, body) is not None,
        winning_rule_id=winner,
        previous_evaluation=previous_evaluation,
        evaluation=evaluation,
        captures=captures,
    )


def read_review_draft(item: QueueItem) -> Draft:
    """Decode a stored preview without substituting defaults for unavailable fields."""
    data = item.draft
    fields = {
        "kind",
        "amount",
        "currency",
        "account",
        "destination",
        "destination_amount",
        "category",
        "payee",
        "description",
        "date",
        "time",
        "errors",
    }
    if item.status != "pending" or not isinstance(data, dict) or set(data) != fields:
        raise NotificationStoreError("Stored notification draft is invalid.")

    def text(key: str) -> str | None:
        value = data[key]
        if value is not None and (not isinstance(value, str) or len(value) > 4096):
            raise NotificationStoreError("Stored notification draft is invalid.")
        return value

    kind = text("kind")
    if kind not in (None, "expense", "income", "transfer"):
        raise NotificationStoreError("Stored notification draft is invalid.")
    errors = data["errors"]
    if (
        not isinstance(errors, list)
        or len(errors) > 50
        or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(part, str) or len(part) > 4096 for part in pair)
            for pair in errors
        )
    ):
        raise NotificationStoreError("Stored notification draft is invalid.")
    amount = text("amount")
    destination_amount = text("destination_amount")
    occurred = text("date")
    try:
        parsed_amount = money(amount) if amount is not None else None
        parsed_destination = money(destination_amount) if destination_amount is not None else None
        parsed_date = date.fromisoformat(occurred) if occurred is not None else None
        if (parsed_amount is not None and parsed_amount <= 0) or (
            parsed_destination is not None and parsed_destination <= 0
        ):
            raise ValueError("Invalid draft amount.")
    except (LedgerError, ValueError) as exc:
        raise NotificationStoreError("Stored notification draft is invalid.") from exc
    return Draft(
        kind,
        parsed_amount,
        text("currency"),
        text("account"),
        text("destination"),
        parsed_destination,
        text("category"),
        text("payee"),
        text("description"),
        parsed_date,
        text("time"),
        tuple((pair[0], pair[1]) for pair in errors),
    )


def _evaluate(
    rules: tuple[Rule, ...],
    app_id: str,
    title: str | None,
    body: str | None,
    ledger: Ledger | None,
    *,
    today: date,
    journal_id: str | None,
) -> tuple[Evaluation, dict[str, str]]:
    match = first_match(rules, app_id, title, body)
    if match is None:
        return Evaluation("unmatched"), {}
    if match.rule.action == "ignore":
        return Evaluation("ignored", match.rule.name), match.captures
    preview = preview_transaction(match, ledger or Ledger(), today=today)
    draft = asdict(preview)
    for key in ("amount", "destination_amount", "date"):
        if draft[key] is not None:
            draft[key] = str(draft[key])
    return Evaluation("pending", match.rule.name, draft, journal_id=journal_id), match.captures


def preview_re_evaluation(
    store: NotificationStore,
    item: QueueItem,
    ledger: Ledger | None,
    *,
    today: date,
    journal_id: str | None = None,
) -> RulePreview:
    """Preview the current winning rule without changing the saved item."""
    if item.status not in ("pending", "unmatched", "ignored", "uncertain"):
        raise NotificationStoreError("Notification is unavailable for re-evaluation.")
    if item.app_id not in store.selected_apps():
        raise NotificationStoreError("The notification's source app is not selected.")
    if item.title is None and item.body is None:
        raise NotificationStoreError("The original notification content is unavailable.")
    if item.journal_id is not None and journal_id != item.journal_id:
        raise NotificationStoreError("Draft targets another journal.")
    rules = tuple(entry.rule for entry in store.rules(item.app_id))
    evaluation, captures = _evaluate(
        rules, item.app_id, item.title, item.body, ledger, today=today, journal_id=journal_id
    )
    return RulePreview(item.snapshot, evaluation, captures, item.draft)


def matching_rule_id(store: NotificationStore, item: QueueItem) -> str | None:
    """Identify a pending draft's winning rule only while its source and rules are unchanged."""
    current = next(
        (
            candidate
            for candidate in store.items(("pending",), app_id=item.app_id)
            if candidate.id == item.id
        ),
        None,
    )
    if (
        current is None
        or current.review_snapshot != item.review_snapshot
        or current.changed
        or current.stale_rule
        or current.app_id not in store.selected_apps()
    ):
        return None
    entries = store.rules(current.app_id)
    match = first_match(
        tuple(entry.rule for entry in entries), current.app_id, current.title, current.body
    )
    if match is None or match.rule.action != "suggest" or match.rule.name != current.rule_name:
        return None
    return next(entry.id for entry in entries if entry.rule is match.rule)


def preview_recent_re_evaluation(
    store: NotificationStore,
    ledger: Ledger | None,
    *,
    now: int,
    today: date,
    journal_id: str | None = None,
) -> tuple[RecentRulePreview, ...]:
    """Preview changed outcomes for retained, selected, non-duplicate samples."""
    store.check_clock(now)
    selected = set(store.selected_apps())
    previews: list[RecentRulePreview] = []
    for item in store.items(("unmatched", "ignored"), with_content=True, newest_first=True):
        if item.app_id not in selected or not item.first_seen <= now < item.first_seen + SAMPLE_AGE:
            continue
        preview = preview_re_evaluation(store, item, ledger, today=today, journal_id=journal_id)
        if (preview.evaluation.outcome, preview.evaluation.rule_name) != (
            item.status,
            item.rule_name,
        ):
            previews.append(RecentRulePreview(item, preview))
    return tuple(previews)


def journal_identity(journal: Journal) -> str:
    """Bind drafts to the exact selected Android document without storing its URI."""
    folder = journal.storage.folder
    if not isinstance(folder, AndroidFolder):
        raise ValueError("Notification imports require an Android journal.")
    identity = json.dumps([folder.uri, folder.filename], ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


async def save_reviewed_notification(
    store: NotificationStore,
    item_id: int,
    journal: Journal,
    save: Callable[[], Awaitable[str | None]],
    *,
    snapshot: str,
) -> str | None:
    """Reserve one candidate and resolve it only after the journal save succeeds."""
    if not journal.ready or not journal.storage.loaded or journal.storage.draft != journal.source:
        raise LedgerError("Reload a valid journal first. Unsaved form input is retained.")
    identity = journal_identity(journal)
    store.begin_import(item_id, journal_id=identity, snapshot=snapshot)
    original = journal.source
    try:
        backup = await save()
    except (LedgerError, ConflictError):
        if (
            journal.ready
            and journal.storage.loaded
            and journal.source == original
            and journal.storage.draft == original
        ):
            store.finish_import(item_id, saved=False)
        raise
    store.finish_import(item_id, saved=True)
    return backup


async def resolve_reviewed_import(
    store: NotificationStore,
    item: QueueItem,
    journal: Journal,
    *,
    source_hash: str,
    saved: bool,
) -> None:
    """Resolve an uncertain save only against the reviewed, unchanged journal."""
    if not journal.ready or not journal.storage.loaded or journal.storage.draft != journal.source:
        raise LedgerError("Reload a valid journal first.")
    if item.status != "saving" or item.journal_id != journal_identity(journal):
        raise NotificationStoreError("Unresolved import targets another journal.")
    if (
        len(source_hash) != 64
        or hashlib.sha256(journal.source.encode("utf-8")).hexdigest() != source_hash
    ):
        raise NotificationStoreError("Journal changed; review it again.")
    if await journal.poll() or (
        not journal.ready
        or not journal.storage.loaded
        or journal.storage.draft != journal.source
        or hashlib.sha256(journal.source.encode("utf-8")).hexdigest() != source_hash
    ):
        raise NotificationStoreError("Journal changed; review it again.")
    store.finish_import(
        item.id, saved=saved, journal_id=item.journal_id, snapshot=item.review_snapshot
    )


async def check_notifications(
    bridge: AndroidNotifications,
    store: NotificationStore,
    ledger: Ledger | None,
    *,
    now: int,
    today: date,
    journal_id: str | None = None,
) -> CheckResult:
    """Perform one explicit check; never write to the user's financial journal."""
    if store.mode() == "off":
        raise ValueError("Enable notification checking before checking.")
    selected = store.selected_apps()
    if not selected:
        raise ValueError("Select at least one source app before checking.")
    notifications = await bridge.active(selected)
    rules = {app_id: tuple(entry.rule for entry in store.rules(app_id)) for app_id in selected}

    def evaluate(item: IncomingNotification) -> Evaluation:
        return _evaluate(
            rules[item.app_id],
            item.app_id,
            item.title,
            item.body,
            ledger,
            today=today,
            journal_id=journal_id,
        )[0]

    results: list[IntakeResult] = [
        store.intake(item, now=now, source="manual", evaluate=evaluate) for item in notifications
    ]
    store.record_check(now, selected)
    return CheckResult(
        recorded=results.count("recorded"),
        updated=results.count("updated"),
        changed=results.count("changed") + results.count("uncertain"),
        duplicates=results.count("duplicate"),
        excluded=sum(results.count(kind) for kind in ("off", "unselected", "too_old")),
        capacity=results.count("capacity"),
    )


def capture_background_notification(
    store: NotificationStore,
    item: IncomingNotification,
    *,
    now: int,
    today: date,
) -> IntakeResult:
    """Evaluate one eligible listener event without opening or writing a journal."""

    def evaluate(notification: IncomingNotification) -> Evaluation:
        rules = tuple(entry.rule for entry in store.rules(notification.app_id))
        return _evaluate(
            rules,
            notification.app_id,
            notification.title,
            notification.body,
            None,
            today=today,
            journal_id=None,
        )[0]

    return store.intake(item, now=now, source="background", evaluate=evaluate)
