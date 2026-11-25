import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

import app.infrastructure.notification_store as notification_store
from app.domain.notification_rules import Pattern, Rule, TransactionMapping, Value
from app.infrastructure.notification_rule_codec import RuleCodecError, decode_rule, encode_rule
from app.infrastructure.notification_store import (
    DAY,
    Evaluation,
    IncomingNotification,
    NotificationStore,
    NotificationStoreError,
)

NOW = 200 * DAY
JOURNAL = "a" * 64


@pytest.fixture
def store(tmp_path: Path):
    database = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        yield database
    finally:
        database.close()


def incoming(
    key: str = "key",
    *,
    app: str = "bank.app",
    posted: int = NOW - 1,
    version: str = "v1",
    body: str = "Paid 5 EUR",
) -> IncomingNotification:
    return IncomingNotification(app, key, posted, version, "Bank", body)


def pending(_: IncomingNotification) -> Evaluation:
    return Evaluation("pending", "Purchases", {"amount": "5", "currency": "EUR"})


def intake(store: NotificationStore, item: IncomingNotification, *, now: int = NOW) -> str:
    return store.intake(item, now=now, source="manual", evaluate=pending)


def test_off_and_unselected_apps_are_not_evaluated_or_retained(store: NotificationStore) -> None:
    calls: list[IncomingNotification] = []

    def evaluate(item: IncomingNotification) -> Evaluation:
        calls.append(item)
        return pending(item)

    item = incoming()
    store.set_mode("manual")
    assert store.intake(item, now=NOW, source="manual", evaluate=evaluate) == "unselected"
    store.set_app_selected(item.app_id, True)
    assert store.intake(item, now=NOW, source="background", evaluate=evaluate) == "off"
    store.set_mode("off")
    assert store.intake(item, now=NOW, source="manual", evaluate=evaluate) == "off"
    assert calls == []
    assert store.items(("pending", "ignored", "unmatched")) == ()


def test_selected_app_limit_matches_native_bridge(
    store: NotificationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notification_store, "MAX_SELECTED_APPS", 2)
    store.set_app_selected("one.app", True)
    store.set_app_selected("two.app", True)
    store.set_app_selected("one.app", True)
    with pytest.raises(NotificationStoreError, match="capacity"):
        store.set_app_selected("three.app", True)
    assert store.selected_apps() == ("one.app", "two.app")
    store.set_app_selected("one.app", False)
    store.set_app_selected("three.app", True)
    assert store.selected_apps() == ("three.app", "two.app")


def test_last_check_migrates_existing_store_and_rejects_stale_settings(tmp_path: Path) -> None:
    path = tmp_path / "notifications.sqlite"
    with sqlite3.connect(path) as database:
        database.execute(
            "CREATE TABLE notification_settings (id INTEGER PRIMARY KEY, mode TEXT NOT NULL, "
            "last_prune INTEGER NOT NULL, expired_count INTEGER NOT NULL DEFAULT 0)"
        )
        database.execute("INSERT INTO notification_settings VALUES (1, 'manual', 0, 0)")
    store = NotificationStore(path)
    try:
        assert store.last_checked() is None
        store.set_app_selected("bank.app", True)
        store.record_check(100, ("bank.app",))
        store.set_app_selected("bank.app", False)
        with pytest.raises(NotificationStoreError, match="settings changed"):
            store.record_check(101, ("bank.app",))
        assert store.last_checked() == 100
    finally:
        store.close()
    reopened = NotificationStore(path)
    try:
        assert reopened.last_checked() == 100
    finally:
        reopened.close()


def test_delete_rule_rejects_stale_review(store: NotificationStore) -> None:
    store.set_app_selected("bank.app", True)
    rule = Rule("Declined", "bank.app", "ignore", body=Pattern("Declined"))
    store.save_rule("first", rule)
    revision = store.rule_revision("bank.app")
    store.save_rule("second", rule)
    with pytest.raises(NotificationStoreError, match="changed"):
        store.delete_rule("first", expected_revision=revision)
    assert [entry.id for entry in store.rules("bank.app")] == ["first", "second"]
    store.delete_rule("first", expected_revision=store.rule_revision("bank.app"))
    assert [entry.id for entry in store.rules("bank.app")] == ["second"]


