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

"""XDG Desktop Portal screenshot backend - the portable Wayland capture path.

Works on any compositor with a portal backend (GNOME, KDE, wlroots), which is
the only capture route left on GNOME 49+ after gnome-screenshot lost the private
API. Pure-python: jeepney for the D-Bus call, Pillow to decode. The portal shows
a one-time consent dialog on GNOME, then caches the grant in the PermissionStore.

The D-Bus transport is isolated behind ``PortalScreenshotClient`` so the capture
logic (decode, crop, cleanup) is unit-tested with a fake client; the live portal
round-trip is covered by the e2e runbook.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from computer_use.core.errors import ScreenCaptureError
from computer_use.core.screenshot import ScreenCapture
from computer_use.core.types import Region, ScreenState

logger = logging.getLogger("computer_use.platform.backends.portal")

try:
    import jeepney as _jeepney
    from jeepney.io.blocking import open_dbus_connection as _open_dbus_connection
except ImportError:
    _jeepney = None
    _open_dbus_connection = None

_PORTAL_BUS = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_SCREENSHOT_IFACE = "org.freedesktop.portal.Screenshot"
_REQUEST_IFACE = "org.freedesktop.portal.Request"
_HANDLE_TOKEN = "vadgrcua_shot"


def _scale_factor() -> float:
    try:
        return float(os.environ.get("GDK_SCALE", "") or 1.0)
    except (ValueError, TypeError):
        return 1.0


def portal_available() -> bool:
    """True if jeepney can reach the Screenshot portal on the session bus."""
    if _jeepney is None or _open_dbus_connection is None:
        return False
    conn = None
    try:
        addr = _jeepney.DBusAddress(
            _PORTAL_PATH, bus_name=_PORTAL_BUS,
            interface="org.freedesktop.DBus.Properties",
        )
        conn = _open_dbus_connection(bus="SESSION")
        msg = _jeepney.new_method_call(addr, "Get", "ss", (_SCREENSHOT_IFACE, "version"))
        reply = conn.send_and_get_reply(msg, timeout=2.0)
        return reply.header.message_type != _jeepney.MessageType.error
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _request_lock_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return Path(runtime) / "vadgr-cua-portal-screenshot.lock"
    return Path(f"/tmp/vadgr-cua-portal-screenshot-{os.getuid()}.lock")


@contextlib.contextmanager
def _portal_request_lock(timeout: float):
    """Hold a per-user lock for one portal Screenshot request.

    GNOME answers only one of several overlapping Screenshot requests; the
    others never receive a Response and wait out their whole timeout. Separate
    computer-use processes therefore take turns.
    """
    import fcntl

    path = _request_lock_path()
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ScreenCaptureError(
                        "another screenshot request did not finish in time"
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


class PortalScreenshotClient:
    """Real transport: ``org.freedesktop.portal.Screenshot`` over jeepney."""

    def take_screenshot(self, timeout: float = 120.0) -> str:
        """Invoke the portal and return a local PNG path. Raises ScreenCaptureError."""
        if _jeepney is None or _open_dbus_connection is None:
            raise ScreenCaptureError("jeepney is required for the screenshot portal")
        with _portal_request_lock(timeout):
            return self._take_screenshot(timeout)

    def _take_screenshot(self, timeout: float) -> str:
        conn = _open_dbus_connection(bus="SESSION")
        try:
            sender = conn.unique_name.lstrip(":").replace(".", "_")
            handle = f"{_PORTAL_PATH}/request/{sender}/{_HANDLE_TOKEN}"
            rule = _jeepney.MatchRule(
                type="signal", interface=_REQUEST_IFACE, member="Response", path=handle,
            )
            with conn.filter(rule) as responses:
                conn.send_and_get_reply(_jeepney.message_bus.AddMatch(rule))
                addr = _jeepney.DBusAddress(
                    _PORTAL_PATH, bus_name=_PORTAL_BUS, interface=_SCREENSHOT_IFACE,
                )
                options = {
                    "handle_token": ("s", _HANDLE_TOKEN),
                    "interactive": ("b", False),
                }
                call = _jeepney.new_method_call(addr, "Screenshot", "sa{sv}", ("", options))
                conn.send_and_get_reply(call)
                signal = conn.recv_until_filtered(responses, timeout=timeout)
            response_code, results = signal.body
            if response_code != 0:
                raise ScreenCaptureError(
                    f"screenshot portal cancelled or denied (code {response_code})"
                )
            uri = results.get("uri")
            if isinstance(uri, tuple):  # variant ('s', value)
                uri = uri[1]
            if not uri:
                raise ScreenCaptureError("screenshot portal returned no uri")
            return unquote(urlparse(uri).path)
        except ScreenCaptureError:
            raise
        except Exception as exc:
            raise ScreenCaptureError(f"screenshot portal failed: {exc}")
        finally:
            try:
                conn.close()
            except Exception:
                pass


class PortalScreenshotCapture(ScreenCapture):
    """Capture via the XDG Screenshot portal; decode + crop + clean up locally."""

    backend_name = "portal"

    def __init__(self, client: PortalScreenshotClient | None = None):
        self._client = client or PortalScreenshotClient()

    def _grab_png(self) -> bytes:
        path = self._client.take_screenshot()
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            raise ScreenCaptureError(f"could not read portal screenshot: {exc}")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        return data

    def capture_full(self) -> ScreenState:
        data = self._grab_png()
        w, h = self._size(data)
        return ScreenState(image_bytes=data, width=w, height=h, scale_factor=_scale_factor())

    def capture_region(self, region: Region) -> ScreenState:
        from PIL import Image

        full = self.capture_full()
        img = Image.open(io.BytesIO(full.image_bytes))
        box = (region.x, region.y, region.x + region.width, region.y + region.height)
        buf = io.BytesIO()
        img.crop(box).save(buf, format="PNG")
        return ScreenState(
            image_bytes=buf.getvalue(),
            width=region.width,
            height=region.height,
            scale_factor=full.scale_factor,
        )

    def get_screen_size(self) -> tuple[int, int]:
        state = self.capture_full()
        return (state.width, state.height)

    def get_scale_factor(self) -> float:
        return _scale_factor()

    @staticmethod
    def _size(data: bytes) -> tuple[int, int]:
        from PIL import Image

        return Image.open(io.BytesIO(data)).size
