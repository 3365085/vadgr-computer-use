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

"""Linux platform backend. Supports X11 (mss/xdotool) and Wayland (Mutter RemoteDesktop/evdev)."""

import io
import logging
import os
import random
import shutil
import subprocess
import tempfile
import time
from typing import Optional

from computer_use.core.actions import ActionExecutor, consume_typing_plan
from computer_use.core.errors import ActionError, PlatformNotSupportedError, ScreenCaptureError
from computer_use.core.screenshot import ScreenCapture
from computer_use.core.smooth_move import (
    DRAG_GRAVITY,
    DRAG_MAX_VEL,
    DRAG_WIND,
    PRE_CLICK_BASE,
    PRE_CLICK_RAND,
    PRE_DRAG_BASE,
    PRE_DRAG_RAND,
    CursorTracker,
    generate_delays,
    smooth_move,
    windmouse_path,
)
from computer_use.core.types import ForegroundWindow, Region, ScreenState
from computer_use.core.typing import TypingPlan
from computer_use.platform.backends import gnome_shell
from computer_use.platform.backends.portal import PortalScreenshotCapture, portal_available
from computer_use.platform.backends.uinput import UinputDevice
from computer_use.platform.backends.xtest import XTestExecutor
from computer_use.platform.base import AvailabilityReport, PlatformBackend

logger = logging.getLogger("computer_use.platform.linux")

# Lazy imports so the module loads even without optional deps.
try:
    import mss as mss_import
except ImportError:
    mss_import = None  # type: ignore[assignment]

try:
    import evdev as evdev_import
    from evdev import ecodes
except ImportError:
    evdev_import = None  # type: ignore[assignment]
    ecodes = None  # type: ignore[assignment]

try:
    import jeepney as jeepney_import
    from jeepney.io.blocking import open_dbus_connection as _open_dbus_connection
except ImportError:
    jeepney_import = None  # type: ignore[assignment]
    _open_dbus_connection = None  # type: ignore[assignment]

# libxkbcommon for layout-aware character-to-keycode mapping.
# Available on every GNOME/Wayland desktop, loaded via ctypes (no pip package needed).
import ctypes
import ctypes.util

_xkb = None  # type: Optional[ctypes.CDLL]
try:
    _xkb_path = ctypes.util.find_library("xkbcommon")
    if _xkb_path:
        _xkb = ctypes.CDLL(_xkb_path)
except OSError:
    _xkb = None


class _XkbRuleNames(ctypes.Structure):
    _fields_ = [
        ("rules", ctypes.c_char_p),
        ("model", ctypes.c_char_p),
        ("layout", ctypes.c_char_p),
        ("variant", ctypes.c_char_p),
        ("options", ctypes.c_char_p),
    ]


if _xkb is not None:
    _xkb.xkb_context_new.argtypes = [ctypes.c_int]
    _xkb.xkb_context_new.restype = ctypes.c_void_p
    _xkb.xkb_context_unref.argtypes = [ctypes.c_void_p]
    _xkb.xkb_context_unref.restype = None
    _xkb.xkb_keymap_new_from_names.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_XkbRuleNames),
        ctypes.c_int,
    ]
    _xkb.xkb_keymap_new_from_names.restype = ctypes.c_void_p
    _xkb.xkb_keymap_unref.argtypes = [ctypes.c_void_p]
    _xkb.xkb_keymap_unref.restype = None
    _xkb.xkb_state_new.argtypes = [ctypes.c_void_p]
    _xkb.xkb_state_new.restype = ctypes.c_void_p
    _xkb.xkb_state_unref.argtypes = [ctypes.c_void_p]
    _xkb.xkb_state_unref.restype = None
    _xkb.xkb_state_update_mask.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,  # mods: depressed, latched, locked
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,  # layouts: depressed, latched, locked
    ]
    _xkb.xkb_state_update_mask.restype = ctypes.c_int
    _xkb.xkb_state_key_get_utf32.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    _xkb.xkb_state_key_get_utf32.restype = ctypes.c_uint32


# ---------------------------------------------------------------------------
# Display session detection
# ---------------------------------------------------------------------------


def _is_wayland() -> bool:
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    return os.environ.get("XDG_SESSION_TYPE") == "wayland"


def _get_scale_factor() -> float:
    raw = os.environ.get("GDK_SCALE", "")
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 1.0


# ---------------------------------------------------------------------------
# Screenshot strategies
# ---------------------------------------------------------------------------


