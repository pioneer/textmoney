import asyncio
import base64
import json
import shutil
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from form_controls import click_async, transaction_form
from test_dialog_keyboard import keyboard_session, press

from app.budget_order import POLICY as ORDER_POLICY
from app.budget_order import BudgetOrderStore
from app.domain.storage import ConflictError
from app.infrastructure.android_storage import AndroidFolder
from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.preferences import FeePreferences
from app.infrastructure.record_files import AndroidRecordFiles, DesktopRecordFiles
from app.journal import FILENAME
from app.shared_defaults import POLICY, SharedDefaults, SharedDefaultsError, _heads
from app.ui.finance import FinanceView

SOURCE = """option "operating_currency" "USD"
2000-01-01 open Assets:Cash USD
2000-01-01 open Assets:Bank USD
2000-01-01 open Expenses:Food
2000-01-01 open Expenses:Fees
2000-01-01 open Expenses:Fees:Bank
2000-01-01 open Expenses:Other
2000-01-01 open Income:Salary
"""
FEES = FeePreferences(("Expenses:Fees",), "Expenses:Fees:Bank")


def store_at(path: Path) -> SharedDefaults:
    return SharedDefaults(DesktopRecordFiles(DesktopFolder(str(path), FILENAME), POLICY))


def test_budget_order_is_shared_independently_of_default_settings(tmp_path: Path) -> None:
    async def scenario() -> None:
        folder = DesktopFolder(str(tmp_path), FILENAME)
        a = BudgetOrderStore(DesktopRecordFiles(folder, ORDER_POLICY))
        b = BudgetOrderStore(DesktopRecordFiles(folder, ORDER_POLICY))
        order = ("Expenses:Home", "Expenses:Food")
        await a.save("budget_order", order, ())
        await b.load()
        assert b.values["budget_order"] == order
        heads = b.heads["budget_order"]
        await a.save("budget_order", tuple(reversed(order)), heads)
        with pytest.raises(ConflictError):
            await b.save("budget_order", (), heads)
        await b.save("budget_order", (), b.heads["budget_order"])
        await a.load()
        assert a.values["budget_order"] == ()
        assert not (tmp_path / FILENAME).exists()
        await store_at(tmp_path).load()
        assert not (tmp_path / "textmoney-settings").exists()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "value",
    [None, "Expenses:Food", ["Expenses:Food", "Expenses:Food"], ["not-an-account"], [7], [{}]],
)
def test_invalid_budget_order_records_are_refused(tmp_path: Path, value: object) -> None:
    async def scenario() -> None:
        directory = tmp_path / "textmoney-budget-order"
        directory.mkdir()
        name, data = record({"budget_order": {"parents": [], "value": value}})
        (directory / name).write_bytes(data)
        store = BudgetOrderStore(
            DesktopRecordFiles(DesktopFolder(str(tmp_path), FILENAME), ORDER_POLICY)
        )
        with pytest.raises(SharedDefaultsError, match="shared_settings_invalid"):
            await store.load()

    asyncio.run(scenario())


def record(changes: object, *, version: object = 1) -> tuple[str, bytes]:
    data = json.dumps({"version": version, "changes": changes}, sort_keys=True).encode()
    return sha256(data).hexdigest() + ".json", data


def test_sequential_devices_groups_and_explicit_clear(tmp_path: Path) -> None:
    async def scenario() -> None:
        a, b = store_at(tmp_path), store_at(tmp_path)
        await a.load()
        await b.load()
        assert not (tmp_path / "textmoney-settings").exists()
        await a.save("account", "Assets:Cash", ())
        account_head = a.heads["account"]
        await b.save("commission", FEES, ())
        assert b.values == {"account": "Assets:Cash", "commission": FEES}
        await a.load()
        await a.save("account", None, account_head)
        await b.load()
        assert b.values["account"] is None and b.values["commission"] == FEES
        assert len(list((tmp_path / "textmoney-settings").glob("*.json"))) == 3
        assert not (tmp_path / FILENAME).exists()
        assert not (tmp_path / "backups").exists()

    asyncio.run(scenario())


