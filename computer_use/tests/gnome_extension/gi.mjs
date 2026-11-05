// Copyright 2026 Victor Santiago Montaño Diaz
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Minimal stand-ins for the GObject introspection namespaces the extension
// imports. Each models only the surface extension.js calls, with the shape of
// the GNOME 50 API it was written against. `state` holds the controls and
// records the tests read: a fake monotonic clock and timer queue, an
// in-memory file system, the accelerator grabs and the screenshot grabs.

export const state = {
    reset() {
        this.now = 1000000;
        this.timers = [];
        this.nextTimer = 1;
        this.files = new Map();
        this.lockdown = false;
        this.grabs = [];
        this.nextAction = 100;
        this.escapeTaken = false;
        this.shots = [];
        this.onGrab = null;
        this.captureScale = 1;
        this.exported = null;
        this.owned = null;
    },

    // Fire every timer due within `ms` of fake time, in deadline order.
    advance(ms) {
        const until = this.now + ms * 1000;
        for (;;) {
            this.timers.sort((a, b) => a.due - b.due);
            const timer = this.timers[0];
            if (!timer || timer.due > until)
                break;
            this.timers.shift();
            this.now = timer.due;
            timer.fn();
        }
        this.now = until;
    },
};
state.reset();

class Signals {
    constructor() {
        this._handlers = [];
    }

    connectObject(signal, fn, owner) {
        this._handlers.push({signal, fn, owner});
    }

    disconnectObject(owner) {
        this._handlers = this._handlers.filter(h => h.owner !== owner);
    }

    emit(signal, ...args) {
        for (const h of this._handlers.filter(x => x.signal === signal))
            h.fn(this, ...args);
    }

    handlerCount() {
        return this._handlers.length;
    }
}

class Actor extends Signals {
    constructor(params = {}) {
        super();
        this.visible = true;
        this.opacity = 255;
        this.x = 0;
        this.y = 0;
        this.width = 0;
        this.height = 0;
        this.scale_x = 1;
        this.scale_y = 1;
        this.children = [];
        this.parent = null;
        this.destroyed = false;
        this.transitions = [];
        Object.assign(this, params);
    }

    add_child(child) {
        child.parent = this;
        this.children.push(child);
    }

    destroy_all_children() {
        for (const child of this.children)
            child.destroyed = true;
        this.children = [];
    }

    destroy() {
        this.destroyed = true;
        this.destroy_all_children();
    }

    set_position(x, y) {
        this.x = x;
        this.y = y;
    }

    set_size(width, height) {
        this.width = width;
        this.height = height;
    }

    set_pivot_point(px, py) {
        this.pivot = [px, py];
    }

    show() {
        this.visible = true;
    }

    hide() {
        this.visible = false;
    }

    // The shell's ease() reaches its final values at the end of the duration.
    // Here a finite ease lands at once, which keeps the timer logic under test
    // and leaves animation timing to the live run. An endless ease (the
    // breathing glow) is only recorded.
    ease(params) {
        const {onComplete, repeatCount, ...rest} = params;
        this.transitions.push(params);
        if (repeatCount === -1)
            return;
        for (const key of ['opacity', 'scale_x', 'scale_y']) {
            if (key in rest)
                this[key] = rest[key];
        }
        onComplete?.();
    }

    remove_all_transitions() {
        this.transitions = [];
    }
}

class Widget extends Actor {}

class Label extends Widget {
    constructor(params = {}) {
        super(params);
        this.text = params.text ?? '';
    }
}

export const St = {
    Widget,
    Label,
    ThemeContext: {
        get_for_stage: () => ({scale_factor: 1}),
    },
};

export const Clutter = {
    BinLayout: class {},
    ActorAlign: {START: 1, CENTER: 3},
    AnimationMode: {EASE_OUT_QUAD: 5, EASE_IN_OUT_SINE: 22},
};

