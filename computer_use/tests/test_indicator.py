# Copyright 2026 Victor Santiago Montaño Diaz
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""What each desktop tool tells the indicator: the words, the point, the box."""

from __future__ import annotations

from computer_use.indicator import (
    INDICATED_TOOLS,
    USER_STOPPED,
    Cue,
    ElementMemory,
    cue_for,
    stopped_result,
)


def _double(x, y):
    """A display-to-screen mapping with a 2x downscale, like a 2560 px screen."""
    return x * 2, y * 2


def _found(ref="atspi:1", name="Save", trust=None, bounds=(100, 200, 80, 30)):
    element = {
        "ref": ref,
        "role": "button",
        "name": name,
        "bounds": dict(zip("xywh", bounds)) if bounds else None,
        "states": ["showing"],
    }
    if trust is not None:
        element["coordinate_trust"] = trust
    return element


class TestWhichToolsAreCued:
    def test_the_desktop_tools_and_only_those(self):
        assert {"ui_act", "ui_find", "ui_tree", "ui_windows", "ui_wait"} <= INDICATED_TOOLS
        assert {"screenshot", "click", "key_press", "type_text", "drag"} <= INDICATED_TOOLS
        assert "app_open" in INDICATED_TOOLS
        for quiet in ("browser", "tabs", "fs", "shell", "time", "get_screen_size",
                      "get_platform_info", "apps"):
            assert quiet not in INDICATED_TOOLS, quiet


class TestPixelCues:
    def test_a_click_rings_its_target_in_screen_pixels(self):
        assert cue_for("click", {"x": 10, "y": 20}, _double) == Cue("click", "Clicking", 20, 40)

    def test_every_click_kind_is_ringed(self):
        assert cue_for("double_click", {"x": 1, "y": 2}, _double).x == 2
        assert cue_for("right_click", {"x": 1, "y": 2}, _double).text == "Right-clicking"

    def test_a_drag_rings_where_it_starts(self):
        cue = cue_for("drag", {"start_x": 5, "start_y": 6, "end_x": 50, "end_y": 60}, _double)
        assert (cue.text, cue.x, cue.y) == ("Dragging", 10, 12)

    def test_a_move_or_scroll_draws_no_ring(self):
        for tool in ("move_mouse", "scroll"):
            cue = cue_for(tool, {"x": 1, "y": 2, "amount": 3}, _double)
            assert (cue.x, cue.y) == (-1, -1), tool

    def test_typing_never_shows_what_is_typed(self):
        cue = cue_for("type_text", {"text": "hunter2"}, _double)
        assert cue.text == "Typing"
        assert "hunter2" not in repr(cue)

    def test_a_screenshot_says_so(self):
        assert cue_for("screenshot", {"format": "jpeg"}, _double).text == "Taking a screenshot"


class TestOwnEscape:
    def test_an_escape_key_press_is_marked_as_vadgrs_own(self):
        for keys in ("escape", "Esc", "shift+escape", " ESCAPE "):
            assert cue_for("key_press", {"keys": keys}, _double).own_escape, keys

    def test_other_keys_are_not(self):
        for keys in ("ctrl+s", "enter", "escapee", "alt+f4"):
            assert not cue_for("key_press", {"keys": keys}, _double).own_escape, keys

    def test_the_pill_names_the_keys(self):
        assert cue_for("key_press", {"keys": "ctrl+s"}, _double).text == "Pressing ctrl+s"


class TestStructuredCues:
    def test_find_names_what_it_looks_for(self):
        assert cue_for("ui_find", {"name": "Save", "role": ""}, _double).text == 'Finding "Save"'
        assert cue_for("ui_find", {"name": "", "role": "button"}, _double).text == "Finding button"
        assert cue_for("ui_find", {"name": "", "role": ""}, _double).text == "Finding controls"

    def test_wait_and_tree_and_windows(self):
        assert cue_for("ui_wait", {"name": "Done", "role": ""}, _double).text == 'Waiting for "Done"'
        assert cue_for("ui_tree", {"app": ""}, _double).text == "Reading the window"
        assert cue_for("ui_tree", {"app": "Files"}, _double).text == 'Reading "Files"'
        assert cue_for("ui_tree", {"app": "*"}, _double).text == "Reading every window"
        assert cue_for("ui_windows", {}, _double).text == "Listing windows"

    def test_app_open_names_the_app(self):
        assert cue_for("app_open", {"target": "Calculator"}, _double).text == 'Opening "Calculator"'

    def test_a_long_name_is_cut(self):
        text = cue_for("ui_find", {"name": "x" * 200, "role": ""}, _double).text
        assert len(text) < 60 and text.endswith('..."')

    def test_act_names_a_remembered_element(self):
        memory = ElementMemory()
        memory.observe("ui_find", {"ok": True, "elements": [_found()]})
        cue = cue_for("ui_act", {"ref": "atspi:1", "action": "click"}, _double, memory)
        assert cue.text == 'Clicking "Save"'

    def test_act_outlines_only_trusted_bounds(self):
        memory = ElementMemory()
        memory.observe("ui_find", {"ok": True, "elements": [
            _found("atspi:1", trust="real"),
            _found("atspi:2", trust="none"),
        ]})
        trusted = cue_for("ui_act", {"ref": "atspi:1", "action": "click"}, _double, memory)
        assert (trusted.x, trusted.y, trusted.width, trusted.height) == (100, 200, 80, 30)
        untrusted = cue_for("ui_act", {"ref": "atspi:2", "action": "click"}, _double, memory)
        assert (untrusted.x, untrusted.width) == (-1, 0)

    def test_act_on_an_unknown_ref_still_says_what_it_does(self):
        cue = cue_for("ui_act", {"ref": "atspi:9", "action": "set_text"}, _double, ElementMemory())
        assert cue.text == "Typing into"
        assert cue_for("ui_act", {"ref": "r", "action": "toggle"}, _double).text == "Toggling"


class TestElementMemory:
    def test_errors_teach_nothing(self):
        memory = ElementMemory()
        memory.observe("ui_find", {"ok": False, "error": "no_tree"})
        memory.observe("ui_find", "not a dict")
        assert memory.get("atspi:1") is None

    def test_wait_and_tree_results_are_learned(self):
        memory = ElementMemory()
        memory.observe("ui_wait", {"ok": True, "element": _found("atspi:5", "Done")})
        memory.observe("ui_tree", {"ok": True, "root": {
            **_found("atspi:6", "Window"),
            "children": [_found("atspi:7", "OK")],
        }})
        assert memory.get("atspi:5")[0] == "Done"
        assert memory.get("atspi:7")[0] == "OK"

    def test_a_later_untrusted_read_keeps_the_trusted_box(self):
        memory = ElementMemory()
        memory.observe("ui_find", {"ok": True, "elements": [_found(trust="real")]})
        memory.observe("ui_tree", {"ok": True, "root": _found()})
        assert memory.get("atspi:1")[1] == (100, 200, 80, 30)

    def test_it_stays_bounded(self):
        memory = ElementMemory(limit=3)
        memory.observe("ui_find", {"ok": True, "elements": [
            _found(f"atspi:{i}", f"n{i}") for i in range(5)
        ]})
        assert memory.get("atspi:0") is None
        assert memory.get("atspi:4")[0] == "n4"


def test_the_stopped_result_tells_the_model_to_wait():
    result = stopped_result()
    assert result["ok"] is False
    assert result["error"] == USER_STOPPED == "user_stopped"
    assert "wait" in result["message"]
