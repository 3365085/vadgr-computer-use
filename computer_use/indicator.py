# Copyright 2026 Victor Santiago Montaño Diaz
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""What a desktop tool tells the on-screen indicator before it acts.

The person at the screen should see that vadgr is driving it, what it is about
to do, and where, and should be able to stop it with Esc. On GNOME the shell
extension draws that (see ``platform/backends/gnome_shell.py``); this module
decides what to tell it. It is pure: a tool name and its arguments in, a
``Cue`` out, so the wording and the coordinates are tested without a shell.

Only the tools that act on the desktop are cued: the native UI tools, the
pixel tools and ``app_open``. The browser tier drives a page, not the desktop,
and the Tier 0 tools touch no window.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

INDICATED_TOOLS = frozenset({
    "ui_tree", "ui_find", "ui_windows", "ui_act", "ui_wait",
    "screenshot", "screenshot_region",
    "click", "double_click", "right_click", "move_mouse", "scroll", "drag",
    "type_text", "key_press",
    "app_open",
})

USER_STOPPED = "user_stopped"

# Click-type actions get a ring at their target before they fire.
_RINGED = {
    "click": "Clicking",
    "double_click": "Double-clicking",
    "right_click": "Right-clicking",
    "drag": "Dragging",
}
_PLAIN = {
    "screenshot": "Taking a screenshot",
    "screenshot_region": "Taking a screenshot",
    "move_mouse": "Moving the pointer",
    "scroll": "Scrolling",
    "type_text": "Typing",
    "ui_windows": "Listing windows",
}
_ACT_VERBS = {
    "click": "Clicking",
    "toggle": "Toggling",
    "expand": "Expanding",
    "focus": "Focusing",
    "set_text": "Typing into",
}
_ESCAPE_KEYS = frozenset({"escape", "esc"})
_NAME_LIMIT = 40


@dataclass(frozen=True)
class Cue:
    """One Begin call: the pill text, and a point or box to highlight.

    Coordinates are screen pixels in the space the screenshots use; -1 means
    there is nothing to point at. ``own_escape`` marks a key press that sends
    Escape itself, which the shell must let through instead of stopping on it.
    """

    tool: str
    text: str
    x: int = -1
    y: int = -1
    width: int = 0
    height: int = 0
    own_escape: bool = False


def stopped_result() -> dict:
    """The result every cued tool returns while the user has stopped vadgr."""
    return {
        "ok": False,
        "error": USER_STOPPED,
        "message": (
            "The user pressed Esc to stop. Do not call another desktop tool; "
            "wait for the user's next instruction. Desktop tools are refused "
            "for 30 seconds after the key press."
        ),
    }


def _quoted(value: str) -> str:
    value = " ".join(str(value).split())
    if len(value) > _NAME_LIMIT:
        value = value[: _NAME_LIMIT - 3] + "..."
    return f'"{value}"'


class ElementMemory:
    """Names and trusted bounds of the elements recent reads returned, by ref.

    ``ui_act`` takes only a ref, so this is how its cue can say what it is
    about to click and, where the session vouches for the bounds, outline it.
    Bounds are kept only for elements whose ``coordinate_trust`` is ``real``:
    a window-relative Wayland bound would put the box in the wrong place.
    """

    def __init__(self, limit: int = 512) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._items: OrderedDict[str, tuple[str, tuple | None]] = OrderedDict()

    def get(self, ref: str) -> tuple[str, tuple | None] | None:
        with self._lock:
            return self._items.get(ref)

    def _remember(self, element: dict) -> None:
        ref = element.get("ref")
        if not isinstance(ref, str):
            return
        bounds = element.get("bounds")
        box = None
        if element.get("coordinate_trust") == "real" and isinstance(bounds, dict):
            try:
                box = (int(bounds["x"]), int(bounds["y"]), int(bounds["w"]), int(bounds["h"]))
            except (KeyError, TypeError, ValueError):
                box = None
        with self._lock:
            known = self._items.get(ref)
            if box is None and known is not None:
                box = known[1]  # a re-read without trust keeps the trusted box
            self._items[ref] = (str(element.get("name") or ""), box)
            self._items.move_to_end(ref)
            while len(self._items) > self._limit:
                self._items.popitem(last=False)

    def _walk(self, node: dict, budget: list[int]) -> None:
        if budget[0] <= 0:
            return
        budget[0] -= 1
        self._remember(node)
        for child in node.get("children") or ():
            if isinstance(child, dict):
                self._walk(child, budget)

    def observe(self, tool: str, result) -> None:
        """Learn from a ui_* result. Anything unexpected is ignored."""
        if not isinstance(result, dict) or result.get("ok") is False:
            return
        if tool == "ui_find":
            for element in result.get("elements") or ():
                if isinstance(element, dict):
                    self._remember(element)
        elif tool == "ui_wait" and isinstance(result.get("element"), dict):
            self._remember(result["element"])
        elif tool == "ui_tree":
            budget = [self._limit]
            roots = result.get("roots") or [result.get("root")]
            for root in roots:
                if isinstance(root, dict):
                    self._walk(root, budget)


def _escape_in(keys: str) -> bool:
    return any(k.strip().lower() in _ESCAPE_KEYS for k in str(keys).split("+"))


def cue_for(
    tool: str,
    args: dict,
    to_real: Callable[[int, int], tuple[int, int]],
    memory: ElementMemory | None = None,
) -> Cue:
    """The cue for ``tool`` called with ``args`` (its bound arguments).

    ``to_real`` maps the display coordinates the pixel tools take to screen
    pixels, the same mapping the tools themselves apply.
    """
    if tool in _RINGED:
        if tool == "drag":
            x, y = to_real(args["start_x"], args["start_y"])
        else:
            x, y = to_real(args["x"], args["y"])
        return Cue(tool, _RINGED[tool], x, y)
    if tool in _PLAIN:
        return Cue(tool, _PLAIN[tool])
    if tool == "key_press":
        keys = str(args.get("keys", ""))
        return Cue(tool, f"Pressing {keys[:_NAME_LIMIT]}", own_escape=_escape_in(keys))
    if tool == "app_open":
        return Cue(tool, f"Opening {_quoted(args.get('target', ''))}")
    if tool in ("ui_find", "ui_wait"):
        verb = "Finding" if tool == "ui_find" else "Waiting for"
        name, role = args.get("name") or "", args.get("role") or ""
        if name:
            return Cue(tool, f"{verb} {_quoted(name)}")
        if role:
            return Cue(tool, f"{verb} {role}")
        return Cue(tool, "Finding controls" if tool == "ui_find" else "Waiting")
    if tool == "ui_tree":
        app = args.get("app") or ""
        if app == "*":
            return Cue(tool, "Reading every window")
        return Cue(tool, f"Reading {_quoted(app)}" if app else "Reading the window")
    if tool == "ui_act":
        verb = _ACT_VERBS.get(str(args.get("action", "")).lower(), "Acting on")
        known = memory.get(str(args.get("ref", ""))) if memory is not None else None
        if known is None:
            return Cue(tool, verb if verb != "Acting on" else "Acting")
        name, box = known
        text = f"{verb} {_quoted(name)}" if name else verb
        if box is not None:
            x, y, width, height = box
            if width > 0 and height > 0:
                return Cue(tool, text, x, y, width, height)
        return Cue(tool, text)
    return Cue(tool, tool)
