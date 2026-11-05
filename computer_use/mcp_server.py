"""MCP server for the computer use engine.

Run: python -m computer_use.mcp_server [--transport stdio|sse] [--max-width 1366]
"""

import argparse
import functools
import inspect
import io
import logging
import math
import os
import queue
import sys
import threading
import time as _time_module
from importlib.metadata import PackageNotFoundError, version

import anyio
import pydantic_core

_DEBUG = os.environ.get("VADGR_DEBUG", "") == "1"

logging.basicConfig(
    level=logging.DEBUG if _DEBUG else logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("computer_use.mcp_server")
_DEBUG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".debug")
_debug_counter = 0


async def _run_cooperative_thread(call, cancellation: threading.Event):
    """Cancel between complete units, then return the worker's truthful outcome."""
    outcome: queue.SimpleQueue = queue.SimpleQueue()

    def run() -> None:
        try:
            outcome.put((True, call()))
        except BaseException as error:
            outcome.put((False, error))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    cancellation_error = None
    try:
        while worker.is_alive():
            await anyio.sleep(0.02)
    except anyio.get_cancelled_exc_class() as error:
        cancellation_error = error
        cancellation.set()
        with anyio.CancelScope(shield=True):
            while worker.is_alive():
                await anyio.sleep(0.02)
    worker.join()
    ok, value = outcome.get_nowait()
    if ok:
        if cancellation_error is not None:
            raise cancellation_error
        return value
    raise value


def _debug_save(data: bytes, prefix: str = "screenshot") -> None:
    """Save PNG to .debug/ when VADGR_DEBUG=1."""
    if not _DEBUG:
        return
    global _debug_counter
    os.makedirs(_DEBUG_DIR, exist_ok=True)
    _debug_counter += 1
    path = os.path.join(_DEBUG_DIR, f"{prefix}_{_debug_counter:04d}.png")
    with open(path, "wb") as f:
        f.write(data)
    logger.info("Debug screenshot saved: %s", path)


from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image as PILImage

from computer_use import indicator as _indicator
from computer_use.core import REGISTRY, Risk, Tier, tool
from computer_use.platform.backends.gnome_shell import UserStopped, default_client
from computer_use.platform.detect import detect_platform


class _CompactMCPServer(MCPServer):
    """The MCPServer every tool is registered on: compact results, and the cue.

    Compact: MCPServer serializes a dict result with indent=2, the client hands
    that text to the model as is, and the indentation is about 40% of the tokens
    of a ui_tree, ui_windows or browser read. A dict result is converted before
    the SDK sees it, with the same serializer minus the indent. str, Image and
    list results keep their own conversion. A tool whose return annotation makes
    the SDK validate an object schema (type_text) keeps its dict, because a
    string fails that validation. A direct Python call still returns the dict.

    Cue: this wrapper is the one place every tool call passes through, so it is
    where a desktop tool tells the on-screen indicator what it is about to do
    (see ``computer_use/indicator.py``). When the user has pressed Esc, a cued
    tool does not run and returns ``user_stopped`` instead.
    """

    def tool(self, *args, **kwargs):
        register = super().tool(*args, **kwargs)

        def decorator(fn):
            name = kwargs.get("name") or fn.__name__
            keeps_dict = _validates_an_object(fn, kwargs.get("structured_output"))
            cued = name in _indicator.INDICATED_TOOLS
            signature = inspect.signature(fn)
            # The SDK wraps a str result as {"result": str}, so the stop goes out
            # as text there: a JSON string inside it reached the model escaped.
            stop_as_text = signature.return_annotation in (str, "str")

            def stopped():
                stop = _indicator.stopped_result()
                return f"{stop['error']}: {stop['message']}" if stop_as_text else deliver(stop)

            def deliver(result):
                if cued:
                    _ELEMENTS.observe(name, result)
                return result if keeps_dict else _compact(result)

            if inspect.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def wire(*a, **kw):
                    if cued and await anyio.to_thread.run_sync(
                        _cue, name, signature, a, kw
                    ):
                        return stopped()
                    try:
                        return deliver(await fn(*a, **kw))
                    except UserStopped:
                        return stopped()
            else:
                @functools.wraps(fn)
                def wire(*a, **kw):
                    if cued and _cue(name, signature, a, kw):
                        return stopped()
                    try:
                        return deliver(fn(*a, **kw))
                    except UserStopped:
                        return stopped()
            register(wire)
            return fn

        return decorator


def _validates_an_object(fn, structured_output) -> bool:
    """Whether the SDK validates this tool's result against an object schema.

    True for a dict-typed return such as type_text's. A str return is also
    validated, but wrapped as {"result": str}, so a dict for it must still be
    compacted into a string; and a bare dict or no annotation is not validated.
    """
    try:
        from mcp.server.mcpserver.tools import Tool as SdkTool

        meta = SdkTool.from_function(fn, structured_output=structured_output).fn_metadata
        return meta.output_schema is not None and not getattr(meta, "wrap_output", False)
    except Exception:
        return False


def _compact(result):
    if isinstance(result, dict):
        return pydantic_core.to_json(result, fallback=str).decode()
    return result


# Names and trusted bounds from recent ui_* reads, so a ui_act cue can say
# what it is about to act on.
_ELEMENTS = _indicator.ElementMemory()


def _cue(name: str, signature: inspect.Signature, args: tuple, kwargs: dict) -> bool:
    """Tell the indicator what the tool is about to do. True: the user stopped it.

    Never raises. The indicator is for the person at the screen, and a tool
    must not fail because it could not be drawn.
    """
    try:
        bound = signature.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        cue = _indicator.cue_for(name, bound.arguments, _to_real, _ELEMENTS)
        return default_client().begin(
            cue.tool, cue.text, cue.x, cue.y, cue.width, cue.height, cue.own_escape
        )
    except Exception as exc:
        logger.debug("indicator cue skipped for %s: %s", name, exc)
        return False


