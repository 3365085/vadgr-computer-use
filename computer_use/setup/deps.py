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

"""System-dependency provisioning - the bits ``pip`` cannot install.

The desktop tier's common GNOME/KDE/X11 paths are pure-python (jeepney, Pillow,
python-xlib) and need no system packages. What's left is genuine OS-package
residue - ``wl-clipboard`` for the clipboard, and write access to ``/dev/uinput``
for the uinput fallback. ``pip`` cannot install those (and must not shell out to
a package manager during install), so this module diagnoses what's missing and
emits an explicit, distro-aware plan, à la ``playwright install-deps``: printed
by default, executed only with ``--yes``.

On GNOME it also installs the vadgr GNOME Shell extension (the on-screen
indicator, Esc to stop, and screenshots without the flash). That one is a
per-user install, so it runs unprivileged after the root steps, and it needs
one log out and back in: GNOME Shell on Wayland loads a new extension only at
login.
"""

from __future__ import annotations

import ast
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from computer_use.platform.backends import gnome_shell

# package-manager -> (detect binary, install-command tokens WITHOUT escalation).
# The privilege escalation (pkexec / sudo) is chosen separately and prepended at
# run time, so the whole plan runs under a single auth prompt.
_MANAGERS = {
    "apt": ("apt-get", ["apt-get", "install", "-y"]),
    "dnf": ("dnf", ["dnf", "install", "-y"]),
    "pacman": ("pacman", ["pacman", "-S", "--noconfirm"]),
    "zypper": ("zypper", ["zypper", "install", "-y"]),
}

# logical dep -> package name(s) per manager. A value may name more than one
# package separated by spaces (the plan joins them into one install line), which
# is how the accessibility entry pulls the bus core and the ATK bridge together.
_PACKAGES = {
    "wl-clipboard": {m: "wl-clipboard" for m in _MANAGERS},
    # The structured Tier 1: the AT-SPI bus/registry (at-spi2-core) plus the ATK
    # bridge that makes GTK apps expose their tree onto it. Package names differ
    # per distro; the bridge is libatk-bridge on Debian and at-spi2-atk elsewhere.
    "at-spi2-core": {
        "apt": "at-spi2-core libatk-bridge2.0-0",
        "dnf": "at-spi2-core at-spi2-atk",
        "pacman": "at-spi2-core at-spi2-atk",
        "zypper": "at-spi2-core at-spi2-atk",
    },
    # The desktop launcher app_open shells out to: gio (glib bin tools), with
    # gtk-launch as the fallback. Both ship with a normal GNOME, so this only
    # fires on a stripped install.
    "desktop-launcher": {
        "apt": "libglib2.0-bin",
        "dnf": "glib2",
        "pacman": "glib2",
        "zypper": "glib2-tools",
    },
}

_UDEV_RULE = Path(__file__).resolve().parent / "udev" / "99-vadgr-uinput.rules"

# The GNOME Shell extension: logical dep name, and what the plan prints.
GNOME_EXTENSION = "gnome-shell-extension"
_EXTENSION_BUNDLE = f"{gnome_shell.EXTENSION_UUID}.shell-extension.zip"
RELOGIN_NOTE = (
    "Log out and back in once: GNOME Shell on Wayland loads a new extension "
    "only at login."
)


def detect_package_manager() -> str | None:
    for name, (binary, _cmd) in _MANAGERS.items():
        if shutil.which(binary):
            return name
    return None


def _clipboard_present() -> bool:
    return any(shutil.which(t) for t in ("wl-copy", "wl-paste", "xclip", "xsel"))


def accessibility_bus_reachable() -> bool:
    """Whether the AT-SPI bus answers. Isolated so diagnose can be tested.

    Never raises: off Linux, or with no accessibility stack, it is False, and the
    diagnosis then lists the stack as missing.
    """
    try:
        from computer_use.tools.ui.atspi import bus_reachable

        return bus_reachable()
    except Exception:
        return False


