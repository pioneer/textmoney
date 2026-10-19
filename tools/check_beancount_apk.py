"""Inspect and optionally execute the exact APK parser payload on host CPython."""

import io
import json
import os
import subprocess
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {"autobean_refactor", "beancount_parser", "lark"}
MVP_SMOKE = """
import asyncio
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import reset_tzpath
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.timekeeping import wall_timestamp

reset_tzpath(())  # Force the bundled tzdata fallback, not the host's timezone database.
assert wall_timestamp(date(2026, 1, 15), "12:00", "Europe/Kyiv").endswith("+02:00")
assert wall_timestamp(date(2026, 7, 15), "12:00", "Europe/Kyiv").endswith("+03:00")
print("Timezone APK Python payload: named zones and DST PASS")

async def smoke():
    directory = Path("mvp-smoke")
    directory.mkdir()
    journal = Journal(DesktopFolder(str(directory.resolve()), FILENAME))
    await journal.load()
    await journal.create_account("Cash", "Synthetic", "UAH", "100")
    await journal.create_category("Food:Groceries", "Synthetic groceries")
    backup = await journal.add_expense(
        "Assets:Cash", "Expenses:Food:Groceries", "12.34", "Synthetic", date.today().isoformat()
    )
    assert backup and (directory / backup).is_file()
    await journal.load()
    assert journal.ledger.balances()["Assets:Cash"] == Decimal("87.66")
    print("MVP APK Python payload: account/category/expense/backup/reload PASS")
    expense = next(t for t in journal.ledger.transactions if t.kind == "expense")
    opening_id = next(t.id for t in journal.ledger.transactions if t.kind == "opening")
    await journal.edit_expense(
        expense.id, "Assets:Cash", "Expenses:Food:Groceries", "7", "",
        date.today().isoformat(), expense.timestamp, "Synthetic\\nsecond line", journal.source,
    )
    await journal.load()
    assert any(t.id == expense.id and not t.payee and t.narration == "Synthetic\\nsecond line"
               for t in journal.ledger.transactions)
    await journal.create_account("Eur", "Euro wallet", "EUR")
    assert journal.ledger.operating_currencies == ("UAH", "EUR")
    print("Simplified expense APK payload: blank payee/multiline/display currency auto-add PASS")
    await journal.edit_account("Assets:Cash", "Renamed", "120", False, journal.source)
    await journal.edit_category("Expenses:Food:Groceries", "Renamed", False, journal.source)
    await journal.set_operating_currencies("UAH USD EUR", journal.source)
    await journal.delete_expense(expense.id, journal.source)
    await journal.create_account("Unused", "Unused", "USD")
    await journal.delete_account("Assets:Unused", journal.source)
    await journal.create_category("Unused", "Unused")
    await journal.delete_category("Expenses:Unused", journal.source)
    await journal.load()
    assert journal.ledger.balances()["Assets:Cash"] == Decimal("120")
    assert journal.ledger.transactions[0].id == opening_id
    assert journal.ledger.operating_currencies == ("UAH", "USD", "EUR")
    print("CRUD APK Python payload: edits/deletion/lifecycle/operating currencies PASS")
    await journal.edit_account("Assets:Cash", "Renamed", "120", True, journal.source)
    await journal.edit_category("Expenses:Food:Groceries", "Renamed", True, journal.source)
    await journal.create_category("Salary", "Salary", root="Income")
    for kind, category, amount in (
        ("income", "Income:Salary", "10"),
        ("reimbursement", "Expenses:Food:Groceries", "2"),
        ("adjustment", "Equity:Balance-Adjustments", "3"),
    ):
        await journal.add_incoming(
            "Assets:Cash", category, amount, "", date.today().isoformat(), kind
        )
    assert journal.ledger.balances()["Assets:Cash"] == Decimal("135")
    totals = journal.ledger.period_totals(date(1970, 1, 1), date.today())
    assert totals == {"income": {"UAH": Decimal("10")}, "expenses": {"UAH": Decimal("-2")}}
    receipt = journal.ledger.transactions[-1]
    await journal.edit_transaction(
        receipt.id, "Assets:Cash", "Equity:Balance-Adjustments", "4", "",
        receipt.date.isoformat(), receipt.timestamp, "Adjusted", journal.source
    )
    await journal.delete_transaction(receipt.id, journal.source)
    assert journal.ledger.balances()["Assets:Cash"] == Decimal("132")
    print("Incoming APK Python payload: income/reimbursement/equity/totals/edit/delete PASS")
    await journal.create_account("Loan", "Loan", "UAH", "100", root="Liabilities")
    await journal.save_transfer(
        "Assets:Cash", "Liabilities:Loan", "20", "2", "Expenses:Bank-Fees",
        "added", date.today().isoformat(), datetime.now().astimezone().isoformat(),
        "Repayment", "", journal.source
    )
    transfer = journal.ledger.transactions[-1]
    assert len(transfer.postings) == 3
    assert journal.ledger.balances()["Liabilities:Loan"] == Decimal("-80")
    assert journal.ledger.balances()["Assets:Cash"] == Decimal("110")
    await journal.save_transfer(
        "Assets:Cash", "Liabilities:Loan", "200", "5", "Expenses:Bank-Fees",
        "deducted", transfer.date.isoformat(), transfer.timestamp, "Edited", "",
        journal.source, identifier=transfer.id
    )
    assert journal.ledger.balances()["Assets:Cash"] == Decimal("-68")
    assert journal.ledger.balance_warnings() == {"Assets:Cash"}
    await journal.load()
    await journal.delete_transaction(transfer.id, journal.source)
    assert journal.ledger.balances()["Assets:Cash"] == Decimal("132")
    print("Transfer APK Python payload: fees/liabilities/overdraft/edit/delete PASS")

asyncio.run(smoke())
"""


