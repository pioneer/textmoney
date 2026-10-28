import asyncio
import json
import subprocess
import tomllib
from pathlib import Path

import pytest
from beancount import loader

import tasks
from app.domain.ledger import LedgerError
from app.domain.recovery import SaveRecord
from app.domain.storage import digest
from app.infrastructure.beancount_journal import JournalCodec
from app.infrastructure.desktop_storage import DesktopFolder
from app.storage import StorageError
from tools import migrate_txncount as migration

BOOK = (
    "; txncount-active in a comment must not change\n"
    '2026-01-01 open Assets:Cash UAH\n  name: "TxnCount wallet"\n'
    "  txncount-active: FALSE ; keep\n"
    '2026-01-01 open Expenses:Food\n  name: "Food"\n  txncount-active: TRUE\n'
    "2026-01-01 open Equity:Opening-Balances\n"
    '2026-01-01 * "Opening"\n  time: "09:00"\n'
    "  Assets:Cash 100 UAH\n  Equity:Opening-Balances -100 UAH\n"
    '2026-01-02 * "Shop" "Lunch txncount-active" #food ^txncount-shared\n'
    '  opaque: "txncount-active: FALSE"\n  time: "12:30"\n'
    '  txncount-kind: "expense"\n  txncount-fee-mode: "legacy-user-value"\n'
    "  Expenses:Food 10 UAH\n  Assets:Cash -10 UAH\n"
    '2026-01-03 * "Refund" ^txncount-shared\n'
    "  Expenses:Food -2 UAH\n  Assets:Cash 2 UAH\n"
    '2026-01-01 custom "budget" Expenses:Food "monthly" 50 UAH\n'
)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_migration_preserves_bytes_except_approved_visibility_tokens(newline: str) -> None:
    source = BOOK.replace("\n", newline)
    result = migration.migrate_source(source)
    assert result == source.replace("  txncount-active:", "  textmoney-active:")
    ledger = JournalCodec().parse(result)
    assert not ledger.accounts[0].active
    assert ledger.categories[0].active
    assert ledger.transactions[2].related_expense == ledger.transactions[1].id
    assert ledger.transactions[2].links == ("txncount-shared",)
    assert len(ledger.budgets) == 1
    old_entries, errors, _ = loader.load_string(source)
    new_entries, new_errors, _ = loader.load_string(result)
    assert not errors and not new_errors
    from beancount.core.data import Transaction

    assert [entry.postings for entry in old_entries if isinstance(entry, Transaction)] == [
        entry.postings for entry in new_entries if isinstance(entry, Transaction)
    ]


@pytest.mark.parametrize(
    "source",
    [
        BOOK.replace("txncount-active: FALSE", 'txncount-active: "FALSE"'),
        BOOK.replace("txncount-active: FALSE", "txncount-active: FALSE\n  textmoney-active: FALSE"),
        BOOK + '\ninclude "external.beancount"\n',
        "invalid financial payload",
    ],
)
def test_migration_rejects_invalid_or_ambiguous_source(source: str) -> None:
    with pytest.raises((migration.MigrationError, LedgerError, ValueError)):
        migration.migrate_source(source)


@pytest.fixture
def old_folder(tmp_path: Path) -> Path:
    source = tmp_path / "old"
    source.mkdir()
    (source / migration.OLD_FILE).write_bytes(BOOK.encode())
    return source


def test_copy_migration_never_changes_original_or_recovery_history(old_folder: Path) -> None:
    backups = old_folder / "backups"
    backups.mkdir()
    (backups / "txncount-legacy.beancount").write_bytes(b"opaque historical backup")
    target = old_folder.parent / "new"
    migration.migrate_journal(old_folder, target)
    assert (old_folder / migration.OLD_FILE).read_bytes() == BOOK.encode()
    assert (backups / "txncount-legacy.beancount").read_bytes() == b"opaque historical backup"
    assert sorted(p.name for p in target.iterdir()) == [migration.NEW_FILE]
    assert (target / migration.NEW_FILE).read_bytes() == migration.migrate_source(BOOK).encode()


