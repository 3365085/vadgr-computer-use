# Copyright 2026 Victor Santiago Montaño Diaz
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""The AT-SPI backend's logic, against an in-memory fake client.

This proves what the runbook cannot cheaply reach on every push: the ref
lifecycle including element_gone, the verb-to-interface mapping and its
unsupported_action listing, the state-bit decoding, the "no active window is
no_tree" rule, and the coordinate_trust that separates X11 from Wayland. The
live bus is the runbook's job; this is the plumbing the tier is thin over, and a
fake client lets every branch be driven deterministically.
"""

from __future__ import annotations

import pytest

from computer_use.platform.resolver.session import SessionContext
from computer_use.tools.ui import atspi
from computer_use.tools.ui.atspi import AtspiBackend, ElementGone, decode_states
from computer_use.tools.ui.backend import StructuredError

_ACCESSIBLE = "org.a11y.atspi.Accessible"
_COMPONENT = "org.a11y.atspi.Component"
_ACTION = "org.a11y.atspi.Action"
_EDITABLE_TEXT = "org.a11y.atspi.EditableText"


class FakeNode:
    def __init__(
        self, role, name, *, states=(), extents=(0, 0, 0, 0),
        interfaces=(), actions=(), children=(), gone=False, toolkit=None,
        pid=0, text=None,
    ):
        self.role = role
        self.name = name
        self.states = list(states)
        self.extents = extents
        self.interfaces = list(interfaces)
        self.actions = list(actions)
        self.children = list(children)
        self.gone = gone
        self.toolkit = toolkit
        self.pid = pid
        self.text = text
        self.did = []  # (action_index) log
        # Models the toolkit's async state update: a queued change becomes
        # visible only after `reveal` more states() reads, so a re-read done too
        # eagerly sees the stale value (the D1 defect).
        self._pending = None
        self._reveal = 0


class FakeClient:
    """An in-memory accessibility tree keyed by opaque node ids."""

    def __init__(self, nodes: dict, root=("reg", "/root"), *,
                 reachable=True, enabled=False, act_lag=0, stale_states=None,
                 sr_children=None):
        self._nodes = nodes
        self._root = root
        self._reachable = reachable
        self._enabled = enabled
        self._act_lag = act_lag
        # Models the warm cache: while an entry is present, states() serves it
        # instead of the live node, exactly like a find-time snapshot would.
        # invalidate() clears the app's entries, which is what acting must do.
        self._stale_states = dict(stale_states or {})
        self.states_reads = 0  # how many times states() was asked, for latency tests
        # A spy on the screen-reader write the tier removed. The backend must
        # never call set_screen_reader (it would start a screen reader on GNOME),
        # so an empty sr_log is the proof. sr_children models the content a thin
        # toolkit would expose only if the flag were raised: it stays hidden,
        # which is how the test shows the window really reads tree-only.
        self.sr_log: list[bool] = []  # every set_screen_reader call, in order
        self._sr_children = dict(sr_children or {})
        self._sr_ever = False

    def _n(self, node) -> FakeNode:
        fake = self._nodes.get(node)
        if fake is None or fake.gone:
            raise ElementGone(str(node))
        return fake

    def reachable(self):
        return self._reachable

    def is_enabled(self):
        return self._enabled

    def root(self):
        return self._root

    def warm_caches(self, bus_names):
        # The fake tree already is the in-memory snapshot, so warming is a no-op;
        # this keeps it a full _Client so the backend can call it unconditionally.
        pass

    def invalidate(self, node):
        bus_name = node[0]
        self._stale_states = {
            n: s for n, s in self._stale_states.items() if n[0] != bus_name
        }

    def children(self, node):
        kids = list(self._n(node).children)
        if self._sr_ever:
            kids += self._sr_children.get(node, [])
        return kids

    def set_screen_reader(self, enabled):
        # A spy only. The backend must never call this; recording a call is how
        # a test proves no screen-reader write happened on a thin read. Revealing
        # sr_children on a True write models the toolkit the removed pulse chased,
        # so a regression that re-added the write would light up both assertions.
        self.sr_log.append(enabled)
        if enabled:
            self._sr_ever = True
        return True

    def role_name(self, node):
        return self._n(node).role

    def name(self, node):
        return self._n(node).name

    def states(self, node):
        self.states_reads += 1
        if node in self._stale_states:
            return tuple(self._stale_states[node])
        fake = self._n(node)
        # Reveal a queued change once its countdown elapses, so an eager re-read
        # sees the pre-action value and a settling one sees the post-action value.
        if fake._reveal > 0:
            fake._reveal -= 1
            if fake._reveal == 0 and fake._pending is not None:
                fake.states.append(fake._pending)
                fake._pending = None
        return tuple(fake.states)

    def extents(self, node):
        ext = self._n(node).extents
        return tuple(ext) if ext is not None else None

    def text(self, node):
        return self._n(node).text

    def interfaces(self, node):
        return tuple(self._n(node).interfaces)

    def actions(self, node):
        return list(self._n(node).actions)

    def do_action(self, node, index):
        fake = self._n(node)
        fake.did.append(index)
        # A GTK toggle button gains "pressed" when its click action fires. With a
        # lag configured, the change is queued and only surfaces after that many
        # states() reads, modelling the toolkit's asynchronous update.
        if fake.role == "toggle button" and "pressed" not in fake.states:
            if self._act_lag > 0:
                fake._pending = "pressed"
                fake._reveal = self._act_lag
            else:
                fake.states.append("pressed")
        return True

    def set_text(self, node, text):
        self._n(node).text = text
        return True

    def grab_focus(self, node):
        fake = self._n(node)
        if "focused" not in fake.states:
            fake.states.append("focused")
        return True

    def toolkits(self):
        seen = []
        for fake in self._nodes.values():
            if fake.toolkit and fake.toolkit not in seen:
                seen.append(fake.toolkit)
        return tuple(seen)

    def pid(self, node):
        return self._n(node).pid


def _session(server="wayland"):
    return SessionContext(server=server, compositor="gnome",
                          has_uinput=False, libs=frozenset())


def _tree_with_button(**button_kwargs):
    """A desktop -> app -> active window -> button, with the button tunable.

    The window and button are on screen (showing/visible) so the showing-prune
    keeps them; a test that wants an off-screen element sets its own states.
    """
    defaults = {
        "role": "push button", "name": "Save", "extents": (0, 0, 74, 30),
        "interfaces": [_ACCESSIBLE, _COMPONENT, _ACTION], "actions": ["Click"],
    }
    defaults.update(button_kwargs)
    bstates = list(defaults.get("states", ()))
    for s in ("showing", "visible"):
        if s not in bstates:
            bstates.append(s)
    defaults["states"] = bstates
    button = FakeNode(**defaults)
    nodes = {
        ("reg", "/root"): FakeNode("desktop frame", "main", children=[("app", "/a")]),
        ("app", "/a"): FakeNode("application", "gedit", toolkit="GTK",
                                children=[("win", "/w")]),
        ("win", "/w"): FakeNode("frame", "Doc - gedit",
                                states=["active", "showing", "visible"],
                                extents=(0, 0, 800, 600),
                                children=[("btn", "/b")]),
        ("btn", "/b"): button,
    }
    return nodes


def _backend(nodes, server="wayland", **client_kwargs):
    return AtspiBackend(FakeClient(nodes, **client_kwargs), _session(server))


class TestStateDecoding:
    def test_pressed_and_has_popup_bits_decode(self):
        # The exact words a real GTK4 "Main Menu" toggle returned after a click:
        # word 0 has focusable/sensitive/showing/visible/pressed, word 1 bit 10
        # is has_popup (state index 42).
        states = decode_states(1125124096, 1024)
        assert "pressed" in states
        assert "has_popup" in states

    def test_no_bits_is_empty(self):
        assert decode_states(0, 0) == ()


class TestCapability:
    def test_coordinate_trust_is_per_window_on_wayland(self):
        # Wayland is not one answer: a Wayland-native window's bounds are
        # window-relative, and an XWayland window's are true screen pixels, so
        # the session-level answer is per_window and each found element carries
        # its own verdict.
        backend = _backend(_tree_with_button(), server="wayland")
        cap = backend.capability()
        assert cap["coordinate_trust"] == "per_window"
        assert cap["backend"] == "atspi"
        assert cap["bus_reachable"] is True

    def test_coordinate_trust_is_real_on_x11(self):
        backend = _backend(_tree_with_button(), server="x11")
        assert backend.capability()["coordinate_trust"] == "real"

    def test_unreachable_bus_reports_no_toolkits(self):
        backend = _backend(_tree_with_button(), reachable=False)
        cap = backend.capability()
        assert cap["bus_reachable"] is False
        assert cap["toolkits_seen"] == []


class TestReadPath:
    def test_tree_returns_active_window_root_with_children(self):
        backend = _backend(_tree_with_button())
        result = backend.tree(depth=6)
        assert result["root"]["role"] == "frame"
        assert result["root"]["children"][0]["name"] == "Save"

    def test_tree_depth_caps_recursion(self):
        backend = _backend(_tree_with_button())
        # depth 0 returns the window itself with no children expanded.
        result = backend.tree(depth=0)
        assert result["root"]["children"] == []

    def test_find_by_role_returns_ref_bounds_states(self):
        backend = _backend(_tree_with_button(states=["enabled"]))
        elements = backend.find("push button", "")
        assert len(elements) == 1
        el = elements[0]
        assert el.ref.startswith("atspi:")
        assert el.bounds.as_dict() == {"x": 0, "y": 0, "w": 74, "h": 30}
        assert "enabled" in el.states

    def test_find_by_name_is_case_insensitive_substring(self):
        backend = _backend(_tree_with_button())
        assert backend.find("", "sav")  # substring, lowered
        assert backend.find("", "nope") == []

    def test_no_active_window_is_no_tree(self):
        nodes = _tree_with_button()
        nodes[("win", "/w")].states = []  # nothing is active
        backend = _backend(nodes)
        with pytest.raises(StructuredError) as exc:
            backend.find("push button", "")
        assert exc.value.code == "no_tree"


class TestFindNamesTheOwningWindow:
    def test_find_results_carry_their_window_title(self):
        # Nine Writer windows hold nine equal paragraphs with equal
        # window-relative bounds; without the owning window's title a caller
        # cannot tell them apart to target one.
        backend = _backend(_tree_with_button())
        el = backend.find("push button", "")[0]
        assert el.window == "Doc - gedit"
        assert el.as_dict()["window"] == "Doc - gedit"

    def test_act_state_omits_the_window_field(self):
        # act's re-read has no frame in hand; the field is absent, not null.
        backend = _backend(_tree_with_button())
        ref = backend.find("push button", "")[0].ref
        state = backend.act(ref, "click", "")["state"]
        assert "window" not in state


class _HealsAfterOneGone(FakeClient):
    """One node answers nothing for exactly one read, then recovers.

    Models the App Center mid-navigation: a replaced node stops answering, the
    walk skips it and loses its subtree, and the very next read finds the tree
    settled.
    """

    def __init__(self, nodes, flaky, **kwargs):
        super().__init__(nodes, **kwargs)
        self._flaky = flaky
        self._tripped = False

    def states(self, node):
        if node == self._flaky and not self._tripped:
            self._tripped = True
            raise ElementGone(str(node))
        return super().states(node)


class TestFindRetriesAnUnsettledTree:
    def _nodes_with_panel(self):
        nodes = _tree_with_button()
        nodes[("win", "/w")].children = [("panel", "/p")]
        nodes[("panel", "/p")] = FakeNode(
            "panel", "", states=["showing", "visible"],
            children=[("btn", "/b")],
        )
        return nodes

    def test_a_subtree_lost_mid_walk_is_found_by_the_bounded_retry(self):
        # First pass: the panel answers nothing, so the button under it is
        # never seen. One retry reads the settled tree and finds it; without
        # the retry this find returns [] and the caller acts on a lie.
        nodes = self._nodes_with_panel()
        client = _HealsAfterOneGone(nodes, flaky=("panel", "/p"))
        backend = AtspiBackend(client, _session())
        elements = backend.find("push button", "")
        assert [e.name for e in elements] == ["Save"]

    def test_a_clean_walk_is_not_walked_twice(self):
        nodes = self._nodes_with_panel()
        client = FakeClient(nodes)
        backend = AtspiBackend(client, _session())
        backend.find("push button", "")
        first_walk_reads = client.states_reads
        client.states_reads = 0
        backend.find("push button", "")
        assert client.states_reads <= first_walk_reads  # no doubled walk


class TestRefLifecycle:
    def test_find_ref_resolves_in_a_later_act(self):
        backend = _backend(_tree_with_button())
        ref = backend.find("push button", "")[0].ref
        result = backend.act(ref, "click", "")
        assert result["state"]["name"] == "Save"

    def test_unknown_ref_is_element_gone(self):
        backend = _backend(_tree_with_button())
        with pytest.raises(StructuredError) as exc:
            backend.act("atspi:deadbeef", "click", "")
        assert exc.value.code == "element_gone"

    def test_ref_to_a_vanished_element_is_element_gone(self):
        nodes = _tree_with_button()
        backend = _backend(nodes)
        ref = backend.find("push button", "")[0].ref
        nodes[("btn", "/b")].gone = True  # the element goes away after the find
        with pytest.raises(StructuredError) as exc:
            backend.act(ref, "click", "")
        assert exc.value.code == "element_gone"

    def test_same_element_reuses_its_ref(self):
        backend = _backend(_tree_with_button())
        first = backend.find("push button", "")[0].ref
        second = backend.find("push button", "")[0].ref
        assert first == second


class TestActVerbs:
    @pytest.mark.parametrize("verb", ["click", "toggle", "expand"])
    def test_live_button_without_action_interface_is_unsupported(self, verb):
        nodes = _tree_with_button(interfaces=[_ACCESSIBLE, _COMPONENT])
        client = FakeClient(nodes)
        calls = []

        def missing_actions(node):
            calls.append(node)
            raise ElementGone("org.freedesktop.DBus.Error.UnknownMethod")

        client.actions = missing_actions
        backend = AtspiBackend(client, _session())
        ref = backend.find("push button", "")[0].ref
        with pytest.raises(StructuredError) as exc:
            backend.act(ref, verb, "")
        assert exc.value.code == "unsupported_action"
        assert exc.value.extra["supported"] == []
        assert calls == []
        assert nodes[("btn", "/b")].did == []
        assert backend.find("push button", "")[0].ref == ref

    def test_click_fires_the_named_action(self):
        nodes = _tree_with_button()
        backend = _backend(nodes)
        ref = backend.find("push button", "")[0].ref
        backend.act(ref, "click", "")
        assert nodes[("btn", "/b")].did == [0]  # index of "Click"

    def test_toggle_rereads_the_new_state(self):
        nodes = _tree_with_button()
        nodes[("btn", "/b")].role = "toggle button"
        backend = _backend(nodes)
        ref = backend.find("toggle button", "")[0].ref
        result = backend.act(ref, "toggle", "")
        assert "pressed" in result["state"]["states"]

    def test_toggle_fires_a_switch_whose_only_action_is_named_toggle(self):
        # GNOME Settings (gnome-control-center) exposes its GtkSwitch rows with
        # a single AT-SPI action named "Toggle" and no "click"; the toggle verb
        # must resolve to it, or the one control the verb exists for is the one
        # it cannot act on.
        nodes = _tree_with_button(actions=["Toggle"])
        nodes[("btn", "/b")].role = "switch"
        backend = _backend(nodes)
        ref = backend.find("switch", "")[0].ref
        backend.act(ref, "toggle", "")
        assert nodes[("btn", "/b")].did == [0]

    def test_click_fires_a_flutter_button_whose_only_action_is_tap(self):
        # Flutter's Linux embedder names a button's activation "Tap" and offers
        # no "click" (observed on the Ubuntu App Center: Tap,
        # DidGainAccessibilityFocus, DidLoseAccessibilityFocus, Focus). The
        # click verb must resolve to it, or every Flutter button is unclickable.
        nodes = _tree_with_button(actions=["Tap", "Focus"])
        backend = _backend(nodes)
        ref = backend.find("push button", "")[0].ref
        backend.act(ref, "click", "")
        assert nodes[("btn", "/b")].did == [0]

    def test_click_fires_a_qt_button_whose_only_action_is_press(self):
        # Qt names a push button's activation "Press".
        nodes = _tree_with_button(actions=["Press"])
        backend = _backend(nodes)
        ref = backend.find("push button", "")[0].ref
        backend.act(ref, "click", "")
        assert nodes[("btn", "/b")].did == [0]

    def test_focus_grabs_when_component_present(self):
        nodes = _tree_with_button()
        backend = _backend(nodes)
        ref = backend.find("push button", "")[0].ref
        result = backend.act(ref, "focus", "")
        assert "focused" in result["state"]["states"]

    def test_set_text_writes_editable_text(self):
        nodes = _tree_with_button(role="entry",
                                  interfaces=[_ACCESSIBLE, _EDITABLE_TEXT],
                                  actions=[])
        nodes[("btn", "/b")].role = "entry"
        backend = _backend(nodes)
        ref = backend.find("entry", "")[0].ref
        backend.act(ref, "set_text", "hello")
        assert nodes[("btn", "/b")].text == "hello"

    def test_unknown_verb_lists_supported(self):
        backend = _backend(_tree_with_button())
        ref = backend.find("push button", "")[0].ref
        with pytest.raises(StructuredError) as exc:
            backend.act(ref, "levitate", "")
        assert exc.value.code == "unsupported_action"
        assert "click" in exc.value.extra["supported"]

    def test_click_on_element_without_click_action_lists_its_actions(self):
        nodes = _tree_with_button(actions=["Expand"])
        backend = _backend(nodes)
        ref = backend.find("push button", "")[0].ref
        with pytest.raises(StructuredError) as exc:
            backend.act(ref, "click", "")
        assert exc.value.code == "unsupported_action"
        assert exc.value.extra["supported"] == ["Expand"]

    def test_set_text_without_editable_interface_is_unsupported(self):
        backend = _backend(_tree_with_button())  # button has no EditableText
        ref = backend.find("push button", "")[0].ref
        with pytest.raises(StructuredError) as exc:
            backend.act(ref, "set_text", "x")
        assert exc.value.code == "unsupported_action"

    def test_focus_without_component_is_unsupported(self):
        nodes = _tree_with_button(interfaces=[_ACCESSIBLE, _ACTION])
        backend = _backend(nodes)
        ref = backend.find("push button", "")[0].ref
        with pytest.raises(StructuredError) as exc:
            backend.act(ref, "focus", "")
        assert exc.value.code == "unsupported_action"


class TestActReReadIsPostAction:
    """D1: the returned state must be the element after acting, not before.

    The toolkit updates state asynchronously, so a re-read done the instant
    DoAction returns sees the stale value. The whole contract of ui_act is that
    the action confirms itself from its own response.
    """

    def test_toggle_state_change_survives_the_toolkit_lag(self):
        nodes = _tree_with_button()
        nodes[("btn", "/b")].role = "toggle button"
        # act_lag=2: the pressed state only appears on the second states() read
        # after DoAction, so an eager single read returns the old state.
        backend = _backend(nodes, act_lag=2)
        ref = backend.find("toggle button", "")[0].ref
        result = backend.act(ref, "toggle", "")
        assert "pressed" in result["state"]["states"]

    def test_act_does_not_read_through_a_stale_cache_snapshot(self):
        # The warm cache is a find-time snapshot. Files (nautilus) exposed the
        # defect live: ui_act(toggle)'s settle poll read the cached pre-action
        # states for the whole window and returned them as the confirmation,
        # while an independent ui_find 24 ms later, which re-warms the cache,
        # saw "pressed". Acting must invalidate the app's snapshot so the
        # pre-action snapshot, the polls and the re-read observe the element.
        nodes = _tree_with_button()
        nodes[("btn", "/b")].role = "toggle button"
        stale = {("btn", "/b"): ("focusable", "showing", "visible")}
        backend = _backend(nodes, stale_states=stale)
        ref = backend.find("toggle button", "")[0].ref
        result = backend.act(ref, "toggle", "")
        assert "pressed" in result["state"]["states"]

    def test_a_plain_click_does_not_settle_poll(self):
        # A digit-style click's effect lands elsewhere (a display), so ui_act must
        # not stall polling the button's own unchanging state. Compared to a
        # settling verb it does far fewer state reads.
        nodes = _tree_with_button()
        client = FakeClient(nodes)
        backend = AtspiBackend(client, _session())
        ref = backend.find("push button", "")[0].ref
        reads_before = client.states_reads
        backend.act(ref, "click", "")
        # find + snapshot + one re-read, not a poll loop of a dozen reads.
        assert client.states_reads - reads_before <= 3


class TestSetTextIsConfirmable:
    """D2: set_text is only trustworthy if the re-read shows the text landed."""

    def test_reread_carries_the_text_that_was_set(self):
        nodes = _tree_with_button(
            role="text box", interfaces=[_ACCESSIBLE, _EDITABLE_TEXT, "org.a11y.atspi.Text"],
            actions=[], text="",
        )
        backend = _backend(nodes)
        ref = backend.find("text box", "")[0].ref
        result = backend.act(ref, "set_text", "hello world")
        assert result["state"]["text"] == "hello world"

    def test_find_surfaces_text_for_text_roles(self):
        nodes = _tree_with_button(role="text box", text="already here",
                                  interfaces=[_ACCESSIBLE, "org.a11y.atspi.Text"])
        backend = _backend(nodes)
        el = backend.find("text box", "")[0]
        assert el.text == "already here"

    def test_a_button_carries_no_text_field(self):
        backend = _backend(_tree_with_button())
        assert backend.find("push button", "")[0].as_dict().get("text") is None


class TestTreeReachesControls:
    """D3: ui_tree must reach the same leaves ui_find reaches.

    GTK stacks anonymous wrapper containers between a window and its controls; a
    tree that stops at the wrappers is no substitute for a screenshot.
    """

    def _deeply_nested(self, wrapper_depth=16):
        # window -> N anonymous generic wrappers -> a named button.
        nodes = {
            ("reg", "/root"): FakeNode("desktop frame", "d", children=[("app", "/a")]),
            ("app", "/a"): FakeNode("application", "app", children=[("win", "/w")]),
        }
        chain = ("win", "/w")
        nodes[chain] = FakeNode("frame", "Win",
                                states=["active", "showing", "visible"])
        parent = chain
        for i in range(wrapper_depth):
            nid = ("g", f"/g{i}")
            nodes[nid] = FakeNode("generic", "", states=["showing", "visible"])
            nodes[parent].children = [nid]
            parent = nid
        btn = ("btn", "/b")
        nodes[btn] = FakeNode("push button", "Deep Save",
                              states=["showing", "visible"],
                              interfaces=[_ACCESSIBLE, _ACTION], actions=["Click"])
        nodes[parent].children = [btn]
        return nodes

    def _leaf_names(self, tree_node):
        names = []
        stack = [tree_node]
        while stack:
            n = stack.pop()
            if not n["children"]:
                names.append(n["name"])
            stack.extend(n["children"])
        return names

    def test_default_depth_reaches_a_control_sixteen_wrappers_deep(self):
        backend = _backend(self._deeply_nested(16))
        tree = backend.tree(depth=6)  # the default; wrappers must be transparent
        assert "Deep Save" in self._leaf_names(tree["root"])

    def test_tree_leaves_include_what_find_returns(self):
        nodes = self._deeply_nested(16)
        backend = _backend(nodes)
        found = {e.name for e in backend.find("push button", "")}
        leaves = set(self._leaf_names(backend.tree(depth=6)["root"]))
        assert found and found <= leaves

    def _all_roles(self, tree_node):
        roles = []
        stack = [tree_node]
        while stack:
            n = stack.pop()
            roles.append(n["role"])
            stack.extend(n["children"])
        return roles

    def test_tree_omits_anonymous_wrappers(self):
        # ui_tree is the screenshot alternative, so it must stay small text: the
        # sixteen anonymous generic wrappers between the window and the button
        # carry nothing and must not appear in the output, only the meaningful
        # nodes (the window and the control).
        backend = _backend(self._deeply_nested(16))
        roles = self._all_roles(backend.tree(depth=6)["root"])
        assert "generic" not in roles
        assert roles.count("push button") == 1
        assert "Deep Save" in self._leaf_names(backend.tree(depth=6)["root"])


class TestBoundsAreSane:
    """D4: garbage extents (unrealised widgets) must not surface as bounds."""

    def test_garbage_extents_yield_no_bounds(self):
        nodes = _tree_with_button(extents=(0, 0, 612489008, 32573))
        backend = _backend(nodes)
        el = backend.find("push button", "")[0]
        assert el.bounds is None

    def test_sane_extents_are_kept(self):
        backend = _backend(_tree_with_button(extents=(10, 20, 74, 30)))
        el = backend.find("push button", "")[0]
        assert el.bounds.as_dict() == {"x": 10, "y": 20, "w": 74, "h": 30}

    def test_negative_size_is_rejected(self):
        backend = _backend(_tree_with_button(extents=(0, 0, -1, 30)))
        assert backend.find("push button", "")[0].bounds is None


class TestOffScreenIsNotSurfaced:
    """D5: an off-screen dialog's controls must not surface as if on screen."""

    def _window_with_hidden_dialog(self):
        return {
            ("reg", "/root"): FakeNode("desktop frame", "d", children=[("app", "/a")]),
            ("app", "/a"): FakeNode("application", "app", children=[("win", "/w")]),
            ("win", "/w"): FakeNode("frame", "Win",
                                    states=["active", "showing", "visible"],
                                    children=[("real", "/r"), ("dlg", "/d")]),
            ("real", "/r"): FakeNode("push button", "Visible Save",
                                     states=["showing", "visible"],
                                     interfaces=[_ACCESSIBLE, _ACTION], actions=["Click"]),
            # An off-screen close-confirmation dialog: not showing, but GTK still
            # reports its buttons as showing. Pruning at the dialog is what keeps
            # them out of the results.
            ("dlg", "/d"): FakeNode("dialog", "", states=["visible"],
                                    children=[("disc", "/disc")]),
            ("disc", "/disc"): FakeNode("push button", "Discard",
                                        states=["showing", "visible"],
                                        interfaces=[_ACCESSIBLE, _ACTION],
                                        actions=["Click"]),
        }

    def test_hidden_dialog_button_is_not_found(self):
        backend = _backend(self._window_with_hidden_dialog())
        assert backend.find("push button", "Discard") == []

    def test_the_visible_control_is_still_found(self):
        backend = _backend(self._window_with_hidden_dialog())
        assert [e.name for e in backend.find("push button", "")] == ["Visible Save"]

    def test_tree_omits_the_off_screen_dialog(self):
        backend = _backend(self._window_with_hidden_dialog())
        tree = backend.tree(depth=6)
        names = []
        stack = [tree["root"]]
        while stack:
            n = stack.pop()
            names.append(n["name"])
            stack.extend(n["children"])
        assert "Discard" not in names
        assert "Visible Save" in names


