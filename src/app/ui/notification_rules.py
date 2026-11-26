"""Reusable, local-only editor for named notification import rules."""

import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import date
from typing import Literal, cast
from uuid import uuid4

import flet as ft

from app.domain.ledger import Ledger
from app.domain.notification_rules import (
    NotificationRuleError,
    Pattern,
    Rule,
    TransactionMapping,
    Value,
)
from app.infrastructure.notification_import import RuleTest, test_rule
from app.infrastructure.notification_store import NotificationStore, NotificationStoreError, Outcome
from app.localization import format_epoch
from app.ui.dialogs import EditorDialog, show_dialog

logger = logging.getLogger(__name__)

Translate = Callable[..., str]
DraftCallback = Callable[[Rule, str | None, str | None], None]
AIStarter = Callable[[str | None, str | None, DraftCallback], Awaitable[None]]

_MAPPING_FIELDS = (
    ("kind", "notification_rule_kind"),
    ("amount", "notification_rule_amount"),
    ("currency", "notification_rule_currency"),
    ("account", "notification_rule_account"),
    ("destination", "notification_rule_destination"),
    ("destination_amount", "notification_rule_destination_amount"),
    ("category", "notification_rule_category"),
    ("payee", "notification_rule_payee"),
    ("description", "notification_rule_description"),
    ("date", "notification_rule_date"),
    ("time", "notification_rule_time"),
)
_MAPPING_MODES = ("none", "fixed", "capture")
_GROUPING_SEPARATORS: dict[str, str | None] = {
    "none": None,
    ".": ".",
    ",": ",",
    "space": " ",
    "nbsp": "\u00a0",
    "narrow": "\u202f",
}
_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y")
_DRAFT_ERROR_KEYS = {
    "Required for import.": "notification_rule_error_required",
    "Mapped value is missing.": "notification_rule_error_missing",
    "Choose expense, income, or transfer.": "notification_rule_error_kind",
    "Amount must be positive.": "notification_rule_error_positive",
    "Map the currency to a three-letter code.": "notification_rule_error_currency",
    "Date must be between 1970-01-01 and today.": "notification_rule_error_date_range",
    "Date does not match the configured format.": "notification_rule_error_date_format",
    "Time must be a local HH:MM or HH:MM:SS value.": "notification_rule_error_time",
    "Account is missing or inactive in the selected journal.": "notification_rule_error_account",
    "Account currency does not match the extracted currency.": (
        "notification_rule_error_account_currency"
    ),
    "Account is not open on this date.": "notification_rule_error_account_date",
    "Transfer accounts must be different.": "notification_rule_error_transfer",
    "Same-currency transfers must have matching amounts.": (
        "notification_rule_error_transfer_amount"
    ),
    "Category is unavailable for this transaction type.": "notification_rule_error_category",
    "Category is not open on this date.": "notification_rule_error_category_date",
    "Payee is invalid.": "notification_rule_error_payee",
    "Description is invalid.": "notification_rule_error_description",
}
_RULE_ERROR_KEYS = {
    "Pattern must contain 1-512 characters.": "notification_rule_error_pattern",
    "Invalid capture name or unclosed brace.": "notification_rule_error_pattern",
    "Capture names must be unique.": "notification_rule_error_pattern",
    "A pattern may contain at most 8 captures.": "notification_rule_error_pattern",
    "Adjacent captures need a literal separator.": "notification_rule_error_pattern",
    "Unescaped closing brace.": "notification_rule_error_pattern",
    "Title and body captures must have distinct names.": "notification_rule_error_pattern",
    "Rule name and source app are required.": "notification_rule_error_name_pattern",
    "A title or body pattern is required.": "notification_rule_error_name_pattern",
    "Enter a value for every mapped field.": "notification_rule_error_mapping_config",
    "An enabled rule refers to an undefined capture.": "notification_rule_error_mapping_config",
    "Priority must be a positive whole number.": "notification_rule_error_priority",
    "Priority is outside the available rule order.": "notification_rule_error_priority",
    "Decimal and grouping separators must differ.": "notification_rule_error_number_format",
    "Unsupported grouping separator.": "notification_rule_error_number_format",
    "Use one symbol=CODE mapping per line.": "notification_rule_error_currency_config",
    "Currency symbols must be unique.": "notification_rule_error_currency_config",
    "Currency symbol mappings need explicit currency codes.": (
        "notification_rule_error_currency_config"
    ),
}


