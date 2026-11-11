import asyncio
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import flet as ft
import flet.canvas as fc
import pytest
from form_controls import category_content, change, click, transaction_form

from app.domain.ledger import Category
from app.icons import DISPLAY_SIZE
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.localization import translate
from app.ui.finance import FinanceView
from app.ui.layout import HoverRow
from app.ui.tree_style import CategoryConnectorRow, CategoryMenuAnchor


def tree_item(control: ft.Control) -> ft.Control:
    if isinstance(control, CategoryConnectorRow):
        assert isinstance(control.content, ft.Container)
        assert isinstance(control.content.content, ft.Control)
        control = control.content.content
    if isinstance(control, HoverRow):
        assert isinstance(control.content, ft.Control)
        return control.content
    return control


def row(control: ft.Control) -> ft.Row:
    control = tree_item(control)
    assert isinstance(control, (ft.ExpansionTile, ft.ListTile))
    value = control.title
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


@pytest.mark.parametrize("field_height", [40, 56, 80])
@pytest.mark.parametrize("option_count", [1, 3, 12])
@pytest.mark.parametrize("height_limit", [320, 480])
def test_mobile_category_menu_is_centered_on_measured_field(
    monkeypatch: pytest.MonkeyPatch, field_height: int, option_count: int, height_limit: int
) -> None:
    dropdown = ft.Dropdown(
        menu_height=height_limit,
        options=[
            ft.DropdownOption(key=str(index), content=ft.Container(height=48))
            for index in range(option_count)
        ],
    )
    anchor = CategoryMenuAnchor(dropdown)
    update = MagicMock()
    monkeypatch.setattr(anchor, "update", update)
    anchor._resized(MagicMock(spec=fc.CanvasResizeEvent, height=field_height, width=300))
    assert dropdown.menu_style is not None
    alignment = dropdown.menu_style.alignment
    assert alignment is not None
    menu_height = min(height_limit, option_count * 48 + 16)
    assert alignment.x == -1
    assert field_height / 2 + alignment.y * field_height / 2 == pytest.approx(
        (field_height - menu_height) / 2
    )
    assert dropdown.menu_style.padding == ft.Padding.symmetric(vertical=8)
    assert update.call_count == (0 if field_height == 56 else 1)
    dropdown.options.pop()
    anchor.before_update()
    assert dropdown.menu_style.alignment == ft.Alignment(
        -1, -min(height_limit, (option_count - 1) * 48 + 16) / field_height
    )


def test_main_category_tree_uses_connector_gutters_without_losing_native_tiles() -> None:
    view = FinanceView()
    expense, _income = view._category_tree(
        (
            Category("Expenses:Food", "Food", date(2000, 1, 1)),
            Category("Expenses:Food:Shops", "Shops", date(2000, 1, 1)),
            Category("Expenses:Travel", "Travel", date(2000, 1, 1)),
        )
    )
    assert isinstance(expense, ft.ExpansionTile) and expense.controls
    parent = expense.controls[0]
    assert isinstance(parent, CategoryConnectorRow)
    assert isinstance(parent.content, ft.Container)
    assert parent.content.padding == ft.Padding.only(left=16)
    assert isinstance(parent.content.content, ft.ExpansionTile)
    assert parent.content.content.on_change is not None
    assert parent.continued == (True,)
    parent._draw(120)
    assert [
        (shape.x1, shape.y1, shape.x2, shape.y2)
        for shape in parent.shapes
        if isinstance(shape, fc.Line)
    ] == [(4, 0, 4, 120), (4, 20, 12, 20)]
    last = expense.controls[-1]
    assert isinstance(last, CategoryConnectorRow)
    last._draw(80)
    elbow = last.shapes[-1]
    assert isinstance(elbow, fc.Path)
    assert elbow.elements[-1] == fc.Path.LineTo(12, 20)


