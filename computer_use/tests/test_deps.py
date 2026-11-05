# Copyright 2026 Victor Santiago Montaño Diaz
#
# Licensed under the Apache License, Version 2.0 (the "License").

"""Dependency provisioning: package-manager detection + the install plan."""

from unittest.mock import patch

import pytest

from computer_use.setup import deps


class TestDetectPackageManager:
    def test_prefers_apt(self):
        with patch("shutil.which", side_effect=lambda c: "/usr/bin/" + c if c == "apt-get" else None):
            assert deps.detect_package_manager() == "apt"

    def test_dnf(self):
        with patch("shutil.which", side_effect=lambda c: "/usr/bin/dnf" if c == "dnf" else None):
            assert deps.detect_package_manager() == "dnf"

    def test_pacman(self):
        with patch("shutil.which", side_effect=lambda c: "/usr/bin/pacman" if c == "pacman" else None):
            assert deps.detect_package_manager() == "pacman"

    def test_none_when_unknown(self):
        with patch("shutil.which", return_value=None):
            assert deps.detect_package_manager() is None


class TestDiagnose:
    def test_flags_missing_clipboard(self):
        with patch("shutil.which", return_value=None), \
             patch("os.access", return_value=False):
            report = deps.diagnose()
        names = {item["name"] for item in report["missing"]}
        assert "wl-clipboard" in names
        assert "uinput-access" in names

    def test_clipboard_present_not_missing(self):
        with patch("shutil.which", side_effect=lambda c: "/usr/bin/wl-copy" if c == "wl-copy" else None), \
             patch("os.access", return_value=True):
            report = deps.diagnose()
        names = {item["name"] for item in report["missing"]}
        assert "wl-clipboard" not in names
        assert "uinput-access" not in names


class TestInstallPlan:
    def test_apt_plan_includes_clipboard_install_and_udev(self):
        plan = deps.build_plan("apt", missing_names={"wl-clipboard", "uinput-access"})
        joined = "\n".join(plan)
        assert "apt-get install" in joined and "wl-clipboard" in joined
        assert "udev" in joined or "uinput" in joined
        assert "usermod -aG input" in joined

    def test_pacman_plan_uses_pacman(self):
        plan = deps.build_plan("pacman", missing_names={"wl-clipboard"})
        assert any("pacman -S" in line for line in plan)

    def test_empty_plan_when_nothing_missing(self):
        assert deps.build_plan("apt", missing_names=set()) == []

    def test_unknown_manager_has_no_install_lines(self):
        plan = deps.build_plan(None, missing_names={"wl-clipboard"})
        assert all("install" not in line.lower() for line in plan)

    def test_uses_given_escalation_prefix(self):
        plan = deps.build_plan("apt", missing_names={"wl-clipboard"}, escalate="pkexec")
        assert plan and plan[0].startswith("pkexec apt-get install")


class TestEscalation:
    def test_prefers_pkexec_with_display(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda c: "/usr/bin/" + c if c in ("pkexec", "sudo") else None)
        monkeypatch.setenv("DISPLAY", ":0")
        assert deps.detect_escalation() == "pkexec"

    def test_falls_back_to_sudo_without_pkexec(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda c: "/usr/bin/sudo" if c == "sudo" else None)
        assert deps.detect_escalation() == "sudo"

    def test_pkexec_needs_a_display(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda c: "/usr/bin/" + c if c in ("pkexec", "sudo") else None)
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert deps.detect_escalation() == "sudo"

    def test_none_when_neither(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda c: None)
        assert deps.detect_escalation() is None


