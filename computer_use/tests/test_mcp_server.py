"""Tests for the MCP server tool wrappers."""

import asyncio
import io
import threading
import time
from unittest.mock import MagicMock

import pytest
from PIL import Image as PILImage

from computer_use.core.types import Platform, ScreenState

pytestmark = pytest.mark.usefixtures("schema_six_typing_runtime")


def _make_png(width: int, height: int) -> bytes:
    """Create a minimal valid PNG of the given size."""
    img = PILImage.new("RGB", (width, height), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.screenshot.return_value = ScreenState(
        image_bytes=_make_png(1920, 1080),
        width=1920,
        height=1080,
    )
    engine.screenshot_region.return_value = ScreenState(
        image_bytes=_make_png(200, 100),
        width=200,
        height=100,
    )
    engine.get_screen_size.return_value = (1920, 1080)
    engine.get_platform.return_value = Platform.WSL2
    engine.get_platform_info.return_value = {
        "platform": "wsl2",
        "backend_available": True,
    }
    return engine


@pytest.fixture(autouse=True)
def _patch_engine(mock_engine):
    import computer_use.mcp_server as mod

    old_max_width = mod._MAX_WIDTH
    mod._engine = mock_engine
    mod._MAX_WIDTH = 1024
    mod._scale_x = 1.0
    mod._scale_y = 1.0
    mod._display_w = 0
    mod._display_h = 0
    mod._offset_x = 0
    mod._offset_y = 0
    yield
    mod._engine = None
    mod._MAX_WIDTH = old_max_width
    mod._scale_x = 1.0
    mod._scale_y = 1.0
    mod._offset_x = 0
    mod._offset_y = 0


class TestScreenshotTools:
    def test_screenshot_returns_image(self, mock_engine):
        from mcp.server.mcpserver import Image

        from computer_use.mcp_server import screenshot

        result = screenshot()
        assert isinstance(result, Image)
        mock_engine.screenshot.assert_called_once()

    def test_screenshot_downscales(self, mock_engine):
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import screenshot

        mod._MAX_WIDTH = 1366
        screenshot()
        assert mod._scale_x > 1.0  # 1920/1366 ~ 1.41
        assert mod._display_w == 1366

    def test_screenshot_no_downscale_when_small(self, mock_engine):
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import screenshot

        mock_engine.screenshot.return_value = ScreenState(
            image_bytes=_make_png(1024, 768),
            width=1024,
            height=768,
        )
        mod._MAX_WIDTH = 1366
        screenshot()
        assert mod._scale_x == 1.0
        assert mod._scale_y == 1.0

    def test_screenshot_stores_offset(self, mock_engine):
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import screenshot

        mock_engine.screenshot.return_value = ScreenState(
            image_bytes=_make_png(1920, 1080),
            width=1920,
            height=1080,
            offset_x=100,
            offset_y=50,
        )
        mod._MAX_WIDTH = 1920
        screenshot()
        assert mod._offset_x == 100
        assert mod._offset_y == 50

    def test_screenshot_region_returns_image(self, mock_engine):
        from mcp.server.mcpserver import Image

        from computer_use.mcp_server import screenshot_region

        result = screenshot_region(10, 20, 200, 100)
        assert isinstance(result, Image)


class TestCoordinateScaling:
    """Action tools scale display coords back to real screen coords."""

    def _set_scale(self, sx, sy, ox=0, oy=0):
        import computer_use.mcp_server as mod

        mod._scale_x = sx
        mod._scale_y = sy
        mod._offset_x = ox
        mod._offset_y = oy

    def test_click_scales_coords(self, mock_engine):
        from computer_use.mcp_server import click

        self._set_scale(2.0, 2.0)
        click(100, 200)
        mock_engine.click.assert_called_once_with(200, 400)

    def test_click_with_offset(self, mock_engine):
        from computer_use.mcp_server import click

        self._set_scale(2.0, 2.0, ox=100, oy=50)
        click(100, 200)
        mock_engine.click.assert_called_once_with(300, 450)

    def test_double_click_scales_coords(self, mock_engine):
        from computer_use.mcp_server import double_click

        self._set_scale(3.0, 3.0)
        double_click(10, 20)
        mock_engine.double_click.assert_called_once_with(30, 60)

    def test_right_click_scales_coords(self, mock_engine):
        from computer_use.mcp_server import right_click

        self._set_scale(2.0, 2.0)
        right_click(50, 75)
        mock_engine.right_click.assert_called_once_with(100, 150)

    def test_move_mouse_scales_coords(self, mock_engine):
        from computer_use.mcp_server import move_mouse

        self._set_scale(2.0, 2.0)
        move_mouse(300, 400)
        mock_engine.move_mouse.assert_called_once_with(600, 800)

    def test_scroll_scales_coords(self, mock_engine):
        from computer_use.mcp_server import scroll

        self._set_scale(2.0, 2.0)
        scroll(100, 200, -3)
        mock_engine.scroll.assert_called_once_with(200, 400, -3)

    def test_drag_scales_both_endpoints(self, mock_engine):
        from computer_use.mcp_server import drag

        self._set_scale(2.0, 2.0)
        drag(10, 20, 100, 200, 0.5)
        mock_engine.drag.assert_called_once_with(20, 40, 200, 400, 0.5)

    def test_drag_with_offset(self, mock_engine):
        from computer_use.mcp_server import drag

        self._set_scale(1.0, 1.0, ox=200, oy=100)
        drag(10, 20, 100, 200, 0.5)
        mock_engine.drag.assert_called_once_with(210, 120, 300, 300, 0.5)

    def test_no_scaling_at_1x(self, mock_engine):
        from computer_use.mcp_server import click

        self._set_scale(1.0, 1.0)
        click(500, 300)
        mock_engine.click.assert_called_once_with(500, 300)


class TestMouseTools:
    def test_click_returns_display_coords(self, mock_engine):
        from computer_use.mcp_server import click

        result = click(500, 300)
        assert "500" in result and "300" in result

    def test_scroll_up(self, mock_engine):
        from computer_use.mcp_server import scroll

        result = scroll(100, 200, 3)
        assert "up" in result

    def test_scroll_down(self, mock_engine):
        from computer_use.mcp_server import scroll

        result = scroll(100, 200, -3)
        assert "down" in result


class TestKeyboardTools:
    @pytest.mark.asyncio
    async def test_type_text(self, mock_engine):
        from computer_use.mcp_server import type_text

        result = await type_text("hello world")
        assert result["human"] is False
        assert result["units"] == 11
        assert "hello world" not in str(result)
        mock_engine.type_text.assert_called_once_with("hello world")

    @pytest.mark.asyncio
    async def test_type_text_does_not_return_input_text(self, mock_engine):
        from computer_use.mcp_server import type_text

        long_text = "a" * 100
        result = await type_text(long_text)
        assert result["units"] == 100
        assert long_text not in str(result)

    @pytest.mark.asyncio
    async def test_human_type_cancellation_returns_truthful_completed_units(self, mock_engine):
        from mcp.server.mcpserver.exceptions import ToolError

        from computer_use.core.typing import TypingCancelled
        from computer_use.mcp_server import type_text

        def cancellable(_text, _plan, *, cancelled):
            while not cancelled():
                time.sleep(0.005)
            raise TypingCancelled(2)

        mock_engine.type_text.side_effect = cancellable
        task = asyncio.create_task(type_text("hello", human=True))
        await asyncio.sleep(0.03)
        task.cancel()

        with pytest.raises(ToolError, match="typing_cancelled: 2 complete units"):
            await task

    @pytest.mark.asyncio
    async def test_cooperative_worker_success_does_not_swallow_task_cancellation(self):
        import computer_use.mcp_server as mod

        cancelled = threading.Event()

        def return_after_cancel():
            while not cancelled.is_set():
                time.sleep(0.005)
            return "late success"

        task = asyncio.create_task(mod._run_cooperative_thread(return_after_cancel, cancelled))
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_human_type_has_no_implicit_total_deadline(self, mock_engine):
        from computer_use.mcp_server import type_text

        mock_engine.type_text.return_value = 0
        result = await type_text("a" * 400, human=True, wpm=10, iki_cv=0)
        assert result["predicted_ms"] > 60_000
        assert result["units"] == 400

    @pytest.mark.asyncio
    async def test_human_type_explicit_deadline_refuses_before_input(self, mock_engine):
        from mcp.server.mcpserver.exceptions import ToolError

        from computer_use.mcp_server import type_text

        with pytest.raises(ToolError, match="typing_deadline_exceeded: 0 complete units"):
            await type_text("abcdef", human=True, wpm=10, iki_cv=0, timeout=100)
        mock_engine.type_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_human_type_runtime_deadline_is_a_named_tool_error(self, mock_engine):
        from mcp.server.mcpserver.exceptions import ToolError

        from computer_use.core.typing import TypingDeadlineExceeded
        from computer_use.mcp_server import type_text

        mock_engine.type_text.side_effect = TypingDeadlineExceeded(3)
        with pytest.raises(ToolError, match="typing_deadline_exceeded: 3 complete units"):
            await type_text("abcdef", human=True, wpm=200, iki_cv=0, timeout=10_000)

    def test_key_press_single(self, mock_engine):
        from computer_use.mcp_server import key_press

        result = key_press("enter")
        assert "enter" in result
        mock_engine.key_press.assert_called_once_with("enter")

    def test_key_press_combo(self, mock_engine):
        from computer_use.mcp_server import key_press

        key_press("ctrl+c")
        mock_engine.key_press.assert_called_once_with("ctrl", "c")

    def test_key_press_triple(self, mock_engine):
        from computer_use.mcp_server import key_press

        key_press("ctrl+shift+s")
        mock_engine.key_press.assert_called_once_with("ctrl", "shift", "s")


class TestBrowserCancellation:
    @pytest.mark.asyncio
    async def test_browser_forwards_explicit_trusted_click(self, monkeypatch):
        from computer_use.mcp_server import browser

        captured = {}

        def trusted_click(*, op, **params):
            captured.update(op=op, **params)
            return {"clicked": True, "via": "cdp"}

        monkeypatch.setattr("computer_use.mcp_server._browser_impl.browser", trusted_click)

        result = await browser(op="click", selector="#open-popup", trusted=True)

        assert result == {"clicked": True, "via": "cdp"}
        assert captured["op"] == "click"
        assert captured["selector"] == "#open-popup"
        assert captured["trusted"] is True

    @pytest.mark.asyncio
    async def test_human_browser_cancel_returns_named_progress(self, monkeypatch):
        from computer_use.browser.protocol import BrowserError, BrowserErrorCode
        from computer_use.mcp_server import browser

        def cancellable(*, op, **params):
            assert op == "type"
            while not params["_cancelled"]():
                time.sleep(0.005)
            raise BrowserError(
                BrowserErrorCode.TYPING_CANCELLED,
                "typing_cancelled: 3 complete units",
            )

        monkeypatch.setattr("computer_use.mcp_server._browser_impl.browser", cancellable)
        task = asyncio.create_task(browser(op="type", selector="#field", text="hello", human=True))
        await asyncio.sleep(0.03)
        task.cancel()

        with pytest.raises(BrowserError) as captured:
            await task
        assert captured.value.code == BrowserErrorCode.TYPING_CANCELLED
        assert "3 complete units" in captured.value.message


class TestInfoTools:
    def test_get_screen_size_returns_display_dims_before_screenshot(self, mock_engine):
        """Before any screenshot, get_screen_size computes display dims from _MAX_WIDTH."""
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import get_screen_size

        # Fixture sets _MAX_WIDTH=1024, engine returns (1920,1080).
        # Display should be 1024 x int(1080 * 1024/1920) = 1024x576
        mod._display_w = 0
        mod._display_h = 0
        result = get_screen_size()
        assert result == "1024x576"

    def test_get_screen_size_returns_display_dims_after_screenshot(self, mock_engine):
        """After screenshot(), get_screen_size returns the downscaled dimensions."""
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import get_screen_size, screenshot

        # Engine returns 1920x1080, _MAX_WIDTH=1024 -> downscale to 1024x576
        screenshot()
        result = get_screen_size()
        assert result == f"{mod._display_w}x{mod._display_h}"
        assert mod._display_w == 1024

    def test_get_screen_size_no_downscale_when_small(self, mock_engine):
        """If the real screen is smaller than _MAX_WIDTH, return real size."""
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import get_screen_size

        mod._display_w = 0
        mod._MAX_WIDTH = 1366
        mock_engine.get_screen_size.return_value = (1024, 768)
        result = get_screen_size()
        assert result == "1024x768"

    def test_get_platform_does_not_require_an_available_backend(self, monkeypatch):
        import computer_use.mcp_server as mod

        monkeypatch.setattr(mod, "detect_platform", lambda: Platform.WSL2)
        monkeypatch.setattr(
            mod,
            "_get_engine",
            MagicMock(side_effect=AssertionError("backend must not be constructed")),
        )

        result = mod.get_platform()
        assert result == "wsl2"

    def test_get_platform_info(self, mock_engine):
        from computer_use.mcp_server import get_platform_info

        result = get_platform_info()
        assert result["platform"] == "wsl2"
        assert result["backend_available"] is True


class TestScreenshotRegionScalePreservation:
    """Regression tests: screenshot_region must NOT clobber the global scale state."""

    def test_region_preserves_scale_factors(self, mock_engine):
        """After screenshot() sets scale, screenshot_region() must not reset it."""
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import screenshot, screenshot_region

        screenshot()
        scale_x_before = mod._scale_x
        scale_y_before = mod._scale_y
        assert scale_x_before > 1.0, "Precondition: screenshot should set scale > 1"

        screenshot_region(100, 200, 50, 30)

        assert mod._scale_x == scale_x_before
        assert mod._scale_y == scale_y_before

    def test_region_preserves_display_dims(self, mock_engine):
        """screenshot_region() must not change _display_w/_display_h."""
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import screenshot, screenshot_region

        screenshot()
        dw_before = mod._display_w
        dh_before = mod._display_h

        screenshot_region(100, 200, 50, 30)

        assert mod._display_w == dw_before
        assert mod._display_h == dh_before

    def test_click_after_region_uses_correct_scale(self, mock_engine):
        """Full integration: screenshot -> region -> click should use the original scale."""
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import click, screenshot, screenshot_region

        screenshot()
        scale_x = mod._scale_x  # 1920/1024 = 1.875

        screenshot_region(100, 200, 50, 30)

        click(100, 200)
        expected_x = int(100 * scale_x)
        expected_y = int(200 * scale_x)  # scale_x == scale_y for uniform downscale
        mock_engine.click.assert_called_once_with(expected_x, expected_y)

    def test_region_preserves_offset(self, mock_engine):
        """screenshot_region() must not change _offset_x/_offset_y."""
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import screenshot, screenshot_region

        mock_engine.screenshot.return_value = ScreenState(
            image_bytes=_make_png(1920, 1080),
            width=1920,
            height=1080,
            offset_x=200,
            offset_y=100,
        )
        screenshot()
        assert mod._offset_x == 200
        assert mod._offset_y == 100

        screenshot_region(50, 50, 100, 100)

        assert mod._offset_x == 200
        assert mod._offset_y == 100

    def test_multiple_regions_dont_drift(self, mock_engine):
        """Calling screenshot_region repeatedly must not degrade scale accuracy."""
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import screenshot, screenshot_region

        screenshot()
        original_scale_x = mod._scale_x

        for _ in range(5):
            screenshot_region(10, 10, 50, 50)

        assert mod._scale_x == original_scale_x


class TestImageEncoding:
    """Format selection, dimension ceiling, and bytes-on-the-wire shape."""

    def _decode_format(self, image_bytes: bytes) -> str:
        """Inspect the magic bytes of a tool result and report jpeg/png/unknown."""
        if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if image_bytes[:2] == b"\xff\xd8":
            return "jpeg"
        return "unknown"

    def test_screenshot_default_is_jpeg(self, mock_engine):
        from computer_use.mcp_server import screenshot

        result = screenshot()
        assert self._decode_format(result.data) == "jpeg"
        # MCPServer uses _format to derive the MIME type sent over MCP.
        assert result._mime_type == "image/jpeg"

    def test_screenshot_png_mode_keeps_png(self, mock_engine):
        from computer_use.mcp_server import screenshot

        result = screenshot(format="png")
        assert self._decode_format(result.data) == "png"
        assert result._mime_type == "image/png"

    def test_screenshot_thumbnail_uses_jpeg_at_thumbnail_width(self, mock_engine):
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import screenshot

        result = screenshot(format="thumbnail")
        assert self._decode_format(result.data) == "jpeg"
        # Thumbnail caps at _THUMBNAIL_WIDTH (640px) regardless of _MAX_WIDTH.
        assert mod._display_w == mod._THUMBNAIL_WIDTH

    def test_screenshot_thumbnail_is_smaller_than_jpeg(self, mock_engine):
        from computer_use.mcp_server import screenshot

        jpeg = screenshot(format="jpeg")
        thumb = screenshot(format="thumbnail")
        # Thumbnail must be strictly smaller in bytes for any non-trivial image.
        assert len(thumb.data) < len(jpeg.data)

    def test_jpeg_smaller_than_png_for_screenshot_like_image(self, mock_engine):
        """Same source image, JPEG output must be smaller than PNG output.

        Uses a screenshot-shaped pattern (large solid regions + a noisy strip)
        rather than per-pixel pseudo-random noise (which is JPEG's worst case
        and not representative of a real desktop capture).
        """
        from PIL import ImageDraw

        from computer_use.core.types import ScreenState
        from computer_use.mcp_server import screenshot

        img = PILImage.new("RGB", (1366, 800), color=(245, 245, 250))  # window bg
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, 1366, 60], fill=(30, 30, 35))  # title bar
        draw.rectangle([0, 60, 240, 800], fill=(220, 220, 230))  # sidebar
        draw.rectangle([260, 80, 1340, 120], fill=(255, 255, 255))  # search bar
        for i in range(20):  # list rows
            draw.rectangle([260, 140 + i * 30, 1340, 160 + i * 30], fill=(250, 250, 255))
            draw.text((280, 145 + i * 30), f"Item {i}", fill=(40, 40, 50))
        # A small noise band so PNG can't trivially win on flat fills.
        pixels = img.load()
        for y in range(750, 800):
            for x in range(1366):
                pixels[x, y] = ((x * 7) % 256, (y * 11) % 256, ((x + y) * 5) % 256)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        mock_engine.screenshot.return_value = ScreenState(
            image_bytes=buf.getvalue(), width=1366, height=800
        )

        png = screenshot(format="png")
        jpeg = screenshot(format="jpeg")
        assert len(jpeg.data) < len(png.data), (
            f"JPEG ({len(jpeg.data)}B) should be smaller than PNG "
            f"({len(png.data)}B) for a screenshot-like image"
        )

    def test_format_jpg_alias_works(self, mock_engine):
        from computer_use.mcp_server import screenshot

        result = screenshot(format="jpg")
        assert self._decode_format(result.data) == "jpeg"

    def test_format_case_insensitive(self, mock_engine):
        from computer_use.mcp_server import screenshot

        assert self._decode_format(screenshot(format="JPEG").data) == "jpeg"
        assert self._decode_format(screenshot(format="PNG").data) == "png"

    def test_invalid_format_raises(self, mock_engine):
        from computer_use.mcp_server import screenshot

        with pytest.raises(ValueError, match="Unsupported format"):
            screenshot(format="webp")

    def test_screenshot_region_default_is_jpeg(self, mock_engine):
        from computer_use.mcp_server import screenshot_region

        result = screenshot_region(0, 0, 200, 100)
        assert self._decode_format(result.data) == "jpeg"

    def test_screenshot_region_png_mode_keeps_png(self, mock_engine):
        from computer_use.mcp_server import screenshot_region

        result = screenshot_region(0, 0, 200, 100, format="png")
        assert self._decode_format(result.data) == "png"


