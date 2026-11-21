import asyncio
import base64
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import httpx
import pytest
from flet.messaging.session import Session
from form_controls import (
    assert_deletion_title,
    category_content,
    click,
    click_async,
    transaction_form,
)
from PIL import Image, ImageDraw
from test_ai import FakeCredentials
from test_daily_summary import walk
from test_dialog_keyboard import finance_session, press
from test_icons import asset, raster, store_at

import app.ui.icons as icon_ui
from app.domain.budgets import Budget
from app.domain.ledger import EQUITY, Posting, Transaction
from app.domain.reporting import period_report
from app.icons import IconAsset, IconError, normalize_generated_image, normalize_image
from app.infrastructure.ai import (
    AIError,
    AIService,
    AISettings,
    Capability,
    GeneratedImage,
    Provider,
)
from app.infrastructure.icon_sources import bundled_icons
from app.infrastructure.preferences import DesktopPreferences
from app.journal import FILENAME, Journal
from app.localization import format_amount
from app.storage import StorageError
from app.ui.dialogs import show_dialog
from app.ui.finance import FinanceView
from app.ui.icons import IconDraft, IconField, IconPicker, IconRemovalDialog, icon_image
from app.ui.tree_picker import TreePicker


def images(control: ft.Control) -> list[ft.Image]:
    return [child for child in walk(control) if isinstance(child, ft.Image)]


@pytest.mark.parametrize(
    "color",
    [(20, 20, 20, 255), (45, 47, 59, 255), (59, 47, 45, 255), (220, 224, 238, 255)],
    ids=["gray", "cool-neutral", "warm-neutral", "light-neutral"],
)
def test_new_generated_icon_scales_and_themes_without_changing_existing(
    color: tuple[int, int, int, int],
) -> None:
    image = Image.new("RGBA", (96, 96))
    ImageDraw.Draw(image).rectangle((37, 34, 58, 61), fill=color)
    output = BytesIO()
    image.save(output, format="PNG")
    original = IconAsset("old", output.getvalue(), False, "AI", "rights", "credit")
    data, monochrome = normalize_generated_image(original.data)
    saved = IconAsset.parse(replace(original, data=data, monochrome=monochrome).serialize())
    displayed = icon_image(saved)
    assert displayed.color == ft.Colors.ON_SURFACE
    assert isinstance(displayed.src, bytes)
    with Image.open(BytesIO(displayed.src)) as rendered:
        bounds = rendered.getchannel("A").getbbox()
        assert bounds is not None
        assert 78 <= max(bounds[2] - bounds[0], bounds[3] - bounds[1]) <= 82
    picker = IconPicker(MagicMock(spec=ft.Page), "en", saved, AsyncMock())
    assert picker.reference_preview is not None
    assert isinstance(picker.reference_preview.content, ft.Image)
    assert picker.reference_preview.content.src == displayed.src
    assert picker.reference_preview.content.color == ft.Colors.ON_SURFACE
    assert icon_image(original).src == original.data
    assert icon_image(original).color is None


@pytest.mark.parametrize(
    ("query", "collection"),
    [
        ("outline woman", "ic"),
        ("home sharp", "material-symbols"),
        ("github", "simple-icons"),
        ("github", "logos"),
    ],
)
def test_builtin_reference_preview_matches_selected_theme_treatment(
    query: str, collection: str
) -> None:
    current = next(icon for icon in bundled_icons(query) if f" · {collection}:" in icon.source)
    picker = IconPicker(MagicMock(spec=ft.Page), "en", current, AsyncMock())
    assert picker.reference_preview is not None
    assert isinstance(picker.reference_preview.content, ft.Image)
    assert picker.reference_preview.content.color == icon_image(current).color


@pytest.mark.parametrize("color", [(255, 0, 0, 255), (0, 0, 60, 255)], ids=["red", "dark-blue"])
def test_generated_colored_art_and_imports_keep_their_colors(
    color: tuple[int, int, int, int],
) -> None:
    image = Image.new("RGBA", (96, 96))
    ImageDraw.Draw(image).rectangle((37, 34, 58, 61), fill=color)
    output = BytesIO()
    image.save(output, format="PNG")
    generated = IconAsset(
        "AI-generated image",
        output.getvalue(),
        False,
        "AI-generated image",
        "AI-generated; rights not verified",
        "credit",
    )
    imported = replace(generated, source="User import", license="User-provided")

    data, monochrome = normalize_generated_image(generated.data)
    colored = icon_image(replace(generated, data=data, monochrome=monochrome))
    untouched = icon_image(imported)
    assert colored.color is None
    assert isinstance(colored.src, bytes)
    with Image.open(BytesIO(colored.src)) as rendered:
        bounds = rendered.getchannel("A").getbbox()
        assert bounds is not None and bounds[3] - bounds[1] >= 78
    assert untouched.color is None and untouched.src == imported.data


@pytest.mark.parametrize("kind", ["two-tone", "opaque", "transparent"])
def test_generated_icon_preserves_complex_or_empty_art(kind: str) -> None:
    image = Image.new("RGBA", (96, 96), "white" if kind == "opaque" else (0, 0, 0, 0))
    if kind == "two-tone":
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 30, 47, 65), fill="black")
        draw.rectangle((48, 30, 65, 65), fill="white")
    output = BytesIO()
    image.save(output, format="PNG")
    generated = IconAsset(
        "AI-generated image",
        output.getvalue(),
        False,
        "AI-generated image",
        "AI-generated; rights not verified",
        "credit",
    )
    data, monochrome = normalize_generated_image(generated.data)
    displayed = icon_image(replace(generated, data=data, monochrome=monochrome))
    assert displayed.color is None
    if kind != "two-tone":
        assert displayed.src == generated.data


@pytest.mark.parametrize("width", [390, 1366])
def test_operations_filter_icons_hierarchy_and_popup_only_icon_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    async def scenario() -> None:
        _session, view, journal = await finance_session(tmp_path, monkeypatch)
        await journal.create_category("Food:Shops", "Shops")
        view.icons = store_at(tmp_path)
        for identifier in ("Assets:Bank", "Expenses:Food", "Expenses:Food:Shops"):
            await view.icons.assign(identifier, asset(identifier), ())
        view.operations_account.value = "Assets:Bank"
        view.operations_category.value = "Expenses:Food:Shops"
        view._apply_layout(width)
        view._refresh()
        account = next(
            option for option in view.operations_account.options if option.key == "Assets:Bank"
        )
        assert account.text == journal.ledger.accounts[0].name
        assert view.operations_account.text_style == ft.TextStyle(size=16)
        assert view.operations_category.text_style == view.operations_account.text_style
        assert isinstance(account.leading_icon, ft.Container) and images(account.leading_icon)
        assert isinstance(view.operations_account.leading_icon, ft.Container)
        assert isinstance(view.operations_category.leading_icon, ft.Container)
        assert images(view.operations_account.leading_icon)
        assert images(view.operations_category.leading_icon)
        for control in (view.operations_account, view.operations_category):
            assert isinstance(control.leading_icon, ft.Container)
            assert control.leading_icon.width == control.leading_icon.height == 24
            assert images(control.leading_icon)[0].width == 16
            assert images(control.leading_icon)[0].height == 16
        categories = view.operations_category.options
        parent = next(option for option in categories if option.key == "Expenses:Food")
        child = next(option for option in categories if option.key == "Expenses:Food:Shops")
        assert categories.index(parent) < categories.index(child)
        for option, indent in ((parent, 0), (child, 16)):
            assert category_content(option).padding == ft.Padding.only(left=indent)
            assert images(category_content(option))
            label = next(
                control
                for control in walk(category_content(option))
                if isinstance(control, ft.Text)
            )
            assert option.text == label.value == ("Shops" if option is child else "Food")
            assert label.size is None and label.weight is None
            assert not label.spans
            assert images(category_content(option))[0].width == 16
            assert images(category_content(option))[0].height == 16
        for listing in (view.account_list, view.category_list):
            assert not any(
                isinstance(control, ft.IconButton) and control.on_click == view._choose_entity_icon
                for control in walk(listing)
            )
        view.operations_account.value = view.operations_category.value = ""
        view._operations_filter_changed(MagicMock())
        assert view.operations_account.leading_icon is None
        assert view.operations_category.leading_icon is None

    asyncio.run(scenario())


def action(dialog: ft.AlertDialog, index: int) -> ft.TextButton:
    control = dialog.actions[index]
    if isinstance(control, ft.KeyboardListener):
        control = control.content
    assert isinstance(control, ft.TextButton)
    return control


def transaction_body(card: ft.Control) -> ft.Column:
    assert isinstance(card, ft.Container) and isinstance(card.content, ft.Row)
    return next(child for child in card.content.controls if isinstance(child, ft.Column))


