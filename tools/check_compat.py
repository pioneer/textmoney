"""Run exact synthetic Beancount compatibility checks against available engines."""

import argparse
import csv
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from beancount import loader
from beancount.core import data
from beancount.ops import summarize

from app.domain.ledger import Ledger, LedgerError
from app.infrastructure.beancount_journal import JournalCodec

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/compatibility.beancount"
QUERY = "SELECT date, account, currency, number"
EXPECTED_BALANCES = {
    ("Assets:Bank:UAH", "UAH"): Decimal("1382.50"),
    ("Assets:Bank:USD", "USD"): Decimal("219.50"),
    ("Assets:Cash", "UAH"): Decimal("55"),
    ("Equity:Balance-Adjustments", "UAH"): Decimal("-5"),
    ("Equity:Opening-Balances", "UAH"): Decimal("-1000"),
    ("Equity:Opening-Balances", "USD"): Decimal("-200"),
    ("Expenses:Bank-Fees", "UAH"): Decimal("1.25"),
    ("Expenses:Bank-Fees", "USD"): Decimal("1"),
    ("Expenses:Food:Groceries", "UAH"): Decimal("80"),
    ("Expenses:Inactive", "UAH"): Decimal("3"),
    ("Income:Salary", "UAH"): Decimal("-500"),
    ("Liabilities:Loan", "UAH"): Decimal("-90"),
}
PostingRow = tuple[str, str, str, Decimal]
Balance = dict[tuple[str, str], Decimal]


class CompatibilityFailure(Exception):
    """A tool ran but disagreed with the synthetic compatibility baseline."""


def executable(name: str) -> str | None:
    candidate = Path(sys.executable).with_name(name)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return shutil.which(name)


