"""Validate, compile and refresh standard gettext resources."""

import argparse
import ast
import re
import string
import tomllib
from datetime import datetime
from pathlib import Path

from babel.messages.catalog import Catalog, Message
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "locales"
RUNTIME = ROOT / "src/app/locale"
DOMAIN = "textmoney"
LANGUAGE_CODE = re.compile(r"[a-z]{2,3}")


def locale_files(locales_dir: Path = LOCALES) -> dict[str, Path]:
    files = {path.stem: path for path in locales_dir.glob("*.po") if path.name != "messages.pot"}
    if "en" not in files:
        raise ValueError("locales/en.po is required as the English fallback")
    invalid = [language for language in files if not LANGUAGE_CODE.fullmatch(language)]
    if invalid:
        raise ValueError(f"Invalid locale filename(s): {sorted(invalid)}")
    priority = {"en": 0, "uk": 1, "ru": 2}
    return dict(sorted(files.items(), key=lambda item: (priority.get(item[0], 3), item[0])))


def project_version() -> str:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return metadata["project"]["version"]


def new_catalog(language: str | None) -> Catalog:
    return Catalog(
        locale=language,
        domain=DOMAIN,
        header_comment=(
            "# TextMoney gettext catalog.\n"
            "# Stable msgids are translation keys; translate msgstr values only."
        ),
        project="TextMoney",
        version=project_version(),
        msgid_bugs_address="https://github.com/pioneer/textmoney/issues",
        revision_date=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M%z"),
        last_translator="TextMoney localization contributors",
        language_team=f"TextMoney {language or 'language'} translators",
        fuzzy=False,
    )


def read_catalog(path: Path, language: str | None = None) -> Catalog:
    with path.open("rb") as stream:
        return read_po(stream, locale=language, abort_invalid=True)


def message_ids(message: Message) -> tuple[str, ...]:
    if isinstance(message.id, str):
        return (message.id,)
    return tuple(message.id)


def placeholders(value: str) -> set[str]:
    try:
        return {
            field.split(".", 1)[0].split("[", 1)[0]
            for _, field, _, _ in string.Formatter().parse(value)
            if field is not None
        }
    except ValueError as exc:
        raise ValueError(f"Invalid named placeholders in translation: {value!r}") from exc


def validate_catalogs(locales_dir: Path = LOCALES) -> dict[str, Catalog]:
    files = locale_files(locales_dir)
    catalogs = {language: read_catalog(path, language) for language, path in files.items()}
    baseline = catalogs["en"]
    expected = {(message.context, message_ids(message)) for message in baseline if message.id}
    for language, catalog in catalogs.items():
        headers = dict(catalog.mime_headers)
        if catalog.locale is None or str(catalog.locale) != language:
            raise ValueError(f"{language}.po has incorrect Language metadata")
        if headers.get("Language") != language or headers.get("Content-Type") != (
            "text/plain; charset=utf-8"
        ):
            raise ValueError(f"{language}.po has invalid gettext headers")
        if catalog.num_plurals < 1:
            raise ValueError(f"{language}.po must define at least one plural form")
        actual = {(message.context, message_ids(message)) for message in catalog if message.id}
        if actual != expected:
            missing, extra = expected - actual, actual - expected
            raise ValueError(f"{language}.po key mismatch; missing={missing}, extra={extra}")
        for message in catalog:
            if not message.id:
                continue
            english = baseline.get(message.id, context=message.context)
            if english is None:
                raise ValueError(f"{language}.po contains an unknown message {message.id!r}")
            translated_values = (
                message.string if isinstance(message.string, tuple) else (message.string,)
            )
            english_values = (
                english.string if isinstance(english.string, tuple) else (english.string,)
            )
            if language == "en" and any(not value for value in english_values):
                raise ValueError(f"en.po contains an empty English translation for {message.id!r}")
            if isinstance(message.id, tuple) and len(translated_values) != catalog.num_plurals:
                raise ValueError(
                    f"{language}.po plural {message.id[0]!r} needs "
                    f"{catalog.num_plurals} msgstr forms"
                )
            source_placeholders = set().union(
                *(placeholders(value) for value in english_values if isinstance(value, str))
            )
            for translated in translated_values:
                if not translated:
                    continue
                if not isinstance(translated, str):
                    raise ValueError(f"{language}.po has an invalid translation for {message.id!r}")
                if placeholders(translated) != source_placeholders:
                    raise ValueError(
                        f"{language}.po placeholder mismatch for {message.id!r}: "
                        f"expected {source_placeholders}, got {placeholders(translated)}"
                    )
    template = read_catalog(locales_dir / "messages.pot")
    template_keys = {(message.context, message_ids(message)) for message in template if message.id}
    if template_keys != expected:
        raise ValueError("messages.pot is stale; run `uv run inv translations-template`")
    return catalogs