mcp = _CompactMCPServer(
    name="computer-use",
    instructions=(
        "Desktop automation. Prefer the structured tiers: a targeted read is a "
        "few dozen tokens and an act is confirmed by its own re-read, while a "
        "full screenshot is 1,500 tokens or more. Pixels are the fallback, not "
        "the default.\n\n"
        "1. Web pages: `browser` (DOM selectors). Confirm each mutating op "
        "with its returned `ok`/`checked` or a read-back (get_value, "
        "read_text, wait_for), not a screenshot.\n"
        "2. Native app windows: `ui_act(action=..., name=..., role=...)` "
        "finds the control and acts in one call when exactly one matches "
        "(roles read like \"button\", \"toggle button\", \"text box\", "
        "\"label\"). On `ambiguous` or `not_found` it lists candidate refs: "
        "act on one with `ui_act(ref=...)`, or `ui_find` to look further. The "
        "act returns the element re-read, so it needs no screenshot. Use "
        "`ui_tree` only to discover what to search for, with a small depth: a "
        "whole window can cost thousands of tokens. If a ui_* call reports the "
        "tier unavailable, or "
        "a window's tree is empty, go to step 3. On Linux, a Chromium or "
        "Electron window with an empty tree builds one when it is relaunched "
        "through `app_open`.\n"
        "3. Pixels, only when steps 1 and 2 cannot reach the target (canvas, "
        "games, an empty tree, a visual judgment): take a screenshot, use "
        "coordinates from the latest screenshot only, and take another only "
        "when the result cannot be read back. format=\"thumbnail\" is enough "
        "for a sanity check.\n\n"
        "If a desktop tool returns error `user_stopped`, the person at the "
        "screen pressed Esc: stop calling desktop tools and wait for their "
        "next instruction."
    ),
)

_MAX_WIDTH = int(os.environ.get("CU_MAX_WIDTH", "0"))  # 0 = auto-detect

# Hard ceiling: stay under Anthropic's 2000px per-image dimension cap that
# trips many-image requests (see anthropics/claude-code#37461, #46656).
_MAX_DIMENSION_CEILING = 1600

# Thumbnail mode: aggressive shrink for sanity-check screenshots in long flows.
_THUMBNAIL_WIDTH = 640
_JPEG_QUALITY = 70
_THUMBNAIL_QUALITY = 40

# Coordinate mapping state. Updated after each screenshot.
# Display coords (what the agent sees) get mapped to real screen coords via _to_real().
_scale_x = 1.0
_scale_y = 1.0
_display_w = 0
_display_h = 0
_offset_x = 0  # primary monitor X origin in virtual screen space
_offset_y = 0  # primary monitor Y origin in virtual screen space

_engine = None


def _compute_max_width(real_width: int) -> int:
    """Pick the largest target width that keeps coordinates accurate for vision models.

    Returns the highest standard resolution that is <= the real screen width.
    Standard targets (most universal across vision models): 1024, 1280, 1366.
    """
    targets = [1024, 1280, 1366]
    for t in reversed(targets):
        if real_width >= t:
            return t
    return real_width  # screen is smaller than 1024, no downscale


def _get_engine():
    global _engine, _MAX_WIDTH
    if _engine is None:
        from computer_use.core.engine import ComputerUseEngine

        _engine = ComputerUseEngine()
        logger.info("Engine initialized (platform=%s)", _engine.get_platform().value)
        if _MAX_WIDTH == 0:
            w, _ = _engine.get_screen_size()
            _MAX_WIDTH = _compute_max_width(w)
            logger.info("Auto-detected max width: %d (screen=%d)", _MAX_WIDTH, w)
    return _engine


def _resolve_format(fmt: str) -> str:
    """Normalize the user-facing format string. Raises ValueError on unknown."""
    f = (fmt or "jpeg").lower()
    if f == "jpg":
        f = "jpeg"
    if f not in ("jpeg", "png", "thumbnail"):
        raise ValueError(
            f"Unsupported format: {fmt!r}. Use 'jpeg' (default), 'png', or 'thumbnail'."
        )
    return f


def _encode_image(img: "PILImage.Image", fmt: str) -> tuple[bytes, str]:
    """Encode a PIL image to bytes in the given format.

    Returns (bytes, image_format) where image_format is what MCPServer's
    Image() constructor expects ("jpeg" or "png").
    """
    buf = io.BytesIO()
    if fmt == "png":
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "png"
    # jpeg or thumbnail
    rgb = img if img.mode == "RGB" else img.convert("RGB")
    quality = _THUMBNAIL_QUALITY if fmt == "thumbnail" else _JPEG_QUALITY
    rgb.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), "jpeg"


def _downscale(
    png_bytes: bytes,
    offset_x: int = 0,
    offset_y: int = 0,
    fmt: str = "jpeg",
) -> tuple[bytes, str]:
    """Resize screenshot, encode in requested format, update scale/offset state.

    Width is determined by:
    - thumbnail format: min(_THUMBNAIL_WIDTH, real_w)
    - other formats:    min(_MAX_WIDTH-or-real_w, _MAX_DIMENSION_CEILING)

    The dimension ceiling is a hard safety cap to stay below Anthropic's
    2000px per-image limit even if a caller passes _MAX_WIDTH > 2000.

    Returns (image_bytes, image_format) where image_format is "jpeg" or "png".
    """
    global _scale_x, _scale_y, _display_w, _display_h, _offset_x, _offset_y

    _offset_x = offset_x
    _offset_y = offset_y

    img = PILImage.open(io.BytesIO(png_bytes))
    real_w, real_h = img.size

    if fmt == "thumbnail":
        target_w = min(_THUMBNAIL_WIDTH, real_w)
    else:
        configured = _MAX_WIDTH if _MAX_WIDTH > 0 else real_w
        target_w = min(configured, _MAX_DIMENSION_CEILING, real_w)

    if target_w >= real_w:
        _scale_x, _scale_y = 1.0, 1.0
        _display_w, _display_h = real_w, real_h
        data, image_format = _encode_image(img, fmt)
        logger.debug(
            "No resize (%dx%d), encoded as %s, %d bytes",
            real_w,
            real_h,
            fmt,
            len(data),
        )
        return data, image_format

    ratio = target_w / real_w
    new_w = target_w
    new_h = int(real_h * ratio)

    _scale_x = real_w / new_w
    _scale_y = real_h / new_h
    _display_w, _display_h = new_w, new_h

    img = img.resize((new_w, new_h), PILImage.LANCZOS)
    data, image_format = _encode_image(img, fmt)
    logger.debug(
        "Resized %dx%d -> %dx%d (scale %.2fx) as %s, %d bytes",
        real_w,
        real_h,
        new_w,
        new_h,
        _scale_x,
        fmt,
        len(data),
    )
    return data, image_format


def _to_real(x: int, y: int) -> tuple[int, int]:
    """Convert display coordinates to absolute screen coordinates."""
    return int(x * _scale_x) + _offset_x, int(y * _scale_y) + _offset_y


