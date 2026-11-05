// Copyright 2026 Victor Santiago Montaño Diaz
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Loaded with `node --import` before the tests, to install the resolve hooks.
import {register} from 'node:module';

register('./loader.mjs', import.meta.url);