class TestDimensionCeiling:
    """The hard cap at 1600px keeps screenshots under Anthropic's 2000px limit."""

    def test_caller_set_max_width_above_ceiling_clamps(self, mock_engine):
        """If a user sets CU_MAX_WIDTH=2000, output is still clamped to 1600."""
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import screenshot

        mod._MAX_WIDTH = 2000  # past the safety ceiling
        screenshot()
        assert mod._display_w == mod._MAX_DIMENSION_CEILING
        assert mod._display_w <= 1600

    def test_caller_set_max_width_below_ceiling_respected(self, mock_engine):
        """A user-chosen value under the ceiling still wins (smaller is fine)."""
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import screenshot

        mod._MAX_WIDTH = 1280
        screenshot()
        assert mod._display_w == 1280

    def test_screen_smaller_than_ceiling_no_resize(self, mock_engine):
        """If the real screen is already small, no resize at all."""
        import computer_use.mcp_server as mod
        from computer_use.core.types import ScreenState
        from computer_use.mcp_server import screenshot

        mock_engine.screenshot.return_value = ScreenState(
            image_bytes=_make_png(1024, 768), width=1024, height=768
        )
        mod._MAX_WIDTH = 0  # auto
        screenshot()
        assert mod._display_w == 1024
        assert mod._scale_x == 1.0

    def test_get_screen_size_applies_ceiling_before_screenshot(self, mock_engine):
        """get_screen_size() pre-screenshot must report the same dim cap as actual screenshots."""
        import computer_use.mcp_server as mod
        from computer_use.mcp_server import get_screen_size

        mod._display_w = 0
        mod._display_h = 0
        mod._MAX_WIDTH = 2500  # absurdly high
        mock_engine.get_screen_size.return_value = (3840, 2160)
        result = get_screen_size()
        # Must clamp to the ceiling, not 2500 or 3840.
        assert result.startswith(f"{mod._MAX_DIMENSION_CEILING}x")