def test_category_menu_lines_continue_through_descendants_and_end_at_last_sibling() -> None:
    view = FinanceView()
    categories = tuple(
        Category(identifier, identifier.rsplit(":", 1)[-1], date(2000, 1, 1))
        for identifier in (
            "Expenses:Food",
            "Expenses:Food:Shops",
            "Expenses:Food:Shops:Market",
            "Expenses:Food:Snacks",
            "Expenses:Travel",
        )
    )
    options = view._category_parent_options(categories, "Expenses")[1:]
    last_child = options[3].content
    assert isinstance(last_child, fc.Canvas)
    assert isinstance(last_child.shapes[-1], fc.Path)
    assert any(
        isinstance(element, fc.Path.QuadraticTo) for element in last_child.shapes[-1].elements
    )
    assert last_child.shapes[-1].elements[-1] == fc.Path.LineTo(15, 20)
    expected = [
        [],
        [(4, 0, 4, 40), (4, 20, 15, 20)],
        [(4, 0, 4, 40)],
        [],
        [],
    ]
    for option, segments in zip(options, expected, strict=True):
        assert isinstance(option.content, fc.Canvas)
        assert option.content.height == 40
        assert [
            (shape.x1, shape.y1, shape.x2, shape.y2)
            for shape in option.content.shapes
            if isinstance(shape, fc.Line)
        ] == segments
        assert option.style == ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=12))
        for shape in option.content.shapes:
            assert isinstance(shape, (fc.Line, fc.Path))
            assert shape.paint.color == ft.Colors.OUTLINE_VARIANT
            assert shape.paint.stroke_width == 1.25
            assert shape.paint.style == ft.PaintingStyle.STROKE


@pytest.mark.parametrize("height", [32, 48, 72, 96])
@pytest.mark.parametrize(
    "continued",
    [(), (True,), (False,), (True, False), (False, False), (True,) * 7 + (False,)],
)
def test_connector_geometry_uses_measured_row_height_and_constant_nesting_gutters(
    monkeypatch: pytest.MonkeyPatch, height: int, continued: tuple[bool, ...]
) -> None:
    content = ft.Container(ft.Text("A category name that can wrap", max_lines=2), height=48)
    connector = CategoryConnectorRow(content, continued)
    update = MagicMock()
    monkeypatch.setattr(connector, "update", update)
    connector._resized(MagicMock(spec=fc.CanvasResizeEvent, height=height, width=300))
    assert connector.content is content
    center = height / 2
    for shape in connector.shapes:
        if isinstance(shape, fc.Line):
            assert shape.x1 % 16 == 4
            if shape.x1 == shape.x2:
                assert (shape.y1, shape.y2) == (0, height)
            else:
                assert shape.y1 == shape.y2 == center
                assert shape.x2 - shape.x1 == 8
    if continued and not continued[-1]:
        elbow = connector.shapes[-1]
        assert isinstance(elbow, fc.Path)
        position = 16 * (len(continued) - 1) + 4
        assert elbow.elements == [
            fc.Path.MoveTo(position, 0),
            fc.Path.LineTo(position, center - 3),
            fc.Path.QuadraticTo(position, center, position + 3, center),
            fc.Path.LineTo(position + 8, center),
        ]
    if not continued:
        assert not connector.shapes
    assert update.call_count == (0 if height == 48 else 1)
    shapes = connector.shapes
    connector._resized(MagicMock(spec=fc.CanvasResizeEvent, height=0, width=0))
    connector._resized(MagicMock(spec=fc.CanvasResizeEvent, height=height, width=600))
    assert connector.shapes is shapes
    assert update.call_count == (0 if height == 48 else 1)