class TestEmptyStateSetIsNotPruned:
    """A node with no states at all is unknown, not hidden.

    Observed on snap-store (Flutter under GTK): the node the Flutter view hangs
    off reports an empty state set, while every control below it reports
    showing. Pruning on the missing "showing" dropped the whole application
    tree, so ui_tree returned the bare frame and ui_find matched nothing.
    """

    def _flutter_tree(self):
        return {
            ("reg", "/root"): FakeNode("desktop frame", "d", children=[("app", "/a")]),
            ("app", "/a"): FakeNode("application", "snap-store",
                                    children=[("win", "/w")]),
            ("win", "/w"): FakeNode("frame", "App Center",
                                    states=["active", "showing", "visible"],
                                    children=[("embed", "/e")]),
            # The Flutter embedded root: structural, and no states at all.
            ("embed", "/e"): FakeNode("filler", "", states=[],
                                      children=[("btn", "/b")]),
            ("btn", "/b"): FakeNode("push button", "Explore",
                                    states=["showing", "visible"],
                                    interfaces=[_ACCESSIBLE, _ACTION],
                                    actions=["Click"]),
        }

    def test_tree_reaches_controls_below_a_stateless_node(self):
        backend = _backend(self._flutter_tree())
        tree = backend.tree(depth=6)
        names = []
        stack = [tree["root"]]
        while stack:
            n = stack.pop()
            names.append(n["name"])
            stack.extend(n["children"])
        assert "Explore" in names

    def test_find_matches_below_a_stateless_node(self):
        backend = _backend(self._flutter_tree())
        assert [e.name for e in backend.find("push button", "")] == ["Explore"]

    def test_a_node_with_states_but_not_showing_is_still_pruned(self):
        nodes = self._flutter_tree()
        nodes[("embed", "/e")].states = ["visible"]  # reported, and off screen
        backend = _backend(nodes)
        assert backend.find("push button", "") == []