class _CliScreenCapture(ScreenCapture):
    """Base for CLI-tool-based screenshot capture (grim, gnome-screenshot, etc.)."""

    def _run_capture(self, args: list[str], output_path: str) -> None:
        raise NotImplementedError

    def _tool_name(self) -> str:
        raise NotImplementedError

    def capture_full(self) -> ScreenState:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            path = tmp.name

        try:
            self._run_capture(self._full_args(path), path)
            with open(path, "rb") as f:
                data = f.read()
            w, h = self._read_image_size(data)
            return ScreenState(
                image_bytes=data,
                width=w,
                height=h,
                scale_factor=_get_scale_factor(),
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def capture_region(self, region: Region) -> ScreenState:
        # Capture full screen and crop. Most CLI tools don't support region natively.
        full = self.capture_full()
        from PIL import Image

        img = Image.open(io.BytesIO(full.image_bytes))
        cropped = img.crop((region.x, region.y, region.x + region.width, region.y + region.height))
        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
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
        return _get_scale_factor()

    def _full_args(self, output_path: str) -> list[str]:
        raise NotImplementedError

    @staticmethod
    def _read_image_size(data: bytes) -> tuple[int, int]:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        return img.size


class GrimScreenCapture(_CliScreenCapture):
    """Wayland screenshots via grim (wlroots compositors: Sway, Hyprland, etc.)."""

    backend_name = "grim"

    def _tool_name(self) -> str:
        return "grim"

    def _full_args(self, output_path: str) -> list[str]:
        return ["grim", output_path]

    def _run_capture(self, args: list[str], output_path: str) -> None:
        result = subprocess.run(args, capture_output=True, timeout=10.0)
        if result.returncode != 0:
            raise ScreenCaptureError(f"grim failed: {result.stderr.decode().strip()}")


class GnomeScreenCapture(_CliScreenCapture):
    """Wayland screenshots via gnome-screenshot (GNOME)."""

    backend_name = "gnome-screenshot"

    def _tool_name(self) -> str:
        return "gnome-screenshot"

    def _full_args(self, output_path: str) -> list[str]:
        return ["gnome-screenshot", "-f", output_path]

    def _run_capture(self, args: list[str], output_path: str) -> None:
        result = subprocess.run(args, capture_output=True, timeout=10.0)
        if result.returncode != 0:
            raise ScreenCaptureError(f"gnome-screenshot failed: {result.stderr.decode().strip()}")


class MssScreenCapture(ScreenCapture):
    """X11 screenshots via mss."""

    backend_name = "mss"

    def __init__(self):
        if mss_import is None:
            raise ScreenCaptureError("mss not installed. Run: pip install mss")
        self._mss = mss_import.mss()

    def capture_full(self) -> ScreenState:
        monitor = self._mss.monitors[1]
        screenshot = self._mss.grab(monitor)
        return ScreenState(
            image_bytes=self._to_png(screenshot),
            width=screenshot.width,
            height=screenshot.height,
            scale_factor=_get_scale_factor(),
        )

    def capture_region(self, region: Region) -> ScreenState:
        monitor = {
            "left": region.x,
            "top": region.y,
            "width": region.width,
            "height": region.height,
        }
        screenshot = self._mss.grab(monitor)
        return ScreenState(
            image_bytes=self._to_png(screenshot),
            width=region.width,
            height=region.height,
            scale_factor=_get_scale_factor(),
        )

    def get_screen_size(self) -> tuple[int, int]:
        monitor = self._mss.monitors[1]
        return (monitor["width"], monitor["height"])

    def get_scale_factor(self) -> float:
        return _get_scale_factor()

    @staticmethod
    def _to_png(screenshot) -> bytes:
        from PIL import Image

        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Factory: pick the right capture strategy
# ---------------------------------------------------------------------------

_WAYLAND_TOOLS: list[tuple[str, type[_CliScreenCapture]]] = [
    ("grim", GrimScreenCapture),
    ("gnome-screenshot", GnomeScreenCapture),
]


def _gnome_shell_capture_available() -> bool:
    """The vadgr GNOME Shell extension is installed here, or answers on the bus.

    Installed is enough: the capture falls back to the portal per call while the
    extension is away (screen locked, shell still starting), and comes back to it
    without a restart. Choosing by a live answer alone pinned a server started at
    such a moment to the flashing portal for its whole life.
    """
    return gnome_shell.session_is_gnome() and (
        gnome_shell.extension_installed() or gnome_shell.extension_available()
    )


def _create_screen_capture() -> ScreenCapture:
    if not _is_wayland():
        return MssScreenCapture()

    # GNOME with the vadgr extension loaded: the shell captures in-process, with
    # no portal flash and the indicator overlay kept out of the image. Probed
    # with a size query, never a capture, so choosing it lights nothing.
    if _gnome_shell_capture_available():
        logger.info("Wayland: using the vadgr GNOME Shell extension for screenshots")
        return gnome_shell.GnomeShellScreenCapture()

    # Binary existing doesn't mean it works (e.g. grim on GNOME).
    # Probe each tool with a real capture attempt.
    for tool, cls in _WAYLAND_TOOLS:
        if not shutil.which(tool):
            continue
        capture = cls()
        try:
            capture.capture_full()
            logger.info("Wayland: using %s for screenshots", tool)
            return capture
        except ScreenCaptureError:
            logger.debug("%s installed but failed, trying next tool", tool)
            continue

    # Portable catch-net: the XDG screenshot portal. Tried AFTER the no-dialog CLI
    # tools so an older desktop (e.g. GNOME 46 / Ubuntu 24.04, where
    # gnome-screenshot still works) keeps its silent path unchanged, while GNOME
    # 49+ - where the CLI tools are dead - transparently falls through to here.
    if portal_available():
        logger.info("Wayland: using XDG screenshot portal")
        return PortalScreenshotCapture()

    raise ScreenCaptureError(
        "No working Wayland screenshot tool found. Install one of: grim, "
        "gnome-screenshot, or an XDG screenshot portal"
    )


# ---------------------------------------------------------------------------
# xdotool key mapping
# ---------------------------------------------------------------------------

XDOTOOL_KEY_MAP = {
    "insert": "Insert",
    "enter": "Return",
    "return": "Return",
    "tab": "Tab",
    "escape": "Escape",
    "esc": "Escape",
    "backspace": "BackSpace",
    "delete": "Delete",
    "del": "Delete",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "Prior",
    "pagedown": "Next",
    "space": "space",
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "super": "super",
    "f1": "F1",
    "f2": "F2",
    "f3": "F3",
    "f4": "F4",
    "f5": "F5",
    "f6": "F6",
    "f7": "F7",
    "f8": "F8",
    "f9": "F9",
    "f10": "F10",
    "f11": "F11",
    "f12": "F12",
}


# ---------------------------------------------------------------------------
# Action execution (xdotool -- works on X11 and Wayland via XWayland)
# ---------------------------------------------------------------------------


def _run_xdotool(*args: str) -> str:
    try:
        result = subprocess.run(
            ["xdotool", *args],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if result.returncode != 0:
            raise ActionError(f"xdotool error: {result.stderr.strip()}")
        return result.stdout.strip()
    except FileNotFoundError:
        raise ActionError("xdotool not installed. Run: sudo apt install xdotool")


class LinuxActionExecutor(ActionExecutor):
    def __init__(self):
        self._tracker = CursorTracker()

    def _raw_move(self, x: int, y: int) -> None:
        _run_xdotool("mousemove", str(x), str(y))
        self._tracker.update(x, y)

    def move_mouse(self, x: int, y: int) -> None:
        smooth_move(x, y, self._tracker.get_pos, self._raw_move)

    def click(self, x: int, y: int, button: str = "left") -> None:
        button_num = {"left": "1", "middle": "2", "right": "3"}.get(button, "1")
        self.move_mouse(x, y)
        time.sleep(PRE_CLICK_BASE + random.random() * PRE_CLICK_RAND)
        _run_xdotool("click", button_num)

    def double_click(self, x: int, y: int) -> None:
        self.move_mouse(x, y)
        time.sleep(PRE_CLICK_BASE + random.random() * PRE_CLICK_RAND)
        _run_xdotool("click", "--repeat", "2", "--delay", "50", "1")

    def type_text(self, text: str) -> None:
        _run_xdotool("type", "--clearmodifiers", text)

    def key_press(self, keys: list[str]) -> None:
        if not keys:
            return
        mapped = [XDOTOOL_KEY_MAP.get(k.lower(), k) for k in keys]
        combo = "+".join(mapped)
        _run_xdotool("key", "--clearmodifiers", combo)

    def scroll(self, x: int, y: int, amount: int) -> None:
        self._raw_move(x, y)
        button = "4" if amount > 0 else "5"
        for _ in range(abs(amount)):
            _run_xdotool("click", button)

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: float = 0.5,
    ) -> None:
        self.move_mouse(start_x, start_y)
        time.sleep(PRE_DRAG_BASE + random.random() * PRE_DRAG_RAND)
        _run_xdotool("mousedown", "1")
        path = windmouse_path(
            start_x,
            start_y,
            end_x,
            end_y,
            gravity=DRAG_GRAVITY,
            wind=DRAG_WIND,
            max_vel=DRAG_MAX_VEL,
        )
        delays = generate_delays(len(path), duration)
        for i, (px, py) in enumerate(path):
            self._raw_move(px, py)
            if i < len(delays):
                time.sleep(delays[i])
        _run_xdotool("mouseup", "1")


# ---------------------------------------------------------------------------
# Shared evdev keycodes + Wayland action executor base
# ---------------------------------------------------------------------------

# Hardcoded evdev keycodes (input-event-codes.h). Fallback when python-evdev not installed.
_FALLBACK_EVDEV_KEYCODES: dict[str, int] = {
    "insert": 110,
    "enter": 28,
    "return": 28,
    "tab": 15,
    "escape": 1,
    "esc": 1,
    "backspace": 14,
    "delete": 111,
    "del": 111,
    "up": 103,
    "down": 108,
    "left": 105,
    "right": 106,
    "home": 102,
    "end": 107,
    "pageup": 104,
    "pagedown": 109,
    "space": 57,
    " ": 57,
    "ctrl": 29,
    "control": 29,
    "alt": 56,
    "rightalt": 100,
    "shift": 42,
    "super": 125,
    "f1": 59,
    "f2": 60,
    "f3": 61,
    "f4": 62,
    "f5": 63,
    "f6": 64,
    "f7": 65,
    "f8": 66,
    "f9": 67,
    "f10": 68,
    "f11": 87,
    "f12": 88,
    "q": 16,
    "w": 17,
    "e": 18,
    "r": 19,
    "t": 20,
    "y": 21,
    "u": 22,
    "i": 23,
    "o": 24,
    "p": 25,
    "a": 30,
    "s": 31,
    "d": 32,
    "f": 33,
    "g": 34,
    "h": 35,
    "j": 36,
    "k": 37,
    "l": 38,
    "z": 44,
    "x": 45,
    "c": 46,
    "v": 47,
    "b": 48,
    "n": 49,
    "m": 50,
    "1": 2,
    "2": 3,
    "3": 4,
    "4": 5,
    "5": 6,
    "6": 7,
    "7": 8,
    "8": 9,
    "9": 10,
    "0": 11,
    "-": 12,
    "=": 13,
    "[": 26,
    "]": 27,
    ";": 39,
    "'": 40,
    ",": 51,
    ".": 52,
    "/": 53,
    "\\": 43,
    "`": 41,
}

_FALLBACK_BUTTON_CODES: dict[str, int] = {"left": 272, "right": 273, "middle": 274}

# Maps shifted characters to their unshifted base key (US QWERTY layout).
# type_text looks up ch.lower() in the keymap, but shifted symbols like _ ( ) don't
# lowercase to their base key. This table bridges the gap.
_SHIFT_TO_BASE: dict[str, str] = {
    "~": "`",
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "-",
    "+": "=",
    "{": "[",
    "}": "]",
    "|": "\\",
    ":": ";",
    '"': "'",
    "<": ",",
    ">": ".",
    "?": "/",
}


_XKB_EVDEV_OFFSET = 8  # XKB keycodes = evdev keycodes + 8


def _clipboard_paste(text: str, key_press_fn) -> None:
    """Paste text via wl-copy + Ctrl+V. Used for characters not in the keyboard layout."""
    try:
        # -o makes wl-copy exit after the first paste instead of blocking forever.
        subprocess.run(
            ["wl-copy", "-o"],
            input=text.encode("utf-8"),
            check=True,
            timeout=5,
            capture_output=True,
        )
        time.sleep(0.1)
        key_press_fn(["ctrl", "v"])
        time.sleep(0.15)
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.warning("wl-copy not available, cannot paste: %r", text[:20])


def _get_system_keyboard_layout() -> str:
    """Read the system keyboard layout from /etc/default/keyboard. Returns 'us' as default."""
    try:
        with open("/etc/default/keyboard") as f:
            for line in f:
                line = line.strip()
                if line.startswith("XKBLAYOUT="):
                    layout = line.split("=", 1)[1].strip('"').strip("'")
                    # Multi-layout configs use comma separation (e.g. "us,fr"). Take the first.
                    return layout.split(",")[0].strip() or "us"
    except (FileNotFoundError, PermissionError):
        pass
    return "us"


class _CharEntry:
    """How to produce a character: keycode + which modifiers to hold."""

    __slots__ = ("altgr", "keycode", "shift")

    def __init__(self, keycode: int, shift: bool = False, altgr: bool = False):
        self.keycode = keycode
        self.shift = shift
        self.altgr = altgr


_SHIFT_MASK = 0x01
_ALTGR_MASK = 0x80
# Standard main keyboard range (evdev keycodes 1-88 covers Esc through F12,
# all alphanumeric, punctuation, modifiers, and number row).
_MAIN_KBD_MIN = 1
_MAIN_KBD_MAX = 88


def _build_xkb_char_map(layout: str | None = None) -> dict[str, _CharEntry] | None:
    """Build char -> _CharEntry map using xkb_state.

    For each physical key on the main keyboard, asks xkb_state what character
    it produces with no mods, shift, altgr, and shift+altgr. Simpler combos
    are checked first so they naturally win (first match kept).
    Returns None if libxkbcommon is not available.
    """
    if _xkb is None:
        return None

    if layout is None:
        layout = _get_system_keyboard_layout()

    ctx = _xkb.xkb_context_new(0)
    if not ctx:
        return None

    names = _XkbRuleNames(
        rules=b"evdev",
        model=b"pc105",
        layout=layout.encode(),
        variant=None,
        options=None,
    )
    keymap = _xkb.xkb_keymap_new_from_names(ctx, ctypes.byref(names), 0)
    if not keymap:
        _xkb.xkb_context_unref(ctx)
        return None

    state = _xkb.xkb_state_new(keymap)
    if not state:
        _xkb.xkb_keymap_unref(keymap)
        _xkb.xkb_context_unref(ctx)
        return None

    # Check modifier combos in order of simplicity (fewest mods first).
    # First match wins, so plain keys beat shifted, shifted beats altgr, etc.
    _MOD_COMBOS = [
        (0, False, False),  # plain
        (_SHIFT_MASK, True, False),  # shift
        (_ALTGR_MASK, False, True),  # altgr
        (_SHIFT_MASK | _ALTGR_MASK, True, True),  # shift+altgr
    ]

    try:
        char_map: dict[str, _CharEntry] = {}

        for mask, shift, altgr in _MOD_COMBOS:
            _xkb.xkb_state_update_mask(state, mask, 0, 0, 0, 0, 0)
            for evdev_kc in range(_MAIN_KBD_MIN, _MAIN_KBD_MAX + 1):
                xkb_kc = evdev_kc + _XKB_EVDEV_OFFSET
                cp = _xkb.xkb_state_key_get_utf32(state, xkb_kc)
                if cp == 0:
                    continue
                ch = chr(cp)
                if ch not in char_map:
                    char_map[ch] = _CharEntry(evdev_kc, shift, altgr)

        return char_map if char_map else None
    finally:
        _xkb.xkb_state_unref(state)
        _xkb.xkb_keymap_unref(keymap)
        _xkb.xkb_context_unref(ctx)


def _build_evdev_key_map() -> dict[str, int]:
    """Build key name -> evdev keycode map. Uses ecodes if available, hardcoded fallback."""
    if ecodes is None:
        return dict(_FALLBACK_EVDEV_KEYCODES)
    return {
        "insert": ecodes.KEY_INSERT,
        "enter": ecodes.KEY_ENTER,
        "return": ecodes.KEY_ENTER,
        "tab": ecodes.KEY_TAB,
        "escape": ecodes.KEY_ESC,
        "esc": ecodes.KEY_ESC,
        "backspace": ecodes.KEY_BACKSPACE,
        "delete": ecodes.KEY_DELETE,
        "del": ecodes.KEY_DELETE,
        "up": ecodes.KEY_UP,
        "down": ecodes.KEY_DOWN,
        "left": ecodes.KEY_LEFT,
        "right": ecodes.KEY_RIGHT,
        "home": ecodes.KEY_HOME,
        "end": ecodes.KEY_END,
        "pageup": ecodes.KEY_PAGEUP,
        "pagedown": ecodes.KEY_PAGEDOWN,
        "space": ecodes.KEY_SPACE,
        " ": ecodes.KEY_SPACE,
        "ctrl": ecodes.KEY_LEFTCTRL,
        "control": ecodes.KEY_LEFTCTRL,
        "alt": ecodes.KEY_LEFTALT,
        "rightalt": ecodes.KEY_RIGHTALT,
        "shift": ecodes.KEY_LEFTSHIFT,
        "super": ecodes.KEY_LEFTMETA,
        "f1": ecodes.KEY_F1,
        "f2": ecodes.KEY_F2,
        "f3": ecodes.KEY_F3,
        "f4": ecodes.KEY_F4,
        "f5": ecodes.KEY_F5,
        "f6": ecodes.KEY_F6,
        "f7": ecodes.KEY_F7,
        "f8": ecodes.KEY_F8,
        "f9": ecodes.KEY_F9,
        "f10": ecodes.KEY_F10,
        "f11": ecodes.KEY_F11,
        "f12": ecodes.KEY_F12,
        **{chr(c): getattr(ecodes, f"KEY_{chr(c).upper()}") for c in range(ord("a"), ord("z") + 1)},
        **{str(i): getattr(ecodes, f"KEY_{i}") for i in range(10)},
        "-": ecodes.KEY_MINUS,
        "=": ecodes.KEY_EQUAL,
        "[": ecodes.KEY_LEFTBRACE,
        "]": ecodes.KEY_RIGHTBRACE,
        ";": ecodes.KEY_SEMICOLON,
        "'": ecodes.KEY_APOSTROPHE,
        ",": ecodes.KEY_COMMA,
        ".": ecodes.KEY_DOT,
        "/": ecodes.KEY_SLASH,
        "\\": ecodes.KEY_BACKSLASH,
        "`": ecodes.KEY_GRAVE,
    }


def _build_button_map() -> dict[str, int]:
    """Build button name -> evdev button code map."""
    if ecodes is None:
        return dict(_FALLBACK_BUTTON_CODES)
    return {"left": ecodes.BTN_LEFT, "right": ecodes.BTN_RIGHT, "middle": ecodes.BTN_MIDDLE}


class _WaylandActionExecutor(ActionExecutor):
    """Base for Wayland executors. Shared typing and key press logic.

    Subclasses provide _key_event() for their transport (evdev writes, DBus calls, etc.).
    """

    def __init__(self):
        self._key_map = _build_evdev_key_map()
        self._btn_map = _build_button_map()
        self._char_map = _build_xkb_char_map()  # layout-aware, None if xkb unavailable
        self._tracker = CursorTracker()

    def _key_event(self, keycode: int, down: bool) -> None:
        raise NotImplementedError

    def _btn_code(self, button: str) -> int:
        return self._btn_map.get(button, self._btn_map["left"])

    def type_text(self, text: str) -> None:
        # Type each character as individual key events, like a real human.
        # Clipboard paste (wl-copy + Ctrl+V) breaks in terminals and other apps
        # that expect Ctrl+Shift+V or handle paste differently.
        # 50ms between events avoids key repeat from Mutter's DBus processing.
        for char in text:
            self._type_char(char, delay=0.05)

    def _type_char(self, ch: str, *, delay: float) -> bool:
        shift_code = self._key_map.get("shift", 42)
        altgr_code = self._key_map.get("rightalt", 100)
        special = {"\n": "enter", "\t": "tab"}
        if ch in special:
            code = self._key_map.get(special[ch], 28)
            self._key_event(code, True)
            time.sleep(delay)
            self._key_event(code, False)
            time.sleep(delay)
            return False

        # Try layout-aware xkb map first, fall back to hardcoded US QWERTY.
        entry = self._char_map.get(ch) if self._char_map else None
        if entry:
            keycode = entry.keycode
            needs_shift = entry.shift
            needs_altgr = entry.altgr
        else:
            needs_shift = ch.isupper() or ch in _SHIFT_TO_BASE
            needs_altgr = False
            base_ch = _SHIFT_TO_BASE.get(ch, ch.lower())
            keycode = self._key_map.get(base_ch)
            if keycode is None:
                _clipboard_paste(ch, self.key_press)
                return True

        if needs_shift:
            self._key_event(shift_code, True)
            time.sleep(delay)
        if needs_altgr:
            self._key_event(altgr_code, True)
            time.sleep(delay)
        try:
            self._key_event(keycode, True)
            time.sleep(delay)
            self._key_event(keycode, False)
        finally:
            if needs_altgr:
                time.sleep(delay)
                self._key_event(altgr_code, False)
            if needs_shift:
                time.sleep(delay)
                self._key_event(shift_code, False)
        time.sleep(delay)
        return entry is None and ord(ch) > 0x7F

    def type_text_plan(self, plan: TypingPlan, *, cancelled=None, deadline=None) -> int:
        return consume_typing_plan(
            plan,
            lambda text: self._type_char(text, delay=0.0),
            cancelled=cancelled,
            deadline=deadline,
        )

    def key_press(self, keys: list[str]) -> None:
        if not keys:
            return
        codes = []
        for k in keys:
            code = None
            if self._char_map and len(k) == 1:
                # Single character: use layout-aware xkb map so shortcuts
                # work correctly on non-US layouts (e.g. Ctrl+A on AZERTY).
                entry = self._char_map.get(k)
                if entry:
                    code = entry.keycode
            if code is None:
                # Named keys (ctrl, enter, f1, etc.) or fallback.
                code = self._key_map.get(k.lower())
            if code is None:
                raise ActionError(f"unknown key: {k}")
            codes.append(code)
        for code in codes:
            self._key_event(code, True)
        time.sleep(0.03)
        for code in reversed(codes):
            self._key_event(code, False)


# ---------------------------------------------------------------------------
# Evdev action execution (Wayland -- writes directly to kernel input devices)
# ---------------------------------------------------------------------------


def _find_evdev_mouse() -> Optional["evdev_import.InputDevice"]:
    """Find the best mouse/pointer device. Prefers absolute (tablet) devices."""
    if evdev_import is None:
        return None

    best = None
    for path in evdev_import.list_devices():
        try:
            dev = evdev_import.InputDevice(path)
        except (PermissionError, OSError):
            continue

        caps = dev.capabilities()
        has_abs = ecodes.EV_ABS in caps
        has_btn = ecodes.EV_KEY in caps and ecodes.BTN_LEFT in [
            c if isinstance(c, int) else c[0] for c in caps.get(ecodes.EV_KEY, [])
        ]

        if has_abs and has_btn:
            # Absolute device with buttons (VBox tablet, Wacom, etc.) -- best option
            logger.info("evdev mouse: using ABS device %s (%s)", dev.name, path)
            if best is not None:
                best.close()
            return dev

        if best is None and has_btn:
            best = dev
        else:
            dev.close()

    if best:
        logger.info("evdev mouse: using REL device %s (%s)", best.name, best.path)
    return best


def _find_evdev_keyboard() -> Optional["evdev_import.InputDevice"]:
    """Find the keyboard input device."""
    if evdev_import is None:
        return None

    for path in evdev_import.list_devices():
        try:
            dev = evdev_import.InputDevice(path)
        except (PermissionError, OSError):
            continue

        caps = dev.capabilities()
        has_keys = ecodes.EV_KEY in caps
        # A real keyboard has letter keys, not just media buttons
        key_caps = [c if isinstance(c, int) else c[0] for c in caps.get(ecodes.EV_KEY, [])]
        has_letters = ecodes.KEY_A in key_caps and ecodes.KEY_Z in key_caps

        if has_keys and has_letters:
            logger.info("evdev keyboard: %s (%s)", dev.name, path)
            return dev
        dev.close()

    return None


class EvdevActionExecutor(_WaylandActionExecutor):
    """Wayland input via python-evdev. Writes directly to kernel input devices."""

    def __init__(
        self,
        mouse_dev: "evdev_import.InputDevice",
        kbd_dev: "evdev_import.InputDevice",
        screen_w: int = 1366,
        screen_h: int = 853,
    ):
        super().__init__()
        self._mouse = mouse_dev
        self._kbd = kbd_dev
        self._screen_w = screen_w
        self._screen_h = screen_h

        # Check if mouse supports absolute positioning
        caps = mouse_dev.capabilities()
        abs_caps = caps.get(ecodes.EV_ABS, [])
        self._has_abs = any((c[0] if isinstance(c, tuple) else c) == ecodes.ABS_X for c in abs_caps)

        if self._has_abs:
            # Read the device's coordinate range
            for item in abs_caps:
                code, info = item if isinstance(item, tuple) else (item, None)
                if code == ecodes.ABS_X and info:
                    self._abs_max_x = info.max
                elif code == ecodes.ABS_Y and info:
                    self._abs_max_y = info.max
            if not hasattr(self, "_abs_max_x"):
                self._abs_max_x = 32767
            if not hasattr(self, "_abs_max_y"):
                self._abs_max_y = 32767

    def _move_abs(self, x: int, y: int) -> None:
        """Move cursor using absolute coordinates."""
        ax = int(x * self._abs_max_x / self._screen_w)
        ay = int(y * self._abs_max_y / self._screen_h)
        self._mouse.write(ecodes.EV_ABS, ecodes.ABS_X, ax)
        self._mouse.write(ecodes.EV_ABS, ecodes.ABS_Y, ay)
        self._mouse.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)

    def _move_rel(self, x: int, y: int) -> None:
        """Move cursor using reset-to-origin + relative movement."""
        # Slam to top-left
        self._mouse.write(ecodes.EV_REL, ecodes.REL_X, -(self._screen_w + 200))
        self._mouse.write(ecodes.EV_REL, ecodes.REL_Y, -(self._screen_h + 200))
        self._mouse.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
        time.sleep(0.015)
        # Walk to target in chunks
        rx, ry = x, y
        while rx > 0 or ry > 0:
            dx = min(rx, 50)
            dy = min(ry, 50)
            self._mouse.write(ecodes.EV_REL, ecodes.REL_X, dx)
            self._mouse.write(ecodes.EV_REL, ecodes.REL_Y, dy)
            self._mouse.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
            rx -= dx
            ry -= dy
            time.sleep(0.003)

    def _raw_move(self, x: int, y: int) -> None:
        if self._has_abs:
            self._move_abs(x, y)
        else:
            self._move_rel(x, y)
        self._tracker.update(x, y)

    def move_mouse(self, x: int, y: int) -> None:
        smooth_move(x, y, self._tracker.get_pos, self._raw_move)

    def click(self, x: int, y: int, button: str = "left") -> None:
        self.move_mouse(x, y)
        time.sleep(PRE_CLICK_BASE + random.random() * PRE_CLICK_RAND)
        btn = self._btn_code(button)
        self._mouse.write(ecodes.EV_KEY, btn, 1)
        self._mouse.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
        time.sleep(0.04)
        self._mouse.write(ecodes.EV_KEY, btn, 0)
        self._mouse.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)

    def double_click(self, x: int, y: int) -> None:
        self.move_mouse(x, y)
        time.sleep(PRE_CLICK_BASE + random.random() * PRE_CLICK_RAND)
        btn = self._btn_code("left")
        for _ in range(2):
            self._mouse.write(ecodes.EV_KEY, btn, 1)
            self._mouse.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
            time.sleep(0.04)
            self._mouse.write(ecodes.EV_KEY, btn, 0)
            self._mouse.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
            time.sleep(0.08)

    def _key_event(self, keycode: int, down: bool) -> None:
        self._kbd.write(ecodes.EV_KEY, keycode, 1 if down else 0)
        self._kbd.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)

    def scroll(self, x: int, y: int, amount: int) -> None:
        self._raw_move(x, y)
        time.sleep(0.05)
        # REL_WHEEL: positive = up, negative = down (matches our API)
        for _ in range(abs(amount)):
            self._mouse.write(ecodes.EV_REL, ecodes.REL_WHEEL, 1 if amount > 0 else -1)
            self._mouse.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
            time.sleep(0.03)

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: float = 0.5,
    ) -> None:
        btn = self._btn_code("left")
        self.move_mouse(start_x, start_y)
        time.sleep(PRE_DRAG_BASE + random.random() * PRE_DRAG_RAND)
        self._mouse.write(ecodes.EV_KEY, btn, 1)
        self._mouse.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)

        path = windmouse_path(
            start_x,
            start_y,
            end_x,
            end_y,
            gravity=DRAG_GRAVITY,
            wind=DRAG_WIND,
            max_vel=DRAG_MAX_VEL,
        )
        delays = generate_delays(len(path), duration)
        for i, (px, py) in enumerate(path):
            self._raw_move(px, py)
            if i < len(delays):
                time.sleep(delays[i])

        self._mouse.write(ecodes.EV_KEY, btn, 0)
        self._mouse.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)


