"""Deterministic, offline Beancount identifier suggestions."""

import re
import unicodedata
from dataclasses import dataclass

from app.domain.ledger import LedgerError, account_path

_CYRILLIC = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "ґ": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "є": "ye",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "і": "i",
    "ї": "yi",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}
_SEMANTIC_NAMES = {
    "cash": "Cash",
    "готівка": "Cash",
    "наличные": "Cash",
    "продукти": "Groceries",
    "продукты": "Groceries",
    "groceries": "Groceries",
    "здоров'я": "Health",
    "здоровье": "Health",
    "health": "Health",
    "кафе": "Cafe",
    "cafe": "Cafe",
    "фриланс": "Freelance",
    "freelance": "Freelance",
}
_CURRENCY_NAMES = {
    "usd": {"usd", "dollar", "dollars", "долар", "доларова", "долари", "доллар", "долларовая"},
    "eur": {"eur", "euro", "євро", "евро"},
    "uah": {"uah", "гривня", "гривні", "гривна", "гривны"},
}
_ACCOUNT_DESCRIPTORS = {"card", "картка", "карта"}


@dataclass(frozen=True)
class CodeCandidate:
    code: str
    valid: bool
    collision: str | None = None


@dataclass(frozen=True)
class CodeSuggestions:
    recommended: CodeCandidate
    alternatives: tuple[CodeCandidate, ...] = ()


def _plain(value: str) -> str:
    output: list[str] = []
    for character in value.casefold():
        if character in _CYRILLIC:
            output.append(_CYRILLIC[character])
            continue
        decomposed = unicodedata.normalize("NFKD", character)
        output.extend(
            char
            for char in decomposed
            if not unicodedata.combining(char) and char.isascii() and char.isalnum()
        )
        if character.isspace() or not character.isalnum():
            output.append(" ")
    return " ".join("".join(output).split())


def _component(value: str) -> str:
    words = _plain(value).split()
    if not words:
        return ""
    return "-".join(word[0].upper() + word[1:] for word in words)


def _semantic_component(value: str) -> str:
    normalized = " ".join(value.casefold().replace("’", "'").split())
    return _SEMANTIC_NAMES.get(normalized, _component(value))


def _child_component(component: str, parent: str | None) -> str:
    if not parent:
        return component
    parent_leaf = _component(parent.rsplit(":", 1)[-1])
    if component == parent_leaf:
        return ""
    if component.startswith(parent_leaf + "-"):
        return component[len(parent_leaf) + 1 :]
    return component


def _currency_adjusted_name(name: str, currency: str) -> str:
    words = name.split()
    currencies = _CURRENCY_NAMES.get(currency.lower(), {currency.lower()})
    while words and words[-1].casefold().strip(".,;:()") in currencies:
        words.pop()
    return " ".join(
        word
        for word in words
        if word.casefold().strip(".,;:()") not in (_ACCOUNT_DESCRIPTORS | currencies)
    )


def _candidate(
    code: str,
    root: str,
    existing: dict[str, str],
) -> CodeCandidate:
    declared_root = code.strip().split(":", 1)[0]
    if declared_root in {"Assets", "Liabilities", "Income", "Expenses", "Equity"} and (
        declared_root != root
    ):
        return CodeCandidate(code, False)
    try:
        normalized = account_path(code, root)
    except LedgerError:
        return CodeCandidate(code, False)
    collision = existing.get(normalized)
    return CodeCandidate(normalized, collision is None, collision)


def validate_account_code(
    code: str,
    root: str,
    existing: tuple[str, ...],
) -> CodeCandidate:
    return _candidate(code, root, {identifier: identifier for identifier in existing})