@mcp.tool()
@tool(name="screenshot", tier=Tier.TWO, risk=Risk.READ_ONLY)
def screenshot(format: str = "jpeg") -> Image:
    """Capture the full virtual screen (all monitors). Default is JPEG (~5x smaller than PNG).

    Format options:
    - "jpeg" (default): JPEG quality 70. Best balance of size and fidelity for
      UI grounding. ~80 KB for a 1366px-wide desktop.
    - "png": Lossless. ~5x larger payload. Use when you need pixel-perfect
      detail (icons, tiny text, color verification).
    - "thumbnail": ~640px wide JPEG quality 40. Tiny (~15-20 KB). Use for
      sanity-check screenshots in long sessions to keep context light.

    IMPORTANT:
    - The image pixel dimensions ARE the coordinate space for all tools
      (click, screenshot_region, etc.). Use get_screen_size() to know
      the dimensions. If the image is 1366x853, coordinates range from
      (0,0) to (1366,853). The same coordinate space holds across formats.
    - Output width is hard-capped at 1600 px to stay under the Anthropic
      many-image dimension limit (2000 px), so a single screenshot can
      never block a Claude Code session.
    """
    engine = _get_engine()
    state = engine.screenshot()
    fmt = _resolve_format(format)
    data, image_format = _downscale(state.image_bytes, state.offset_x, state.offset_y, fmt=fmt)
    _debug_save(data, "screenshot")
    return Image(data=data, format=image_format)


@mcp.tool()
@tool(name="screenshot_region", tier=Tier.TWO, risk=Risk.READ_ONLY)
def screenshot_region(x: int, y: int, width: int, height: int, format: str = "jpeg") -> Image:
    """Capture a rectangular region of the screen. Coordinates are in display space.

    Format options match screenshot(): "jpeg" (default), "png", "thumbnail".
    A region is small to begin with, so the size win is modest, but JPEG
    stays consistent with screenshot() and avoids a per-image PNG payload.
    """
    engine = _get_engine()
    rx, ry = _to_real(x, y)
    rw, rh = int(width * _scale_x), int(height * _scale_y)
    state = engine.screenshot_region(rx, ry, rw, rh)
    # Don't pass through _downscale -- that would clobber the global scale
    # factors that screenshot() established. Just re-encode the raw bytes.
    fmt = _resolve_format(format)
    img = PILImage.open(io.BytesIO(state.image_bytes))
    data, image_format = _encode_image(img, fmt)
    _debug_save(data, "region")
    return Image(data=data, format=image_format)


@mcp.tool()
@tool(name="click", tier=Tier.TWO, risk=Risk.MEDIUM)
def click(x: int, y: int) -> str:
    """Left-click at screen coordinates (pixels).

    Always take a screenshot() first to confirm the target element's position.
    After clicking, take another screenshot() to verify the click landed
    correctly. Aim for the center of the target element.
    """
    engine = _get_engine()
    engine.click(*_to_real(x, y))
    return f"Clicked at ({x}, {y})"


@mcp.tool()
@tool(name="double_click", tier=Tier.TWO, risk=Risk.MEDIUM)
def double_click(x: int, y: int) -> str:
    """Double-click at screen coordinates."""
    engine = _get_engine()
    engine.double_click(*_to_real(x, y))
    return f"Double-clicked at ({x}, {y})"


@mcp.tool()
@tool(name="right_click", tier=Tier.TWO, risk=Risk.MEDIUM)
def right_click(x: int, y: int) -> str:
    """Right-click at screen coordinates."""
    engine = _get_engine()
    engine.right_click(*_to_real(x, y))
    return f"Right-clicked at ({x}, {y})"


@mcp.tool()
@tool(name="move_mouse", tier=Tier.TWO, risk=Risk.MEDIUM)
def move_mouse(x: int, y: int) -> str:
    """Move the mouse without clicking."""
    engine = _get_engine()
    engine.move_mouse(*_to_real(x, y))
    return f"Mouse moved to ({x}, {y})"


@mcp.tool()
@tool(name="scroll", tier=Tier.TWO, risk=Risk.MEDIUM)
def scroll(x: int, y: int, amount: int) -> str:
    """Scroll at position. Positive = up, negative = down."""
    engine = _get_engine()
    engine.scroll(*_to_real(x, y), amount)
    direction = "up" if amount > 0 else "down"
    return f"Scrolled {direction} {abs(amount)} notches at ({x}, {y})"


@mcp.tool()
@tool(name="drag", tier=Tier.TWO, risk=Risk.MEDIUM)
def drag(start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.5) -> str:
    """Drag from one point to another."""
    engine = _get_engine()
    engine.drag(*_to_real(start_x, start_y), *_to_real(end_x, end_y), duration)
    return f"Dragged from ({start_x}, {start_y}) to ({end_x}, {end_y})"


@mcp.tool()
@tool(name="type_text", tier=Tier.TWO, risk=Risk.MEDIUM)
async def type_text(
    text: str,
    human: bool = False,
    timing_profile: str | None = None,
    wpm: int | None = None,
    iki_cv: float | None = None,
    timeout: int | None = None,
) -> dict[str, object]:
    """Type text into the focused field, optionally with human-paced timing."""
    from computer_use.core.typing import (
        TypingCancelled,
        TypingDeadlineExceeded,
        TypingOptions,
        build_typing_plan,
        require_typing_deadline,
    )

    started = _time_module.monotonic()
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("timeout must be a positive finite number of milliseconds")
    deadline = started + timeout / 1000 if timeout is not None else None
    plan = build_typing_plan(
        text,
        TypingOptions(
            human=human,
            timing_profile=timing_profile,
            wpm=wpm,
            iki_cv=iki_cv,
        ),
    )
    if human and deadline is not None:
        try:
            require_typing_deadline(plan, (deadline - _time_module.monotonic()) * 1000)
        except TypingDeadlineExceeded as error:
            raise ToolError(str(error)) from error
    engine = _get_engine()
    if human:
        cancelled = threading.Event()
        execution = {
            "cancelled": cancelled.is_set,
        }
        if deadline is not None:
            execution["deadline"] = deadline
        try:
            fallback_units = await _run_cooperative_thread(
                functools.partial(
                    engine.type_text,
                    text,
                    plan,
                    **execution,
                ),
                cancelled,
            )
        except (TypingCancelled, TypingDeadlineExceeded) as error:
            raise ToolError(str(error)) from error
    else:
        await anyio.to_thread.run_sync(engine.type_text, text)
    elapsed_ms = round((_time_module.monotonic() - started) * 1000)
    if human:
        result = plan.metadata(elapsed_ms=elapsed_ms)
        result["fallback_units"] = fallback_units
        return result
    return {
        "human": False,
        "units": len(text),
        "fallback_units": 0,
        "elapsed_ms": elapsed_ms,
    }