# ---------------------------------------------------------------------------
# Pure-python uinput action execution (Wayland fallback -- no evdev/compiler)
# ---------------------------------------------------------------------------


class UinputActionExecutor(_WaylandActionExecutor):
    """Wayland input via the pure-python uinput device (no evdev package).

    The non-GNOME Wayland fallback: writes a virtual absolute-pointer + keyboard
    to /dev/uinput. Reuses the layout-aware typing/key logic from the base; only
    the transport (a UinputDevice) differs. Raises OSError from the constructor
    if /dev/uinput is not writable, so the factory can fall through cleanly.
    """

    def __init__(self, screen_w: int = 1366, screen_h: int = 768):
        super().__init__()
        self._dev = UinputDevice(screen_w=screen_w, screen_h=screen_h)

    def _raw_move(self, x: int, y: int) -> None:
        self._dev.move_abs(x, y)
        self._tracker.update(x, y)

    def move_mouse(self, x: int, y: int) -> None:
        smooth_move(x, y, self._tracker.get_pos, self._raw_move)

    def _key_event(self, keycode: int, down: bool) -> None:
        self._dev.key(keycode, down)

    def click(self, x: int, y: int, button: str = "left") -> None:
        self.move_mouse(x, y)
        time.sleep(PRE_CLICK_BASE + random.random() * PRE_CLICK_RAND)
        self._dev.button(self._btn_code(button), True)
        time.sleep(0.04)
        self._dev.button(self._btn_code(button), False)

    def double_click(self, x: int, y: int) -> None:
        self.move_mouse(x, y)
        time.sleep(PRE_CLICK_BASE + random.random() * PRE_CLICK_RAND)
        for _ in range(2):
            self._dev.button(self._btn_code("left"), True)
            time.sleep(0.04)
            self._dev.button(self._btn_code("left"), False)
            time.sleep(0.08)

    def scroll(self, x: int, y: int, amount: int) -> None:
        self._raw_move(x, y)
        time.sleep(0.05)
        for _ in range(abs(amount)):
            self._dev.wheel(1 if amount > 0 else -1)
            time.sleep(0.03)

    def drag(self, start_x, start_y, end_x, end_y, duration: float = 0.5) -> None:
        self.move_mouse(start_x, start_y)
        time.sleep(PRE_DRAG_BASE + random.random() * PRE_DRAG_RAND)
        self._dev.button(self._btn_code("left"), True)
        path = windmouse_path(
            start_x,
            start_y,
            end_x,
            end_y,
            gravity=DRAG_GRAVITY,
            wind=DRAG_WIND,
            max_vel=DRAG_MAX_VEL,
        )
        delays = generate_delays(len(path), duration)
        for i, (px, py) in enumerate(path):
            self._raw_move(px, py)
            if i < len(delays):
                time.sleep(delays[i])
        self._dev.button(self._btn_code("left"), False)


