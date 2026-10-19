import asyncio
import tomllib
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest
from autobean_refactor import models
from autobean_refactor.parser import Parser
from beancount import loader
from beancount.core import data, getters
from beancount.ops import summarize
from beancount_parser.parser import make_parser
from lark.exceptions import UnexpectedInput

from app.beancount_diagnostics import (
    EXPECTED_BALANCES,
    DiagnosticsError,
    Totals,
    append_transaction,
    bundled_sources,
    edit_narration,
    parse_book,
    render,
    run_diagnostics,
    unit_totals,
)
from app.ui.beancount import BeancountDiagnosticsView
from app.ui.home import StorageView, home_view


def official_book(
    tmp_path: Path, sources: dict[str, str], entry: str = "main.beancount"
) -> data.Directives:
    for name, content in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
    entries, errors, _ = loader.load_file(str(tmp_path / entry))
    assert not errors, [error.message for error in errors]
    return entries


def official_totals(
    entries: data.Directives, start: date = date.min, end: date = date.max
) -> Totals:
    selected = [entry for entry in entries if start <= entry.date < end]
    balances, _ = summarize.balance_by_account(selected)
    totals: Totals = {}
    for account, balance in balances.items():
        for position in balance:
            number = position.units.number
            assert isinstance(number, Decimal)
            totals[account, position.units.currency] = number
    return totals


def test_complete_bundled_experiment_passes_on_desktop() -> None:
    result = run_diagnostics()
    assert result.passed, result.display()
    assert "Not a full engine" in result.display()
    assert "quantity" not in result.display() or "refused" in result.display()


@pytest.mark.parametrize(
    "start,end",
    [
        (date.min, date.max),
        (date(2026, 1, 1), date(2026, 2, 1)),
        (date(2026, 2, 1), date(2026, 3, 1)),
        (date(2026, 1, 12), date(2026, 1, 13)),
    ],
)
def test_quantity_and_period_totals_match_official_beancount_3(
    tmp_path: Path, start: date, end: date
) -> None:
    sources = bundled_sources()
    entries = official_book(tmp_path, sources)
    actual = unit_totals(parse_book(sources), start, end)
    assert actual == official_totals(entries, start, end)
    assert all(isinstance(value, Decimal) for value in actual.values())
    if start == date.min:
        assert actual == EXPECTED_BALANCES
        assert len(getters.get_account_open_close(entries)) == 11
        assert len([entry for entry in entries if isinstance(entry, data.Transaction)]) == 14


def test_metadata_transactions_and_accounts_match_official(tmp_path: Path) -> None:
    sources = bundled_sources()
    official = official_book(tmp_path, sources)
    book = parse_book(sources)
    actual_accounts = {
        entry.account: list(entry.currencies)
        for file in book.values()
        for entry in file.raw_directives
        if isinstance(entry, models.Open)
    }
    expected_accounts = {
        entry.account: entry.currencies for entry in official if isinstance(entry, data.Open)
    }
    assert actual_accounts == expected_accounts
    candidate = {
        transaction.meta.get("id"): transaction
        for file in book.values()
        for transaction in file.raw_directives
        if isinstance(transaction, models.Transaction)
    }
    reference = [entry for entry in official if isinstance(entry, data.Transaction)]
    assert {entry.meta["id"] for entry in reference} == candidate.keys()
    for expected in reference:
        actual = candidate[expected.meta["id"]]
        assert actual.date == expected.date
        assert actual.payee == expected.payee
        assert actual.narration == expected.narration
        assert actual.flag == expected.flag
        assert set(actual.tags) == expected.tags
        assert set(actual.links) == expected.links
        for key, value in actual.meta.items():
            if isinstance(value, models.Account):
                # Lossless model retains token type; official metadata uses an account string.
                assert expected.meta[key] == value.value
                continue
            assert expected.meta[key] == value
            assert type(expected.meta[key]) is type(value)
        actual_postings = [p for p in actual.postings if isinstance(p, models.Posting)]
        assert len(actual_postings) == len(expected.postings)
        # Official booking groups postings by currency; the syntax model keeps source order.
        expected_by_units = {}
        for posting in expected.postings:
            assert posting.units is not None
            expected_by_units[posting.account, posting.units.currency] = posting
        for posting in actual_postings:
            expected_posting = expected_by_units[posting.account, posting.currency]
            assert expected_posting.units is not None
            assert (posting.account, posting.number, posting.currency) == (
                expected_posting.account,
                expected_posting.units.number,
                expected_posting.units.currency,
            )
            for key, value in posting.meta.items():
                assert expected_posting.meta is not None
                assert expected_posting.meta[key] == value


