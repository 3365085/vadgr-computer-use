# Copyright 2026 Victor Santiago Montaño Diaz
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""The GNOME Shell extension's Python side, and the contract with the shell side.

The client is driven through a fake D-Bus connection and the capture backend
through a fake client, so the degrade paths (no bus, no extension, a slow
shell, the user's Esc) are all provable here. The extension's own logic runs
under Node with stubbed GNOME APIs (gnome_extension/test_extension.mjs). None
of this proves GNOME behaves as stubbed: that is the live run on GNOME 50.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from computer_use.core.errors import ScreenCaptureError
from computer_use.core.types import Region, ScreenState
from computer_use.platform.backends import gnome_shell
from computer_use.platform.backends.gnome_shell import (
    ExtensionUnavailable,
    GnomeShellScreenCapture,
    ShellClient,
    UserStopped,
)

_TESTS = Path(__file__).resolve().parent
_EXTENSION = gnome_shell.extension_dir()


def _png(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height)).save(buf, format="PNG")
    return buf.getvalue()


# -- the client, over a fake connection ----------------------------------------

needs_jeepney = pytest.mark.skipif(
    gnome_shell._jeepney is None, reason="jeepney is a Linux-only dependency"
)


class FakeConnection:
    """Answers each method call from a script of (kind, payload) replies."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []
        self.closed = False

    def send_and_get_reply(self, message, timeout=None):
        fields = message.header.fields
        self.sent.append(SimpleNamespace(
            member=fields[gnome_shell._jeepney.HeaderFields.member],
            signature=fields.get(gnome_shell._jeepney.HeaderFields.signature, ""),
            destination=fields[gnome_shell._jeepney.HeaderFields.destination],
            path=fields[gnome_shell._jeepney.HeaderFields.path],
            body=message.body,
            timeout=timeout,
        ))
        kind, payload = self.replies.pop(0)
        if kind == "raise":
            raise payload
        if kind == "error":
            name, text = payload
            return SimpleNamespace(
                header=SimpleNamespace(
                    message_type=gnome_shell._jeepney.MessageType.error,
                    fields={gnome_shell._jeepney.HeaderFields.error_name: name},
                ),
                body=(text,),
            )
        return SimpleNamespace(
            header=SimpleNamespace(
                message_type=gnome_shell._jeepney.MessageType.method_return, fields={}
            ),
            body=payload,
        )

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _client(*connections, clock=None):
    pending = list(connections)

    def connect():
        conn = pending.pop(0)
        if isinstance(conn, Exception):
            raise conn
        return conn

    return ShellClient(connect=connect, clock=clock or Clock())


@needs_jeepney
class TestShellClient:
    def test_begin_sends_the_cue_and_reads_the_stop_flag(self):
        conn = FakeConnection(("ok", (True,)))
        client = _client(conn)
        assert client.begin("click", "Clicking", 10, 20, 0, 0, False) is True
        (call,) = conn.sent
        assert call.member == "Begin"
        assert call.signature == "ssiiiib"
        assert call.destination == gnome_shell.BUS_NAME
        assert call.path == gnome_shell.OBJECT_PATH
        assert call.body == ("click", "Clicking", 10, 20, 0, 0, False)
        assert call.timeout <= 1.0

    def test_begin_is_a_no_op_without_the_extension(self):
        clock = Clock()
        conn = FakeConnection(
            ("error", ("org.freedesktop.DBus.Error.ServiceUnknown", "no owner")),
            ("ok", (False,)),
        )
        client = _client(conn, clock=clock)
        assert client.begin("click", "Clicking") is False
        # Believed absent for a moment: the next call does not touch the bus.
        assert client.begin("click", "Clicking") is False
        assert len(conn.sent) == 1
        clock.now += 2.5
        assert client.begin("click", "Clicking") is False
        assert len(conn.sent) == 2

    def test_no_session_bus_is_a_no_op_and_is_not_retried_at_once(self):
        clock = Clock()
        attempts = []

        def connect():
            attempts.append(clock.now)
            raise OSError("no DBUS_SESSION_BUS_ADDRESS")

        client = ShellClient(connect=connect, clock=clock)
        assert client.begin("click", "Clicking") is False
        assert client.available() is False
        assert len(attempts) == 1
        clock.now += 31
        assert client.begin("click", "Clicking") is False
        assert len(attempts) == 2

    def test_a_shell_that_does_not_answer_never_holds_the_tool(self):
        first = FakeConnection(("raise", TimeoutError("no reply")))
        second = FakeConnection(("ok", (False,)))
        client = _client(first, second)
        assert client.begin("click", "Clicking") is False
        assert first.closed is True  # it may still owe the late reply
        assert client.begin("click", "Clicking") is False
        assert len(second.sent) == 1

    def test_a_refused_capture_after_esc_is_user_stopped(self):
        conn = FakeConnection(
            ("error", (gnome_shell.USER_STOPPED_ERROR, "The user pressed Esc"))
        )
        with pytest.raises(UserStopped, match="user_stopped"):
            _client(conn).screenshot("/run/user/1/x.png")

    def test_other_shell_errors_are_capture_errors(self):
        conn = FakeConnection(
            ("error", ("com.vadgr.cua.Shell.Error.Failed", "the area lies outside"))
        )
        with pytest.raises(ScreenCaptureError, match="the area lies outside"):
            _client(conn).screenshot("/run/user/1/x.png", 1, 2, 3, 4)

    def test_screenshot_and_size_signatures(self):
        conn = FakeConnection(("ok", ()), ("ok", (3840, 2160)))
        client = _client(conn)
        client.screenshot("/run/user/1/x.png", 1, 2, 3, 4)
        assert client.capture_size() == (3840, 2160)
        shot, size = conn.sent
        assert (shot.member, shot.signature) == ("Screenshot", "siiii")
        assert shot.body == ("/run/user/1/x.png", 1, 2, 3, 4)
        assert size.member == "CaptureSize"

    def test_available_probes_with_a_size_query_not_a_capture(self):
        conn = FakeConnection(("ok", (1920, 1080)))
        client = _client(conn)
        assert client.available() is True
        assert [c.member for c in conn.sent] == ["CaptureSize"]


# -- the capture backend, over a fake client ----------------------------------


class FakeClient:
    def __init__(self, png=None, error=None, size=(2560, 1440)):
        self.png = png
        self.error = error
        self.size = size
        self.shots = []

    def screenshot(self, path, x=0, y=0, width=0, height=0):
        self.shots.append((path, x, y, width, height))
        if self.error is not None:
            raise self.error
        assert not os.path.exists(path), "the shell must create the file itself"
        Path(path).write_bytes(self.png)

    def capture_size(self):
        if self.error is not None:
            raise self.error
        return self.size


class FakePortal:
    def __init__(self):
        self.calls = []

    def capture_full(self):
        self.calls.append("full")
        return ScreenState(image_bytes=b"portal", width=1, height=1)

    def capture_region(self, region):
        self.calls.append(("region", region))
        return ScreenState(image_bytes=b"portal", width=1, height=1)

    def get_screen_size(self):
        self.calls.append("size")
        return (1, 1)


class TestShellCapture:
    def test_full_capture_reads_and_removes_the_shell_png(self):
        client = FakeClient(png=_png(64, 48))
        capture = GnomeShellScreenCapture(client=client)
        state = capture.capture_full()
        assert (state.width, state.height) == (64, 48)
        (path, *area) = client.shots[0]
        assert area == [0, 0, 0, 0]
        assert path.endswith(".png") and os.path.isabs(path)
        assert not os.path.exists(os.path.dirname(path))  # private dir removed
        assert capture.backend_name == "gnome-shell-extension"

    def test_region_capture_passes_the_region(self):
        client = FakeClient(png=_png(30, 20))
        capture = GnomeShellScreenCapture(client=client)
        state = capture.capture_region(Region(5, 6, 30, 20))
        assert client.shots[0][1:] == (5, 6, 30, 20)
        assert (state.width, state.height) == (30, 20)

    def test_screen_size_comes_from_the_shell_without_a_capture(self):
        client = FakeClient(size=(2560, 1440))
        capture = GnomeShellScreenCapture(client=client)
        assert capture.get_screen_size() == (2560, 1440)
        assert client.shots == []

    def test_falls_back_to_the_portal_when_the_extension_is_gone(self):
        client = FakeClient(error=ExtensionUnavailable("disabled"))
        portal = FakePortal()
        capture = GnomeShellScreenCapture(client=client, fallback=lambda: portal)
        assert capture.capture_full().image_bytes == b"portal"
        assert capture.capture_region(Region(1, 2, 3, 4)).image_bytes == b"portal"
        assert capture.get_screen_size() == (1, 1)
        assert capture.backend_name == "portal"

    def test_back_on_the_extension_once_it_answers_again(self):
        client = FakeClient(error=ExtensionUnavailable("disabled"), png=_png(8, 8))
        capture = GnomeShellScreenCapture(client=client, fallback=FakePortal)
        capture.capture_full()
        client.error = None
        assert capture.capture_full().width == 8
        assert capture.backend_name == "gnome-shell-extension"

    def test_the_users_esc_is_not_hidden_by_a_fallback(self):
        client = FakeClient(error=UserStopped("user_stopped: Esc"))
        portal = FakePortal()
        capture = GnomeShellScreenCapture(client=client, fallback=lambda: portal)
        with pytest.raises(UserStopped):
            capture.capture_full()
        assert portal.calls == []


# -- choosing the backend ----------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="the Linux platform module")
class TestSelection:
    def test_gnome_wayland_with_the_extension_uses_it(self, monkeypatch):
        from computer_use.platform import linux

        monkeypatch.setattr(linux, "_is_wayland", lambda: True)
        monkeypatch.setattr(gnome_shell, "session_is_gnome", lambda: True)
        monkeypatch.setattr(gnome_shell, "extension_available", lambda: True)
        monkeypatch.setattr(linux, "portal_available", lambda: True)
        capture = linux._create_screen_capture()
        assert isinstance(capture, GnomeShellScreenCapture)

    def test_without_the_extension_gnome_keeps_the_portal(self, monkeypatch):
        from computer_use.platform import linux

        monkeypatch.setattr(linux, "_is_wayland", lambda: True)
        monkeypatch.setattr(gnome_shell, "session_is_gnome", lambda: True)
        monkeypatch.setattr(gnome_shell, "extension_available", lambda: False)
        monkeypatch.setattr(linux, "portal_available", lambda: True)
        monkeypatch.setattr(shutil, "which", lambda name: None)
        capture = linux._create_screen_capture()
        assert capture.backend_name == "portal"

    def test_another_desktop_is_never_probed(self, monkeypatch):
        from computer_use.platform import linux

        monkeypatch.setattr(linux, "_is_wayland", lambda: True)
        monkeypatch.setattr(gnome_shell, "session_is_gnome", lambda: False)
        monkeypatch.setattr(
            gnome_shell, "extension_available",
            lambda: (_ for _ in ()).throw(AssertionError("probed")),
        )
        monkeypatch.setattr(linux, "portal_available", lambda: True)
        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert linux._create_screen_capture().backend_name == "portal"

    def test_doctor_lists_the_extension_ahead_of_the_cli_tools(self, monkeypatch):
        from computer_use.platform.backends import linux_providers
        from computer_use.platform.resolver.session import SessionContext

        monkeypatch.setattr(gnome_shell, "extension_available", lambda: True)
        monkeypatch.setattr(linux_providers, "portal_available", lambda: True)
        ctx = SessionContext(server="wayland", compositor="gnome",
                             has_uinput=False, libs=frozenset())
        report = linux_providers.describe_backends(ctx)
        assert report["capture"]["selected"] == "gnome-shell-extension"
        priorities = {c["name"]: c["priority"] for c in report["capture"]["candidates"]}
        assert priorities["gnome-shell-extension"] == 65
        assert priorities["grim"] < 65 < priorities["mss"]

    def test_doctor_skips_the_extension_off_gnome(self, monkeypatch):
        from computer_use.platform.backends import linux_providers
        from computer_use.platform.resolver.session import SessionContext

        monkeypatch.setattr(gnome_shell, "extension_available", lambda: True)
        ctx = SessionContext(server="wayland", compositor="kde",
                             has_uinput=False, libs=frozenset())
        candidates = linux_providers.describe_backends(ctx)["capture"]["candidates"]
        (ext,) = [c for c in candidates if c["name"] == "gnome-shell-extension"]
        assert ext["applicable"] is False


# -- the packaged extension and its contract with this client -----------------


def _interface_methods() -> dict[str, tuple[str, str]]:
    """Method -> (in signature, out signature), read from extension.js."""
    source = (_EXTENSION / "extension.js").read_text()
    xml = re.search(r"const IFACE = `(.*?)`;", source, re.DOTALL).group(1)
    methods = {}
    for name, body in re.findall(r'<method name="(\w+)">(.*?)</method>', xml, re.DOTALL):
        args = re.findall(r'<arg type="(\w+)" direction="(in|out)"', body)
        methods[name] = (
            "".join(t for t, d in args if d == "in"),
            "".join(t for t, d in args if d == "out"),
        )
    return methods


class TestPackagedExtension:
    def test_metadata_names_this_extension_and_only_gnome_50(self):
        metadata = json.loads((_EXTENSION / "metadata.json").read_text())
        assert metadata["uuid"] == gnome_shell.EXTENSION_UUID == _EXTENSION.name
        assert metadata["shell-version"] == ["50"]

    def test_every_file_the_installer_packs_is_there(self):
        for name in gnome_shell.EXTENSION_FILES:
            assert (_EXTENSION / name).is_file(), name

    def test_the_bus_name_and_path_match_the_client(self):
        source = (_EXTENSION / "extension.js").read_text()
        assert f"const BUS_NAME = '{gnome_shell.BUS_NAME}';" in source
        assert f"const OBJECT_PATH = '{gnome_shell.OBJECT_PATH}';" in source
        assert f'<interface name="{gnome_shell.INTERFACE}">' in source
        assert gnome_shell.USER_STOPPED_ERROR == f"{gnome_shell.INTERFACE}.Error.UserStopped"
        assert "${ERROR_PREFIX}.UserStopped" in source

    def test_the_interface_signatures_match_the_client(self):
        assert _interface_methods() == {
            "Begin": ("ssiiiib", "b"),
            "Screenshot": ("siiii", ""),
            "CaptureSize": ("", "ii"),
        }

    def test_the_wheel_and_the_profile_wheels_ship_it(self):
        # A wheel without the files would leave install-deps nothing to install.
        from fnmatch import fnmatch

        repo = _TESTS.parent.parent
        pyproject = (repo / "pyproject.toml").read_text()
        block = re.search(r'"computer_use\.platform" = \[(.*?)\]', pyproject, re.DOTALL).group(1)
        patterns = re.findall(r'"([^"]+)"', block)
        package = repo / "computer_use" / "platform"
        for name in gnome_shell.EXTENSION_FILES:
            relative = (_EXTENSION / name).relative_to(package).as_posix()
            assert any(fnmatch(relative, p) for p in patterns), relative

        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "build_profile_wheels", repo / "scripts" / "build_profile_wheels.py"
        )
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        shipped = builder.source_files(repo)
        for name in gnome_shell.EXTENSION_FILES:
            relative = (_EXTENSION / name).relative_to(repo).as_posix()
            assert relative in shipped, relative


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_extension_logic_under_node():
    """Run the extension's own tests (stubbed GNOME APIs) with node:test."""
    harness = _TESTS / "gnome_extension"
    result = subprocess.run(
        ["node", "--import", "./register.mjs", "--test", "test_extension.mjs"],
        cwd=harness, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert re.search(r"^# fail 0$", result.stdout, re.MULTILINE), result.stdout[-2000:]
    assert not re.search(r"^# pass 0$", result.stdout, re.MULTILINE), "no extension test ran"