# ---------------------------------------------------------------------------
# Mutter RemoteDesktop (GNOME Wayland -- proper input injection via DBus)
# ---------------------------------------------------------------------------


_MUTTER_RD_BUS = "org.gnome.Mutter.RemoteDesktop"
_MUTTER_RD_PATH = "/org/gnome/Mutter/RemoteDesktop"
_MUTTER_RD_IFACE = "org.gnome.Mutter.RemoteDesktop"
_MUTTER_RD_SESSION_IFACE = "org.gnome.Mutter.RemoteDesktop.Session"
_MUTTER_SC_BUS = "org.gnome.Mutter.ScreenCast"
_MUTTER_SC_PATH = "/org/gnome/Mutter/ScreenCast"
_MUTTER_SC_IFACE = "org.gnome.Mutter.ScreenCast"
_MUTTER_SC_SESSION_IFACE = "org.gnome.Mutter.ScreenCast.Session"
_DBUS_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"


def _is_mutter_available() -> bool:
    """Check if Mutter RemoteDesktop DBus interface is reachable."""
    if jeepney_import is None or _open_dbus_connection is None:
        return False
    conn = None
    try:
        addr = jeepney_import.DBusAddress(
            object_path=_MUTTER_RD_PATH,
            bus_name=_MUTTER_RD_BUS,
            interface=_DBUS_PROPERTIES_IFACE,
        )
        conn = _open_dbus_connection(bus="SESSION")
        msg = jeepney_import.new_method_call(addr, "GetAll", "s", (_MUTTER_RD_IFACE,))
        reply = conn.send_and_get_reply(msg, timeout=2.0)
        return reply.header.message_type == jeepney_import.MessageType.method_return
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _mutter_rd_addr() -> "jeepney_import.DBusAddress":
    return jeepney_import.DBusAddress(
        object_path=_MUTTER_RD_PATH,
        bus_name=_MUTTER_RD_BUS,
        interface=_MUTTER_RD_IFACE,
    )