@mcp.tool()
@tool(name="key_press", tier=Tier.TWO, risk=Risk.MEDIUM)
def key_press(keys: str) -> str:
    """Press a key combo, e.g. "ctrl+c", "alt+tab", "enter"."""
    engine = _get_engine()
    key_list = [k.strip() for k in keys.split("+")]
    engine.key_press(*key_list)
    return f"Pressed: {keys}"


@mcp.tool()
@tool(name="get_screen_size", tier=Tier.TWO, risk=Risk.READ_ONLY)
def get_screen_size() -> str:
    """Returns "WIDTHxHEIGHT" in display pixels (the coordinate space for all tools)."""
    if _display_w > 0 and _display_h > 0:
        return f"{_display_w}x{_display_h}"
    # No screenshot taken yet -- compute what the display size would be.
    # Mirror the logic in _downscale so the answer is consistent with the
    # first screenshot's actual dimensions.
    engine = _get_engine()
    w, h = engine.get_screen_size()
    configured = _MAX_WIDTH if _MAX_WIDTH > 0 else w
    target_w = min(configured, _MAX_DIMENSION_CEILING, w)
    if target_w >= w:
        return f"{w}x{h}"
    ratio = target_w / w
    return f"{target_w}x{int(h * ratio)}"


@mcp.tool()
@tool(name="get_platform", tier=Tier.TWO, risk=Risk.READ_ONLY)
def get_platform() -> str:
    """Returns detected platform: wsl2, linux, windows, or macos."""
    # Platform identity is available before a capture/input backend is. Keeping
    # this probe behind full engine construction made it fail precisely when a
    # caller needed to learn which unsupported or not-yet-configured platform
    # it was running on.
    return detect_platform().value


@mcp.tool()
@tool(name="get_platform_info", tier=Tier.TWO, risk=Risk.READ_ONLY)
def get_platform_info() -> dict:
    """Returns platform details."""
    engine = _get_engine()
    return engine.get_platform_info()


# --- Tier 0 system tools (0.3.0) ---
#
# Each module under `computer_use.tools.system` exposes one dispatch
# function. The wrappers below apply both `@mcp.tool()` (for the wire
# surface) and `@tool(...)` (for the registry), matching the 0.2.0
# pattern used by the pixel tools above.

from computer_use.browser import tool as _browser_impl
from computer_use.tools.system import clipboard as _clipboard_impl
from computer_use.tools.system import data as _data_impl
from computer_use.tools.system import env as _env_impl
from computer_use.tools.system import fs as _fs_impl
from computer_use.tools.system import http as _http_impl
from computer_use.tools.system import shell as _shell_impl
from computer_use.tools.system import tempfile as _tempfile_impl
from computer_use.tools.system import time as _time_impl
from computer_use.tools.ui import tools as _ui_impl


@mcp.tool()
@tool(name="fs", tier=Tier.ZERO, risk=Risk.MEDIUM)
def fs(op: str, path: str, content: str = "", recursive: bool = False):
    """Filesystem ops: read, write, list, stat, delete.

    Sub-ops:
    - read(path) -> str
    - write(path, content) -> {path, written}
    - list(path) -> [str]
    - stat(path) -> {path, size, kind, mtime}
    - delete(path, recursive=False) -> {path, deleted}
    """
    return _fs_impl.fs(op=op, path=path, content=content, recursive=recursive)


@mcp.tool()
@tool(name="shell", tier=Tier.ZERO, risk=Risk.HIGH)
async def shell(
    op: str,
    command=None,
    shell_mode: bool = False,
    timeout: int = 30,
    cwd: str = None,
):
    """Subprocess + PATH lookup.

    Sub-ops:
    - run(command, shell_mode=False, timeout=30, cwd=None)
      -> {returncode, stdout, stderr}
    - which(command) -> path or None

    `command` is an argv list, or a string that is split into argv the way a
    command line is split, so "uname -a" runs. No shell is involved, so nothing
    is expanded. For shell syntax (&&, ||, |, ;, redirection, $(), backticks)
    pass shell_mode=True; a plain run refuses it by name.

    Classified HIGH risk: shell.run can mutate anything on the host.
    """
    return await _shell_impl.shell_async(
        op=op, command=command, shell_mode=shell_mode, timeout=timeout, cwd=cwd
    )


@mcp.tool()
@tool(name="http", tier=Tier.ZERO, risk=Risk.MEDIUM)
def http(
    op: str,
    url: str,
    body: str = None,
    headers: dict = None,
    timeout: int = 30,
):
    """HTTP GET / POST via stdlib urllib.

    Sub-ops:
    - get(url, headers={}, timeout=30) -> {status, headers, body}
    - post(url, body=..., headers={}, timeout=30) -> {status, headers, body}
    """
    return _http_impl.http(op=op, url=url, body=body, headers=headers, timeout=timeout)


@mcp.tool()
@tool(name="env", tier=Tier.ZERO, risk=Risk.LOW)
def env(op: str, name: str, value: str = None):
    """Process-scoped environment variables.

    Sub-ops:
    - get(name) -> str or None
    - set(name, value) -> {name, value}  (does NOT persist)
    """
    return _env_impl.env(op=op, name=name, value=value)


@mcp.tool()
@tool(name="time", tier=Tier.ZERO, risk=Risk.READ_ONLY)
def time(op: str, seconds: float = 0, tz: str = None):
    """Clock + sleep.

    Sub-ops:
    - now(tz=None) -> ISO-8601 string (default UTC)
    - sleep(seconds) -> {slept: float}  (capped at 60s)
    """
    return _time_impl.time(op=op, seconds=seconds, tz=tz)


@mcp.tool()
@tool(name="tempfile", tier=Tier.ZERO, risk=Risk.LOW)
def tempfile(op: str = "temp_path", prefix: str = "vcu-", suffix: str = ""):
    """Allocate a unique temporary-file path (the file is NOT created).

    Sub-ops:
    - temp_path(prefix='vcu-', suffix='') -> absolute path str
    """
    return _tempfile_impl.tempfile(op=op, prefix=prefix, suffix=suffix)


@mcp.tool()
@tool(name="data", tier=Tier.ZERO, risk=Risk.READ_ONLY)
def data(op: str, source: str = None, value=None):
    """JSON / CSV / YAML parse + serialize.

    Sub-ops:
    - parse_json(source) / serialize_json(value)
    - parse_csv(source)  / serialize_csv(value)
    - parse_yaml(source) / serialize_yaml(value)  (requires PyYAML)
    """
    return _data_impl.data(op=op, source=source, value=value)


