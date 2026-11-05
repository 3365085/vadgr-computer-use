"""Live helpers for the R04 to R09 cells: GNOME indicator, native acts, Flutter.

Each subcommand calls the installed server's MCP tool layer in process, or the
vadgr GNOME Shell extension over the session bus, on a real GNOME Wayland
desktop, and prints what it observed as JSON lines. None assigns a verdict.

Run it with the interpreter of the installed entry point, so the code under test
is the installed build, for example:

    ~/.local/share/uv/tools/vadgr-computer-use/bin/python -I gnome_cells.py edges

`box`, `fallback` and `flash --portal-control` each take one screenshot through
the xdg portal, which flashes the screen. Tell the person at the desk first.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import subprocess
import time

from PIL import Image

from computer_use import mcp_server
from computer_use.platform import linux
from computer_use.platform.backends.gnome_shell import EXTENSION_UUID, ShellClient
from computer_use.platform.backends.portal import PortalScreenshotCapture


def emit(**fields) -> None:
    print(json.dumps({"t": round(time.time(), 3), **fields}, ensure_ascii=False), flush=True)


def call(name: str, args: dict):
    """One tool call through the server's MCP layer: (is_error, payload, seconds)."""
    start = time.monotonic()
    result = asyncio.run(mcp_server.mcp.call_tool(name, args))
    seconds = round(time.monotonic() - start, 3)
    if result.structured_content is not None:
        payload = result.structured_content
    else:
        first = result.content[0]
        payload = first if first.type == "image" else _json_or_text(first.text)
    return bool(result.is_error), payload, seconds