def _mutter_rd_session_addr(session_path: str) -> "jeepney_import.DBusAddress":
    return jeepney_import.DBusAddress(
        object_path=session_path,
        bus_name=_MUTTER_RD_BUS,
        interface=_MUTTER_RD_SESSION_IFACE,
    )


def _mutter_rd_session_props_addr(session_path: str) -> "jeepney_import.DBusAddress":
    return jeepney_import.DBusAddress(
        object_path=session_path,
        bus_name=_MUTTER_RD_BUS,
        interface=_DBUS_PROPERTIES_IFACE,
    )


def _mutter_sc_addr() -> "jeepney_import.DBusAddress":
    return jeepney_import.DBusAddress(
        object_path=_MUTTER_SC_PATH,
        bus_name=_MUTTER_SC_BUS,
        interface=_MUTTER_SC_IFACE,
    )


def _mutter_sc_session_addr(session_path: str) -> "jeepney_import.DBusAddress":
    return jeepney_import.DBusAddress(
        object_path=session_path,
        bus_name=_MUTTER_SC_BUS,
        interface=_MUTTER_SC_SESSION_IFACE,
    )


def _unwrap_variant(variant) -> object:
    # jeepney Get returns ('signature', value)
    if isinstance(variant, tuple) and len(variant) == 2:
        return variant[1]
    return variant