def test_skip_requires_the_reviewed_notification_snapshot(store: NotificationStore) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    intake(store, incoming())
    item = store.items(("pending",))[0]
    intake(store, incoming(version="v2", body="Paid 6 EUR"))
    with pytest.raises(NotificationStoreError, match="changed"):
        store.skip(item.id, snapshot=item.review_snapshot)
    current = store.items(("pending",))[0]
    store.skip(current.id, snapshot=current.review_snapshot)
    assert len(store.items(("skipped",))) == 1


def test_recent_batch_respects_capacity_expiry_and_never_partially_applies(
    store: NotificationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    for key in ("first", "second"):
        store.intake(
            incoming(key),
            now=NOW,
            source="manual",
            evaluate=lambda _: Evaluation("unmatched"),
        )
    samples = store.items(("unmatched",))
    batch = tuple((item.id, item.snapshot, pending(incoming(item.key))) for item in samples)
    monkeypatch.setattr(notification_store, "MAX_PENDING", 1)
    with pytest.raises(NotificationStoreError, match="capacity"):
        store.apply_recent_evaluations(batch, now=NOW)
    assert store.count(("unmatched",)) == 2
    assert store.count(("pending",)) == 0
    with pytest.raises(NotificationStoreError, match="expired"):
        store.apply_recent_evaluations(batch, now=NOW + 7 * DAY)
    assert store.count(("unmatched",)) == 2


def test_notification_pages_are_bounded_and_newest_first(store: NotificationStore) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    for index in range(3):
        intake(store, incoming(key=str(index)), now=NOW + index)
    assert store.count(("pending",)) == 3
    assert [item.key for item in store.items(("pending",), limit=2, newest_first=True)] == [
        "2",
        "1",
    ]
    assert [
        item.key for item in store.items(("pending",), limit=2, offset=2, newest_first=True)
    ] == ["0"]
    with pytest.raises(NotificationStoreError, match="page"):
        store.items(("pending",), limit=0)


def test_old_history_recovery_requires_explicit_duplicate_review(store: NotificationStore) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    store.reset_history(NOW)
    item = incoming(posted=NOW - 10)
    assert intake(store, item, now=NOW + 1) == "too_old"
    with pytest.raises(NotificationStoreError, match="possible duplicate"):
        store.recover_old(item, now=NOW + 1, confirm_possible_duplicate=False)
    item_id = store.recover_old(item, now=NOW + 1, confirm_possible_duplicate=True)
    recovered = store.items(("uncertain",))[0]
    assert recovered.id == item_id and recovered.body == item.body
    with pytest.raises(NotificationStoreError, match="retained"):
        store.recover_old(item, now=NOW + 1, confirm_possible_duplicate=True)
    with pytest.raises(NotificationStoreError, match="possible duplicate"):
        store.apply_evaluation(recovered.id, recovered.snapshot, pending(item))
    store.apply_evaluation(
        recovered.id, recovered.snapshot, pending(item), confirm_possible_duplicate=True
    )
    assert len(store.items(("pending",))) == 1


def test_old_recovery_candidates_are_visible_selected_and_not_already_retained(
    store: NotificationStore,
) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    store.reset_history(NOW)
    retained = incoming(key="retained", posted=NOW - 20)
    store.recover_old(retained, now=NOW + 1, confirm_possible_duplicate=True)
    old = incoming(key="old", posted=NOW - 10)
    new = incoming(key="new", posted=NOW + 1)
    other = incoming(key="other", app="other.app", posted=NOW - 10)
    empty = replace(old, key="empty", title=None, body=None)
    assert store.old_recovery_candidates((old, retained, old, new, other, empty), now=NOW + 1) == (
        old,
    )
    assert len(store.items(("uncertain",))) == 1
    store.recover_old(old, now=NOW + 1, confirm_possible_duplicate=True)
    assert store.old_recovery_candidates((old,), now=NOW + 1) == ()
    store.set_mode("off")
    with pytest.raises(NotificationStoreError, match="Enable"):
        store.old_recovery_candidates((old,), now=NOW + 1)


def test_duplicate_checks_precede_evaluation_and_distinct_occurrences_are_kept(
    store: NotificationStore,
) -> None:
    store.set_mode("background")
    store.set_app_selected("bank.app", True)
    calls = 0

    def evaluate(item: IncomingNotification) -> Evaluation:
        nonlocal calls
        calls += 1
        return pending(item)

    first = incoming()
    assert store.intake(first, now=NOW, source="background", evaluate=evaluate) == "recorded"
    assert store.intake(first, now=NOW, source="manual", evaluate=evaluate) == "duplicate"
    second = incoming("another-key", posted=NOW)
    assert store.intake(second, now=NOW, source="background", evaluate=evaluate) == "recorded"
    assert calls == 2
    assert len(store.items(("pending",))) == 2


def test_reused_key_with_new_post_time_is_flagged_instead_of_duplicated(
    store: NotificationStore,
) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    assert intake(store, incoming()) == "recorded"
    second = incoming(posted=NOW, version="v2")
    assert intake(store, second) == "uncertain"
    items = store.items(("pending",))
    assert len(items) == 1 and items[0].changed
    suspect = store.items(("uncertain",))[0]
    with pytest.raises(NotificationStoreError, match="possible duplicate"):
        store.apply_evaluation(suspect.id, suspect.snapshot, pending(second))
    store.apply_evaluation(
        suspect.id, suspect.snapshot, pending(second), confirm_possible_duplicate=True
    )
    assert len(store.items(("pending",))) == 2


def test_separate_occurrences_within_a_second_are_not_silently_merged(
    store: NotificationStore,
) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    first = replace(incoming(posted=NOW), posted_millis=NOW * 1000)
    second = replace(first, posted_millis=NOW * 1000 + 1)
    assert intake(store, first) == "recorded"
    assert intake(store, second) == "uncertain"
    assert len(store.items(("pending",))) == 1
    assert len(store.items(("uncertain",))) == 1


def test_uncertain_update_refreshes_sample_without_reviving_cleared_content(
    store: NotificationStore,
) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    assert intake(store, incoming()) == "recorded"
    second = incoming(posted=NOW, version="v2")
    assert intake(store, second) == "uncertain"
    original = store.items(("uncertain",))[0]
    changed = replace(second, version="v3", body="Paid 7 EUR")
    assert intake(store, changed) == "changed"
    refreshed = store.items(("uncertain",))[0]
    assert refreshed.body == "Paid 7 EUR"
    with pytest.raises(NotificationStoreError, match="changed"):
        store.apply_evaluation(
            original.id, original.snapshot, pending(second), confirm_possible_duplicate=True
        )
    store.clear_captured()
    assert intake(store, replace(changed, version="v4"), now=NOW) == "changed"
    cleared = store.items(("uncertain",))[0]
    assert cleared.title is None and cleared.body is None


def test_updates_preserve_editing_and_do_not_reopen_imported_items(
    store: NotificationStore,
) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    assert intake(store, incoming()) == "recorded"
    item = store.items(("pending",))[0]
    store.bind_journal(item.id, JOURNAL)
    store.set_editing(item.id, True)
    assert intake(store, incoming(version="v2", body="Paid 6 EUR")) == "changed"
    retained = store.items(("pending",))[0]
    assert retained.body == "Paid 5 EUR" and retained.changed
    store.set_editing(item.id, False)

    def pending_bound(item: IncomingNotification) -> Evaluation:
        return Evaluation("pending", "Purchases", pending(item).draft, JOURNAL)

    assert (
        store.intake(
            incoming(version="v2", body="Paid 6 EUR"),
            now=NOW,
            source="manual",
            evaluate=pending_bound,
        )
        == "updated"
    )
    assert store.items(("pending",))[0].body == "Paid 6 EUR"
    store.begin_import(item.id, journal_id=JOURNAL)
    store.finish_import(item.id, saved=True)
    assert intake(store, incoming(version="v3", body="Paid 7 EUR")) == "changed"
    imported = store.items(("imported",))[0]
    assert imported.changed and imported.body is None and imported.draft is None


def test_uncertain_saves_survive_restart_and_require_resolution(tmp_path: Path) -> None:
    path = tmp_path / "notifications.sqlite"
    store = NotificationStore(path)
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    assert intake(store, incoming()) == "recorded"
    item_id = store.items(("pending",))[0].id
    store.bind_journal(item_id, JOURNAL)
    store.begin_import(item_id, journal_id=JOURNAL)
    store.close()
    store = NotificationStore(path)
    try:
        assert store.items(("saving",))[0].id == item_id
        with pytest.raises(NotificationStoreError):
            store.begin_import(item_id, journal_id=JOURNAL)
        with pytest.raises(NotificationStoreError):
            store.reset_history(NOW)
        store.finish_import(item_id, saved=False)
        assert store.items(("pending",))[0].id == item_id
        store.begin_import(item_id, journal_id=JOURNAL)
        store.finish_import(item_id, saved=True)
        assert store.items(("imported",))[0].draft is None
    finally:
        store.close()


def test_pending_draft_cannot_be_silently_retargeted(store: NotificationStore) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    assert intake(store, incoming()) == "recorded"
    item_id = store.items(("pending",))[0].id
    with pytest.raises(NotificationStoreError, match="another journal"):
        store.begin_import(item_id, journal_id=JOURNAL)
    store.bind_journal(item_id, JOURNAL)
    with pytest.raises(NotificationStoreError, match="another journal"):
        store.bind_journal(item_id, "b" * 64)
    with pytest.raises(NotificationStoreError, match="another journal"):
        store.begin_import(item_id, journal_id="b" * 64)
    assert intake(store, incoming(version="v2", body="Paid 7 EUR")) == "changed"
    item = store.items(("pending",))[0]
    assert item.body == "Paid 5 EUR"
    assert item.journal_id == JOURNAL


def test_begin_import_rejects_a_changed_review_snapshot(store: NotificationStore) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    assert intake(store, incoming()) == "recorded"
    item = store.items(("pending",))[0]
    store.bind_journal(item.id, JOURNAL)
    store.set_editing(item.id, True)
    item = store.items(("pending",))[0]
    assert intake(store, incoming(version="v2", body="Paid 7 EUR")) == "changed"
    with pytest.raises(NotificationStoreError, match="changed"):
        store.begin_import(item.id, journal_id=JOURNAL, snapshot=item.review_snapshot)
    assert store.items(("pending",))[0].changed


def test_begin_review_binds_only_the_presented_candidate(store: NotificationStore) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    assert intake(store, incoming()) == "recorded"
    item = store.items(("pending",))[0]
    assert intake(store, incoming(version="v2", body="Paid 7 EUR")) == "updated"
    with pytest.raises(NotificationStoreError, match="changed"):
        store.begin_review(item.id, item.review_snapshot, journal_id=JOURNAL)
    current = store.items(("pending",))[0]
    bound = store.begin_review(current.id, current.review_snapshot, journal_id=JOURNAL)
    assert bound.journal_id == JOURNAL
    with pytest.raises(NotificationStoreError, match="another journal"):
        store.begin_review(bound.id, bound.review_snapshot, journal_id="b" * 64)
    assert intake(store, incoming(version="v3", body="Paid 9 EUR")) == "changed"
    assert store.items(("pending",))[0].body == "Paid 7 EUR"


def test_rule_edit_does_not_invalidate_a_pending_transaction_review(
    store: NotificationStore,
) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    assert intake(store, incoming()) == "recorded"
    item = store.items(("pending",))[0]
    assert not item.stale_rule
    bound = store.begin_review(item.id, item.review_snapshot, journal_id=JOURNAL)
    store.save_rule(
        "new-rule", Rule("New rule", "bank.app", "ignore", body=Pattern.parse("{message}"))
    )
    refreshed = store.items(("pending",))[0]
    assert refreshed.stale_rule
    assert refreshed.review_snapshot == bound.review_snapshot
    store.begin_import(bound.id, journal_id=JOURNAL, snapshot=bound.review_snapshot)
    assert len(store.items(("saving",))) == 1


def test_legacy_pending_rule_revision_is_unknown_until_re_evaluated(tmp_path: Path) -> None:
    path = tmp_path / "notifications.sqlite"
    store = NotificationStore(path)
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    intake(store, incoming())
    store.close()
    with sqlite3.connect(path) as database:
        database.execute("ALTER TABLE notification_items DROP COLUMN rule_revision")
    store = NotificationStore(path)
    try:
        item = store.items(("pending",))[0]
        assert item.stale_rule
        store.apply_evaluation(item.id, item.snapshot, pending(incoming()))
        assert not store.items(("pending",))[0].stale_rule
    finally:
        store.close()


def test_stale_rule_tracks_edits_deletion_and_explicit_re_evaluation(
    store: NotificationStore,
) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    store.set_app_selected("other.app", True)
    rule = Rule("Purchases", "bank.app", "ignore", body=Pattern("Paid"))
    store.save_rule("bank-rule", rule)
    intake(store, incoming())
    item = store.items(("pending",))[0]
    assert not item.stale_rule

    store.save_rule("other-rule", Rule("Other", "other.app", "ignore", body=Pattern("Paid")))
    assert not store.items(("pending",))[0].stale_rule
    store.save_rule("bank-rule", replace(rule, name="Changed"))
    changed = store.items(("pending",))[0]
    assert changed.stale_rule
    assert changed.draft == item.draft
    assert changed.review_snapshot == item.review_snapshot

    store.apply_evaluation(changed.id, changed.snapshot, pending(incoming()))
    assert not store.items(("pending",))[0].stale_rule
    store.delete_rule("bank-rule")
    deleted = store.items(("pending",))[0]
    assert deleted.stale_rule
    assert deleted.draft == item.draft


def test_retention_and_cutoff_prevent_replay_after_history_is_pruned(
    store: NotificationStore,
) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    item = incoming(posted=NOW - 5)
    assert intake(store, item) == "recorded"
    store.prune(NOW + 30 * DAY)
    assert store.expired_count() == 1
    assert store.items(("expired",))[0].body is None
    store.prune(NOW + 90 * DAY)
    assert store.items(("expired",)) == ()
    assert intake(store, item, now=NOW + 90 * DAY) == "too_old"
    store.acknowledge_expired(1)
    assert store.expired_count() == 0


def test_acknowledging_expired_requires_the_count_shown(store: NotificationStore) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    intake(store, incoming("first"))
    store.prune(NOW + 30 * DAY)
    intake(store, incoming("second", posted=NOW + 30 * DAY), now=NOW + 30 * DAY)
    store.prune(NOW + 60 * DAY)
    with pytest.raises(NotificationStoreError, match="changed"):
        store.acknowledge_expired(1)
    assert store.expired_count() == 2
    store.acknowledge_expired(2)
    assert store.expired_count() == 0


def test_count_caps_do_not_evict_pending_and_advance_cutoff_for_records(
    store: NotificationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notification_store, "MAX_PENDING", 1)
    monkeypatch.setattr(notification_store, "MAX_RECORDS", 2)
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    first = incoming("1", posted=NOW - 4)
    assert intake(store, first) == "recorded"
    assert intake(store, incoming("2", posted=NOW - 3)) == "capacity"
    store.skip(store.items(("pending",))[0].id)
    assert intake(store, incoming("2", posted=NOW - 3)) == "recorded"
    store.skip(store.items(("pending",))[0].id)
    assert intake(store, incoming("3", posted=NOW - 2)) == "recorded"
    assert len(store.items(("pending", "skipped"))) == 2
    assert intake(store, first) == "too_old"


def test_samples_expire_and_deselect_drops_raw_samples(store: NotificationStore) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    result = store.intake(
        incoming(), now=NOW, source="manual", evaluate=lambda _: Evaluation("unmatched")
    )
    assert result == "recorded"
    store.set_app_selected("bank.app", False)
    sample = store.items(("unmatched",))[0]
    assert sample.title is None and sample.body is None
    store.set_app_selected("bank.app", True)
    assert intake(store, incoming()) == "duplicate"


def test_clear_and_reset_do_not_replay_still_visible_notifications(
    store: NotificationStore,
) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    item = incoming()
    assert intake(store, item) == "recorded"
    store.clear_captured()
    assert store.items(("cleared",))[0].draft is None
    assert intake(store, item) == "duplicate"
    store.reset_history(NOW)
    assert intake(store, item) == "too_old"
    assert intake(store, incoming("later", posted=NOW + 1), now=NOW + 1) == "recorded"


def test_clock_reversal_and_evaluation_failures_are_explicit_and_atomic(
    store: NotificationStore,
) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)

    def fails(_: IncomingNotification) -> Evaluation:
        raise ValueError("rule failed")

    with pytest.raises(ValueError, match="rule failed"):
        store.intake(incoming(), now=NOW, source="manual", evaluate=fails)
    assert store.items(("pending", "unmatched", "ignored")) == ()
    assert intake(store, incoming()) == "recorded"
    with pytest.raises(NotificationStoreError, match="clock moved backwards"):
        store.prune(NOW - 1)


