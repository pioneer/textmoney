import asyncio
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import flet as ft
import pytest
from form_controls import change, click, transaction_form

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
    assert view.categories_panel.theme is not None
    assert view.categories_panel.theme.list_tile_theme == ft.ListTileTheme(
        horizontal_spacing=8, min_leading_width=24
    )
    assert view.accounts_panel.theme is None
    categories = (
        Category("Expenses:Food", "Їжа", date(1970, 1, 1)),
        Category("Expenses:Food:Shops", "Магазини", date(1970, 1, 1)),
        Category("Income:Work", "Робота", date(1970, 1, 1)),
        Category("Income:Work:Salary", "Зарплата", date(1970, 1, 1)),
    )
    expense, income = view._category_tree(categories)
    assert isinstance(expense, ft.ExpansionTile) and isinstance(income, ft.ExpansionTile)
    assert label(expense).value == translate(language, "categories_expenses")
    assert label(income).value == translate(language, "categories_income")
    for section, category in [(expense, categories[0]), (income, categories[2])]:
        assert section.expanded
        assert section.dense and section.min_tile_height == 40
        assert section.affinity == ft.TileAffinity.LEADING
        assert section.tile_padding == 0
        assert section.controls_padding == ft.Padding.only(left=16)
        assert section.shape == section.collapsed_shape == ft.RoundedRectangleBorder()
        assert section.controls is not None
        parent = section.controls[0]
        assert isinstance(parent, ft.ExpansionTile)
        assert parent.dense and parent.min_tile_height == 40
        assert parent.tile_padding == 0
        assert parent.affinity == ft.TileAffinity.LEADING
        assert parent.shape == parent.collapsed_shape == ft.RoundedRectangleBorder()
        assert not parent.expanded
        assert label(parent).value == category.name
        assert parent.controls is not None
        leaf = parent.controls[0]
        assert isinstance(leaf, ft.Container)
        assert leaf.padding == 0
        assert leaf.data == category.id + (
            ":Shops" if category.id.startswith("Expenses") else ":Salary"
        )
        for node in [parent, leaf]:
            edit, delete = row(node).controls[1:]
            assert isinstance(edit, ft.IconButton) and isinstance(delete, ft.IconButton)
            assert edit.data == delete.data == ("category", node.data)
            assert edit.on_click == view._edit_entity
            assert delete.on_click == view._delete_entity
            assert (
                edit.style
                == delete.style
                == ft.ButtonStyle(visual_density=ft.VisualDensity.COMPACT)
            )


def test_missing_parent_nodes_are_visible_but_not_editable_and_inactive_labels_remain() -> None:
    view = FinanceView()
    expense, income = view._category_tree(
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


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize("width", [390, 1366])
@pytest.mark.parametrize("root", ["Expenses", "Income"])
@pytest.mark.parametrize("inline", [False, True])
def test_parent_selectors_show_names_smaller_codes_and_contiguous_hierarchy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    language: str,
    width: int,
    root: str,
    inline: bool,
) -> None:
    async def scenario() -> None:
        source = ""
        for kind in ("Income", "Expenses"):
            for path, name in [
                ("Food:Shops:Market", "Market"),
                ("Food-Other", "Food"),
                ("Food:Shops", "Shops"),
                ("Food", "Food"),
                ("Hidden", "Hidden"),
            ]:
                source += f'2000-01-01 open {kind}:{path}\n    name: "{name}"\n'
                if path == "Hidden":
                    source += "    textmoney-active: FALSE\n"
            source += f'2000-01-02 open {kind}:Hidden:Child\n    name: "Child"\n'
        (tmp_path / FILENAME).write_text(source, newline="")
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        page = MagicMock(spec=ft.Page)
        page.width = width
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view = FinanceView()
        view.journal, view.language = journal, language
        view._refresh()
        transaction = None
        if inline:
            view._new_transaction(MagicMock())
            transaction = transaction_form(page.show_dialog.call_args.args[0])
            transaction.description.value = "Unsaved transaction"
            transaction.sign.selected = ["+" if root == "Income" else "-"]
            change(transaction.sign)
            click(transaction.category_create)
            dialog = page.show_dialog.call_args.args[0]
            assert isinstance(dialog.content, ft.Column)
            parent = next(
                child
                for control in dialog.content.controls
                if isinstance(control, ft.Row)
                for child in control.controls
                if isinstance(child, ft.Dropdown) and child.data == "quick_category_parent"
            )
            name, code = (
                next(
                    control
                    for control in dialog.content.controls
                    if isinstance(control, ft.TextField) and control.data == key
                )
                for key in ("quick_category_name", "quick_category_path")
            )
        else:
            view.category_root.value = root
            view._category_type_changed(MagicMock())
            view._create_category(MagicMock())
            dialog = page.show_dialog.call_args.args[0]
            parent, name, code = view.category_parent, view.category_name, view.category_id
        assert not name.value
        assert [(option.key, option.text) for option in parent.options] == [
            ("", view.tr("category_parent_none")),
            (f"{root}:Food", f"Food ({root}:Food)"),
            (f"{root}:Food:Shops", f"Shops ({root}:Food:Shops)"),
            (f"{root}:Food:Shops:Market", f"Market ({root}:Food:Shops:Market)"),
            (f"{root}:Food-Other", f"Food ({root}:Food-Other)"),
            (f"{root}:Hidden:Child", f"Child ({root}:Hidden:Child)"),
        ]
        for option, depth in zip(parent.options[1:], [0, 1, 2, 0, 1], strict=True):
            content = option.content
            assert isinstance(content, ft.Container)
            assert content.padding == ft.Padding.only(left=16 * depth)
            label = content.content
            assert isinstance(label, ft.Text) and label.size == 16
            assert label.spans and len(label.spans) == 1
            span = label.spans[0]
            assert span.text == f" ({option.key})"
            assert span.style and span.style.size == 12
            assert f"{label.value}{span.text}" == option.text
        parent.value = f"{root}:Food:Shops"
        change(parent)
        name.value = "Synthetic child"
        change(name)
        assert code.value and code.value.startswith(f"{parent.value}:")
        click(dialog.actions[0])
        assert journal.source == source
        assert (tmp_path / FILENAME).read_text(newline="") == source
        assert not (tmp_path / "backups").exists()
        if transaction is not None:
            assert transaction.description.value == "Unsaved transaction"

    asyncio.run(scenario())
