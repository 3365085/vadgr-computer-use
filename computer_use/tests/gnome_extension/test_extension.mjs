// Copyright 2026 Victor Santiago Montaño Diaz
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// The GNOME Shell extension's logic, against stubbed GNOME APIs.
//
// This proves the decisions the extension makes: when Esc stops vadgr and for
// how long, when the grab is lent back for vadgr's own Esc, that a capture
// hides the overlay for the grab and lights it after, that a capture never
// overwrites a file, and how image pixels map to stage pixels. It cannot prove
// the GNOME APIs behave as stubbed; the live GNOME 50 run is what does that.
//
// Run with: node --import ./register.mjs --test test_extension.mjs

import {beforeEach, afterEach, test} from 'node:test';
import assert from 'node:assert/strict';

import {Shell, makeGlobal, state} from './gi.mjs';
import * as Main from './stub_main.mjs';

const extensionUrl = new URL(
    '../../platform/gnome_extension/vadgr-cua@vadgr.com/extension.js', import.meta.url);
const {default: VadgrCuaExtension} = await import(extensionUrl.href);

let extension;
let indicator;

function root() {
    return Main.layoutManager.chrome[0];
}

function activeGrabs() {
    return state.grabs.filter(g => g.active);
}

function pressEscape(action = activeGrabs()[0]?.action) {
    global.display.emit('accelerator-activated', action, 0, 0);
}

function begin(tool = 'click', text = 'Clicking', x = -1, y = -1, width = 0, height = 0,
    ownEscape = false) {
    return indicator.Begin(tool, text, x, y, width, height, ownEscape);
}

function invocation() {
    return {
        value: undefined,
        error: null,
        return_value(value) {
            this.value = value;
        },
        return_dbus_error(name, message) {
            this.error = {name, message};
        },
    };
}

beforeEach(() => {
    state.reset();
    Main.__reset();
    Object.assign(globalThis, makeGlobal());
    extension = new VadgrCuaExtension({uuid: 'vadgr-cua@vadgr.com'});
    extension.enable();
    indicator = state.exported.object;
});

afterEach(() => {
    extension.disable();
});

test('enable exports the service and disable takes everything back', () => {
    assert.equal(state.exported.path, '/com/vadgr/cua/Shell');
    assert.equal(state.owned, 'com.vadgr.cua.Shell');
    assert.match(state.exported.xml, /<interface name="com.vadgr.cua.Shell">/);
    begin();
    extension.disable();
    assert.equal(state.exported, null);
    assert.equal(state.owned, null);
    assert.equal(Main.layoutManager.chrome.length, 0);
    assert.equal(Main.layoutManager.handlerCount(), 0);
    assert.equal(global.display.handlerCount(), 0);
    assert.equal(activeGrabs().length, 0);
    assert.equal(state.timers.length, 0);
    extension.enable();
});

test('no actor takes a click or the focus', () => {
    const visit = actor => {
        assert.equal(actor.reactive, false, actor.style_class ?? actor.name);
        assert.equal(actor.can_focus, false, actor.style_class ?? actor.name);
        actor.children.forEach(visit);
    };
    visit(root());
});

test('Begin lights the overlay, names the action and arms Esc', () => {
    assert.equal(root().visible, false);
    assert.equal(begin('click', 'Clicking "Save"'), false);
    assert.equal(root().visible, true);
    assert.equal(indicator._pill.text, 'Clicking "Save" · Esc to stop');
    const [grab] = activeGrabs();
    assert.equal(grab.accelerator, 'Escape');
    assert.equal(Main.wm.allowed[`external-grab-${grab.action}`],
        Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW);
});

test('the glow covers every edge of every monitor', () => {
    assert.equal(indicator._glow.children.length, 4);
    Main.layoutManager.monitors.push({x: 1920, y: 0, width: 1280, height: 1024});
    Main.layoutManager.emit('monitors-changed');
    const sides = indicator._glow.children.map(e => e.style_class.split(' ')[1]);
    assert.equal(sides.length, 8);
    assert.equal(sides.filter(s => s === 'vadgr-cua-edge-right').length, 2);
});

test('the glow breathes while the overlay is up', () => {
    begin();
    const [breath] = indicator._glow.transitions;
    assert.equal(breath.repeatCount, -1);
    assert.equal(breath.autoReverse, true);
});

test('a click ring is drawn at the target, in stage pixels', () => {
    state.captureScale = 2;
    begin('click', 'Clicking', 400, 300);
    const ring = indicator._ring;
    assert.equal(ring.x + ring.width / 2, 200);
    assert.equal(ring.y + ring.height / 2, 150);
    const [grow] = ring.transitions;
    assert.equal(grow.duration, 300);
    assert.equal(grow.animationRequired, true);
});

test('a control with trusted bounds gets a target box instead', () => {
    begin('ui_act', 'Clicking "Save"', 100, 200, 80, 30);
    const target = indicator._target;
    assert.deepEqual([target.x, target.y, target.width, target.height], [100, 200, 80, 30]);
    assert.equal(indicator._ring.transitions.length, 0);
});

test('no coordinates, no highlight', () => {
    begin('ui_find', 'Finding "Save"');
    assert.equal(indicator._ring.transitions.length, 0);
    assert.equal(indicator._target.transitions.length, 0);
});