class TestAccessibilityDiagnosis:
    """The structured Tier 1 needs the AT-SPI stack; diagnose must name it."""

    def test_unreachable_bus_is_flagged_on_linux(self, monkeypatch):
        monkeypatch.setattr(deps.sys, "platform", "linux")
        monkeypatch.setattr(deps, "accessibility_bus_reachable", lambda: False)
        monkeypatch.setattr("shutil.which", lambda c: None)
        monkeypatch.setattr("os.access", lambda *a: True)
        names = {item["name"] for item in deps.diagnose()["missing"]}
        assert "at-spi2-core" in names

    def test_reachable_bus_is_not_flagged(self, monkeypatch):
        monkeypatch.setattr(deps.sys, "platform", "linux")
        monkeypatch.setattr(deps, "accessibility_bus_reachable", lambda: True)
        monkeypatch.setattr("shutil.which", lambda c: None)
        monkeypatch.setattr("os.access", lambda *a: True)
        names = {item["name"] for item in deps.diagnose()["missing"]}
        assert "at-spi2-core" not in names

    def test_not_probed_off_linux(self, monkeypatch):
        # No AT-SPI stack exists to install on Windows or macOS, so it is never
        # listed there however the probe would answer.
        monkeypatch.setattr(deps.sys, "platform", "darwin")
        monkeypatch.setattr(deps, "accessibility_bus_reachable",
                            lambda: (_ for _ in ()).throw(AssertionError("probed")))
        monkeypatch.setattr("shutil.which", lambda c: None)
        monkeypatch.setattr("os.access", lambda *a: True)
        names = {item["name"] for item in deps.diagnose()["missing"]}
        assert "at-spi2-core" not in names

    def test_apt_plan_pulls_bus_core_and_atk_bridge(self):
        plan = deps.build_plan("apt", missing_names={"at-spi2-core"})
        joined = "\n".join(plan)
        assert "at-spi2-core" in joined
        assert "libatk-bridge2.0-0" in joined

    def test_dnf_plan_uses_at_spi2_atk(self):
        plan = deps.build_plan("dnf", missing_names={"at-spi2-core"})
        joined = "\n".join(plan)
        assert "at-spi2-core" in joined and "at-spi2-atk" in joined


class TestPrivilegedSteps:
    def test_apt_steps_are_unprefixed_commands(self):
        steps = deps._privileged_steps("apt", {"wl-clipboard", "uinput-access"})
        assert any(s.startswith("apt-get install -y wl-clipboard") for s in steps)
        assert any("/etc/udev/rules.d" in s for s in steps)
        assert any(s.startswith("usermod -aG input") for s in steps)
        # no escalation baked into the raw steps
        assert all(not s.startswith(("sudo", "pkexec")) for s in steps)