def test_preexisting_conflict_is_explicit_and_resolution_references_all_heads(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        directory = tmp_path / "textmoney-settings"
        directory.mkdir()
        for account in ("Assets:Cash", "Assets:Bank"):
            name, data = record({"account": {"parents": [], "value": account}})
            (directory / name).write_bytes(data)
        store = store_at(tmp_path)
        await store.load()
        assert "account" not in store.values
        assert set(store.conflicts["account"]) == {"Assets:Cash", "Assets:Bank"}
        heads = store.heads["account"]
        await store.save("account", "Assets:Bank", heads)
        assert not store.conflicts and store.values["account"] == "Assets:Bank"
        saved = json.loads(store.records[store.heads["account"][0]])
        assert tuple(saved["changes"]["account"]["parents"]) == heads
        with pytest.raises(ConflictError, match="shared_settings_stale"):
            await store.save("account", "Assets:Cash", heads)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes,version",
    [
        ({}, 1),
        ({"unknown": {"parents": [], "value": None}}, 1),
        ({"account": {"parents": [], "value": "Expenses:Food"}}, 1),
        ({"account": {"parents": [], "value": ""}}, 1),
        ({"account": {"parents": ["invalid"], "value": None}}, 1),
        ({"commission": {"parents": [], "value": {"branches": [], "default": "Expenses:Fees"}}}, 1),
        ({"account": {"parents": [], "value": None}}, True),
        ({"account": {"parents": [], "value": None}}, 2),
    ],
)
def test_invalid_records_are_visible_errors(
    tmp_path: Path, changes: object, version: object
) -> None:
    async def scenario() -> None:
        directory = tmp_path / "textmoney-settings"
        directory.mkdir()
        name, data = record(changes, version=version)
        (directory / name).write_bytes(data)
        with pytest.raises(SharedDefaultsError, match="shared_settings_invalid"):
            await store_at(tmp_path).load()

    asyncio.run(scenario())


