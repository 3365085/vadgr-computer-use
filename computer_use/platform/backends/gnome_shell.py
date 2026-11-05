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

"""The vadgr GNOME Shell extension, seen from Python: indicator and capture.

On GNOME Wayland an ordinary client can neither draw a global, click-through
overlay nor take a screenshot without the flash the screenshot portal
hard-codes. The extension shipped in ``platform/gnome_extension`` does both
from inside the shell and exports them on the session bus; this module is the
jeepney client for it and the capture backend built on that client.

Everything degrades. With the extension absent (not installed, not enabled
yet, another desktop, another OS) the indicator calls do nothing, and the
capture falls back to the screenshot portal. The live shell round trip is the
e2e's job; the unit tests drive this module through a fake connection.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from computer_use.core.errors import ScreenCaptureError
from computer_use.core.screenshot import ScreenCapture
from computer_use.core.types import Region, ScreenState

logger = logging.getLogger("computer_use.platform.backends.gnome_shell")

try:
    import jeepney as _jeepney
    from jeepney.io.blocking import open_dbus_connection as _open_dbus_connection
except ImportError:
    _jeepney = None
    _open_dbus_connection = None

BUS_NAME = "com.vadgr.cua.Shell"
OBJECT_PATH = "/com/vadgr/cua/Shell"
INTERFACE = "com.vadgr.cua.Shell"
EXTENSION_UUID = "vadgr-cua@vadgr.com"
EXTENSION_FILES = ("metadata.json", "extension.js", "stylesheet.css")

USER_STOPPED_ERROR = f"{INTERFACE}.Error.UserStopped"

# Errors meaning nobody serves the interface here: the name has no owner, or an
# owner that is not this extension.
_ABSENT_ERRORS = frozenset({
    "org.freedesktop.DBus.Error.ServiceUnknown",
    "org.freedesktop.DBus.Error.NameHasNoOwner",
    "org.freedesktop.DBus.Error.UnknownObject",
    "org.freedesktop.DBus.Error.UnknownInterface",
    "org.freedesktop.DBus.Error.UnknownMethod",
})

# Begin sits in front of every desktop tool, so a shell that is slow to answer
# must not hold a tool up for long: the action goes ahead without the cue.
_BEGIN_TIMEOUT = 1.0
_CAPTURE_TIMEOUT = 10.0
_SIZE_TIMEOUT = 2.0
# How long an absent extension is believed before the bus is asked again. A
# missing session bus is believed for longer, since it does not come back.
_ABSENT_RETRY = 2.0
_NO_BUS_RETRY = 30.0


class ExtensionUnavailable(Exception):
    """Nothing on the session bus serves the extension's interface."""


class UserStopped(ScreenCaptureError):
    """The user pressed Esc; the shell refuses desktop actions for a while."""


def extension_dir() -> Path:
    """The packaged extension, the directory ``install-deps`` installs from."""
    return Path(__file__).resolve().parent.parent / "gnome_extension" / EXTENSION_UUID


def session_is_gnome() -> bool:
    return "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def _default_connect():
    if _open_dbus_connection is None:
        raise ExtensionUnavailable("jeepney is not installed")
    return _open_dbus_connection(bus="SESSION")


