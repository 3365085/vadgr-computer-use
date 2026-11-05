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

"""Abstract screenshot capture interface."""

from abc import ABC, abstractmethod

from computer_use.core.types import Region, ScreenState


class ScreenCapture(ABC):
    """Abstract base for capturing screenshots."""

    @property
    def backend_name(self) -> str:
        """A short name for the capture path, reported by get_platform_info."""
        return type(self).__name__

    @abstractmethod
    def capture_full(self) -> ScreenState:
        """Capture the entire screen. Returns PNG bytes in a ScreenState."""
        ...

    @abstractmethod
    def capture_region(self, region: Region) -> ScreenState:
        """Capture a specific rectangular region."""
        ...

    @abstractmethod
    def get_screen_size(self) -> tuple[int, int]:
        """Return (width, height) of the primary display."""
        ...

    @abstractmethod
    def get_scale_factor(self) -> float:
        """Return the display scale factor (1.0 for standard, 2.0 for HiDPI)."""
        ...