@pytest.mark.parametrize(
    ("kind", "identifier"), [("category", "Expenses:Food"), ("account", "Assets:Bank")]
)
@pytest.mark.parametrize("assigned", [False, True])
def test_edit_entity_shows_current_icon_and_tracks_picker_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, identifier: str, assigned: bool
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.language = "en"
        view.icons = store_at(tmp_path)
        await view.icons.load()
        original = asset("original")
        replacement = asset("replacement")
        if assigned:
            await view.icons.assign(identifier, original, ())
        view._edit_entity(MagicMock(control=ft.IconButton(data=(kind, identifier))))
        parent = session.page._dialogs.controls[-1]
        assert isinstance(parent, ft.AlertDialog)
        assert [image.src for image in images(parent)] == ([original.data] if assigned else [])
        name = next(control for control in walk(parent) if isinstance(control, ft.TextField))
        name.value = "Unsaved name"
        choose = next(
            control
            for control in walk(parent)
            if isinstance(control, ft.IconButton) and control.tooltip == view.tr("icon_choose")
        )
        remove = next(
            control
            for control in walk(parent)
            if isinstance(control, ft.IconButton) and control.tooltip == view.tr("icon_remove")
        )
        field = next(control for control in walk(parent) if isinstance(control, IconField))
        assert isinstance(field.controls[0], ft.Text)
        assert field.controls[0].value == view.tr("icon_choose")
        row = field.controls[1]
        assert isinstance(row, ft.Row) and row.alignment == ft.MainAxisAlignment.SPACE_BETWEEN
        assert field.preview.width == 40 and field.preview.alignment == ft.Alignment.CENTER
        assert isinstance(row.controls[1], ft.Row)
        assert row.controls[1].controls == [choose, remove]
        assert not any(isinstance(control, ft.Text) for control in walk(row))
        assert choose.icon == ft.Icons.IMAGE_OUTLINED and remove.icon == ft.Icons.DELETE_OUTLINE
        assert remove.visible == assigned

        await session.dispatch_event(choose._i, "click", None)
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        assert picker.ai_settings == view.ai_settings
        item = next(
            item
            for item in (*journal.ledger.accounts, *journal.ledger.categories)
            if item.id == identifier
        )
        labels = [control.value for control in walk(picker) if isinstance(control, ft.Text)]
        assert (
            f"{view.tr('category_name' if kind == 'category' else 'account_name')}: {item.name}"
            in labels
        )
        assert (
            f"{view.tr('category_path' if kind == 'category' else 'account_code')}: {identifier}"
            in labels
        )
        assert not any(isinstance(label, str) and "Unsaved name" in label for label in labels)
        picker._select(replacement)
        click(picker.cancel)
        assert [image.src for image in images(parent)] == ([original.data] if assigned else [])

        click(choose)
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        picker._select(replacement)
        assign = view.icons.assign
        monkeypatch.setattr(view.icons, "assign", AsyncMock(side_effect=IconError("icon_storage")))
        await click_async(picker.submit)
        assert picker.open
        assert [image.src for image in images(parent)] == ([original.data] if assigned else [])
        monkeypatch.setattr(view.icons, "assign", assign)
        await click_async(picker.submit)
        assert parent.open and name.value == "Unsaved name"
        assert [image.src for image in images(parent)] == [replacement.data]
        assert remove.visible
        assert view.icons.assets[identifier] == replacement

        click(choose)
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        click(picker.remove)
        await click_async(picker.submit)
        assert parent.open and name.value == "Unsaved name"
        assert not images(parent)
        assert not remove.visible
        assert identifier not in view.icons.assets
        assert journal.source == (tmp_path / FILENAME).read_text()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("kind", "identifier"), [("category", "Expenses:Food"), ("account", "Assets:Bank")]
)
def test_edit_entity_remove_icon_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, identifier: str
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.language = "en"
        view.icons = store_at(tmp_path)
        await view.icons.load()
        original = asset("original")
        await view.icons.assign(identifier, original, ())
        original_source = journal.source
        view._edit_entity(MagicMock(control=ft.IconButton(data=(kind, identifier))))
        parent = session.page._dialogs.controls[-1]
        assert isinstance(parent, ft.AlertDialog)
        name = next(control for control in walk(parent) if isinstance(control, ft.TextField))
        name.value = "Unsaved name"
        remove = next(
            control
            for control in walk(parent)
            if isinstance(control, ft.IconButton) and control.tooltip == view.tr("icon_remove")
        )
        assert remove.visible
        await session.dispatch_event(remove._i, "click", None)
        confirmation = session.page._dialogs.controls[-1]
        assert isinstance(confirmation, ft.AlertDialog) and not isinstance(confirmation, IconPicker)
        assert_deletion_title(confirmation, view.tr("icon_remove"))
        assert not any(isinstance(control, ft.TextField) for control in walk(confirmation))
        assert action(confirmation, 0).content == view.tr("crud_cancel")
        assert action(confirmation, -1).content == view.tr("crud_delete")
        await press(session, "Escape")
        assert parent.open and view.icons.assets[identifier] == original

        click(remove)
        confirmation = session.page._dialogs.controls[-1]
        assert isinstance(confirmation, ft.AlertDialog)
        assign = view.icons.assign
        monkeypatch.setattr(view.icons, "assign", AsyncMock(side_effect=IconError("icon_storage")))
        await click_async(action(confirmation, -1))
        assert confirmation.open and parent.open and remove.visible
        assert any(
            isinstance(control, ft.Text) and control.value == view.tr("icon_storage")
            for control in walk(confirmation)
        )
        assert view.icons.assets[identifier] == original
        assert [image.src for image in images(parent)] == [original.data]
        monkeypatch.setattr(view.icons, "assign", assign)
        await press(session, "Enter")
        assert not confirmation.open and parent.open and not remove.visible
        assert name.value == "Unsaved name" and not images(parent)
        assert identifier not in view.icons.assets and journal.source == original_source
        reloaded = store_at(tmp_path)
        await reloaded.load()
        assert identifier not in reloaded.assets

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_edit_icon_buttons_have_localized_tooltips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    async def scenario() -> None:
        session, view, _journal = await finance_session(tmp_path, monkeypatch)
        view.language = language
        view._edit_entity(MagicMock(control=ft.IconButton(data=("category", "Expenses:Food"))))
        parent = session.page._dialogs.controls[-1]
        assert isinstance(parent, ft.AlertDialog)
        actions = [
            control
            for control in walk(parent)
            if isinstance(control, ft.IconButton)
            and control.icon in {ft.Icons.IMAGE_OUTLINED, ft.Icons.DELETE_OUTLINE}
        ]
        assert [control.tooltip for control in actions] == [
            view.tr("icon_choose"),
            view.tr("icon_remove"),
        ]

    asyncio.run(scenario())


def test_remove_shortcut_rejects_stale_icon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, view, _journal = await finance_session(tmp_path, monkeypatch)
        view.language = "en"
        view.icons = store_at(tmp_path)
        await view.icons.load()
        identifier = "Expenses:Food"
        await view.icons.assign(identifier, asset("original"), ())
        view._edit_entity(MagicMock(control=ft.IconButton(data=("category", identifier))))
        parent = session.page._dialogs.controls[-1]
        assert isinstance(parent, ft.AlertDialog)
        remove = next(
            control
            for control in walk(parent)
            if isinstance(control, ft.IconButton) and control.tooltip == view.tr("icon_remove")
        )
        click(remove)
        confirmation = session.page._dialogs.controls[-1]
        assert isinstance(confirmation, ft.AlertDialog) and not isinstance(confirmation, IconPicker)
        other = store_at(tmp_path)
        await other.load()
        replacement = asset("replacement")
        await other.assign(identifier, replacement, other.heads[identifier])
        await click_async(action(confirmation, -1))
        assert confirmation.open and parent.open
        assert any(
            isinstance(control, ft.Text) and control.value == view.tr("icon_stale")
            for control in walk(confirmation)
        )
        reloaded = store_at(tmp_path)
        await reloaded.load()
        assert reloaded.assets[identifier] == replacement

    asyncio.run(scenario())


async def creation_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> tuple[Session, FinanceView, Journal, ft.AlertDialog, IconDraft, str]:
    session, view, journal = await finance_session(tmp_path, monkeypatch)
    view.language = "en"
    view.icons = store_at(tmp_path)
    await view.icons.load()
    if kind in {"asset", "liability"}:
        view._create_account(MagicMock())
        root = "Liabilities" if kind == "liability" else "Assets"
        identifier = f"{root}:New"
        view.account_root.value = root
        view.account_name.value = "New account"
        view.account_id.value = identifier
        view.currency.value = "UAH"
    elif kind.startswith("quick-"):
        view._transaction_dialog(journal)
        transaction = session.page._dialogs.controls[-1]
        assert isinstance(transaction, ft.AlertDialog)
        form = transaction_form(transaction)
        form.choose(kind.removeprefix("quick-"))
        form.amount.value = "125"
        form.description.value = "Parent draft"
        click(form.category_create)
        identifier = "Income:New" if kind == "quick-income" else "Expenses:New"
        dialog = session.page._dialogs.controls[-1]
        for control in walk(dialog):
            if isinstance(control, ft.TextField):
                if control.data == "quick_category_path":
                    control.value = identifier
                elif control.data == "quick_category_name":
                    control.value = "New category"
    else:
        view._create_category(MagicMock())
        root = "Income" if kind == "income" else "Expenses"
        identifier = f"{root}:New"
        view.category_root.value = root
        view.category_name.value = "New category"
        view.category_id.value = identifier
    dialog = session.page._dialogs.controls[-1]
    assert isinstance(dialog, ft.AlertDialog)
    draft = next(control for control in walk(dialog) if isinstance(control, IconDraft))
    return session, view, journal, dialog, draft, identifier