class TestFindBudgetIsNotSpentOnPrunedNodes:
    """The node budget must count processed nodes, not pruned ones.

    Observed on LibreOffice Writer: about six thousand cached menu items, none
    showing, precede the document body in walk order. Each pruned item spent
    one unit of the find budget, so the walk died inside the menus and the
    document body was never reached.
    """

    def _menu_heavy_tree(self, hidden=2500):
        nodes = {
            ("reg", "/root"): FakeNode("desktop frame", "d", children=[("app", "/a")]),
            ("app", "/a"): FakeNode("application", "soffice",
                                    children=[("win", "/w")]),
            ("win", "/w"): FakeNode("frame", "Untitled 1",
                                    states=["active", "showing", "visible"],
                                    children=[("bar", "/bar"), ("doc", "/doc")]),
            ("bar", "/bar"): FakeNode("menu bar", "", states=["showing", "visible"]),
            ("doc", "/doc"): FakeNode("document text", "Document",
                                      states=["showing", "visible", "editable"],
                                      interfaces=[_ACCESSIBLE, "org.a11y.atspi.Text"],
                                      text="body"),
        }
        items = []
        for i in range(hidden):
            nid = ("mi", f"/m{i}")
            nodes[nid] = FakeNode("menu item", f"Item {i}", states=["visible"])
            items.append(nid)
        nodes[("bar", "/bar")].children = items
        return nodes

    def test_a_match_past_thousands_of_hidden_menu_items_is_found(self):
        backend = _backend(self._menu_heavy_tree())
        found = backend.find("document text", "")
        assert [e.name for e in found] == ["Document"]