class MutterRemoteDesktopExecutor(_WaylandActionExecutor):
    """GNOME Wayland input via Mutter RemoteDesktop + ScreenCast DBus over jeepney."""

    def __init__(self):
        super().__init__()
        self._conn = None
        self._session_path: str | None = None
        self._stream_path: str | None = None
        self._setup_session()

    def _call(self, addr, method: str, signature: str = "", body: tuple = ()):
        msg = jeepney_import.new_method_call(addr, method, signature or None, body)
        reply = self._conn.send_and_get_reply(msg)
        if reply.header.message_type == jeepney_import.MessageType.error:
            err = reply.header.fields.get(jeepney_import.HeaderFields.error_name, "unknown")
            raise ActionError(f"Mutter DBus error {err}: {reply.body}")
        return reply.body

    def _setup_session(self) -> None:
        if jeepney_import is None or _open_dbus_connection is None:
            raise PlatformNotSupportedError(
                "jeepney is required for GNOME Wayland input. "
                "Install with: pip install vadgr-computer-use"
            )

        self._conn = _open_dbus_connection(bus="SESSION")

        body = self._call(_mutter_rd_addr(), "CreateSession")
        self._session_path = body[0]

        sid_body = self._call(
            _mutter_rd_session_props_addr(self._session_path),
            "Get",
            "ss",
            (_MUTTER_RD_SESSION_IFACE, "SessionId"),
        )
        session_id = _unwrap_variant(sid_body[0])

        sc_body = self._call(
            _mutter_sc_addr(),
            "CreateSession",
            "a{sv}",
            ([("remote-desktop-session-id", ("s", session_id))],),
        )
        sc_session_path = sc_body[0]

        stream_body = self._call(
            _mutter_sc_session_addr(sc_session_path),
            "RecordMonitor",
            "sa{sv}",
            ("", []),
        )
        self._stream_path = stream_body[0]

        self._call(_mutter_rd_session_addr(self._session_path), "Start")
        logger.info("Mutter RemoteDesktop session started (jeepney)")

    def _ensure_session(self) -> None:
        if self._session_path is None:
            self._setup_session()

    def _session_call(self, method: str, signature: str = "", body: tuple = ()) -> None:
        self._ensure_session()
        self._call(_mutter_rd_session_addr(self._session_path), method, signature, body)

    def _raw_move(self, x: int, y: int) -> None:
        self._session_call(
            "NotifyPointerMotionAbsolute",
            "sdd",
            (self._stream_path, float(x), float(y)),
        )
        self._tracker.update(x, y)

    def move_mouse(self, x: int, y: int) -> None:
        smooth_move(x, y, self._tracker.get_pos, self._raw_move)

    def _button(self, btn: int, pressed: bool) -> None:
        self._session_call("NotifyPointerButton", "ib", (int(btn), bool(pressed)))

    def click(self, x: int, y: int, button: str = "left") -> None:
        btn = self._btn_code(button)
        self.move_mouse(x, y)
        time.sleep(PRE_CLICK_BASE + random.random() * PRE_CLICK_RAND)
        self._button(btn, True)
        time.sleep(0.04)
        self._button(btn, False)

    def double_click(self, x: int, y: int) -> None:
        btn = self._btn_code("left")
        self.move_mouse(x, y)
        time.sleep(PRE_CLICK_BASE + random.random() * PRE_CLICK_RAND)
        for _ in range(2):
            self._button(btn, True)
            time.sleep(0.04)
            self._button(btn, False)
            time.sleep(0.08)

    def _key_event(self, keycode: int, down: bool) -> None:
        self._session_call("NotifyKeyboardKeycode", "ub", (int(keycode), bool(down)))

    def _keysym_event(self, keysym: int, down: bool) -> None:
        self._session_call("NotifyKeyboardKeysym", "ub", (int(keysym), bool(down)))

    def type_text(self, text: str) -> None:
        # Use keycodes from xkb char map (layout-aware). Characters not in the
        # layout (dead-key combos, CJK, etc.) go through clipboard paste.
        delay = 0.05
        shift_code = self._key_map.get("shift", 42)
        altgr_code = self._key_map.get("rightalt", 100)
        special = {"\n": "enter", "\t": "tab"}

        # Split text into runs: keycode-typeable chars vs clipboard-needed chars.
        clipboard_buf: list[str] = []

        def _flush_clipboard() -> None:
            if not clipboard_buf:
                return
            chunk = "".join(clipboard_buf)
            clipboard_buf.clear()
            _clipboard_paste(chunk, self.key_press)

        for ch in text:
            if ch in special:
                _flush_clipboard()
                code = self._key_map.get(special[ch], 28)
                self._key_event(code, True)
                time.sleep(delay)
                self._key_event(code, False)
                time.sleep(delay)
                continue

            entry = self._char_map.get(ch) if self._char_map else None
            if entry is None:
                # Fallback for hardcoded US QWERTY
                needs_shift = ch.isupper() or ch in _SHIFT_TO_BASE
                base_ch = _SHIFT_TO_BASE.get(ch, ch.lower())
                kc = self._key_map.get(base_ch)
                if kc is not None:
                    entry = _CharEntry(kc, needs_shift)

            if entry is None:
                # Not in layout - queue for clipboard paste
                clipboard_buf.append(ch)
                continue

            _flush_clipboard()
            if entry.shift:
                self._key_event(shift_code, True)
                time.sleep(delay)
            if entry.altgr:
                self._key_event(altgr_code, True)
                time.sleep(delay)
            self._key_event(entry.keycode, True)
            time.sleep(delay)
            self._key_event(entry.keycode, False)
            if entry.altgr:
                time.sleep(delay)
                self._key_event(altgr_code, False)
            if entry.shift:
                time.sleep(delay)
                self._key_event(shift_code, False)
            time.sleep(delay)

        _flush_clipboard()

    def key_press(self, keys: list[str]) -> None:
        if not keys:
            return
        codes = []
        for k in keys:
            code = None
            if self._char_map and len(k) == 1:
                entry = self._char_map.get(k)
                if entry:
                    code = entry.keycode
            if code is None:
                code = self._key_map.get(k.lower())
            if code is None:
                raise ActionError(f"unknown key: {k}")
            codes.append(code)
        for code in codes:
            self._key_event(code, True)
        time.sleep(0.03)
        for code in reversed(codes):
            self._key_event(code, False)

    def scroll(self, x: int, y: int, amount: int) -> None:
        self._raw_move(x, y)
        time.sleep(0.05)
        # Mutter RemoteDesktop axis convention (empirically verified):
        # positive dy = scroll down, negative dy = scroll up.
        # API contract: positive amount = up, negative = down.
        step_discrete = -1 if amount > 0 else 1
        step_pixels = -15.0 if amount > 0 else 15.0
        for _ in range(abs(amount)):
            self._session_call("NotifyPointerAxisDiscrete", "ui", (0, int(step_discrete)))
            self._session_call(
                "NotifyPointerAxis",
                "ddu",
                (0.0, float(step_pixels), 0),
            )
            time.sleep(0.03)
        self._session_call("NotifyPointerAxis", "ddu", (0.0, 0.0, 1))

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: float = 0.5,
    ) -> None:
        btn = self._btn_code("left")
        self.move_mouse(start_x, start_y)
        time.sleep(PRE_DRAG_BASE + random.random() * PRE_DRAG_RAND)
        self._button(btn, True)

        path = windmouse_path(
            start_x,
            start_y,
            end_x,
            end_y,
            gravity=DRAG_GRAVITY,
            wind=DRAG_WIND,
            max_vel=DRAG_MAX_VEL,
        )
        delays = generate_delays(len(path), duration)
        for i, (px, py) in enumerate(path):
            self._raw_move(px, py)
            if i < len(delays):
                time.sleep(delays[i])

        self._button(btn, False)

    def close(self) -> None:
        if self._session_path is not None and self._conn is not None:
            try:
                self._call(_mutter_rd_session_addr(self._session_path), "Stop")
            except Exception:
                pass
        self._session_path = None
        self._stream_path = None
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