@pytest.mark.parametrize(
    "kind", ["asset", "liability", "expense", "income", "quick-expense", "quick-income"]
)
def test_creation_icon_is_a_draft_until_entity_is_saved_and_survives_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async def scenario() -> None:
        session, view, journal, parent, draft, identifier = await creation_form(
            tmp_path, monkeypatch, kind
        )
        original = journal.source
        assert isinstance(draft.controls[0], ft.Text)
        assert draft.controls[0].value == view.tr("icon_choose")
        row = draft.controls[1]
        assert isinstance(row, ft.Row) and row.alignment == ft.MainAxisAlignment.SPACE_BETWEEN
        assert draft.preview.width == 40 and draft.preview.alignment == ft.Alignment.CENTER
        assert isinstance(row.controls[1], ft.Row)
        assert row.controls[1].controls == [draft.choose_button, draft.remove_button]
        assert not draft.remove_button.visible
        click(draft.choose_button)
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        assert picker.ai_settings == view.ai_settings
        labels = [control.value for control in walk(picker) if isinstance(control, ft.Text)]
        category = kind not in {"asset", "liability"}
        assert view.tr("icon_target_draft") in labels
        name_label = view.tr("category_name" if category else "account_name")
        name = "New category" if category else "New account"
        assert f"{name_label}: {name}" in labels
        assert f"{view.tr('category_path' if category else 'account_code')}: {identifier}" in labels
        selected = bundled_icons("bank")[0]
        picker._select(selected)
        await session.dispatch_event(picker.search._i, "focus", {})
        await press(session, "Enter")
        assert not picker.open and parent.open
        assert draft.selected == selected
        assert draft.remove_button.visible
        assert images(draft)[0].width == images(draft)[0].height == 16
        assert journal.source == original
        assert view.icons is not None and not view.icons.records
        await click_async(action(parent, -1))
        assert not parent.open and view.error_message is None
        assert identifier in view._existing_identifiers(journal)
        assert view.icons.assets[identifier] == selected
        assert images(view.home_balances if kind in {"asset", "liability"} else view.category_list)
        if kind.startswith("quick-"):
            transaction = next(
                dialog
                for dialog in session.page._dialogs.controls
                if isinstance(dialog, ft.AlertDialog) and dialog.open
            )
            assert transaction.open
            form = transaction_form(transaction)
            assert form.category.value == identifier
            assert form.amount.value == "125" and form.description.value == "Parent draft"
            assert form.category.leading_icon is not None
            option = next(option for option in form.category.options if option.key == identifier)
            assert category_content(option).padding == ft.Padding.only(left=0)
            assert images(category_content(option))
        saved = journal.source
        reloaded = store_at(tmp_path)
        await reloaded.load()
        assert reloaded.assets[identifier] == selected
        await journal.load()
        assert journal.source == saved == (tmp_path / FILENAME).read_text()
        assert selected.label not in saved

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["asset", "expense", "quick-expense"])
def test_creation_picker_shows_current_values_and_empty_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async def scenario() -> None:
        session, view, _journal, parent, draft, _identifier = await creation_form(
            tmp_path, monkeypatch, kind
        )
        category = kind != "asset"
        if kind == "quick-expense":
            fields = {
                control.data: control
                for control in walk(parent)
                if isinstance(control, ft.TextField)
            }
            name, code = fields["quick_category_name"], fields["quick_category_path"]
        else:
            name = view.category_name if category else view.account_name
            code = view.category_id if category else view.account_id
        name.value = code.value = ""
        click(draft.choose_button)
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        labels = [control.value for control in walk(picker) if isinstance(control, ft.Text)]
        name_label = view.tr("category_name" if category else "account_name")
        code_label = view.tr("category_path" if category else "account_code")
        assert f"{name_label}: {view.tr('icon_target_empty')}" in labels
        assert f"{code_label}: {view.tr('icon_target_empty')}" in labels
        click(picker.cancel)

        name.value = "Updated draft"
        code.value = "Parent:Child"
        click(draft.choose_button)
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        labels = [control.value for control in walk(picker) if isinstance(control, ft.Text)]
        assert f"{name_label}: Updated draft" in labels
        assert f"{code_label}: Parent:Child" in labels
        click(picker.cancel)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["asset", "expense", "quick-expense"])
