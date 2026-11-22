"""Documentation screenshot capture against the real, isolated dynamic web app."""

import argparse
import asyncio
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageStat
from playwright.sync_api import Browser, Page, sync_playwright

from tools.release import version_numbers

ROOT = Path(__file__).resolve().parents[1]
READY = "TextMoney screenshot ready"


@dataclass(frozen=True)
class Scene:
    filename: str
    width: int
    height: int


SCENES = (
    Scene("home-desktop.png", 1580, 1050),
    Scene("expense-mobile.png", 415, 900),
    Scene("navigation-mobile.png", 415, 900),
    Scene("home-mobile.png", 415, 1400),
    Scene("budgets-desktop.png", 1580, 1500),
    Scene("reports-desktop.png", 1366, 1380),
)


class DocumentImages(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            self.sources.extend(value for key, value in attrs if key == "src" and value)


def _document_scenes(root: Path, document: Path) -> list[Scene]:
    text = (root / document).read_text(encoding="utf-8")
    parser = DocumentImages()
    parser.feed(text)
    sources = parser.sources + re.findall(r"!\[[^\]]*\]\(\s*<?([^\s)>]+)>?", text)
    references = dict(re.findall(r"(?m)^\s*\[([^\]]+)\]:\s*<?([^\s>]+)>?", text))
    for alt, key in re.findall(r"!\[([^\]]*)\]\[([^\]]*)\]", text):
        if (key or alt) not in references:
            raise RuntimeError(f"Unresolved {document.stem} image reference: {key or alt}")
        sources.append(references[key or alt])
    selected: list[Scene] = []
    known = {scene.filename: scene for scene in SCENES}
    prefix = (
        Path(os.path.relpath(root / "docs/screenshots", (root / document).parent)).as_posix() + "/"
    )
    for source in sources:
        url = urlsplit(source)
        if url.scheme or url.netloc:
            continue
        path = unquote(url.path).removeprefix("./")
        if not path.startswith(prefix):
            continue
        filename = path.removeprefix(prefix)
        if filename not in known or url.query or url.fragment:
            raise RuntimeError(f"Unmapped {document.stem} screenshot: {source}")
        destination = root / document.parent / path
        if destination.resolve() != root.resolve() / "docs/screenshots" / filename:
            raise RuntimeError(f"{document.stem} screenshot must not use symlinks: {source}")
        if known[filename] not in selected:
            selected.append(known[filename])
    return selected


def readme_scenes(root: Path) -> list[Scene]:
    scenes = _document_scenes(root, Path("README.md"))
    if not scenes:
        raise RuntimeError("README contains no mapped app screenshots in docs/screenshots/.")
    return scenes


def documentation_scenes(root: Path) -> list[Scene]:
    return list(
        dict.fromkeys([*readme_scenes(root), *_document_scenes(root, Path("docs/user-guide.md"))])
    )


def capture_release(current: str, override: str) -> str:
    version = override or current
    version_numbers(version)
    return version


async def seed_demo(state: Path, today: date) -> None:
    from app.icons import IconStore
    from app.infrastructure.desktop_storage import DesktopFolder
    from app.infrastructure.icon_files import DesktopIconFiles
    from app.infrastructure.icon_sources import bundled_icons
    from app.journal import FILENAME, Journal
    from app.ui.finance import FOLDER_KEY, LANGUAGE_KEY, THEME_KEY, TIMEZONE_KEY

    folder = state / "journal"
    folder.mkdir()
    month = today.replace(day=1).isoformat()
    source = f"""option "operating_currency" "EUR"
option "operating_currency" "UAH"
2000-01-01 open Assets:Cash EUR
    name: "Cash"
2000-01-01 open Assets:Card EUR
    name: "Everyday card"
2000-01-01 open Assets:Savings EUR
    name: "Savings"
2000-01-01 open Assets:Travel UAH
    name: "Travel fund"
2000-01-01 open Liabilities:CreditCard EUR
    name: "Credit card"
2000-01-01 open Equity:Opening-Balances
2000-01-01 open Expenses:Groceries
    name: "Groceries"
2000-01-01 open Expenses:Cafes
    name: "Cafes"
2000-01-01 open Expenses:Home
    name: "Home"
2000-01-01 open Expenses:Transport
    name: "Transport"
2000-01-01 open Expenses:Subscriptions
    name: "Subscriptions"
2000-01-01 open Expenses:Health
    name: "Health"
2000-01-01 open Income:Salary
    name: "Salary"
{month} custom "budget" Expenses:Groceries "monthly" 200.00 EUR
{month} custom "budget" Expenses:Cafes "monthly" 50.00 EUR
{month} custom "budget" Expenses:Home "monthly" 600.00 EUR
{month} custom "budget" Expenses:Transport "monthly" 80.00 EUR
{month} custom "budget" Expenses:Subscriptions "monthly" 30.00 EUR
{month} custom "budget" Expenses:Health "monthly" 100.00 EUR
{month} * "Opening balances"
    Assets:Cash 1300.00 EUR
    Assets:Card 5000.00 EUR
    Assets:Savings 2400.00 EUR
    Liabilities:CreditCard -200.00 EUR
    Equity:Opening-Balances -8500.00 EUR
{month} * "Travel fund opening balance"
    Assets:Travel 10000.00 UAH
    Equity:Opening-Balances -10000.00 UAH
{month} * "Home rent"
    time: "09:00"
    Expenses:Home 660.00 EUR
    Assets:Card -660.00 EUR
{month} * "Monthly travel pass"
    time: "10:00"
    Expenses:Transport 40.00 EUR
    Assets:Card -40.00 EUR
{month} * "Music subscription"
    time: "11:00"
    Expenses:Subscriptions 9.99 EUR
    Assets:Card -9.99 EUR
{month} * "Credit card repayment"
    time: "12:00"
    Assets:Card -100.00 EUR
    Liabilities:CreditCard 100.00 EUR
{month} * "Lunch with friends"
    time: "13:00"
    Expenses:Cafes 32.00 EUR
    Liabilities:CreditCard -32.00 EUR
{month} * "Morning coffee"
    time: "14:00"
    Expenses:Cafes 7.50 EUR
    Assets:Cash -7.50 EUR
{month} * "Local market" "Weekly groceries"
    time: "17:45"
    Expenses:Groceries 64.25 EUR
    Assets:Card -64.25 EUR
"""
    (folder / FILENAME).write_text(source, encoding="utf-8")
    adapter = DesktopFolder(str(folder), FILENAME)
    journal = Journal(adapter, today=lambda: today)
    await journal.load()
    icons = IconStore(DesktopIconFiles(adapter))
    await icons.load()
    assignments = {
        "Assets:Cash": "outline-payments",
        "Assets:Card": "outline-credit-card",
        "Assets:Savings": "outline-savings",
        "Assets:Travel": "outline-flight",
        "Liabilities:CreditCard": "outline-account-balance",
        "Expenses:Groceries": "outline-shopping-cart",
        "Expenses:Cafes": "outline-local-cafe",
        "Expenses:Home": "outline-home",
        "Expenses:Transport": "outline-train",
        "Expenses:Subscriptions": "outline-movie",
        "Expenses:Health": "outline-local-hospital",
    }
    catalogue = {icon.label: icon for icon in bundled_icons()}
    for identifier, name in assignments.items():
        await icons.assign(identifier, catalogue[name], ())
    preferences = state / "config/textmoney/preferences.json"
    preferences.parent.mkdir(parents=True)
    preferences.write_text(
        json.dumps(
            {
                FOLDER_KEY: str(folder),
                LANGUAGE_KEY: "en",
                THEME_KEY: "light",
                TIMEZONE_KEY: "UTC",
            }
        ),
        encoding="utf-8",
    )


def serve(scene: Scene, release: str, port: int) -> None:
    import flet as ft

    from app.application import main as app_main
    from app.ui.finance import FinanceView
    from app.version import release_identity

    async def main(page: ft.Page) -> None:
        try:
            await app_main(page)
            safe_area = page.controls[0]
            if not isinstance(safe_area, ft.SafeArea) or not isinstance(
                safe_area.content, FinanceView
            ):
                raise RuntimeError("Application root changed; update the screenshot driver.")
            view = safe_area.content
            async with asyncio.timeout(45):
                while not view.startup_complete:
                    await asyncio.sleep(0.05)
                if view.error_message or view.journal is None or not view.journal.ready:
                    raise RuntimeError("Demo journal startup failed.")
                while view.home_budgets.summary is None:
                    if view.home_budgets.failed:
                        raise RuntimeError("Demo budget summary failed.")
                    await asyncio.sleep(0.05)
            if scene.filename == "expense-mobile.png":
                view._navigate("transactions")
                transaction = next(
                    item
                    for item in view.journal.ledger.transactions
                    if item.narration == "Weekly groceries"
                )
                view._transaction_dialog(view.journal, transaction)
            elif scene.filename == "budgets-desktop.png":
                view._navigate("budgets")
                if view.budgets.worker is None:
                    raise RuntimeError("Demo budget refresh did not start.")
                async with asyncio.timeout(45):
                    await asyncio.wrap_future(view.budgets.worker)
                if view.budgets.error.visible or len(view.budgets.report_rows) != 6:
                    raise RuntimeError("Demo budget report failed.")
            elif scene.filename == "reports-desktop.png":
                view._navigate("reports")
            view.release_label.value = f"v{release} ({release_identity()[1]})"
            page.title = READY
            page.update()
        except Exception:
            logging.exception("Screenshot app initialization failed")
            page.title = "TextMoney screenshot failed"
            page.update()
            raise

    ft.run(main, host="127.0.0.1", port=port, view=None, assets_dir=str(ROOT / "assets"))


def validate_png(path: Path, scene: Scene) -> None:
    with Image.open(path) as image:
        image.load()
        if image.format != "PNG" or image.size != (scene.width, scene.height):
            raise RuntimeError(f"Invalid screenshot format or dimensions: {scene.filename}")
        if max(ImageStat.Stat(image.convert("RGB")).stddev) < 10:
            raise RuntimeError(f"Screenshot appears blank: {scene.filename}")


def prepare_page(page: Page, scene: Scene, release: str) -> None:
    page.wait_for_function(
        "() => document.title.startsWith('TextMoney screenshot ')", timeout=60000
    )
    if page.title() != READY:
        raise RuntimeError(f"App initialization failed for {scene.filename}")
    page.locator("flt-semantics-placeholder").evaluate("(element) => element.click()")
    if scene.filename == "expense-mobile.png":
        page.get_by_role("button", name="Save changes", exact=True).wait_for()
        page.get_by_role("textbox", name="Description").wait_for()
    else:
        page.get_by_text(re.compile(rf"^v{re.escape(release)} \(\d+\)$")).wait_for()
        if scene.filename == "budgets-desktop.png":
            page.get_by_role("button", name="Create budget", exact=True).wait_for()
            page.get_by_role("group", name=re.compile(r"\bGroceries\b.*Consumed:")).wait_for()
        elif scene.filename == "reports-desktop.png":
            page.get_by_text("Expenses by category", exact=True).wait_for()
            page.get_by_text("Account balances at period end", exact=True).wait_for()
        elif scene.filename == "navigation-mobile.png":
            page.get_by_role("button", name=re.compile(r"^Your balances\b")).wait_for()
            page.get_by_role("button", name="More", exact=True).click()
            page.get_by_role("menuitem", name="Accounts", exact=True).wait_for()
            page.get_by_role("menuitem", name="Categories", exact=True).wait_for()
            page.get_by_role("menuitem", name="Settings", exact=True).wait_for()
        else:
            page.get_by_role("button", name=re.compile(r"^Your balances\b")).wait_for()
            page.get_by_text("Overspent: 1 of 6 budgets this month", exact=True).wait_for()
    page.mouse.move(0, 0)
    # Flutter's semantics can precede the final painted frame and menu animation.
    page.wait_for_timeout(750)


def capture_scene(
    browser: Browser,
    root: Path,
    state: Path,
    environment: dict[str, str],
    scene: Scene,
    release: str,
) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log = state / "server.log"
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.screenshots",
                "--serve",
                scene.filename,
                "--release",
                release,
                "--port",
                str(port),
            ],
            cwd=root,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Screenshot server exited before startup.")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Screenshot server did not start.") from None
                    time.sleep(0.1)
            with browser.new_context(
                viewport={"width": scene.width, "height": scene.height},
                device_scale_factor=1,
                locale="en-US",
                timezone_id="UTC",
                color_scheme="light",
            ) as context:
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
                prepare_page(page, scene, release)
                page.screenshot(path=str(state / scene.filename))
        except Exception as exc:
            raise RuntimeError(
                f"Capture failed for {scene.filename}. No documentation screenshots replaced.\n"
                f"{log.read_text(encoding='utf-8')}"
            ) from exc
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def regenerate(root: Path, client: Path, release: str) -> None:
    from app.version import release_identity

    scenes = documentation_scenes(root)
    guide = root / "docs/user-guide.md"
    if guide.resolve() != root.resolve() / "docs/user-guide.md":
        raise RuntimeError("The user guide must not use symlinks.")
    guide_text = guide.read_text(encoding="utf-8")
    updated_guide, count = re.subn(
        r"(?<=Screenshots use synthetic data from TextMoney )v\d+\.\d+\.\d+ \(build \d+\)",
        f"v{release} (build {release_identity()[1]})",
        guide_text,
    )
    if count != 1:
        raise RuntimeError("The user guide must contain exactly one screenshot version caption.")
    with TemporaryDirectory(prefix="textmoney-screenshots-") as directory:
        state = Path(directory)
        asyncio.run(seed_demo(state, datetime.now(UTC).date()))
        environment = os.environ | {
            "PYTHONPATH": str(root / "src") + os.pathsep + str(root),
            "XDG_CONFIG_HOME": str(state / "config"),
            "APPDATA": str(state / "config"),
            "FLET_WEB_PATH": str(client),
            "FLET_FORCE_WEB_SERVER": "true",
            "FLET_SERVER_IP": "127.0.0.1",
            "FLET_SERVER_PORT": "",
            "APP_DEBUG": "0",
        }
        with sync_playwright() as playwright, playwright.chromium.launch() as browser:
            for scene in scenes:
                capture_scene(browser, root, state, environment, scene, release)
                validate_png(state / scene.filename, scene)
        if guide.read_text(encoding="utf-8") != guide_text:
            raise RuntimeError("The user guide changed during capture; no screenshots replaced.")
        for scene in scenes:
            destination = root / "docs/screenshots" / scene.filename
            destination.write_bytes((state / scene.filename).read_bytes())
            print(f"Updated {destination.relative_to(root)} (v{release})")
        if updated_guide != guide_text:
            guide.write_text(updated_guide, encoding="utf-8")
            print(f"Updated docs/user-guide.md screenshot version (v{release})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", choices=[scene.filename for scene in SCENES])
    parser.add_argument("--release", required=True)
    parser.add_argument("--client", type=Path)
    parser.add_argument("--port", type=int, default=0)
    arguments = parser.parse_args()
    version_numbers(arguments.release)
    if arguments.serve:
        serve(
            next(scene for scene in SCENES if scene.filename == arguments.serve),
            arguments.release,
            arguments.port,
        )
    elif arguments.client:
        regenerate(ROOT, arguments.client, arguments.release)
    else:
        parser.error("--client is required for capture")
