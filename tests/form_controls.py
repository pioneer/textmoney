"""Stable control lookup for the universal transaction dialog's UI tests."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast
from unittest.mock import MagicMock

import flet as ft

from app.ui.datetime_fields import DateTimeFields


def change(control: ft.Dropdown | ft.Checkbox | ft.SegmentedButton | ft.TextField) -> None:
    handler = control.on_select if isinstance(control, ft.Dropdown) else control.on_change
    assert handler is not None
    cast(Callable[[object], object], handler)(MagicMock())


@dataclass
class TransactionForm:
    sign: ft.SegmentedButton
    sign_hint: ft.Text
    account: ft.Dropdown
    category: ft.Dropdown
    amount: ft.TextField
    second_account: ft.Dropdown
    second_amount: ft.TextField
    special: ft.Dropdown
    reference: ft.Dropdown
    description: ft.TextField
    payee: ft.TextField
    advanced: ft.ExpansionTile
    fee_enabled: ft.Checkbox
    fee: ft.TextField
    fee_mode: ft.Dropdown
    fee_currency: ft.Dropdown
    fee_category: ft.Dropdown
    preview: ft.Text
    when: DateTimeFields

    def choose(self, kind: str) -> None:
        """Exercise sign/advanced controls, not a hidden operation selector."""
        assert kind in {"expense", "income", "reimbursement", "adjustment"}
        self.sign.selected = ["-" if kind == "expense" else "+"]
        self.special.value = kind if kind != "expense" else "income"
        change(self.sign)


def transaction_form(dialog: ft.AlertDialog) -> TransactionForm:
    assert isinstance(dialog.content, ft.Column)
    controls: dict[str, ft.Control] = {}
    when: DateTimeFields | None = None

    def visit(control: ft.Control) -> None:
        nonlocal when
        if isinstance(control, DateTimeFields):
            when = control
        if isinstance(control.data, str):
            controls[control.data] = control
        if isinstance(control, (ft.Row, ft.Column, ft.ExpansionTile)):
            for child in control.controls or []:
                visit(child)

    visit(dialog.content)

    def get[T: ft.Control](key: str, cls: type[T]) -> T:
        value = controls[key]
        assert isinstance(value, cls)
        return value

    assert when is not None
    return TransactionForm(
        sign=get("sign", ft.SegmentedButton),
        sign_hint=get("sign_hint", ft.Text),
        account=get("account", ft.Dropdown),
        category=get("category", ft.Dropdown),
        amount=get("amount", ft.TextField),
        second_account=get("second_account", ft.Dropdown),
        second_amount=get("second_amount", ft.TextField),
        special=get("special", ft.Dropdown),
        reference=get("reference", ft.Dropdown),
        description=get("description", ft.TextField),
        payee=get("payee", ft.TextField),
        advanced=get("advanced", ft.ExpansionTile),
        fee_enabled=get("fee_enabled", ft.Checkbox),
        fee=get("fee", ft.TextField),
        fee_mode=get("fee_mode", ft.Dropdown),
        fee_currency=get("fee_currency", ft.Dropdown),
        fee_category=get("fee_category", ft.Dropdown),
        preview=get("preview", ft.Text),
        when=when,
    )