export const GLib = {
    PRIORITY_DEFAULT: 0,
    SOURCE_REMOVE: false,
    timeout_add(priority, ms, fn) {
        const id = state.nextTimer++;
        state.timers.push({id, due: state.now + ms * 1000, fn});
        return id;
    },
    source_remove(id) {
        state.timers = state.timers.filter(t => t.id !== id);
        return true;
    },
    get_monotonic_time: () => state.now,
    path_is_absolute: path => path.startsWith('/'),
};

class FileExistsError extends Error {}

class FakeFile {
    constructor(path) {
        this.path = path;
    }

    create(flags, cancellable) {
        if (state.files.has(this.path))
            throw new FileExistsError(`Error creating file ${this.path}: File exists`);
        state.files.set(this.path, '');
        return {closed: false, file: this, close() {
            this.closed = true;
        }};
    }

    delete(cancellable) {
        state.files.delete(this.path);
        return true;
    }
}

export const Gio = {
    BusNameOwnerFlags: {NONE: 0},
    FileCreateFlags: {NONE: 0},
    DBus: {session: {name: 'session-bus'}},
    File: {new_for_path: path => new FakeFile(path)},
    Settings: class {
        constructor({schema_id: schemaId}) {
            this.schemaId = schemaId;
        }

        get_boolean(key) {
            return key === 'disable-save-to-disk' && state.lockdown;
        }
    },
    DBusExportedObject: {
        wrapJSObject(xml, object) {
            return {
                xml,
                object,
                export(connection, path) {
                    state.exported = {connection, path, object, xml};
                },
                unexport() {
                    state.exported = null;
                },
            };
        },
    },
    bus_own_name_on_connection(connection, name, flags, acquired, lost) {
        state.owned = name;
        return 7;
    },
    bus_unown_name(id) {
        if (id === 7)
            state.owned = null;
    },
};

export const Meta = {
    KeyBindingAction: {NONE: 0},
    KeyBindingFlags: {IGNORE_AUTOREPEAT: 1 << 4, TRIGGER_RELEASE: 1 << 7},
    external_binding_name_for_action: action => `external-grab-${action}`,
};

export const Mtk = {
    Rectangle: class {
        constructor({x, y, width, height}) {
            Object.assign(this, {x, y, width, height});
        }
    },
};

class Screenshot {
    // GNOME 50 grabs the pixels synchronously inside these calls and calls
    // back once the PNG is written; the stub records the grab where the real
    // one paints, then calls back on a later turn.
    screenshot(includeCursor, stream, callback) {
        state.shots.push({kind: 'full', includeCursor, stream});
        state.onGrab?.();
        setTimeout(() => callback(this, {kind: 'full'}), 0);
    }

    screenshot_area(x, y, width, height, stream, callback) {
        state.shots.push({kind: 'area', x, y, width, height, stream});
        state.onGrab?.();
        setTimeout(() => callback(this, {kind: 'area'}), 0);
    }

    screenshot_finish(result) {
        return [true, null];
    }

    screenshot_area_finish(result) {
        return [true, null];
    }
}

export const Shell = {
    Screenshot,
    ActionMode: {NONE: 0, NORMAL: 1 << 1, OVERVIEW: 1 << 2},
};

// GNOME Shell's `global`, installed on Node's globalThis by the tests.
export function makeGlobal() {
    const display = new Signals();
    display.grab_accelerator = (accelerator, flags) => {
        if (state.escapeTaken)
            return Meta.KeyBindingAction.NONE;
        const action = state.nextAction++;
        state.grabs.push({accelerator, flags, action, active: true});
        return action;
    };
    display.ungrab_accelerator = action => {
        const grab = state.grabs.find(g => g.action === action && g.active);
        if (!grab)
            return false;
        grab.active = false;
        return true;
    };
    return {
        display,
        screen_width: 1920,
        screen_height: 1080,
        stage: {
            get_capture_final_size(rect) {
                const s = state.captureScale;
                return [true, rect.width * s, rect.height * s, s];
            },
        },
    };
}