def describe_draft_fields(translate: Translate, draft: dict[str, object] | None) -> list[str]:
    if draft is None:
        return []
    return [
        f"{translate(label)}: {value}"
        for field, label in _MAPPING_FIELDS
        if isinstance((value := draft.get(field)), str) and value
    ]


def describe_draft_errors(translate: Translate, draft: dict[str, object] | None) -> list[str]:
    errors = draft.get("errors") if draft else None
    if not isinstance(errors, (list, tuple)):
        return []
    labels = dict(_MAPPING_FIELDS)
    return [
        f"{translate(labels.get(pair[0], 'notification_rule_mapping'))}: "
        f"{translate(_DRAFT_ERROR_KEYS.get(message, 'notification_rule_error_other'))}"
        for pair in errors
        if isinstance(pair, (list, tuple))
        and len(pair) == 2
        and isinstance(pair[0], str)
        and isinstance((message := pair[1]), str)
    ]


class NotificationRuleEditor:
    """Build and test a rule draft, then save it with a revision check.

    Construct with a selected source app. New rules start disabled. The editor
    owns only the rule form; testing never changes the store or journal.
    """

    def __init__(
        self,
        page: ft.Page,
        translate: Translate,
        store: NotificationStore,
        app_id: str,
        *,
        rule_id: str | None = None,
        draft: Rule | None = None,
        sample_title: str | None = None,
        sample_body: str | None = None,
        ledger: Ledger | None = None,
        today: Callable[[], date] = date.today,
        on_saved: Callable[[Rule], None] | None = None,
        on_ai: AIStarter | None = None,
    ) -> None:
        self.page = page
        self.tr = translate
        self.store = store
        self.app_id = app_id
        self.ledger = ledger
        self.today = today
        self.on_saved = on_saved
        self.on_ai = on_ai
        self.entries = store.rules(app_id)
        if draft is not None and (rule_id is not None or draft.app_id != app_id):
            raise NotificationStoreError("Rule draft must use the selected source app.")
        self.rule_id = rule_id or str(uuid4())
        self.current = next((entry for entry in self.entries if entry.id == self.rule_id), None)
        if rule_id is not None and self.current is None:
            raise NotificationStoreError("Rule does not exist for this source app.")
        initial = (
            self.current.rule if self.current else replace(draft, enabled=False) if draft else None
        )
        self.expected_revision = store.rule_revision(app_id)
        self.saved_rule: Rule | None = None

        self.name = ft.TextField(max_length=120, on_change=self._draft_changed)
        self.source = ft.TextField(value=app_id, read_only=True)
        self.action = ft.Dropdown(
            value="suggest",
            options=[],
            on_select=self._action_changed,
        )
        self.title_pattern = ft.TextField(max_length=512, on_change=self._draft_changed)
        self.body_pattern = ft.TextField(max_length=512, on_change=self._draft_changed)
        self.enabled = ft.Checkbox(value=False, on_change=self._draft_changed)
        self.priority = ft.TextField(on_change=self._draft_changed)
        self.decimal_separator = ft.Dropdown(value=".", options=[], on_select=self._draft_changed)
        self.grouping_separator = ft.Dropdown(
            value="none", options=[], on_select=self._draft_changed
        )
        self.date_format = ft.Dropdown(value="%Y-%m-%d", options=[], on_select=self._draft_changed)
        self.currency_symbols = ft.TextField(
            multiline=True, min_lines=2, max_lines=4, on_change=self._draft_changed
        )
        self.samples = {
            str(item.id): item
            for item in store.items(
                ("pending", "unmatched", "ignored", "uncertain"),
                app_id=app_id,
                with_content=True,
                newest_first=True,
            )
        }
        self.sample_choice = ft.Dropdown(value="", on_select=self._sample_selected)
        self.sample_title = ft.TextField(
            value=sample_title or "", max_length=4096, on_change=self._draft_changed
        )
        self.sample_body = ft.TextField(
            value=sample_body or "",
            multiline=True,
            min_lines=2,
            max_lines=4,
            max_length=4096,
            on_change=self._draft_changed,
        )
        self.sample_result = ft.Text(selectable=True)
        self.status = ft.Text()
        self.details_header = ft.Text(size=18, weight=ft.FontWeight.BOLD)
        self.mapping_header = ft.Text(size=18, weight=ft.FontWeight.BOLD)
        self.sample_header = ft.Text(size=18, weight=ft.FontWeight.BOLD)
        self.test_button = ft.OutlinedButton(on_click=self._test)
        self.ai_button = ft.TextButton(on_click=self._ai, visible=on_ai is not None)
        self.cancel_button = ft.TextButton(on_click=self._cancel)
        self.save_button = ft.TextButton(on_click=self._save)
        self.mappings: dict[str, tuple[ft.Dropdown, ft.TextField]] = {}
        self.form = ft.Column(
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self.dialog: EditorDialog | None = None

        for field, _label in _MAPPING_FIELDS:
            source = getattr(initial.mapping, field) if initial and initial.mapping else None
            mode = "none" if source is None else "capture" if source.captured else "fixed"
            self.mappings[field] = (
                ft.Dropdown(
                    value=mode,
                    options=[],
                    data=field,
                    expand=True,
                    on_select=self._mapping_mode_changed,
                ),
                ft.TextField(
                    value=source.text if source is not None else "",
                    expand=True,
                    on_change=self._draft_changed,
                ),
            )

        if initial is not None:
            self.name.value = initial.name
            self.action.value = initial.action
            self.title_pattern.value = initial.title.source if initial.title else ""
            self.body_pattern.value = initial.body.source if initial.body else ""
            self.enabled.value = initial.enabled
            self.priority.value = str(
                self.entries.index(self.current) + 1 if self.current else len(self.entries) + 1
            )
            if initial.mapping:
                self.decimal_separator.value = initial.mapping.decimal_separator
                self.grouping_separator.value = next(
                    (
                        key
                        for key, value in _GROUPING_SEPARATORS.items()
                        if value == initial.mapping.grouping_separator
                    ),
                    "none",
                )
                self.date_format.value = initial.mapping.date_format
                self.currency_symbols.value = "\n".join(
                    f"{symbol}={code}" for symbol, code in initial.mapping.currency_symbols
                )
        else:
            self.priority.value = str(len(self.entries) + 1)

        self._build_form()
        self.render()

    def _build_form(self) -> None:
        rows: list[ft.Control] = [self.details_header]
        rows.extend(
            [
                self.name,
                self.source,
                self.action,
                self.title_pattern,
                self.body_pattern,
                self.enabled,
                self.priority,
                self.mapping_header,
            ]
        )
        for field, _label in _MAPPING_FIELDS:
            mode, value = self.mappings[field]
            rows.extend(
                [
                    ft.Row(
                        [mode, value],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    )
                ]
            )
        rows.extend(
            [
                self.decimal_separator,
                self.grouping_separator,
                self.date_format,
                self.currency_symbols,
                self.sample_header,
                self.sample_choice,
                self.sample_title,
                self.sample_body,
                self.ai_button,
                self.test_button,
                self.sample_result,
                self.status,
            ]
        )
        self.form.controls = rows

    def render(self) -> None:
        """Apply localized labels without replacing any user-entered values."""
        self.details_header.value = self.tr("notification_rule_details")
        self.mapping_header.value = self.tr("notification_rule_mapping")
        self.sample_header.value = self.tr("notification_rule_sample")
        self.name.label = self.tr("notification_rule_name")
        self.source.label = self.tr("notification_rule_source")
        self.action.label = self.tr("notification_rule_action")
        self.action.options = [
            ft.DropdownOption(key="suggest", text=self.tr("notification_rule_suggest")),
            ft.DropdownOption(key="ignore", text=self.tr("notification_rule_ignore")),
        ]
        self.title_pattern.label = self.tr("notification_rule_title_pattern")
        self.title_pattern.helper = self.tr("notification_rule_pattern_help")
        self.body_pattern.label = self.tr("notification_rule_body_pattern")
        self.body_pattern.helper = self.tr("notification_rule_pattern_help")
        self.enabled.label = self.tr("notification_rule_enabled")
        self.priority.label = self.tr("notification_rule_priority")
        for field, label_key in _MAPPING_FIELDS:
            mode, value = self.mappings[field]
            mode.label = self.tr(label_key)
            mode.options = [
                ft.DropdownOption(
                    key=choice,
                    text=self.tr(
                        {
                            "none": "notification_rule_unmapped",
                            "fixed": "notification_rule_fixed",
                            "capture": "notification_rule_capture",
                        }[choice]
                    ),
                )
                for choice in _MAPPING_MODES
            ]
            value.label = self.tr("notification_rule_mapping_value")
            value.disabled = mode.value == "none" or self.action.value == "ignore"
        self.decimal_separator.label = self.tr("notification_rule_decimal_separator")
        self.decimal_separator.options = [
            ft.DropdownOption(key=".", text="."),
            ft.DropdownOption(key=",", text=","),
        ]
        self.grouping_separator.label = self.tr("notification_rule_grouping_separator")
        self.grouping_separator.options = [
            ft.DropdownOption(key=key, text=self.tr(f"notification_rule_group_{key}"))
            for key in _GROUPING_SEPARATORS
        ]
        self.date_format.label = self.tr("notification_rule_date_format")
        self.date_format.options = [
            ft.DropdownOption(key=value, text=self.tr(f"notification_rule_date_{index}"))
            for index, value in enumerate(_DATE_FORMATS)
        ]
        self.currency_symbols.label = self.tr("notification_rule_currency_symbols")
        self.sample_choice.label = self.tr("notification_rule_sample_choice")
        self.sample_choice.options = [
            ft.DropdownOption(key="", text=self.tr("notification_rule_sample_manual")),
            *(
                ft.DropdownOption(
                    key=key,
                    text=(
                        f"{format_epoch(item.posted)} "
                        f"— {' '.join((item.title or item.body or '').split())[:80]}"
                    ),
                )
                for key, item in self.samples.items()
            ),
        ]
        self.sample_title.label = self.tr("notification_rule_sample_title")
        self.sample_body.label = self.tr("notification_rule_sample_body")
        self.ai_button.content = self.tr("notification_ai_title")
        self.test_button.content = self.tr("notification_rule_test")
        self.cancel_button.content = self.tr("crud_cancel")
        self.save_button.content = self.tr("crud_save")

    def _action_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        self._clear_feedback()
        self.render()
        self._update_form()

    def _mapping_mode_changed(self, event: ft.Event[ft.Dropdown]) -> None:
        self._clear_feedback()
        self.render()
        self._update_form()

    def _sample_selected(self, event: ft.Event[ft.Dropdown]) -> None:
        item = self.samples.get(self.sample_choice.value or "")
        if item is not None:
            self.sample_title.value = item.title or ""
            self.sample_body.value = item.body or ""
        self._clear_feedback()
        self._update_form()

    def _draft_changed(self, event: ft.Event) -> None:
        self._clear_feedback()
        self._update_form()

    def _clear_feedback(self) -> None:
        self.sample_result.value = ""
        self.status.value = ""

    def _update_form(self) -> None:
        if self.form.parent is not None:
            self.form.update()

    def _priority(self) -> int:
        try:
            position = int(self.priority.value or "")
        except ValueError as exc:
            raise NotificationRuleError("Priority must be a positive whole number.") from exc
        entries = self.store.rules(self.app_id)
        current = any(entry.id == self.rule_id for entry in entries)
        remaining = len(entries) - int(current)
        if not 1 <= position <= remaining + 1:
            raise NotificationRuleError("Priority is outside the available rule order.")
        return position - 1

    def _currency_mappings(self) -> tuple[tuple[str, str], ...]:
        mappings: list[tuple[str, str]] = []
        for line in (self.currency_symbols.value or "").splitlines():
            item = line.strip()
            if not item:
                continue
            symbol, separator, code = item.partition("=")
            if not separator:
                raise NotificationRuleError("Use one symbol=CODE mapping per line.")
            mappings.append((symbol.strip(), code.strip().upper()))
        return tuple(mappings)

    def build_rule(self) -> Rule:
        """Build the current form as a validated domain rule."""
        title_text = self.title_pattern.value or ""
        body_text = self.body_pattern.value or ""
        title = Pattern.parse(title_text) if title_text else None
        body = Pattern.parse(body_text) if body_text else None
        action = cast(str, self.action.value)
        mapping: TransactionMapping | None = None
        if action == "suggest":
            values: dict[str, Value | None] = {}
            for field, (mode, entry) in self.mappings.items():
                selected_mode = cast(str, mode.value)
                text = (entry.value or "").strip()
                if selected_mode == "none":
                    values[field] = None
                elif selected_mode in ("fixed", "capture") and text:
                    values[field] = Value(text, captured=selected_mode == "capture")
                else:
                    raise NotificationRuleError("Enter a value for every mapped field.")
            grouping_key = self.grouping_separator.value
            if not isinstance(grouping_key, str) or grouping_key not in _GROUPING_SEPARATORS:
                raise NotificationRuleError("Unsupported grouping separator.")
            grouping = _GROUPING_SEPARATORS[grouping_key]
            mapping = TransactionMapping(
                kind=values["kind"],
                amount=values["amount"],
                currency=values["currency"],
                account=values["account"],
                destination=values["destination"],
                destination_amount=values["destination_amount"],
                category=values["category"],
                payee=values["payee"],
                description=values["description"],
                date=values["date"],
                time=values["time"],
                decimal_separator=cast(str, self.decimal_separator.value),
                grouping_separator=grouping,
                date_format=cast(
                    Literal["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"],
                    self.date_format.value,
                ),
                currency_symbols=self._currency_mappings(),
            )
        rule = Rule(
            name=(self.name.value or "").strip(),
            app_id=self.app_id,
            action=cast(Literal["suggest", "ignore"], action),
            title=title,
            body=body,
            enabled=bool(self.enabled.value),
            mapping=mapping,
        )
        if rule.enabled and rule.missing_captures():
            raise NotificationRuleError("An enabled rule refers to an undefined capture.")
        return rule

    def test_sample(self) -> RuleTest:
        """Run explicit, non-persisting priority/match preview for the current form."""
        before_revision = self.store.rule_revision(self.app_id)
        result = test_rule(
            self.store,
            self.rule_id,
            self.build_rule(),
            self._priority(),
            title=(self.sample_title.value or "") or None,
            body=(self.sample_body.value or "") or None,
            ledger=self.ledger,
            today=self.today(),
        )
        after_revision = self.store.rule_revision(self.app_id)
        if before_revision != after_revision:
            raise NotificationStoreError("Rules changed; test the rule again.")
        self.expected_revision = after_revision
        self.entries = self.store.rules(self.app_id)
        self.current = next((entry for entry in self.entries if entry.id == self.rule_id), None)
        return result

    def save_rule(self) -> Rule:
        """Persist the rule draft only if the source app's order is unchanged."""
        rule = self.build_rule()
        self.store.save_rule(
            self.rule_id,
            rule,
            position=self._priority(),
            expected_revision=self.expected_revision,
        )
        self.saved_rule = rule
        return rule

    def _test_description(self, result: RuleTest) -> str:
        def outcome(evaluation: Outcome) -> str:
            return self.tr(
                {
                    "pending": "notification_rule_outcome_suggest",
                    "ignored": "notification_rule_outcome_ignore",
                    "unmatched": "notification_rule_outcome_unmatched",
                }[evaluation]
            )

        if result.winning_rule_id is None:
            winner = self.tr("notification_rule_test_no_match")
        elif result.winning_rule_id == self.rule_id:
            winner = self.tr("notification_rule_test_wins")
        else:
            existing = next(
                (
                    entry.rule.name
                    for entry in self.store.rules(self.app_id)
                    if entry.id == result.winning_rule_id
                ),
                self.tr("notification_rule_test_other"),
            )
            winner = self.tr("notification_rule_test_winner", name=existing)
        match = self.tr(
            "notification_rule_test_candidate_match"
            if result.proposed_matched
            else "notification_rule_test_candidate_no_match"
        )
        if result.captures:
            captures = ", ".join(f"{key}={value}" for key, value in sorted(result.captures.items()))
        else:
            captures = self.tr("notification_rule_test_no_captures")
        previous = result.previous_evaluation
        parts = [
            self.tr("notification_rule_test_current"),
            self.tr(
                "notification_rule_test_outcome",
                outcome=outcome(previous.outcome),
            ),
            previous.rule_name or self.tr("notification_no_rule"),
        ]
        parts.extend(describe_draft_fields(self.tr, previous.draft))
        previous_errors = describe_draft_errors(self.tr, previous.draft)
        if previous_errors:
            parts.append(
                self.tr("notification_rule_test_errors", errors="; ".join(previous_errors))
            )
        parts.extend(
            [
                self.tr("notification_rule_test_proposed"),
                match,
                winner,
                self.tr(
                    "notification_rule_test_outcome",
                    outcome=outcome(result.evaluation.outcome),
                ),
                self.tr("notification_rule_test_captures", captures=captures),
            ]
        )
        parts.extend(describe_draft_fields(self.tr, result.evaluation.draft))
        messages = describe_draft_errors(self.tr, result.evaluation.draft)
        if messages:
            parts.append(self.tr("notification_rule_test_errors", errors="; ".join(messages)))
        elif result.evaluation.draft is not None:
            parts.append(self.tr("notification_rule_test_no_errors"))
        return "\n".join(parts)

    def open(self) -> EditorDialog:
        """Show the editor with the shared adaptive and keyboard-safe dialog."""
        self.render()
        self.dialog = EditorDialog(
            self.tr("notification_rule_editor"),
            self.form,
            [self.cancel_button, self.save_button],
            compact=lambda: (self.page.width or 600) <= 599,
            cancel_on_back=self._close,
        )
        show_dialog(self.page, self.dialog)
        return self.dialog

    def _close(self) -> None:
        self.page.pop_dialog()

    async def _cancel(self, event: ft.Event) -> None:
        self._close()

    async def _ai(self, event: ft.Event) -> None:
        if self.on_ai is None:
            return

        def open_draft(rule: Rule, title: str | None, body: str | None) -> None:
            def saved(rule: Rule) -> None:
                self._close()
                if self.on_saved is not None:
                    self.on_saved(rule)

            NotificationRuleEditor(
                self.page,
                self.tr,
                self.store,
                self.app_id,
                draft=rule,
                sample_title=title,
                sample_body=body,
                ledger=self.ledger,
                today=self.today,
                on_saved=saved,
                on_ai=self.on_ai,
            ).open()

        await self.on_ai(
            self.sample_title.value or None, self.sample_body.value or None, open_draft
        )

    async def _test(self, event: ft.Event) -> None:
        try:
            result = self.test_sample()
            self.sample_result.value = self._test_description(result)
        except NotificationRuleError as exc:
            self.sample_result.value = self.tr(
                _RULE_ERROR_KEYS.get(str(exc), "notification_rule_test_failed")
            )
        except (NotificationStoreError, ValueError):
            self.sample_result.value = self.tr("notification_rule_test_failed")
        except sqlite3.Error:
            logger.exception("Notification rule test storage failed")
            self.sample_result.value = self.tr("notification_rule_test_failed")
        if self.sample_result.parent is not None:
            self.sample_result.update()

    async def _save(self, event: ft.Event) -> None:
        try:
            rule = self.save_rule()
        except NotificationRuleError as exc:
            self.status.value = self.tr(_RULE_ERROR_KEYS.get(str(exc), "notification_rule_invalid"))
        except NotificationStoreError as exc:
            key = (
                "notification_rule_conflict"
                if "changed" in str(exc).casefold()
                else "notification_rule_save_failed"
            )
            self.status.value = self.tr(key)
        except sqlite3.Error:
            logger.exception("Notification rule save storage failed")
            self.status.value = self.tr("notification_rule_save_failed")
        else:
            self._close()
            if self.on_saved is not None:
                self.on_saved(rule)
        if self.status.parent is not None:
            self.status.update()