@mcp.tool()
@tool(name="clipboard", tier=Tier.ZERO, risk=Risk.LOW)
def clipboard(op: str, text: str = None):
    """Read / write the system clipboard.

    Sub-ops:
    - copy(text) -> {backend, bytes}
    - paste() -> str

    Backend chain: clip.exe (Windows/WSL2), pbcopy (macOS), wl-copy
    (Wayland), xclip (X11). Raises RuntimeError if none are on PATH.
    """
    return _clipboard_impl.clipboard(op=op, text=text)


# --- Tier 1: browser (MV3 extension + native messaging) ---
#
# One op-routed `browser` tool plus the separate HIGH-risk `browser_eval`.
# Both are thin clients over a BrowserBridge; they are always registered (the
# *action* fails with a guided error when no browser is connected, never the
# registration).


@mcp.tool()
@tool(name="browser", tier=Tier.ONE, risk=Risk.MEDIUM)
async def browser(
    op: str,
    url: str = None,
    selector: str = None,
    name: str = None,
    text: str = None,
    value: str = None,
    by: str = "css",
    state: str = "visible",
    wait: str = "load",
    action: str = "get",
    all: bool = False,
    clear: bool = True,
    submit: bool = False,
    timeout: int | None = None,
    scroll_by: dict = None,
    key: str = None,
    force: bool = False,
    trusted: bool = False,
    mode: str = None,
    window_id: int = None,
    tab_id: int = None,
    reveals: str = None,
    files: list = None,
    roles: list = None,
    cursor: int = None,
    limit: int = None,
    arm: bool = True,
    profile_id: str = None,
    human: bool = False,
    timing_profile: str = None,
    wpm: int = None,
    iki_cv: float = None,
):
    """Drive the browser by selector, through the MV3 extension (Tier 1).

    Sub-ops:
    - navigate(url, wait="load") / back / forward / reload -> {url, title}
    - wait_for(selector, state="visible"|"hidden"|"attached", timeout) -> {matched}
    - query(selector, by="css"|"xpath", all=False, limit=50, cursor=None)
      -> {nodes:[{tag, text, attrs}], next_cursor?}  (capped + paginated)
    - read_text(selector=None) -> str
    - get_attribute(selector, name) -> live value/checked/selected/disabled, else attr
    - get_value(selector) -> {value}  (input/textarea/select AND custom widgets)
    - element_state(selector) -> {visible, receives_events, enabled, focused,
      editable, checked?, value?, bbox}  (the explicit actionability read)
    - click(selector, by="css", trusted=False) -> {clicked, checked?, ok?, via?}
      Auto-escalates to a trusted CDP click when the DOM click left a
      state-bearing widget (data-state / aria-expanded / aria-checked /
      aria-pressed / aria-selected) unchanged. Pass trusted=True for a
      pointer-driven control that exposes no such state. VERIFY: ok, or a
      read-back op.
    - type / fill(selector, text, clear=True, submit=False) -> {typed, value, ok};
      human type has no implicit total deadline. An optional positive timeout
      is one explicit caller deadline
    - clear(selector) -> {value:"", ok}
    - select(selector, value) -> {selected, value, ok}
    - focus(selector) / blur(selector) -> {focused}
    - hover(selector, reveals=None) -> {hovered, revealed?}
    - scroll(selector=None | scroll_by={x,y}) -> {ok}
    - press(key, selector=None) -> {pressed}  (trusted key via chrome.debugger;
      returns a named limitation instead of activating an inactive target)
    - upload(selector, files=[path,...]) -> {uploaded, files, ok}  (paths are on
      cua's OS; cua rewrites them to the browser's OS automatically)
    - dialog(action="accept"|"dismiss", text=None, arm=True) -> {armed}  (ARM
      BEFORE the op that pops the JS dialog - it pauses the renderer synchronously)
    - snapshot(selector=None, roles=None, cursor=None, limit=50)
      -> {nodes:[{role, name, state, value, ref}], next_cursor?}  (paginated AX,
      pierces shadow DOM + frames; supersedes accessibility_tree)
    - use_target(mode="owned"|"attach", window_id=None, tab_id=None,
      profile_id=None) -> {browser, window_id, tab_id, created}  (pin the session
      target; profile_id also selects which connected browser profile to act in)
    - cookies(action="get"|"set"|"clear", url, name, value)
    - status() -> {connected, browsers, setup, reason, profiles}  (pre-flight; no
      page; `profiles` lists every connected browser profile)

    `screenshot` is NOT a browser op - it is a separate pixel tool. Call the
    top-level `screenshot` tool to see the page.

    TARGETING: by default the agent acts on its own dedicated window (opened in
    your real profile), pinned by id, so focus changes never move the target. Use
    `use_target(mode="attach")` to act on the tab you are currently looking at.
    Every result carries `target: {window_id, tab_id, url}` so you always see
    which tab you acted on. Use the `tabs` / `windows` tools to enumerate what is
    open (`tabs(op="list")`) and to open / switch / close windows and tabs. If the
    pinned tab/window is closed mid-task the op fails with `target_lost`
    (terminal) - it never silently grabs your active tab; run `tabs(op="list")`
    then `use_target` to recover.

    VERIFY EVERY MUTATING OP - the DOM is the ground truth; never assume an
    action worked (this is the web equivalent of screenshot-before/after):
    - type/fill/clear -> check the returned `ok` (or get_value/get_attribute).
    - checkbox/radio click -> the returned `checked` flipped as intended.
    - select -> the returned `ok` (or get_value is the chosen option).
    - upload -> the returned `ok` (input's files.length matched).
    - a click that should change the page -> wait_for the expected element, then
      read_text/query to confirm the new state; a click that navigates returns
      {navigated, url} - confirm the destination is right.
    If the read-back does NOT match, the action did NOT take effect - retry or
    stop; do not continue on an unverified action.

    ACTIONABILITY: a mutating op refuses a non-actionable target (hidden / covered
    / disabled) with op_failed - act on the VISIBLE element, not a hidden mirror
    (e.g. some pages have a hidden form-field twin of the real editor). Use
    element_state(selector) to check first. Pass force=True only to bypass this
    for a deliberately-hidden real control.

    On a terminal browser error (not set up / not connected / op unsupported /
    target lost) the tool raises with a guided pixel fallback - prefer this tool;
    degrade to the pixel tools only when it says so.
    """
    params = {
        "url": url,
        "selector": selector,
        "name": name,
        "text": text,
        "value": value,
        "state": state,
        "wait": wait,
        "action": action,
        "all": all,
        "clear": clear,
        "submit": submit,
        "timeout": timeout,
        "key": key,
        "force": force,
        "trusted": trusted,
        "mode": mode,
        "window_id": window_id,
        "tab_id": tab_id,
        "reveals": reveals,
        "files": files,
        "roles": roles,
        "cursor": cursor,
        "limit": limit,
        "profile_id": profile_id,
        "human": human,
        "timing_profile": timing_profile,
        "wpm": wpm,
        "iki_cv": iki_cv,
    }
    # `arm` defaults True and is only meaningful for `dialog`; pass it there.
    if op == "dialog":
        params["arm"] = arm
    # `by` is the css/xpath selector mode for most ops, but the {x,y} offset for
    # `scroll`. The MCP surface keeps them distinct (`by` vs `scroll_by`); the
    # op handler reads `by` either way.
    if op == "scroll":
        if scroll_by is not None:
            params["by"] = scroll_by
    else:
        params["by"] = by
    params = {k: v for k, v in params.items() if v is not None}
    if op == "type" and human:
        cancelled = threading.Event()
        params["_cancelled"] = cancelled.is_set
        return await _run_cooperative_thread(
            functools.partial(_browser_impl.browser, op=op, **params), cancelled
        )
    return await anyio.to_thread.run_sync(
        functools.partial(_browser_impl.browser, op=op, **params),
        abandon_on_cancel=True,
    )