@pytest.mark.parametrize("width", [390, 599, 600, 1366])
@pytest.mark.parametrize("kind", ["expense", "income"])
@pytest.mark.parametrize("editing", [False, True])
def test_transaction_categories_are_indented_and_menu_height_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int, kind: str, editing: bool
) -> None:
    async def scenario() -> None:
        root = "Expenses" if kind == "expense" else "Income"
        source = ""
        for path in ("Food-Other", "Food:Shops:Market", "Food", "Food:Shops"):
            source += f'2000-01-01 open {root}:{path}\n    name: "{path.split(":")[-1]}"\n'
        if editing:
            amount = 1 if kind == "expense" else -1
            source += (
                '2000-01-01 open Assets:Cash USD\n2000-01-02 * "Original"\n'
                f"    {root}:Food {amount} USD\n    Assets:Cash {-amount} USD\n"
            )
        (tmp_path / FILENAME).write_text(source, newline="")
        journal = Journal(DesktopFolder(str(tmp_path), FILENAME))
        await journal.load()
        page = MagicMock(spec=ft.Page)
        page.width = width
        monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
        view = FinanceView()
        view.journal = journal
        view._apply_layout(width)
        view._transaction_dialog(journal, journal.ledger.transactions[0] if editing else None)
        form = transaction_form(page.show_dialog.call_args.args[0])
        if not editing:
            form.choose(kind)
        assert form.category.menu_height == (480 if width <= 599 else None)
        if width <= 599:
            assert form.category.menu_style and form.category.menu_style.alignment
            assert form.category.menu_style.alignment.y < 0
        else:
            assert form.category.menu_style is None
        assert [option.key for option in form.category.options] == [
            f"{root}:Food",
            f"{root}:Food:Shops",
            f"{root}:Food:Shops:Market",
            f"{root}:Food-Other",
        ]
        for option, depth in zip(form.category.options, [0, 1, 2, 0], strict=True):
            content = category_content(option)
            assert content.padding == ft.Padding.only(left=16 * depth)
            assert option.text == (option.key or "").split(":")[-1]
            assert isinstance(content.content, ft.Text)
            assert content.content.value == option.text
            assert not content.content.spans
        assert journal.source == source

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["uk", "en", "ru"])
def test_income_expense_sections_have_equal_layout_and_nested_actions(language: str) -> None:
    view = FinanceView()
    view.language = language
    assert view.categories_panel.theme is not None
    assert view.categories_panel.theme.list_tile_theme == ft.ListTileTheme(
        horizontal_spacing=12, min_leading_width=DISPLAY_SIZE, min_vertical_padding=0
    )
    assert view.categories_panel.theme.icon_theme == ft.IconTheme(size=DISPLAY_SIZE)
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
        assert section.controls_padding == 0
        assert section.shape == section.collapsed_shape == ft.RoundedRectangleBorder()
        assert section.controls is not None
        parent = tree_item(section.controls[0])
        assert isinstance(parent, ft.ExpansionTile)
        assert parent.dense and parent.min_tile_height == 40
        assert parent.tile_padding == 0
        assert parent.affinity == ft.TileAffinity.LEADING
        assert parent.leading is None
        assert parent.shape == parent.collapsed_shape == ft.RoundedRectangleBorder()
        assert not parent.expanded
        assert label(parent).value == category.name
        assert parent.controls is not None
        leaf = tree_item(parent.controls[0])
        assert isinstance(leaf, ft.ListTile)
        assert leaf.content_padding == 0 and leaf.min_vertical_padding == 0
        assert leaf.min_height == parent.min_tile_height == 40
        assert leaf.dense and leaf.visual_density == parent.visual_density
        assert leaf.data == category.id + (
            ":Shops" if category.id.startswith("Expenses") else ":Salary"
        )
        for node in [parent, leaf]:
            operations, edit, delete = row(node).controls[1:]
            assert isinstance(operations, ft.IconButton)
            assert operations.data == ("category", node.data)
            assert operations.on_click == view._show_entity_operations
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
    work = tree_item(income.controls[0])
    assert isinstance(work, ft.ExpansionTile) and work.controls is not None
    assert label(work).value == "Work"
    assert len(row(work).controls) == 1
    remote = tree_item(work.controls[0])
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
        food = tree_item(section.controls[0])
        assert isinstance(food, ft.ExpansionTile) and food.on_change is not None
        toggle(food, True)
        toggle(section, False)
        view.language = "uk"
        view._apply_layout(390)
        view._refresh()
        rebuilt = view.category_list.controls[0]
        assert isinstance(rebuilt, ft.ExpansionTile) and rebuilt.controls is not None
        assert not rebuilt.expanded
        rebuilt_food = tree_item(rebuilt.controls[0])
        assert isinstance(rebuilt_food, ft.ExpansionTile) and rebuilt_food.expanded
        toggle(rebuilt_food, False)
        view._refresh()
        rebuilt = view.category_list.controls[0]
        assert isinstance(rebuilt, ft.ExpansionTile) and rebuilt.controls is not None
        rebuilt_food = tree_item(rebuilt.controls[0])
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
            content = category_content(option)
            assert content.padding == ft.Padding.only(left=16 * depth)
            label = content.content
            assert isinstance(label, ft.Text) and label.size is None and label.weight is None
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
