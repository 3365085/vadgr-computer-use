# Copyright 2026 Victor Santiago Montaño Diaz
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""The structured (AT-SPI) tier's backend seam.

The four ui_* tools are thin over a StructuredBackend: they resolve the backend
for the running session, call it, and normalise the result. The backend is
optional at import and optional at runtime - a box with no accessibility bus
must still start and answer ``at_spi_unavailable`` rather than fail, which is the
lesson 0.6.6 shipped. Resolution therefore never raises: it returns None, and the
tools report the remedy.

Errors are named and absence is not one: an empty match is a successful read, not
a failure (conflating them teaches a model to retry a working tool). A stale ref
is ``element_gone`` and never degrades to clicking an old coordinate - re-acting
on where something used to be is the exact failure a structured tier removes.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Named error codes (the contract; the loop and the model read these, not prose).
AT_SPI_UNAVAILABLE = "at_spi_unavailable"
NO_TREE = "no_tree"
ELEMENT_GONE = "element_gone"
TIMEOUT = "timeout"
UNSUPPORTED_ACTION = "unsupported_action"
# ui_act by name and role: it acts only on exactly one match, and names the
# other two outcomes rather than guessing.
NOT_FOUND = "not_found"
AMBIGUOUS = "ambiguous"
NO_TARGET = "no_target"

# The one-line remedy that ships with at_spi_unavailable. It points at the single
# cross-distro provisioner rather than a hand-typed package line, per the family's
# "easy to install is a contract" rule.
_LINUX_REMEDY = (
    "No accessibility bus reachable. Enable it and install the stack with "
    "`vadgr-cua install-deps` (at-spi2-core plus the toolkit bridges)."
)

# The structured native tier speaks AT-SPI, which only Linux has. Telling a macOS
# or Windows caller to install at-spi2-core sends them after a package their
# platform cannot have, and reads as a broken install rather than a tier that is
# not offered there. The browser tier and the pixel tier both still work.
_NO_ATSPI_PLATFORM_REMEDY = (
    "The structured native tier needs AT-SPI, which is Linux only. On this "
    "platform use the browser tier for web pages, or the screenshot and "
    "pointer tools for native windows."
)


def unavailable_remedy(platform: str | None = None) -> str:
    """The remedy that fits the platform being asked."""
    name = platform if platform is not None else sys.platform
    if name.startswith("linux"):
        return _LINUX_REMEDY
    return _NO_ATSPI_PLATFORM_REMEDY


# Kept as a module attribute because callers import it by name. It resolves once
# for the running platform, which is the only one a given process can report on.
UNAVAILABLE_REMEDY = unavailable_remedy()


@dataclass(frozen=True)
class Bounds:
    """Screen bounds of an element, in the same space the Tier 2 tools take.

    On Wayland the compositor withholds a client's global surface position, so a
    toolkit reports its window origin as (0, 0) and these bounds are
    window-relative rather than true screen pixels. That is why grounding a
    Tier 2 click on them is an X11-only capability: the resolver reports the
    coordinate trust so a caller never aims a pixel click at a Wayland origin.
    """

    x: int
    y: int
    w: int
    h: int

    def as_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass(frozen=True)
