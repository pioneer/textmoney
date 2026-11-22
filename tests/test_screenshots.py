import asyncio
import json
import re
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image, ImageDraw
from playwright.sync_api import Browser, Page

from app.domain.budgets import current_month_summary
from app.infrastructure.desktop_storage import DesktopFolder
from app.journal import FILENAME, Journal
from app.ui.finance import FOLDER_KEY, LANGUAGE_KEY, THEME_KEY, TIMEZONE_KEY
from tools import screenshots


def test_readme_selects_html_and_markdown_screenshots_once(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        '<img src="assets/icon.png">\n'
        '<a href="docs/screenshots/home-desktop.png">'
        '<img src="./docs/screenshots/home-desktop.png"></a>\n'
        "![Expense](docs/screenshots/expense-mobile.png)\n"
        "![Menu][navigation]\n"
        "[navigation]: docs/screenshots/navigation-mobile.png\n"
        '<img src="docs/screenshots/home-desktop.png">\n'
        "![External](https://example.com/image.png)\n"
    )
    assert screenshots.readme_scenes(tmp_path) == list(screenshots.SCENES[:3])
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/user-guide.md").write_text(
        '<img src="screenshots/navigation-mobile.png">\n'
        "![Home](screenshots/home-mobile.png)\n"
        "![Budgets][budgets]\n"
        "[budgets]: screenshots/budgets-desktop.png\n"
        "![Reports](screenshots/reports-desktop.png)\n"
    )
    assert set(screenshots.documentation_scenes(tmp_path)) == set(screenshots.SCENES)
    assert len(screenshots.documentation_scenes(tmp_path)) == len(screenshots.SCENES)


@pytest.mark.parametrize("source", ["screenshots/unknown.png", "screenshots/%2e%2e/private.png"])
def test_unmapped_user_guide_screenshot_is_rejected(tmp_path: Path, source: str) -> None:
    (tmp_path / "README.md").write_text('<img src="docs/screenshots/home-desktop.png">')
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/user-guide.md").write_text(f"![Guide]({source})")
    with pytest.raises(RuntimeError, match="Unmapped user-guide screenshot"):
        screenshots.documentation_scenes(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "docs/screenshots/new-screen.png",
        "docs/screenshots/../assets/icon.png",
        "docs/screenshots/%2e%2e/assets/icon.png",
        "docs/screenshots/home-desktop.png?old=1",
    ],
)
def test_unmapped_screenshot_fails_instead_of_leaving_stale_image(
    tmp_path: Path, source: str
) -> None:
    (tmp_path / "README.md").write_text(f'<img src="{source}">')
    with pytest.raises(RuntimeError, match="Unmapped README screenshot"):
        screenshots.readme_scenes(tmp_path)


