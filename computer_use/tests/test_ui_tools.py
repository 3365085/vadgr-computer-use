# Copyright 2026 Victor Santiago Montaño Diaz
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""The structured-tier tools, against a fake backend.

None of this proves the AT-SPI tier works - that is the runbook's job. It proves
the plumbing the tier is thin over: the ref lifecycle including element_gone, the
empty-match-is-ok rule, the named errors, ui_wait's timeout, and the honest
degrade when no backend resolves.
"""

from __future__ import annotations

from computer_use.tools.ui import tools as ui
from computer_use.tools.ui.backend import (
    ELEMENT_GONE,
    NO_TREE,
    UNSUPPORTED_ACTION,
    Bounds,
    Element,
    StructuredError,
)

_EL = Element(
    ref="atspi:3f1c/12/4/1",
    role="push button",
    name="Save",
    bounds=Bounds(812, 604, 74, 30),
    states=("enabled", "visible", "showing"),
)


class FakeBackend:
    """A StructuredBackend stand-in with scripted results and failures."""

    def __init__(self, *, elements=(), act=None, act_error=None, wait=None,
                 windows=None, nearest=()):
        self._elements = list(elements)
        self._act = act or {"state": _EL.as_dict()}
        self._act_error = act_error
        self._wait = wait
        self._windows = windows if windows is not None else []
        self._nearest = list(nearest)
        self.last_app = None  # records the app arg the tools threaded through
        self.acted = []  # (ref, action, text) per act, in order
        self.finds = []  # (role, name, app) per find, in order
        self.nearest_calls = []

    def tree(self, depth, app=""):
        self.last_app = app
        return {"root": _EL.as_dict(), "depth": depth}

    def find(self, role, name, app=""):
        self.last_app = app
        self.finds.append((role, name, app))
        # The real backend matches a name as a case-insensitive substring.
        return [
            e
            for e in self._elements
            if (not role or e.role == role)
            and (not name or name.lower() in e.name.lower())
        ]

    def nearest(self, role, name, app="", limit=5):
        self.nearest_calls.append((role, name, app, limit))
        return self._nearest[:limit]

    def act(self, ref, action, text):
        self.acted.append((ref, action, text))
        if self._act_error is not None:
            raise self._act_error
        return self._act

    def wait(self, role, name, timeout_ms, app=""):
        self.last_app = app
        return self._wait

    def windows(self):
        return {"windows": self._windows}

    def capability(self):
        return {"bus_reachable": True, "backend": "fake"}


def _use(monkeypatch, backend):
    monkeypatch.setattr(ui, "resolve_backend", lambda: backend)


class TestDegradesHonestlyWhenUnavailable:
    """No backend must be at_spi_unavailable-with-a-remedy, never a crash."""

    def test_every_tool_reports_unavailable(self, monkeypatch):
        monkeypatch.setattr(ui, "resolve_backend", lambda: None)
        results = (ui.ui_tree(), ui.ui_find(), ui.ui_act("r", "click"),
                   ui.ui_wait(), ui.ui_windows())
        for result in results:
            assert result["ok"] is False
            assert result["error"] == "at_spi_unavailable"
            assert result["remedy"]  # a non-empty one-line remedy


class TestAppTargeting:
    """The app arg is threaded through, and default stays the active window."""

    def test_default_app_is_empty_backward_compatible(self, monkeypatch):
        backend = FakeBackend(elements=[_EL])
        _use(monkeypatch, backend)
        ui.ui_find(role="push button")
        assert backend.last_app == ""

    def test_find_threads_the_app_through(self, monkeypatch):
        backend = FakeBackend(elements=[_EL])
        _use(monkeypatch, backend)
        ui.ui_find(name="Save", app="Calculator")
        assert backend.last_app == "Calculator"

    def test_tree_and_wait_thread_the_app_through(self, monkeypatch):
        backend = FakeBackend(wait=_EL)
        _use(monkeypatch, backend)
        ui.ui_tree(app="*")
        assert backend.last_app == "*"
        ui.ui_wait(app="Files")
        assert backend.last_app == "Files"

    def test_ui_windows_lists_the_windows(self, monkeypatch):
        wins = [{"app_name": "gnome-calculator", "title": "Calculator",
                 "active": False, "ref": "atspi:9"}]
        _use(monkeypatch, FakeBackend(windows=wins))
        result = ui.ui_windows()
        assert result["ok"] is True
        assert result["windows"] == wins


class TestReadContract:
    def test_empty_match_is_ok_not_error(self, monkeypatch):
        _use(monkeypatch, FakeBackend(elements=[]))
        result = ui.ui_find(role="push button")
        assert result["ok"] is True
        assert result["elements"] == []

    def test_find_returns_ref_bounds_and_states(self, monkeypatch):
        _use(monkeypatch, FakeBackend(elements=[_EL]))
        result = ui.ui_find(name="Save")
        assert result["ok"] is True
        (el,) = result["elements"]
        assert el["ref"] == "atspi:3f1c/12/4/1"
        assert el["bounds"] == {"x": 812, "y": 604, "w": 74, "h": 30}
        assert "enabled" in el["states"]

    def test_tree_is_depth_capped_by_the_argument(self, monkeypatch):
        _use(monkeypatch, FakeBackend())
        assert ui.ui_tree(depth=3)["depth"] == 3

    def test_no_tree_is_a_named_error(self, monkeypatch):
        backend = FakeBackend()
        backend.tree = lambda depth, app="": (_ for _ in ()).throw(
            StructuredError(NO_TREE, "gedit exposes no tree")
        )
        _use(monkeypatch, backend)
        result = ui.ui_tree()
        assert result["ok"] is False
        assert result["error"] == NO_TREE


class TestActContract:
    def test_stale_ref_is_element_gone_and_does_not_act(self, monkeypatch):
        _use(monkeypatch, FakeBackend(act_error=StructuredError(ELEMENT_GONE)))
        result = ui.ui_act("atspi:dead/9", "click")
        assert result["ok"] is False
        assert result["error"] == ELEMENT_GONE

    def test_unsupported_action_names_the_error(self, monkeypatch):
        err = StructuredError(UNSUPPORTED_ACTION, supported=["click", "focus"])
        _use(monkeypatch, FakeBackend(act_error=err))
        result = ui.ui_act("atspi:1/2", "toggle")
        assert result["error"] == UNSUPPORTED_ACTION
        assert result["supported"] == ["click", "focus"]

    def test_act_returns_the_reread_state(self, monkeypatch):
        _use(monkeypatch, FakeBackend(act={"state": {"checked": True}}))
        result = ui.ui_act("atspi:1/2", "toggle")
        assert result["ok"] is True
        assert result["state"] == {"checked": True}


class TestWaitContract:
    def test_timeout_reports_elapsed(self, monkeypatch):
        _use(monkeypatch, FakeBackend(wait=None))
        result = ui.ui_wait(role="dialog", timeout_ms=250)
        assert result["ok"] is False
        assert result["error"] == "timeout"
        assert result["elapsed_ms"] == 250

    def test_found_returns_the_element(self, monkeypatch):
        _use(monkeypatch, FakeBackend(wait=_EL))
        result = ui.ui_wait(name="Save")
        assert result["ok"] is True
        assert result["element"]["name"] == "Save"

class TestTheUnavailableRemedyFitsThePlatform:
    """The structured native tier is AT-SPI, and only Linux has it.

    Sending a macOS or Windows caller after `at-spi2-core` points them at a
    package their platform cannot have, so the tier reads as a broken install
    rather than one that is not offered there.
    """

    def test_linux_is_told_how_to_install_the_bus(self):
        from computer_use.tools.ui.backend import unavailable_remedy

        remedy = unavailable_remedy("linux")
        assert "install-deps" in remedy
        assert "at-spi2-core" in remedy

    def test_other_platforms_are_not_sent_after_a_linux_package(self):
        from computer_use.tools.ui.backend import unavailable_remedy

        for platform in ("darwin", "win32"):
            remedy = unavailable_remedy(platform)
            assert "at-spi2-core" not in remedy, platform
            assert "install-deps" not in remedy, platform
            assert "Linux only" in remedy, platform


def _button(ref, name, window="Doc"):
    return Element(ref=ref, role="button", name=name, bounds=Bounds(0, 0, 10, 10),
                   states=("showing",), window=window)


class TestActByName:
    """ui_act by name and role acts on exactly one match, never on a guess."""

    def test_one_match_is_acted_on_and_its_ref_returned(self, monkeypatch):
        backend = FakeBackend(elements=[_button("atspi:1", "Save")])
        _use(monkeypatch, backend)
        result = ui.ui_act(action="click", name="Save", role="button", app="gedit")
        assert result["ok"] is True
        assert result["ref"] == "atspi:1"
        assert backend.acted == [("atspi:1", "click", "")]
        assert backend.finds == [("button", "Save", "gedit")]

    def test_no_match_is_not_found_with_the_nearest_candidates(self, monkeypatch):
        near = [_button("atspi:7", "Save changes"), _button("atspi:8", "Saved")]
        backend = FakeBackend(elements=[_button("atspi:1", "Open")], nearest=near)
        _use(monkeypatch, backend)
        result = ui.ui_act(action="click", name="Save", role="button")
        assert result["ok"] is False
        assert result["error"] == "not_found"
        assert [c["ref"] for c in result["candidates"]] == ["atspi:7", "atspi:8"]
        assert result["candidates"][0] == {
            "ref": "atspi:7", "role": "button", "name": "Save changes",
            "window": "Doc", "bounds": {"x": 0, "y": 0, "w": 10, "h": 10},
        }
        assert backend.nearest_calls == [("button", "Save", "", 5)]
        assert backend.acted == []

    def test_several_matches_are_ambiguous_with_at_most_five_refs(self, monkeypatch):
        many = [_button(f"atspi:{i}", "Save", window=f"Doc {i}") for i in range(7)]
        backend = FakeBackend(elements=many)
        _use(monkeypatch, backend)
        result = ui.ui_act(action="click", name="Save")
        assert result["error"] == "ambiguous"
        assert result["count"] == 7
        assert [c["ref"] for c in result["candidates"]] == [f"atspi:{i}" for i in range(5)]
        assert [c["window"] for c in result["candidates"]][:2] == ["Doc 0", "Doc 1"]
        assert backend.acted == []

    def test_a_whole_name_wins_over_partial_matches(self, monkeypatch):
        backend = FakeBackend(elements=[
            _button("atspi:1", "Save As..."), _button("atspi:2", "save"),
            _button("atspi:3", "Save All"),
        ])
        _use(monkeypatch, backend)
        result = ui.ui_act(action="click", name="Save")
        assert result["ok"] is True and result["ref"] == "atspi:2"

    def test_two_whole_name_matches_are_still_ambiguous(self, monkeypatch):
        backend = FakeBackend(elements=[
            _button("atspi:1", "Save"), _button("atspi:2", "Save"),
            _button("atspi:3", "Save As..."),
        ])
        _use(monkeypatch, backend)
        result = ui.ui_act(action="click", name="Save")
        assert result["error"] == "ambiguous"
        assert result["count"] == 2

    def test_ref_wins_over_name(self, monkeypatch):
        backend = FakeBackend(elements=[_button("atspi:1", "Save")])
        _use(monkeypatch, backend)
        result = ui.ui_act("atspi:9", "toggle", name="Save", role="button")
        assert result["ok"] is True
        assert "ref" not in result  # the ref path keeps its old shape
        assert backend.acted == [("atspi:9", "toggle", "")]
        assert backend.finds == []

    def test_a_ref_call_is_unchanged(self, monkeypatch):
        backend = FakeBackend()
        _use(monkeypatch, backend)
        result = ui.ui_act("atspi:1/2", "set_text", "hello")
        assert result["ok"] is True
        assert backend.acted == [("atspi:1/2", "set_text", "hello")]

    def test_role_alone_picks_a_single_control(self, monkeypatch):
        backend = FakeBackend(elements=[_button("atspi:4", "OK")])
        _use(monkeypatch, backend)
        assert ui.ui_act(action="click", role="button")["ref"] == "atspi:4"

    def test_no_ref_no_name_no_role_is_a_named_error(self, monkeypatch):
        backend = FakeBackend(elements=[_button("atspi:1", "Save")])
        _use(monkeypatch, backend)
        result = ui.ui_act(action="click")
        assert result["error"] == "no_target"
        assert backend.finds == [] and backend.acted == []

    def test_a_vanished_match_is_element_gone_not_a_retry(self, monkeypatch):
        backend = FakeBackend(elements=[_button("atspi:1", "Save")],
                              act_error=StructuredError(ELEMENT_GONE))
        _use(monkeypatch, backend)
        assert ui.ui_act(action="click", name="Save")["error"] == ELEMENT_GONE
        assert len(backend.acted) == 1
