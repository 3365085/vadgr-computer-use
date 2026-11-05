# Copyright 2026 Victor Santiago Montaño Diaz
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""The Cache GetItems parser, against a synthetic reply body.

The parsing is the pure part of the fast path, so it is proven without a live
bus: the node keying, the child grouping and ordering, the completeness check
that decides when to fall back to a live read, and the shared state decoding.
"""

from __future__ import annotations

import pytest

from computer_use.tools.ui.atspi import ElementGone, decode_states
from computer_use.tools.ui.cache import build_snapshot


def _item(bus, path, parent_path, index, child_count, role_enum, name,
          interfaces=(), states=(0, 0), app_path="/app"):
    # One GetItems struct: (ref, app, parent, index, child_count, interfaces,
    # name, role, description, states). refs are (so) = (bus_name, path).
    return [
        [bus, path], [bus, app_path], [bus, parent_path], index, child_count,
        list(interfaces), name, role_enum, "", list(states),
    ]


def _body():
    # frame -> [button "7", button "8"]; the frame declares 2 children.
    return [
        _item("bus", "/frame", "/app", 0, 2, 23, "Calculator"),
        _item("bus", "/b7", "/frame", 0, 0, 43, "7"),
        _item("bus", "/b8", "/frame", 1, 0, 43, "8"),
    ]


class TestSnapshotStructure:
    def test_keys_by_node_and_counts_items(self):
        snap = build_snapshot(_body(), decode_states)
        assert len(snap) == 3
        assert snap.has(("bus", "/frame"))
        assert snap.item(("bus", "/b7")).name == "7"
        assert snap.item(("bus", "/b7")).role_enum == 43

    def test_children_grouped_by_parent_in_index_order(self):
        # Feed the children out of order; they must come back sorted by index.
        body = [
            _item("bus", "/frame", "/app", 0, 2, 23, "Calculator"),
            _item("bus", "/b8", "/frame", 1, 0, 43, "8"),
            _item("bus", "/b7", "/frame", 0, 0, 43, "7"),
        ]
        snap = build_snapshot(body, decode_states)
        assert snap.children(("bus", "/frame")) == [("bus", "/b7"), ("bus", "/b8")]

    def test_role_enums_collected(self):
        snap = build_snapshot(_body(), decode_states)
        assert snap.role_enums() == {23, 43}
        assert snap.node_with_role(43) in (("bus", "/b7"), ("bus", "/b8"))


class TestCompleteness:
    def test_complete_node_holds_all_declared_children(self):
        snap = build_snapshot(_body(), decode_states)
        assert snap.is_complete(("bus", "/frame")) is True

    def test_incomplete_node_is_flagged_for_a_live_read(self):
        # The frame declares 2 children but only one is in the cache: not warm.
        body = [
            _item("bus", "/frame", "/app", 0, 2, 23, "Calculator"),
            _item("bus", "/b7", "/frame", 0, 0, 43, "7"),
        ]
        snap = build_snapshot(body, decode_states)
        assert snap.is_complete(("bus", "/frame")) is False

    def test_absent_node_is_not_complete(self):
        snap = build_snapshot(_body(), decode_states)
        assert snap.is_complete(("bus", "/missing")) is False

    def test_unknown_child_count_is_never_complete(self):
        # A negative child count is AT-SPI for "unknown". LibreOffice exports
        # its document body with child_count=-1 and no cached children while a
        # live GetChildren returns the paragraph; trusting the cache here hides
        # the one element the document window exists for.
        body = [
            _item("bus", "/frame", "/app", 0, 1, 23, "Writer"),
            _item("bus", "/doc", "/frame", 0, -1, 55, "Document"),
        ]
        snap = build_snapshot(body, decode_states)
        assert snap.is_complete(("bus", "/doc")) is False

    def test_unknown_child_count_with_cached_children_is_still_not_complete(self):
        # Even with some children cached, a declared -1 cannot vouch that they
        # are all of them, so the read must go live.
        body = [
            _item("bus", "/menu", "/app", 0, -1, 33, "File"),
            _item("bus", "/item", "/menu", 0, 0, 35, "Open"),
        ]
        snap = build_snapshot(body, decode_states)
        assert snap.is_complete(("bus", "/menu")) is False


class TestStateDecodingMatchesLivePath:
    def test_states_decode_with_the_shared_function(self):
        # Word 0 bit 20 is pressed, exactly as the live GetState path decodes it.
        body = [_item("bus", "/t", "/app", 0, 0, 62, "Toggle",
                      states=(1 << 20, 0))]
        snap = build_snapshot(body, decode_states)
        assert "pressed" in snap.item(("bus", "/t")).states


# ---------------------------------------------------------------------------
# The client's use of the cache: serve from a warm snapshot, degrade without one
# ---------------------------------------------------------------------------

# dbus-fast is a Linux-only dependency (sys_platform == 'linux' in pyproject),
# so the client tests skip where it is absent while the pure parser tests above
# still run on every OS.
try:
    from dbus_fast import MessageType
    _HAS_DBUS_FAST = True
except ModuleNotFoundError:
    MessageType = None
    _HAS_DBUS_FAST = False

from computer_use.tools.ui.atspi import _DbusClient


class _Reply:
    def __init__(self, body, error_name=None):
        self.body = body
        self.error_name = error_name
        self.message_type = (
            MessageType.ERROR if error_name else MessageType.METHOD_RETURN
        )


class _Variant:
    def __init__(self, value):
        self.value = value


class _FakeBus:
    """A stand-in bus: canned GetItems per app, and counted live calls.

    ``call`` returns a resolved reply (not a coroutine) and ``run`` passes it
    through, which is all _DbusClient needs, so no event loop is involved.

    ``texts`` maps (dest, path) to the node's real text. GetText answers the
    way LibreOffice's bridge does, because that is the behaviour the client
    must survive: an end offset past the character count returns "", never a
    clamped read (GTK clamps; LibreOffice does not).
    """

    def __init__(self, get_items: dict, live_role="LIVE-ROLE", live_children=None,
                 texts=None, hang_members=None, hang_dests=None,
                 action_names=None, blank_bulk_actions=False,
                 silent_settext=False):
        self._get_items = get_items
        self._live_role = live_role
        self._live_children = live_children or {}
        self._texts = texts or {}
        self._hang_members = hang_members or set()
        self._hang_dests = hang_dests  # None hangs every dest for a hung member
        self._action_names = action_names or {}
        self._blank_bulk_actions = blank_bulk_actions
        self._silent_settext = silent_settext
        self.extents_answer = None
        self.live_calls = 0
        self.timeouts = []  # (member, per-call timeout) for every call

    _HANG = object()  # a call whose reply never arrives; run() raises on it

    def call(self, dest, path, iface, member, signature="", body=None, timeout=None):
        self.timeouts.append((member, timeout))
        if member in self._hang_members and (
            self._hang_dests is None or dest in self._hang_dests
        ):
            # The real bus raises when the reply future is *awaited*, not when
            # the message is built, so the sentinel defers the raise to run().
            self.live_calls += 1
            return self._HANG
        if member == "GetExtents" and self.extents_answer is not None:
            self.live_calls += 1
            return _Reply([list(self.extents_answer)])
        if member == "GetActions":
            self.live_calls += 1
            names = self._action_names.get((dest, path), [])
            shown = ["" for _ in names] if self._blank_bulk_actions else names
            return _Reply([[[n, "", ""] for n in shown]])
        if member == "GetName" and iface == "org.a11y.atspi.Action":
            self.live_calls += 1
            names = self._action_names.get((dest, path), [])
            index = body[0]
            return _Reply([names[index] if index < len(names) else ""])
        if member == "GetItems":
            items = self._get_items.get(dest)
            if items is None:
                return _Reply(None, error_name="org.freedesktop.DBus.Error.UnknownMethod")
            return _Reply([items])
        # Any per-node read here is a live fallback; count it and answer plainly.
        self.live_calls += 1
        if member == "GetRoleName":
            return _Reply([self._live_role])
        if member == "GetChildren":
            kids = self._live_children.get((dest, path), [])
            return _Reply([[list(k) for k in kids]])
        if member == "SetTextContents":
            self.live_calls += 1
            if not self._silent_settext:
                self._texts[(dest, path)] = body[0]
            return _Reply([True])
        if member == "DeleteText":
            self.live_calls += 1
            value = self._texts.get((dest, path), "")
            start, end = body
            self._texts[(dest, path)] = value[:start] + value[end:]
            return _Reply([True])
        if member == "InsertText":
            self.live_calls += 1
            value = self._texts.get((dest, path), "")
            pos, text, length = body
            self._texts[(dest, path)] = value[:pos] + text[:length] + value[pos:]
            return _Reply([True])
        if member == "GetText":
            value = self._texts.get((dest, path), "")
            start, end = body
            if end == -1:
                end = len(value)
            if end > len(value):
                return _Reply([""])  # the LibreOffice out-of-range answer
            return _Reply([value[start:end]])
        if member == "Get" and body == ["org.a11y.atspi.Text", "CharacterCount"]:
            value = self._texts.get((dest, path), "")
            return _Reply([_Variant(len(value))])
        return _Reply([""])

    def run(self, value):
        if value is self._HANG:
            raise TimeoutError()
        return value

    def gather(self, calls):
        # Mirrors the real bus: each call is bounded on its own, so one that
        # never answers comes back as its exception rather than sinking the
        # whole batch.
        out = []
        for spec in calls:
            value = self.call(*spec)
            out.append(TimeoutError() if value is self._HANG else value)
        return out


def _one_item(bus, path, parent, cc, role_enum, name):
    return [[bus, path], [bus, "/app"], [bus, parent], 0, cc, [], name,
            role_enum, "", [0, 0]]


class _OptionalRoleNameBus(_FakeBus):
    """AccessKit exports GetRole but omits the optional GetRoleName method."""

    def __init__(self, *, role=23, name_error="UnknownMethod", role_error=None,
                 cached=False):
        items = [_one_item("accesskit", "/frame", "/app", 0, role, "Test window")]
        super().__init__({"accesskit": items} if cached else {})
        self.role = role
        self.name_error = name_error
        self.role_error = role_error
        self.methods = []

    def call(self, dest, path, iface, member, signature="", body=None, timeout=None):
        self.methods.append(member)
        if dest == "accesskit" and member == "GetRoleName":
            return _Reply(None, error_name=f"org.freedesktop.DBus.Error.{self.name_error}")
        if dest == "accesskit" and member == "GetRole":
            if self.role_error:
                return _Reply(None, error_name=f"org.freedesktop.DBus.Error.{self.role_error}")
            return _Reply([self.role])
        return super().call(dest, path, iface, member, signature, body)


@pytest.mark.skipif(not _HAS_DBUS_FAST, reason="dbus-fast is installed on Linux only")
class TestOptionalRoleName:
    @pytest.mark.parametrize("role, expected", [
        (16, "dialog"), (23, "frame"), (29, "label"), (43, "button"),
        (61, "text"), (62, "toggle button"), (69, "window"),
        (75, "application"), (79, "entry"), (130, "switch"),
        (70, "extended"), (9999, "unknown"),
    ])
    def test_missing_optional_name_uses_canonical_numeric_role(self, role, expected):
        bus = _OptionalRoleNameBus(role=role)
        assert _DbusClient(bus).role_name(("accesskit", "/frame")) == expected
        assert bus.methods == ["GetRoleName", "GetRole"]

    @pytest.mark.parametrize("error", ["UnknownObject", "ServiceUnknown", "NoReply"])
    def test_object_failure_is_not_an_optional_method(self, error):
        bus = _OptionalRoleNameBus(name_error=error)
        with pytest.raises(ElementGone):
            _DbusClient(bus).role_name(("accesskit", "/frame"))
        assert bus.methods == ["GetRoleName"]

    @pytest.mark.parametrize("error", ["UnknownObject", "ServiceUnknown", "UnknownMethod"])
    def test_missing_numeric_role_still_reports_gone(self, error):
        bus = _OptionalRoleNameBus(role_error=error)
        with pytest.raises(ElementGone):
            _DbusClient(bus).role_name(("accesskit", "/frame"))
        assert bus.methods == ["GetRoleName", "GetRole"]

    def test_cache_calibration_uses_same_fallback(self):
        bus = _OptionalRoleNameBus(cached=True)
        client = _DbusClient(bus)
        client.warm_caches(["accesskit"])
        before = list(bus.methods)
        assert client.role_name(("accesskit", "/frame")) == "frame"
        assert bus.methods == before

    def test_toolkit_role_names_do_not_cross_application_caches(self):
        bus = _OptionalRoleNameBus(role=43, cached=True)
        bus._get_items["gtk"] = [_one_item("gtk", "/button", "/app", 0, 43, "Test")]
        client = _DbusClient(bus)
        client.warm_caches(["gtk", "accesskit"])
        assert client.role_name(("gtk", "/button")) == "LIVE-ROLE"
        assert client.role_name(("accesskit", "/frame")) == "button"


@pytest.mark.skipif(not _HAS_DBUS_FAST,
                    reason="dbus-fast is installed on Linux only")
class TestClientServesFromCache:
    def test_role_and_name_come_from_the_snapshot_not_the_bus(self):
        body = [_one_item("gtk", "/btn", "/frame", 0, 43, "7")]
        bus = _FakeBus({"gtk": body})
        client = _DbusClient(bus)
        client.warm_caches(["gtk"])
        # role calibration is one live GetRoleName per distinct enum; after that,
        # reading the node's role and name touches the bus no further.
        calls_after_warm = bus.live_calls
        assert client.role_name(("gtk", "/btn")) == "LIVE-ROLE"  # calibrated name
        assert client.name(("gtk", "/btn")) == "7"
        assert bus.live_calls == calls_after_warm  # served from the snapshot

    def test_cache_absent_app_degrades_to_live(self):
        # The app exports no Cache (GetItems errors), so every read is a live call.
        bus = _FakeBus({})  # no app has items
        client = _DbusClient(bus)
        client.warm_caches(["chromium"])
        before = bus.live_calls
        client.role_name(("chromium", "/x"))
        client.name(("chromium", "/x"))
        assert bus.live_calls > before  # it went to the bus, not a snapshot

    def test_unknown_child_count_falls_back_to_a_live_children_read(self):
        # The observed LibreOffice shape: the cache advertises the node but
        # declares child_count=-1 and holds no children for it, while a live
        # GetChildren returns the real child. The client must walk live rather
        # than trust the empty cached child list.
        body = [_one_item("soffice", "/doc", "/frame", -1, 55, "Document")]
        bus = _FakeBus(
            {"soffice": body},
            live_children={("soffice", "/doc"): [["soffice", "/para"]]},
        )
        client = _DbusClient(bus)
        client.warm_caches(["soffice"])
        kids = client.children(("soffice", "/doc"))
        assert kids == [("soffice", "/para")]

    def test_extents_timeout_degrades_to_no_bounds_and_trips_the_breaker(self):
        # The Ubuntu App Center's Flutter embedder never answers GetExtents
        # (observed: a dbind timeout from gi.Atspi too, so it is the app, not
        # the client). One unanswered call must read as unknown bounds, and the
        # app must not be asked again in the same traversal: a per-element
        # stall would turn one ui_tree of that app into minutes.
        bus = _FakeBus({}, hang_members={"GetExtents"})
        client = _DbusClient(bus)
        assert client.extents(("snapstore", "/btn")) is None
        calls_after_first = bus.live_calls
        assert client.extents(("snapstore", "/other")) is None
        assert bus.live_calls == calls_after_first  # breaker: no second ask
        assert client.unknown_fields(("snapstore", "/other")) == ("bounds",)

    def test_extents_breaker_is_per_app_not_global(self):
        bus = _FakeBus({}, hang_members={"GetExtents"}, hang_dests={"snapstore"})
        client = _DbusClient(bus)
        assert client.extents(("snapstore", "/btn")) is None
        # Another app still answers (the fake returns a plain empty reply body
        # for unknown members, which extents() cannot parse into four ints, so
        # give it a real answer).
        bus.extents_answer = (1, 2, 3, 4)
        assert client.extents(("gtk", "/btn")) == (1, 2, 3, 4)

    def test_a_getitems_timeout_leaves_other_apps_warm_and_is_not_reasked(self):
        # The Ubuntu App Center never answers Cache.GetItems (observed on the
        # wire: every warm_caches gathered it for five seconds and the raw
        # TimeoutError killed the whole find). The timed-out app degrades to
        # live reads, the other apps' snapshots still warm, and the app is not
        # asked for GetItems again: every later find would re-pay the stall.
        body = [_one_item("gtk", "/btn", "/frame", 0, 43, "7")]
        bus = _FakeBus({"gtk": body}, hang_members={"GetItems"},
                       hang_dests={"snapstore"})
        client = _DbusClient(bus)
        client.warm_caches(["snapstore", "gtk"])
        # gtk is warm: its name comes from the snapshot, no live call.
        before = bus.live_calls
        assert client.name(("gtk", "/btn")) == "7"
        assert bus.live_calls == before
        # A later warm never asks the timed-out app again.
        before = bus.live_calls
        client.warm_caches(["snapstore"])
        assert bus.live_calls == before

    def test_a_silent_settextcontents_falls_back_to_delete_plus_insert(self):
        # Flutter's embedder answers SetTextContents with success and writes
        # nothing (observed on the Ubuntu App Center search field), while its
        # InsertText lands. A write that does not read back is retried as
        # delete-plus-insert, and the return value is the read-back, not the
        # bridge's unconditional success flag.
        bus = _FakeBus({}, texts={("snapstore", "/entry"): "old"},
                       silent_settext=True)
        client = _DbusClient(bus)
        assert client.set_text(("snapstore", "/entry"), "blender") is True
        assert bus._texts[("snapstore", "/entry")] == "blender"

    def test_a_landed_settextcontents_needs_no_fallback_writes(self):
        bus = _FakeBus({}, texts={("gtk", "/entry"): "old"})
        client = _DbusClient(bus)
        assert client.set_text(("gtk", "/entry"), "new") is True
        assert bus._texts[("gtk", "/entry")] == "new"

    def test_a_write_that_never_lands_reports_false(self):
        # Both write paths refused: the client must say so, not return the
        # adaptor's unconditional True (the at-spi2 adaptor always answers
        # success, so the read-back is the only truth available).
        bus = _FakeBus({}, texts={("ro", "/para"): "locked"},
                       silent_settext=True, hang_members={"InsertText"})
        client = _DbusClient(bus)
        try:
            landed = client.set_text(("ro", "/para"), "new")
        except Exception:
            landed = False
        assert landed is False

    def test_an_unanswered_getstate_reads_as_unknown_not_gone(self):
        # Flutter's embedder leaves GetState unanswered for live nodes (the
        # Ubuntu App Center). Reading that as a vanished node dropped the node
        # and its whole subtree, at five seconds a node; it is an unknown field
        # instead, and an empty state set already means "presumed showing".
        bus = _FakeBus({}, hang_members={"GetState"})
        client = _DbusClient(bus)
        assert client.states(("snapstore", "/btn")) == ()
        assert client.unknown_fields(("snapstore", "/btn")) == ("states",)

    def test_one_stall_skips_both_reads_of_that_app_for_the_traversal(self):
        bus = _FakeBus({}, hang_members={"GetState"}, hang_dests={"snapstore"})
        client = _DbusClient(bus)
        client.begin_traversal()
        client.states(("snapstore", "/one"))
        asked = bus.live_calls
        assert client.extents(("snapstore", "/two")) is None
        assert client.states(("snapstore", "/two")) == ()
        assert bus.live_calls == asked  # neither read went to the bus
        assert client.unknown_fields(("snapstore", "/two")) == ("bounds", "states")
        # Another application is still read in full.
        bus.extents_answer = (1, 2, 3, 4)
        assert client.extents(("gtk", "/btn")) == (1, 2, 3, 4)
        assert client.unknown_fields(("gtk", "/btn")) == ()

    def test_a_new_traversal_asks_the_stalled_app_again(self):
        bus = _FakeBus({}, hang_members={"GetExtents"})
        client = _DbusClient(bus)
        client.begin_traversal()
        client.extents(("snapstore", "/btn"))
        asked = bus.live_calls
        client.begin_traversal()
        assert client.unknown_fields(("snapstore", "/btn")) == ()
        client.extents(("snapstore", "/btn"))
        assert bus.live_calls == asked + 1

    def test_only_getstate_and_getextents_are_bounded_at_half_a_second(self):
        bus = _FakeBus({}, live_children={("gtk", "/frame"): [["gtk", "/btn"]]})
        bus.extents_answer = (0, 0, 10, 10)
        client = _DbusClient(bus)
        client.states(("gtk", "/btn"))
        client.extents(("gtk", "/btn"))
        client.children(("gtk", "/frame"))
        timeouts = dict(bus.timeouts)
        assert timeouts["GetState"] == 0.5
        assert timeouts["GetExtents"] == 0.5
        assert timeouts["GetChildren"] is None  # the bus's own, longer bound

    def test_other_per_node_reads_that_never_answer_are_still_element_gone(self):
        # A node whose children the app will not list is unreachable: the walk
        # skips it, as before, rather than a raw TimeoutError killing the find.
        from computer_use.tools.ui.atspi import ElementGone
        bus = _FakeBus({}, hang_members={"GetChildren"})
        client = _DbusClient(bus)
        with pytest.raises(ElementGone):
            client.children(("snapstore", "/stale"))

    def test_unnamed_bulk_actions_fall_back_to_per_index_getname(self):
        # The Ubuntu App Center's Flutter embedder answers GetActions with the
        # right number of entries but every name empty, while the per-index
        # Action.GetName answers "Tap" and "Focus" (observed; gi.Atspi reads the
        # same). An empty bulk name is re-asked per index, or every Flutter
        # button reports two unnameable actions and no verb can resolve.
        bus = _FakeBus({}, action_names={("snapstore", "/btn"): ["Tap", "Focus"]},
                       blank_bulk_actions=True)
        client = _DbusClient(bus)
        assert client.actions(("snapstore", "/btn")) == ["Tap", "Focus"]

    def test_named_bulk_actions_need_no_extra_reads(self):
        bus = _FakeBus({}, action_names={("gtk", "/btn"): ["click"]})
        client = _DbusClient(bus)
        before = bus.live_calls
        assert client.actions(("gtk", "/btn")) == ["click"]
        assert bus.live_calls == before + 1  # one bulk read, no per-index calls

    def test_text_survives_a_bridge_that_rejects_out_of_range_reads(self):
        # LibreOffice answers GetText(0, end) with "" whenever end exceeds the
        # character count, where GTK clamps and answers with the full text. A
        # fixed-cap read therefore reported every Writer paragraph as empty,
        # which made a landed set_text look like a no-op. An empty first read
        # is re-checked against CharacterCount and re-read with the real range.
        bus = _FakeBus({}, texts={("soffice", "/para"): "landed text"})
        client = _DbusClient(bus)
        assert client.text(("soffice", "/para")) == "landed text"

    def test_text_of_a_truly_empty_element_stays_empty(self):
        bus = _FakeBus({}, texts={("soffice", "/para"): ""})
        client = _DbusClient(bus)
        assert client.text(("soffice", "/para")) == ""

    def test_invalidate_drops_only_that_apps_snapshot(self):
        # Acting invalidates the target app's snapshot so the act-time reads are
        # live; every other app's snapshot stays warm.
        body_a = [_one_item("gtk", "/btn", "/frame", 0, 43, "7")]
        body_b = [_one_item("qt", "/btn", "/frame", 0, 43, "8")]
        bus = _FakeBus({"gtk": body_a, "qt": body_b})
        client = _DbusClient(bus)
        client.warm_caches(["gtk", "qt"])
        client.invalidate(("gtk", "/btn"))
        before = bus.live_calls
        client.name(("gtk", "/btn"))
        assert bus.live_calls > before  # the invalidated app reads live now
        before = bus.live_calls
        assert client.name(("qt", "/btn")) == "8"
        assert bus.live_calls == before  # the other app is still snapshot-served


def _state_words(names):
    from computer_use.tools.ui.atspi import _STATE_NAMES

    low = high = 0
    for name in names:
        index = _STATE_NAMES.index(name)
        if index < 32:
            low |= 1 << index
        else:
            high |= 1 << (index - 32)
    return [low, high]


class _Peer:
    """The applications' end of the accessibility bus, on the real event loop.

    A (bus name, path, member) in ``hang`` never answers: its coroutine waits
    forever, the way a stalled Flutter embedder leaves the call pending. Every
    other call answers at once from ``tree``.
    """

    def __init__(self, tree, hang=()):
        self.tree = tree
        self.hang = set(hang)
        self.asked = []

    async def call(self, message):
        import asyncio

        key = (message.destination, message.path)
        self.asked.append((*key, message.member))
        if (*key, message.member) in self.hang:
            await asyncio.Event().wait()
        member = message.member
        if member == "GetAddress":
            return _Reply(["unix:path=/nowhere"])
        if member == "GetItems":
            return _Reply(None, error_name="org.freedesktop.DBus.Error.UnknownMethod")
        node = self.tree.get(key)
        if node is None:
            return _Reply(None, error_name="org.freedesktop.DBus.Error.UnknownObject")
        if member == "GetChildren":
            return _Reply([[list(child) for child in node.get("children", ())]])
        if member == "GetRoleName":
            return _Reply([node["role"]])
        if member == "Get" and message.body[1] == "Name":
            return _Reply([_Variant(node.get("name", ""))])
        if member == "GetState":
            return _Reply([_state_words(node.get("states", ()))])
        if member == "GetExtents":
            return _Reply([list(node.get("extents", (0, 0, 0, 0)))])
        return _Reply(None, error_name="org.freedesktop.DBus.Error.UnknownMethod")


@pytest.mark.skipif(not _HAS_DBUS_FAST, reason="dbus-fast is installed on Linux only")
class TestStalledReadsOnALiveLoop:
    """A Flutter app that never answers some reads, through the real bus code.

    Measured on the test machine before the fix: one ui_tree of such an app
    took 25 seconds, five seconds per unanswered read, and the nodes behind
    those reads were dropped. The bound is one half-second wait per app per
    traversal, and every node is still returned.
    """

    FLUTTER, GTK = ":1.10", ":1.11"
    STALLED = ("Two", "Four", "Six", "Seven")

    def _backend(self):
        from computer_use.platform.resolver.session import SessionContext
        from computer_use.tools.ui.atspi import (
            _REGISTRY_BUS,
            _ROOT_PATH,
            AtspiBackend,
            _AtspiBus,
        )

        on = ["showing", "visible", "enabled"]
        names = ("One", "Two", "Three", "Four", "Five", "Six", "Seven")
        controls = [(self.FLUTTER, f"/n{i}") for i in range(len(names))]
        tree = {
            (_REGISTRY_BUS, _ROOT_PATH): {
                "role": "desktop frame",
                "children": [(self.FLUTTER, "/app"), (self.GTK, "/app")],
            },
            (self.FLUTTER, "/app"): {"role": "application", "name": "flutter-app",
                                     "children": [(self.FLUTTER, "/frame")]},
            (self.FLUTTER, "/frame"): {
                "role": "frame", "name": "App Center",
                "states": ["active", *on], "extents": (0, 0, 800, 600),
                "children": controls,
            },
            (self.GTK, "/app"): {"role": "application", "name": "gtk-app",
                                 "children": [(self.GTK, "/frame")]},
            (self.GTK, "/frame"): {"role": "frame", "name": "Editor", "states": on,
                                   "extents": (0, 0, 640, 480),
                                   "children": [(self.GTK, "/save")]},
            (self.GTK, "/save"): {"role": "button", "name": "Save", "states": on,
                                  "extents": (5, 5, 60, 24)},
        }
        hang = []
        for node, name in zip(controls, names):
            tree[node] = {"role": "button", "name": name, "states": on,
                          "extents": (10, 10, 50, 20)}
            if name in self.STALLED:
                hang += [(*node, "GetState"), (*node, "GetExtents")]
        peer = _Peer(tree, hang)
        bus = _AtspiBus(call_timeout=5.0)
        bus._session = bus._a11y = peer  # connected: no real bus is touched
        session = SessionContext(server="wayland", compositor="gnome",
                                 has_uinput=False, libs=frozenset())
        return AtspiBackend(_DbusClient(bus), session), peer, bus

    def test_a_tree_with_stalled_nodes_returns_in_under_two_seconds(self):
        import time

        backend, peer, bus = self._backend()
        try:
            started = time.monotonic()
            root = backend.tree(depth=6, app="flutter-app")["root"]
            elapsed = time.monotonic() - started
        finally:
            bus.close()
        assert elapsed < 2.0, f"ui_tree took {elapsed:.2f}s"
        children = {child["name"]: child for child in root["children"]}
        assert list(children) == ["One", "Two", "Three", "Four", "Five", "Six", "Seven"]
        # Read before the app stalled: in full.
        assert "showing" in children["One"]["states"]
        assert "unknown" not in children["One"]
        # From the first stall on, both fields read as unknown, never as facts.
        for name in ("Two", "Three", "Seven"):
            assert children[name]["unknown"] == ["bounds", "states"], name
            assert children[name]["states"] == [] and children[name]["bounds"] is None
        # One bounded wait for the app, not one per stalled node.
        stalled_asks = [a for a in peer.asked if a[2] in ("GetState", "GetExtents")
                        and a[1] in {"/n1", "/n3", "/n5", "/n6"}]
        assert len(stalled_asks) == 1

    def test_another_app_in_the_same_read_is_untouched(self):
        import time

        backend, _peer, bus = self._backend()
        try:
            started = time.monotonic()
            found = backend.find("button", "", app="*")
            elapsed = time.monotonic() - started
        finally:
            bus.close()
        assert elapsed < 2.0, f"ui_find took {elapsed:.2f}s"
        by_name = {e.name: e for e in found}
        assert set(by_name) == {"One", "Two", "Three", "Four", "Five", "Six", "Seven", "Save"}
        assert "showing" in by_name["Save"].states
        assert by_name["Save"].unknown == ()
        assert by_name["Save"].bounds.as_dict() == {"x": 5, "y": 5, "w": 60, "h": 24}

    def test_each_traversal_gives_the_app_one_new_chance(self):
        backend, peer, bus = self._backend()
        try:
            backend.tree(depth=6, app="flutter-app")
            backend.tree(depth=6, app="flutter-app")
        finally:
            bus.close()
        first_stall = [a for a in peer.asked if a[1] == "/n1" and a[2] == "GetState"]
        assert len(first_stall) == 2


@pytest.mark.skipif(not _HAS_DBUS_FAST, reason="dbus-fast is installed on Linux only")
class TestBusTimeouts:
    def test_a_call_that_outlives_the_bus_timeout_is_cancelled_not_leaked(self):
        # The caller gets the builtin TimeoutError on every Python version
        # (3.10 raises separate classes from the loop's future), and the
        # pending call on the loop is cancelled rather than left waiting.
        import asyncio
        import threading

        from computer_use.tools.ui.atspi import _AtspiBus

        bus = _AtspiBus(call_timeout=0.05)
        cancelled = threading.Event()

        async def never_answers():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        try:
            with pytest.raises(TimeoutError):
                bus.run(never_answers())
            assert cancelled.wait(1.0)
        finally:
            bus.close()
