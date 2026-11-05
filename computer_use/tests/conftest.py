"""Shared pytest fixtures.

The autouse fixture below blocks tests from opening the real session bus
during a Mutter RemoteDesktop interaction. Any test that needs that path
must patch _open_dbus_connection itself.
"""

import sys
from unittest.mock import patch

import pytest

import computer_use.core.typing as typing_model
from computer_use.core.typing_profile import ArtifactInterpreter

# These modules import Linux-only bindings at module scope, so they fail at
# collection on Windows and macOS rather than skipping. A collection guard is
# the only thing that runs early enough: a module-level skipif still imports the
# module first. Without this the whole suite is uncollectable off Linux, and a
# suite that cannot be collected cannot enforce the four-platform rule.
collect_ignore = []
if sys.platform != "linux":
    collect_ignore += ["test_linux.py", "test_uinput.py"]


def _schema_six_profile():
    classes = (
        "same_key",
        "same_finger",
        "same_hand",
        "alternate_hand",
        "other",
        "ordinary_space",
        "clause",
        "sentence",
        "newline",
        "paragraph",
    )
    profile = {
        "schema": 6,
        "profile": "test",
        "nominal_wpm": 68,
        "limits": {
            "minimum_interval_ms": 20.0,
            "maximum_total_gap_ms": 5000.0,
            "maximum_transport_unit_ms": 5000.0,
            "minimum_validation_graphemes": 200,
            "class_maximum_ms": {
                **{name: 1500.0 for name in classes[:6]},
                "clause": 2500.0,
                "sentence": 3500.0,
                "newline": 5000.0,
                "paragraph": 5000.0,
            },
        },
        "model": {
            "kind": "observable_context_empirical_total_gap",
            "version": 1,
            "rank_dependence": "independent",
            "rank_transition": None,
            "ordinary_space_added_pause_ms": 0.0,
            "styles": [
                {"weight": 0.5, "speed_log": -0.2},
                {"weight": 0.5, "speed_log": 0.2},
            ],
            "class_quantiles": {
                name: [0.5 + index * 0.1, 1.0 + index * 0.1, 1.5 + index * 0.1]
                for index, name in enumerate(classes)
            },
            "reference_class_weights": {name: 0.1 for name in classes},
            "calibration_scale": 1.0,
        },
        "fit": {},
        "validation": {"cleared": True},
    }
    interpreter = ArtifactInterpreter(profile)
    profile["model"]["calibration_scale"] = interpreter._scale_for_quantiles(
        68,
        interpreter.class_quantiles,
        custom=False,
    )
    return profile


_SCHEMA_SIX_PROFILE = _schema_six_profile()
_SCHEMA_SIX_INTERPRETER = ArtifactInterpreter(_SCHEMA_SIX_PROFILE)


@pytest.fixture
def schema_six_typing_runtime(monkeypatch):
    """Keep non-artifact tests on the target schema until gated generation."""
    monkeypatch.setattr(
        typing_model,
        "_profile_interpreter",
        lambda: _SCHEMA_SIX_INTERPRETER,
    )


@pytest.fixture
def schema_six_profile():
    return _schema_six_profile()


@pytest.fixture(autouse=True)
def _no_live_gnome_shell(monkeypatch):
    """Keep the indicator and the shell capture off a developer's live session.

    A test that runs a cued tool through the MCP wire would otherwise light the
    real GNOME overlay, and an Esc pressed at that desk would fail the test.
    The default client here never finds the extension; a test that drives the
    client gives it a fake connection of its own.
    """
    from computer_use.platform.backends import gnome_shell

    def no_bus():
        raise gnome_shell.ExtensionUnavailable("unit tests never reach the live shell")

    monkeypatch.setattr(
        gnome_shell, "_default_client", gnome_shell.ShellClient(connect=no_bus)
    )


@pytest.fixture(autouse=True)
def _block_real_session_bus():
    """Stop a test from opening the real session bus.

    The guard is for Linux, where the bus exists. It is autouse, so it ran
    before every test on every platform, and on Windows and macOS the patch
    target does not exist: `computer_use.platform.linux` does not import there.
    That raised `AttributeError` in setup and made the whole suite uncollectable
    off Linux, which is how a `fcntl` import at module scope reached a release
    that supervises the Windows bridge. The four-platform rule needs a suite
    that runs on the four platforms.
    """
    try:
        import computer_use.platform.linux
    except (ImportError, AttributeError):
        # No session bus on this platform, so there is nothing to block.
        yield
        return

    blocker = patch(
        "computer_use.platform.linux._open_dbus_connection",
        side_effect=AssertionError(
            "test attempted to open the real session bus; "
            "patch computer_use.platform.linux._open_dbus_connection in the test"
        ),
    )
    blocker.start()
    try:
        yield
    finally:
        blocker.stop()