def test_rules_round_trip_and_reorder_without_replaying_samples(store: NotificationStore) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    sample = incoming()
    assert (
        store.intake(sample, now=NOW, source="manual", evaluate=lambda _: Evaluation("unmatched"))
        == "recorded"
    )
    purchase = Rule(
        "Purchase",
        "bank.app",
        "suggest",
        body=Pattern.parse("Paid {amount} {currency}"),
        mapping=TransactionMapping(
            kind=Value("expense"),
            amount=Value("amount", captured=True),
            currency=Value("currency", captured=True),
            account=Value("Assets:Bank"),
        ),
    )
    ignored = Rule("Declined", "bank.app", "ignore", body=Pattern.parse("Declined {amount}"))
    assert decode_rule(encode_rule(purchase)) == purchase
    store.save_rule("purchase", purchase)
    store.save_rule("ignored", ignored)
    store.reorder_rules("bank.app", ("ignored", "purchase"))
    assert [entry.rule for entry in store.rules("bank.app")] == [ignored, purchase]
    with pytest.raises(NotificationStoreError, match="once"):
        store.reorder_rules("bank.app", ("purchase", "purchase"))
    assert intake(store, sample) == "duplicate"
    store.delete_rule("ignored")
    assert [entry.id for entry in store.rules("bank.app")] == ["purchase"]