def main() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    build = project["tool"]["flet"]["build_number"]
    with ZipFile(ROOT / f"build/apk/txncount-{version}.apk") as apk:
        app_bytes = apk.read("assets/app.zip")
        site_bytes = apk.read("assets/sitepackages.zip")
        extract_bytes = apk.read("assets/extract.zip")
    with (
        ZipFile(io.BytesIO(app_bytes)) as app,
        ZipFile(io.BytesIO(site_bytes)) as site,
        ZipFile(io.BytesIO(extract_bytes)) as extracted,
    ):
        assert len([n for n in app.namelist() if n.endswith(".beancount")]) == 6
        assert "app/beancount_diagnostics.pyc" in app.namelist()
        assert not any("beancount_poc" in name for name in app.namelist())
        assert json.loads(app.read("app/.release.json")) == {
            "version": version,
            "build_number": build,
        }
        for module in (
            "domain/ledger",
            "infrastructure/beancount_journal",
            "infrastructure/preferences",
            "journal",
            "ui/finance",
            "ui/datetime_fields",
            "timekeeping",
        ):
            assert f"app/{module}.pyc" in app.namelist()
        assert "autobean_refactor/beancount.lark" in extracted.namelist()
        assert "beancount_parser/grammar/beancount.lark" in extracted.namelist()
        for package in PACKAGES:
            assert any(n.startswith(f"{package}/") for n in extracted.namelist())
            assert not any(n.startswith(f"{package}/") for n in site.namelist())
        assert not any(n.startswith("beancount/") for n in site.namelist())
        assert not any(n.startswith("fava/") for n in site.namelist())
        assert "tzdata/zoneinfo/Europe/Kyiv" in site.namelist()
        print(
            "APK: 6 fixtures; parser bytecode; extracted grammar packages; "
            "no official native engine."
        )

        # Flet already provides host CPython to compile the Android payload.
        python = (
            ROOT
            / "build/flutter/build/build_python_3.14.7/python/bin"
            / ("python.exe" if os.name == "nt" else "python3")
        )
        if not python.is_file():
            raise FileNotFoundError(
                "Flet host CPython 3.14.7 not found; payload execution unverified."
            )
        with TemporaryDirectory(prefix="txncount-beancount-apk-") as temporary:
            directory = Path(temporary)
            (directory / "app.zip").write_bytes(app_bytes)
            (directory / "sitepackages.zip").write_bytes(site_bytes)
            # Only this project's own generated parser package files are extracted.
            for name in extracted.namelist():
                if name.split("/", 1)[0] in PACKAGES and not name.endswith("/"):
                    target = directory / "extracted" / name
                    if not target.resolve().is_relative_to(directory.resolve()):
                        raise ValueError("Unsafe APK archive path.")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(extracted.read(name))
            paths = [directory / "extracted", directory / "app.zip", directory / "sitepackages.zip"]
            subprocess.run(
                [str(python), "-s", "-m", "app.beancount_diagnostics"],
                env=os.environ | {"PYTHONPATH": os.pathsep.join(map(str, paths))},
                cwd=temporary,
                check=True,
            )
            subprocess.run(
                [
                    str(python),
                    "-s",
                    "-c",
                    "from app.version import release_identity\n"
                    f"assert release_identity() == {(version, build)!r}\n" + MVP_SMOKE,
                ],
                env=os.environ | {"PYTHONPATH": os.pathsep.join(map(str, paths))},
                cwd=temporary,
                check=True,
            )
        print("Host CPython payload smoke test passed. This is NOT an Android runtime test.")


if __name__ == "__main__":
    main()
