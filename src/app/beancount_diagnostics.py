"""Fixture-only syntax/editing experiment, not a Beancount accounting engine."""

import io
import logging
import platform
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from importlib.metadata import version
from importlib.resources import files
from pathlib import PurePosixPath

from autobean_refactor import models, printer
from autobean_refactor.parser import Parser
from beancount_parser.parser import make_parser
from lark.exceptions import UnexpectedInput

logger = logging.getLogger(__name__)
type Totals = dict[tuple[str, str], Decimal]
ZERO = Decimal("0")
EXPECTED_BALANCES: Totals = {
    ("Assets:Bank", "UAH"): Decimal("47180"),
    ("Assets:Cash", "UAH"): Decimal("1750"),
    ("Assets:USD", "USD"): Decimal("150"),
    ("Assets:EUR", "EUR"): Decimal("85"),
    ("Liabilities:Loan", "UAH"): Decimal("-4000"),
    ("Equity:Opening", "UAH"): Decimal("-10000"),
    ("Equity:Opening", "USD"): Decimal("-100"),
    ("Equity:Opening", "EUR"): Decimal("-50"),
    ("Income:Salary", "UAH"): Decimal("-40000"),
    ("Expenses:Food", "UAH"): Decimal("1100"),
    ("Expenses:Travel", "UAH"): Decimal("200"),
    ("Expenses:Fees", "UAH"): Decimal("120"),
    ("Expenses:Interest", "UAH"): Decimal("100"),
}


class DiagnosticsError(Exception):
    """Explicit experiment failure or deliberately unsupported accounting feature."""


def render(model: models.RawModel) -> str:
    return printer.print_model(model, io.StringIO()).getvalue()


def bundled_sources() -> dict[str, str]:
    directory = files("app").joinpath("fixtures", "beancount")
    return {
        resource.name: resource.read_text(encoding="utf-8")
        for resource in directory.iterdir()
        if resource.name.endswith(".beancount")
    }


def parse_book(sources: Mapping[str, str], entry: str = "main.beancount") -> dict[str, models.File]:
    """Resolve only literal relative includes in an explicitly supplied in-memory bundle."""
    parser = Parser()
    parsed: dict[str, models.File] = {}
    active: set[str] = set()

    def visit(name: str) -> None:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or any(c in name for c in "*?[]\\"):
            raise DiagnosticsError("Only literal, relative bundle includes are supported.")
        if name in active:
            raise DiagnosticsError(f"Include cycle in synthetic bundle: {name}.")
        if name in parsed:
            return
        if name not in sources:
            raise DiagnosticsError(f"Missing included fixture: {name}.")
        try:
            model = parser.parse(sources[name], models.File)
        except UnexpectedInput as exc:
            raise DiagnosticsError(
                f"Unsupported syntax in {name}, line {exc.line}; no text rewritten."
            ) from exc
        parsed[name] = model
        active.add(name)
        for directive in model.raw_directives:
            if isinstance(directive, models.Include):
                visit(str(path.parent / directive.filename))
        active.remove(name)

    visit(entry)
    return parsed


def unit_totals(
    book: Mapping[str, models.File], start: date = date.min, end: date = date.max
) -> Totals:
    """Sum explicit units per account/currency over [start, end); no booking or valuation."""
    if start >= end:
        raise DiagnosticsError("Period must have start < end.")
    descriptive = (
        models.Option,
        models.Include,
        models.Open,
        models.Commodity,
        models.Note,
        models.Event,
        models.Price,
        models.Custom,
    )
    totals: Totals = {}
    with localcontext() as context:
        context.prec = 50
        for file in book.values():
            for directive in file.raw_directives:
                if isinstance(directive, descriptive):
                    continue
                if not isinstance(directive, models.Transaction):
                    raise DiagnosticsError(
                        f"{type(directive).__name__} needs official engine semantics; "
                        "quantity totals refused."
                    )
                # Refuse unsupported features even outside the requested period.
                for posting in directive.postings:
                    if isinstance(posting, models.BlockComment):
                        continue
                    if (
                        posting.number is None
                        or posting.currency is None
                        or posting.cost is not None
                    ):
                        raise DiagnosticsError(
                            "Cost lots or inferred/partial amounts require the official engine; "
                            "quantity totals refused."
                        )
                    if start <= directive.date < end:
                        key = (posting.account, posting.currency)
                        totals[key] = totals.get(key, ZERO) + posting.number
    return totals


