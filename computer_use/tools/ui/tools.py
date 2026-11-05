# Copyright 2026 Victor Santiago Montaño Diaz
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""The four structured-tier tools, thin over the resolved backend.

Four tools, not one multiplexed verb: ui_act mutates user state (Risk.MEDIUM) and
the other three only read (Risk.READ_ONLY). A single tool would carry one risk for
both, and the loop's approval reads that field, so multiplexing would either gate
reads behind approval or let writes through without it.
"""

from __future__ import annotations

from computer_use.tools.ui.backend import (
    AMBIGUOUS,
    AT_SPI_UNAVAILABLE,
    NO_TARGET,
    NOT_FOUND,
    TIMEOUT,
    UNAVAILABLE_REMEDY,
    Element,
    StructuredError,
    resolve_backend,
)

# How many candidates a not_found or ambiguous ui_act lists.
_CANDIDATES = 5


def _unavailable() -> dict:
    return {"ok": False, "error": AT_SPI_UNAVAILABLE, "remedy": UNAVAILABLE_REMEDY}


def ui_tree(depth: int = 6, app: str = "") -> dict:
    """Read an accessible tree, filtered and depth-capped.

    ``app=""`` reads the focused window (unchanged). ``app="Name"`` reads that
    application's window; ``app="*"`` reads every open window.
    """
    backend = resolve_backend()
    if backend is None:
        return _unavailable()
    try:
        return {"ok": True, **backend.tree(depth, app)}
    except StructuredError as e:
        return e.as_dict()


def ui_find(role: str = "", name: str = "", app: str = "") -> dict:
    """Find elements matching role, name, or both. An empty match is ``ok``.

    ``app=""`` searches the focused window (unchanged). ``app="Name"`` searches
    that application's window; ``app="*"`` searches every open window.
    """
    backend = resolve_backend()
    if backend is None:
        return _unavailable()
    try:
        elements = backend.find(role, name, app)
        return {"ok": True, "elements": [e.as_dict() for e in elements]}
    except StructuredError as e:
        return e.as_dict()


def ui_windows() -> dict:
    """List open top-level windows across all apps: app, title, active, ref, pid."""
    backend = resolve_backend()
    if backend is None:
        return _unavailable()
    try:
        return {"ok": True, **backend.windows()}
    except StructuredError as e:
        return e.as_dict()


def _candidate(element: Element) -> dict:
    out = {"ref": element.ref, "role": element.role, "name": element.name}
    if element.window is not None:
        out["window"] = element.window
    if element.bounds is not None:
        out["bounds"] = element.bounds.as_dict()
    return out


def _wanted(role: str, name: str) -> str:
    parts = [f"role={role!r}"] if role else []
    if name:
        parts.append(f"name={name!r}")
    return " ".join(parts)


def _resolve_target(backend, role: str, name: str, app: str) -> Element:
    """The one element a role and name pick out, or a named failure.

    A name matches as find matches it, as a case-insensitive substring, so
    "Save" also finds "Save As"; when several match, the ones whose whole name
    equals the request win. Anything but exactly one survivor is an error that
    lists candidates, because acting on a guess is the failure this avoids.
    """
    matches = backend.find(role, name, app)
    if name and len(matches) > 1:
        exact = [e for e in matches if e.name.lower() == name.lower()]
        if exact:
            matches = exact
    if len(matches) == 1:
        return matches[0]
    if not matches:
        nearest = backend.nearest(role, name, app, _CANDIDATES)
        raise StructuredError(
            NOT_FOUND,
            f"no element matches {_wanted(role, name)}; act on a candidate ref, "
            "or use ui_find to look further",
            candidates=[_candidate(e) for e in nearest],
        )
    raise StructuredError(
        AMBIGUOUS,
        f"{len(matches)} elements match {_wanted(role, name)}; act on one ref, "
        "or narrow name, role or app",
        count=len(matches),
        candidates=[_candidate(e) for e in matches[:_CANDIDATES]],
    )


def ui_act(
    ref: str = "",
    action: str = "",
    text: str = "",
    *,
    name: str = "",
    role: str = "",
    app: str = "",
) -> dict:
    """Perform an action on one element, then re-read and return its state.

    The element is ``ref`` when one is given (it wins over name and role), or
    else the single element ``name`` and/or ``role`` pick out within ``app``,
    scoped as ui_find scopes it. By name the result also carries the ``ref``
    acted on, for the next call.

    The re-read is the point: a structured action that returns ``ok`` without
    observing the result is a pixel click with better manners. A stale ref fails
    ``element_gone``; it never falls back to the element's old coordinates.
    """
    backend = resolve_backend()
    if backend is None:
        return _unavailable()
    try:
        if ref:
            return {"ok": True, **backend.act(ref, action, text)}
        if not name and not role:
            raise StructuredError(
                NO_TARGET, "pass a ref from ui_find, or a name and/or role"
            )
        target = _resolve_target(backend, role, name, app)
        return {"ok": True, "ref": target.ref, **backend.act(target.ref, action, text)}
    except StructuredError as e:
        return e.as_dict()


def ui_wait(
    role: str = "", name: str = "", timeout_ms: int = 5000, app: str = ""
) -> dict:
    """Block until a matching element appears or the timeout elapses (bounded).

    ``app`` scopes the wait the same way ``ui_find`` does: the focused window by
    default, a named application's window, or ``"*"`` for every open window.
    """
    backend = resolve_backend()
    if backend is None:
        return _unavailable()
    try:
        element = backend.wait(role, name, timeout_ms, app)
        if element is None:
            return {"ok": False, "error": TIMEOUT, "elapsed_ms": timeout_ms}
        return {"ok": True, "element": element.as_dict()}
    except StructuredError as e:
        return e.as_dict()
