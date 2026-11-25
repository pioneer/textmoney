import asyncio
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest
from popup_keyboard import PopupMenuBackScope

from app.domain.notification_rules import NotificationRuleError, Pattern, Rule, Value
from app.infrastructure.notification_store import (
    Evaluation,
    IncomingNotification,
    NotificationStore,
    NotificationStoreError,
)
from app.ui.dialogs import EditorDialog
from app.ui.notification_rules import NotificationRuleEditor


def _editor(
    path: Path, *, rule_id: str | None = None
) -> tuple[NotificationStore, NotificationRuleEditor]:
    store = NotificationStore(path)
    store.set_app_selected("bank.app", True)
    return store, _new_editor(store, rule_id=rule_id)


def _new_editor(store: NotificationStore, *, rule_id: str | None = None) -> NotificationRuleEditor:
    return NotificationRuleEditor(
        page=MagicMock(spec=ft.Page),
        translate=lambda key, **values: key.format(**values),
        store=store,
        app_id="bank.app",
        rule_id=rule_id,
        today=lambda: date(2026, 11, 23),
    )


def _set_mapping(editor: NotificationRuleEditor, field: str, mode: str, value: str) -> None:
    selector, entry = editor.mappings[field]
    selector.value = mode
    entry.value = value


def _fill_minimum(editor: NotificationRuleEditor) -> None:
    editor.name.value = "Card purchase"
    editor.body_pattern.value = "Paid {amount} {currency} on {date}"
    _set_mapping(editor, "kind", "fixed", "expense")
    _set_mapping(editor, "amount", "capture", "amount")
    _set_mapping(editor, "currency", "capture", "currency")
    _set_mapping(editor, "account", "fixed", "Assets:Bank")
    _set_mapping(editor, "date", "capture", "date")
    _set_mapping(editor, "category", "fixed", "Expenses:Food")


def test_ai_draft_opens_separately_and_preserves_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    store.set_app_selected("bank.app", True)
    opened: list[NotificationRuleEditor] = []
    monkeypatch.setattr(NotificationRuleEditor, "open", lambda editor: opened.append(editor))
    proposed = Rule("AI draft", "bank.app", "ignore", body=Pattern("Declined"))

    async def start(title, body, callback) -> None:
        assert title == "Bank" and body == "Original"
        callback(proposed, "Bank", "Redacted")

    try:
        editor = NotificationRuleEditor(
            MagicMock(spec=ft.Page),
            lambda key, **values: key,
            store,
            "bank.app",
            on_ai=start,
        )
        editor.name.value = "Unsaved parent"
        editor.sample_title.value = "Bank"
        editor.sample_body.value = "Original"
        asyncio.run(editor._ai(MagicMock()))
        assert editor.name.value == "Unsaved parent"
        assert editor.sample_body.value == "Original"
        assert len(opened) == 1
        assert opened[0].name.value == "AI draft"
        assert opened[0].sample_body.value == "Redacted"
        assert opened[0].enabled.value is False
        assert store.rules("bank.app") == ()
    finally:
        store.close()


def test_saving_ai_draft_closes_parent_before_returning_to_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    store.set_app_selected("bank.app", True)
    opened: list[NotificationRuleEditor] = []
    monkeypatch.setattr(NotificationRuleEditor, "open", lambda editor: opened.append(editor))
    proposed = Rule("AI draft", "bank.app", "ignore", body=Pattern("Declined"), enabled=False)
    page = MagicMock(spec=ft.Page)
    saved = MagicMock()

    async def start(title, body, callback) -> None:
        callback(proposed, title, body)

    try:
        parent = NotificationRuleEditor(
            page,
            lambda key, **values: key,
            store,
            "bank.app",
            on_saved=saved,
            on_ai=start,
        )
        parent.name.value = "Unsaved parent"
        asyncio.run(parent._ai(MagicMock()))
        assert parent.name.value == "Unsaved parent"
        assert len(opened) == 1
        asyncio.run(opened[0]._cancel(MagicMock()))
        assert page.pop_dialog.call_count == 1
        assert parent.name.value == "Unsaved parent"
        saved.assert_not_called()
        page.pop_dialog.reset_mock()
        opened.clear()
        asyncio.run(parent._ai(MagicMock()))
        asyncio.run(opened[0]._save(MagicMock()))
        assert page.pop_dialog.call_count == 2
        saved.assert_called_once_with(proposed)
        assert store.rules("bank.app")[0].rule == proposed
    finally:
        store.close()


