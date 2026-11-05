// Copyright 2026 Victor Santiago Montaño Diaz
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// The vadgr-cua indicator and flash-free capture, inside GNOME Shell.
//
// It lives in the shell because nothing else can do either job on Wayland: an
// ordinary client cannot draw a global, always-on-top, click-through overlay
// (Mutter has no layer-shell), and the screenshot portal hard-codes the
// screen flash even after the user granted access.
//
// One object on the session bus. Begin() lights the overlay before a desktop
// tool acts and answers whether the user pressed Esc in the last 30 seconds.
// Screenshot() captures inside the shell with the overlay hidden for the grab.
// Any process of this user can call the interface, so every capture lights
// the glow: there is no silent screenshot.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Mtk from 'gi://Mtk';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'com.vadgr.cua.Shell';
const OBJECT_PATH = '/com/vadgr/cua/Shell';
const ERROR_PREFIX = 'com.vadgr.cua.Shell.Error';

const IFACE = `<node>
  <interface name="com.vadgr.cua.Shell">
    <method name="Begin">
      <arg type="s" direction="in" name="tool"/>
      <arg type="s" direction="in" name="text"/>
      <arg type="i" direction="in" name="x"/>
      <arg type="i" direction="in" name="y"/>
      <arg type="i" direction="in" name="width"/>
      <arg type="i" direction="in" name="height"/>
      <arg type="b" direction="in" name="own_escape"/>
      <arg type="b" direction="out" name="stopped"/>
    </method>
    <method name="Screenshot">
      <arg type="s" direction="in" name="path"/>
      <arg type="i" direction="in" name="x"/>
      <arg type="i" direction="in" name="y"/>
      <arg type="i" direction="in" name="width"/>
      <arg type="i" direction="in" name="height"/>
    </method>
    <method name="CaptureSize">
      <arg type="i" direction="out" name="width"/>
      <arg type="i" direction="out" name="height"/>
    </method>
  </interface>
</node>`;

// The glow fades this long after the last Begin or Screenshot.
const IDLE_FADE_MS = 15000;
// One Esc press stops every desktop tool for this long.
const STOP_WINDOW_US = 30 * 1000000;
// After vadgr sends its own Esc the grab stays released this long, so the key
// reaches the application instead of stopping vadgr itself.
const OWN_ESCAPE_MS = 2000;
// How long "Stopped" stays on screen before the overlay fades.
const STOPPED_LINGER_MS = 2000;
const FADE_MS = 400;
const BREATHE_MS = 1400;
const RING_MS = 300;
const TARGET_HOLD_MS = 900;
const EDGE_PX = 16;
const RING_PX = 48;
const PILL_GAP_PX = 8;
const MIDDLE_DOT = '·';

class Indicator {
    constructor() {
        this._stoppedAt = 0;
        this._escAction = Meta.KeyBindingAction.NONE;
        this._escName = null;
        // Whether the last grab of Escape succeeded. Starts true so the first
        // action, which may lend the key to vadgr's own Esc, still names it.
        this._escWorks = true;
        this._idleId = 0;
        this._lendId = 0;
        this._lockdown = new Gio.Settings({schema_id: 'org.gnome.desktop.lockdown'});

        // Nothing here is reactive or focusable: the overlay never takes a
        // click or the keyboard focus from the window under it.
        const passive = {reactive: false, can_focus: false, track_hover: false};
        this._root = new St.Widget({name: 'vadgrCuaOverlay', visible: false, opacity: 0, ...passive});
        this._glow = new St.Widget({...passive});
        this._ring = new St.Widget({style_class: 'vadgr-cua-ring', visible: false, ...passive});
        this._target = new St.Widget({style_class: 'vadgr-cua-target', visible: false, ...passive});
        this._pillBox = new St.Widget({layout_manager: new Clutter.BinLayout(), ...passive});
        this._pill = new St.Label({
            style_class: 'vadgr-cua-pill',
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.START,
            ...passive,
        });
        this._pillBox.add_child(this._pill);
        for (const child of [this._glow, this._ring, this._target, this._pillBox])
            this._root.add_child(child);

        Main.layoutManager.addTopChrome(this._root);
        Main.layoutManager.connectObject('monitors-changed', () => this._layout(), this);
        global.display.connectObject('accelerator-activated', (display, action) => {
            if (action !== Meta.KeyBindingAction.NONE && action === this._escAction)
                this._onEscape();
        }, this);
        this._layout();
    }

    destroy() {
        if (this._idleId)
            GLib.source_remove(this._idleId);
        if (this._lendId)
            GLib.source_remove(this._lendId);
        this._idleId = this._lendId = 0;
        this._releaseEscape();
        global.display.disconnectObject(this);
        Main.layoutManager.disconnectObject(this);
        Main.layoutManager.removeChrome(this._root);
        this._root.destroy();
    }

    // -- D-Bus methods -----------------------------------------------------