class TestEngineSingleton:
    def test_lazy_init(self):
        import computer_use.mcp_server as mod

        mod._engine = None
        assert mod._engine is None


class TestNativeWindowsImport:
    """Importing the MCP server must not pull in POSIX-only modules.

    On native Windows there is no `fcntl`, so an eager import of the bridge
    supervisor crashes the stdio server before it can serve a single tool
    call. The supervisor only matters for daemon subcommands (doctor,
    install-daemon, stop-daemon, restart-daemon), which run on WSL2/Linux.
    """

    def test_supervisor_not_imported_at_module_load(self):
        import importlib
        import sys

        for name in list(sys.modules):
            if name == "computer_use.mcp_server" or name.startswith("computer_use.bridge"):
                del sys.modules[name]

        importlib.import_module("computer_use.mcp_server")
        assert "computer_use.bridge.supervisor" not in sys.modules

    def test_module_imports_when_fcntl_unavailable(self, monkeypatch):
        import builtins
        import importlib
        import sys

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "fcntl":
                raise ModuleNotFoundError("No module named 'fcntl'")
            return real_import(name, *args, **kwargs)

        for mod_name in list(sys.modules):
            if (
                mod_name == "fcntl"
                or mod_name.startswith("computer_use.bridge")
                or mod_name == "computer_use.mcp_server"
            ):
                sys.modules.pop(mod_name, None)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        importlib.import_module("computer_use.mcp_server")