class Element:
    """One accessible element: an opaque, session-scoped ref plus what it is.

    ``text`` is the element's textual content for text/editable/value elements
    (empty everywhere else). It is what makes a ``set_text`` confirmable: the
    caller reads back what actually landed rather than trusting an ``ok``.

    ``window`` is the title of the top-level window the element was found
    under. It is what disambiguates equal matches across windows: nine Writer
    documents expose nine paragraphs with the same role, name and (on Wayland,
    window-relative) bounds, and only the owning window tells them apart.

    ``coordinate_trust`` says whether this element's bounds are true screen
    pixels a Tier 2 click can aim at. It is only carried on Wayland, where the
    answer is per window: an XWayland client's bounds are real (the X server
    vouches for the window origin), a Wayland-native client's are
    window-relative. On X11 every bound is real and the capability block
    already says so once.

    ``unknown`` names the fields the application did not answer for in time
    ("bounds", "states"). Their null and empty values are then not facts about
    the element, and the field says so rather than leaving a reader to guess.
    """

    ref: str
    role: str
    name: str
    bounds: Bounds | None = None
    states: tuple[str, ...] = ()
    text: str | None = None
    window: str | None = None
    coordinate_trust: str | None = None
    unknown: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        out = {
            "ref": self.ref,
            "role": self.role,
            "name": self.name,
            "bounds": self.bounds.as_dict() if self.bounds else None,
            "states": list(self.states),
        }
        # Only carried when the element actually has text, so a button or a
        # container is not padded with a null field on every read.
        if self.text is not None:
            out["text"] = self.text
        # Only carried where a window is known (find results); act's re-read
        # has no frame in hand and omits it rather than shipping a null.
        if self.window is not None:
            out["window"] = self.window
        # Only carried where the session makes it ambiguous (Wayland).
        if self.coordinate_trust is not None:
            out["coordinate_trust"] = self.coordinate_trust
        # Only carried where a read went unanswered, so an answering app pays
        # nothing for it.
        if self.unknown:
            out["unknown"] = list(self.unknown)
        return out


class StructuredError(Exception):
    """A named structured-tier failure, carrying its code and optional extras."""

    def __init__(self, code: str, message: str = "", **extra) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message
        self.extra = extra

    def as_dict(self) -> dict:
        out = {"ok": False, "error": self.code}
        if self.message:
            out["message"] = self.message
        out.update(self.extra)
        return out


@runtime_checkable
class StructuredBackend(Protocol):
    """What a per-OS structured backend (AT-SPI, later UIA, AX) must provide.

    One provider per OS resolves behind this seam; the tools never branch on the
    OS or the distro. Each method may raise StructuredError with a named code.
    """

    def tree(self, depth: int, app: str) -> dict: ...
    def find(self, role: str, name: str, app: str) -> list[Element]: ...
    def nearest(
        self, role: str, name: str, app: str, limit: int
    ) -> list[Element]: ...
    def act(self, ref: str, action: str, text: str) -> dict: ...
    def wait(
        self, role: str, name: str, timeout_ms: int, app: str
    ) -> Element | None: ...
    def windows(self) -> dict: ...
    def capability(self) -> dict: ...


# The resolved backend is cached for the process, and the cache is the reason a
# ref survives across tool calls: ui_find hands out a ref, ui_act resolves it, and
# both must reach the same backend instance holding the same ref table. Rebuilding
# per call would reset that table and make every ref look stale. A None result is
# cached too, because "no structured tier on this OS" does not change mid-session.
_CACHED_BACKEND: StructuredBackend | None = None
_RESOLVED = False


def resolve_backend() -> StructuredBackend | None:
    """Return the process's structured backend, or None if there is none.

    Never raises. A missing optional dependency or an OS with no structured tier
    yet both mean the same thing to a caller - "no structured tier here" - which
    the tools translate to at_spi_unavailable with its remedy. The import is
    deferred so the package loads on a box with no accessibility stack at all. The
    backend itself is built at most once and reused (see the cache note above).
    """
    global _CACHED_BACKEND, _RESOLVED
    if _RESOLVED:
        return _CACHED_BACKEND
    _RESOLVED = True
    try:
        from computer_use.tools.ui.atspi import build_atspi_backend

        _CACHED_BACKEND = build_atspi_backend()
    except Exception:
        _CACHED_BACKEND = None
    return _CACHED_BACKEND


def structured_capability() -> dict:
    """The structured tier's capability, for get_platform_info to report.

    Always a dict, never a raise: a box with no structured tier answers
    ``available: False`` with a reason rather than an error, so the capability is
    discoverable in one read. ``available`` tracks bus reachability, because a
    resolved backend whose bus is down still cannot drive the tier.
    """
    backend = resolve_backend()
    if backend is None:
        return {
            "available": False,
            "backend": None,
            "reason": "no structured tier on this platform",
        }
    cap = dict(backend.capability())
    cap["available"] = bool(cap.get("bus_reachable", False))
    return cap