    Begin(tool, text, x, y, width, height, ownEscape) {
        if (this._isStopped())
            return true;
        // vadgr is about to send Escape itself: lend the key to the
        // application before anything grabs it again.
        if (ownEscape)
            this._lendEscape();
        this._activate(text || tool);
        this._highlight(x, y, width, height);
        return false;
    }

    CaptureSize() {
        const [ok, width, height] = global.stage.get_capture_final_size(this._screenRect());
        if (!ok)
            return [global.screen_width, global.screen_height];
        return [width, height];
    }

    async ScreenshotAsync(params, invocation) {
        const [path, x, y, width, height] = params;
        if (this._isStopped()) {
            invocation.return_dbus_error(`${ERROR_PREFIX}.UserStopped`,
                'The user pressed Esc to stop desktop automation.');
            return;
        }
        if (!GLib.path_is_absolute(path) || !path.endsWith('.png')) {
            invocation.return_dbus_error(`${ERROR_PREFIX}.InvalidArgs`,
                'path must be an absolute path ending in .png');
            return;
        }
        // The same switch the shell's own screenshot service honours.
        if (this._lockdown.get_boolean('disable-save-to-disk')) {
            invocation.return_dbus_error(`${ERROR_PREFIX}.Disabled`,
                'Saving screenshots to disk is disabled by the lockdown settings.');
            return;
        }

        let file = null;
        let stream = null;
        try {
            const area = this._area(x, y, width, height);
            file = Gio.File.new_for_path(path);
            // create, never replace: an existing file is refused, so the
            // interface cannot overwrite anything.
            stream = file.create(Gio.FileCreateFlags.NONE, null);
            this._activate(null);
            await this._capture(stream, area);
            stream.close(null);
            invocation.return_value(null);
        } catch (e) {
            if (stream) {
                try {
                    stream.close(null);
                    file.delete(null);
                } catch {
                }
            }
            invocation.return_dbus_error(`${ERROR_PREFIX}.Failed`, String(e?.message ?? e));
        }
    }

    // -- overlay -----------------------------------------------------------

    _layout() {
        this._root.set_position(0, 0);
        this._root.set_size(global.screen_width, global.screen_height);
        this._glow.destroy_all_children();
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        const t = EDGE_PX * scale;
        for (const m of Main.layoutManager.monitors) {
            this._edge('top', m.x, m.y, m.width, t);
            this._edge('bottom', m.x, m.y + m.height - t, m.width, t);
            this._edge('left', m.x, m.y, t, m.height);
            this._edge('right', m.x + m.width - t, m.y, t, m.height);
        }
        const primary = Main.layoutManager.primaryMonitor;
        if (primary) {
            const panel = Main.panel?.height ?? 0;
            this._pillBox.set_position(primary.x, primary.y + panel + PILL_GAP_PX * scale);
            this._pillBox.width = primary.width;
        }
        const ring = RING_PX * scale;
        this._ring.set_size(ring, ring);
        this._ring.set_pivot_point(0.5, 0.5);
    }

    _edge(side, x, y, width, height) {
        this._glow.add_child(new St.Widget({
            style_class: `vadgr-cua-edge vadgr-cua-edge-${side}`,
            reactive: false,
            can_focus: false,
            x,
            y,
            width,
            height,
        }));
    }

    _activate(text) {
        if (!this._lendId)
            this._grabEscape();
        if (text !== null)
            this._setText(text);
        if (!this._root.visible) {
            this._root.show();
            this._breathe();
        }
        // At full opacity at once, not faded in: the ring and the target box
        // live inside the root and last about as long as a fade, so the first
        // action after an idle spell drew them nearly invisible. This also
        // cancels a fade-out in progress.
        this._root.remove_all_transitions();
        this._root.opacity = 255;
        this._restartIdle(IDLE_FADE_MS);
    }

    _setText(text, hint = true) {
        this._pill.text = hint && this._escWorks
            ? `${text} ${MIDDLE_DOT} Esc to stop`
            : text;
    }

    _breathe() {
        this._glow.remove_all_transitions();
        this._glow.opacity = 255;
        this._glow.ease({
            opacity: 110,
            duration: BREATHE_MS,
            mode: Clutter.AnimationMode.EASE_IN_OUT_SINE,
            repeatCount: -1,
            autoReverse: true,
        });
    }

