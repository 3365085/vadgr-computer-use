"""Behavior tests for ComputerUseEngine with mocked backends.

Covers the v0.1.0 surface: screenshots, mouse/keyboard, coordinate translation,
platform info. No autonomous mode, no config loading, no Action dataclass.
"""

from unittest.mock import MagicMock, patch

import pytest

from computer_use.core.engine import ComputerUseEngine
from computer_use.core.errors import PlatformNotSupportedError
from computer_use.core.types import Platform, Region, ScreenState
from computer_use.platform.base import AvailabilityReport


@pytest.fixture
def mock_backend():
    backend = MagicMock()
    backend.is_available.return_value = True
    backend.availability_report.return_value = AvailabilityReport(available=True)

    capture = MagicMock()
    capture.capture_full.return_value = ScreenState(
        image_bytes=b"\x89PNG_MOCK",
        width=1920,
        height=1080,
        offset_x=0,
        offset_y=0,
    )
    capture.capture_region.return_value = ScreenState(
        image_bytes=b"\x89PNG_REGION",
        width=200,
        height=100,
    )
    capture.get_screen_size.return_value = (1920, 1080)

    executor = MagicMock()

    backend.get_screen_capture.return_value = capture
    backend.get_action_executor.return_value = executor

    return backend, capture, executor


@pytest.fixture
def engine(mock_backend):
    backend, _, _ = mock_backend
    with patch("computer_use.core.engine.detect_platform", return_value=Platform.LINUX):
        with patch("computer_use.core.engine.get_backend", return_value=backend):
            return ComputerUseEngine()


class TestInit:
    def test_construction_happy_path(self, engine):
        assert engine.get_platform() == Platform.LINUX

    def test_raises_when_backend_unavailable(self, mock_backend):
        backend, _, _ = mock_backend
        backend.is_available.return_value = False
        backend.availability_report.return_value = AvailabilityReport(
            available=False,
            missing=("xdotool",),
            remediation="Install xdotool: apt install xdotool.",
        )
        with patch("computer_use.core.engine.detect_platform", return_value=Platform.LINUX):
            with patch("computer_use.core.engine.get_backend", return_value=backend):
                with pytest.raises(PlatformNotSupportedError) as exc:
                    ComputerUseEngine()
        # Engine surfaces the actionable remediation, not a generic message.
        assert "xdotool" in str(exc.value)
        assert "apt install xdotool" in str(exc.value)


class TestScreenshot:
    def test_screenshot_returns_state(self, engine, mock_backend):
        _, capture, _ = mock_backend
        state = engine.screenshot()
        assert state.width == 1920
        assert state.height == 1080
        capture.capture_full.assert_called_once()

    def test_screenshot_region_passes_region(self, engine, mock_backend):
        _, capture, _ = mock_backend
        engine.screenshot_region(10, 20, 200, 100)
        region = capture.capture_region.call_args[0][0]
        assert isinstance(region, Region)
        assert (region.x, region.y, region.width, region.height) == (10, 20, 200, 100)

    def test_screenshot_updates_virtual_screen_offset(self, engine, mock_backend):
        _, capture, executor = mock_backend
        capture.capture_full.return_value = ScreenState(
            image_bytes=b"", width=1920, height=1080,
            offset_x=-1920, offset_y=0,
        )
        engine.screenshot()
        engine.click(100, 200)
        # click translates through offset: (100-1920, 200+0)
        executor.click.assert_called_once_with(-1820, 200)