def test_documented_invoke_commands_work_end_to_end(old_folder: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    target = old_folder.parent / "new"
    command = ["uv", "run", "inv"]
    result = subprocess.run(
        [*command, "migrate-txncount", "--source", str(old_folder), "--destination", str(target)],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Migration verified" in result.stdout
    settings = old_folder.parent / "old-preferences.json"
    settings.write_text('{"txncount.language.v1": "uk"}')
    destination = old_folder.parent / "config/preferences.json"
    result = subprocess.run(
        [
            *command,
            "migrate-txncount-preferences",
            "--source",
            str(settings),
            "--destination",
            str(destination),
            "--journal-folder",
            str(target),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Migration verified" in result.stdout
    assert json.loads(destination.read_text()) == {
        "textmoney.folder.v1": str(target.resolve()),
        "textmoney.language.v1": "uk",
    }


def legacy_record(source: Path, phase: str, pending: tuple[str, ...] = ()) -> None:
    record = SaveRecord(digest(BOOK.encode()), digest(BOOK.encode()) or "", None, phase, pending)
    # Construct legacy metadata without asking the new runtime to accept old backup names.
    value = json.loads(record.encode())
    value["backup"] = "backups/txncount-20260101T120000000000Z-" + "a" * 32 + "-pending.beancount"
    value.pop("checksum")
    value["checksum"] = digest(json.dumps(value, sort_keys=True).encode())
    (source / ".txncount.beancount.recovery.json").write_text(json.dumps(value))


@pytest.mark.parametrize("phase", ["complete", "resolved"])
def test_completed_legacy_recovery_is_archived_not_renamed(old_folder: Path, phase: str) -> None:
    legacy_record(old_folder, phase)
    marker = old_folder / ".txncount.beancount.recovery.json"
    original = marker.read_bytes()
    target = old_folder.parent / "new"
    migration.migrate_journal(old_folder, target)
    assert marker.read_bytes() == original
    assert not (target / ".textmoney.beancount.recovery.json").exists()


@pytest.mark.parametrize("kind", ["prepared", "damaged", "missing-with-pending"])
def test_unresolved_recovery_blocks_before_creating_output(old_folder: Path, kind: str) -> None:
    if kind == "prepared":
        legacy_record(old_folder, "prepared")
    elif kind == "damaged":
        (old_folder / ".txncount.beancount.recovery.json").write_text("corrupt")
    else:
        backups = old_folder / "backups"
        backups.mkdir()
        (
            backups / ("txncount-20260101T120000000000Z-" + "a" * 32 + "-pending.beancount")
        ).write_bytes(b"pending")
    target = old_folder.parent / "new"
    with pytest.raises(migration.MigrationError):
        migration.migrate_journal(old_folder, target)
    assert not target.exists()
    assert (old_folder / migration.OLD_FILE).read_bytes() == BOOK.encode()


def test_existing_destination_or_nested_output_is_refused(old_folder: Path) -> None:
    target = old_folder.parent / "new"
    target.mkdir()
    with pytest.raises(migration.MigrationError, match="must not exist"):
        migration.migrate_journal(old_folder, target)
    with pytest.raises(migration.MigrationError, match="outside"):
        migration.migrate_journal(old_folder, old_folder / "nested")


def test_partial_output_is_blocked_by_runtime(
    old_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = old_folder.parent / "new"
    write = migration._write_new

    def fail(path: Path, data: bytes) -> None:
        if path.name == migration.NEW_FILE:
            path.write_bytes(b"partial")
            raise OSError("synthetic write failure")
        write(path, data)

    monkeypatch.setattr(migration, "_write_new", fail)
    with pytest.raises(OSError):
        migration.migrate_journal(old_folder, target)
    assert (target / migration.INCOMPLETE).exists()
    with pytest.raises(StorageError, match="Incomplete migration"):
        asyncio.run(DesktopFolder(str(target), migration.NEW_FILE).read())
    assert (old_folder / migration.OLD_FILE).read_bytes() == BOOK.encode()


def test_source_changed_during_copy_leaves_blocked_output(
    old_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = old_folder.parent / "new"
    write = migration._write_new

    def changed(path: Path, data: bytes) -> None:
        write(path, data)
        if path.name == migration.NEW_FILE:
            (old_folder / migration.OLD_FILE).write_bytes(BOOK.encode() + b"; external\n")

    monkeypatch.setattr(migration, "_write_new", changed)
    with pytest.raises(migration.MigrationError, match="changed during"):
        migration.migrate_journal(old_folder, target)
    assert (target / migration.INCOMPLETE).exists()


def test_legacy_folder_cannot_silently_become_an_empty_new_journal(old_folder: Path) -> None:
    with pytest.raises(StorageError, match="Legacy TxnCount"):
        asyncio.run(DesktopFolder(str(old_folder), migration.NEW_FILE).read())


def test_source_and_destination_symlink_traversal_is_refused(old_folder: Path) -> None:
    linked = old_folder.parent / "linked"
    try:
        linked.symlink_to(old_folder, target_is_directory=True)
    except OSError:
        if migration.os.name != "nt":
            raise
        pytest.skip("This Windows account cannot create directory symlinks.")
    with pytest.raises(migration.MigrationError, match="symlinks"):
        migration.migrate_journal(linked, old_folder.parent / "new")
    with pytest.raises(migration.MigrationError, match="symlinks"):
        migration.migrate_journal(old_folder, linked / "new")


@pytest.mark.parametrize("language", ["en", "uk", "ru"])
def test_migration_failures_are_actionable_in_each_language(language: str) -> None:
    from app.localization import localized_error, translate

    assert localized_error(
        language, "Incomplete migration. Review the migration output before opening."
    ) == translate(language, "error_migration_incomplete")
    assert localized_error(
        language, "Legacy TxnCount journal found. Migrate a copy before opening in TextMoney."
    ) == translate(language, "error_migration_required")


def test_changed_destination_is_not_reported_as_success(
    old_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = old_folder.parent / "new"
    write = migration._write_new

    def changed(path: Path, data: bytes) -> None:
        write(path, data)
        if path.name == migration.NEW_FILE:
            path.write_bytes(data + b"; external destination edit\n")

    monkeypatch.setattr(migration, "_write_new", changed)
    with pytest.raises(migration.MigrationError, match="Destination changed"):
        migration.migrate_journal(old_folder, target)
    assert (target / migration.INCOMPLETE).exists()


def test_command_reports_failure_without_echoing_private_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / migration.OLD_FILE).write_text("private-invalid-journal-payload")
    target = tmp_path / "new"
    monkeypatch.setattr(
        migration.sys,
        "argv",
        ["migrate-txncount", "journal", "--source", str(source), "--destination", str(target)],
    )
    with pytest.raises(SystemExit) as failure:
        migration.main()
    assert failure.value.code == 1
    error = capsys.readouterr().err
    assert "Invalid legacy syntax" in error
    assert "private-invalid-journal-payload" not in error
    assert not target.exists()


def test_settings_are_explicitly_mapped_without_old_folder_or_diagnostic_grants(
    old_folder: Path,
) -> None:
    target = old_folder.parent / "new"
    migration.migrate_journal(old_folder, target)
    source = old_folder.parent / "old-preferences.json"
    original = json.dumps(
        {
            "txncount.folder.v1": "/old",
            "txncount.language.v1": "uk",
            "txncount.theme.v1": "dark",
            "txncount.timezone.v1": "Europe/Kyiv",
            "txncount.backup-cleanup.v1": "off",
            "txncount.default-fee-category.v1": "Expenses:Food",
            "txncount.diagnostics.folder.v1": "/synthetic",
            "unrelated": "preserve only in original",
        }
    )
    source.write_text(original)
    destination = old_folder.parent / "config/textmoney/preferences.json"
    migration.migrate_preferences(source, destination, target)
    values = json.loads(destination.read_text())
    assert len(values) == 6
    assert values["textmoney.folder.v1"] == str(target.resolve())
    assert values["textmoney.language.v1"] == "uk"
    assert not any(key.startswith("txncount.") for key in values)
    assert source.read_text() == original
    with pytest.raises(migration.MigrationError, match="must not exist"):
        migration.migrate_preferences(source, destination, target)


@pytest.mark.parametrize("value", ['{"txncount.language.v1": 3}', "{broken"])
def test_invalid_settings_are_not_copied(old_folder: Path, value: str) -> None:
    target = old_folder.parent / "new"
    migration.migrate_journal(old_folder, target)
    source = old_folder.parent / "settings.json"
    source.write_text(value)
    destination = old_folder.parent / "output.json"
    with pytest.raises((migration.MigrationError, ValueError)):
        migration.migrate_preferences(source, destination, target)
    assert not destination.exists()


def test_new_linux_development_id_fits_existing_binary_slot() -> None:
    assert len(tasks.LINUX_DEV_APP_ID) == len(tasks.STOCK_CLIENT_APP_ID)
    assert "textmoney" in tasks.LINUX_DEV_APP_ID


def test_android_bridge_and_python_agree_on_new_storage_identity() -> None:
    root = Path(__file__).resolve().parents[1]
    bridge = (
        root / "extensions/finance-saf/src/flutter/finance_saf/lib/src/extension.dart"
    ).read_text()
    assert "target != 'textmoney.beancount'" in bridge
    assert migration.INCOMPLETE in bridge
    assert "target != 'txncount.beancount'" not in bridge
    assert "Legacy TxnCount journal found" in bridge


def test_apk_smoke_mirrors_every_package_extracted_at_runtime() -> None:
    from tools.check_beancount_apk import PACKAGES

    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert set(project["tool"]["flet"]["android"]["extract_packages"]) == PACKAGES