def test_safe_edit_and_append_written_to_synthetic_files_match_official(tmp_path: Path) -> None:
    sources = bundled_sources()
    before = official_totals(official_book(tmp_path, sources))
    january = sources["january.beancount"]
    edited = edit_narration(january, "split-001", "Edited split expense")
    assert edited == january.replace('"Split expense"', '"Edited split expense"', 1)
    sources["january.beancount"] = edited
    # This writes the edited source to a temp file, never the bundled original.
    assert official_totals(official_book(tmp_path, sources)) == before
    february = sources["february.beancount"]
    sources["february.beancount"] = append_transaction(february)
    assert sources["february.beancount"].startswith(february)
    official = official_book(tmp_path, sources)
    after = official_totals(official)
    assert after == unit_totals(parse_book(sources))
    assert after[("Expenses:Food", "UAH")] == Decimal("1112.34")
    assert after[("Assets:Bank", "UAH")] == Decimal("47167.66")
    appended = next(
        e
        for e in official
        if isinstance(e, data.Transaction) and e.meta.get("id") == "diagnostics-appended-001"
    )
    assert appended.tags == {"diagnostics"}


def test_advanced_engine_semantics_are_valid_but_not_silently_calculated(tmp_path: Path) -> None:
    sources = bundled_sources()
    reference = official_book(tmp_path, sources, "advanced.beancount")
    totals = official_totals(reference)
    assert totals[("Assets:USD", "USD")] == Decimal("80")
    assert totals[("Assets:Broker", "SHRS")] == Decimal("2")
    assert totals[("Assets:Cash", "UAH")] == Decimal("5")
    book = parse_book(sources, "advanced.beancount")
    assert render(book["advanced.beancount"]) == sources["advanced.beancount"]
    with pytest.raises(DiagnosticsError, match="official engine"):
        unit_totals(book)
    original = sources["advanced.beancount"]
    edited = edit_narration(original, "inferred-001", "Edited inferred transaction")
    assert edited == original.replace(
        '"Inferred balancing amount"', '"Edited inferred transaction"', 1
    )
    sources["advanced.beancount"] = edited
    assert official_totals(official_book(tmp_path, sources, "advanced.beancount")) == totals


@pytest.mark.parametrize(
    "text",
    [
        '2026-01-01 * "Inference"\n  Assets:USD 1 USD\n  Equity:Opening\n',
        '2026-01-01 * "Cost"\n  Assets:Broker 1 SHRS {1 USD}\n  Assets:USD -1 USD\n',
        "2026-01-01 pad Assets:Cash Equity:Opening\n",
        "2026-01-01 balance Assets:Cash 5 UAH\n",
        'plugin "beancount.plugins.auto_accounts"\n',
        "pushtag #inherited\n",
    ],
)
def test_each_unsupported_accounting_feature_refuses_totals(text: str) -> None:
    book = parse_book({"main.beancount": text})
    with pytest.raises(DiagnosticsError, match="official engine"):
        unit_totals(book)


def test_launchplatform_is_not_a_full_beancount_3_parser() -> None:
    parser = make_parser()
    sources = bundled_sources()
    for name in ("main.beancount", "accounts.beancount", "january.beancount", "february.beancount"):
        parser.parse(sources[name])
    with pytest.raises(UnexpectedInput):
        parser.parse(sources["advanced.beancount"])


def test_unknown_syntax_rejected_by_both_candidates_and_official() -> None:
    text = bundled_sources()["unknown.beancount"]
    with pytest.raises(UnexpectedInput):
        make_parser().parse(text)
    with pytest.raises(DiagnosticsError, match="Unsupported syntax"):
        parse_book({"main.beancount": text})
    _, errors, _ = loader.load_string(text)
    assert errors
    with pytest.raises(DiagnosticsError, match="no text rewritten"):
        edit_narration(text, "unknown", "Do not write")
    assert text == bundled_sources()["unknown.beancount"]
    # Append preserves even unknown bytes, but does NOT make invalid syntax valid.
    assert append_transaction(text).startswith(text)


@pytest.mark.parametrize(
    "include", ['"missing.beancount"', '"../outside"', '"/absolute"', '"*.bean"']
)
def test_include_errors_are_explicit(include: str) -> None:
    with pytest.raises(DiagnosticsError):
        parse_book({"main.beancount": f"include {include}\n"})