@pytest.mark.parametrize("cancel", ["picker", "creation", "remove"])
def test_creation_icon_cancellation_and_removal_do_not_write_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, cancel: str
) -> None:
    async def scenario() -> None:
        session, view, journal, parent, draft, identifier = await creation_form(
            tmp_path, monkeypatch, kind
        )
        original = journal.source
        click(draft.choose_button)
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        picker._select(asset())
        await click_async(picker.submit)
        assert draft.selected == asset()
        click(draft.choose_button)
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker) and picker.selected == asset()
        picker._select(asset("Another"))
        if cancel == "remove":
            click(picker.remove)
            await click_async(picker.submit)
            assert draft.selected is None and draft.preview.content is None
        else:
            await session.dispatch_event(picker.search._i, "focus", {})
            await press(session, "Escape")
            assert draft.selected == asset()
        if cancel == "creation":
            click(action(parent, 0))
            assert journal.source == original
            assert identifier not in view._existing_identifiers(journal)
        else:
            if cancel == "picker":
                assert journal.source == original
                click(action(parent, 0))
            else:
                await click_async(action(parent, -1))
                assert identifier in view._existing_identifiers(journal)
        assert view.icons is not None and not view.icons.records

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind", ["asset", "liability", "expense", "income", "quick-expense", "quick-income"]
)
def test_creation_remove_icon_shortcut_keeps_draft_until_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async def scenario() -> None:
        session, view, journal, parent, draft, _identifier = await creation_form(
            tmp_path, monkeypatch, kind
        )
        original = journal.source
        click(draft.choose_button)
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        picker._select(asset())
        await click_async(picker.submit)
        assert draft.selected == asset() and draft.remove_button.visible

        await session.dispatch_event(draft.remove_button._i, "click", None)
        confirmation = session.page._dialogs.controls[-1]
        assert isinstance(confirmation, ft.AlertDialog) and not isinstance(confirmation, IconPicker)
        assert_deletion_title(confirmation, view.tr("icon_remove"))
        assert not any(isinstance(control, ft.TextField) for control in walk(confirmation))
        await press(session, "Escape")
        assert parent.open and draft.selected == asset() and draft.remove_button.visible

        click(draft.remove_button)
        confirmation = session.page._dialogs.controls[-1]
        assert isinstance(confirmation, ft.AlertDialog) and not isinstance(confirmation, IconPicker)
        await press(session, "Enter")
        assert parent.open and draft.selected is None and not draft.remove_button.visible
        assert draft.preview.content is None and journal.source == original
        assert view.icons is not None and not view.icons.records

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["asset", "expense", "quick-expense"])
def test_failed_entity_creation_keeps_icon_draft_without_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async def scenario() -> None:
        _session, view, journal, parent, draft, identifier = await creation_form(
            tmp_path, monkeypatch, kind
        )
        draft.selected = asset()
        original = journal.source
        method = "create_account" if kind == "asset" else "create_category"
        monkeypatch.setattr(journal, method, AsyncMock(side_effect=StorageError("write failed")))
        await click_async(action(parent, -1))
        assert parent.open and draft.selected == asset()
        assert view.error_message == "write failed"
        assert journal.source == original
        assert identifier not in view._existing_identifiers(journal)
        assert view.icons is not None and not view.icons.records

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["asset", "expense", "quick-expense"])
@pytest.mark.parametrize("failure", ["write", "concurrent", "unavailable"])
def test_post_creation_icon_failure_closes_creation_and_reports_saved_entity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, failure: str
) -> None:
    async def scenario() -> None:
        session, view, journal, parent, draft, identifier = await creation_form(
            tmp_path, monkeypatch, kind
        )
        assert view.icons is not None
        draft.selected = asset("Requested")
        if failure == "write":
            monkeypatch.setattr(
                view.icons, "assign", AsyncMock(side_effect=StorageError("write failed"))
            )
        elif failure == "concurrent":
            await view.icons.assign(identifier, asset("Concurrent"), ())
        else:
            view.icons = None
        await click_async(action(parent, -1))
        assert not parent.open
        assert identifier in view._existing_identifiers(journal)
        assert view.error_message == "icon_create_failed"
        assert view.icon_notice.visible and view.icon_notice.value == view.tr("icon_create_failed")
        assert "created" in (view.message.value or "")
        if failure == "concurrent":
            assert view.icons is not None and view.icons.assets[identifier].label == "Concurrent"
        if kind.startswith("quick-"):
            transaction = next(
                dialog
                for dialog in session.page._dialogs.controls
                if isinstance(dialog, ft.AlertDialog) and dialog.open
            )
            assert transaction.open
            assert transaction_form(transaction).category.value == identifier
        await journal.load()
        assert (
            sum(
                item.id == identifier
                for item in (*journal.ledger.accounts, *journal.ledger.categories)
            )
            == 1
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("case", ["narrated", "blank", "opening-small", "opening-large"])
def test_icon_movements_keep_consistent_columns_with_or_without_inline_amount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compact: bool, case: str
) -> None:
    async def scenario() -> None:
        _session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.assign("Assets:Bank", asset("Bank"), ())
        await journal.add_expense(
            "Assets:Bank", "Expenses:Food", "10", "", "2026-10-18", "Synthetic"
        )
        transaction = journal.ledger.transactions[-1]
        if case == "blank":
            transaction = replace(transaction, narration="")
            await view.icons.assign("Expenses:Food", asset("Food"), ())
        elif case.startswith("opening"):
            amount = Decimal("123.17" if case == "opening-small" else "234601.87")
            transaction = replace(
                transaction,
                kind="opening",
                postings=(
                    replace(transaction.postings[0], account="Assets:Bank", amount=amount),
                    replace(
                        transaction.postings[1], account="Equity:Opening-Balances", amount=-amount
                    ),
                ),
            )
        card = view._transaction_card(
            transaction, {"Assets:Bank": "Bank", "Expenses:Food": "Food"}, compact
        )
        assert isinstance(card, ft.Container) and isinstance(card.content, ft.Row)
        details = transaction_body(card)
        assert isinstance(details, ft.Column)
        movement = details.controls[-1]
        assert isinstance(movement, ft.Row) and len(movement.controls) == 2
        source, destination = movement.controls
        assert source.expand is True
        assert isinstance(destination, ft.Row) and destination.expand == 2
        arrow, target, *amounts = destination.controls
        assert isinstance(arrow, ft.Text) and arrow.value == "→"
        assert target.expand is True
        assert len(amounts) == (0 if case == "narrated" else 1)
        labels = [
            child
            for control in (source, target)
            for child in walk(control)
            if isinstance(child, ft.Text)
        ]
        assert len(labels) == 2
        assert all(
            label.max_lines == 1 and label.overflow == ft.TextOverflow.ELLIPSIS for label in labels
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("balance", ["-123.17", "0", "234601.87"])
def test_opening_icons_keep_title_and_account_for_assets_and_liabilities(
    tmp_path: Path, language: str, compact: bool, balance: str
) -> None:
    async def scenario() -> None:
        view = FinanceView()
        view.language = language
        view.icons = store_at(tmp_path)
        amount = Decimal(balance)
        for root in ("Assets", "Liabilities"):
            identifier = f"{root}:Bank"
            await view.icons.assign(identifier, asset("Bank"), ())
            transaction = Transaction(
                "opening",
                date(2026, 1, 1),
                "",
                "",
                "Existing opening narration",
                (Posting(identifier, amount, "UAH"), Posting(EQUITY, -amount, "UAH")),
                "opening",
            )
            card = view._transaction_card(transaction, {identifier: "Bank"}, compact)
            assert isinstance(card, ft.Container) and isinstance(card.content, ft.Row)
            leading = card.content.controls[0]
            assert isinstance(leading, ft.Container) and isinstance(leading.content, ft.Image)
            assert leading.content.semantics_label == "Bank"
            assert leading.content.width == leading.content.height == 25.92
            details = transaction_body(card)
            assert isinstance(details, ft.Column) and len(details.controls) == 1
            movement = details.controls[0]
            assert isinstance(movement, ft.Row)
            source, destination = movement.controls
            assert isinstance(destination, ft.Row)
            arrow, target, rendered_amount = destination.controls
            labels = [child for child in walk(source) if isinstance(child, ft.Text)]
            assert len(labels) == 1 and labels[0].value == view.tr("kind_opening")
            assert labels[0].size == 10
            assert not images(source)
            assert [child.value for child in walk(target) if isinstance(child, ft.Text)] == ["Bank"]
            assert not images(target)
            assert isinstance(arrow, ft.Text) and arrow.value == "→"
            assert isinstance(rendered_amount, ft.Text)
            assert rendered_amount.value == format_amount(abs(amount), language, "UAH")
            assert source.expand is True and target.expand is True and destination.expand == 2
            assert transaction.narration == "Existing opening narration"
            assert transaction.postings[0].amount == amount

    asyncio.run(scenario())


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize(
    ("kind", "source", "target", "leading_id", "account_id"),
    [
        ("expense", "Assets:Bank", "Expenses:Food", "Expenses:Food", "Assets:Bank"),
        ("income", "Income:Work", "Assets:Bank", "Income:Work", "Assets:Bank"),
        ("reimbursement", "Expenses:Food", "Assets:Bank", "Expenses:Food", "Assets:Bank"),
        ("transfer", "Assets:Bank", "Assets:Other", "Assets:Other", "Assets:Bank"),
        ("opening", EQUITY, "Assets:Bank", "Assets:Bank", None),
        ("opening", EQUITY, "Liabilities:Card", "Liabilities:Card", None),
    ],
)
def test_transaction_icon_leads_the_whole_row_without_duplicating_movement_icon(
    tmp_path: Path,
    compact: bool,
    read_only: bool,
    kind: str,
    source: str,
    target: str,
    leading_id: str,
    account_id: str | None,
) -> None:
    async def scenario() -> None:
        view = FinanceView()
        view.icons = store_at(tmp_path)
        await view.icons.assign(leading_id, asset("Leading"), ())
        if account_id is not None:
            await view.icons.assign(account_id, asset("Account"), ())
        transaction = Transaction(
            "synthetic",
            date(2026, 1, 1),
            "",
            "",
            "Synthetic title",
            (Posting(source, Decimal("-10"), "UAH"), Posting(target, Decimal("10"), "UAH")),
            kind,
        )
        card = view._transaction_card(
            transaction, {source: "Source", target: "Target"}, compact, read_only=read_only
        )
        assert isinstance(card, ft.Container) and isinstance(card.content, ft.Row)
        leading = card.content.controls[0]
        assert isinstance(leading, ft.Container)
        assert leading.width == 40 and leading.height is None
        assert leading.bgcolor == ft.Colors.SECONDARY_CONTAINER
        assert leading.border_radius == 8
        assert leading.margin == ft.Margin.only(right=8)
        assert leading.alignment == ft.Alignment.CENTER
        image = leading.content
        assert isinstance(image, ft.Image)
        assert image.width == image.height == 25.92
        assert image.semantics_label == "Leading"
        assert image.fit == ft.BoxFit.CONTAIN and image.color == ft.Colors.ON_SURFACE
        assert image.tooltip == "Leading · CC0-1.0"
        assert card.content.intrinsic_height
        assert card.content.vertical_alignment == ft.CrossAxisAlignment.STRETCH
        details = transaction_body(card)
        assert card.content.controls[1] is details
        assert [item.semantics_label for item in images(details)] == (
            ["Account"] if account_id is not None else []
        )
        assert all(item.width == item.height == 16 for item in images(details))
        assert len(card.content.controls[2:]) == (0 if read_only else 1 if compact else 2)
        assert transaction.narration == "Synthetic title"

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["expense", "income"])
@pytest.mark.parametrize("fee_mode", ["added", "deducted"])
def test_leading_cashflow_icon_uses_principal_category_not_commission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, fee_mode: str
) -> None:
    async def scenario() -> None:
        _session, view, journal = await finance_session(tmp_path, monkeypatch)
        await journal.create_category("Salary", "Salary", root="Income", opened=date(2000, 1, 1))
        await journal.create_category("Fees", "Fees", opened=date(2000, 1, 1))
        category = "Expenses:Food" if kind == "expense" else "Income:Salary"
        view.icons = store_at(tmp_path)
        for identifier, label in (
            (category, "Principal"),
            ("Assets:Bank", "Account"),
            ("Expenses:Fees", "Commission"),
        ):
            await view.icons.assign(identifier, asset(label), ())
        options = {"fee": "5", "fee_category": "Expenses:Fees", "fee_mode": fee_mode}
        if kind == "expense":
            await journal.add_expense(
                "Assets:Bank", category, "100", "", "2026-01-01", "Synthetic", **options
            )
        else:
            await journal.add_incoming(
                "Assets:Bank", category, "100", "", "2026-01-01", kind, "Synthetic", **options
            )
        source = journal.source
        view._refresh()
        for context in (view.home_activity, view.history):
            rendered = images(context)
            assert [image.semantics_label for image in rendered] == ["Principal", "Account"]
            assert [image.width for image in rendered] == [25.92, 16]
        assert journal.source == source == (tmp_path / FILENAME).read_text()

    asyncio.run(scenario())


@pytest.mark.parametrize("with_store", [False, True])
def test_missing_category_icon_preserves_row_without_fallback_slot(
    tmp_path: Path, with_store: bool
) -> None:
    async def scenario() -> None:
        view = FinanceView()
        if with_store:
            view.icons = store_at(tmp_path)
            await view.icons.assign("Assets:Bank", asset("Account"), ())
        transaction = Transaction(
            "synthetic",
            date(2026, 1, 1),
            "",
            "",
            "Synthetic title",
            (
                Posting("Assets:Bank", Decimal("-10"), "UAH"),
                Posting("Expenses:Food", Decimal("10"), "UAH"),
            ),
            "expense",
        )
        card = view._transaction_card(transaction, {"Assets:Bank": "Bank", "Expenses:Food": "Food"})
        assert isinstance(card, ft.Container) and isinstance(card.content, ft.Row)
        assert card.content.controls[0] is transaction_body(card)
        assert not card.content.intrinsic_height
        assert all(image.width == image.height == 16 for image in images(card))

    asyncio.run(scenario())


@pytest.mark.parametrize("artwork", ["monochrome-svg", "colored-raster"])
def test_leading_icon_preserves_artwork_and_does_not_resize_other_surfaces(
    tmp_path: Path, artwork: str
) -> None:
    async def scenario() -> None:
        view = FinanceView()
        view.icons = store_at(tmp_path)
        selected = asset("Category")
        if artwork == "colored-raster":
            selected = replace(selected, data=normalize_image(raster(48, 96))[0], monochrome=False)
        await view.icons.assign("Expenses:Food", selected, ())
        transaction = Transaction(
            "synthetic",
            date(2026, 1, 1),
            "",
            "",
            "Synthetic",
            (
                Posting("Assets:Bank", Decimal("-10"), "UAH"),
                Posting("Expenses:Food", Decimal("10"), "UAH"),
            ),
            "expense",
        )
        card = view._transaction_card(transaction, {})
        image = images(card)[0]
        assert image.width == image.height == 25.92
        assert image.src == selected.data and image.fit == ft.BoxFit.CONTAIN
        assert image.color == (ft.Colors.ON_SURFACE if selected.monochrome else None)
        assert image.tooltip == "Category · CC0-1.0"
        inline = view._entity_icon("Expenses:Food")
        assert isinstance(inline, ft.Image) and inline.width == inline.height == 16
        assert view.icons.assets["Expenses:Food"] == selected

    asyncio.run(scenario())


def test_complex_transaction_does_not_guess_a_leading_category(tmp_path: Path) -> None:
    async def scenario() -> None:
        view = FinanceView()
        view.icons = store_at(tmp_path)
        await view.icons.assign("Expenses:Food", asset("Food"), ())
        transaction = Transaction(
            "synthetic",
            date(2026, 1, 1),
            "",
            "",
            "Complex entry",
            (
                Posting("Assets:Bank", Decimal("-10"), "UAH"),
                Posting("Expenses:Food", Decimal("5"), "UAH"),
                Posting("Expenses:Other", Decimal("5"), "UAH"),
            ),
            "external",
        )
        card = view._transaction_card(transaction, {})
        assert isinstance(card, ft.Container) and isinstance(card.content, ft.Row)
        assert card.content.controls[0] is transaction_body(card)
        assert not images(card)

    asyncio.run(scenario())