def test_readme_without_screenshots_fails(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text('<img src="assets/icon.png">')
    with pytest.raises(RuntimeError, match="no mapped app screenshots"):
        screenshots.readme_scenes(tmp_path)


def test_screenshot_symlink_is_not_overwritten(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text('<img src="docs/screenshots/home-desktop.png">')
    destination = tmp_path / "docs/screenshots"
    destination.mkdir(parents=True)
    original = tmp_path / "private.png"
    original.write_bytes(b"untouched")
    try:
        (destination / "home-desktop.png").symlink_to(original)
    except OSError as exc:
        pytest.skip(f"Symlinks unavailable on this host: {exc}")
    with pytest.raises(RuntimeError, match="symlinks"):
        screenshots.readme_scenes(tmp_path)
    assert original.read_bytes() == b"untouched"


@pytest.mark.parametrize("override,expected", [("", "0.4.9"), ("0.5.0", "0.5.0")])
def test_release_defaults_to_current_or_uses_override(override: str, expected: str) -> None:
    assert screenshots.capture_release("0.4.9", override) == expected


def test_invalid_release_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="stable version"):
        screenshots.capture_release("0.4.9", "not a version")


def test_home_capture_waits_for_merged_balances_button() -> None:
    page = MagicMock(spec=Page)
    page.title.return_value = screenshots.READY

    def text_locator(text: object, **kwargs: object) -> MagicMock:
        if text == "Your balances":
            raise AssertionError("The native button merges its heading and icon labels.")
        return MagicMock()

    page.get_by_text.side_effect = text_locator
    screenshots.prepare_page(page, screenshots.SCENES[0], "0.4.12")
    role = page.get_by_role.call_args
    assert role.args == ("button",)
    assert role.kwargs["name"].search("Your balances Open accounts")
    page.get_by_role.return_value.wait_for.assert_called_once()


@pytest.mark.parametrize("filename", ["budgets-desktop.png", "reports-desktop.png"])
def test_guide_capture_waits_for_its_report_instead_of_home(filename: str) -> None:
    page = MagicMock(spec=Page)
    page.title.return_value = screenshots.READY

    def role_locator(role: str, **kwargs: object) -> MagicMock:
        name = kwargs.get("name")
        if isinstance(name, re.Pattern) and name.search("Your balances"):
            raise AssertionError("A report capture must not wait for Home.")
        return MagicMock()

    page.get_by_role.side_effect = role_locator
    scene = next(scene for scene in screenshots.SCENES if scene.filename == filename)
    screenshots.prepare_page(page, scene, "0.4.18")
    if filename == "budgets-desktop.png":
        page.get_by_role.assert_any_call("button", name="Create budget", exact=True)
        role = page.get_by_role.call_args
        assert role.args == ("group",)
        assert role.kwargs["name"].search("outline-shopping-cart Groceries Consumed: 32.13%")
    else:
        page.get_by_text.assert_any_call("Expenses by category", exact=True)
        page.get_by_text.assert_any_call("Account balances at period end", exact=True)


@pytest.mark.parametrize("count", [0, 2])
def test_invalid_guide_caption_fails_before_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    (tmp_path / "README.md").write_text('<img src="docs/screenshots/home-desktop.png">')
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/user-guide.md").write_text(
        "Screenshots use synthetic data from TextMoney v0.4.9 (build 57).\n" * count
    )
    seed = AsyncMock()
    monkeypatch.setattr(screenshots, "seed_demo", seed)
    with pytest.raises(RuntimeError, match="exactly one screenshot version caption"):
        screenshots.regenerate(tmp_path, tmp_path / "client", "0.4.18")
    seed.assert_not_called()


def test_demo_journal_and_settings_are_isolated_and_show_budget_activity(tmp_path: Path) -> None:
    async def scenario() -> None:
        today = date(2027, 2, 1)
        await screenshots.seed_demo(tmp_path, today)
        preferences = json.loads(
            (tmp_path / "config/textmoney/preferences.json").read_text(encoding="utf-8")
        )
        assert preferences == {
            FOLDER_KEY: str(tmp_path / "journal"),
            LANGUAGE_KEY: "en",
            THEME_KEY: "light",
            TIMEZONE_KEY: "UTC",
        }
        journal = Journal(DesktopFolder(preferences[FOLDER_KEY], FILENAME), today=lambda: today)
        await journal.load()
        assert journal.ready
        assert all(transaction.date <= today for transaction in journal.ledger.transactions)
        expense = next(t for t in journal.ledger.transactions if t.narration == "Weekly groceries")
        assert expense.kind == "expense"
        summary = current_month_summary(journal.ledger, today)
        assert summary.total == 6 and len(summary.overspent) == 1
        assert len(journal.ledger.accounts) == 5

    asyncio.run(scenario())