def test_include_cycle_and_relative_nested_include() -> None:
    with pytest.raises(DiagnosticsError, match="cycle"):
        parse_book({"main.beancount": 'include "main.beancount"\n'})
    sources = {
        "main.beancount": 'include "sub/one.beancount"\n',
        "sub/one.beancount": 'include "two.beancount"\n',
        "sub/two.beancount": "; only a comment\n",
    }
    assert list(parse_book(sources)) == list(sources)


def test_ambiguous_id_refuses_edit_and_crlf_roundtrip_is_exact() -> None:
    source = bundled_sources()["january.beancount"]
    with pytest.raises(DiagnosticsError, match="exactly one"):
        edit_narration(source, "not-present", "No change")
    with pytest.raises(DiagnosticsError, match="exactly one"):
        edit_narration(source + source, "split-001", "No change")
    crlf = source.replace("\n", "\r\n")
    assert render(Parser().parse(crlf, models.File)) == crlf
    assert edit_narration(crlf, "split-001", "CRLF edit") == crlf.replace(
        '"Split expense"', '"CRLF edit"', 1
    )


def test_parser_acceptance_does_not_imply_accounting_validation() -> None:
    sources = bundled_sources()
    invalid = sources["january.beancount"].replace("600.00 UAH", "601.00 UAH", 1)
    make_parser().parse(invalid)
    # Unit aggregation has no claim to detect an unbalanced transaction.
    assert unit_totals(parse_book(sources | {"january.beancount": invalid}))[
        ("Expenses:Food", "UAH")
    ] == Decimal("1101")
    _, errors, _ = loader.load_string(sources["accounts.beancount"] + "\n" + invalid)
    assert any("does not balance" in error.message for error in errors)


def test_empty_invalid_period_and_exact_decimal_arithmetic() -> None:
    assert unit_totals(parse_book({"main.beancount": "; empty\n"})) == {}
    with pytest.raises(DiagnosticsError, match="start < end"):
        unit_totals({}, date(2026, 1, 1), date(2026, 1, 1))
    text = '2026-01-01 * "Decimal"\n  Expenses:Food (0.1 + 0.2) UAH\n  Assets:Bank -0.3 UAH\n'
    assert unit_totals(parse_book({"main.beancount": text}))[("Expenses:Food", "UAH")] == Decimal(
        "0.3"
    )


def test_bundled_check_reports_failure_not_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.beancount_diagnostics.bundled_sources", lambda: {})
    result = run_diagnostics()
    assert not result.passed
    assert "FAIL" in result.display()
    assert "Missing included fixture" in result.display()


def test_android_action_uses_only_bundled_experiment_and_reports_exceptions(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        view = BeancountDiagnosticsView()
        view.update = MagicMock()
        monkeypatch.setattr("app.ui.beancount._run_bundled_experiment", lambda: "PASS: synthetic")
        await view._run(MagicMock())
        assert view.result.value == "PASS: synthetic"
        assert not view.button.disabled

        def fail() -> str:
            raise ImportError("synthetic dependency failure")

        monkeypatch.setattr("app.ui.beancount._run_bundled_experiment", fail)
        await view._run(MagicMock())
        assert "FAIL" in (view.result.value or "")
        assert "ImportError" in (view.result.value or "")
        assert not view.button.disabled
        assert "Traceback" in caplog.text

    asyncio.run(scenario())


def test_storage_view_is_preserved_next_to_separate_parser_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ui.finance import FinanceView

    root = home_view()
    assert isinstance(root.content, FinanceView)
    page = MagicMock(spec=ft.Page)
    monkeypatch.setattr(FinanceView, "page", property(lambda self: page))
    root.content._diagnostics(MagicMock())
    assert isinstance(root.content.diagnostics.controls[0], BeancountDiagnosticsView)
    assert isinstance(root.content.diagnostics.controls[1], StorageView)


def test_android_extracts_grammar_readers_instead_of_zipimporting_them() -> None:
    root = Path(__file__).resolve().parents[1]
    configuration = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    extracted = configuration["tool"]["flet"]["android"]["extract_packages"]
    assert {"autobean_refactor", "beancount_parser", "lark"} <= set(extracted)
    assert not any(
        requirement.startswith("beancount==")
        for requirement in configuration["project"]["dependencies"]
    )