@pytest.mark.parametrize("key", ["Escape", "Enter"])
def test_picker_keys_cancel_or_save_and_leave_parent_draft_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.load()
        view._transaction_dialog(journal)
        parent = session.page._dialogs.controls[-1]
        assert isinstance(parent, ft.AlertDialog)
        form = transaction_form(parent)
        form.payee.value = "Synthetic unsaved draft"
        original = (tmp_path / FILENAME).read_bytes()
        view._open_icon_picker(journal, "Assets:Bank")
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        picker._select(bundled_icons("bank")[0])
        await session.dispatch_event(picker.search._i, "focus", {})
        await press(session, key)
        assert not picker.open and parent.open
        assert form.payee.value == "Synthetic unsaved draft"
        assert (tmp_path / FILENAME).read_bytes() == original
        assert bool(view.icons.assets) == (key == "Enter")
        if key == "Enter":
            assert images(view.home_balances)

    asyncio.run(scenario())


def test_failed_save_retains_selection_and_does_not_close_picker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        persist = AsyncMock(side_effect=IconError("icon_storage"))
        picker = IconPicker(session.page, "en", asset(), persist)
        show_dialog(session.page, picker)
        await session.dispatch_event(picker.search._i, "focus", {})
        await press(session, "Enter")
        assert picker.open and picker.selected == asset()
        assert "permissions" in (picker.status.value or "")
        assert not picker.submit.disabled and not picker.cancel.disabled

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["picker", "removal"])
def test_failed_busy_update_does_not_leave_icon_dialog_unresponsive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        persist = AsyncMock()
        dialog = (
            IconPicker(session.page, "en", asset(), persist)
            if kind == "picker"
            else IconRemovalDialog(session.page, "en", persist)
        )
        show_dialog(session.page, dialog)
        update = session.page.update
        failures = [RuntimeError("update failed")]

        def flaky_update(*controls: ft.Control) -> None:
            if failures:
                raise failures.pop()
            update(*controls)

        monkeypatch.setattr(session.page, "update", flaky_update)
        with pytest.raises(RuntimeError, match="update failed"):
            await click_async(dialog.submit)
        persist.assert_not_awaited()
        assert not dialog.busy and not dialog.cancel.disabled and not dialog.submit.disabled
        click(dialog.cancel)
        assert not dialog.open

    asyncio.run(scenario())


def test_picker_search_uses_bundled_catalogue_without_online_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        picker = IconPicker(session.page, "en", None, AsyncMock())
        assert len(picker.results.controls) == 100
        assert picker.results in picker.search.controls
        assert picker.results_limit in picker.search.controls
        assert picker.search.view_size_constraints == ft.BoxConstraints(max_height=320)
        assert not any(
            "online" in (control.value or "").casefold()
            for control in walk(picker)
            if isinstance(control, ft.Text)
        )
        picker.search.value = "банк"
        picker._local(MagicMock(control=picker.search))
        assert picker.results.controls[0].data == bundled_icons("банк")[0]
        picker.search.value = "nothing-matches-this-icon-query"
        picker._local(MagicMock(control=picker.search))
        assert len(picker.results.controls) == 1
        assert isinstance(picker.results.controls[0], ft.Text)
        assert picker.results.controls[0].value == picker.tr("icon_no_results")

    asyncio.run(scenario())


def test_picker_search_finds_previously_assigned_icons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        _session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.load()
        legacy = asset("monobank")
        await view.icons.assign("Assets:Bank", legacy, ())

        view._open_icon_picker(journal, "Assets:Bank")
        picker = view.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        picker.search.value = "monobank"
        picker._local(MagicMock(control=picker.search))
        assert any(
            isinstance(control, ft.TextButton) and control.data == legacy
            for control in picker.results.controls
        )

    asyncio.run(scenario())


def test_picker_search_opens_and_selects_without_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        persist = AsyncMock()
        picker = IconPicker(session.page, "en", asset("Previous"), persist)
        show_dialog(session.page, picker)
        open_view = AsyncMock()
        close_view = AsyncMock()
        monkeypatch.setattr(picker.search, "open_view", open_view)
        monkeypatch.setattr(picker.search, "close_view", close_view)
        assert picker.import_button.icon == ft.Icons.UPLOAD_FILE
        assert picker.remove.icon == ft.Icons.DELETE_OUTLINE
        assert images(picker.preview)[0].width == images(picker.preview)[0].height == 48
        await picker._open_search(ft.Event(name="tap", control=picker.search))
        open_view.assert_awaited_once()
        selected = bundled_icons("bank")[0]
        await picker._choose(ft.Event(name="click", control=ft.TextButton(data=selected)))
        close_view.assert_awaited_once()
        assert picker.selected == selected and picker.open
        assert images(picker.preview)[0].width == images(picker.preview)[0].height == 48
        persist.assert_not_awaited()
        session.page.pop_dialog()

    asyncio.run(scenario())


def test_picker_selection_panel_tracks_draft_and_keeps_original_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        original, candidate = asset("Original"), asset("Candidate")
        persist = AsyncMock()
        picker = IconPicker(session.page, "en", original, persist)
        show_dialog(session.page, picker)
        assert isinstance(picker.ai_generate, ft.FilledButton)
        assert picker.ai_section.controls is not None
        assert picker.ai_section.controls[1] is picker.ai_reference_row
        assert picker.ai_section.controls[2] is picker.ai_prompt
        action_row = picker.ai_section.controls[3]
        assert isinstance(action_row, ft.Row) and action_row.controls[0] is picker.ai_generate
        assert any(
            isinstance(control, ft.Text) and control.value == picker.tr("icon_selected")
            for control in walk(picker)
        )
        assert picker.ai_reference_tile.title == picker.tr("icon_ai_reference")
        assert picker.ai_reference_tile.subtitle == picker.tr("icon_ai_reference_hint")
        assert picker.reference_preview is not None
        assert isinstance(picker.reference_preview.content, ft.Image)
        picker._select(candidate)
        assert picker.selected == candidate
        assert images(picker.preview)[0].src == candidate.data
        assert picker.selection_license.value == candidate.license
        assert picker.reference_preview.content.src == original.data
        assert picker.reference == original
        picker._remove(MagicMock())
        assert picker.selected is None and picker.remove.disabled
        assert isinstance(picker.preview.controls[0], ft.Text)
        assert picker.preview.controls[0].value == picker.tr("icon_none")
        assert not picker.selection_license.value and not picker.selection_rights.visible
        persist.assert_not_awaited()
        click(picker.cancel)
        assert not picker.open

    asyncio.run(scenario())


def test_import_resizes_previews_and_requires_explicit_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        persist = AsyncMock()
        data = raster(512, 512)
        monkeypatch.setattr(
            ft.FilePicker,
            "pick_files",
            AsyncMock(
                return_value=[
                    ft.FilePickerFile(id=1, name="synthetic.png", size=len(data), bytes=data)
                ]
            ),
        )
        picker = IconPicker(session.page, "en", None, persist)
        show_dialog(session.page, picker)
        await click_async(picker.import_button)
        assert picker.selected is not None
        assert not picker.selected.monochrome
        assert "permission" in (picker.status.value or "")
        assert len(images(picker.preview)) == 1
        image = images(picker.preview)[0]
        assert image.width == image.height == 48
        persist.assert_not_awaited()
        await press(session)
        persist.assert_not_awaited()

    asyncio.run(scenario())


def test_invalid_import_never_appears_as_candidate_or_replaces_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        data = raster(16, 16)
        monkeypatch.setattr(
            ft.FilePicker,
            "pick_files",
            AsyncMock(
                return_value=[ft.FilePickerFile(id=1, name="small.png", size=len(data), bytes=data)]
            ),
        )
        picker = IconPicker(session.page, "en", asset(), AsyncMock())
        show_dialog(session.page, picker)
        original = list(picker.results.controls)
        await click_async(picker.import_button)
        assert picker.selected == asset()
        assert picker.results.controls == original
        assert "48" in (picker.status.value or "")

    asyncio.run(scenario())


def test_ai_picker_requires_key_and_valid_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        settings = AISettings(DesktopPreferences(tmp_path / "ai.json"), FakeCredentials())
        picker = IconPicker(session.page, "en", asset(), AsyncMock(), ai_settings=lambda: settings)
        show_dialog(session.page, picker)
        assert picker.ai_load_task is not None
        await asyncio.wrap_future(picker.ai_load_task)
        assert not picker.ai_section.visible
        assert "API key" in picker.ai_hint.value
        await settings.save_key(Provider.OPENAI, "test-key")
        await picker._load_ai()
        assert not picker.ai_section.visible
        assert "model" in picker.ai_hint.value
        await settings.select(Capability.GENERATE, "openai:gpt-image-2.5-flare")
        await picker._load_ai()
        assert picker.ai_section.visible and not picker.ai_hint.visible
        await settings.remove_key(Provider.OPENAI)
        await picker._load_ai()
        assert not picker.ai_section.visible and "API key" in picker.ai_hint.value
        session.page.pop_dialog()

    asyncio.run(scenario())