def test_save_rule_at_priority_is_atomic_and_does_not_change_other_rules(
    store: NotificationStore,
) -> None:
    store.set_app_selected("bank.app", True)
    first = Rule("First", "bank.app", "ignore", body=Pattern.parse("{message}"))
    second = Rule("Second", "bank.app", "ignore", body=Pattern.parse("Declined {amount}"))
    store.save_rule("first", first)
    store.save_rule("second", second, position=0)
    assert [entry.id for entry in store.rules("bank.app")] == ["second", "first"]
    revision = store.rule_revision("bank.app")
    with pytest.raises(NotificationStoreError, match="position"):
        store.save_rule("second", replace(second, name="Changed"), position=2)
    assert [entry.rule for entry in store.rules("bank.app")] == [second, first]
    store.save_rule("second", replace(second, name="Changed"), position=1)
    assert [entry.rule.name for entry in store.rules("bank.app")] == ["First", "Changed"]
    with pytest.raises(NotificationStoreError, match="changed"):
        store.save_rule("first", replace(first, name="Stale"), expected_revision=revision)
    assert [entry.rule.name for entry in store.rules("bank.app")] == ["First", "Changed"]


def test_saving_rule_rejects_a_source_deselected_during_edit(store: NotificationStore) -> None:
    store.set_app_selected("bank.app", True)
    rule = Rule("Declined", "bank.app", "ignore", body=Pattern("Declined"))
    store.save_rule("rule", rule)
    revision = store.rule_revision("bank.app")
    store.set_app_selected("bank.app", False)
    with pytest.raises(NotificationStoreError, match="Select the source app"):
        store.save_rule("rule", replace(rule, name="Edited"), expected_revision=revision)
    assert store.rules("bank.app")[0].rule == rule