class TestGnomeShellExtension:
    """install-deps puts the GNOME Shell extension in place for the desktop user."""

    @pytest.fixture
    def gnome(self, monkeypatch):
        monkeypatch.setattr(deps.sys, "platform", "linux")
        monkeypatch.setattr(deps.gnome_shell, "session_is_gnome", lambda: True)
        monkeypatch.setattr(deps.os, "geteuid", lambda: 1000, raising=False)
        monkeypatch.setattr(
            "shutil.which", lambda c: "/usr/bin/gnome-extensions" if c == "gnome-extensions" else None
        )
        monkeypatch.setattr("os.access", lambda *a: True)
        monkeypatch.setattr(deps, "accessibility_bus_reachable", lambda: True)
        monkeypatch.setattr(deps, "_launcher_present", lambda: True)
        monkeypatch.setattr(deps, "_clipboard_present", lambda: True)

    def _state(self, monkeypatch, installed, enabled, running):
        monkeypatch.setattr(deps, "_extension_installed", lambda: installed)
        monkeypatch.setattr(deps, "_extension_enabled", lambda: enabled)
        monkeypatch.setattr(deps.gnome_shell, "extension_available", lambda: running)

    def test_off_gnome_it_does_not_apply(self, gnome, monkeypatch):
        monkeypatch.setattr(deps.gnome_shell, "session_is_gnome", lambda: False)
        assert deps.gnome_extension_status() is None
        names = {item["name"] for item in deps.diagnose()["missing"]}
        assert deps.GNOME_EXTENSION not in names

    def test_as_root_it_is_skipped_with_a_note(self, gnome, monkeypatch):
        monkeypatch.setattr(deps.os, "geteuid", lambda: 0, raising=False)
        report = deps.diagnose()
        assert deps.GNOME_EXTENSION not in {i["name"] for i in report["missing"]}
        assert any("not as root" in note for note in report["notes"])

    def test_missing_when_not_installed(self, gnome, monkeypatch):
        self._state(monkeypatch, installed=False, enabled=False, running=False)
        (item,) = deps.diagnose()["missing"]
        assert item["name"] == deps.GNOME_EXTENSION
        assert "not installed" in item["reason"]

    def test_missing_when_installed_but_not_enabled(self, gnome, monkeypatch):
        self._state(monkeypatch, installed=True, enabled=False, running=False)
        (item,) = deps.diagnose()["missing"]
        assert "not enabled" in item["reason"]

    def test_installed_and_enabled_but_not_loaded_asks_for_a_relogin(self, gnome, monkeypatch):
        self._state(monkeypatch, installed=True, enabled=True, running=False)
        report = deps.diagnose()
        assert report["missing"] == []
        assert any("log out and back in" in note.lower() for note in report["notes"])

    def test_running_needs_nothing(self, gnome, monkeypatch):
        self._state(monkeypatch, installed=True, enabled=True, running=True)
        report = deps.diagnose()
        assert report["missing"] == [] and report["notes"] == []

    def test_plan_runs_the_extension_steps_unprivileged_after_root(self):
        plan = deps.build_plan(
            "apt", {"wl-clipboard", deps.GNOME_EXTENSION}, escalate="pkexec"
        )
        assert plan[0].startswith("pkexec apt-get install")
        assert plan[-2].startswith("gnome-extensions install --force ")
        assert plan[-1] == "gnome-extensions enable vadgr-cua@vadgr.com"

    def test_pack_matches_the_gnome_extensions_layout(self, tmp_path):
        import zipfile

        bundle = deps.pack_extension(tmp_path)
        assert bundle.name == "vadgr-cua@vadgr.com.shell-extension.zip"
        with zipfile.ZipFile(bundle) as archive:
            assert sorted(archive.namelist()) == sorted(deps.gnome_shell.EXTENSION_FILES)
            source = deps.gnome_shell.extension_dir()
            for name in archive.namelist():
                assert archive.read(name) == (source / name).read_bytes()

    def test_installed_means_the_same_bytes(self, tmp_path, monkeypatch):
        import shutil as real_shutil

        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert deps._extension_installed() is False
        target = tmp_path / "gnome-shell" / "extensions" / "vadgr-cua@vadgr.com"
        real_shutil.copytree(deps.gnome_shell.extension_dir(), target)
        assert deps._extension_installed() is True
        (target / "extension.js").write_text("// an older build\n")
        assert deps._extension_installed() is False

    def test_gsettings_lists_are_parsed(self, monkeypatch):
        for printed, parsed in (("@as []", []), ("['a@b', 'c@d']", ["a@b", "c@d"])):
            monkeypatch.setattr(deps, "_gsettings_get", lambda key, p=printed: p)
            assert deps._gsettings_strv("enabled-extensions") == parsed
        monkeypatch.setattr(deps, "_gsettings_get", lambda key: "not a list")
        assert deps._gsettings_strv("enabled-extensions") is None