def test_ai_generation_uses_selected_model_and_saves_only_after_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        settings = AISettings(DesktopPreferences(tmp_path / "ai.json"), FakeCredentials())
        await settings.save_key(Provider.OPENAI, "test-key")
        await settings.select(Capability.GENERATE, "openai:gpt-image-2.5-flare")
        requests: list[dict] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200, json={"data": [{"b64_json": base64.b64encode(raster(512, 512)).decode()}]}
            )

        monkeypatch.setattr(
            icon_ui,
            "AIService",
            lambda value: AIService(value, transport=httpx.MockTransport(handle)),
        )
        persist = AsyncMock()
        picker = IconPicker(session.page, "en", asset(), persist, ai_settings=lambda: settings)
        scroll = AsyncMock()
        monkeypatch.setattr(picker.body, "scroll_to", scroll)
        show_dialog(session.page, picker)
        assert picker.ai_load_task is not None
        await asyncio.wrap_future(picker.ai_load_task)
        assert not requests
        picker.ai_prompt.value = "A red watercolor fox, not monochrome"
        await click_async(picker.ai_generate)
        assert len(requests) == 1
        prompt = requests[0]["prompt"]
        assert requests[0]["model"] == "gpt-image-2.5-flare"
        assert "images" not in requests[0]
        assert picker.ai_reference_row.visible and not picker.ai_reference.value
        assert (
            "Material Icons" in prompt and "explicit style instructions take precedence" in prompt
        )
        assert "thick, bold strokes over fine lines" in prompt
        assert "When in doubt, prefer fewer, larger elements over smaller details." in prompt
        assert prompt.endswith(picker.ai_prompt.value)
        assert picker.ai_prompt.value not in (picker.status.value or "")
        assert all(
            "Material Icons" not in (control.value or "")
            for control in walk(picker)
            if isinstance(control, ft.Text)
        )
        assert picker.selected is not None and picker.selected.source == "AI-generated image"
        assert picker.selected.license == "AI-generated; rights not verified"
        assert picker.selection_license.value == picker.selected.license
        assert picker.selection_rights.visible
        assert picker.ai_generate.content == picker.tr("icon_ai_generate_again")
        assert picker.ai_status.value == picker.tr("icon_ai_ready")
        assert picker.ai_feedback.label == picker.ai_status.value
        assert picker.ai_feedback.live_region and picker.ai_feedback.exclude_semantics
        scroll.assert_awaited_once_with(scroll_key=picker.selection_panel.key, duration=350)
        assert len(images(picker.preview)) == 1
        assert images(picker.preview)[0].width == images(picker.preview)[0].height == 48
        persist.assert_not_awaited()
        generated = picker.selected
        await click_async(picker.submit)
        persist.assert_awaited_once_with(generated)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "color", [(20, 20, 20, 255), (45, 47, 59, 255)], ids=["gray", "cool-neutral"]
)
def test_ai_generation_persists_theme_adaptive_fitted_icon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, color: tuple[int, int, int, int]
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        settings = AISettings(DesktopPreferences(tmp_path / "ai.json"), FakeCredentials())
        await settings.save_key(Provider.OPENAI, "test-key")
        await settings.select(Capability.GENERATE, "openai:gpt-image-2.5-flare")
        image = Image.new("RGBA", (256, 256))
        ImageDraw.Draw(image).rectangle((104, 100, 151, 155), fill=color)
        output = BytesIO()
        image.save(output, format="PNG")
        service = MagicMock()
        service.generate_image = AsyncMock(
            return_value=GeneratedImage(output.getvalue(), "image/png")
        )
        monkeypatch.setattr(icon_ui, "AIService", lambda _: service)
        store = store_at(tmp_path)

        async def save(selected: IconAsset | None) -> None:
            await store.assign("Expenses:Food", selected, ())

        picker = IconPicker(session.page, "en", None, save, ai_settings=lambda: settings)
        monkeypatch.setattr(picker.body, "scroll_to", AsyncMock())
        show_dialog(session.page, picker)
        assert picker.ai_load_task is not None
        await asyncio.wrap_future(picker.ai_load_task)
        picker.ai_prompt.value = "Food"
        await click_async(picker.ai_generate)
        assert picker.selected is not None and picker.selected.monochrome
        assert images(picker.preview)[0].color == ft.Colors.ON_SURFACE
        await click_async(picker.submit)
        reloaded = store_at(tmp_path)
        await reloaded.load()
        saved = reloaded.assets["Expenses:Food"]
        assert saved.monochrome and icon_image(saved).color == ft.Colors.ON_SURFACE
        with Image.open(BytesIO(saved.data)) as rendered:
            bounds = rendered.getchannel("A").getbbox()
            assert bounds is not None
            assert 78 <= max(bounds[2] - bounds[0], bounds[3] - bounds[1]) <= 82

    asyncio.run(scenario())


@pytest.mark.parametrize("vector", [False, True])
def test_ai_reference_is_opt_in_and_preserves_original_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vector: bool
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        settings = AISettings(DesktopPreferences(tmp_path / "ai.json"), FakeCredentials())
        await settings.save_key(Provider.OPENAI, "test-key")
        await settings.select(Capability.GENERATE, "openai:gpt-image-2.5-flare")
        original = replace(
            asset("Original"),
            data=asset().data if vector else normalize_image(raster())[0],
            monochrome=vector,
            license_url="https://example.test/license",
            license_text="Original license text",
        )
        captured = Image.new("RGBA", (96, 96))
        ImageDraw.Draw(captured).rectangle((30, 30, 65, 65), fill=(240, 240, 240, 128))
        capture_data = BytesIO()
        captured.save(capture_data, format="PNG")
        capture = AsyncMock(return_value=capture_data.getvalue())
        monkeypatch.setattr(ft.Screenshot, "capture", capture)
        service = MagicMock()
        service.generate_image = AsyncMock(
            return_value=GeneratedImage(raster(512, 512), "image/png")
        )
        monkeypatch.setattr(icon_ui, "AIService", lambda _: service)
        picker = IconPicker(session.page, "en", original, AsyncMock(), ai_settings=lambda: settings)
        monkeypatch.setattr(picker.body, "scroll_to", AsyncMock())
        show_dialog(session.page, picker)
        assert picker.ai_load_task is not None
        await asyncio.wrap_future(picker.ai_load_task)
        assert picker.reference_preview is not None
        assert isinstance(picker.reference_preview.content, ft.Image)
        assert picker.reference_preview.content.src == original.data
        assert not picker.ai_reference.value
        picker.selected = asset("Another candidate")
        picker.ai_reference.value = True
        picker.ai_prompt.value = "Add an X"
        await click_async(picker.ai_generate)
        args, kwargs = service.generate_image.await_args
        assert args[0].endswith("Add an X")
        assert "reference icon" in args[0]
        assert "requested changes take precedence" in args[0]
        assert "thick, bold strokes over fine lines" in args[0]
        if vector:
            with Image.open(BytesIO(kwargs["reference_image"])) as sent:
                assert sent.getpixel((40, 40)) == (0, 0, 0, 128)
                assert sent.getchannel("A").getpixel((0, 0)) == 0
        else:
            assert kwargs["reference_image"] == normalize_image(original.data)[0]
        assert capture.await_count == int(vector)
        result = picker.selected
        assert result is not None and result.license == picker.tr("icon_ai_rights")
        assert all(
            text in result.attribution
            for text in (original.label, original.source, original.attribution, original.license)
        )
        assert result.license_url == original.license_url
        assert result.license_text == original.license_text
        assert IconAsset.parse(result.serialize()) == result
        session.page.pop_dialog()

    asyncio.run(scenario())


def test_ai_reference_option_absent_without_existing_icon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        picker = IconPicker(session.page, "en", None, AsyncMock())
        assert not picker.ai_reference_row.visible
        assert picker.reference_preview is None

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["empty", "transparent", "capture", "oversized-credit"])
def test_ai_reference_preparation_failure_never_sends_or_replaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        settings = AISettings(DesktopPreferences(tmp_path / "ai.json"), FakeCredentials())
        await settings.save_key(Provider.OPENAI, "test-key")
        await settings.select(Capability.GENERATE, "openai:gpt-image-2.5-flare")
        original = asset()
        if failure == "oversized-credit":
            original = replace(original, attribution="a" * 32768)
        stream = BytesIO()
        Image.new("RGBA", (96, 96)).save(stream, format="PNG")
        capture = AsyncMock(
            return_value=stream.getvalue() if failure == "transparent" else b"",
            side_effect=RuntimeError("private capture detail") if failure == "capture" else None,
        )
        monkeypatch.setattr(ft.Screenshot, "capture", capture)
        service = MagicMock()
        monkeypatch.setattr(icon_ui, "AIService", lambda _: service)
        picker = IconPicker(session.page, "en", original, AsyncMock(), ai_settings=lambda: settings)
        show_dialog(session.page, picker)
        assert picker.ai_load_task is not None
        await asyncio.wrap_future(picker.ai_load_task)
        picker.ai_reference.value = True
        picker.ai_prompt.value = "Redraw"
        await click_async(picker.ai_generate)
        service.generate_image.assert_not_called()
        assert picker.selected == original
        assert picker.ai_status.value
        assert picker.ai_feedback.label == picker.ai_status.value
        assert not picker.busy and not picker.ai_reference_tile.disabled
        session.page.pop_dialog()

    asyncio.run(scenario())