def diagnose() -> dict:
    """Report which system-level deps are missing, each with a reason + fix."""
    missing = []
    if not _clipboard_present():
        missing.append({
            "name": "wl-clipboard",
            "reason": "no clipboard backend (wl-copy/xclip) on PATH",
            "fix": "install wl-clipboard",
        })
    if not os.access("/dev/uinput", os.W_OK):
        missing.append({
            "name": "uinput-access",
            "reason": "/dev/uinput is not writable (uinput input fallback unavailable)",
            "fix": "install the udev rule and join the 'input' group",
        })
    # The structured tier needs a reachable accessibility bus. Report it missing
    # only on Linux: it is the only platform with an AT-SPI stack to install, and
    # probing elsewhere would list a package that does not apply.
    if sys.platform.startswith("linux") and not accessibility_bus_reachable():
        missing.append({
            "name": "at-spi2-core",
            "reason": "no accessibility bus reachable (structured Tier 1 unavailable)",
            "fix": "install at-spi2-core and the ATK bridge, then start a desktop session",
        })
    # The launcher app_open needs one of gio or gtk-launch on PATH.
    if sys.platform.startswith("linux") and not _launcher_present():
        missing.append({
            "name": "desktop-launcher",
            "reason": "no gio or gtk-launch on PATH (app_open cannot launch apps)",
            "fix": "install the glib launcher tools",
        })
    notes: list[str] = []
    extension = gnome_extension_status()
    if extension is not None:
        if not extension["installed"] or not extension["enabled"]:
            state = "not installed or out of date" if not extension["installed"] else "not enabled"
            missing.append({
                "name": GNOME_EXTENSION,
                "reason": (
                    f"the vadgr GNOME Shell extension is {state} (no on-screen "
                    "indicator or Esc to stop, and every screenshot flashes)"
                ),
                "fix": "install and enable it for this user, then log out and back in",
            })
        elif not extension["running"]:
            notes.append(
                "The vadgr GNOME Shell extension is installed and enabled but not "
                f"running yet. {RELOGIN_NOTE}"
            )
    elif _gnome_session_as_root():
        notes.append(
            "Run install-deps as your desktop user, not as root, to install the "
            "vadgr GNOME Shell extension."
        )
    return {"package_manager": detect_package_manager(), "missing": missing, "notes": notes}


def _launcher_present() -> bool:
    return any(shutil.which(t) for t in ("gio", "gtk-launch"))


# -- the GNOME Shell extension: per user, unprivileged -------------------------


def _gnome_session_as_root() -> bool:
    return (
        sys.platform.startswith("linux")
        and gnome_shell.session_is_gnome()
        and os.geteuid() == 0
    )


def _extension_installed() -> bool:
    """Whether this user's copy matches the packaged extension byte for byte."""
    source, target = gnome_shell.extension_dir(), gnome_shell.user_extension_dir()
    try:
        return all(
            (target / name).read_bytes() == (source / name).read_bytes()
            for name in gnome_shell.EXTENSION_FILES
        )
    except OSError:
        return False


def _gsettings_get(key: str) -> str | None:
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.shell", key],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _gsettings_strv(key: str) -> list[str] | None:
    """An org.gnome.shell string-list setting, or None when it cannot be read."""
    text = _gsettings_get(key)
    if text is None:
        return None
    if text.startswith("@as"):  # how gsettings prints an empty list
        text = text[3:].strip()
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return value