def _multi_app_tree():
    # Two apps, each with one frame. Calculator's frame is NOT active; gedit's is.
    # This is the exact shape the expansion targets: read a non-focused window.
    return {
        ("reg", "/root"): FakeNode("desktop frame", "d",
                                   children=[("a1", "/a1"), ("a2", "/a2")]),
        ("a1", "/a1"): FakeNode("application", "gnome-calculator", pid=100,
                                children=[("f1", "/f1")]),
        ("f1", "/f1"): FakeNode("frame", "Calculator",
                                states=["showing", "visible"],
                                children=[("b7", "/b7")]),
        ("b7", "/b7"): FakeNode("button", "7", states=["showing", "visible"],
                                interfaces=[_ACCESSIBLE, _ACTION], actions=["Click"]),
        ("a2", "/a2"): FakeNode("application", "gedit", pid=200,
                                children=[("f2", "/f2")]),
        ("f2", "/f2"): FakeNode("frame", "gedit",
                                states=["active", "showing", "visible"],
                                children=[("bs", "/bs")]),
        ("bs", "/bs"): FakeNode("push button", "Save",
                                states=["showing", "visible"],
                                interfaces=[_ACCESSIBLE, _ACTION], actions=["Click"]),
    }


class TestReadAnyWindow:
    """The expansion: target a window by name or search them all, not only active."""

    def test_default_reads_the_active_window(self):
        backend = _backend(_multi_app_tree())
        names = {e.name for e in backend.find("", "", app="")}
        assert "Save" in names  # gedit is active
        assert "7" not in names  # Calculator is not

    def test_by_name_reads_a_non_focused_window(self):
        # The motivating case: Calculator is not focused, gedit is, yet a read
        # targeted by name still finds Calculator's button.
        backend = _backend(_multi_app_tree())
        found = backend.find("button", "7", app="gnome-calculator")
        assert [e.name for e in found] == ["7"]

    def test_star_searches_every_window(self):
        backend = _backend(_multi_app_tree())
        names = {e.name for e in backend.find("", "", app="*")}
        assert {"7", "Save"} <= names

    def test_unknown_app_is_no_tree(self):
        backend = _backend(_multi_app_tree())
        with pytest.raises(StructuredError) as exc:
            backend.find("", "", app="no-such-app")
        assert exc.value.code == "no_tree"

    def test_tree_by_name_returns_a_single_root(self):
        backend = _backend(_multi_app_tree())
        result = backend.tree(depth=6, app="gnome-calculator")
        assert result["root"]["name"] == "Calculator"

    def test_tree_star_returns_multiple_roots(self):
        backend = _backend(_multi_app_tree())
        result = backend.tree(depth=6, app="*")
        titles = {r["name"] for r in result["roots"]}
        assert {"Calculator", "gedit"} <= titles