def test_explicit_re_evaluation_requires_unchanged_preview_and_preserves_edits(
    store: NotificationStore,
) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    sample = incoming()
    assert (
        store.intake(sample, now=NOW, source="manual", evaluate=lambda _: Evaluation("unmatched"))
        == "recorded"
    )
    original = store.items(("unmatched",))[0]
    store.apply_evaluation(original.id, original.snapshot, pending(sample))
    with pytest.raises(NotificationStoreError, match="preview"):
        store.apply_evaluation(original.id, original.snapshot, pending(sample))
    item = store.items(("pending",))[0]
    store.bind_journal(item.id, JOURNAL)
    store.set_editing(item.id, True)
    item = store.items(("pending",))[0]
    with pytest.raises(NotificationStoreError, match="Confirm"):
        store.apply_evaluation(item.id, item.snapshot, Evaluation("ignored", "Declined"))
    assert store.items(("pending",))[0].draft is not None
    store.apply_evaluation(
        item.id, item.snapshot, Evaluation("ignored", "Declined"), overwrite_edits=True
    )
    assert store.items(("pending",)) == ()
    assert store.items(("ignored",))[0].body == "Paid 5 EUR"


def test_re_evaluation_does_not_reopen_imported_or_retargeted_items(
    store: NotificationStore,
) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    assert intake(store, incoming()) == "recorded"
    item_id = store.items(("pending",))[0].id
    store.bind_journal(item_id, JOURNAL)
    item = store.items(("pending",))[0]
    with pytest.raises(NotificationStoreError, match="another journal"):
        store.apply_evaluation(
            item.id,
            item.snapshot,
            Evaluation("pending", "Other", {"amount": "5"}, "b" * 64),
        )
    store.begin_import(item_id, journal_id=JOURNAL)
    store.finish_import(item_id, saved=True)
    imported = store.items(("imported",))[0]
    with pytest.raises(NotificationStoreError, match="unavailable"):
        store.apply_evaluation(imported.id, imported.snapshot, pending(incoming()))