def edit_narration(text: str, transaction_id: str, narration: str) -> str:
    """Return an in-memory lossless edit; never write user files."""
    try:
        model = Parser().parse(text, models.File)
    except UnexpectedInput as exc:
        raise DiagnosticsError(
            f"Unsupported syntax at line {exc.line}; no text rewritten."
        ) from exc
    matches = [
        directive
        for directive in model.raw_directives
        if isinstance(directive, models.Transaction) and directive.meta.get("id") == transaction_id
    ]
    if len(matches) != 1:
        raise DiagnosticsError(
            "Editing requires exactly one matching transaction ID; no text rewritten."
        )
    matches[0].narration = narration
    return render(model)


def append_transaction(text: str) -> str:
    """Append one synthetic transaction without reserializing any existing bytes."""
    transaction = models.Transaction.from_value(
        date(2026, 2, 4),
        "Synthetic",
        "Appended by parser diagnostics",
        [
            models.Posting.from_value("Expenses:Food", Decimal("12.34"), "UAH"),
            models.Posting.from_value("Assets:Bank", Decimal("-12.34"), "UAH"),
        ],
        meta={"id": "diagnostics-appended-001"},
        tags=["diagnostics"],
    )
    return text + ("\n" if text.endswith("\n") else "\n\n") + render(transaction) + "\n"


@dataclass(frozen=True)
class DiagnosticsResult:
    passed: bool
    diagnostics: tuple[str, ...]

    def display(self) -> str:
        return "\n".join(
            [
                "PASS: bounded Beancount fixture experiment"
                if self.passed
                else "FAIL: Beancount diagnostics",
                *self.diagnostics,
            ]
        )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DiagnosticsError(message)