test('Esc stops every tool for 30 seconds, then vadgr may resume', () => {
    begin();
    pressEscape();
    assert.equal(indicator._pill.text, 'Stopped');
    assert.equal(begin(), true);
    state.advance(29000);
    assert.equal(begin(), true);
    state.advance(1001);
    assert.equal(begin(), false);
    assert.equal(activeGrabs().length, 1);
});

test('a stopped overlay fades and lets Esc go back to the applications', () => {
    begin();
    pressEscape();
    state.advance(2000);
    assert.equal(root().visible, false);
    assert.equal(activeGrabs().length, 0);
});

test('an accelerator that is not the Esc grab is ignored', () => {
    begin();
    pressEscape(9999);
    assert.equal(begin(), false);
});

test("vadgr's own Esc is let through, then the grab returns", () => {
    begin();
    assert.equal(activeGrabs().length, 1);
    begin('key_press', 'Pressing escape', -1, -1, 0, 0, true);
    assert.equal(activeGrabs().length, 0);
    // A tool that runs alongside must not take the key back early.
    begin('screenshot', 'Taking a screenshot');
    assert.equal(activeGrabs().length, 0);
    state.advance(2000);
    assert.equal(activeGrabs().length, 1);
});

test('the first action can be an own Esc and still names the stop key', () => {
    begin('key_press', 'Pressing escape', -1, -1, 0, 0, true);
    assert.equal(indicator._pill.text, 'Pressing escape · Esc to stop');
    assert.equal(activeGrabs().length, 0);
    state.advance(2000);
    assert.equal(activeGrabs().length, 1);
});

test('the overlay fades 15 seconds after the last action and frees Esc', () => {
    begin();
    state.advance(14000);
    begin();
    state.advance(14000);
    assert.equal(root().visible, true);
    state.advance(1001);
    assert.equal(root().visible, false);
    assert.equal(activeGrabs().length, 0);
});

test('when Escape cannot be grabbed the pill does not promise it', () => {
    state.escapeTaken = true;
    begin('click', 'Clicking');
    assert.equal(indicator._pill.text, 'Clicking');
});

test('a screenshot hides the overlay for the grab and lights it after', async () => {
    let visibleAtGrab = null;
    state.onGrab = () => {
        visibleAtGrab = root().visible;
    };
    const call = invocation();
    await indicator.ScreenshotAsync(['/run/user/1000/vadgr/shot.png', 0, 0, 0, 0], call);
    assert.equal(call.error, null);
    assert.equal(call.value, null);
    assert.equal(visibleAtGrab, false);
    assert.equal(root().visible, true);
    const [shot] = state.shots;
    assert.equal(shot.kind, 'full');
    assert.equal(shot.includeCursor, false);
    assert.equal(shot.stream.closed, true);
    assert.equal(activeGrabs().length, 1);
    assert.ok(state.files.has('/run/user/1000/vadgr/shot.png'));
});

test('an area is converted from image pixels to stage pixels', async () => {
    state.captureScale = 2;
    const call = invocation();
    await indicator.ScreenshotAsync(['/tmp/area.png', 100, 50, 200, 100], call);
    assert.equal(call.error, null);
    const [shot] = state.shots;
    assert.deepEqual([shot.kind, shot.x, shot.y, shot.width, shot.height],
        ['area', 50, 25, 100, 50]);
});

test('an area outside the screen is refused before any file is made', async () => {
    const call = invocation();
    await indicator.ScreenshotAsync(['/tmp/out.png', 5000, 10, 20, 20], call);
    assert.equal(call.error.name, 'com.vadgr.cua.Shell.Error.Failed');
    assert.equal(state.files.size, 0);
    assert.equal(state.shots.length, 0);
});

test('a screenshot is refused while the user has stopped vadgr', async () => {
    begin();
    pressEscape();
    const call = invocation();
    await indicator.ScreenshotAsync(['/tmp/shot.png', 0, 0, 0, 0], call);
    assert.equal(call.error.name, 'com.vadgr.cua.Shell.Error.UserStopped');
    assert.equal(state.shots.length, 0);
    assert.equal(state.files.size, 0);
});

test('a relative or non-PNG path is refused', async () => {
    for (const path of ['shot.png', '/tmp/shot.jpg']) {
        const call = invocation();
        await indicator.ScreenshotAsync([path, 0, 0, 0, 0], call);
        assert.equal(call.error.name, 'com.vadgr.cua.Shell.Error.InvalidArgs', path);
    }
    assert.equal(state.shots.length, 0);
});

test('the lockdown switch that blocks saving screenshots is honoured', async () => {
    state.lockdown = true;
    const call = invocation();
    await indicator.ScreenshotAsync(['/tmp/shot.png', 0, 0, 0, 0], call);
    assert.equal(call.error.name, 'com.vadgr.cua.Shell.Error.Disabled');
    assert.equal(state.shots.length, 0);
});

test('a screenshot never overwrites an existing file', async () => {
    state.files.set('/tmp/keep.png', 'precious');
    const call = invocation();
    await indicator.ScreenshotAsync(['/tmp/keep.png', 0, 0, 0, 0], call);
    assert.equal(call.error.name, 'com.vadgr.cua.Shell.Error.Failed');
    assert.equal(state.files.get('/tmp/keep.png'), 'precious');
    assert.equal(state.shots.length, 0);
});

test('CaptureSize reports a full capture in image pixels', () => {
    state.captureScale = 2;
    assert.deepEqual(indicator.CaptureSize(), [3840, 2160]);
});