def _json_or_text(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return text


def _orange(rgb) -> bool:
    r, g, b = rgb
    return r > 200 and 80 < g < 175 and b < 90


def edge_orange(image: Image.Image, band: int) -> dict:
    """Fraction of glow-coloured pixels in each edge band of a capture."""
    img = image.convert("RGB")
    w, h = img.size
    px = img.load()
    boxes = {
        "top": (0, 0, w, band), "bottom": (0, h - band, w, h),
        "left": (0, 0, band, h), "right": (w - band, 0, w, h),
    }
    out = {}
    for side, (x0, y0, x1, y1) in boxes.items():
        pts = [(x, y) for y in range(y0, y1) for x in range(x0, x1)]
        out[side] = round(sum(_orange(px[p]) for p in pts) / len(pts), 4)
    return out


def cmd_edges(_args) -> None:
    """R04: the capture path, and no glow inside a capture taken while it is lit."""
    _, info, _ = call("get_platform_info", {})
    emit(cell="R04", capture=info.get("capture"))
    err, image, seconds = call("screenshot", {"format": "png"})
    shot = Image.open(io.BytesIO(base64.b64decode(image.data)))
    emit(cell="R04", tool="screenshot", error=err, seconds=seconds,
         size=shot.size, edge_orange=edge_orange(shot, band=8))


def cmd_regions(_args) -> None:
    """R09: a region asked right after a thumbnail stays in the full coordinate space."""
    _, size, _ = call("get_screen_size", {})
    call("screenshot", {"format": "jpeg"})
    call("screenshot", {"format": "thumbnail"})
    err, image, seconds = call(
        "screenshot_region", {"x": 600, "y": 570, "width": 640, "height": 60}
    )
    region = None if err else Image.open(io.BytesIO(base64.b64decode(image.data))).size
    emit(cell="R09", screen=size, region_error=err, region_size=region, seconds=seconds)


def cmd_acts(args) -> None:
    """R07: ui_act by name on a live app: one match, none, and many."""
    _, opened, _ = call("app_open", {"target": args.app})
    emit(cell="R07", app_open=opened)
    time.sleep(1.0)  # a freshly mapped window can drop its first activation
    scope = args.app
    for label, act in (
        ("one", {"action": "click", "name": "7", "role": "button", "app": scope}),
        ("near_miss", {"action": "click", "name": "Backspase", "app": scope}),
        ("none", {"action": "click", "name": "no-such-control-zz", "app": scope}),
        ("many", {"action": "click", "role": "button", "app": scope}),
    ):
        err, payload, seconds = call("ui_act", act)
        emit(cell="R07", case=label, error=err, seconds=seconds, result=payload)
    time.sleep(0.5)  # the toolkit updates the display asynchronously
    _, found, _ = call("ui_find", {"role": "text box", "name": "", "app": scope})
    emit(cell="R07", read_back=[e.get("text") for e in found.get("elements", [])])


def cmd_flutter(args) -> None:
    """R08: a Flutter app is read in time, by its window title."""
    for name, tool_args in (
        ("ui_tree", {"depth": 6, "app": args.title}),
        ("ui_find", {"role": "button", "name": "", "app": args.title}),
    ):
        err, payload, seconds = call(name, tool_args)
        text = json.dumps(payload, ensure_ascii=False)
        emit(cell="R08", tool=name, error=err, ok=payload.get("ok"), seconds=seconds,
             unknown_bounds=text.count('"bounds"]'), unknown_states=text.count('"states"]'),
             chars=len(text))


def cmd_box(args) -> None:
    """R05: from an idle overlay, a target box appears at once where it was asked.

    The extension keeps its overlay out of its own captures, so the one
    observation that can see it is a portal capture, which flashes.
    """
    x, y, w, h = args.x, args.y, args.w, args.h
    shell = ShellClient()
    shell.begin("ui_act", "R05 target box", x, y, w, h)
    shot = Image.open(io.BytesIO(PortalScreenshotCapture().capture_full().image_bytes))
    img = shot.convert("RGB")
    px = img.load()
    margin = 150
    pts = [
        (px_x, px_y)
        for px_y in range(max(0, y - margin), min(img.height, y + h + margin))
        for px_x in range(max(0, x - margin), min(img.width, x + w + margin))
        if _orange(px[px_x, px_y])
    ]
    edges = {
        "left": sum(1 for p in pts if abs(p[0] - x) <= 3),
        "right": sum(1 for p in pts if abs(p[0] - (x + w)) <= 3),
        "top": sum(1 for p in pts if abs(p[1] - y) <= 3),
        "bottom": sum(1 for p in pts if abs(p[1] - (y + h)) <= 3),
    }
    emit(cell="R05", asked=[x, y, x + w, y + h], capture_size=img.size, edge_pixels=edges)
    if args.save:
        img.crop((x - 60, y - 60, x + w + 60, y + h + 60)).save(args.save)


def cmd_esc(_args) -> None:
    """R06: after the person presses Esc, desktop tools refuse for 30 seconds."""
    shell = ShellClient()
    width, height = shell.capture_size()
    emit(cell="R06", waiting_for="Esc pressed in the focused fixture window")
    start = time.monotonic()
    while not shell.begin("click", 'R06: press Esc now', width // 2, height // 2):
        if time.monotonic() - start > 300:
            emit(cell="R06", observed="no Esc within 300 s")
            return
        time.sleep(1.0)
    stopped = time.monotonic()
    emit(cell="R06", observed="stopped")
    for name, tool_args in (
        ("screenshot", {"format": "thumbnail"}),
        ("click", {"x": 5, "y": 5}),
        ("key_press", {"keys": "enter"}),
        ("type_text", {"text": "x"}),
    ):
        err, payload, _ = call(name, tool_args)
        emit(cell="R06", tool=name, error=err, result=payload)
    while shell.begin("screenshot", "R06 waiting"):
        time.sleep(0.5)
    emit(cell="R06", resumed_after_s=round(time.monotonic() - stopped, 1))


def cmd_own_esc(_args) -> None:
    """R06: vadgr's own Esc reaches the focused application while the overlay is lit."""
    for keys in ("a", "escape"):
        err, payload, _ = call("key_press", {"keys": keys})
        emit(cell="R06", tool="key_press", keys=keys, error=err, result=payload)


def cmd_fallback(_args) -> None:
    """R04: an installed extension that is away is still chosen, and capture falls
    back to the portal per call, then returns to the extension without a restart."""
    subprocess.run(["gnome-extensions", "disable", EXTENSION_UUID], check=True)
    try:
        time.sleep(2)
        capture = linux._create_screen_capture()
        emit(cell="R04", chosen_while_away=type(capture).__name__)
        capture.capture_full()
        emit(cell="R04", captured_while_away_via=capture.backend_name)
    finally:
        subprocess.run(["gnome-extensions", "enable", EXTENSION_UUID], check=True)
    time.sleep(3)
    capture.capture_full()
    emit(cell="R04", captured_after_return_via=capture.backend_name)


def cmd_flash(args) -> None:
    """R04: labelled extension captures, for the person at the desk and a bus monitor."""
    shell = ShellClient()

    def say(text):
        shell.begin("test", text)
        emit(cell="R04", step=text)

    time.sleep(args.lead)
    say("R04 1/3: overlay only, no capture")
    time.sleep(5)
    for fmt in ("jpeg", "thumbnail"):
        say(f"R04 2/3: {fmt} capture in 2 s")
        time.sleep(2)
        err, _, seconds = call("screenshot", {"format": fmt})
        emit(cell="R04", tool="screenshot", format=fmt, error=err, seconds=seconds)
        time.sleep(3)
    say("R04 3/3: five captures in a row")
    time.sleep(2)
    for _ in range(5):
        call("screenshot", {})
        time.sleep(0.6)
    if args.portal_control:
        say("R04 control: portal capture in 3 s, this one flashes")
        time.sleep(3)
        PortalScreenshotCapture().capture_full()
        emit(cell="R04", control="portal capture")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("edges").set_defaults(fn=cmd_edges)
    sub.add_parser("regions").set_defaults(fn=cmd_regions)
    acts = sub.add_parser("acts")
    acts.add_argument("--app", default="Calculator")
    acts.set_defaults(fn=cmd_acts)
    flutter = sub.add_parser("flutter")
    flutter.add_argument("--title", default="FlClash")
    flutter.set_defaults(fn=cmd_flutter)
    box = sub.add_parser("box")
    for name, default in (("x", 1000), ("y", 600), ("w", 400), ("h", 200)):
        box.add_argument(f"--{name}", type=int, default=default)
    box.add_argument("--save", default="")
    box.set_defaults(fn=cmd_box)
    sub.add_parser("esc").set_defaults(fn=cmd_esc)
    sub.add_parser("own-esc").set_defaults(fn=cmd_own_esc)
    sub.add_parser("fallback").set_defaults(fn=cmd_fallback)
    flash = sub.add_parser("flash")
    flash.add_argument("--lead", type=float, default=8.0)
    flash.add_argument("--portal-control", action="store_true")
    flash.set_defaults(fn=cmd_flash)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