class ShellClient:
    """Method calls to the extension over one lazily opened, locked connection.

    The tools run on worker threads, so the connection is shared under a lock.
    A failed transport drops the connection and the next call reconnects.
    """

    def __init__(self, connect=None, clock=time.monotonic) -> None:
        self._connect = connect or _default_connect
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = None
        self._absent_until = 0.0

    def _drop(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    def _call(self, method: str, signature: str | None, body: tuple, timeout: float):
        if _jeepney is None:
            raise ExtensionUnavailable("jeepney is not installed")
        with self._lock:
            if self._clock() < self._absent_until:
                raise ExtensionUnavailable("the extension was absent moments ago")
            if self._conn is None:
                try:
                    self._conn = self._connect()
                except Exception as exc:
                    self._absent_until = self._clock() + _NO_BUS_RETRY
                    raise ExtensionUnavailable(f"no session bus: {exc}") from exc
            address = _jeepney.DBusAddress(
                OBJECT_PATH, bus_name=BUS_NAME, interface=INTERFACE
            )
            message = _jeepney.new_method_call(address, method, signature, body)
            try:
                reply = self._conn.send_and_get_reply(message, timeout=timeout)
            except Exception as exc:
                # A timeout or a broken socket: the connection may still owe a
                # late reply, so it is replaced rather than reused.
                self._drop()
                raise ExtensionUnavailable(f"{method} got no answer: {exc}") from exc
            if reply.header.message_type != _jeepney.MessageType.error:
                return reply.body
            name = reply.header.fields.get(_jeepney.HeaderFields.error_name, "")
            detail = reply.body[0] if reply.body else name
            if name in _ABSENT_ERRORS:
                self._absent_until = self._clock() + _ABSENT_RETRY
                raise ExtensionUnavailable(f"{name}: {detail}")
            if name == USER_STOPPED_ERROR:
                raise UserStopped(f"user_stopped: {detail}")
            raise ScreenCaptureError(f"GNOME Shell extension error {name}: {detail}")

    def available(self) -> bool:
        """Whether the extension answers on the bus (it also proves the API)."""
        try:
            self.capture_size()
        except Exception:
            return False
        return True

    def begin(
        self,
        tool: str,
        text: str,
        x: int = -1,
        y: int = -1,
        width: int = 0,
        height: int = 0,
        own_escape: bool = False,
    ) -> bool:
        """Light the overlay for an action. True means the user stopped vadgr.

        Never raises: the cue is a courtesy to the person at the screen, and a
        tool must not fail because the overlay could not be drawn.
        """
        try:
            (stopped,) = self._call(
                "Begin", "ssiiiib",
                (tool, text, int(x), int(y), int(width), int(height), bool(own_escape)),
                _BEGIN_TIMEOUT,
            )
        except Exception as exc:
            logger.debug("indicator Begin skipped: %s", exc)
            return False
        return bool(stopped)

    def screenshot(
        self, path: str, x: int = 0, y: int = 0, width: int = 0, height: int = 0
    ) -> None:
        """Have the shell write a PNG of the screen (or an area) to ``path``."""
        self._call(
            "Screenshot", "siiii",
            (path, int(x), int(y), int(width), int(height)),
            _CAPTURE_TIMEOUT,
        )

    def capture_size(self) -> tuple[int, int]:
        """The pixel size of a full capture, without taking one."""
        width, height = self._call("CaptureSize", None, (), _SIZE_TIMEOUT)
        return int(width), int(height)


_default_client: ShellClient | None = None
_default_lock = threading.Lock()


def default_client() -> ShellClient:
    """The process-wide client, so every tool shares one connection."""
    global _default_client
    with _default_lock:
        if _default_client is None:
            _default_client = ShellClient()
        return _default_client


def extension_available() -> bool:
    return default_client().available()


def _scale_factor() -> float:
    try:
        return float(os.environ.get("GDK_SCALE", "") or 1.0)
    except (ValueError, TypeError):
        return 1.0


def _runtime_dir() -> str | None:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return runtime
    return None


def _png_size(data: bytes) -> tuple[int, int]:
    from PIL import Image

    return Image.open(io.BytesIO(data)).size


def _portal_capture() -> ScreenCapture:
    from computer_use.platform.backends.portal import PortalScreenshotCapture, portal_available

    if not portal_available():
        raise ScreenCaptureError(
            "The GNOME Shell extension is not running and no screenshot portal "
            "answers. Run `vadgr-cua install-deps`, then log out and back in."
        )
    return PortalScreenshotCapture()


class GnomeShellScreenCapture(ScreenCapture):
    """Capture through the extension: no flash, and the overlay never shows.

    The shell writes the PNG into a private directory this process creates and
    removes. When the extension goes away (disabled, or the screen locked) a
    capture falls back to the screenshot portal rather than failing.
    """

    def __init__(self, client: ShellClient | None = None, fallback=None) -> None:
        self._client = client or default_client()
        self._make_fallback = fallback or _portal_capture
        self._fallback: ScreenCapture | None = None
        self._fell_back = False

    @property
    def backend_name(self) -> str:
        return "portal" if self._fell_back else "gnome-shell-extension"

    def _portal(self) -> ScreenCapture:
        if self._fallback is None:
            self._fallback = self._make_fallback()
        self._fell_back = True
        return self._fallback

    def _grab(self, x: int = 0, y: int = 0, width: int = 0, height: int = 0) -> bytes:
        workdir = tempfile.mkdtemp(prefix="vadgr-cua-shot-", dir=_runtime_dir())
        path = os.path.join(workdir, "capture.png")
        try:
            self._client.screenshot(path, x, y, width, height)
            data = Path(path).read_bytes()
        except OSError as exc:
            raise ScreenCaptureError(f"could not read the shell screenshot: {exc}") from exc
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        self._fell_back = False
        return data

    def _state(self, data: bytes) -> ScreenState:
        width, height = _png_size(data)
        return ScreenState(
            image_bytes=data, width=width, height=height, scale_factor=_scale_factor()
        )

    def capture_full(self) -> ScreenState:
        try:
            return self._state(self._grab())
        except ExtensionUnavailable as exc:
            logger.info("GNOME Shell extension unavailable (%s); using the portal", exc)
            return self._portal().capture_full()

    def capture_region(self, region: Region) -> ScreenState:
        try:
            return self._state(
                self._grab(region.x, region.y, region.width, region.height)
            )
        except ExtensionUnavailable as exc:
            logger.info("GNOME Shell extension unavailable (%s); using the portal", exc)
            return self._portal().capture_region(region)

    def get_screen_size(self) -> tuple[int, int]:
        try:
            return self._client.capture_size()
        except ExtensionUnavailable:
            return self._portal().get_screen_size()

    def get_scale_factor(self) -> float:
        return _scale_factor()