def test_ai_reference_capture_can_be_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        settings = AISettings(DesktopPreferences(tmp_path / "ai.json"), FakeCredentials())
        await settings.save_key(Provider.OPENAI, "test-key")
        await settings.select(Capability.GENERATE, "openai:gpt-image-2.5-flare")
        started = asyncio.Event()

        async def capture(self: ft.Screenshot, **kwargs: object) -> bytes:
            started.set()
            await asyncio.Event().wait()
            return b""

        monkeypatch.setattr(ft.Screenshot, "capture", capture)
        service = MagicMock()
        monkeypatch.setattr(icon_ui, "AIService", lambda _: service)
        original = asset()
        picker = IconPicker(session.page, "en", original, AsyncMock(), ai_settings=lambda: settings)
        show_dialog(session.page, picker)
        assert picker.ai_load_task is not None
        await asyncio.wrap_future(picker.ai_load_task)
        picker.ai_reference.value = True
        picker.ai_prompt.value = "Redraw"
        task = asyncio.create_task(click_async(picker.ai_generate))
        await started.wait()
        assert picker.ai_reference.disabled and picker.ai_reference_tile.disabled
        click(picker.ai_cancel)
        await task
        service.generate_image.assert_not_called()
        assert picker.selected == original and not picker.ai_reference.disabled
        assert picker.ai_status.value == picker.tr("icon_ai_cancelled")
        session.page.pop_dialog()

    asyncio.run(scenario())


def test_ai_reference_capture_timeout_reports_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        settings = AISettings(DesktopPreferences(tmp_path / "ai.json"), FakeCredentials())
        await settings.save_key(Provider.OPENAI, "test-key")
        await settings.select(Capability.GENERATE, "openai:gpt-image-2.5-flare")

        async def capture(self: ft.Screenshot, **kwargs: object) -> bytes:
            await asyncio.Event().wait()
            return b""

        monkeypatch.setattr(ft.Screenshot, "capture", capture)
        monkeypatch.setattr(icon_ui, "_CAPTURE_TIMEOUT", 0.01)
        service = MagicMock()
        monkeypatch.setattr(icon_ui, "AIService", lambda _: service)
        original = asset()
        picker = IconPicker(session.page, "en", original, AsyncMock(), ai_settings=lambda: settings)
        show_dialog(session.page, picker)
        assert picker.ai_load_task is not None
        await asyncio.wrap_future(picker.ai_load_task)
        picker.ai_reference.value = True
        picker.ai_prompt.value = "Redraw"
        await click_async(picker.ai_generate)
        service.generate_image.assert_not_called()
        assert picker.selected == original
        assert picker.ai_status.value == picker.tr("icon_ai_reference_error")
        assert not picker.busy
        session.page.pop_dialog()

    asyncio.run(scenario())


@pytest.mark.parametrize("platform", [ft.PagePlatform.LINUX, ft.PagePlatform.ANDROID])
@pytest.mark.parametrize("description", ["A fox", ""])
def test_ai_prompt_enter_generates_without_saving_picker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: ft.PagePlatform, description: str
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        session.page.platform = platform
        settings = AISettings(DesktopPreferences(tmp_path / "ai.json"), FakeCredentials())
        await settings.save_key(Provider.OPENAI, "test-key")
        await settings.select(Capability.GENERATE, "openai:gpt-image-2.5-flare")
        service = MagicMock()
        service.generate_image = AsyncMock(
            return_value=GeneratedImage(raster(512, 512), "image/png")
        )
        monkeypatch.setattr(icon_ui, "AIService", lambda _: service)
        persist = AsyncMock()
        picker = IconPicker(session.page, "en", asset(), persist, ai_settings=lambda: settings)
        monkeypatch.setattr(picker.body, "scroll_to", AsyncMock())
        show_dialog(session.page, picker)
        assert picker.ai_load_task is not None
        await asyncio.wrap_future(picker.ai_load_task)
        picker.ai_section.expanded = True
        picker.ai_prompt.value = description
        session.page.update()
        await session.dispatch_event(picker.ai_prompt._i, "focus", None)
        if platform == ft.PagePlatform.LINUX:
            await press(session, "Enter")
        persist.assert_not_awaited()
        assert picker.open
        await session.dispatch_event(picker.ai_prompt._i, "submit", None)
        assert picker.open
        persist.assert_not_awaited()
        if description:
            service.generate_image.assert_awaited_once()
            assert picker.selected is not None and picker.selected.source == "AI-generated image"
        else:
            service.generate_image.assert_not_awaited()
            assert picker.selected == asset()
            assert picker.ai_status.value == picker.tr("icon_ai_prompt_required")

    asyncio.run(scenario())


def test_ai_prompt_submit_ignores_disabled_or_inactive_picker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        settings = AISettings(DesktopPreferences(tmp_path / "ai.json"), FakeCredentials())
        await settings.save_key(Provider.OPENAI, "test-key")
        await settings.select(Capability.GENERATE, "openai:gpt-image-2.5-flare")
        service = MagicMock()
        service.generate_image = AsyncMock()
        monkeypatch.setattr(icon_ui, "AIService", lambda _: service)
        picker = IconPicker(session.page, "en", asset(), AsyncMock(), ai_settings=lambda: settings)
        show_dialog(session.page, picker)
        assert picker.ai_load_task is not None
        await asyncio.wrap_future(picker.ai_load_task)
        picker.ai_section.expanded = True
        picker.ai_prompt.value = "A fox"
        picker.ai_prompt.disabled = True
        session.page.update()
        await session.dispatch_event(picker.ai_prompt._i, "submit", None)
        picker.ai_prompt.disabled = False
        child = ft.AlertDialog(actions=[ft.TextButton("Close", on_click=lambda _: None)])
        show_dialog(session.page, child)
        await session.dispatch_event(picker.ai_prompt._i, "submit", None)
        service.generate_image.assert_not_awaited()
        assert picker.open

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["asset", "expense", "quick-expense"])
def test_ai_icon_creation_persists_after_entity_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async def scenario() -> None:
        session, view, journal, parent, draft, identifier = await creation_form(
            tmp_path, monkeypatch, kind
        )
        preferences = DesktopPreferences(tmp_path / "ai.json")
        settings = AISettings(preferences, FakeCredentials())
        await settings.save_key(Provider.OPENAI, "test-key")
        await settings.select(Capability.GENERATE, "openai:gpt-image-2.5-flare")
        view.preferences = preferences
        view._ai_settings = settings
        service = MagicMock()
        service.generate_image = AsyncMock(
            return_value=GeneratedImage(raster(512, 512), "image/png")
        )
        monkeypatch.setattr(icon_ui, "AIService", lambda _: service)
        click(draft.choose_button)
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker) and picker.ai_load_task is not None
        monkeypatch.setattr(picker.body, "scroll_to", AsyncMock())
        await asyncio.wrap_future(picker.ai_load_task)
        assert picker.ai_section.visible
        picker.ai_prompt.value = "A fox"
        await click_async(picker.ai_generate)
        assert draft.selected is None
        await click_async(picker.submit)
        assert draft.selected is not None and parent.open
        await click_async(action(parent, -1))
        assert not parent.open
        reloaded = store_at(tmp_path)
        await reloaded.load()
        assert reloaded.assets[identifier] == draft.selected
        assert "A fox" not in journal.source

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["provider", "image", "cancel", "unexpected"])
def test_ai_generation_failure_or_cancel_keeps_previous_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        settings = AISettings(DesktopPreferences(tmp_path / "ai.json"), FakeCredentials())
        await settings.save_key(Provider.OPENAI, "test-key")
        await settings.select(Capability.GENERATE, "openai:gpt-image-2.5-flare")
        started = asyncio.Event()

        async def generate(prompt: str) -> object:
            started.set()
            if failure == "cancel":
                await asyncio.Event().wait()
            if failure == "provider":
                raise AIError("provider_network_error", provider=Provider.OPENAI)
            if failure == "unexpected":
                raise RuntimeError("private description and secret")
            return GeneratedImage(raster(16, 16), "image/png")

        service = MagicMock()
        service.generate_image = AsyncMock(side_effect=generate)
        monkeypatch.setattr(icon_ui, "AIService", lambda _: service)
        previous = asset()
        persist = AsyncMock()
        picker = IconPicker(session.page, "en", previous, persist, ai_settings=lambda: settings)
        show_dialog(session.page, picker)
        assert picker.ai_load_task is not None
        await asyncio.wrap_future(picker.ai_load_task)
        picker.ai_prompt.value = "A cat"
        task = asyncio.create_task(click_async(picker.ai_generate))
        await started.wait()
        if failure == "cancel":
            assert picker.ai_cancel.visible and picker.ai_progress.visible
            click(picker.ai_cancel)
        await task
        assert picker.selected == previous
        assert picker.ai_status.value
        assert not picker.busy and not picker.ai_cancel.visible
        persist.assert_not_awaited()
        assert "private description and secret" not in caplog.text
        session.page.pop_dialog()

    asyncio.run(scenario())


