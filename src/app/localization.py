"""Gettext-backed localization and locale-aware display formatting."""

import gettext
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from functools import cache
from pathlib import Path
from typing import Final

from babel.core import Locale
from babel.numbers import format_decimal

from app.domain.ledger import LedgerError

LOCALE_DIR: Final = Path(__file__).with_name("locale")
DOMAIN: Final = "textmoney"


def _available_languages() -> tuple[str, ...]:
    if not LOCALE_DIR.is_dir():
        return ()
    priority = {"en": 0, "uk": 1, "ru": 2}
    languages = {
        path.name
        for path in LOCALE_DIR.iterdir()
        if path.is_dir() and (path / "LC_MESSAGES" / f"{DOMAIN}.mo").is_file()
    }
    return tuple(sorted(languages, key=lambda language: (priority.get(language, 3), language)))


LANGUAGES: Final = _available_languages()


class MissingTranslationKey(LookupError):
    """Raised when an English translation key is not present."""


@cache
def _translations(language: str) -> gettext.GNUTranslations:
    try:
        return gettext.translation(
            DOMAIN, localedir=str(LOCALE_DIR), languages=[language], fallback=False
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Compiled translation catalogs are missing; run `uv run inv translations-compile`."
        ) from exc


def _locale(language: str) -> str:
    normalized = language.replace("-", "_")
    if normalized in LANGUAGES:
        return normalized
    base = normalized.split("_", 1)[0].lower()
    return next(
        (available for available in LANGUAGES if available.split("_", 1)[0].lower() == base),
        "en",
    )


def _format_translation(value: str, key: str, values: dict[str, object]) -> str:
    try:
        return value.format(**values)
    except (KeyError, IndexError, ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"Invalid formatting values for translation key {key!r}: {exc}") from exc


_ERROR_KEYS: Final = {
    (
        "Incomplete migration. Review the migration output before opening."
    ): "error_migration_incomplete",
    "Reload a valid journal first. Unsaved form input is retained.": "error_reload",
    "Folder service is starting. Try again shortly.": "error_folder_starting",
    "Select a folder first.": "error_select_folder_first",
    "That technical account ID already exists.": "error_duplicate_account",
    "That category already exists.": "error_duplicate_category",
    "Date must be YYYY-MM-DD.": "error_date_format",
    "Amount must be a nonnegative decimal with at most 2 fractional digits.": "error_amount_format",
    "Invalid monetary amount.": "error_amount_invalid",
    (
        "Amounts must be finite, within 12 integer digits, and have at most 2 decimals."
    ): "error_amount_range",
    "Display name/payee must be 1-120 characters without control characters.": "error_name",
    (
        "Technical ID needs colon-separated segments starting with A-Z, "
        "e.g. Cash or Food:Groceries."
    ): "error_identifier",
    "Currency must be a three-letter uppercase code, e.g. UAH, USD or EUR.": "error_currency",
    "Insufficient funds: an ordinary account would become negative.": "error_negative",
    "Expense amount must be greater than zero.": "error_expense_positive",
    "Expense date must be between 1970-01-01 and today.": "error_expense_date",
    "Future-dated transactions are not supported in MVP 0.1.": "error_future_transaction",
    "Choose an explicitly created account and category.": "error_choose_account_category",
    "Conflict: file changed externally. Draft retained. Read to reload.": "error_conflict",
    (
        "Journal has unsupported or invalid syntax. Original bytes preserved; saving blocked."
    ): "error_invalid_journal",
    "Invalid monetary expression. Saving blocked; journal preserved.": "error_invalid_journal",
    "Unbalanced or multi-currency transaction. No journal changes allowed.": "error_unbalanced",
    "Posting references an undeclared account. Create accounts explicitly.": "error_undeclared",
    "Posting currency differs from the account currency.": "error_currency_mismatch",
    (
        "Duplicate transaction ID. Journal unchanged; resolve duplicates externally."
    ): "error_duplicate_id",
    "Transaction needs at least two explicit postings.": "error_transaction_shape",
    "Transaction predates its account/category declaration.": "error_date_order",
    "Timestamp must include a timezone.": "error_invalid_journal",
    "Transaction IDs must be nonempty strings.": "error_invalid_journal",
    "File is not UTF-8. Draft retained; file not modified.": "error_utf8",
    ("Read first to establish a baseline before writing or backing up."): "error_not_ready",
    "No file exists to back up. Original is untouched.": "error_no_backup_source",
    "Backup verification failed. Original not modified.": "error_backup",
    "Write read-back differs. Inspect file and backup.": "error_write_uncertain",
    "Selected folder is unavailable. Select Folder again.": "error_folder_missing",
    "Selected folder disappeared. Select Folder again.": "error_folder_missing",
    (
        "The journal disappeared. Restore it before reloading; forms retained."
    ): "error_file_disappeared",
    "Persistent read/write grant missing. Select Folder again.": "error_grant",
    "Refusing a symlink test file; use a regular synthetic file.": "error_symlink",
    "Refusing a symlink backups folder.": "error_symlink",
    "Invalid SAF read response.": "error_invalid_response",
    "Invalid SAF file data.": "error_invalid_response",
    "Invalid SAF bridge response.": "error_invalid_response",
    "Invalid SAF backup response. Original not modified.": "error_invalid_response",
    "Android requires a content URI returned by the SAF picker.": "error_folder_missing",
}