def test_editor_builds_and_round_trips_fixed_and_captured_mappings(tmp_path: Path) -> None:
    store, editor = _editor(tmp_path / "notifications.sqlite")
    try:
        _fill_minimum(editor)
        _set_mapping(editor, "destination", "fixed", "Assets:Savings")
        _set_mapping(editor, "destination_amount", "capture", "received")
        _set_mapping(editor, "payee", "fixed", "Corner shop")
        _set_mapping(editor, "description", "capture", "memo")
        _set_mapping(editor, "time", "fixed", "12:30")
        editor.title_pattern.value = "Bank"
        editor.priority.value = "1"
        editor.decimal_separator.value = ","
        editor.grouping_separator.value = "."
        editor.date_format.value = "%d/%m/%Y"
        editor.currency_symbols.value = "€=EUR\nUS$=USD"

        assert editor.enabled.value is False
        saved = editor.save_rule()
        stored = store.rules("bank.app")[0]
        assert stored.id == editor.rule_id
        assert stored.rule == saved
        mapping = stored.rule.mapping
        assert mapping is not None
        assert mapping.amount == Value("amount", captured=True)
        assert mapping.account == Value("Assets:Bank")
        assert mapping.destination_amount == Value("received", captured=True)
        assert mapping.description == Value("memo", captured=True)
        assert mapping.decimal_separator == ","
        assert mapping.grouping_separator == "."
        assert mapping.date_format == "%d/%m/%Y"
        assert mapping.currency_symbols == (("€", "EUR"), ("US$", "USD"))
        assert stored.rule.app_id == "bank.app"
        assert stored.rule.enabled is False
    finally:
        store.close()


def test_ignore_rule_has_no_mapping_and_does_not_clear_mapping_draft(tmp_path: Path) -> None:
    store, editor = _editor(tmp_path / "notifications.sqlite")
    try:
        _fill_minimum(editor)
        editor.action.value = "ignore"
        saved = editor.save_rule()
        assert saved.action == "ignore"
        assert saved.mapping is None
        assert editor.mappings["amount"][1].value == "amount"
    finally:
        store.close()


def test_pattern_editor_preserves_literal_boundary_whitespace(tmp_path: Path) -> None:
    store, editor = _editor(tmp_path / "notifications.sqlite")
    try:
        editor.name.value = "Trailing space"
        editor.action.value = "ignore"
        editor.body_pattern.value = " Paid {amount} "
        editor.sample_body.value = " Paid 5 "
        assert editor.test_sample().proposed_matched
        assert editor.save_rule().body == Pattern(" Paid {amount} ")
    finally:
        store.close()