def test_stale_assignment_is_not_overwritten_and_selection_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.load()
        view._open_icon_picker(journal, "Assets:Bank")
        picker = session.page._dialogs.controls[-1]
        assert isinstance(picker, IconPicker)
        picker.selected = asset("Draft")
        external = store_at(tmp_path)
        await external.assign("Assets:Bank", asset("External"), ())
        await session.dispatch_event(picker.search._i, "focus", {})
        await press(session, "Enter")
        assert picker.open and picker.selected.label == "Draft"
        assert "reopen" in (picker.status.value or "")
        assert view.icons.assets["Assets:Bank"].label == "External"

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_entity_icons_reach_lists_settings_transactions_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.language = language
        view.icons = store_at(tmp_path)
        await view.icons.assign("Assets:Bank", asset("Bank"), ())
        await view.icons.assign("Expenses:Food", asset("Food"), ())
        await journal.add_expense(
            "Assets:Bank", "Expenses:Food", "10", "", "2026-10-18", "Synthetic"
        )
        view._refresh()
        assert images(view.home_balances)
        assert images(view.account_list)
        assert images(view.category_list)
        assert len(images(view.history)) >= 2
        for context in (view.home_balances, view.account_list, view.category_list):
            assert all(image.width == image.height == 16 for image in images(context))
        assert [image.width for image in images(view.history)] == [25.92, 16]
        view.default_account.value = "Assets:Bank"
        view._render_default_account()
        assert isinstance(view.default_account.leading_icon, ft.Container)
        assert view.default_account.leading_icon.width == 24
        assert images(view.default_account.leading_icon)[0].width == 16
        assert any(
            isinstance(option.leading_icon, ft.Image) for option in view.default_account.options
        )
        report = period_report(
            journal.ledger,
            journal.ledger.transactions[-1].date,
            journal.ledger.transactions[-1].date,
        )
        section = view._report_category_section(
            "report_expense_categories",
            report.expense_categories,
            (),
            "UAH",
            report.currencies[0].expenses,
        )
        assert images(section)
        view._transaction_dialog(journal)
        dialog = session.page._dialogs.controls[-1]
        assert isinstance(dialog, ft.AlertDialog)
        form = transaction_form(dialog)
        account_option = next(
            option for option in form.account.options if option.key == "Assets:Bank"
        )
        category_option = next(
            option for option in form.category.options if option.key == "Expenses:Food"
        )
        assert isinstance(account_option.leading_icon, ft.Container)
        assert account_option.leading_icon.width == account_option.leading_icon.height == 24
        assert isinstance(account_option.leading_icon.content, ft.Image)
        assert (
            account_option.leading_icon.content.width
            == account_option.leading_icon.content.height
            == 16
        )
        assert category_content(category_option).padding == ft.Padding.only(left=0)
        assert images(category_content(category_option))
        assert all(
            image.width == image.height == 16 for image in images(category_content(category_option))
        )
        for option in (*form.category.options, *form.fee_category.options):
            for label in walk(category_content(option)):
                if isinstance(label, ft.Text):
                    assert label.size is None and label.weight is None
        form.account.value = "Assets:Bank"
        await form.account._trigger_event("select", None)
        assert isinstance(form.account.leading_icon, ft.Container)
        assert form.account.leading_icon.width == form.account.leading_icon.height == 24
        assert isinstance(form.account.leading_icon.content, ft.Image)
        form.category.value = "Expenses:Food"
        await form.category._trigger_event("select", None)
        assert isinstance(form.category.leading_icon, ft.Container)
        assert form.category.leading_icon.width == form.category.leading_icon.height == 24
        assert isinstance(form.category.leading_icon.content, ft.Image)

    asyncio.run(scenario())


@pytest.mark.parametrize("width", [390, 1366])
def test_budget_and_default_selected_icons_keep_shared_dropdown_proportions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view._apply_layout(width)
        view.icons = store_at(tmp_path)
        for identifier in ("Assets:Bank", "Expenses:Food"):
            await view.icons.assign(identifier, asset(identifier), ())
        await journal.save_budget(
            "Expenses:Food", date(2026, 10, 1), "monthly", "50", "UAH", journal.source
        )
        view._refresh()
        before = journal.source
        budget = journal.ledger.budgets[-1]
        view.budgets._form(budget)
        dialog = session.page._dialogs.controls[-1]
        assert isinstance(dialog, ft.AlertDialog) and isinstance(dialog.content, ft.Control)
        category = next(
            control
            for control in walk(dialog.content)
            if isinstance(control, ft.Dropdown) and control.label == view.tr("budget_category")
        )
        view.default_account.value = "Assets:Bank"
        view.default_fee.value = "Expenses:Food"
        view._render_default_account()
        view._render_fee_settings()
        for dropdown in (category, view.default_account, view.default_fee):
            icon = dropdown.leading_icon
            assert isinstance(icon, ft.Container)
            assert icon.width == icon.height == 24
            assert icon.alignment == ft.Alignment.CENTER
            assert isinstance(icon.content, ft.Image)
            assert icon.content.width == icon.content.height == 16
            if dropdown is not view.default_account:
                for option in dropdown.options:
                    for label in walk(category_content(option)):
                        if isinstance(label, ft.Text):
                            assert label.size is None and label.weight is None
            dropdown.value = ""
            view._entity_dropdown_selected(MagicMock(control=dropdown))
            assert dropdown.leading_icon is None
        category.value = "Expenses:Food"
        view._entity_dropdown_selected(MagicMock(control=category))
        assert isinstance(category.leading_icon, ft.Container)
        session.page.pop_dialog()
        view.budgets._delete(budget)
        deletion = session.page._dialogs.controls[-1]
        assert isinstance(deletion, ft.AlertDialog) and isinstance(deletion.content, ft.Control)
        assert [image.semantics_label for image in images(deletion.content)] == ["Expenses:Food"]
        assert journal.source == before

    asyncio.run(scenario())


@pytest.mark.parametrize("identifier", ["Expenses:Food", "Expenses:Food:Shops"])
def test_parent_category_selected_icons_match_account_dropdowns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identifier: str
) -> None:
    async def scenario() -> None:
        _session, view, journal = await finance_session(tmp_path, monkeypatch)
        await journal.create_category("Food:Shops", "Shops")
        view.icons = store_at(tmp_path)
        await view.icons.assign(identifier, asset(identifier), ())
        view.category_parent.value = identifier
        view._refresh_category_parent_options()
        icon = view.category_parent.leading_icon
        assert isinstance(icon, ft.Container)
        assert icon.width == icon.height == 24
        assert isinstance(icon.content, ft.Image)
        assert icon.content.width == icon.content.height == 16
        for option in view.category_parent.options[1:]:
            labels = [
                control
                for control in walk(category_content(option))
                if isinstance(control, ft.Text)
            ]
            assert labels and all(label.size is None and label.weight is None for label in labels)

    asyncio.run(scenario())


def test_budget_definition_tree_and_filters_use_entity_icons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.assign("Expenses:Food", asset(), ())
        budget = Budget(
            "synthetic",
            "Expenses:Food",
            journal.today().replace(day=1),
            "monthly",
            Decimal(100),
            "UAH",
        )
        assert images(view.budgets._definition(budget))
        view.budgets.category_id = "Expenses:Food"
        view.budgets.category_choices = {"Expenses:Food": "Food"}
        view.budgets._sync_filters()
        assert isinstance(view.budgets.scope.leading_icon, ft.Container)
        assert images(view.budgets.scope.leading_icon)
        category_option = next(
            option for option in view.budgets.scope.options if option.key == "Expenses:Food"
        )
        assert isinstance(category_option.content, ft.Control)
        assert images(category_option.content)
        assert isinstance(view.budgets.active_filters.controls[0], ft.Chip)
        assert isinstance(view.budgets.active_filters.controls[0].leading, ft.Image)
        picker = TreePicker(
            session.page,
            "en",
            "Synthetic",
            {"Expenses:Food": "Food"},
            "",
            "All",
            AsyncMock(),
            icon=view._entity_icon,
        )
        assert isinstance(picker.listing.controls[0], ft.Container)
        button = picker.listing.controls[0].content
        assert isinstance(button, ft.TextButton) and isinstance(button.icon, ft.Image)

    asyncio.run(scenario())


@pytest.mark.parametrize("vector", [True, False])
def test_icons_render_at_16_pixels_without_changing_stored_artwork(vector: bool) -> None:
    original = (
        asset()
        if vector
        else IconAsset(
            "Imported",
            normalize_image(raster())[0],
            False,
            "User-provided",
            "Unverified",
            "Synthetic",
        )
    )
    serialized = original.serialize()
    image = icon_image(original)
    assert image.width == image.height == 16
    assert image.src == original.data
    assert image.fit == ft.BoxFit.CONTAIN
    assert image.color == (ft.Colors.ON_SURFACE if vector else None)
    assert original.serialize() == serialized


def test_icon_read_failure_does_not_disable_financial_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        _session, view, journal = await finance_session(tmp_path, monkeypatch)
        view.icons = store_at(tmp_path)
        await view.icons.assign("Assets:Bank", asset(), ())
        monkeypatch.setattr(
            view.icons.files, "read_records", AsyncMock(side_effect=IconError("icon_storage"))
        )
        await view._load_icons()
        assert journal.ready
        assert view.icon_notice.visible
        assert view.icons.assets["Assets:Bank"] == asset()

    asyncio.run(scenario())


@pytest.mark.parametrize("removal", [False, True])
def test_busy_save_disables_escape_and_duplicate_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, removal: bool
) -> None:
    async def scenario() -> None:
        session, _view, _journal = await finance_session(tmp_path, monkeypatch)
        started, release = asyncio.Event(), asyncio.Event()

        async def persist(_asset: object) -> None:
            started.set()
            await release.wait()

        save = AsyncMock(side_effect=persist)
        dialog = (
            IconRemovalDialog(session.page, "en", save)
            if removal
            else IconPicker(session.page, "en", asset(), save)
        )
        show_dialog(session.page, dialog)
        task = asyncio.create_task(click_async(dialog.submit))
        try:
            async with asyncio.timeout(10):
                await started.wait()
                assert dialog.cancel.disabled and dialog.submit.disabled
                await press(session)
                assert dialog.open
                await click_async(dialog.submit)
                save.assert_awaited_once()
        finally:
            release.set()
            await task
        assert not dialog.open

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (StorageError("failed"), "icon_storage"),
        (TimeoutError(), "icon_storage"),
        (RuntimeError("failed"), "icon_failed"),
    ],
)
def test_icon_removal_reports_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    message: str,
) -> None:
    async def scenario() -> None:
        session, view, _journal = await finance_session(tmp_path, monkeypatch)
        view.language = "en"
        dialog = IconRemovalDialog(session.page, "en", AsyncMock(side_effect=failure))
        show_dialog(session.page, dialog)
        await click_async(dialog.submit)
        assert dialog.open and dialog.status.value == view.tr(message)
        assert not dialog.cancel.disabled and not dialog.submit.disabled

    asyncio.run(scenario())