class TestListWindows:
    def test_windows_lists_every_frame_with_active_flag(self):
        backend = _backend(_multi_app_tree())
        wins = backend.windows()["windows"]
        by_title = {w["title"]: w for w in wins}
        assert set(by_title) == {"Calculator", "gedit"}
        assert by_title["gedit"]["active"] is True
        assert by_title["Calculator"]["active"] is False
        assert by_title["Calculator"]["app_name"] == "gnome-calculator"
        assert by_title["gedit"]["ref"].startswith("atspi:")

    def test_windows_carry_the_owning_app_pid(self):
        # app_open confirms a launch by process identity when the a11y app name
        # matches no desktop-entry token (LibreOffice launches as 'soffice'),
        # so each window carries its application's pid.
        backend = _backend(_multi_app_tree())
        by_title = {w["title"]: w for w in backend.windows()["windows"]}
        assert by_title["Calculator"]["pid"] == 100
        assert by_title["gedit"]["pid"] == 200


class TestBusUnavailable:
    def test_every_read_and_act_needs_a_reachable_bus(self):
        backend = _backend(_tree_with_button(), reachable=False)
        for call in (
            lambda: backend.tree(3),
            lambda: backend.find("push button", ""),
            lambda: backend.act("atspi:1", "click", ""),
            lambda: backend.wait("push button", "", 0),
        ):
            with pytest.raises(StructuredError) as exc:
                call()
            assert exc.value.code == "at_spi_unavailable"
            assert exc.value.extra["remedy"]


