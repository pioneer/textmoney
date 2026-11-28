import asyncio
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from flet.controls.services.semantics_service import AccessibilityFeatures
from test_dialog_keyboard import keyboard_session

from app.ui.finance import MOTION_KEY, FinanceView, MotionMode
from app.ui.theme import dark_theme, light_theme


@pytest.fixture
def motion_view(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[FinanceView, ft.Page, MagicMock]]:
    session = keyboard_session()
    page = session.page
    page.theme = light_theme()
    page.dark_theme = dark_theme()
    monkeypatch.setattr(FinanceView, "page", property(lambda _: page))
    view = FinanceView()
    preferences = MagicMock(get=AsyncMock(return_value=None), set=AsyncMock(return_value=True))
    view.preferences = preferences
    yield view, page, preferences


def selected_mode(view: FinanceView) -> MotionMode:
    (selected,) = [item.data for item in view.motion_choice.items if item.checked]
    return selected


def test_system_mode_refreshes_motion_without_removing_native_feedback(
    motion_view: tuple[FinanceView, ft.Page, MagicMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    view, page, preferences = motion_view
    normal = AccessibilityFeatures(False, False, False, False, False, False, False, False)
    reduced = AccessibilityFeatures(False, False, True, False, False, False, False, False)
    ios = AccessibilityFeatures(False, False, False, False, False, True, False, False)
    read = AsyncMock(side_effect=[normal, reduced, ios, normal])
    monkeypatch.setattr(ft.SemanticsService, "get_accessibility_features", read)

    async def scenario() -> None:
        await view._load_motion()
        preferences.get.assert_awaited_once_with(MOTION_KEY)
        assert selected_mode(view) == MotionMode.SYSTEM
        assert page.theme_animation_style is None
        assert view.reveal_duration == 350
        assert await view._refresh_system_motion()
        assert page.theme_animation_style == ft.AnimationStyle.no_animation()
        assert page.theme and page.theme.page_transitions.linux == ft.PageTransitionTheme.NONE
        assert (
            page.dark_theme and page.dark_theme.page_transitions.ios == ft.PageTransitionTheme.NONE
        )
        assert view.overflow_menu.popup_animation_style == ft.AnimationStyle.no_animation()
        assert view.advanced_settings.animation_style == ft.AnimationStyle.no_animation()
        assert all(item.on_click == view._motion_changed for item in view.motion_choice.items)
        assert view.reveal_duration == 0
        assert not await view._refresh_system_motion()
        assert await view._refresh_system_motion()
        assert page.theme_animation_style is None
        assert page.theme and page.theme.page_transitions == ft.PageTransitionsTheme()
        assert view.advanced_settings.animation_style is None
        assert view.reveal_duration == 350

    asyncio.run(scenario())


@pytest.mark.parametrize("mode,duration", [(MotionMode.REDUCED, 0), (MotionMode.FULL, 350)])
def test_explicit_modes_are_saved_and_restored_without_querying_os(
    motion_view: tuple[FinanceView, ft.Page, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    mode: MotionMode,
    duration: int,
) -> None:
    view, _, preferences = motion_view
    view._system_reduces_motion = True
    view._apply_motion()
    read = AsyncMock()
    monkeypatch.setattr(ft.SemanticsService, "get_accessibility_features", read)

    async def scenario() -> None:
        item = next(item for item in view.motion_choice.items if item.data == mode)
        await view._motion_changed(MagicMock(control=item))
        preferences.set.assert_awaited_once_with(MOTION_KEY, mode.value)
        assert view.reveal_duration == duration
        assert selected_mode(view) == mode
        preferences.get.return_value = mode.value
        restored = FinanceView()
        restored.preferences = preferences
        await restored._load_motion()
        assert selected_mode(restored) == mode
        assert restored.reveal_duration == duration
        read.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [None, RuntimeError("preference storage unavailable")])
def test_motion_save_failure_keeps_the_previous_mode(
    motion_view: tuple[FinanceView, ft.Page, MagicMock],
    caplog: pytest.LogCaptureFixture,
    error: Exception | None,
) -> None:
    view, _, preferences = motion_view
    view.motion_mode = MotionMode.REDUCED
    view._apply_motion()
    preferences.set.return_value = False
    preferences.set.side_effect = error
    full = next(item for item in view.motion_choice.items if item.data == MotionMode.FULL)

    asyncio.run(view._motion_changed(MagicMock(control=full)))

    assert selected_mode(view) == MotionMode.REDUCED
    assert view.reveal_duration == 0
    assert view.message_key == "error_motion_save"
    assert "Could not save the motion preference" in caplog.text


def test_system_query_failure_preserves_last_observation_and_recovers(
    motion_view: tuple[FinanceView, ft.Page, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    view, _, preferences = motion_view
    preferences.get.return_value = "unknown-mode"
    read = AsyncMock(
        side_effect=[
            AccessibilityFeatures(False, False, True, False, False, False, False, False),
            TimeoutError(),
            TimeoutError(),
            AccessibilityFeatures(False, False, False, False, False, False, False, False),
        ]
    )
    monkeypatch.setattr(ft.SemanticsService, "get_accessibility_features", read)

    async def scenario() -> None:
        await view._load_motion()
        assert view.motion_mode == MotionMode.SYSTEM
        assert view.reveal_duration == 0
        for _ in range(2):
            assert not await view._refresh_system_motion()
            assert view.reveal_duration == 0
        assert await view._refresh_system_motion()
        assert view.reveal_duration == 350

    asyncio.run(scenario())

    assert "Could not load the saved motion preference" in caplog.text
    assert caplog.text.count("Could not read the system motion preference") == 1