def run(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CompatibilityFailure(f"{args[0]} could not complete: {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CompatibilityFailure(f"{args[0]} exited {result.returncode}: {detail}")
    return result


def official_postings(entries: list[data.Directive]) -> Counter[PostingRow]:
    rows: Counter[PostingRow] = Counter()
    for entry in entries:
        if not isinstance(entry, data.Transaction):
            continue
        for posting in entry.postings:
            units = posting.units
            if units is None or units.number is None:
                raise CompatibilityFailure("Official Beancount left a fixture posting inferred.")
            rows[
                (
                    entry.date.isoformat(),
                    str(posting.account),
                    units.currency,
                    units.number,
                )
            ] += 1
    return rows


def app_postings(source: str) -> tuple[Counter[PostingRow], Ledger, Balance]:
    ledger = JournalCodec(today=lambda: date(2026, 12, 31)).parse(source)
    rows: Counter[PostingRow] = Counter()
    for transaction in ledger.transactions:
        for posting in transaction.postings:
            rows[
                (
                    transaction.date.isoformat(),
                    posting.account,
                    posting.currency,
                    posting.amount,
                )
            ] += 1
    return rows, ledger, posting_balances(rows)


def posting_balances(rows: Counter[PostingRow]) -> Balance:
    amounts: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for _, account, currency, number in rows.elements():
        amounts[account, currency] += number
    return {key: value for key, value in amounts.items() if value}


def official_balances(entries: list[data.Directive]) -> Balance:
    balances, _ = summarize.balance_by_account(entries)
    result: Balance = {}
    for account, inventory in balances.items():
        for position in inventory:
            units = position.units
            if units is None or units.number is None:
                raise CompatibilityFailure("Official Beancount returned an empty balance position.")
            result[account, units.currency] = units.number
    return result


def bean_query_postings(path: Path) -> Counter[PostingRow]:
    command = executable("bean-query")
    if command is None:
        raise FileNotFoundError("bean-query")
    output = run([command, "--format", "csv", str(path), QUERY]).stdout
    reader = csv.DictReader(output.splitlines())
    required = {"date", "account", "currency", "number"}
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise CompatibilityFailure("Beanquery returned an unexpected CSV column set.")
    rows: Counter[PostingRow] = Counter()
    for row in reader:
        rows[
            (
                row["date"].strip(),
                row["account"].strip(),
                row["currency"].strip(),
                Decimal(row["number"].strip()),
            )
        ] += 1
    return rows


def rustledger_postings(path: Path, command: str) -> Counter[PostingRow]:
    result = run([command, "query", "--format", "json", "--no-cache", str(path), QUERY])
    payload = json.loads(result.stdout)
    columns = payload.get("columns")
    rows = payload.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise CompatibilityFailure("RustLedger returned an unexpected JSON query shape.")
    indexes = {column: index for index, column in enumerate(columns)}
    if any(column not in indexes for column in ("date", "account", "currency", "number")):
        raise CompatibilityFailure("RustLedger omitted a required query column.")
    postings: Counter[PostingRow] = Counter()
    for row in rows:
        if row == columns:
            continue
        postings[
            (
                str(row[indexes["date"]]),
                str(row[indexes["account"]]),
                str(row[indexes["currency"]]),
                Decimal(str(row[indexes["number"]])),
            )
        ] += 1
    return postings


def fava_report_totals(path: Path) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    from fava.core import FavaLedger
    from fava.util.date import Year

    ledger = FavaLedger(str(path), poll_watcher=False)
    if ledger.errors:
        messages = "; ".join(getattr(error, "message", str(error)) for error in ledger.errors)
        raise CompatibilityFailure(f"Fava reported errors: {messages}")
    filtered = ledger.get_filtered(time="2026")
    result: list[dict[str, Decimal]] = []
    for root in ("Expenses", "Income"):
        trees, intervals = ledger.interval_balances(filtered, Year, root)
        if len(trees) != 1 or len(intervals) != 1:
            raise CompatibilityFailure(f"Fava returned an unexpected {root} interval count.")
        node = trees[0][root]
        result.append(
            {
                currency: amount
                for (currency, cost), amount in node.balance_children.items()
                if cost is None
            }
        )
    return result[0], {currency: -amount for currency, amount in result[1].items()}


def find_rustfava(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise CompatibilityFailure(
                "RUSTFAVA_APPIMAGE must identify an executable AppImage file."
            )
        return path.resolve()
    candidates: list[Path] = []
    for directory in (Path.home() / "Applications", Path.home() / "AppImages"):
        if directory.is_dir():
            candidates.extend(
                path
                for path in directory.iterdir()
                if "rustfava" in path.name.lower() and path.suffix.lower() == ".appimage"
            )
    return next(
        (path.resolve() for path in candidates if path.is_file() and os.access(path, os.X_OK)),
        None,
    )


def check_rustfava(explicit: str | None) -> bool:
    appimage = find_rustfava(explicit)
    if appimage is None:
        print(
            "SKIPPED rustfava AppImage: not found in RUSTFAVA_APPIMAGE, "
            "~/Applications or ~/AppImages"
        )
        print("NOT TESTED rustfava GUI: native GUI automation is not available in this check.")
        return False
    result = run([str(appimage), "--appimage-version"], timeout=10)
    print(f"PASS rustfava AppImage discovered: {appimage}")
    runtime = result.stdout.strip() or result.stderr.strip()
    print(f"INFO AppImage runtime: {runtime or 'version flag returned no text'}")
    print("NOT TESTED rustfava GUI: AppImage metadata is not a journal-loading test.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail if Beanquery, Fava, RustLedger, or the rustfava AppImage is unavailable",
    )
    parser.add_argument("--rustfava-appimage", help="explicit rustfava AppImage path")
    args = parser.parse_args()

    source = FIXTURE.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="textmoney-check-compat-") as directory:
        path = Path(directory) / FIXTURE.name
        path.write_text(source, encoding="utf-8", newline="")

        official_version = importlib.metadata.version("beancount")
        if not official_version.startswith("3."):
            raise CompatibilityFailure(f"Expected official Beancount 3; found {official_version}.")
        entries, errors, _ = loader.load_file(str(path))
        if errors:
            raise CompatibilityFailure(
                "Official Beancount rejected the synthetic fixture: "
                + "; ".join(error.message for error in errors)
            )
        official_rows = official_postings(entries)
        official = official_balances(entries)
        app_rows, ledger, app = app_postings(source)
        if app_rows != official_rows or app != official or app != EXPECTED_BALANCES:
            raise CompatibilityFailure(
                "TextMoney and official Beancount disagree on explicit posting "
                "or currency balances."
            )
        totals = ledger.period_totals(date(2026, 1, 1), date(2026, 12, 31))
        expected_totals = {
            "income": {"UAH": Decimal("500")},
            "expenses": {"UAH": Decimal("84.25"), "USD": Decimal("1")},
        }
        if totals != expected_totals:
            raise CompatibilityFailure(f"Unexpected TextMoney period totals: {totals}.")
        print(f"PASS official Beancount {official_version} ({Path(loader.__file__).resolve()})")
        print(
            f"PASS TextMoney: {len(app_rows)} explicit postings and {len(app)} "
            "currency-specific balances match the golden results"
        )
        print("PASS period totals: income 500 UAH; expenses 84.25 UAH and 1 USD")

        bean_query = executable("bean-query")
        if bean_query is None:
            print("SKIPPED Beanquery: bean-query executable not found")
            beanquery_available = False
        else:
            bean_rows = bean_query_postings(path)
            if bean_rows != official_rows:
                raise CompatibilityFailure("Beanquery posting-level results differ from Beancount.")
            version_result = run([bean_query, "--version"])
            print(f"PASS Beanquery ({version_result.stdout.strip()}): exact posting rows match")
            beanquery_available = True

        try:
            expenses, income = fava_report_totals(path)
        except ImportError:
            print("SKIPPED Fava: Python package unavailable")
            fava_available = False
        else:
            if expenses != expected_totals["expenses"] or income != expected_totals["income"]:
                raise CompatibilityFailure(
                    f"Fava interval reports disagree with the expected totals: "
                    f"income={income}, expenses={expenses}."
                )
            print("PASS Fava interval reports: income 500 UAH; expenses 84.25 UAH and 1 USD")
            fava_available = True

        rustledger = shutil.which("rledger")
        if rustledger is None:
            print("SKIPPED RustLedger: rledger executable not found")
            rustledger_available = False
        else:
            run([rustledger, "check", str(path)])
            rust_rows = rustledger_postings(path, rustledger)
            if rust_rows != official_rows:
                raise CompatibilityFailure(
                    "RustLedger and official Beancount disagree on explicit posting rows."
                )
            run(
                [
                    rustledger,
                    "report",
                    "--format",
                    "json",
                    "--no-pager",
                    str(path),
                    "balances",
                ]
            )
            run(
                [
                    rustledger,
                    "report",
                    "--format",
                    "json",
                    "--no-pager",
                    str(path),
                    "income",
                ]
            )
            version_result = run([rustledger, "--version"])
            print(
                f"PASS {version_result.stdout.strip()}: check, exact BQL posting query, "
                "balances and income reports"
            )
            print(
                "NOTE RustLedger report JSON can round fractional display amounts; "
                "exact cross-engine comparisons use BQL posting results."
            )
            rustledger_available = True

        rustfava_available = check_rustfava(
            args.rustfava_appimage or os.getenv("RUSTFAVA_APPIMAGE")
        )

    missing = [
        name
        for name, available in (
            ("Beanquery", beanquery_available),
            ("Fava", fava_available),
            ("RustLedger", rustledger_available),
            ("rustfava AppImage", rustfava_available),
        )
        if not available
    ]
    if args.strict and missing:
        print(f"FAIL strict compatibility: required tools unavailable: {', '.join(missing)}")
        return 1
    print("NOT TESTED Fava/rustfava desktop GUI views; use the documented manual smoke checklist.")
    print("PASS compatibility suite completed for all available automated engines.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CompatibilityFailure, LedgerError, OSError, ValueError) as exc:
        print(f"FAIL compatibility suite: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