class TestWait:
    def test_returns_the_first_match_immediately(self):
        backend = _backend(_tree_with_button())
        element = backend.wait("push button", "", 1000)
        assert element is not None
        assert element.name == "Save"

    def test_returns_none_on_timeout(self):
        backend = _backend(_tree_with_button())
        assert backend.wait("dialog", "", 0) is None


class TestForegroundWindow:
    """The Wayland foreground-window path, migrated off gi onto the client."""

    def _two_active_windows(self):
        # A chrome window then a terminal window, both active. GNOME orders the
        # most recently focused last, so the terminal must win.
        return {
            ("reg", "/root"): FakeNode("desktop frame", "main",
                                       children=[("chrome", "/c"), ("term", "/t")]),
            ("chrome", "/c"): FakeNode("application", "Google Chrome", pid=100,
                                       children=[("cw", "/cw")]),
            ("cw", "/cw"): FakeNode("frame", "Chrome", states=["active"],
                                    extents=(0, 0, 1920, 1080)),
            ("term", "/t"): FakeNode("application", "gnome-terminal", pid=200,
                                     children=[("tw", "/tw")]),
            ("tw", "/tw"): FakeNode("frame", "Terminal", states=["active"],
                                    extents=(0, 0, 800, 600)),
        }

    def test_last_active_window_wins(self, monkeypatch):
        monkeypatch.setattr(atspi, "_proc_comm", lambda pid: "")
        backend = _backend(self._two_active_windows())
        fg = backend.foreground_window()
        assert fg is not None
        assert fg.pid == 200
        assert fg.title == "Terminal"

    def test_app_name_prefers_proc_comm(self, monkeypatch):
        monkeypatch.setattr(atspi, "_proc_comm", lambda pid: "firefox")
        backend = _backend(self._two_active_windows())
        assert backend.foreground_window().app_name == "firefox"

    def test_none_when_no_active_window(self, monkeypatch):
        nodes = self._two_active_windows()
        nodes[("cw", "/cw")].states = []
        nodes[("tw", "/tw")].states = []
        backend = _backend(nodes)
        assert backend.foreground_window() is None

    def test_none_when_bus_unreachable(self):
        backend = _backend(self._two_active_windows(), reachable=False)
        assert backend.foreground_window() is None

    def test_module_helper_is_none_without_a_backend(self, monkeypatch):
        from computer_use.tools.ui import backend as be

        monkeypatch.setattr(be, "resolve_backend", lambda: None)
        assert atspi.foreground_window() is None


class TestBuildBackend:
    def test_off_linux_there_is_no_backend(self, monkeypatch):
        monkeypatch.setattr(atspi.sys, "platform", "darwin")
        assert atspi.build_atspi_backend() is None


class TestStructuredCapabilityHelper:
    """tools.ui.backend.structured_capability is what get_platform_info reads."""

    def test_reports_unavailable_when_no_backend_resolves(self, monkeypatch):
        from computer_use.tools.ui import backend as be

        monkeypatch.setattr(be, "resolve_backend", lambda: None)
        cap = be.structured_capability()
        assert cap["available"] is False
        assert cap["backend"] is None

    def test_reports_the_backends_capability_when_resolved(self, monkeypatch):
        from computer_use.tools.ui import backend as be

        b = _backend(_tree_with_button(), server="x11")
        monkeypatch.setattr(be, "resolve_backend", lambda: b)
        cap = be.structured_capability()
        assert cap["backend"] == "atspi"
        assert cap["coordinate_trust"] == "real"
        # available tracks bus reachability: a resolved backend on a reachable
        # bus is usable, which is what a caller checks before choosing the tier.
        assert cap["available"] is True