def compile_catalogs(
    locales_dir: Path = LOCALES,
    runtime_dir: Path = RUNTIME,
) -> None:
    catalogs = validate_catalogs(locales_dir)
    for language, catalog in catalogs.items():
        destination = runtime_dir / language / "LC_MESSAGES" / f"{DOMAIN}.mo"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as stream:
            write_mo(stream, catalog)


def update_template() -> None:
    english = read_catalog(LOCALES / "en.po", "en")
    template = new_catalog(None)
    for message in english:
        if not message.id:
            continue
        ids = message.id
        template.add(
            ids,
            None,
            context=message.context,
            auto_comments=message.auto_comments,
            locations=message.locations,
            flags=message.flags,
        )
    destination = LOCALES / "messages.pot"
    with destination.open("wb") as stream:
        write_po(stream, template, sort_output=False)


def initialize_catalog(language: str) -> None:
    if not LANGUAGE_CODE.fullmatch(language) or language == "en":
        raise ValueError(f"Invalid new locale code: {language!r}")
    destination = LOCALES / f"{language}.po"
    if destination.exists():
        raise FileExistsError(f"Translation catalog already exists: {destination.name}")
    template = read_catalog(LOCALES / "messages.pot")
    catalog = new_catalog(language)
    for message in template:
        if message.id:
            catalog.add(
                message.id,
                None,
                context=message.context,
                auto_comments=message.auto_comments,
                locations=message.locations,
                flags=message.flags,
            )
    with destination.open("wb") as stream:
        write_po(stream, catalog, sort_output=False)


def check_python_keys(catalog: Catalog) -> None:
    keys = {key for message in catalog if message.id for key in message_ids(message)}
    referenced: set[str] = set()
    for path in (ROOT / "src/app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            if function_name == "tr" and node.args and isinstance(node.args[0], ast.Constant):
                if isinstance(node.args[0].value, str):
                    referenced.add(node.args[0].value)
            elif (
                function_name == "translate"
                and len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                referenced.add(node.args[1].value)
            elif (
                function_name == "translate_plural"
                and len(node.args) > 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[2], ast.Constant)
                and isinstance(node.args[1].value, str)
                and isinstance(node.args[2].value, str)
            ):
                referenced.update((node.args[1].value, node.args[2].value))
    for node in ast.walk(ast.parse((ROOT / "src/app/localization.py").read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_ERROR_KEYS"
                for target in node.targets
            )
            and isinstance(node.value, ast.Dict)
        ):
            referenced.update(
                value.value
                for value in node.value.values
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            )
    missing = {f"ui.{key}" for key in referenced} - keys
    if missing:
        raise ValueError(f"Python references missing English PO keys: {sorted(missing)}")


def validate() -> None:
    catalogs = validate_catalogs()
    check_python_keys(catalogs["en"])
    print(f"Validated {len(catalogs['en']) - 1} gettext messages in {len(catalogs)} catalogs.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate", "compile", "template", "init"))
    parser.add_argument("language", nargs="?")
    arguments = parser.parse_args()
    command = arguments.command
    if command == "validate":
        validate()
    elif command == "compile":
        compile_catalogs()
    elif command == "template":
        update_template()
    elif arguments.language:
        initialize_catalog(arguments.language)
    else:
        parser.error("init requires a locale code")


if __name__ == "__main__":
    main()