class TestCompactWireText:
    """A dict result reaches the client as compact JSON, not indent=2 text.

    The client hands the text to the model as is, so the indentation was paid in
    tokens on every structured read. A direct Python call still gets the dict.
    """

    def test_sync_dict_result_is_compact_on_the_wire(self):
        from computer_use import mcp_server

        result = asyncio.run(
            mcp_server.mcp.call_tool("time", {"op": "sleep", "seconds": 0})
        )
        assert [c.text for c in result.content] == ['{"slept":0.0}']

    def test_async_dict_result_is_compact_on_the_wire(self):
        import json
        import sys

        from computer_use import mcp_server

        result = asyncio.run(mcp_server.mcp.call_tool(
            "shell",
            {"op": "run", "command": [sys.executable, "-c", "print(1)"]},
        ))
        (text,) = [c.text for c in result.content]
        assert "\n" not in text
        assert json.loads(text)["returncode"] == 0

    def test_direct_call_still_returns_the_dict(self):
        from computer_use import mcp_server

        assert mcp_server.time(op="sleep", seconds=0) == {"slept": 0.0}


class TestServerInstructions:
    def test_structured_tiers_come_before_pixels(self):
        from computer_use import mcp_server

        text = mcp_server.mcp.instructions
        assert "ALWAYS take a screenshot" not in text
        assert text.index("`browser`") < text.index("`ui_find`") < text.index(
            "take a screenshot"
        )


class TestUiActByName:
    def test_the_wire_schema_takes_name_role_and_app(self):
        from computer_use import mcp_server

        tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
        schema = tools["ui_act"].input_schema
        assert schema["required"] == ["action"]
        assert {"ref", "action", "text", "name", "role", "app"} <= set(schema["properties"])

    def test_the_wrapper_threads_every_argument(self, monkeypatch):
        from computer_use import mcp_server
        from computer_use.tools.ui import tools as ui_tools

        seen = {}

        def fake(**kwargs):
            seen.update(kwargs)
            return {"ok": True}

        monkeypatch.setattr(ui_tools, "ui_act", fake)
        mcp_server.ui_act(action="click", name="Save", role="button", app="gedit")
        assert seen == {"ref": "", "action": "click", "text": "", "name": "Save",
                        "role": "button", "app": "gedit"}

    def test_the_instructions_lead_native_apps_with_ui_act_by_name(self):
        from computer_use import mcp_server

        text = mcp_server.mcp.instructions
        assert "`ui_act(action=..., name=..., role=...)`" in text
        assert text.index("`ui_act(action=") < text.index("`ui_find`")
