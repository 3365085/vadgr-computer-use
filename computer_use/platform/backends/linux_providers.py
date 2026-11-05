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

"""Linux backend providers + the session-aware ``describe_backends`` for doctor.

Each desktop backend is wrapped as a provider (name + priority + ``supports`` +
``create``) and registered, so the resolver and ``vadgr-cua doctor`` see one
ordered list. ``describe_backends`` reports, for the live session, which capture
and input backend is selected and which candidates were applicable - the
"what got picked and why" view, without constructing (and thus without prompting
for) anything.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass

from computer_use.platform.backends.portal import portal_available
from computer_use.platform.resolver import registry
from computer_use.platform.resolver.providers import BackendUnavailable
from computer_use.platform.resolver.session import SessionContext


def _mutter_available() -> bool:
    try:
        from computer_use.platform import linux
    except ImportError:
        return False

    return linux._is_mutter_available()


def _evdev_present() -> bool:
    try:
        from computer_use.platform import linux
    except ImportError:
        return False

    return linux.evdev_import is not None


@dataclass
class _Provider:
    """A named, prioritised wrapper around one backend constructor."""

    name: str
    priority: int
    _supports: Callable[[SessionContext], bool]
    _factory: Callable[[SessionContext], object]

    def supports(self, ctx: SessionContext) -> bool:
        return self._supports(ctx)

    def create(self, ctx: SessionContext):
        try:
            return self._factory(ctx)
        except Exception as exc:  # construction failure => not usable
            raise BackendUnavailable(str(exc))


def _which(cmd: str) -> bool:
    return bool(shutil.which(cmd))


# --- capture providers (priority high -> low; see 0.4.1/linux-platform.md) ---
def _cap_mss(ctx):
    from computer_use.platform import linux

    return linux.MssScreenCapture()


def _cap_grim(ctx):
    from computer_use.platform import linux

    return linux.GrimScreenCapture()


def _cap_gnome(ctx):
    from computer_use.platform import linux

    return linux.GnomeScreenCapture()


def _cap_portal(ctx):
    from computer_use.platform import linux

    return linux.PortalScreenshotCapture()


def _cap_gnome_shell(ctx):
    from computer_use.platform.backends.gnome_shell import GnomeShellScreenCapture

    return GnomeShellScreenCapture()


def _gnome_shell_available() -> bool:
    from computer_use.platform.backends.gnome_shell import (
        extension_available,
        extension_installed,
    )

    return extension_installed() or extension_available()


CAPTURE_PROVIDERS = [
    _Provider("mss", 90, lambda c: c.server == "x11", _cap_mss),
    # The vadgr shell extension: no flash, and the indicator is kept out of the
    # image. Ahead of the CLI tools because on GNOME it is the only capture
    # path without the portal's flash; absent, GNOME falls through to them.
    _Provider(
        "gnome-shell-extension", 65,
        lambda c: c.server == "wayland" and c.compositor == "gnome" and _gnome_shell_available(),
        _cap_gnome_shell,
    ),
    _Provider("grim", 60, lambda c: c.server == "wayland" and _which("grim"), _cap_grim),
    _Provider(
        "gnome-screenshot", 50,
        lambda c: c.server == "wayland" and _which("gnome-screenshot"), _cap_gnome,
    ),
    _Provider("portal", 30, lambda c: c.server == "wayland" and portal_available(), _cap_portal),
]


# --- input providers ---
def _in_xtest(ctx):
    from computer_use.platform import linux

    return linux.XTestExecutor()


def _in_xdotool(ctx):
    from computer_use.platform import linux

    return linux.LinuxActionExecutor()


def _in_mutter(ctx):
    from computer_use.platform import linux

    return linux.MutterRemoteDesktopExecutor()


def _in_uinput(ctx):
    from computer_use.platform import linux

    return linux.UinputActionExecutor()


INPUT_PROVIDERS = [
    _Provider("xtest", 90, lambda c: c.server == "x11", _in_xtest),
    _Provider("xdotool", 85, lambda c: c.server == "x11" and _which("xdotool"), _in_xdotool),
    _Provider(
        "mutter-remotedesktop", 70,
        lambda c: c.server == "wayland" and c.compositor == "gnome" and _mutter_available(),
        _in_mutter,
    ),
    _Provider("evdev", 25, lambda c: c.server == "wayland" and _evdev_present(), _in_xtest),
    _Provider("uinput", 20, lambda c: c.server == "wayland" and c.has_uinput, _in_uinput),
]

for _p in CAPTURE_PROVIDERS:
    registry.register_capture(_p)
for _p in INPUT_PROVIDERS:
    registry.register_input(_p)


def _select(providers, ctx: SessionContext) -> dict:
    candidates = []
    selected = None
    for provider in sorted(providers, key=lambda p: -p.priority):
        ok = provider.supports(ctx)
        candidates.append({"name": provider.name, "priority": provider.priority, "applicable": ok})
        if ok and selected is None:
            selected = provider.name
    return {"selected": selected, "candidates": candidates}


def describe_backends(ctx: SessionContext | None = None) -> dict:
    """Session + selected capture/input backend + candidate applicability."""
    ctx = ctx or SessionContext.detect()
    return {
        "server": ctx.server,
        "compositor": ctx.compositor,
        "has_uinput": ctx.has_uinput,
        "libs": sorted(ctx.libs),
        "capture": _select(CAPTURE_PROVIDERS, ctx),
        "input": _select(INPUT_PROVIDERS, ctx),
    }