class TestMouseActions:
    def test_click_forwards_to_executor(self, engine, mock_backend):
        _, _, executor = mock_backend
        engine.click(500, 300)
        executor.click.assert_called_once_with(500, 300)

    def test_double_click(self, engine, mock_backend):
        _, _, executor = mock_backend
        engine.double_click(50, 60)
        executor.double_click.assert_called_once_with(50, 60)

    def test_right_click(self, engine, mock_backend):
        _, _, executor = mock_backend
        engine.right_click(9, 9)
        executor.click.assert_called_once_with(9, 9, button="right")

    def test_move_mouse(self, engine, mock_backend):
        _, _, executor = mock_backend
        engine.move_mouse(42, 42)
        executor.move_mouse.assert_called_once_with(42, 42)

    def test_scroll(self, engine, mock_backend):
        _, _, executor = mock_backend
        engine.scroll(100, 200, -3)
        executor.scroll.assert_called_once_with(100, 200, -3)

    def test_drag(self, engine, mock_backend):
        _, _, executor = mock_backend
        engine.drag(10, 20, 30, 40, duration=0.25)
        executor.drag.assert_called_once_with(10, 20, 30, 40, 0.25)


class TestKeyboard:
    def test_type_text(self, engine, mock_backend):
        _, _, executor = mock_backend
        engine.type_text("hello")
        executor.type_text.assert_called_once_with("hello")

    def test_key_press(self, engine, mock_backend):
        _, _, executor = mock_backend
        engine.key_press("ctrl", "c")
        executor.key_press.assert_called_once_with(["ctrl", "c"])


class TestPlatformInfo:
    def test_get_screen_size(self, engine):
        assert engine.get_screen_size() == (1920, 1080)

    def test_get_platform_info(self, engine):
        info = engine.get_platform_info()
        assert info["platform"] == "linux"
        assert info["backend_available"] is True
        assert "accessibility" not in info

    def test_get_platform_info_names_the_capture_path(self, engine, mock_backend):
        # After the GNOME Shell extension is installed and the user logged back
        # in, this is where they read that screenshots go through it.
        _, capture, _ = mock_backend
        capture.backend_name = "gnome-shell-extension"
        assert engine.get_platform_info()["capture"] == "gnome-shell-extension"

    def test_capture_name_defaults_to_the_class(self, mock_backend):
        from computer_use.core.screenshot import ScreenCapture

        class PlainCapture(ScreenCapture):
            def capture_full(self):
                raise NotImplementedError

            def capture_region(self, region):
                raise NotImplementedError

            def get_screen_size(self):
                return (1, 1)

            def get_scale_factor(self):
                return 1.0

        assert PlainCapture().backend_name == "PlainCapture"

    def test_get_platform_info_carries_the_structured_block(self, engine, mock_backend):
        # The structured tier's capability is reported, never assumed: a catalogue
        # that lists ui_find where it cannot work is the dead-control rule one
        # layer down, so get_platform_info surfaces the backend's own answer.
        backend, _, _ = mock_backend
        backend.structured_capability.return_value = {
            "backend": "atspi", "available": True, "coordinate_trust": "none",
        }
        info = engine.get_platform_info()
        assert info["structured"]["backend"] == "atspi"
        assert info["structured"]["coordinate_trust"] == "none"


class TestAutonomousSurfaceGone:
    """These symbols were removed when autonomous mode was dropped. If any
    come back unintentionally, fail loudly."""

    def test_engine_has_no_run_task(self, engine):
        assert not hasattr(engine, "run_task")

    def test_engine_has_no_execute_action(self, engine):
        assert not hasattr(engine, "execute_action")

    def test_engine_has_no_provider_attrs(self, engine):
        for attr in ("_get_provider", "_provider", "_provider_name", "_history", "_config"):
            assert not hasattr(engine, attr), f"{attr} leaked back onto engine"

    def test_constructor_rejects_provider_kwarg(self, mock_backend):
        backend, _, _ = mock_backend
        with patch("computer_use.core.engine.detect_platform", return_value=Platform.LINUX):
            with patch("computer_use.core.engine.get_backend", return_value=backend):
                with pytest.raises(TypeError):
                    ComputerUseEngine(provider="anthropic")

    def test_constructor_rejects_config_path_kwarg(self, mock_backend):
        backend, _, _ = mock_backend
        with patch("computer_use.core.engine.detect_platform", return_value=Platform.LINUX):
            with patch("computer_use.core.engine.get_backend", return_value=backend):
                with pytest.raises(TypeError):
                    ComputerUseEngine(config_path="x.yaml")
