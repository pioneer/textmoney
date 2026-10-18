"""Reproducible desktop evidence for parser and native Android feasibility."""

from importlib import import_module
from importlib.metadata import version
from pathlib import Path

from autobean_refactor import models, printer
from autobean_refactor.parser import Parser
from beancount import loader
from beancount_parser.parser import make_parser
from lark.exceptions import UnexpectedInput

FIXTURES = Path(__file__).resolve().parents[1] / "src/app/fixtures/beancount"


def main() -> None:
    import io

    for name in ("beancount-parser", "autobean-refactor", "lark", "beancount"):
        print(f"{name}: {version(name)}")
    native_module = import_module("beancount.parser._parser")
    assert native_module.__file__ is not None
    print(f"Official native parser: {Path(native_module.__file__).name}")
    launch = make_parser()
    lossless = Parser()
    for path in sorted(FIXTURES.glob("*.beancount")):
        text = path.read_text(encoding="utf-8")
        for name, parse in (
            ("LaunchPlatform", lambda text=text: launch.parse(text)),
            ("autobean", lambda text=text: lossless.parse(text, models.File)),
        ):
            try:
                parsed = parse()
            except UnexpectedInput as exc:
                print(f"{path.name}: {name} REJECT at line {exc.line}")
            else:
                print(f"{path.name}: {name} ACCEPT")
                if isinstance(parsed, models.File):
                    assert printer.print_model(parsed, io.StringIO()).getvalue() == text
                    for entry in parsed.raw_directives:
                        if (
                            isinstance(entry, models.Transaction)
                            and entry.meta.get("id") == "split-001"
                        ):
                            print(
                                f"  split: {len(entry.postings)} postings, "
                                f"{len(entry.meta)} metadata, tags={sorted(entry.tags)}"
                            )
    for name in ("main.beancount", "advanced.beancount", "unknown.beancount"):
        entries, errors, _ = loader.load_file(str(FIXTURES / name))
        print(f"Official {name}: {len(entries)} entries, {len(errors)} errors")
        for error in errors:
            print(f"  {type(error).__name__}: {error.message}")


if __name__ == "__main__":
    main()