@mcp.tool()
@tool(name="browser_eval", tier=Tier.ONE, risk=Risk.HIGH)
def browser_eval(expression: str):
    """Run arbitrary JavaScript in the active page (HIGH risk).

    Returns the evaluated value. Separate from `browser` so the common ops keep
    a lower risk ceiling; this is the escape hatch for anything not yet a
    first-class op.
    """
    return _browser_impl.browser_eval(expression=expression)


@mcp.tool()
@tool(name="tabs", tier=Tier.ONE, risk=Risk.MEDIUM)
def tabs(
    op: str,
    url: str = None,
    window_id: int = None,
    tab_id: int = None,
    background: bool = True,
    force: bool = False,
):
    """Enumerate and manage browser tabs, through the MV3 extension (Tier 1).

    Listings show every window/tab and its ownership. Target-bound operations
    require this client's lease; listing alone never claims a target.

    Sub-ops:
    - list -> {windows:[{window_id, focused, owned, tabs:[{tab_id, url, title,
      active, owned, is_current}]}]}  (READ_ONLY; the awareness + recovery map:
      after any drift, list -> use_target the real tab -> resume)
    - open(url=None, window_id=None, background=True)
      -> {window_id, tab_id, url, created}  (a new OWNED tab; sets the target)
    - claim(tab_id, window_id=None) -> {window_id, tab_id, ownership}
      (claims an unowned or orphaned tab and sets the logical target without
      activating it; a foreign window lease or tab owner refuses the claim)
    - release(tab_id) -> {released, tab_id, revision}
      (releases this client's tab lease without closing the tab; inherited
      window ownership must be released through windows.release instead)
    - switch(tab_id, window_id=None) -> {window_id, tab_id, url, is_current}
      (requires the window lease; moves the target and activates the tab without
      raising the window over your foreground)
    - close(tab_id, force=False) -> {closed, tab_id}  (requires ownership;
      closing the current tab makes the next op raise
      target_lost - run list then use_target to recover)

    Another client's target returns target_owned_by_another_client; force
    never bypasses ownership. Results may carry this client's
    `target: {window_id, tab_id, url}`; release and closed-target results need
    not carry a target.
    """
    return _browser_impl.tabs(
        op=op,
        url=url,
        window_id=window_id,
        tab_id=tab_id,
        background=background,
        force=force,
    )


@mcp.tool()
@tool(name="windows", tier=Tier.ONE, risk=Risk.MEDIUM)
def windows(
    op: str,
    url: str = None,
    window_id: int = None,
    focused: bool = False,
    force: bool = False,
):
    """Enumerate and manage browser windows, through the MV3 extension (Tier 1).

    Sub-ops:
    - list -> {windows:[{window_id, focused, owned, tab_count, active_tab_id}]}
      (READ_ONLY; the thin variant of tabs.list)
    - open(url=None, focused=False) -> {window_id, tab_id, created}  (a new OWNED
      window, unfocused by default; sets the target)
    - claim(window_id) -> {window_id, tab_id, ownership}
      (claims an unowned or orphaned window and its tabs; sets the logical
      target; refuses a foreign window owner or foreign child-tab owner)
    - release(window_id) -> {released, window_id, revision}
      (releases this client's window and inherited tab leases without closing
      the window or its tabs)
    - focus(window_id) -> {focused, window_id}  (the EXPLICIT raise - the agent
      requires the window lease and never raises a window automatically)
    - close(window_id, force=False) -> {closed, window_id}
      (requires the window lease)

    Listing never claims. Another client's window or child-tab lease returns
    target_owned_by_another_client; force never bypasses ownership.
    """
    return _browser_impl.windows(
        op=op,
        url=url,
        window_id=window_id,
        focused=focused,
        force=force,
    )


@mcp.tool()
@tool(name="profiles", tier=Tier.ONE, risk=Risk.MEDIUM)
def profiles(op: str, profile_id: str = None):
    """Enumerate and select the connected browser profile (Tier 1).

    When the extension is installed in more than one Chrome profile (personal,
    work, several Google accounts), each connects separately; this tool tells
    them apart and picks which one to drive.

    Sub-ops:
    - list -> {profiles:[{profile_id, browser, is_current, window_count,
      tab_count, sample_tab_titles}]}  (READ_ONLY; recognize a profile by what is
      open in it, e.g. "the one with work Gmail and Figma")
    - use(profile_id) -> {profile_id, browser, is_current:true}  (pin which
      profile the browser / tabs / windows ops act within)

    With more than one profile connected and none selected, the next page op
    raises a terminal `profile_ambiguous` error listing the choices (never a
    silent guess). A single connected profile is used automatically, so
    single-profile setups need no extra step. You can also pin a default with the
    CUA_BROWSER_PROFILE env var (a profile_id prefix or a tab-title substring).
    """
    return _browser_impl.profiles(op=op, profile_id=profile_id)


# --- Tier 1: structured (AT-SPI on Linux; UIA and AX to follow) ---
#
# Read the accessibility tree and act on an element by reference: small text
# instead of an image, tens of milliseconds instead of seconds, and correct under
# a window that moved. The pixel tier stays exactly as it is - this is added
# beside it, never a replacement, and the loop still chooses the rung.