def _gsettings_set_strv(key: str, values: list[str]) -> bool:
    text = "[" + ", ".join(json.dumps(v) for v in values) + "]"
    try:
        out = subprocess.run(
            ["gsettings", "set", "org.gnome.shell", key, text],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def _extension_enabled() -> bool:
    uuid = gnome_shell.EXTENSION_UUID
    enabled = _gsettings_strv("enabled-extensions")
    disabled = _gsettings_strv("disabled-extensions") or []
    return enabled is not None and uuid in enabled and uuid not in disabled


def gnome_extension_status() -> dict | None:
    """Where the vadgr GNOME Shell extension stands, or None where it does not apply.

    It applies to a Linux GNOME session that has the ``gnome-extensions`` tool,
    run as the desktop user: root's extensions are not the desktop's.
    """
    if not sys.platform.startswith("linux") or not gnome_shell.session_is_gnome():
        return None
    if not shutil.which("gnome-extensions") or os.geteuid() == 0:
        return None
    return {
        "installed": _extension_installed(),
        "enabled": _extension_enabled(),
        "running": gnome_shell.extension_available(),
    }


def pack_extension(dest: Path) -> Path:
    """Zip the packaged extension the way ``gnome-extensions pack`` lays it out."""
    source = gnome_shell.extension_dir()
    bundle = dest / _EXTENSION_BUNDLE
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in gnome_shell.EXTENSION_FILES:
            archive.write(source / name, arcname=name)
    return bundle


def _enable_in_settings(uuid: str) -> bool:
    """List the extension as enabled, so the shell loads it at the next login.

    The running shell answers ``gnome-extensions enable`` for an extension it
    has not loaded with "does not exist", which is every fresh install on
    Wayland. The setting is what the shell reads when it starts.
    """
    enabled = _gsettings_strv("enabled-extensions")
    if enabled is None:
        return False
    ok = uuid in enabled or _gsettings_set_strv("enabled-extensions", [*enabled, uuid])
    disabled = _gsettings_strv("disabled-extensions") or []
    if ok and uuid in disabled:
        ok = _gsettings_set_strv("disabled-extensions", [u for u in disabled if u != uuid])
    return ok


def _user_steps(missing_names: set[str]) -> list[str]:
    """The unprivileged steps, run as the desktop user after the root ones."""
    if GNOME_EXTENSION not in missing_names:
        return []
    return [
        f"gnome-extensions install --force {_EXTENSION_BUNDLE}",
        f"gnome-extensions enable {gnome_shell.EXTENSION_UUID}",
    ]


def install_gnome_extension() -> int:
    """Install and enable the extension for this user. Returns an exit code."""
    uuid = gnome_shell.EXTENSION_UUID
    with tempfile.TemporaryDirectory(prefix="vadgr-cua-extension-") as workdir:
        bundle = pack_extension(Path(workdir))
        print(f"\n$ gnome-extensions install --force {bundle.name}")
        rc = subprocess.run(["gnome-extensions", "install", "--force", str(bundle)]).returncode
    if rc != 0:
        print(f"extension install failed (rc={rc})")
        return rc
    print(f"$ gnome-extensions enable {uuid}")
    enable = subprocess.run(
        ["gnome-extensions", "enable", uuid], capture_output=True, text=True
    )
    if enable.returncode != 0 and not _enable_in_settings(uuid):
        print((enable.stderr or enable.stdout).strip())
        print("extension enable failed")
        return enable.returncode or 1
    if _gsettings_get("disable-user-extensions") == "true":
        print(
            "GNOME has user extensions switched off. Turn them on in the "
            "Extensions app, or run: gsettings set org.gnome.shell "
            "disable-user-extensions false"
        )
    print(RELOGIN_NOTE)
    return 0


def detect_escalation() -> str | None:
    """Pick how to gain root: ``pkexec`` (graphical polkit auth) when there is a
    display, else ``sudo``. Returns None if neither is available.

    pkexec pops a desktop password dialog (one click, no terminal), so the whole
    plan runs under a single GUI prompt - the macOS-style "authenticate once".
    """
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if has_display and shutil.which("pkexec"):
        return "pkexec"
    if shutil.which("sudo"):
        return "sudo"
    if shutil.which("pkexec"):
        return "pkexec"
    return None


def _privileged_steps(manager: str | None, missing_names: set[str]) -> list[str]:
    """The raw root commands (no escalation prefix) to provision the missing deps."""
    steps: list[str] = []
    pkgs = [
        _PACKAGES[name][manager]
        for name in sorted(missing_names)
        if name in _PACKAGES and manager in _MANAGERS
    ]
    if pkgs:
        steps.append(" ".join(_MANAGERS[manager][1] + pkgs))
    if "uinput-access" in missing_names:
        steps.append(f"cp {_UDEV_RULE} /etc/udev/rules.d/99-vadgr-uinput.rules")
        steps.append("udevadm control --reload-rules && udevadm trigger")
        steps.append(f"usermod -aG input {getpass.getuser()}")
    return steps


def build_plan(
    manager: str | None, missing_names: set[str], *, escalate: str = "sudo"
) -> list[str]:
    """Human-readable plan: the privileged steps, each shown under ``escalate``."""
    if not missing_names:
        return []
    steps = _privileged_steps(manager, missing_names)
    plan = [f"{escalate} {step}" for step in steps]
    if not steps and "wl-clipboard" in missing_names and manager is None:
        plan.append("# unknown package manager - add 'wl-clipboard' via your distro's tools")
    return plan + _user_steps(missing_names)


def install_deps(*, assume_yes: bool = False, dry_run: bool = True) -> int:
    """Diagnose, then print the plan (default) or run it under one auth prompt."""
    report = diagnose()
    notes = report.get("notes", [])
    if not report["missing"]:
        print("All system dependencies are present.")
        for note in notes:
            print(note)
        return 0

    manager = report["package_manager"]
    names = {item["name"] for item in report["missing"]}
    escalate = detect_escalation()
    plan = build_plan(manager, names, escalate=escalate or "sudo")

    print(f"Detected package manager: {manager or 'unknown'}")
    print(f"Privilege escalation: {escalate or 'none (run as root)'}")
    print("Missing:")
    for item in report["missing"]:
        print(f"  - {item['name']}: {item['reason']}")
    print("\nPlan:")
    for line in plan:
        print(f"  {line}")
    if GNOME_EXTENSION in names:
        print(f"\nThen: {RELOGIN_NOTE}")
    for note in notes:
        print(f"\n{note}")

    if dry_run or not assume_yes:
        print("\n(dry run - re-run with --yes to execute)")
        return 0

    rc = 0
    if names - {GNOME_EXTENSION}:
        rc = _run_privileged(_privileged_steps(manager, names), escalate)
    # The extension is the user's own and installs without root, so it goes
    # ahead even when a root step could not.
    if GNOME_EXTENSION in names:
        rc = install_gnome_extension() or rc
    return rc


def _run_privileged(steps: list[str], escalate: str | None) -> int:
    if not steps:
        print("\nNothing runnable (unknown package manager) - install the package manually.")
        return 1
    if escalate is None:
        print("\nNo pkexec or sudo available - run the plan as root.")
        return 1

    # One escalated invocation -> a single GUI/sudo auth prompt for the whole plan.
    joined = " && ".join(steps)
    print(f"\n$ {escalate} sh -c {joined!r}")
    rc = subprocess.run([escalate, "sh", "-c", joined]).returncode
    if rc != 0:
        print(f"provisioning failed (rc={rc})")
    return rc