def run_diagnostics() -> DiagnosticsResult:
    """The same repeatable check runs on desktop and from the Android button."""
    diagnostics: list[str] = []
    try:
        diagnostics.append(f"Python {platform.python_version()}; {platform.system()}")
        diagnostics.append(
            "; ".join(
                f"{name} {version(name)}"
                for name in ("beancount-parser", "autobean-refactor", "lark")
            )
        )
        sources = bundled_sources()
        book = parse_book(sources)
        for name, model in book.items():
            _require(render(model) == sources[name], f"Lossless round-trip failed: {name}.")
        transactions = [
            entry
            for file in book.values()
            for entry in file.raw_directives
            if isinstance(entry, models.Transaction)
        ]
        accounts = [
            entry.account
            for file in book.values()
            for entry in file.raw_directives
            if isinstance(entry, models.Open)
        ]
        _require(
            len(book) == 4 and len(accounts) == 11 and len(transactions) == 14,
            "Unexpected file/account/transaction counts.",
        )
        split = next(t for t in transactions if t.meta.get("id") == "split-001")
        first_posting = split.postings[0]
        related = split.meta.get("related")
        _require(
            split.meta.get("timestamp") == "2026-01-06T12:34:56+02:00"
            and split.meta.get("verified") is True
            and split.meta.get("rate") == Decimal("1.25")
            and split.meta.get("settled") == date(2026, 1, 6)
            and isinstance(related, models.Account)
            and related.value == "Assets:Bank"
            and set(split.tags) == {"food", "shared"}
            and set(split.links) == {"receipt-001"}
            and isinstance(first_posting, models.Posting)
            and first_posting.meta.get("receipt") == "synthetic-receipt",
            "Metadata, tag, link or posting metadata mismatch.",
        )
        diagnostics.append(
            "4 included files; 11 accounts; 14 transactions; typed metadata/tags OK."
        )

        launch = make_parser()
        for name in book:
            launch.parse(sources[name])
        try:
            launch.parse(sources["advanced.beancount"])
        except UnexpectedInput:
            diagnostics.append(
                "LaunchPlatform basic syntax OK; advanced syntax rejected as expected."
            )
        else:
            raise DiagnosticsError(
                "LaunchPlatform advanced-syntax behavior changed; review compatibility."
            )

        _require(unit_totals(book) == EXPECTED_BALANCES, "Golden unit-balance mismatch.")
        january = unit_totals(book, date(2026, 1, 1), date(2026, 2, 1))
        february = unit_totals(book, date(2026, 2, 1), date(2026, 3, 1))
        for name, totals, expense, income in (
            ("January", january, Decimal("1120"), Decimal("-30000")),
            ("February", february, Decimal("400"), Decimal("-10000")),
        ):
            _require(
                sum(
                    (v for (a, c), v in totals.items() if a.startswith("Expenses:") and c == "UAH"),
                    ZERO,
                )
                == expense
                and totals[("Income:Salary", "UAH")] == income,
                f"{name} period totals mismatch.",
            )
        diagnostics.append(
            "Decimal units match desktop oracle golden: bank 47180 UAH, "
            "cash 1750 UAH, USD 150, EUR 85, loan -4000 UAH."
        )
        diagnostics.append(
            "Period expense totals: January 1120 UAH; February 400 UAH (refund netted)."
        )

        original = sources["january.beancount"]
        edited = edit_narration(original, "split-001", "Edited split expense")
        _require(
            edited == original.replace('"Split expense"', '"Edited split expense"', 1),
            "Narration edit changed unrelated bytes.",
        )
        appended = append_transaction(sources["february.beancount"])
        _require(
            appended.startswith(sources["february.beancount"]), "Append changed existing bytes."
        )
        edited_sources = sources | {"january.beancount": edited, "february.beancount": appended}
        after = unit_totals(parse_book(edited_sources))
        _require(
            after[("Expenses:Food", "UAH")] == Decimal("1112.34")
            and after[("Assets:Bank", "UAH")] == Decimal("47167.66"),
            "Appended transaction totals mismatch.",
        )
        diagnostics.append("Lossless narration edit and synthetic append OK (memory only).")

        advanced = parse_book(sources, "advanced.beancount")
        _require(
            render(advanced["advanced.beancount"]) == sources["advanced.beancount"],
            "Advanced directives not preserved.",
        )
        advanced_text = sources["advanced.beancount"]
        _require(
            edit_narration(advanced_text, "inferred-001", "Edited inferred transaction")
            == advanced_text.replace(
                '"Inferred balancing amount"', '"Edited inferred transaction"', 1
            ),
            "Edit changed unsupported directives.",
        )
        try:
            unit_totals(advanced)
        except DiagnosticsError:
            diagnostics.append(
                "Advanced directives preserved; unsupported accounting explicitly refused."
            )
        else:
            raise DiagnosticsError("Unsupported accounting was not refused.")
        try:
            parse_book(sources, "unknown.beancount")
        except DiagnosticsError:
            diagnostics.append("Unknown dated syntax rejected; no source rewritten.")
        else:
            raise DiagnosticsError("Unknown syntax was silently accepted.")
        diagnostics.append(
            "Not a full engine: no inference, booking, validation, valuation or plugins."
        )
    except (DiagnosticsError, UnexpectedInput) as exc:
        logger.warning("Bundled Beancount experiment failed: %s", type(exc).__name__)
        diagnostics.append(str(exc))
        return DiagnosticsResult(False, tuple(diagnostics))
    logger.info("Bundled Beancount experiment completed")
    return DiagnosticsResult(True, tuple(diagnostics))


if __name__ == "__main__":
    result = run_diagnostics()
    print(result.display())
    raise SystemExit(0 if result.passed else 1)