def test_editor_accepts_a_disabled_copy_and_prefilled_sample(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_app_selected("bank.app", True)
        draft = Rule("Draft", "bank.app", "ignore", body=Pattern("Paid {amount}"), enabled=True)
        editor = NotificationRuleEditor(
            MagicMock(spec=ft.Page),
            lambda key, **values: key,
            store,
            "bank.app",
            draft=draft,
            sample_title="Bank",
            sample_body="Paid 5",
        )
        assert editor.enabled.value is False
        assert editor.priority.value == "1"
        assert editor.sample_title.value == "Bank"
        assert editor.sample_body.value == "Paid 5"
        assert editor.test_sample().proposed_matched
        assert editor.save_rule().enabled is False
        with pytest.raises(NotificationStoreError, match="source app"):
            NotificationRuleEditor(
                MagicMock(spec=ft.Page),
                lambda key, **values: key,
                store,
                "other.app",
                draft=draft,
            )
    finally:
        store.close()


def test_editor_uses_only_selected_app_samples_without_changing_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    try:
        store.set_mode("manual")
        for app_id in ("bank.app", "other.app"):
            store.set_app_selected(app_id, True)
            store.intake(
                IncomingNotification(app_id, "key", 100, "v1", "", "Paid 5"),
                now=101,
                source="manual",
                evaluate=lambda _: Evaluation("unmatched"),
            )
        editor = _new_editor(store)
        update = MagicMock()
        monkeypatch.setattr(editor, "_update_form", update)
        assert len(editor.samples) == 1
        editor.sample_choice.value = next(iter(editor.samples))
        editor._sample_selected(MagicMock())
        assert editor.sample_title.value == ""
        assert editor.sample_body.value == "Paid 5"
        update.assert_called_once()
        editor.sample_body.value = "Redacted"
        assert store.items(("unmatched",))[0].body == "Paid 5"
    finally:
        store.close()


def test_rule_mapping_changes_update_disabled_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, editor = _editor(tmp_path / "notifications.sqlite")
    try:
        update = MagicMock()
        monkeypatch.setattr(editor, "_update_form", update)
        mode, value = editor.mappings["amount"]
        assert value.disabled
        mode.value = "capture"
        editor._mapping_mode_changed(MagicMock())
        assert not value.disabled
        editor.action.value = "ignore"
        editor._action_changed(MagicMock())
        assert value.disabled
        assert update.call_count == 2
    finally:
        store.close()


def test_editor_opens_through_shared_dialog_helper_and_keeps_native_controls(
    tmp_path: Path,
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    store.set_app_selected("bank.app", True)
    page = MagicMock(spec=ft.Page)
    page.platform = ft.PagePlatform.ANDROID
    page.web = False
    page.width = 390
    editor = NotificationRuleEditor(
        page=page,
        translate=lambda key, **values: key.format(**values),
        store=store,
        app_id="bank.app",
    )
    try:
        dialog = editor.open()
        assert isinstance(dialog, EditorDialog)
        assert isinstance(dialog.content, PopupMenuBackScope)
        assert dialog.content.content is editor.form
        assert dialog.actions == [editor.cancel_button, editor.save_button]
        assert editor.cancel_button.on_click is not None
        assert editor.save_button.on_click is not None
        assert editor.source.read_only
        assert editor.enabled.value is False
        page.show_dialog.assert_called_once_with(dialog)
    finally:
        store.close()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("body_pattern", "Paid {bad-name}", "Invalid capture"),
        ("currency_symbols", "€ EUR", "symbol=CODE"),
        ("grouping_separator", ".", "must differ"),
        ("grouping_separator", "invalid", "Unsupported grouping"),
    ],
)
def test_editor_rejects_invalid_patterns_and_number_formats(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    store, editor = _editor(tmp_path / "notifications.sqlite")
    try:
        _fill_minimum(editor)
        if field == "body_pattern":
            editor.body_pattern.value = value
        elif field == "currency_symbols":
            editor.currency_symbols.value = value
        else:
            editor.decimal_separator.value = "."
            editor.grouping_separator.value = value
        with pytest.raises(NotificationRuleError, match=message):
            editor.build_rule()
    finally:
        store.close()


def test_invalid_pattern_reports_actionable_feedback(tmp_path: Path) -> None:
    store, editor = _editor(tmp_path / "notifications.sqlite")
    try:
        _fill_minimum(editor)
        editor.body_pattern.value = "Paid {bad-name}"
        asyncio.run(editor._test(MagicMock()))
        assert editor.sample_result.value == "notification_rule_error_pattern"
        asyncio.run(editor._save(MagicMock()))
        assert editor.status.value == "notification_rule_error_pattern"
        assert store.rules("bank.app") == ()
    finally:
        store.close()


def test_rule_editor_reports_storage_failures_and_preserves_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, editor = _editor(tmp_path / "notifications.sqlite")
    try:
        _fill_minimum(editor)
        error = sqlite3.OperationalError("storage unavailable")
        monkeypatch.setattr(editor, "test_sample", MagicMock(side_effect=error))
        asyncio.run(editor._test(MagicMock()))
        assert editor.sample_result.value == "notification_rule_test_failed"
        monkeypatch.setattr(editor, "save_rule", MagicMock(side_effect=error))
        asyncio.run(editor._save(MagicMock()))
        assert editor.status.value == "notification_rule_save_failed"
        assert editor.name.value == "Card purchase"
        assert store.rules("bank.app") == ()
    finally:
        store.close()


def test_sample_test_reports_higher_priority_winner_and_captures_without_writing(
    tmp_path: Path,
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    store.set_app_selected("bank.app", True)
    try:
        store.save_rule(
            "ignore-first",
            Rule("Ignore first", "bank.app", "ignore", body=Pattern.parse("Paid {amount} EUR")),
        )
        editor = _new_editor(store)
        _fill_minimum(editor)
        editor.body_pattern.value = "Paid {amount} EUR"
        editor.priority.value = "2"
        editor.sample_body.value = "Paid 8 EUR"
        revision = store.rule_revision("bank.app")

        result = editor.test_sample()

        assert result.proposed_matched
        assert result.winning_rule_id == "ignore-first"
        assert result.evaluation.outcome == "ignored"
        assert result.captures == {"amount": "8"}
        assert "notification_rule_test_winner" in editor._test_description(result)
        assert store.rule_revision("bank.app") == revision
        assert [entry.id for entry in store.rules("bank.app")] == ["ignore-first"]
        assert store.items(("pending", "ignored", "unmatched")) == ()
    finally:
        store.close()


def test_sample_test_compares_existing_and_proposed_outcomes_without_saving(
    tmp_path: Path,
) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite")
    store.set_app_selected("bank.app", True)
    try:
        store.save_rule(
            "existing",
            Rule("Old", "bank.app", "ignore", body=Pattern("Paid {amount} EUR")),
        )
        editor = _new_editor(store, rule_id="existing")
        editor.tr = lambda key, **values: f"{key} {values}" if values else key
        editor.action.value = "suggest"
        _set_mapping(editor, "amount", "fixed", "9")
        editor.sample_body.value = "Paid 8 EUR"
        revision = store.rule_revision("bank.app")

        result = editor.test_sample()

        assert result.previous_evaluation.outcome == "ignored"
        assert result.evaluation.outcome == "pending"
        assert result.evaluation.draft is not None
        assert result.evaluation.draft["amount"] == "9"
        description = editor._test_description(result)
        assert "notification_rule_test_current" in description
        assert "notification_rule_outcome_ignore" in description
        assert "notification_rule_test_proposed" in description
        assert "notification_rule_amount: 9" in description
        assert store.rule_revision("bank.app") == revision
    finally:
        store.close()


def test_sample_test_compares_existing_and_proposed_field_values(tmp_path: Path) -> None:
    store, editor = _editor(tmp_path / "notifications.sqlite")
    try:
        _fill_minimum(editor)
        editor.enabled.value = True
        editor.save_rule()
        editor = _new_editor(store, rule_id=editor.rule_id)
        editor.sample_body.value = "Paid 8 EUR on 2026-11-20"
        _set_mapping(editor, "amount", "fixed", "9")
        result = editor.test_sample()
        assert result.previous_evaluation.draft is not None
        assert result.previous_evaluation.draft["amount"] == "8"
        assert result.evaluation.draft is not None
        assert result.evaluation.draft["amount"] == "9"
        description = editor._test_description(result)
        assert description.index("notification_rule_amount: 8") < description.index(
            "notification_rule_amount: 9"
        )
    finally:
        store.close()


def test_sample_test_reports_draft_errors_without_persisting_candidate(
    tmp_path: Path,
) -> None:
    store, editor = _editor(tmp_path / "notifications.sqlite")
    try:
        editor.name.value = "Incomplete suggestion"
        editor.body_pattern.value = "Paid {amount} EUR"
        _set_mapping(editor, "amount", "capture", "amount")
        editor.sample_body.value = "Paid 8 EUR"
        before_revision = store.rule_revision("bank.app")

        result = editor.test_sample()

        assert result.winning_rule_id == editor.rule_id
        assert result.captures == {"amount": "8"}
        assert result.evaluation.draft is not None
        errors = result.evaluation.draft["errors"]
        assert isinstance(errors, (list, tuple))
        assert ("kind", "Required for import.") in errors
        assert store.rule_revision("bank.app") == before_revision
        assert store.rules("bank.app") == ()
        assert store.items(("pending", "ignored", "unmatched")) == ()
        description = editor._test_description(result)
        assert "notification_rule_amount: 8" in description
        assert "notification_rule_test_errors" in description
    finally:
        store.close()


def test_save_conflict_preserves_editor_draft(tmp_path: Path) -> None:
    store, editor = _editor(tmp_path / "notifications.sqlite")
    try:
        _fill_minimum(editor)
        store.save_rule(
            "concurrent",
            Rule("Concurrent", "bank.app", "ignore", body=Pattern.parse("Other")),
        )
        with pytest.raises(NotificationStoreError, match="changed"):
            editor.save_rule()
        assert editor.name.value == "Card purchase"
        assert [entry.id for entry in store.rules("bank.app")] == ["concurrent"]
    finally:
        store.close()