class _Runs:
    """A scripted subprocess.run: answers by argv prefix and records each call."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, argv, **kwargs):
        from types import SimpleNamespace

        self.calls.append(list(argv))
        for prefix, (rc, out) in self.answers.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return SimpleNamespace(returncode=rc, stdout=out, stderr="")
        raise AssertionError(f"unexpected command {argv}")


class TestInstallGnomeExtension:
    def test_fresh_install_enables_through_the_setting(self, monkeypatch, capsys):
        # The running shell has not loaded a fresh install, so the enable
        # command reports it missing; the setting is what the next login reads.
        runs = _Runs({
            ("gnome-extensions", "install"): (0, ""),
            ("gnome-extensions", "enable"): (2, ""),
            ("gsettings", "get", "org.gnome.shell", "enabled-extensions"): (0, "['other@x']"),
            ("gsettings", "get", "org.gnome.shell", "disabled-extensions"):
                (0, "['vadgr-cua@vadgr.com']"),
            ("gsettings", "get", "org.gnome.shell", "disable-user-extensions"): (0, "false"),
            ("gsettings", "set"): (0, ""),
        })
        monkeypatch.setattr(deps.subprocess, "run", runs)
        assert deps.install_gnome_extension() == 0
        install = next(c for c in runs.calls if c[:2] == ["gnome-extensions", "install"])
        assert install[2] == "--force" and install[3].endswith(".shell-extension.zip")
        sets = [c for c in runs.calls if c[:2] == ["gsettings", "set"]]
        assert sets == [
            ["gsettings", "set", "org.gnome.shell", "enabled-extensions",
             '["other@x", "vadgr-cua@vadgr.com"]'],
            ["gsettings", "set", "org.gnome.shell", "disabled-extensions", "[]"],
        ]
        assert deps.RELOGIN_NOTE in capsys.readouterr().out

    def test_an_enable_the_shell_accepts_needs_no_setting(self, monkeypatch):
        runs = _Runs({
            ("gnome-extensions",): (0, ""),
            ("gsettings", "get", "org.gnome.shell", "disable-user-extensions"): (0, "false"),
        })
        monkeypatch.setattr(deps.subprocess, "run", runs)
        assert deps.install_gnome_extension() == 0
        assert not any(c[:2] == ["gsettings", "set"] for c in runs.calls)

    def test_a_failed_install_stops_before_enabling(self, monkeypatch):
        runs = _Runs({("gnome-extensions", "install"): (1, "")})
        monkeypatch.setattr(deps.subprocess, "run", runs)
        assert deps.install_gnome_extension() == 1
        assert len(runs.calls) == 1

    def test_switched_off_user_extensions_are_reported(self, monkeypatch, capsys):
        runs = _Runs({
            ("gnome-extensions",): (0, ""),
            ("gsettings", "get", "org.gnome.shell", "disable-user-extensions"): (0, "true"),
        })
        monkeypatch.setattr(deps.subprocess, "run", runs)
        assert deps.install_gnome_extension() == 0
        assert "disable-user-extensions false" in capsys.readouterr().out

    def test_only_the_extension_missing_runs_no_root_step(self, monkeypatch):
        report = {"package_manager": "apt", "notes": [], "missing": [
            {"name": deps.GNOME_EXTENSION, "reason": "not installed", "fix": "install"},
        ]}
        monkeypatch.setattr(deps, "diagnose", lambda: report)
        monkeypatch.setattr(deps, "detect_escalation", lambda: "pkexec")
        monkeypatch.setattr(deps.subprocess, "run", _Runs({}))  # any root run fails
        installed = []
        monkeypatch.setattr(deps, "install_gnome_extension", lambda: installed.append(1) or 0)
        assert deps.install_deps(assume_yes=True, dry_run=False) == 0
        assert installed == [1]

    def test_dry_run_prints_the_steps_and_the_relogin(self, monkeypatch, capsys):
        report = {"package_manager": "apt", "notes": [], "missing": [
            {"name": deps.GNOME_EXTENSION, "reason": "not installed", "fix": "install"},
        ]}
        monkeypatch.setattr(deps, "diagnose", lambda: report)
        monkeypatch.setattr(deps, "detect_escalation", lambda: "pkexec")
        assert deps.install_deps() == 0
        out = capsys.readouterr().out
        assert "gnome-extensions enable vadgr-cua@vadgr.com" in out
        assert deps.RELOGIN_NOTE in out
        assert "dry run" in out