@mcp.tool()
@tool(name="ui_tree", tier=Tier.ONE, risk=Risk.READ_ONLY)
def ui_tree(depth: int = 6, app: str = "") -> dict:
    """Read an accessible tree (Tier 1), filtered and depth-capped.

    Returns small structured text, not an image. Prefer this over a screenshot to
    understand a native (non-web) window. ``app=""`` reads the focused window;
    ``app="Name"`` reads that application's window even if it is not focused;
    ``app="*"`` reads every open window. On a box with no accessibility bus it
    returns ``error: at_spi_unavailable`` with a one-line remedy.
    """
    return _ui_impl.ui_tree(depth=depth, app=app)


@mcp.tool()
@tool(name="ui_find", tier=Tier.ONE, risk=Risk.READ_ONLY)
def ui_find(role: str = "", name: str = "", app: str = "") -> dict:
    """Find native UI elements (Tier 1) by role, name, or both.

    Each result carries an opaque ``ref`` for ui_act, plus ``bounds`` and
    ``states``. An empty match is a successful read (``ok`` with an empty list),
    not an error. Match by role (e.g. "button"), by name, or both. ``app=""``
    searches the focused window; ``app="Name"`` searches that application's window
    even when unfocused; ``app="*"`` searches every open window.
    """
    return _ui_impl.ui_find(role=role, name=name, app=app)


@mcp.tool()
@tool(name="ui_windows", tier=Tier.ONE, risk=Risk.READ_ONLY)
def ui_windows() -> dict:
    """List open top-level windows across all apps (Tier 1).

    Each entry has the app name, the window title, whether it is active, and an
    opaque ``ref``. This is how to discover what is open before targeting a
    window with ``ui_find(app=...)`` or ``ui_tree(app=...)``.
    """
    return _ui_impl.ui_windows()


@mcp.tool()
@tool(name="ui_act", tier=Tier.ONE, risk=Risk.MEDIUM)
def ui_act(
    action: str,
    ref: str = "",
    text: str = "",
    name: str = "",
    role: str = "",
    app: str = "",
) -> dict:
    """Act on one native UI element (Tier 1): click, focus, set_text, toggle, expand.

    Target it directly by ``name`` and/or ``role`` (e.g. "button"), with ``app``
    scoping the search as in ui_find, or by a ``ref`` from ui_find; a ``ref``
    wins when both are given. By name it acts only when exactly one element
    matches, and an element whose whole name equals ``name`` wins over partial
    matches. No match returns ``not_found`` with the nearest ``candidates``;
    several return ``ambiguous`` with up to five candidate refs and the
    ``count``. A by-name act also returns the ``ref`` it used.

    Re-reads the element after acting and returns its new state, so a toggle that
    did not toggle is visible in the response. A ref that no longer resolves fails
    ``element_gone`` and never falls back to clicking its old coordinates. Pass
    ``text`` for ``set_text``.
    """
    return _ui_impl.ui_act(
        ref=ref, action=action, text=text, name=name, role=role, app=app
    )


@mcp.tool()
@tool(name="ui_wait", tier=Tier.ONE, risk=Risk.READ_ONLY)
def ui_wait(role: str = "", name: str = "", timeout_ms: int = 5000, app: str = "") -> dict:
    """Block until a matching native element appears or the timeout elapses (Tier 1).

    Bounded by ``timeout_ms``. Returns the element when it appears, or
    ``error: timeout`` with the elapsed milliseconds. ``app`` scopes the wait the
    same way ``ui_find`` does.
    """
    return _ui_impl.ui_wait(role=role, name=name, timeout_ms=timeout_ms, app=app)


@mcp.tool()
@tool(name="apps", tier=Tier.ZERO, risk=Risk.READ_ONLY)
def apps() -> dict:
    """List installed launchable apps (Tier 0): id, name, and icon where present.

    Read from the XDG desktop entries. Use an entry's ``id`` (or its ``name``)
    with ``app_open`` to launch it.
    """
    from computer_use.tools import apps as _apps_impl

    return _apps_impl.list_apps()


@mcp.tool()
@tool(name="app_open", tier=Tier.ZERO, risk=Risk.MEDIUM)
def app_open(target: str, timeout_ms: int = 5000) -> dict:
    """Launch an installed app by id or name and confirm it opened (Tier 0).

    Resolves the target against the XDG desktop entries, launches with the
    desktop's own launcher, then watches the a11y bus for the app's window.
    ``ok`` means a matching window is open and the result carries it (with
    ``already_open`` when it predates the launch); a launch that dispatched but
    mapped no window within ``timeout_ms`` is the named error ``no_window``.
    Where the a11y bus cannot answer, the result says ``confirmed: false``.
    """
    from computer_use.tools import apps as _apps_impl

    return _apps_impl.open_app(target, timeout_ms=timeout_ms)


# --- CLI: management subcommands ---
#
# The package ships a single entry point (`vadgr-cua`). With no arguments
# it starts the MCP stdio server (the hot path). With a subcommand it
# exposes daemon lifecycle controls that used to live in external tooling.
# Keeping every subcommand as a small function makes them unit-testable
# without spinning up argparse or touching sys.argv.


def _get_supervisor():
    """Build a DaemonSupervisor on demand.

    Importing the supervisor pulls in `fcntl`, which doesn't exist on native
    Windows. The stdio MCP server (the hot path) doesn't need it; only the
    daemon subcommands do, and those run on WSL2/Linux.
    """
    from computer_use.bridge.supervisor import DaemonSupervisor

    return DaemonSupervisor()


def _registry_status() -> dict:
    """Snapshot of the ToolRegistry for `doctor` output.

    Returns the registry contract's three fields:
    - `registry_loaded`: True once the registry import succeeded.
    - `tool_count`: total number of registered tools.
    - `tier_breakdown`: map of tier-string -> count (e.g. {"2": 13}).
    """
    return {
        "registry_loaded": True,
        "tool_count": REGISTRY.count(),
        "tier_breakdown": {str(tier): count for tier, count in REGISTRY.tier_breakdown().items()},
    }


def _cmd_doctor(args) -> int:
    """Print structured status of the bridge daemon and the tool registry.

    Exits 0 regardless of daemon state -- callers parse the JSON.
    """
    import json as _json

    status = _get_supervisor().status()
    if sys.platform == "darwin":
        from computer_use.platform.macos import macos_permission_status

        status.update(macos_permission_status())
    status.update(_registry_status())
    if sys.platform == "linux":
        status["platform_backends"] = _platform_backends_status()
    print(_json.dumps(status, indent=2))
    return 0