    _restartIdle(ms) {
        if (this._idleId)
            GLib.source_remove(this._idleId);
        this._idleId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            this._idleId = 0;
            this._fadeOut();
            return GLib.SOURCE_REMOVE;
        });
    }

    _fadeOut() {
        this._root.ease({
            opacity: 0,
            duration: FADE_MS,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => {
                this._root.hide();
                this._glow.remove_all_transitions();
                this._releaseEscape();
            },
        });
    }

    _highlight(x, y, width, height) {
        if (x < 0 || y < 0)
            return;
        const s = this._captureScale();
        if (width > 0 && height > 0)
            this._showTarget(x / s, y / s, width / s, height / s);
        else
            this._showRing(x / s, y / s);
    }

    _showRing(x, y) {
        const ring = this._ring;
        ring.remove_all_transitions();
        ring.set_position(x - ring.width / 2, y - ring.height / 2);
        ring.opacity = 255;
        ring.scale_x = ring.scale_y = 0.3;
        ring.show();
        ring.ease({
            scale_x: 1.6,
            scale_y: 1.6,
            opacity: 0,
            duration: RING_MS,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            animationRequired: true,
            onComplete: () => ring.hide(),
        });
    }

    _showTarget(x, y, width, height) {
        const target = this._target;
        target.remove_all_transitions();
        target.set_position(x, y);
        target.set_size(width, height);
        target.opacity = 255;
        target.show();
        target.ease({
            opacity: 0,
            delay: TARGET_HOLD_MS,
            duration: FADE_MS,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            animationRequired: true,
            onComplete: () => target.hide(),
        });
    }

    // -- capture -----------------------------------------------------------

    _screenRect() {
        return new Mtk.Rectangle({
            x: 0,
            y: 0,
            width: global.screen_width,
            height: global.screen_height,
        });
    }

    // Image pixels per stage pixel for a full capture. Callers speak in image
    // pixels (the space the screenshots and the input executor use); the stage
    // is in logical pixels when the monitors are scaled.
    _captureScale() {
        const [ok, , , scale] = global.stage.get_capture_final_size(this._screenRect());
        return ok && scale > 0 ? scale : 1;
    }

    _area(x, y, width, height) {
        if (width <= 0 || height <= 0)
            return null;
        const s = this._captureScale();
        const area = {x: Math.floor(x / s), y: Math.floor(y / s)};
        area.width = Math.min(Math.ceil(width / s), global.screen_width - area.x);
        area.height = Math.min(Math.ceil(height / s), global.screen_height - area.y);
        if (area.x < 0 || area.y < 0 || area.width <= 0 || area.height <= 0)
            throw new Error('the area lies outside the screen');
        return area;
    }

    _capture(stream, area) {
        const shooter = new Shell.Screenshot();
        return new Promise((resolve, reject) => {
            const finish = (obj, res) => {
                try {
                    if (area)
                        obj.screenshot_area_finish(res);
                    else
                        obj.screenshot_finish(res);
                    resolve();
                } catch (e) {
                    reject(e);
                }
            };
            // GNOME 50 paints the stage into the capture buffer synchronously
            // inside this call (shell-screenshot.c) and encodes the PNG on a
            // thread afterwards. Hiding the overlay around the call keeps it
            // out of the image, and no frame is painted without it.
            this._root.hide();
            try {
                if (area)
                    shooter.screenshot_area(area.x, area.y, area.width, area.height, stream, finish);
                else
                    shooter.screenshot(false, stream, finish);
            } catch (e) {
                reject(e);
            } finally {
                this._root.show();
            }
        });
    }

    // -- Esc ---------------------------------------------------------------

    _isStopped() {
        return this._stoppedAt !== 0 &&
            GLib.get_monotonic_time() - this._stoppedAt < STOP_WINDOW_US;
    }

    _onEscape() {
        this._stoppedAt = GLib.get_monotonic_time();
        this._setText('Stopped', false);
        this._restartIdle(STOPPED_LINGER_MS);
    }

    // A grabbed accelerator is consumed by the compositor, press and release,
    // so the application under the pointer never sees the Esc that stopped
    // vadgr. A key-press handler on the stage would not work here: keys sent
    // to a Wayland window never reach the stage.
    _grabEscape() {
        if (this._escAction !== Meta.KeyBindingAction.NONE)
            return;
        const action = global.display.grab_accelerator('Escape',
            Meta.KeyBindingFlags.IGNORE_AUTOREPEAT | Meta.KeyBindingFlags.TRIGGER_RELEASE);
        this._escWorks = action !== Meta.KeyBindingAction.NONE;
        if (!this._escWorks)
            return;
        this._escAction = action;
        this._escName = Meta.external_binding_name_for_action(action);
        Main.wm.allowKeybinding(this._escName,
            Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW);
    }

    _releaseEscape() {
        if (this._escAction === Meta.KeyBindingAction.NONE)
            return;
        global.display.ungrab_accelerator(this._escAction);
        Main.wm.allowKeybinding(this._escName, Shell.ActionMode.NONE);
        this._escAction = Meta.KeyBindingAction.NONE;
        this._escName = null;
    }

    _lendEscape() {
        this._releaseEscape();
        if (this._lendId)
            GLib.source_remove(this._lendId);
        this._lendId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, OWN_ESCAPE_MS, () => {
            this._lendId = 0;
            if (this._root.visible)
                this._grabEscape();
            return GLib.SOURCE_REMOVE;
        });
    }
}

export default class VadgrCuaExtension extends Extension {
    enable() {
        this._indicator = new Indicator();
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this._indicator);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.bus_own_name_on_connection(Gio.DBus.session, BUS_NAME,
            Gio.BusNameOwnerFlags.NONE, null, null);
    }

    disable() {
        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = 0;
        }
        this._dbus?.unexport();
        this._dbus = null;
        this._indicator?.destroy();
        this._indicator = null;
    }
}