def translate(
    language: str,
    key: str,
    **values: object,
) -> str:
    msgid = f"ui.{key}"
    english = _translations("en")
    english_value = english.gettext(msgid)
    if english_value in {msgid, ""}:
        raise MissingTranslationKey(f"Missing English translation key: {key!r}")
    selected = _translations(_locale(language))
    selected_value = selected.gettext(msgid)
    value = english_value if selected_value in {msgid, ""} else selected_value
    return _format_translation(value, key, values)


def translate_plural(
    language: str,
    singular_key: str,
    plural_key: str,
    count: int,
    **values: object,
) -> str:
    singular = f"ui.{singular_key}"
    plural = f"ui.{plural_key}"
    english = _translations("en")
    english_s = english.ngettext(singular, plural, 1)
    english_p = english.ngettext(singular, plural, 2)
    if english_s in {singular, plural, ""} or english_p in {singular, plural, ""}:
        raise MissingTranslationKey(
            f"Missing English plural translation keys: {singular_key!r}, {plural_key!r}"
        )
    selected = _translations(_locale(language))
    value = selected.ngettext(singular, plural, count)
    if value in {singular, plural, ""}:
        value = english.ngettext(singular, plural, count)
    return _format_translation(value, singular_key, {"count": count, **values})


def catalog_keys() -> dict[str, frozenset[str]]:
    result: dict[str, frozenset[str]] = {}
    for language in LANGUAGES:
        catalog = vars(_translations(language)).get("_catalog")
        if not isinstance(catalog, dict):
            raise RuntimeError(f"Invalid compiled gettext catalog for {language!r}.")
        keys = {
            msgid[3:] for msgid in catalog if isinstance(msgid, str) and msgid.startswith("ui.")
        }
        result[language] = frozenset(keys)
    return result


def system_language(locales: tuple[str, ...] | list[str]) -> str:
    for locale_tag in locales:
        language = locale_tag.replace("-", "_").lower()
        exact = next((code for code in LANGUAGES if code.lower() == language), None)
        if exact:
            return exact
        base = language.split("_", 1)[0]
        match = next(
            (code for code in LANGUAGES if code.split("_", 1)[0].lower() == base),
            None,
        )
        if match:
            return match
    return "en"


def localized_error(language: str, message: str) -> str:
    if message.startswith("Line "):
        return f"{translate(language, 'error_unsupported')}\n\n{message}"
    key = message if message in catalog_keys()["en"] else _ERROR_KEYS.get(message)
    if key is None:
        if message.startswith(
            ("Write failed or uncertain.", "SAF save outcome is uncertain.", "Journal may already")
        ):
            key = "error_write_uncertain"
        elif message.startswith("Read failed ("):
            key = "error_read"
        elif message.startswith("Backup failed ("):
            key = "error_backup"
        elif message.startswith("Unsupported") or "unsupported" in message.lower():
            key = "error_unsupported"
        elif message.startswith("Transaction textmoney-kind") or message.startswith(
            "Account display-name"
        ):
            key = "error_invalid_journal"
        else:
            key = "error_storage_generic"
    return translate(language, key)


def format_amount(value: Decimal, language: str, currency: str = "") -> str:
    raw = f"{abs(value):,.2f}"
    if language == "en":
        number = raw
    else:
        grouping = "\u202f" if language == "uk" else "\u00a0"
        number = raw.replace(",", "\0").replace(".", ",").replace("\0", grouping)
    sign = "−" if value < 0 else ""
    return f"{sign}{number} {currency}".rstrip()


def format_budget_number(value: Decimal, precision: int = 2, locale: str | None = None) -> str:
    """Fava frontend fixed-point values (d3-format / Intl, not transaction money)."""
    precision = max(0, min(precision, 20))
    # Fava sends Decimal strings to the browser and formats ECMAScript numbers.
    # Do not let binary floats enter the accounting calculations themselves.
    binary = float(value)
    if not (-float("inf") < binary < float("inf")):
        return ("-" if value < 0 else "") + ("∞" if locale else "Infinity")
    number = Decimal(str(abs(binary))) if locale else Decimal.from_float(abs(binary))
    with localcontext() as context:
        context.prec = max(50, len(number.as_tuple().digits) + precision + 2)
        rounded = number.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)
        if locale:
            selected = Locale.parse(locale)
            pattern = selected.decimal_formats[None]
            fraction = "." + "0" * precision if precision else ""
            raw = format_decimal(
                rounded,
                format=pattern.pattern.split(".")[0] + fraction,
                locale=selected,
                decimal_quantization=False,
            )
            return ("-" if value.is_signed() else "") + raw
        return ("−" if value < 0 and rounded else "") + f"{rounded:.{precision}f}"


def format_date(value: date, language: str) -> str:
    month = translate(language, f"date_month_{value.month:02}")
    if language not in {"uk", "ru"}:
        return f"{month} {value.day}, {value.year}"
    return f"{value.day} {month} {value.year}"


def format_timestamp(value: str, language: str, zone: str = "system") -> str:
    parsed = datetime.fromisoformat(value)
    return f"{format_date(parsed.date(), language)} · {parsed:%H:%M:%S}"


def date_input(value: date, language: str) -> str:
    return value.strftime("%m/%d/%Y" if language == "en" else "%d.%m.%Y")


def parse_date_input(value: str, language: str) -> date:
    try:
        return datetime.strptime(value, "%m/%d/%Y" if language == "en" else "%d.%m.%Y").date()
    except ValueError as exc:
        raise LedgerError("crud_date_invalid") from exc