class TestEnablement:
    """Enablement is a layer: the bus and each toolkit are enabled separately."""

    def test_launch_env_turns_on_qt_accessibility(self):
        env = atspi.accessibility_launch_env({"HOME": "/home/x"})
        assert env["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"] == "1"
        assert env["HOME"] == "/home/x"  # the base env is preserved, not replaced

    def test_launch_env_defaults_to_the_process_environment(self):
        # The no-base call is the normal launch path, and it raised NameError:
        # the function used os.environ without importing os, which every test
        # dodged by passing a base dict.
        env = atspi.accessibility_launch_env()
        assert env["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"] == "1"

    def test_chromium_flag_forces_renderer_accessibility(self):
        assert "--force-renderer-accessibility" in atspi.chromium_accessibility_flags()

    def test_launch_env_turns_on_chromium_accessibility(self):
        # Chromium's env gate rides beside Qt's: one launch environment enables
        # whichever of the two toolkits the entry turns out to be.
        env = atspi.accessibility_launch_env({"HOME": "/home/x"})
        assert env["ACCESSIBILITY_ENABLED"] == "1"

    def test_bus_reachable_is_false_off_linux(self, monkeypatch):
        monkeypatch.setattr(atspi.sys, "platform", "darwin")
        assert atspi.bus_reachable() is False

    def test_enable_bus_is_false_off_linux(self, monkeypatch):
        monkeypatch.setattr(atspi.sys, "platform", "darwin")
        assert atspi.enable_bus() is False


class TestPlatformDefaultIsHonest:
    """The other three platforms answer 'no structured tier', never a stub."""

    def test_base_backend_reports_no_structured_tier(self):
        from computer_use.platform.base import PlatformBackend

        class _Bare(PlatformBackend):
            def get_screen_capture(self):
                raise NotImplementedError

            def get_action_executor(self):
                raise NotImplementedError

            def is_available(self):
                return True

            def get_foreground_window(self):
                return None

        cap = _Bare().structured_capability()
        assert cap["available"] is False
        assert cap["backend"] is None


def _thin_flutter_tree():
    """A window that answers with no content: a thin already-running toolkit.

    The frame is on screen and healthy, but it exposes no children. Its would-be
    content lives in the client's sr_children, which a set_screen_reader(True)
    write would reveal. The tier must never make that write, so the content stays
    hidden and the window reads tree-only.
    """
    nodes = {
        ("reg", "/root"): FakeNode("desktop frame", "main",
                                   children=[("app", "/a")]),
        ("app", "/a"): FakeNode("application", "snap-store", toolkit="GTK",
                                children=[("win", "/w")]),
        ("win", "/w"): FakeNode("frame", "App Center",
                                states=["active", "showing", "visible"],
                                extents=(0, 0, 1213, 768), children=[]),
        ("search", "/s"): FakeNode(
            "text", "Search for apps",
            states=["editable", "showing", "visible"],
            interfaces=[_ACCESSIBLE, _EDITABLE_TEXT], text="",
        ),
    }
    sr_children = {("win", "/w"): [("search", "/s")]}
    return nodes, sr_children


class TestNoScreenReaderWrite:
    """A thin read never raises the screen-reader flag.

    On GNOME setting org.a11y.Status.ScreenReaderEnabled autostarts a screen
    reader (and speech), so the tier never sets it. A thin already-running
    toolkit degrades honestly to tree-only. The sr_log spy on the client records
    every write, so an empty log is proof no screen-reader write happened.
    """

    def test_thin_find_makes_no_screen_reader_write(self):
        nodes, sr_children = _thin_flutter_tree()
        be = _backend(nodes, sr_children=sr_children)
        found = be.find("text", "", "")
        # The content lives behind the flag; the tier never raises it, so the
        # window reads tree-only and the search field never appears.
        assert found == []
        assert be._client.sr_log == []

    def test_thin_tree_makes_no_screen_reader_write(self):
        nodes, sr_children = _thin_flutter_tree()
        be = _backend(nodes, sr_children=sr_children)
        out = be.tree(6, "")
        assert out["root"]["children"] == []
        assert be._client.sr_log == []

    def test_a_rich_window_makes_no_screen_reader_write(self):
        be = _backend(_tree_with_button())
        found = be.find("push button", "", "")
        assert [e.name for e in found] == ["Save"]
        assert be._client.sr_log == []


def _xwayland_tree(frame_origin=(67, 32)):
    """A window whose frame reports a real, nonzero screen origin.

    The shape of an XWayland client on a Wayland session: the X server knows
    the true window position, and the toolkit reports true screen extents
    through AT-SPI. The matching X toplevel is what the trust check confirms
    against.
    """
    x, y = frame_origin
    nodes = {
        ("reg", "/root"): FakeNode("desktop frame", "main",
                                   children=[("app", "/a")]),
        ("app", "/a"): FakeNode("application", "code", toolkit="Chromium",
                                children=[("win", "/w")]),
        ("win", "/w"): FakeNode("frame", "readme - Code",
                                states=["active", "showing", "visible"],
                                extents=(x, y, 1213, 768),
                                children=[("btn", "/b")]),
        ("btn", "/b"): FakeNode(
            "push button", "Save",
            states=["showing", "visible"], extents=(x + 35, y + 10, 74, 30),
            interfaces=[_ACCESSIBLE, _COMPONENT, _ACTION], actions=["click"],
        ),
    }
    return nodes