# ---------------------------------------------------------------------------
# Action executor factory
# ---------------------------------------------------------------------------


def _create_action_executor(screen_w: int = 1366, screen_h: int = 853) -> ActionExecutor:
    """Pick the best action executor for the current session.

    Priority: Mutter RemoteDesktop (GNOME) > evdev (other Wayland) > xdotool (X11).
    """
    if not _is_wayland():
        # X11: prefer pure-python XTEST; fall back to xdotool if python-xlib or
        # the XTEST extension is unavailable.
        try:
            executor = XTestExecutor()
            logger.info("X11: using XTEST (python-xlib)")
            return executor
        except Exception as e:
            logger.info("X11: XTEST unavailable (%s), falling back to xdotool", e)
            return LinuxActionExecutor()

    if _is_mutter_available():
        try:
            executor = MutterRemoteDesktopExecutor()
            logger.info("Wayland: using Mutter RemoteDesktop")
            return executor
        except Exception as e:
            logger.warning("Mutter RemoteDesktop failed: %s, trying evdev", e)

    if evdev_import is not None:
        mouse = _find_evdev_mouse()
        kbd = _find_evdev_keyboard()
        if mouse is not None and kbd is not None:
            logger.info("Wayland: using evdev")
            return EvdevActionExecutor(mouse, kbd, screen_w, screen_h)
        if mouse:
            mouse.close()
        if kbd:
            kbd.close()

    # Pure-python uinput fallback (no evdev package, no compiler). Needs
    # /dev/uinput writable (the installer ships a udev rule for that).
    try:
        executor = UinputActionExecutor(screen_w, screen_h)
        logger.info("Wayland: using pure-python uinput")
        return executor
    except OSError as e:
        logger.warning("uinput fallback unavailable: %s", e)

    raise PlatformNotSupportedError(
        "No working Wayland input method. On GNOME, ensure Mutter is running "
        "(jeepney must reach org.gnome.Mutter.RemoteDesktop). On Sway/Hyprland, "
        "install evdev wheels (already a runtime dep) and add your user to the "
        "input group: sudo usermod -aG input $USER, then log out and back in."
    )


