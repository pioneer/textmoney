import asyncio
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import flet as ft
import pytest

from app.domain.ledger import Category
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import translate
from app.ui.finance import FinanceView


def row(control: ft.Control) -> ft.Row:
    assert isinstance(control, (ft.ExpansionTile, ft.Container))
    value = control.title if isinstance(control, ft.ExpansionTile) else control.content
    assert isinstance(value, ft.Row)
    return value


def label(control: ft.Control) -> ft.Text:
    column = row(control).controls[0]
    assert isinstance(column, ft.Column)
    text = column.controls[0]
    assert isinstance(text, ft.Text)
    return text


def toggle(control: ft.ExpansionTile, expanded: bool) -> None:
    assert control.on_change is not None
    cast(Callable[[object], object], control.on_change)(MagicMock(control=control, data=expanded))


@pytest.mark.parametrize("language", ["uk", "en", "ru"])
def test_income_expense_sections_have_equal_layout_and_nested_actions(language: str) -> None:
    view = FinanceView()
    view.language = language
    categories = (
        Category("Expenses:Food", "Їжа", date(1970, 1, 1)),
        Category("Expenses:Food:Shops", "Магазини", date(1970, 1, 1)),
        Category("Income:Work", "Робота", date(1970, 1, 1)),
        Category("Income:Work:Salary", "Зарплата", date(1970, 1, 1)),
    )
    expense, separator, income = view._category_tree(categories)
    assert isinstance(separator, ft.Divider)
    assert isinstance(expense, ft.ExpansionTile) and isinstance(income, ft.ExpansionTile)
    assert label(expense).value == translate(language, "categories_expenses")
    assert label(income).value == translate(language, "categories_income")
    for section, category in [(expense, categories[0]), (income, categories[2])]:
        assert section.expanded
        assert section.dense and section.min_tile_height == 48
        assert section.affinity == ft.TileAffinity.LEADING
        assert section.controls is not None
        parent = section.controls[0]
        assert isinstance(parent, ft.ExpansionTile)
        assert not parent.expanded
        assert label(parent).value == category.name
        assert parent.controls is not None
        leaf = parent.controls[0]
        assert isinstance(leaf, ft.Container)
        assert leaf.data == category.id + (
            ":Shops" if category.id.startswith("Expenses") else ":Salary"
        )
        for node in [parent, leaf]:
            edit, delete = row(node).controls[1:]
            assert isinstance(edit, ft.IconButton) and isinstance(delete, ft.IconButton)
            assert edit.data == delete.data == ("category", node.data)
            assert edit.on_click == view._edit_entity
            assert delete.on_click == view._delete_entity


def test_missing_parent_nodes_are_visible_but_not_editable_and_inactive_labels_remain() -> None:
    view = FinanceView()
    expense, _, income = view._category_tree(
        (Category("Income:Work:Remote:Salary", "Salary", date(1970, 1, 1), active=False),)
    )
    assert isinstance(expense, ft.ExpansionTile)
    assert expense.controls is not None
    empty = expense.controls[0]
    assert isinstance(empty, ft.Text) and empty.value == view.tr("categories_empty")
    assert isinstance(income, ft.ExpansionTile) and income.controls is not None
    work = income.controls[0]
    assert isinstance(work, ft.ExpansionTile) and work.controls is not None
    assert label(work).value == "Work"
    assert len(row(work).controls) == 1
    remote = work.controls[0]
    assert isinstance(remote, ft.ExpansionTile) and remote.controls is not None
    salary = remote.controls[0]
    assert label(salary).value == "Salary"
    labels = row(salary).controls[0]
    assert isinstance(labels, ft.Column)
    inactive = labels.controls[1]
    assert isinstance(inactive, ft.Text) and inactive.value == view.tr("crud_inactive_label")


def test_expansion_state_survives_refresh_language_resize_and_accounting_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        page = MagicMock(spec=ft.Page)
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        await journal.create_category("Food:Shops", "Shops")
        await journal.create_category("Work:Salary", "Salary", root="Income")
        view = FinanceView()
        view.journal = journal
        original = journal.source
        view._refresh()
        section = view.category_list.controls[0]
        assert isinstance(section, ft.ExpansionTile) and section.controls is not None
        food = section.controls[0]
        assert isinstance(food, ft.ExpansionTile) and food.on_change is not None
        toggle(food, True)
        toggle(section, False)
        view.language = "uk"
        view._apply_layout(390)
        view._refresh()
        rebuilt = view.category_list.controls[0]
        assert isinstance(rebuilt, ft.ExpansionTile) and rebuilt.controls is not None
        assert not rebuilt.expanded
        rebuilt_food = rebuilt.controls[0]
        assert isinstance(rebuilt_food, ft.ExpansionTile) and rebuilt_food.expanded
        toggle(rebuilt_food, False)
        view._refresh()
        rebuilt = view.category_list.controls[0]
        assert isinstance(rebuilt, ft.ExpansionTile) and rebuilt.controls is not None
        rebuilt_food = rebuilt.controls[0]
        assert isinstance(rebuilt_food, ft.ExpansionTile) and not rebuilt_food.expanded
        assert label(rebuilt).value == "Витрати"
        assert journal.source == original
        assert (tmp_path / FILENAME).read_text() == original

    asyncio.run(scenario())