def validate_category_code(
    code: str,
    root: str,
    existing: tuple[str, ...],
    *,
    parent: str | None = None,
) -> CodeCandidate:
    identifiers = {identifier: identifier for identifier in existing}
    candidate = _candidate(code, root, identifiers)
    if not candidate.valid:
        return candidate
    if parent:
        try:
            normalized_parent = account_path(parent, root)
        except LedgerError:
            return CodeCandidate(candidate.code, False)
        if (
            normalized_parent not in existing
            or not candidate.code.startswith(normalized_parent + ":")
            or candidate.code.count(":") != normalized_parent.count(":") + 1
        ):
            return CodeCandidate(candidate.code, False)
    return candidate


def _deduplicate(candidates: list[CodeCandidate]) -> tuple[CodeCandidate, ...]:
    seen: set[str] = set()
    unique = []
    for candidate in candidates:
        if candidate.code not in seen:
            seen.add(candidate.code)
            unique.append(candidate)
    return tuple(unique)


def _matched_account_convention(name: str, root: str, identifiers: tuple[str, ...]) -> str | None:
    name_key = _plain(name).replace(" ", "")
    matches: list[tuple[int, int, str]] = []
    for identifier in identifiers:
        parts = identifier.split(":")
        if not parts or parts[0] != root:
            continue
        for index, part in enumerate(parts[1:], 1):
            if re.fullmatch(r"[A-Z]{3}", part):
                continue
            component_key = _plain(part).replace(" ", "")
            if component_key and component_key in name_key:
                prefix = ":".join(parts[: index + 1])
                matches.append((len(component_key), -len(parts), prefix))
    if not matches:
        return None
    return max(matches)[2]


def suggest_account_code(
    name: str,
    root: str,
    currency: str,
    existing: tuple[str, ...] = (),
) -> CodeSuggestions:
    """Suggest an account path using matching existing hierarchy before defaults."""
    clean_name = _currency_adjusted_name(name.strip(), currency)
    component = _semantic_component(clean_name)
    if not component:
        component = "Account"
    existing_names = {identifier: identifier for identifier in existing}
    conventional_prefix = _matched_account_convention(clean_name, root, existing)
    if conventional_prefix:
        recommended = f"{conventional_prefix}:{currency.upper()}"
    elif component == "Cash":
        recommended = f"{root}:Cash:{currency.upper()}"
    elif root == "Liabilities":
        recommended = f"{root}:Loans:{component}:{currency.upper()}"
    else:
        recommended = f"{root}:Bank:{component}:{currency.upper()}"

    candidates = [_candidate(recommended, root, existing_names)]
    direct = f"{root}:{component}:{currency.upper()}"
    nested = (
        f"{root}:Bank:{component}:{currency.upper()}"
        if root == "Assets"
        else f"{root}:Loans:{component}:{currency.upper()}"
    )
    candidates.extend(
        _candidate(code, root, existing_names) for code in (direct, nested) if code != recommended
    )
    unique = _deduplicate(candidates)
    return CodeSuggestions(unique[0], unique[1:3])


def suggest_category_code(
    name: str,
    root: str,
    existing: tuple[str, ...] = (),
    *,
    parent: str | None = None,
) -> CodeSuggestions:
    """Suggest a category path, using an existing selected parent when supplied."""
    prefix = root
    if parent:
        try:
            normalized_parent = account_path(parent, root)
        except LedgerError:
            return CodeSuggestions(CodeCandidate(parent, False))
        if normalized_parent not in existing:
            return CodeSuggestions(CodeCandidate(normalized_parent, False))
        prefix = normalized_parent
    component = (
        _child_component(
            _semantic_component(name.strip()),
            prefix if prefix != root else None,
        )
        or "Category"
    )

    existing_names = {identifier: identifier for identifier in existing}
    recommended = f"{prefix}:{component}"
    candidates = [_candidate(recommended, root, existing_names)]
    transliterated = _child_component(_component(name.strip()), prefix if prefix != root else None)
    alternative = f"{prefix}:{transliterated}" if transliterated else recommended
    if alternative != recommended:
        candidates.append(_candidate(alternative, root, existing_names))
    unique = _deduplicate(candidates)
    return CodeSuggestions(unique[0], unique[1:3])
