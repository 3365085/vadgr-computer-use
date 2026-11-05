// Copyright 2026 Victor Santiago Montaño Diaz
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// `gi://Name` resolves here with ?ns=Name; every namespace object lives in the
// one shared gi.mjs instance, so tests and the extension see the same state.
import * as gi from './gi.mjs';

const namespace = new URL(import.meta.url).searchParams.get('ns');
if (!(namespace in gi))
    throw new Error(`no stub for gi://${namespace}`);

export default gi[namespace];