def _platform_backends_status() -> dict:
    """Resolved capture/input backends for the live Linux session (for doctor).

    Reports which backend the resolver selects and which candidates applied, so a
    user (or agent) can see why a tier is or isn't available. Best-effort: any
    failure degrades to an error string rather than breaking doctor.
    """
    try:
        from computer_use.platform.backends.linux_providers import describe_backends

        return describe_backends()
    except Exception as exc:  # pragma: no cover - defensive
        return {"error": str(exc)}


def _cmd_install_daemon(args) -> int:
    """Eager deploy + launch. Returns non-zero if the daemon can't come up."""
    client = _get_supervisor().ensure_running()
    if client is None:
        print(
            "Daemon install failed. Run `vadgr-cua doctor` for diagnostics.",
            file=sys.stderr,
        )
        return 1
    print("Daemon installed and running.")
    return 0


def _cmd_setup(args) -> int:
    """One-time post-install permission prompt (macOS).

    Fires the Accessibility and Screen Recording prompts so the user can
    grant them before wiring up an agent. Prints permission state as
    JSON. No-op on non-macOS platforms.
    """
    import json as _json

    if sys.platform != "darwin":
        print(_json.dumps({"platform": sys.platform, "applicable": False}))
        return 0

    from computer_use.platform.macos import (
        macos_permission_status,
        request_permissions,
    )

    request_permissions()
    print(_json.dumps(macos_permission_status(), indent=2))
    return 0


def _cmd_browser_setup(args) -> int:
    """Self-register the browser-tier native host and print the load steps.

    Writes the native-host manifest (+ registry on Windows/WSL) so Chrome can
    spawn the host shim, then prints how to sideload the extension. Best-effort
    on the registration, explicit on the instructions.
    """
    from computer_use.setup.extension_setup import ensure_registered, load_steps

    result = ensure_registered()
    print(f"native host registered for: {', '.join(result['browsers'])}")
    print(f"host: {result['host_path']}\n")
    print(load_steps())
    return 0


def _cmd_install_deps(args) -> int:
    """Provision what pip can't: system packages, the uinput udev rule, and on
    GNOME the shell extension (indicator, Esc to stop, no screenshot flash).

    Prints a distro-aware plan by default; executes it with --yes.
    """
    from computer_use.setup.deps import install_deps

    yes = getattr(args, "yes", False)
    return install_deps(assume_yes=yes, dry_run=not yes)


def _cmd_stop_daemon(args) -> int:
    """Best-effort stop. Always returns 0 -- stop is idempotent."""
    _get_supervisor().stop()
    print("Daemon stopped.")
    return 0


def _cmd_restart_daemon(args) -> int:
    """Stop then start. Returns non-zero if the restart didn't come up."""
    client = _get_supervisor().restart()
    if client is None:
        print("Daemon restart failed.", file=sys.stderr)
        return 1
    print("Daemon restarted.")
    return 0


# Command -> module-level function name. Resolved via globals() at
# dispatch time so tests that patch the module attribute see the fake.
_SUBCOMMAND_NAMES: dict = {
    "doctor": "_cmd_doctor",
    "setup": "_cmd_setup",
    "browser-setup": "_cmd_browser_setup",
    "install-daemon": "_cmd_install_daemon",
    "install-deps": "_cmd_install_deps",
    "stop-daemon": "_cmd_stop_daemon",
    "restart-daemon": "_cmd_restart_daemon",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vadgr-cua", description="Computer Use MCP Server")
    try:
        package_version = version("vadgr-computer-use")
    except PackageNotFoundError:
        package_version = "unknown"
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version}")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for SSE transport (default: 8000)",
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=_MAX_WIDTH,
        help="Max screenshot width in pixels (0=auto, env: CU_MAX_WIDTH)",
    )

    sub = parser.add_subparsers(dest="command", required=False)
    sub.add_parser("doctor", help="Print daemon status as JSON")
    sub.add_parser(
        "setup",
        help="Fire macOS permission prompts and print state (no-op elsewhere)",
    )
    sub.add_parser(
        "browser-setup",
        help="Register the browser-tier native host and print the load steps",
    )
    sub.add_parser(
        "install-daemon",
        help="Deploy and launch the Windows bridge daemon (WSL2 only)",
    )
    deps_parser = sub.add_parser(
        "install-deps",
        help=(
            "Provision what pip can't (wl-clipboard, /dev/uinput udev rule, "
            "the GNOME Shell extension)"
        ),
    )
    deps_parser.add_argument(
        "--yes",
        action="store_true",
        help="Execute the plan instead of printing it",
    )
    sub.add_parser("stop-daemon", help="Stop the Windows bridge daemon")
    sub.add_parser("restart-daemon", help="Stop then start the daemon")

    return parser


def _start_browser_tier() -> None:
    """Best-effort: self-register the native host and connect to the broker.

    Registering at startup (not only lazily on first browser use) means the
    extension's load order never matters - Chrome can spawn the host shim and
    reach a live listener whenever the user enables the extension. Wrapped so a
    transport/registration failure never blocks the MCP server from coming up.
    """
    try:
        from computer_use.setup.extension_setup import ensure_registered

        ensure_registered()
    except Exception as e:  # best-effort - never break startup
        logger.debug("browser-tier self-registration skipped: %s", e)
    try:
        from computer_use.browser import tool as browser_tool

        browser_tool._default_bridge().status()
    except Exception as e:
        logger.debug("browser-tier listener not started: %s", e)


def _run_mcp_server(args) -> int:
    """Run the MCPServer with the parsed CLI args."""
    global _MAX_WIDTH
    _MAX_WIDTH = args.max_width

    logger.info(
        "Starting Computer Use MCP server (transport=%s, max_width=%s)",
        args.transport,
        _MAX_WIDTH or "auto",
    )

    _start_browser_tier()

    # mcp 2.x takes the transport's options on run() instead of on a settings
    # object, so the port travels with the call that uses it. run() is
    # overloaded per transport and the stdio arm ignores any keyword it is
    # given, so each branch passes only what its own overload documents.
    if args.transport == "sse":
        logger.info("SSE server on port %d", args.port)
        mcp.run(transport="sse", port=args.port)
    else:
        mcp.run(transport="stdio")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handler_name = _SUBCOMMAND_NAMES.get(args.command)
    if handler_name is not None:
        return globals()[handler_name](args)
    return _run_mcp_server(args)


if __name__ == "__main__":
    sys.exit(main())