def painted_png(path: Path, scene: screenshots.Scene) -> None:
    image = Image.new("RGB", (scene.width, scene.height), "white")
    ImageDraw.Draw(image).rectangle((0, 0, scene.width // 2, scene.height), fill="teal")
    image.save(path)


@pytest.mark.parametrize("server_exited", [True, False])
def test_capture_failure_reports_context_and_stops_owned_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server_exited: bool
) -> None:
    process = MagicMock()
    process.poll.return_value = 1 if server_exited else None
    monkeypatch.setattr(screenshots.subprocess, "Popen", MagicMock(return_value=process))
    monkeypatch.setattr(screenshots.socket, "create_connection", MagicMock())
    monkeypatch.setattr(
        screenshots, "prepare_page", MagicMock(side_effect=RuntimeError("client failed"))
    )
    browser = MagicMock(spec=Browser)
    with pytest.raises(RuntimeError, match="Capture failed for home-desktop.png") as error:
        screenshots.capture_scene(browser, tmp_path, tmp_path, {}, screenshots.SCENES[0], "0.5.0")
    assert str(error.value.__cause__) == (
        "Screenshot server exited before startup." if server_exited else "client failed"
    )
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=10)
    process.kill.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["capture", "blank", "wrong-size", "corrupt", "missing", "changed-guide", None]
)
def test_all_captures_validate_before_replacing_documentation_images_and_caption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    readme = '<img src="assets/icon.png">\n' + "\n".join(
        f'<img src="docs/screenshots/{scene.filename}">' for scene in screenshots.SCENES
    )
    (tmp_path / "README.md").write_text(readme)
    destination = tmp_path / "docs/screenshots"
    destination.mkdir(parents=True)
    guide = tmp_path / "docs/user-guide.md"
    guide_text = (
        "# User guide\n\nScreenshots use synthetic data from TextMoney v0.4.9 (build 57), "
        "not a personal journal.\n"
    )
    guide.write_text(guide_text)
    for scene in screenshots.SCENES:
        (destination / scene.filename).write_bytes(b"original")
    unrelated = destination / "unreferenced.png"
    unrelated.write_bytes(b"unrelated")
    monkeypatch.setattr(screenshots, "seed_demo", AsyncMock())
    monkeypatch.setattr(screenshots, "sync_playwright", MagicMock())
    monkeypatch.setattr("app.version.release_identity", lambda: ("0.4.9", 71))
    states: list[Path] = []

    def capture(
        browser: Browser,
        root: Path,
        state: Path,
        environment: dict[str, str],
        scene: screenshots.Scene,
        release: str,
    ) -> None:
        states.append(state)
        assert environment["XDG_CONFIG_HOME"] == environment["APPDATA"] == str(state / "config")
        assert environment["FLET_WEB_PATH"] == str(tmp_path / "client")
        assert release == "0.5.0"
        path = state / scene.filename
        if scene == screenshots.SCENES[1]:
            if failure == "changed-guide":
                guide.write_text(guide_text + "Concurrent edit\n")
            if failure == "capture":
                raise RuntimeError("Capture failed")
            if failure == "missing":
                return
            if failure == "blank":
                Image.new("RGB", (scene.width, scene.height), "white").save(path)
                return
            if failure == "wrong-size":
                Image.new("RGB", (10, 10), "white").save(path)
                return
            if failure == "corrupt":
                path.write_bytes(b"not an image")
                return
        painted_png(path, scene)

    monkeypatch.setattr(screenshots, "capture_scene", capture)
    if failure:
        with pytest.raises((RuntimeError, OSError)):
            screenshots.regenerate(tmp_path, tmp_path / "client", "0.5.0")
        for scene in screenshots.SCENES:
            assert (destination / scene.filename).read_bytes() == b"original"
        assert guide.read_text() == guide_text + (
            "Concurrent edit\n" if failure == "changed-guide" else ""
        )
    else:
        screenshots.regenerate(tmp_path, tmp_path / "client", "0.5.0")
        for scene in screenshots.SCENES:
            screenshots.validate_png(destination / scene.filename, scene)
        assert guide.read_text() == guide_text.replace("v0.4.9 (build 57)", "v0.5.0 (build 71)")
    assert unrelated.read_bytes() == b"unrelated"
    assert (tmp_path / "README.md").read_text() == readme
    assert states and all(not state.exists() for state in states)
