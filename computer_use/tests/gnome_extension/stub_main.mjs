// Copyright 2026 Victor Santiago Montaño Diaz
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Stand-in for resource:///org/gnome/shell/ui/main.js: the layout manager,
// the window manager's keybinding filter and the top panel. The bindings are
// live, so __reset() gives every test a fresh shell.

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

class LayoutManager extends Signals {
    constructor() {
        super();
        this.chrome = [];
        this.monitors = [{x: 0, y: 0, width: 1920, height: 1080}];
        this.primaryMonitor = this.monitors[0];
    }

    addTopChrome(actor, params) {
        this.chrome.push(actor);
    }

    removeChrome(actor) {
        this.chrome = this.chrome.filter(a => a !== actor);
    }
}

export let layoutManager = new LayoutManager();
export let wm = null;
export let panel = {height: 32};

export function __reset() {
    layoutManager = new LayoutManager();
    wm = {
        allowed: {},
        allowKeybinding(name, modes) {
            this.allowed[name] = modes;
        },
    };
    panel = {height: 32};
}

__reset();
