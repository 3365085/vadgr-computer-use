"""A disposable GTK 4 window that records every key press it receives (R06).

Run it with the system Python that has PyGObject, not the product interpreter:

    python3 key_fixture.py /path/to/keys.log

Each line of the log is a wall-clock timestamp and a key name. The window holds
no owner data; close it when the cell is done.
"""

import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk

LOG = sys.argv[1]


def on_key(_controller, keyval, _keycode, _state):
    with open(LOG, "a") as handle:
        handle.write(f"{time.time():.3f} {Gdk.keyval_name(keyval)}\n")
    return False


def activate(app):
    window = Gtk.ApplicationWindow(application=app, title="vadgr key fixture")
    window.set_default_size(420, 200)
    window.set_child(Gtk.Label(label="Key presses to this window are recorded"))
    keys = Gtk.EventControllerKey()
    keys.connect("key-pressed", on_key)
    window.add_controller(keys)
    window.present()


app = Gtk.Application(application_id="com.vadgr.e2e.KeyFixture")
app.connect("activate", activate)
app.run([])