# ---------------------------------------------------------------------------
# Foreground window detection
# ---------------------------------------------------------------------------

_FG_WINDOW_TTL = 0.1  # 100ms cache
_fg_window_cache: tuple[float, ForegroundWindow | None] | None = None


def _get_foreground_window_linux() -> ForegroundWindow | None:
    global _fg_window_cache
    now = time.monotonic()
    if _fg_window_cache is not None:
        ts, cached = _fg_window_cache
        if now - ts < _FG_WINDOW_TTL:
            return cached

    result = _query_foreground_window()
    _fg_window_cache = (now, result)
    return result


def _query_foreground_window_wayland() -> ForegroundWindow | None:
    """Get the focused window on Wayland via AT-SPI2 accessibility.

    Speaks AT-SPI over the pure-python dbus-fast client (the structured tier's
    own client), not PyGObject: an isolated venv cannot import gi, so the old
    gi.Atspi path was dead on every install this package produces. Any failure,
    including no accessibility bus, returns None so the engine keeps running and
    the caller falls back to XWayland.
    """
    try:
        from computer_use.tools.ui.atspi import foreground_window
    except Exception:
        return None
    return foreground_window()


def _query_foreground_window_xdotool() -> ForegroundWindow | None:
    """Get the focused window on X11 via xdotool."""
    try:
        result = subprocess.run(
            [
                "xdotool",
                "getactivewindow",
                "getwindowname",
                "getactivewindow",
                "getwindowgeometry",
                "--shell",
                "getactivewindow",
                "getwindowpid",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if result.returncode != 0:
            return None

        lines = result.stdout.strip().split("\n")
        if len(lines) < 2:
            return None

        title = lines[0]
        geo = {}
        pid = 0
        for line in lines[1:]:
            if "=" in line:
                k, v = line.split("=", 1)
                geo[k.strip()] = v.strip()
            elif line.strip().isdigit():
                pid = int(line.strip())

        x = int(geo.get("X", 0))
        y = int(geo.get("Y", 0))
        width = int(geo.get("WIDTH", 0))
        height = int(geo.get("HEIGHT", 0))

        app_name = ""
        if pid > 0:
            try:
                with open(f"/proc/{pid}/comm") as f:
                    app_name = f.read().strip()
            except OSError:
                pass

        return ForegroundWindow(
            app_name=app_name,
            title=title,
            x=x,
            y=y,
            width=width,
            height=height,
            pid=pid,
        )
    except Exception:
        return None


def _query_foreground_window() -> ForegroundWindow | None:
    """Get the focused window. Tries Wayland (AT-SPI2) first, then X11 (xdotool)."""
    if _is_wayland():
        result = _query_foreground_window_wayland()
        if result:
            return result
    return _query_foreground_window_xdotool()


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


def _wayland_screenshot_tool_available() -> bool:
    return (
        any(shutil.which(tool) for tool, _cls in _WAYLAND_TOOLS)
        or portal_available()
        or _gnome_shell_capture_available()
    )


class LinuxBackend(PlatformBackend):
    def __init__(self):
        self._capture: ScreenCapture | None = None
        self._executor: ActionExecutor | None = None

    def get_screen_capture(self) -> ScreenCapture:
        if self._capture is None:
            self._capture = _create_screen_capture()
        return self._capture

    def get_action_executor(self) -> ActionExecutor:
        if self._executor is None:
            self._executor = _create_action_executor()
        return self._executor

    def is_available(self) -> bool:
        return self.availability_report().available

    def availability_report(self) -> AvailabilityReport:
        if _is_wayland():
            return self._wayland_report()
        return self._x11_report()

    def _wayland_report(self) -> AvailabilityReport:
        missing: list[str] = []
        remediations: list[str] = []

        has_mutter = _is_mutter_available()
        has_evdev = evdev_import is not None

        if not has_mutter and not has_evdev:
            if jeepney_import is None:
                missing.append("jeepney")
                remediations.append(
                    "pip install vadgr-computer-use (jeepney is a declared runtime "
                    "dep; if absent, your install is incomplete)"
                )
            else:
                missing.append("input-injection")
                remediations.append(
                    "On GNOME, start a Wayland session (org.gnome.Mutter.RemoteDesktop "
                    "must be on the session bus). On Sway/Hyprland, install evdev and "
                    "add your user to the 'input' group."
                )

        if not _wayland_screenshot_tool_available():
            missing.append("screenshot-tool")
            remediations.append(
                "Install a Wayland screenshot tool: apt install gnome-screenshot "
                "(GNOME) or apt install grim (wlroots: Sway, Hyprland)."
            )

        if not missing:
            return AvailabilityReport(available=True)
        return AvailabilityReport(
            available=False,
            missing=tuple(missing),
            remediation=" ".join(remediations),
        )

    def _x11_report(self) -> AvailabilityReport:
        if shutil.which("xdotool"):
            return AvailabilityReport(available=True)
        return AvailabilityReport(
            available=False,
            missing=("xdotool",),
            remediation=(
                "Install xdotool: apt install xdotool (Debian/Ubuntu), "
                "dnf install xdotool (Fedora), pacman -S xdotool (Arch)."
            ),
        )

    def get_foreground_window(self) -> ForegroundWindow | None:
        return _get_foreground_window_linux()

    def structured_capability(self) -> dict:
        # The AT-SPI tier's own report: bus reachability, whether it is enabled,
        # the coordinate trust (real on X11, none on Wayland) and the toolkits
        # seen. Imported here so a box with no accessibility stack still loads
        # the backend and answers, rather than failing at import.
        from computer_use.tools.ui.backend import structured_capability

        return structured_capability()