class TestPerWindowCoordinateTrust:
    """On Wayland, an element's bounds are trustworthy exactly when its window
    is an XWayland client: the X server vouches for the origin. The check is a
    match between the frame's own claimed geometry and a real X toplevel, so a
    Wayland-native popup that happens to claim a nonzero origin never passes.
    """

    def test_x_confirmed_frame_marks_elements_real(self, monkeypatch):
        be = _backend(_xwayland_tree(), server="wayland")
        monkeypatch.setattr(
            atspi, "_x_toplevel_geometries",
            lambda: [("readme - Code", 67, 32, 1213, 768)],
        )
        found = be.find("push button", "", "")
        assert found[0].coordinate_trust == "real"
        assert found[0].as_dict()["coordinate_trust"] == "real"

    def test_zero_origin_frame_is_window_relative_and_x_is_not_asked(
        self, monkeypatch
    ):
        def boom():
            raise AssertionError("a (0,0) frame must not cost an X round trip")

        monkeypatch.setattr(atspi, "_x_toplevel_geometries", boom)
        be = _backend(_tree_with_button(), server="wayland")
        found = be.find("push button", "", "")
        assert found[0].coordinate_trust == "none"
        assert found[0].as_dict()["coordinate_trust"] == "none"

    def test_unconfirmed_nonzero_origin_is_not_trusted(self, monkeypatch):
        # A Wayland-native popup reports its parent-relative guess as a nonzero
        # origin; no X toplevel matches it, so its bounds stay untrusted.
        be = _backend(_xwayland_tree(frame_origin=(99, 25)), server="wayland")
        monkeypatch.setattr(atspi, "_x_toplevel_geometries", list)
        found = be.find("push button", "", "")
        assert found[0].coordinate_trust == "none"

    def test_x11_elements_carry_no_per_element_field(self):
        # On X11 every bound is real and the capability block already says so;
        # a per-element field would be 33 copies of the same word.
        be = _backend(_tree_with_button(), server="x11")
        found = be.find("push button", "", "")
        assert found[0].coordinate_trust is None
        assert "coordinate_trust" not in found[0].as_dict()

    def test_x_failure_degrades_to_untrusted(self, monkeypatch):
        def boom():
            raise RuntimeError("no X server")

        monkeypatch.setattr(atspi, "_x_toplevel_geometries", boom)
        be = _backend(_xwayland_tree(), server="wayland")
        found = be.find("push button", "", "")
        assert found[0].coordinate_trust == "none"


class TestNearest:
    """The candidates a ui_act that matched nothing hands back."""

    @staticmethod
    def _window(*controls):
        """desktop -> app -> active window holding the given (role, name, states)."""
        nodes = {
            ("reg", "/root"): FakeNode("desktop frame", "main", children=[("app", "/a")]),
            ("app", "/a"): FakeNode("application", "gedit", children=[("win", "/w")]),
        }
        kids = []
        for index, (role, name, states) in enumerate(controls):
            node = ("app", f"/c{index}")
            nodes[node] = FakeNode(
                role, name, states=states, extents=(index, 0, 10, 10),
                interfaces=[_ACCESSIBLE, _COMPONENT, _ACTION], actions=["Click"],
            )
            kids.append(node)
        nodes[("win", "/w")] = FakeNode(
            "frame", "Doc - gedit", states=["active", "showing", "visible"],
            extents=(0, 0, 800, 600), children=kids,
        )
        return nodes

    def test_a_legacy_role_name_still_finds_the_button(self):
        on = ["showing", "visible"]
        backend = _backend(self._window(
            ("button", "Save", on), ("button", "Cancel", on),
            ("label", "Save changes?", on), ("filler", "", on),
        ))
        assert backend.find("push button", "Save") == []
        names = [e.name for e in backend.nearest("push button", "Save")]
        assert names[0] == "Save"
        assert "Save changes?" in names
        assert "Cancel" not in names  # shares a letter, not the request
        assert "" not in names  # anonymous wrappers are never offered

    def test_a_misspelt_name_ranks_the_closest_first(self):
        on = ["showing"]
        backend = _backend(self._window(
            ("button", "Open", on), ("button", "Save", on), ("button", "Sage", on),
        ))
        assert [e.name for e in backend.nearest("", "Svae")][:1] == ["Save"]

    def test_the_limit_holds_and_walk_order_breaks_ties(self):
        on = ["showing"]
        backend = _backend(self._window(*[("button", "Save", on)] * 8))
        found = backend.nearest("", "Save file", limit=3)
        assert len(found) == 3
        assert [e.bounds.x for e in found] == [0, 1, 2]

    def test_an_off_screen_control_is_never_a_candidate(self):
        backend = _backend(self._window(
            ("button", "Save", ["visible"]), ("button", "Saved", ["showing"]),
        ))
        assert [e.name for e in backend.nearest("", "Save")] == ["Saved"]

    def test_candidates_carry_their_window_and_refs_that_act(self):
        backend = _backend(self._window(("button", "Save", ["showing"])))
        (candidate,) = backend.nearest("push button", "Save")
        assert candidate.window == "Doc - gedit"
        assert candidate.coordinate_trust == "none"  # Wayland, origin (0, 0)
        assert backend.act(candidate.ref, "click", "")["action"] == "click"

    def test_with_no_name_the_role_decides(self):
        on = ["showing"]
        backend = _backend(self._window(("button", "OK", on), ("label", "Status", on)))
        assert [e.name for e in backend.nearest("push button", "")] == ["OK"]

    def test_nothing_close_is_an_empty_list(self):
        backend = _backend(self._window(("button", "Open", ["showing"])))
        assert backend.nearest("", "Quit") == []


class TestActByNameEndToEnd:
    """ui_act by name over the real backend logic: one call instead of two."""

    @pytest.fixture
    def ui(self, monkeypatch):
        from computer_use.tools.ui import tools

        return tools

    def test_one_call_clicks_the_named_button(self, ui, monkeypatch):
        nodes = _tree_with_button(role="button", name="Save")
        backend = _backend(nodes)
        monkeypatch.setattr(ui, "resolve_backend", lambda: backend)
        result = ui.ui_act(action="click", name="save", role="button")
        assert result["ok"] is True
        assert nodes[("btn", "/b")].did == [0]
        assert result["state"]["name"] == "Save"
        # The returned ref is the button's, for the next call.
        assert ui.ui_act(result["ref"], "click")["ok"] is True
        assert nodes[("btn", "/b")].did == [0, 0]

    def test_a_legacy_role_comes_back_as_a_candidate_that_acts(self, ui, monkeypatch):
        nodes = _tree_with_button(role="button", name="Save")
        backend = _backend(nodes)
        monkeypatch.setattr(ui, "resolve_backend", lambda: backend)
        result = ui.ui_act(action="click", name="Save", role="push button")
        assert result["error"] == "not_found"
        assert nodes[("btn", "/b")].did == []
        (candidate,) = result["candidates"]
        assert (candidate["role"], candidate["name"]) == ("button", "Save")
        assert ui.ui_act(candidate["ref"], "click")["ok"] is True
        assert nodes[("btn", "/b")].did == [0]