def test_incomplete_history_retains_loaded_values_and_recovers(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = store_at(tmp_path)
        await store.save("account", "Assets:Cash", ())
        parent = store.heads["account"][0]
        original = store.records[parent]
        await store.save("account", "Assets:Bank", (parent,))
        path = tmp_path / "textmoney-settings" / parent
        path.unlink()
        with pytest.raises(SharedDefaultsError, match="shared_settings_incomplete"):
            await store.load()
        assert store.values["account"] == "Assets:Bank"
        path.write_bytes(original)
        await store.load()

    asyncio.run(scenario())


def test_cycle_validation() -> None:
    with pytest.raises(SharedDefaultsError, match="shared_settings_invalid"):
        _heads({"a": (("b",), None), "b": (("a",), None)})


def test_separate_devices_receive_complete_history_without_device_preferences(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        a, b = tmp_path / "desktop", tmp_path / "android-copy"
        a.mkdir()
        b.mkdir()
        store = store_at(a)
        await store.save("account", "Assets:Cash", ())
        await store.save("commission", FEES, ())
        shutil.copytree(a / "textmoney-settings", b / "textmoney-settings")
        other = store_at(b)
        await other.load()
        assert other.values == {"account": "Assets:Cash", "commission": FEES}
        await other.save("account", None, other.heads["account"])
        shutil.copytree(b / "textmoney-settings", a / "textmoney-settings", dirs_exist_ok=True)
        await store.load()
        assert store.values["account"] is None and store.values["commission"] == FEES

    asyncio.run(scenario())


def test_idempotent_saves_and_read_limits(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = store_at(tmp_path)
        await store.save("account", None, ())
        heads = store.heads["account"]
        await store.save("account", None, heads)
        assert store.heads["account"] == heads
        for policy in (
            replace(POLICY, max_records=0),
            replace(POLICY, max_record=1),
            replace(POLICY, max_total=1),
        ):
            files = DesktopRecordFiles(DesktopFolder(str(tmp_path), FILENAME), policy)
            with pytest.raises(SharedDefaultsError, match="shared_settings_(invalid|limit)"):
                await files.read_records()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", ["permission", "no_write", "partial", "conflict_copy", "symlink"]
)
def test_save_failures_preserve_previous_values(tmp_path: Path, failure: str) -> None:
    async def scenario() -> None:
        store = store_at(tmp_path)
        await store.save("account", "Assets:Cash", ())
        before = dict(store.records)
        if failure == "permission":
            store.files.create_record = AsyncMock(side_effect=OSError("Synthetic"))
        elif failure == "no_write":
            store.files.create_record = AsyncMock()
        elif failure == "partial":

            async def partial(name: str, data: bytes) -> None:
                (tmp_path / "textmoney-settings" / name).write_bytes(data[:12])

            store.files.create_record = partial
        elif failure == "conflict_copy":
            (tmp_path / "textmoney-settings" / "conflicted.json").write_text("{}")
        else:
            (tmp_path / "textmoney-settings" / ("a" * 64 + ".json")).symlink_to(
                tmp_path / "missing"
            )
        with pytest.raises(SharedDefaultsError):
            await store.save("account", "Assets:Bank", store.heads["account"])
        assert store.values["account"] == "Assets:Cash"
        for name, data in before.items():
            assert (tmp_path / "textmoney-settings" / name).read_bytes() == data

    asyncio.run(scenario())


@pytest.mark.parametrize("budget_order", [False, True])
def test_android_uses_existing_grant_and_settings_namespace(budget_order: bool) -> None:
    async def scenario() -> None:
        records: dict[str, bytes] = {}
        namespace = "budget_order" if budget_order else "settings"

        async def call(method: str, **args: object) -> dict[str, object]:
            if method == f"{namespace}_read":
                return {
                    "ok": True,
                    "value": {n: base64.b64encode(d).decode() for n, d in records.items()},
                }
            assert method == f"{namespace}_create"
            assert args["uri"] == "content://synthetic"
            assert args["filename"] == FILENAME
            name, data = args["name"], args["data"]
            assert isinstance(name, str) and isinstance(data, str)
            records[name] = base64.b64decode(data)
            return {"ok": True, "value": True}

        bridge = MagicMock(call=AsyncMock(side_effect=call))
        folder = AndroidFolder(bridge, "content://synthetic", FILENAME)
        if budget_order:
            store = BudgetOrderStore(AndroidRecordFiles(folder, ORDER_POLICY))
            await store.save("budget_order", ("Expenses:Food", "Expenses:Home"), ())
            assert store.values == {"budget_order": ("Expenses:Food", "Expenses:Home")}
        else:
            store = SharedDefaults(AndroidRecordFiles(folder, POLICY))
            await store.save("account", "Assets:Bank", ())
            await store.save("commission", FEES, ())
            assert store.values == {"account": "Assets:Bank", "commission": FEES}

    asyncio.run(scenario())


async def view_at(path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FinanceView, MagicMock]:
    page = MagicMock(spec=ft.Page)
    page.width, page.platform = 390, ft.PagePlatform.LINUX
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    await view._attach(str(path))
    view._refresh()
    return view, page


def test_shared_reload_preserves_drafts_and_explicit_review_allows_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        (tmp_path / FILENAME).write_text(SOURCE)
        view, page = await view_at(tmp_path, monkeypatch)
        view.default_account.value = "Assets:Cash"
        await view._save_default_account(MagicMock())
        view._new_transaction(MagicMock())
        form = transaction_form(page.show_dialog.call_args.args[0])
        form.amount.value = "17."
        view.default_account.value = "Assets:Bank"
        external = store_at(tmp_path)
        await external.load()
        await external.save("account", None, external.heads["account"])
        await external.save("commission", FEES, ())
        view.next_icon_poll = 0
        await view._poll_journal()
        assert view.default_account_id is None
        assert view.default_account.value == "Assets:Bank"
        assert form.account.value == "Assets:Cash" and form.amount.value == "17."
        form.choose("income")
        assert form.fee_category.value == "Expenses:Bank-Fees"
        assert view.fee_preferences == FEES
        assert view.shared_review.visible
        await view._save_default_account(MagicMock())
        assert view.error_message == "shared_settings_stale"
        await view._review_shared_defaults(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        button = dialog.actions[-1]
        if isinstance(button, ft.KeyboardListener):
            button = button.content
        assert isinstance(button, ft.TextButton)
        await click_async(button)
        await view._save_default_account(MagicMock())
        assert view.default_account_id == "Assets:Bank"
        await external.load()
        assert external.values["account"] == "Assets:Bank"
        assert (tmp_path / FILENAME).read_text() == SOURCE

    asyncio.run(scenario())


def test_missing_references_conflicts_and_corruption_do_not_disable_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        (tmp_path / FILENAME).write_text(SOURCE)
        store = store_at(tmp_path)
        await store.save("account", "Assets:Missing", ())
        await store.save("commission", FeePreferences(("Expenses",), "Expenses:Missing"), ())
        view, page = await view_at(tmp_path, monkeypatch)
        assert view.journal and view.journal.ready
        assert view.default_account_notice.visible and view.default_fee_notice.visible
        assert view.default_fee.value == "Expenses:Missing"
        view._new_transaction(MagicMock())
        form = transaction_form(page.show_dialog.call_args.args[0])
        assert form.account.value is None and form.fee_category.value is None
        name, data = record({"commission": {"parents": [], "value": json.loads(FEES.serialize())}})
        directory = tmp_path / "textmoney-settings"
        (directory / name).write_bytes(data)
        await view._load_shared_defaults()
        view._refresh()
        assert view.default_fee.value is None and view.shared_notice.visible
        assert "Expenses:Missing" in (view.shared_details.value or "")
        view.default_fee.value = FEES.default
        view.fee_branches_draft = FEES.branches
        await view._save_default_fee(MagicMock())
        assert view.fee_preferences == FEES
        (directory / "broken.json").write_text("bad")
        await view._load_shared_defaults()
        view._refresh()
        assert view.journal.ready and view.shared_notice.visible
        assert view.shared_defaults_error == "shared_settings_invalid"
        view.default_account.value = "Assets:Cash"
        await view._save_default_account(MagicMock())
        assert view.default_account.value == "Assets:Cash" and view.journal.ready

    asyncio.run(scenario())


def test_folder_defaults_do_not_leak_and_legacy_choices_require_explicit_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        for folder in ("a", "b"):
            (tmp_path / folder).mkdir()
            (tmp_path / folder / FILENAME).write_text(SOURCE)
        view, _ = await view_at(tmp_path / "a", monkeypatch)
        view.legacy_default_account, view.legacy_fee_preferences = "Assets:Cash", FEES
        await view._attach(str(tmp_path / "a"))
        view._refresh()
        assert view.default_account.value == "Assets:Cash" and view.default_account_id is None
        assert view.default_fee.value == FEES.default and view.fee_preferences == FeePreferences()
        assert not (tmp_path / "a" / "textmoney-settings").exists()
        await view._save_default_account(MagicMock())
        await view._save_default_fee(MagicMock())
        await view._attach(str(tmp_path / "b"))
        view._refresh()
        assert view.default_account_id is None and view.fee_preferences == FeePreferences()
        assert not (tmp_path / "b" / "textmoney-settings").exists()
        await view._attach(str(tmp_path / "a"))
        view._refresh()
        assert view.default_account_id == "Assets:Cash" and view.fee_preferences == FEES

    asyncio.run(scenario())


@pytest.mark.parametrize("platform", [ft.PagePlatform.LINUX, ft.PagePlatform.WINDOWS])
@pytest.mark.parametrize("key", ["Escape", "Enter"])
def test_review_keyboard_acknowledges_versions_without_publishing_or_changing_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: ft.PagePlatform, key: str
) -> None:
    async def scenario() -> None:
        (tmp_path / FILENAME).write_text(SOURCE)
        session = keyboard_session(platform)
        session.page.width = 390
        monkeypatch.setattr(FinanceView, "page", property(lambda _: session.page))
        view = FinanceView()
        await view._attach(str(tmp_path))
        view.default_account.value = "Assets:Bank"
        external = store_at(tmp_path)
        await external.save("account", "Assets:Cash", ())
        await view._load_shared_defaults()
        before = dict(external.records)
        assert view.default_account_heads == ()
        await view._review_shared_defaults(MagicMock())
        await press(session, key)
        assert view.default_account.value == "Assets:Bank"
        assert view.default_account_heads == (external.heads["account"] if key == "Enter" else ())
        await external.load()
        assert external.records == before and (tmp_path / FILENAME).read_text() == SOURCE

    asyncio.run(scenario())


def test_change_during_review_refuses_stale_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        (tmp_path / FILENAME).write_text(SOURCE)
        view, page = await view_at(tmp_path, monkeypatch)
        view.default_account.value = "Assets:Bank"
        store = store_at(tmp_path)
        await store.save("account", "Assets:Cash", ())
        await view._load_shared_defaults()
        await view._review_shared_defaults(MagicMock())
        dialog = page.show_dialog.call_args.args[0]
        await store.save("account", None, store.heads["account"])
        button = dialog.actions[-1]
        if isinstance(button, ft.KeyboardListener):
            button = button.content
        assert isinstance(button, ft.TextButton)
        await click_async(button)
        assert view.error_message == "shared_settings_stale"
        assert view.default_account_heads == ()
        assert view.default_account.value == "Assets:Bank" and view.journal and view.journal.ready

    asyncio.run(scenario())
