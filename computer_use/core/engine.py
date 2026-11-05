# Copyright 2026 Victor Santiago Montaño Diaz
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Main engine facade: the public Python API for the computer use engine.

Mirror of the MCP tool surface. The engine does not call any LLM; the
caller (an MCP client, a test, or a user script) decides what to do.
"""

import logging

from computer_use.core.actions import ActionExecutor
from computer_use.core.errors import PlatformNotSupportedError
from computer_use.core.screenshot import ScreenCapture
from computer_use.core.types import Platform, Region, ScreenState
from computer_use.platform.base import PlatformBackend
from computer_use.platform.detect import detect_platform, get_backend

logger = logging.getLogger("computer_use.engine")


class ComputerUseEngine:
    """Primary API for the computer use engine.

    Usage:
        engine = ComputerUseEngine()
        screen = engine.screenshot()
        engine.click(500, 300)
        engine.type_text("hello world")
    """

    def __init__(self):
        self._platform = detect_platform()
        logger.info("Detected platform: %s", self._platform.value)

        self._backend: PlatformBackend = get_backend(self._platform)
        report = self._backend.availability_report()
        if not report.available:
            missing = ", ".join(report.missing) if report.missing else "unknown"
            raise PlatformNotSupportedError(
                f"Platform {self._platform.value} backend is not available "
                f"(missing: {missing}). {report.remediation}"
            )

        self._capture: ScreenCapture = self._backend.get_screen_capture()
        self._executor: ActionExecutor = self._backend.get_action_executor()
        # Virtual screen offset for multi-monitor coordinate translation.
        # Populated on first screenshot. Screenshot pixel (x, y) maps to
        # absolute screen coordinate (x + offset_x, y + offset_y).
        self._vs_offset_x: int = 0
        self._vs_offset_y: int = 0

    def screenshot(self) -> ScreenState:
        """Capture and return the full virtual screen (all monitors)."""
        state = self._capture.capture_full()
        self._vs_offset_x = state.offset_x
        self._vs_offset_y = state.offset_y
        return state

    def _to_abs(self, x: int, y: int) -> tuple[int, int]:
        """Translate screenshot pixel coords to absolute screen coords."""
        return (x + self._vs_offset_x, y + self._vs_offset_y)

    def screenshot_region(self, x: int, y: int, width: int, height: int) -> ScreenState:
        """Capture a rectangular region of the screen."""
        return self._capture.capture_region(Region(x, y, width, height))

    def click(self, x: int, y: int) -> None:
        """Left-click at screenshot coordinates (auto-translated for multi-monitor)."""
        ax, ay = self._to_abs(x, y)
        self._executor.click(ax, ay)

    def double_click(self, x: int, y: int) -> None:
        """Double-click at screenshot coordinates."""
        ax, ay = self._to_abs(x, y)
        self._executor.double_click(ax, ay)

    def right_click(self, x: int, y: int) -> None:
        """Right-click at screenshot coordinates."""
        ax, ay = self._to_abs(x, y)
        self._executor.click(ax, ay, button="right")

    def move_mouse(self, x: int, y: int) -> None:
        """Move mouse without clicking."""
        ax, ay = self._to_abs(x, y)
        self._executor.move_mouse(ax, ay)

    def type_text(self, text: str, plan=None, *, cancelled=None, deadline=None) -> int:
        """Type text through the fast path or one validated timing plan."""
        if plan is None:
            self._executor.type_text(text)
            return 0
        else:
            return self._executor.type_text_plan(
                plan,
                cancelled=cancelled,
                deadline=deadline,
            )

    def key_press(self, *keys: str) -> None:
        """Press a key combination. e.g. engine.key_press('ctrl', 'c')"""
        self._executor.key_press(list(keys))

    def scroll(self, x: int, y: int, amount: int) -> None:
        """Scroll at a position. Positive = up, negative = down."""
        ax, ay = self._to_abs(x, y)
        self._executor.scroll(ax, ay, amount)

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: float = 0.5,
    ) -> None:
        """Drag from one position to another."""
        asx, asy = self._to_abs(start_x, start_y)
        aex, aey = self._to_abs(end_x, end_y)
        self._executor.drag(asx, asy, aex, aey, duration)

    def get_screen_size(self) -> tuple[int, int]:
        """Return (width, height) of the primary display."""
        return self._capture.get_screen_size()

    def get_platform(self) -> Platform:
        """Return the detected platform."""
        return self._platform

    def get_platform_info(self) -> dict:
        """Return platform info, including the capture path and the structured tier."""
        capture = getattr(self._capture, "backend_name", None)
        return {
            "platform": self._platform.value,
            "backend_available": self._backend.is_available(),
            "capture": capture if isinstance(capture, str) else type(self._capture).__name__,
            "structured": self._backend.structured_capability(),
        }
