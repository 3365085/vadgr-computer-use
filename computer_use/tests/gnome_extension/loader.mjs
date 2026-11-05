// Copyright 2026 Victor Santiago Montaño Diaz
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Node module hooks that resolve the GNOME Shell import specifiers to the
// stubs in this directory, so the extension module loads under node:test.
// The stubs model only the calls the extension makes; GNOME Shell itself is
// exercised on a real GNOME 50 session, never here.

const here = new URL('./', import.meta.url);

const SHELL_MODULES = {
    'resource:///org/gnome/shell/ui/main.js': 'stub_main.mjs',
    'resource:///org/gnome/shell/extensions/extension.js': 'stub_extension.mjs',
};

export async function resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('gi://')) {
        const namespace = specifier.slice('gi://'.length).split('?')[0];
        return {url: new URL(`stub_gi.mjs?ns=${namespace}`, here).href, shortCircuit: true};
    }
    if (specifier in SHELL_MODULES)
        return {url: new URL(SHELL_MODULES[specifier], here).href, shortCircuit: true};
    return nextResolve(specifier, context);
}

// The extension ships as extension.js with no package.json beside it, the
// layout GNOME Shell expects, so Node is told it is an ES module.
export async function load(url, context, nextLoad) {
    if (url.includes('/gnome_extension/') && url.endsWith('/extension.js'))
        return nextLoad(url, {...context, format: 'module'});
    return nextLoad(url, context);
}
