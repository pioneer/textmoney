import pytest

from app.domain.code_suggestions import suggest_account_code, suggest_category_code
from app.domain.ledger import LedgerError, account_path


@pytest.mark.parametrize(
    ("name", "currency", "expected", "collision"),
    [
        ("Монобанк, доларова картка", "USD", "Assets:Monobank:USD", False),
        ("ПриватБанк картка", "UAH", "Assets:Privatbank:UAH", True),
        ("Готівка", "UAH", "Assets:Cash:UAH", True),
        ("Mystery Wallet", "EUR", "Assets:Bank:Mystery-Wallet:EUR", False),
    ],
)
def test_account_suggestions_are_valid_and_deterministic(
    name: str, currency: str, expected: str, collision: bool
) -> None:
    existing = (
        "Assets:Monobank:UAH",
        "Assets:Privatbank:UAH",
        "Assets:Privatbank:USD",
        "Assets:Cash:UAH",
    )
    result = suggest_account_code(name, "Assets", currency, existing)
    assert result.recommended.code == expected
    assert result.recommended.valid is not collision
    assert bool(result.recommended.collision) is collision
    assert suggest_account_code(name, "Assets", currency, existing).recommended.code == expected
    assert account_path(result.recommended.code, "Assets") == expected


def test_new_liability_account_uses_liability_hierarchy() -> None:
    result = suggest_account_code("Монобанк", "Liabilities", "UAH")
    assert result.recommended.code == "Liabilities:Loans:Monobank:UAH"
    assert result.recommended.valid
    assert not result.recommended.code.startswith("Assets:")


def test_existing_account_convention_precedes_default_hierarchy() -> None:
    result = suggest_account_code(
        "Монобанк, доларова картка",
        "Assets",
        "USD",
        ("Assets:Bank:Monobank:UAH",),
    )
    assert result.recommended.code == "Assets:Bank:Monobank:USD"


def test_account_suggestion_removes_currency_and_card_descriptors() -> None:
    result = suggest_account_code(
        "Монобанк доларова картка",
        "Assets",
        "USD",
    )
    assert result.recommended.code == "Assets:Bank:Monobank:USD"


@pytest.mark.parametrize(
    ("name", "root", "parent", "expected"),
    [
        ("Здоров'я", "Expenses", None, "Expenses:Health"),
        ("Кафе", "Expenses", "Expenses:Food", "Expenses:Food:Cafe"),
        ("Фриланс", "Income", None, "Income:Freelance"),
        ("Нова категорія", "Expenses", None, "Expenses:Nova-Kategoriya"),
    ],
)
def test_category_suggestions_respect_root_parent_and_transliteration(
    name: str,
    root: str,
    parent: str | None,
    expected: str,
) -> None:
    existing = ("Expenses:Food", "Expenses:Food:Groceries") if parent else ()
    result = suggest_category_code(name, root, existing, parent=parent)
    assert result.recommended.code == expected
    assert result.recommended.valid
    assert account_path(result.recommended.code, root) == expected


def test_category_parent_must_exist_under_matching_type_root() -> None:
    wrong_root = suggest_category_code(
        "Salary", "Income", ("Expenses:Work",), parent="Expenses:Work"
    )
    missing_parent = suggest_category_code("Salary", "Income", (), parent="Income:Work")
    assert not wrong_root.recommended.valid
    assert not missing_parent.recommended.valid


def test_collisions_are_explicit_and_no_numeric_suffix_is_invented() -> None:
    result = suggest_category_code("Продукты", "Expenses", ("Expenses:Groceries",))
    assert result.recommended.code == "Expenses:Groceries"
    assert result.recommended.collision == "Expenses:Groceries"
    assert not result.recommended.valid
    assert all(not candidate.code.endswith("-2") for candidate in result.alternatives)


def test_suggestions_never_hide_beancount_validation_errors() -> None:
    result = suggest_category_code("123 invalid!", "Expenses")
    assert not result.recommended.valid
    with pytest.raises(LedgerError):
        account_path(result.recommended.code, "Expenses")