def test_re_evaluation_rejects_rule_changes_since_preview(store: NotificationStore) -> None:
    store.set_mode("manual")
    store.set_app_selected("bank.app", True)
    sample = incoming()
    assert (
        store.intake(sample, now=NOW, source="manual", evaluate=lambda _: Evaluation("unmatched"))
        == "recorded"
    )
    item = store.items(("unmatched",))[0]
    store.save_rule("ignore", Rule("Ignore", "bank.app", "ignore", body=Pattern.parse("{text}")))
    with pytest.raises(NotificationStoreError, match="preview"):
        store.apply_evaluation(item.id, item.snapshot, Evaluation("ignored", "Ignore"))
    assert store.items(("unmatched",))[0].id == item.id


def test_rule_codec_rejects_untrusted_or_unknown_structure() -> None:
    with pytest.raises(RuleCodecError):
        decode_rule('{"name":"incomplete"}')
    with pytest.raises(RuleCodecError):
        decode_rule("x" * 70_000)


def test_invalid_capture_mapping_can_be_saved_only_as_disabled_draft(
    store: NotificationStore,
) -> None:
    store.set_app_selected("bank.app", True)
    rule = Rule(
        "Purchase",
        "bank.app",
        "suggest",
        body=Pattern.parse("Paid {amount}"),
        mapping=TransactionMapping(amount=Value("missing", captured=True)),
    )
    with pytest.raises(NotificationStoreError, match="undefined capture"):
        store.save_rule("purchase", rule)
    store.save_rule("purchase", replace(rule, enabled=False))
    assert store.rules("bank.app")[0].rule.missing_captures() == ("missing",)
